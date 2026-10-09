"""TASK_251A: release-blocking review corrections to the TASK_251 collector.

1. A consistent, bounded timeout contract: a recoverable HTTP 429 never turns into an
   accidental parent kill, a permanently throttled provider ends with an explicit
   classified outcome while the child is alive, and incompatible retry/timeout settings
   are refused before any provider request.
2. Fail closed on missing child completion evidence: an exit code alone never proves a
   batch complete; missing, corrupt, oversized, incomplete or contradictory summaries
   leave every planned field unfinished and the run is never SUCCESS.

No provider, no production database; fake clocks and in-process HTTP responses only.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import provider_governor as pg  # noqa: E402

STAT_URL = "https://sh.dataspace.copernicus.eu/statistics/v1"


class FakeClock:
    def __init__(self, start: float = 1_800_000_000.0):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


def response(status: int, *, headers: dict | None = None, body=None) -> httpx.Response:
    request = httpx.Request("POST", STAT_URL)
    return httpx.Response(status, request=request, headers=headers or {}, json=body if body is not None else {})


def scripted(*responses, clock: FakeClock | None = None, request_seconds: float = 1.5):
    queue = list(responses)
    timeouts: list[float | None] = []

    def send(timeout=None):
        timeouts.append(timeout)
        if clock is not None:
            clock.now += request_seconds
        return queue.pop(0)
    send.timeouts = timeouts
    return send


def child_governor(tmp_path, clock, *, field_timeout=180.0, **policy):
    """A governor as a batch runner's child sees it: deadline = spawn + timeout - exit margin."""
    return pg.ProviderGovernor(pg.ProviderPolicy(**policy), tmp_path, run_id="r251a", stage="ndvi", clock=clock,
                               sleep=clock.sleep, deadline=clock.now + field_timeout - pg.CHILD_EXIT_MARGIN_SECONDS)


# ═══ Fix 1: timeout contract ════════════════════════════════════════════════

def test_default_effective_timeout_contract_fits_the_production_field_timeout():
    contract = pg.timeout_contract(pg.ProviderPolicy(), 180)
    assert contract["no_throttle_reserve_seconds"] == 51.0           # 10 + 15 + 1 + 5 + 2 x 10
    assert contract["max_retry_wait_capacity_seconds"] == 129.0
    assert contract["retry_budget_seconds"] == 120.0 <= contract["max_retry_wait_capacity_seconds"]
    assert contract["child_provider_window_seconds"] == 165.0       # the parent kills at 180 s


def test_a_retry_budget_that_cannot_fit_the_field_timeout_is_refused_before_any_provider_request(tmp_path):
    from scripts import collect_satellite as collector

    args = collector.parse_args(["--apply", "--field-ids", "4", "--indices", "ndvi", "--output-dir", str(tmp_path / "o"),
                                 "--state-dir", str(tmp_path / "s"), "--lock-dir", str(tmp_path / "l"),
                                 "--provider-429-retry-budget-seconds", "300"])
    with pytest.raises(collector.ContractError, match="does not fit the 180s field timeout"):
        collector.validate(args)
    code, summary = collector.run(args, child_runner=lambda *a: pytest.fail("child started"),
                                  lock_acquire=lambda *a, **k: "lock", lock_release=lambda lock: None,
                                  run_id_factory=lambda: "refused")
    assert code == 2 and summary["run_status"] == "FAILED"
    # The same budget is consistent with a field timeout that can hold it.
    ok = collector.parse_args(["--apply", "--field-ids", "4", "--indices", "ndvi", "--output-dir", str(tmp_path / "o2"),
                               "--state-dir", str(tmp_path / "s"), "--lock-dir", str(tmp_path / "l"),
                               "--provider-429-retry-budget-seconds", "300", "--field-timeout-seconds", "360"])
    assert collector.validate(ok)["timeout_contract"]["retry_budget_seconds"] == 300


def test_a_standalone_runner_refuses_an_incompatible_inherited_policy(tmp_path):
    from scripts import run_ndvi_collection_cycle as ndvi

    policy = pg.ProviderPolicy(retry_budget_seconds=300, max_backoff_seconds=60)
    env = {pg.POLICY_ENV: policy.to_json(), pg.PACER_DIR_ENV: str(tmp_path), pg.RUN_ID_ENV: "x", pg.STAGE_ENV: "ndvi"}
    with patch.dict(os.environ, env), patch.object(ndvi, "acquire_lock", return_value="lock"), \
            patch.object(ndvi, "release_lock"):
        code = ndvi.run(ndvi.parse_args(["--write", "--field-ids", "1", "--output-dir", str(tmp_path / "n")]),
                        child_runner=lambda *a: pytest.fail("child started"))
    summary = json.loads(next((tmp_path / "n").glob("cycle_*/cycle_summary.json")).read_text(encoding="utf-8"))
    assert code == 2 and "does not fit" in " ".join(summary["diagnostics"])


@pytest.mark.parametrize(("budget", "field_timeout", "ok"), [
    (849, 900, True),      # the largest budget the largest field timeout can hold
    (850, 900, False),
    (1800, 900, False),    # the policy's own maximum never fits a child
    (120, 171, True),      # 171 - 51 = 120
    (120, 170, False),
])
def test_maximum_permitted_retry_settings(budget, field_timeout, ok):
    policy = pg.ProviderPolicy(max_retries=10, retry_budget_seconds=budget, max_backoff_seconds=60)
    if ok:
        assert pg.timeout_contract(policy, field_timeout)["retry_budget_seconds"] == budget
    else:
        with pytest.raises(pg.ProviderPolicyError):
            pg.timeout_contract(policy, field_timeout)


def test_a_429_recovery_needing_more_than_180_s_ends_explicitly_under_the_default_contract(tmp_path):
    clock = FakeClock()
    spawned = clock.now
    gov = child_governor(tmp_path, clock)
    send = scripted(*[response(429, headers={"Retry-After": "70000"}) for _ in range(3)], response(200), clock=clock)
    with pytest.raises(pg.ProviderThrottleError) as caught:
        gov.post("statistical", send, timeout=60.0)
    assert caught.value.kind == "rate_limited" and caught.value.reason == "retry_budget"
    # Classified while the child is alive, well before the runner's 180 s kill.
    assert clock.now - spawned < 180 - pg.CHILD_EXIT_MARGIN_SECONDS
    assert gov.counters.last_failure["category"] == "rate_limited"


def test_the_same_recovery_succeeds_when_the_field_timeout_is_configured_to_hold_it(tmp_path):
    clock = FakeClock()
    spawned = clock.now
    pg.timeout_contract(pg.ProviderPolicy(retry_budget_seconds=400, max_backoff_seconds=60), 600)
    gov = child_governor(tmp_path, clock, field_timeout=600, retry_budget_seconds=400, max_backoff_seconds=60)
    send = scripted(*[response(429, headers={"Retry-After": "70000"}) for _ in range(3)], response(200), clock=clock)
    assert gov.post("statistical", send, timeout=60.0).status_code == 200
    assert clock.now - spawned > 180                                   # longer than the old kill
    assert clock.now - spawned < 600 - pg.CHILD_EXIT_MARGIN_SECONDS


def test_pacing_wait_plus_retry_backoff_never_outlives_the_child(tmp_path):
    clock = FakeClock()
    other = pg.ProviderGovernor(pg.ProviderPolicy(), tmp_path, run_id="r251a", stage="multi", clock=clock, sleep=clock.sleep)
    other.cooldown("statistical", 60.0)                               # another child's 429
    gov = pg.ProviderGovernor(pg.ProviderPolicy(), tmp_path, run_id="r251a", stage="ndvi", clock=clock,
                              sleep=clock.sleep, deadline=clock.now + 100)
    send = scripted(response(429, headers={"Retry-After": "45000"}), response(200), clock=clock)
    with pytest.raises(pg.ProviderThrottleError) as caught:
        gov.post("statistical", send, timeout=60.0)
    assert caught.value.reason == "child_deadline" and caught.value.kind == "rate_limited"
    assert clock.now <= gov.deadline                                  # it never slept past its deadline
    # The 45 s the provider asked for is kept for the next child.
    state = json.loads((tmp_path / pg.STATE_FILENAME).read_text(encoding="utf-8"))
    assert state["budgets"]["processing"]["cooldown_until"] >= clock.now + 40


def test_a_cooldown_beyond_the_deadline_is_reported_without_sleeping(tmp_path):
    clock = FakeClock()
    pg.ProviderGovernor(pg.ProviderPolicy(), tmp_path, run_id="r251a", stage="multi", clock=clock,
                        sleep=clock.sleep).cooldown("statistical", 300.0)
    gov = child_governor(tmp_path, clock)
    with pytest.raises(pg.ProviderDeadlineError) as caught:
        gov.post("statistical", scripted(response(200)), timeout=60.0)
    assert (caught.value.kind, caught.value.reason) == ("rate_limited", "child_deadline")
    assert clock.sleeps == []
    assert gov.counters.last_failure["category"] == "rate_limited"
    from services.satellite_safety import classify_provider_error, safe_provider_error_summary

    assert classify_provider_error(caught.value).category == "rate_limited"
    assert "endpoint=statistical" in safe_provider_error_summary(caught.value)


def test_request_timeouts_are_clipped_to_the_time_left(tmp_path):
    clock = FakeClock()
    gov = pg.ProviderGovernor(pg.ProviderPolicy(), tmp_path, run_id="r251a", stage="ndvi", clock=clock,
                              sleep=clock.sleep, deadline=clock.now + 30)
    send = scripted(response(200))
    gov.post("statistical", send, timeout=120.0)
    assert send.timeouts == [30.0]
    without_deadline = pg.ProviderGovernor(pg.ProviderPolicy(), tmp_path / "x", run_id="r", stage="ndvi",
                                           clock=clock, sleep=clock.sleep)
    send = scripted(response(200))
    without_deadline.post("oauth", send, timeout=None)
    assert send.timeouts == [pg.DEFAULT_REQUEST_TIMEOUT_SECONDS]


def test_persistent_throttling_terminates_inside_the_child_window(tmp_path):
    clock = FakeClock()
    spawned = clock.now
    gov = child_governor(tmp_path, clock, max_retries=10)
    send = scripted(*[response(429) for _ in range(20)], clock=clock)
    with pytest.raises(pg.ProviderThrottleError) as caught:
        gov.post("statistical", send, timeout=60.0)
    assert caught.value.kind == "rate_limited"
    assert clock.now - spawned <= 180 - pg.CHILD_EXIT_MARGIN_SECONDS


def test_without_a_governor_the_call_sites_keep_their_own_timeouts():
    from services.satellite import SentinelHubService
    from services.satellite_collection import MultiIndexSentinelHubService

    seen = []
    ndvi = SentinelHubService("fixture-id", "fixture-secret", provider="cdse")
    multi = MultiIndexSentinelHubService("fixture-id", "fixture-secret", provider="cdse")
    for service in (ndvi, multi):
        service._provider_post("statistical", lambda timeout: seen.append(timeout), timeout=60.0)
        service._provider_post("oauth", lambda timeout: seen.append(timeout), timeout=None)
    assert seen == [60.0, None, 60.0, None]


# ─── batch runners: per-child deadlines inside the cycle budget ────────────

def _ndvi_child(code=0, seen=None):
    def run(cmd, timeout):
        if seen is not None:
            seen.append((timeout, os.environ.get(pg.CHILD_DEADLINE_ENV)))
        field = int(cmd[cmd.index("--field-id") + 1])
        Path(cmd[cmd.index("--output-log") + 1]).write_text(json.dumps({
            "schema_version": 1, "field_id": field, "mode": "write", "date_from": cmd[cmd.index("--date-from") + 1],
            "date_to": cmd[cmd.index("--date-to") + 1], "exit_code": code, "inserted_count": 0,
            "skipped_existing_count": 1 if code == 0 else 0, "quality_blocked_count": 0, "error_count": 0}),
            encoding="utf-8")
        return {"exit_code": code, "timed_out": False, "stdout": "", "stderr": ""}
    return run


def test_every_child_gets_its_deadline_inside_its_own_timeout(tmp_path):
    from scripts import run_ndvi_collection_cycle as ndvi

    seen = []
    with patch.object(ndvi, "acquire_lock", return_value="lock"), patch.object(ndvi, "release_lock"):
        code = ndvi.run(ndvi.parse_args(["--write", "--field-ids", "1,2", "--output-dir", str(tmp_path / "n")]),
                        child_runner=_ndvi_child(0, seen))
    assert code == 0 and [timeout for timeout, _ in seen] == [180, 180]
    import time as real_time
    for timeout, deadline in seen:
        # spawn + 180 s - 15 s exit margin, i.e. comfortably inside the runner's kill.
        assert 0 < float(deadline) - real_time.time() <= 180 - pg.CHILD_EXIT_MARGIN_SECONDS
    assert pg.CHILD_DEADLINE_ENV not in os.environ                     # restored afterwards
    summary = json.loads(next((tmp_path / "n").glob("cycle_*/cycle_summary.json")).read_text(encoding="utf-8"))
    assert summary["timeout_contract"]["retry_budget_seconds"] == 120


@pytest.mark.parametrize("runner_name", ["ndvi", "multi"])
def test_a_runner_stops_starting_children_when_the_cycle_budget_is_spent(tmp_path, runner_name):
    from scripts import run_multi_index_collection_cycle as multi
    from scripts import run_ndvi_collection_cycle as ndvi

    runner = ndvi if runner_name == "ndvi" else multi
    import time as real_time
    env = {pg.POLICY_ENV: pg.ProviderPolicy().to_json(), pg.PACER_DIR_ENV: str(tmp_path), pg.RUN_ID_ENV: "x",
           pg.STAGE_ENV: runner_name, pg.CYCLE_DEADLINE_ENV: repr(real_time.time() + 60)}   # < 30 + 51 s left

    def multi_child(cmd, timeout):
        pytest.fail("child started after the cycle budget was spent")
    child = (lambda *a: pytest.fail("child started after the cycle budget was spent")) if runner_name == "ndvi" else multi_child
    with patch.dict(os.environ, env), patch.object(runner, "acquire_lock", return_value="lock"), \
            patch.object(runner, "release_lock"):
        code = runner.run(runner.parse_args(["--write", "--field-ids", "1,2", "--output-dir", str(tmp_path / "out")]),
                          child_runner=child)
    summary = json.loads(next((tmp_path / "out").glob("cycle_*/cycle_summary.json")).read_text(encoding="utf-8"))
    assert summary["provider_failure_category"] == "timeout"
    assert summary["unattempted_field_ids"] == [1, 2] and summary["successful_field_ids"] == []
    assert code == 1 and summary["exit_code"] == 1                   # unfinished work is never a clean batch


# ═══ Fix 2: completion evidence ══════════════════════════════════════════════

def _args(root: Path, *extra):
    from scripts import collect_satellite as collector

    return collector.parse_args(["--dry-run", "--field-ids", "4,5", "--indices", "ndvi,savi",
                                 "--output-dir", str(root / "output"), "--state-dir", str(root / "state"),
                                 "--lock-dir", str(root / "locks"), *extra])


def _run(root: Path, writer, exit_codes=None):
    from scripts import collect_satellite as collector

    calls = []

    def child(command, timeout):
        provider = "ndvi" if "run_ndvi_collection_cycle.py" in command[1] else "multi"
        calls.append(provider)
        output = Path(command[command.index("--output-dir") + 1])
        writer(provider, output, command)
        code = (exit_codes or {}).get(provider, 0)
        return {"exit_code": code, "timed_out": False, "stdout": "", "stderr": ""}
    code, summary = collector.run(_args(root), child_runner=child, lock_acquire=lambda *a, **k: "lock",
                                  lock_release=lambda lock: None, run_id_factory=lambda: "evidence")
    latest = json.loads((root / "output" / collector.LATEST_STATUS_FILENAME).read_text(encoding="utf-8"))
    return code, summary, latest, calls


def _valid(provider, output, command, **override):
    payload = {"selected_field_ids": [4, 5], "successful_field_ids": [4, 5], "failed_field_ids": [],
               "unattempted_field_ids": [], "success_count": 2, "failure_count": 0, "unattempted_count": 0,
               "inserted_count": 0, "skipped_existing_count": 2, "quality_blocked_count": 0, "timeout_count": 0,
               "exit_code": 0}
    payload.update(override)
    (output / "cycle_1").mkdir(exist_ok=True)
    (output / "cycle_1" / "cycle_summary.json").write_text(json.dumps(payload), encoding="utf-8")


def test_normal_valid_completed_batches_are_success(tmp_path):
    code, summary, latest, _ = _run(tmp_path, _valid)
    assert code == 0 and summary["run_status"] == "SUCCESS" and latest["status"] == "succeeded"
    assert all(stage["unverified_batch_count"] == 0 and stage["completed"] == 2 for stage in summary["stages"].values())


@pytest.mark.parametrize(("label", "writer", "reason"), [
    ("missing", lambda provider, output, command: None, "missing_summary"),
    ("corrupt", lambda provider, output, command: ((output / "cycle_1").mkdir(),
                                                   (output / "cycle_1" / "cycle_summary.json").write_text("{not json")),
     "unreadable_summary"),
    ("counters only", lambda provider, output, command: ((output / "cycle_1").mkdir(),
                                                         (output / "cycle_1" / "cycle_summary.json").write_text(json.dumps(
                                                             {"success_count": 2, "failure_count": 0}))),
     "incomplete_summary:selected_field_ids"),
])
def test_exit_zero_without_usable_evidence_is_never_success(tmp_path, label, writer, reason):
    code, summary, latest, _ = _run(tmp_path, writer)
    assert code == 1, label
    assert summary["run_status"] == "PARTIAL"
    assert latest["status"] == "failed" and latest["failure_category"] == "operational"
    for stage in summary["stages"].values():
        assert stage["unverified_batches"] == [{"batch": 1, "exit_code": 0, "reason": reason}]
        assert stage["unfinished_field_ids"] == [4, 5] and stage["completed"] == 0
    assert any("completion evidence" in item for item in summary["diagnostics"])


def test_an_oversized_summary_is_not_evidence(tmp_path):
    from scripts import collect_satellite as collector

    def writer(provider, output, command):
        (output / "cycle_1").mkdir()
        (output / "cycle_1" / "cycle_summary.json").write_text(
            json.dumps({"pad": "x" * (collector.MAX_CYCLE_SUMMARY_BYTES + 1)}), encoding="utf-8")
    code, summary, _, _ = _run(tmp_path, writer)
    assert code == 1
    assert summary["stages"]["ndvi"]["unverified_batches"][0]["reason"] == "oversized_summary"


@pytest.mark.parametrize(("override", "reason"), [
    ({"success_count": 1}, "count_mismatch:success_count"),
    ({"selected_field_ids": [4, 9], "successful_field_ids": [4, 9]}, "field_set_mismatch"),
    ({"successful_field_ids": [4, 5], "failed_field_ids": [5], "failure_count": 1}, "outcome_partition_mismatch"),
    ({"successful_field_ids": [4], "success_count": 1}, "outcome_partition_mismatch"),
    ({"successful_field_ids": [4], "failed_field_ids": [5], "success_count": 1, "failure_count": 1},
     "exit_zero_with_unfinished_fields"),
    ({"exit_code": 1}, "exit_code_mismatch"),
    ({"successful_field_ids": [4, -5]}, "incomplete_summary:successful_field_ids"),
    ({"skipped_existing_count": -1}, "incomplete_summary:skipped_existing_count"),
])
def test_incomplete_or_contradictory_counts_fail_closed(tmp_path, override, reason):
    code, summary, _, _ = _run(tmp_path, lambda provider, output, command: _valid(provider, output, command, **override))
    assert code == 1 and summary["run_status"] == "PARTIAL"
    assert summary["stages"]["ndvi"]["unverified_batches"][0]["reason"] == reason
    assert summary["stages"]["ndvi"]["unfinished_field_ids"] == [4, 5]


def test_partially_completed_ndvi_and_index_stages_are_partial_with_exact_unfinished_ids(tmp_path):
    def writer(provider, output, command):
        if provider == "ndvi":
            _valid(provider, output, command, successful_field_ids=[4], failed_field_ids=[5],
                   success_count=1, failure_count=1, exit_code=1)
        else:
            _valid(provider, output, command, successful_field_ids=[5], unattempted_field_ids=[4],
                   success_count=1, unattempted_count=1, exit_code=1)
    code, summary, latest, calls = _run(tmp_path, writer, exit_codes={"ndvi": 1, "multi": 1})
    assert calls == ["ndvi", "multi"]
    assert code == 1 and summary["run_status"] == "PARTIAL" and latest["status"] == "failed"
    assert summary["stages"]["ndvi"]["unfinished_field_ids"] == [5]
    assert summary["stages"]["multi"]["unfinished_field_ids"] == [4]
    assert all(stage["unverified_batch_count"] == 0 for stage in summary["stages"].values())


def test_an_incomplete_run_after_a_throttled_stage_is_classified_degraded_quota(tmp_path):
    def writer(provider, output, command):
        if provider == "ndvi":
            _valid(provider, output, command, successful_field_ids=[4], unattempted_field_ids=[5], success_count=1,
                   unattempted_count=1, exit_code=1, provider_failure_category="rate_limited")
        # the index stage child exits 0 but leaves no evidence at all
    code, summary, latest, calls = _run(tmp_path, writer, exit_codes={"ndvi": 1, "multi": 0})
    assert calls == ["ndvi", "multi"]
    assert code == 1 and summary["run_status"] == "DEGRADED"
    assert summary["stage_stops"] == {"ndvi": "rate_limited"}
    assert summary["stages"]["multi"]["unverified_batches"][0]["reason"] == "missing_summary"
    assert summary["stages"]["multi"]["unfinished_field_ids"] == [4, 5]
    assert latest["failure_category"] == "quota"                     # DB vocabulary, no new label


def test_the_cycle_deadline_reaches_every_batch_runner(tmp_path):
    seen = []

    def writer(provider, output, command):
        seen.append(os.environ.get(pg.CYCLE_DEADLINE_ENV))
        _valid(provider, output, command)
    import time as real_time
    before = real_time.time()
    _run(tmp_path, writer)
    assert len(seen) == 2 and all(value is not None for value in seen)
    assert all(before + 21600 - 5 <= float(value) <= real_time.time() + 21600 for value in seen)
    assert pg.CYCLE_DEADLINE_ENV not in os.environ
