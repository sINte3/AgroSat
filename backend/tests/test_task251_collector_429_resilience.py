"""TASK_251: Sentinel collector HTTP 429 resilience (pacing, bounded retries, no stage starvation).

No live provider call: every HTTP response is an ``httpx.Response`` built in-process, every
clock is a fake (except the one cross-process pacing test, which uses real processes and a
short real interval), and no database is touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import provider_governor as pg  # noqa: E402

STAT_URL = "https://sh.dataspace.copernicus.eu/statistics/v1"
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"


class FakeClock:
    """Shared wall clock for in-process 'children'; sleeping advances it."""

    def __init__(self, start: float = 1_800_000_000.0):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


def response(status: int, *, url: str = STAT_URL, headers: dict | None = None, body=None) -> httpx.Response:
    request = httpx.Request("POST", url, headers={"Authorization": "Bearer SECRET-TOKEN-VALUE"})
    if body is None:
        return httpx.Response(status, request=request, headers=headers or {}, text="")
    return httpx.Response(status, request=request, headers=headers or {}, json=body)


def governor(tmp_path: Path, clock: FakeClock, *, stage: str = "ndvi", run_id: str = "run1", **policy) -> pg.ProviderGovernor:
    return pg.ProviderGovernor(pg.ProviderPolicy(**policy), tmp_path, run_id=run_id, stage=stage,
                               clock=clock, sleep=clock.sleep)


def scripted(*responses):
    queue = list(responses)
    calls = []

    def send(timeout=None):
        calls.append(timeout)
        return queue.pop(0)
    send.calls = calls
    return send


# ─── A. HTTP 429 followed by a successful retry ─────────────────────────────

def test_a_429_then_success_is_retried_once_with_the_provider_delay(tmp_path):
    clock = FakeClock()
    gov = governor(tmp_path, clock)
    send = scripted(response(429, headers={"Retry-After": "3398"}), response(200, body={"data": []}))
    result = gov.post("statistical", send)
    assert result.status_code == 200
    assert len(send.calls) == 2
    assert gov.counters.http_429 == {"statistical": 1} and gov.counters.retries == 1
    # Retry-After 3398 ms = 3.4 s, but never faster than the 5 s pacing floor.
    assert gov.counters.cooldown_seconds == 5.0
    report = pg.run_report(tmp_path, "run1")
    assert report["stages"]["ndvi"]["http_429"] == {"statistical": 1}
    assert report["stages"]["ndvi"]["retry_after_seconds"] == [3.398]


# ─── B. Multiple 429 responses, then recovery ───────────────────────────────

def test_b_multiple_429_back_off_exponentially_then_recover(tmp_path):
    clock = FakeClock()
    gov = governor(tmp_path, clock)
    send = scripted(response(429), response(429), response(429), response(200, body={}))
    assert gov.post("statistical", send).status_code == 200
    assert gov.counters.retries == 3
    assert gov.counters.cooldown_seconds == 15 + 30 + 60      # base 15 s, doubling
    assert gov.counters.http_429["statistical"] == 3


# ─── C. Exhausted retry budget ──────────────────────────────────────────────

def test_c_persistent_429_raises_rate_limited_after_max_retries(tmp_path):
    clock = FakeClock()
    gov = governor(tmp_path, clock, max_retries=2)
    send = scripted(*[response(429, headers={"Retry-After": "1000"}) for _ in range(3)])
    with pytest.raises(pg.ProviderThrottleError) as caught:
        gov.post("statistical", send)
    assert caught.value.kind == "rate_limited" and caught.value.endpoint == "statistical"
    assert caught.value.attempts == 3 and len(send.calls) == 3
    assert gov.counters.last_failure["category"] == "rate_limited"


def test_c_total_wait_budget_is_never_exceeded(tmp_path):
    clock = FakeClock()
    gov = governor(tmp_path, clock, retry_budget_seconds=300)
    # Each 429 asks for 200 s: the first wait fits (200 <= 300), a second would not.
    send = scripted(*[response(429, headers={"Retry-After": "200000"}) for _ in range(5)])
    with pytest.raises(pg.ProviderThrottleError) as caught:
        gov.post("statistical", send)
    assert caught.value.kind == "rate_limited" and caught.value.reason == "retry_budget"
    assert len(send.calls) == 2
    # One 200 s wait was spent; the second is not waited but kept as the shared cooldown.
    assert gov.counters.cooldown_seconds == 400


def test_c_a_wait_beyond_the_policy_is_a_suspected_quota_and_is_not_retried(tmp_path):
    clock = FakeClock()
    gov = governor(tmp_path, clock)
    send = scripted(response(429, headers={"Retry-After": str(3_600_000)}))   # one hour
    with pytest.raises(pg.ProviderThrottleError) as caught:
        gov.post("statistical", send)
    assert caught.value.kind == "quota_suspected" and len(send.calls) == 1
    assert gov.counters.retries == 0


def test_c_a_provider_quota_message_is_a_suspected_quota(tmp_path):
    gov = governor(tmp_path, FakeClock())
    body = {"error": {"status": 429, "reason": "Too Many Requests", "message": "Monthly quota exhausted"}}
    with pytest.raises(pg.ProviderThrottleError) as caught:
        gov.post("statistical", scripted(response(429, body=body)))
    assert caught.value.kind == "quota_suspected"


# ─── D. Retry-After parsing and bounds ──────────────────────────────────────

@pytest.mark.parametrize(("value", "endpoint", "expected"), [
    ("3398", "statistical", 3.398),       # Sentinel Hub documents milliseconds
    ("0", "statistical", 0.0),
    ("2500", "statistical", 2.5),
    ("120", "oauth", 120.0),              # RFC 9110 delta-seconds on the token endpoint
    (None, "statistical", None),
    ("", "statistical", None),
    ("-5", "statistical", None),
    ("soon", "statistical", None),
    ("9" * 70, "statistical", None),
])
def test_d_retry_after_units_and_invalid_values(value, endpoint, expected):
    assert pg.parse_retry_after(value, endpoint) == expected


def test_d_retry_after_http_date_is_relative_to_the_clock():
    now = datetime(2026, 10, 9, 6, 0, tzinfo=timezone.utc)
    later = format_datetime(now + timedelta(seconds=45), usegmt=True)
    earlier = format_datetime(now - timedelta(seconds=45), usegmt=True)
    assert pg.parse_retry_after(later, "oauth", now=now.timestamp()) == 45.0
    assert pg.parse_retry_after(earlier, "oauth", now=now.timestamp()) == 0.0


def test_d_retry_after_below_the_pacing_floor_waits_the_floor(tmp_path):
    clock = FakeClock()
    gov = governor(tmp_path, clock)
    gov.post("statistical", scripted(response(429, headers={"Retry-After": "10"}), response(200, body={})))
    assert gov.counters.cooldown_seconds == 5.0           # never an immediate retry storm


# ─── E. Authentication failure is not treated as 429 ────────────────────────

@pytest.mark.parametrize("status", [401, 403])
def test_e_authentication_failures_are_returned_unretried_and_classified(tmp_path, status):
    from services.satellite_safety import classify_provider_error, safe_provider_error_summary

    gov = governor(tmp_path, FakeClock())
    send = scripted(response(status, url=TOKEN_URL, body={"error": "invalid_client"}))
    result = gov.post("oauth", send)
    assert result.status_code == status and len(send.calls) == 1
    assert gov.counters.http_429 == {} and gov.counters.retries == 0
    with pytest.raises(httpx.HTTPStatusError) as caught:
        result.raise_for_status()
    assert classify_provider_error(caught.value).category == "authentication"
    assert "endpoint=oauth" in safe_provider_error_summary(caught.value)


def test_e_unrelated_http_errors_are_not_retried(tmp_path):
    gov = governor(tmp_path, FakeClock())
    for status in (400, 422, 500, 503):
        send = scripted(response(status))
        assert gov.post("statistical", send).status_code == status
        assert len(send.calls) == 1
    assert gov.counters.retries == 0


# ─── F. Shared pacing between collector children ───────────────────────────

def test_f_two_child_governors_share_one_pacing_budget(tmp_path):
    clock = FakeClock()
    first = governor(tmp_path, clock, stage="ndvi")
    second = governor(tmp_path, clock, stage="ndvi")
    slots = []
    for gov in (first, second, first, second, first, second):
        gov.acquire("statistical")
        slots.append(clock.now)
    gaps = [b - a for a, b in zip(slots, slots[1:])]
    assert min(gaps) >= 5.0 - 1e-9
    rates = pg.run_report(tmp_path, "run1")["request_rates"]["processing"]
    assert rates["requests"] == 6 and rates["min_spacing_seconds"] >= 5.0


CHILD_SCRIPT = r"""
import sys, time, json
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from services.provider_governor import ProviderGovernor, ProviderPolicy
gov = ProviderGovernor(ProviderPolicy(min_interval_seconds=1.0), Path(sys.argv[2]), run_id="xproc", stage="ndvi")
stamps = []
for _ in range(3):
    gov.acquire("statistical")
    stamps.append(time.time())
print(json.dumps(stamps))
"""


def test_f_pacing_is_enforced_across_real_processes(tmp_path):
    """Two real processes, real clock: their requests interleave at >= the 1 s minimum."""
    script = tmp_path / "child.py"
    script.write_text(CHILD_SCRIPT, encoding="utf-8")
    processes = [subprocess.Popen([sys.executable, str(script), str(BACKEND), str(tmp_path / "pacer")],
                                  stdout=subprocess.PIPE, text=True) for _ in range(2)]
    stamps = []
    for process in processes:
        out, _ = process.communicate(timeout=60)
        assert process.returncode == 0
        stamps.extend(json.loads(out))
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(stamps) == 6 and min(gaps) >= 0.95


# ─── G. Shared pacing and cooldown between NDVI and supporting indices ─────

def test_g_ndvi_and_index_stages_share_the_processing_budget_and_cooldown(tmp_path):
    clock = FakeClock()
    ndvi = governor(tmp_path, clock, stage="ndvi")
    multi = governor(tmp_path, clock, stage="multi")
    ndvi.acquire("statistical")
    start = clock.now
    multi.acquire("statistical")
    assert clock.now - start >= 5.0                       # same budget across stages
    # An NDVI 429 cools the budget down for the index stage as well.
    ndvi.cooldown("statistical", 60.0)
    before = clock.now
    multi.acquire("statistical")
    assert clock.now - before >= 60.0 - 1e-9
    # The OAuth budget is separate and only lightly paced.
    before = clock.now
    multi.acquire("oauth")
    assert clock.now - before <= 1.0
    report = pg.run_report(tmp_path, "run1")
    assert set(report["stages"]) == {"ndvi", "multi"}


# ─── H/I. Stage starvation and DEGRADED / PARTIAL status (orchestrator) ────

def _canonical_args(root: Path):
    from scripts import collect_satellite as collector

    return collector.parse_args(["--apply", "--field-ids", "4,5", "--indices", "ndvi,savi,ndmi",
                                 "--output-dir", str(root / "output"), "--state-dir", str(root / "state"),
                                 "--lock-dir", str(root / "locks")])


def _child_writer(outcomes: dict[str, dict]):
    calls = []

    def child(command, timeout):
        provider = "ndvi" if "run_ndvi_collection_cycle.py" in command[1] else "multi"
        calls.append((provider, dict(os.environ)))
        spec = outcomes[provider]
        output = Path(command[command.index("--output-dir") + 1])
        cycle = output / f"cycle_{len(calls)}"
        cycle.mkdir()
        unattempted = spec.get("unattempted", [])
        (cycle / "cycle_summary.json").write_text(json.dumps({
            "success_count": len(spec["ok"]), "failure_count": len(spec["failed"]), "inserted_count": 0,
            "skipped_existing_count": len(spec["ok"]), "quality_blocked_count": 0, "timeout_count": 0,
            "selected_field_ids": [4, 5], "successful_field_ids": spec["ok"], "failed_field_ids": spec["failed"],
            "unattempted_field_ids": unattempted, "unattempted_count": len(unattempted), "exit_code": spec["code"],
            "provider_failure_category": spec.get("category"),
        }), encoding="utf-8")
        return {"exit_code": spec["code"], "timed_out": False, "stdout": "", "stderr": spec.get("stderr", "")}
    child.calls = calls
    return child


def _run_canonical(root: Path, child):
    from scripts import collect_satellite as collector

    with patch("services.autonomous_monitoring.begin_apply_run", return_value=None):
        return collector.run(_canonical_args(root), child_runner=child,
                             lock_acquire=lambda *a, **k: "lock", lock_release=lambda lock: None,
                             run_id_factory=lambda: "run251")


def test_h_ndvi_rate_limit_does_not_starve_the_supporting_index_stage(tmp_path):
    child = _child_writer({
        "ndvi": {"code": 1, "ok": [4], "failed": [5], "category": "rate_limited",
                 "stderr": "Sentinel provider error category=rate_limited status=429 retryable=true endpoint=statistical"},
        "multi": {"code": 0, "ok": [4, 5], "failed": []},
    })
    code, summary = _run_canonical(tmp_path, child)
    assert [provider for provider, _ in child.calls] == ["ndvi", "multi"]      # index stage ran
    assert code == 1
    assert summary["stage_stops"] == {"ndvi": "rate_limited"}
    assert summary["run_status"] == "DEGRADED"
    assert summary["stages"]["ndvi"]["unfinished_field_ids"] == [5]
    assert summary["stages"]["multi"]["unfinished_field_ids"] == []
    latest = json.loads((tmp_path / "output" / "collector_latest_status.json").read_text(encoding="utf-8"))
    assert latest["status"] == "failed" and latest["failure_category"] == "quota"
    # Every child received the run's shared governor environment, tagged per stage.
    stages = [env.get(pg.STAGE_ENV) for _, env in child.calls]
    assert stages == ["ndvi", "multi"]
    assert all(env.get(pg.RUN_ID_ENV) == "run251" for _, env in child.calls)
    assert all(env.get(pg.PACER_DIR_ENV) == str((tmp_path / "state").resolve()) for _, env in child.calls)
    assert json.loads(child.calls[0][1][pg.POLICY_ENV])["min_interval_seconds"] == 5.0
    # ... and the parent's environment is restored afterwards.
    assert pg.POLICY_ENV not in os.environ and pg.STAGE_ENV not in os.environ


def test_i_persistent_provider_failure_is_degraded_never_success(tmp_path):
    child = _child_writer({
        "ndvi": {"code": 1, "ok": [], "failed": [4], "unattempted": [5], "category": "rate_limited"},
        "multi": {"code": 1, "ok": [], "failed": [4], "unattempted": [5], "category": "rate_limited"},
    })
    code, summary = _run_canonical(tmp_path, child)
    assert code == 1 and summary["run_status"] == "DEGRADED"
    assert summary["stages"]["ndvi"]["unfinished_field_ids"] == [4, 5]
    assert summary["stages"]["multi"]["unfinished_field_ids"] == [4, 5]


def test_i_suspected_quota_stops_every_stage_and_keeps_unfinished_ids(tmp_path):
    child = _child_writer({
        "ndvi": {"code": 1, "ok": [], "failed": [4], "unattempted": [5], "category": "quota"},
        "multi": {"code": 0, "ok": [4, 5], "failed": []},
    })
    code, summary = _run_canonical(tmp_path, child)
    assert [provider for provider, _ in child.calls] == ["ndvi"]               # provider-wide stop
    assert code == 1 and summary["run_status"] == "DEGRADED"
    assert summary["stages"]["multi"]["unfinished_field_ids"] == [4, 5]       # never silently dropped
    assert summary["stages"]["multi"]["batches_run"] == 0


def test_i_ordinary_field_failure_is_partial_and_a_complete_run_is_success(tmp_path):
    from scripts import collect_satellite as collector

    partial = _child_writer({"ndvi": {"code": 1, "ok": [4], "failed": [5]}, "multi": {"code": 0, "ok": [4, 5], "failed": []}})
    code, summary = _run_canonical(tmp_path / "a", partial)
    assert code == 1 and summary["run_status"] == "PARTIAL"
    complete = _child_writer({"ndvi": {"code": 0, "ok": [4, 5], "failed": []}, "multi": {"code": 0, "ok": [4, 5], "failed": []}})
    code, summary = _run_canonical(tmp_path / "b", complete)
    assert code == 0 and summary["run_status"] == "SUCCESS"
    assert collector.run_status(0, {}, {"ndvi": {"unfinished_count": 1}}) == "PARTIAL"


def test_runners_stop_a_batch_on_a_rate_limited_child_without_respawning_it(tmp_path):
    from scripts import run_multi_index_collection_cycle as multi
    from scripts import run_ndvi_collection_cycle as ndvi

    def ndvi_child(cmd, _timeout):
        field = int(cmd[cmd.index("--field-id") + 1])
        Path(cmd[cmd.index("--output-log") + 1]).write_text(json.dumps({
            "schema_version": 1, "field_id": field, "mode": "write", "date_from": cmd[cmd.index("--date-from") + 1],
            "date_to": cmd[cmd.index("--date-to") + 1], "exit_code": 1, "inserted_count": 0,
            "skipped_existing_count": 0, "quality_blocked_count": 0, "error_count": 1,
            "provider_resilience": {"stage": "ndvi", "requests": {"statistical": 5}, "http_429": {"statistical": 5},
                                    "retries": 4, "cooldown_seconds": 285.0, "pacing_wait_seconds": 0.0,
                                    "last_failure": {"category": "rate_limited", "endpoint": "statistical", "status": 429}}}),
            encoding="utf-8")
        return {"exit_code": 1, "timed_out": False, "stdout": "", "stderr": ""}

    with patch.object(ndvi, "acquire_lock", return_value="lock"), patch.object(ndvi, "release_lock"):
        code = ndvi.run(ndvi.parse_args(["--write", "--field-ids", "1,2,3", "--max-attempts", "3",
                                         "--output-dir", str(tmp_path / "n")]), child_runner=ndvi_child)
    summary = json.loads(next((tmp_path / "n").glob("cycle_*/cycle_summary.json")).read_text(encoding="utf-8"))
    assert code == 1 and summary["provider_failure_category"] == "rate_limited"
    assert summary["attempt_counts"] == 1 and summary["unattempted_field_ids"] == [2, 3]
    assert summary["provider_resilience"]["http_429"] == {"statistical": 5}

    def multi_child(cmd, _timeout):
        Path(cmd[cmd.index("--output-log") + 1]).write_text(json.dumps({
            "errors": 1, "db_inserted": 0, "provider_resilience": {"last_failure": {"category": "rate_limited"}}}),
            encoding="utf-8")
        return {"exit_code": 1, "timed_out": False,
                "stdout": "ERROR: Sentinel provider error category=rate_limited status=429 retryable=true endpoint=statistical",
                "stderr": ""}

    with patch.object(multi, "acquire_lock", return_value="lock"), patch.object(multi, "release_lock"):
        code = multi.run(multi.parse_args(["--write", "--field-ids", "1,2,3", "--max-attempts", "3",
                                           "--output-dir", str(tmp_path / "m")]), child_runner=multi_child)
    summary = json.loads(next((tmp_path / "m").glob("cycle_*/cycle_summary.json")).read_text(encoding="utf-8"))
    assert code == 1 and summary["provider_failure_category"] == "rate_limited"
    assert summary["attempt_counts"] == 1 and summary["unattempted_field_ids"] == [2, 3]


def test_a_raw_provider_429_line_is_stage_level_not_provider_wide():
    assert pg.child_failure_category(["Sentinel provider error category=quota_or_rate_limit status=429"]) == "rate_limited"
    assert pg.child_failure_category(["category=quota_suspected status=429"]) == "quota"
    assert pg.child_failure_category(["provider category=authentication status=403"]) == "authentication"
    assert pg.child_failure_category([], "rate_limited") == "rate_limited"


# ─── J/K. Idempotent retries and unchanged quality gates ────────────────────

def _ndvi_interval(day_from, day_to, *, sample_count=120, no_data_count=24, mean=0.62):
    stats = {"sampleCount": sample_count, "noDataCount": no_data_count, "mean": mean, "min": 0.31, "max": 0.84,
             "stDev": 0.07, "percentiles": {"10.0": 0.52, "90.0": 0.71}}
    return {"interval": {"from": f"{day_from}T00:00:00Z", "to": f"{day_to}T00:00:00Z"},
            "outputs": {"ndvi": {"bands": {"B0": {"stats": stats}}},
                        "observed": {"bands": {"B0": {"stats": {"sampleCount": sample_count, "noDataCount": 0}}}}}}


# ~0.84 ha: 84 field pixels at 10 m, inside the 120-pixel fixture grid.
FIELD_WKT = "POLYGON((64.40 39.80, 64.4011 39.80, 64.4011 39.8008, 64.40 39.8008, 64.40 39.80))"


def _service_with_provider(statistical_responses, gov=None):
    from services.satellite import SentinelHubService

    service = SentinelHubService("fixture-id", "fixture-secret", provider="cdse")
    if gov is not None:
        service.provider_governor = gov
    payloads = []
    queue = list(statistical_responses)

    def fake_post(url, **kwargs):
        if url == service._provider_endpoints.token_url:
            return response(200, url=url, body={"access_token": "fixture-access", "expires_in": 3600})
        payloads.append(json.dumps(kwargs.get("json"), sort_keys=True))
        return queue.pop(0)
    return service, fake_post, payloads


def test_j_a_retried_request_is_identical_and_persists_once(tmp_path):
    from scripts import collect_ndvi

    ok = response(200, body={"data": [_ndvi_interval("2026-10-03", "2026-10-04")]})
    gov = governor(tmp_path, FakeClock(), stage="ndvi")
    service, fake_post, payloads = _service_with_provider([response(429, headers={"Retry-After": "2000"}), ok], gov)
    stored: set[tuple[int, str]] = set()
    writes = []

    def writer(field_id, _enterprise, record, _start, _end):          # ndvi_records UNIQUE (field, date)
        writes.append(record["captured_date"])
        key = (field_id, record["captured_date"])
        if key in stored:
            return False
        stored.add(key)
        return True

    log = tmp_path / "child.json"
    args = collect_ndvi.parse_args(["--write", "--field-id", "246", "--output-log", str(log),
                                    "--date-from", "2026-09-24", "--date-to", "2026-10-08"])
    with patch("services.satellite.httpx.post", side_effect=fake_post), \
            patch.object(collect_ndvi, "acquire_lock", return_value="lock"), patch.object(collect_ndvi, "release_lock"):
        code = collect_ndvi.run(args, lookup=lambda _: {"geometry_wkt": FIELD_WKT, "enterprise_id": 9},
                                service_factory=lambda: service, writer=writer)
    result = json.loads(log.read_text(encoding="utf-8"))
    assert code == 0 and result["inserted_count"] == 1
    assert len(payloads) == 2 and payloads[0] == payloads[1]          # the same idempotent read, re-sent
    assert writes == ["2026-10-03"]                                   # one observation, written once
    # A repeat run (scheduled cycle) skips the stored acquisition instead of duplicating it.
    service2, fake_post2, _ = _service_with_provider([ok])
    with patch("services.satellite.httpx.post", side_effect=fake_post2), \
            patch.object(collect_ndvi, "acquire_lock", return_value="lock"), patch.object(collect_ndvi, "release_lock"):
        collect_ndvi.run(args, lookup=lambda _: {"geometry_wkt": FIELD_WKT, "enterprise_id": 9},
                         service_factory=lambda: service2, writer=writer)
    assert json.loads(log.read_text(encoding="utf-8"))["skipped_existing_count"] == 1
    assert stored == {(246, "2026-10-03")}


def test_k_retrying_does_not_change_the_parsed_field_statistic(tmp_path):
    body = {"data": [_ndvi_interval("2026-10-03", "2026-10-04"), _ndvi_interval("2026-10-06", "2026-10-07", no_data_count=115)]}
    plain, plain_post, _ = _service_with_provider([response(200, body=body)])
    with patch("services.satellite.httpx.post", side_effect=plain_post):
        expected = plain.get_ndvi_stats(FIELD_WKT, date(2026, 9, 24), date(2026, 10, 8))
    retried, retried_post, _ = _service_with_provider(
        [response(429, headers={"Retry-After": "1000"}), response(200, body=body)], governor(tmp_path, FakeClock()))
    with patch("services.satellite.httpx.post", side_effect=retried_post):
        actual = retried.get_ndvi_stats(FIELD_WKT, date(2026, 9, 24), date(2026, 10, 8))
    assert actual == expected
    assert actual["captured_date"] == "2026-10-03" and actual["field_samples_accepted"] is True
    assert actual["valid_pixel_count"] == 96                          # TASK_246 multi-pixel contract intact


def test_k_supporting_index_service_retries_the_same_way(tmp_path):
    from services.satellite_collection import MultiIndexSentinelHubService

    values = {"savi": 0.41, "evi": 0.36, "ndmi": 0.18, "ndre": 0.29}
    outputs = {code: {"bands": {"B0": {"stats": {"sampleCount": 342, "noDataCount": 200, "mean": mean, "min": mean - 0.2,
                                                   "max": mean + 0.2, "stDev": 0.05,
                                                   "percentiles": {"10.0": mean - 0.08, "90.0": mean + 0.08}}}}}
               for code, mean in values.items()}
    outputs["observed"] = {"bands": {"B0": {"stats": {"sampleCount": 342, "noDataCount": 200}}}}
    body = {"data": [{"interval": {"from": "2026-10-03T00:00:00Z", "to": "2026-10-04T00:00:00Z"}, "outputs": outputs}]}
    service = MultiIndexSentinelHubService("fixture-id", "fixture-secret", provider="cdse")
    service.provider_governor = governor(tmp_path, FakeClock(), stage="multi")
    queue = [response(429, headers={"Retry-After": "1500"}), response(200, body=body)]

    def fake_post(url, **kwargs):
        if url == service._provider_endpoints.token_url:
            return response(200, url=url, body={"access_token": "fixture-access", "expires_in": 3600})
        return queue.pop(0)
    with patch("services.satellite_collection.httpx.post", side_effect=fake_post):
        records = service.collect_indices_flat(FIELD_WKT, list(values), date(2026, 9, 24), date(2026, 10, 9))
    assert {r["index_code"] for r in records} == set(values)
    assert all(r["valid_pixel_count"] == 142 and r["field_samples_accepted"] for r in records)
    assert service.provider_governor.counters.http_429 == {"statistical": 1}


def test_k_web_process_services_never_get_a_governor():
    from services.satellite import SentinelHubService
    from services.satellite_collection import MultiIndexSentinelHubService

    assert SentinelHubService.provider_governor is None and MultiIndexSentinelHubService.provider_governor is None
    assert pg.governor_from_environment({}) is None


# ─── L. Scheduled Task / runner contract ────────────────────────────────────

def test_l_the_production_runner_arguments_still_parse_with_the_default_policy(tmp_path):
    from scripts import collect_satellite as collector

    runner = (BACKEND.parent / "ops").rglob("Invoke-Collector.ps1")
    text = next(runner).read_text(encoding="utf-8-sig")
    for flag in ("--provider-min-interval-seconds", "--provider-429-max-retries"):
        assert flag not in text                                       # the signed runner is unchanged
    args = collector.parse_args(["--apply", "--all-active-fields", "--indices", "ndvi,savi,evi,ndmi,ndre",
                                 "--batch-size", "25", "--lookback-days", "14", "--max-attempts", "2",
                                 "--retry-base-seconds", "2", "--field-timeout-seconds", "180",
                                 "--cycle-timeout-seconds", "21600", "--output-dir", str(tmp_path / "o"),
                                 "--state-dir", str(tmp_path / "s"), "--lock-dir", str(tmp_path / "l")])
    plan = collector.validate(args)
    assert plan["provider_policy"] == pg.ProviderPolicy()
    assert plan["provider_policy"].min_interval_seconds == 5.0
    # TASK_251A: the default retry policy fits the production 180 s field timeout.
    assert plan["timeout_contract"]["field_timeout_seconds"] == 180
    assert plan["timeout_contract"]["max_retry_wait_capacity_seconds"] >= plan["provider_policy"].retry_budget_seconds


@pytest.mark.parametrize("flag,value", [
    ("--provider-min-interval-seconds", "0.5"), ("--provider-min-interval-seconds", "121"),
    ("--provider-429-max-retries", "11"), ("--provider-429-retry-budget-seconds", "5"),
    ("--provider-429-max-retry-after-seconds", "4000"),
])
def test_l_policy_settings_are_bounded(tmp_path, flag, value):
    from scripts import collect_satellite as collector

    args = collector.parse_args(["--dry-run", "--field-ids", "4", "--indices", "ndvi", "--output-dir", str(tmp_path / "o"),
                                 "--state-dir", str(tmp_path / "s"), "--lock-dir", str(tmp_path / "l"), flag, value])
    with pytest.raises(collector.ContractError):
        collector.validate(args)


def test_l_policy_rejects_inconsistent_or_unknown_settings():
    with pytest.raises(pg.ProviderPolicyError):
        pg.ProviderPolicy(base_backoff_seconds=200, max_backoff_seconds=100)
    with pytest.raises(pg.ProviderPolicyError):
        pg.ProviderPolicy.from_mapping({"min_interval_seconds": 5, "unexpected": 1})
    with pytest.raises(pg.ProviderPolicyError):
        pg.policy_from_environment({pg.POLICY_ENV: "{not json"})


# ─── M. No infinite loops, unbounded waits or overlapping runs ─────────────

def test_m_a_corrupt_or_future_pacer_state_never_causes_an_unbounded_wait(tmp_path):
    clock = FakeClock()
    (tmp_path / pg.STATE_FILENAME).write_text(json.dumps({
        "schema_version": 1, "run": {"run_id": "run1", "stages": {}, "timestamps": {}},
        "budgets": {"processing": {"next_at": clock.now + 10**9, "cooldown_until": clock.now + 10**9},
                    "oauth": {"next_at": 0, "cooldown_until": 0}}}), encoding="utf-8")
    gov = governor(tmp_path, clock)
    waited = gov.acquire("statistical")
    assert waited <= gov.policy.max_retry_after_seconds
    (tmp_path / pg.STATE_FILENAME).write_text("not json", encoding="utf-8")
    assert governor(tmp_path, clock).acquire("statistical") <= 5.0     # reset, never trusted blindly


def test_m_an_always_throttled_provider_terminates(tmp_path):
    clock = FakeClock()
    gov = governor(tmp_path, clock, max_retries=10, retry_budget_seconds=1800)
    calls = []

    def send(timeout=None):
        calls.append(1)
        return response(429)
    with pytest.raises(pg.ProviderThrottleError):
        gov.post("statistical", send)
    assert len(calls) <= 11 and gov.counters.cooldown_seconds <= 1800 + gov.policy.max_backoff_seconds


def test_m_a_held_pacer_lock_times_out_instead_of_hanging(tmp_path):
    held = pg._FileLock(tmp_path / pg.LOCK_FILENAME, 5)
    from services.satellite_safety import classify_provider_error

    with held:
        gov = governor(tmp_path, FakeClock(), lock_timeout_seconds=1)
        with pytest.raises(pg.ProviderPacingError) as caught:
            gov.acquire("statistical")
    # A local pacer problem is not reported as provider throttling.
    assert classify_provider_error(caught.value).category == "pacing_unavailable"
    assert pg.child_failure_category(["category=pacing_unavailable"], had_errors=True) == "provider_error"


def test_m_overlapping_collector_runs_are_still_refused(tmp_path):
    from scripts import collect_satellite as collector

    code, summary = collector.run(_canonical_args(tmp_path), child_runner=lambda *a: pytest.fail("child ran"),
                                  lock_acquire=Mock(side_effect=SystemExit(3)), lock_release=lambda lock: None,
                                  run_id_factory=lambda: "overlap")
    assert code == 3 and "LOCK_CONTENTION" in summary["diagnostics"]


# ─── Diagnostics are secret-free ────────────────────────────────────────────

def test_diagnostics_keep_only_allow_listed_numeric_headers(tmp_path):
    gov = governor(tmp_path, FakeClock())
    throttled = response(429, headers={"Retry-After": "2000", "X-RateLimit-Remaining": "0",
                                       "Set-Cookie": "session=COOKIE-SECRET", "X-Debug": "Bearer LEAKED"},
                         body={"error": {"code": "RATE_LIMIT_EXCEEDED", "message": "slow down, token=abc123"}})
    gov.post("statistical", scripted(throttled, response(200, body={})))
    state_text = (tmp_path / pg.STATE_FILENAME).read_text(encoding="utf-8")
    for secret in ("SECRET-TOKEN-VALUE", "COOKIE-SECRET", "LEAKED", "abc123", "slow down"):
        assert secret not in state_text
    report = pg.run_report(tmp_path, "run1")["stages"]["ndvi"]
    assert report["rate_limit_headers"] == {"retry-after": "2000", "x-ratelimit-remaining": "0"}
    assert report["provider_error_codes"] == ["RATE_LIMIT_EXCEEDED"]


def test_throttle_errors_keep_existing_handlers_and_name_the_endpoint(tmp_path):
    from services.satellite_safety import classify_provider_error, safe_provider_error_summary

    gov = governor(tmp_path, FakeClock(), max_retries=0)
    with pytest.raises(httpx.HTTPStatusError) as caught:             # subclass: old handlers still catch it
        gov.post("oauth", scripted(response(429, url=TOKEN_URL)))
    result = classify_provider_error(caught.value)
    assert (result.category, result.retryable, result.status_code) == ("rate_limited", True, 429)
    summary = safe_provider_error_summary(caught.value)
    assert "category=rate_limited" in summary and "endpoint=oauth" in summary and "SECRET" not in summary


# ─── Request-rate simulation (deterministic, fake clock) ───────────────────

class SlidingWindowProvider:
    """A provider that throttles more than ``limit`` requests in any 60 s window."""

    def __init__(self, clock: FakeClock, limit: int, request_seconds: float = 1.5):
        self.clock, self.limit, self.request_seconds = clock, limit, request_seconds
        self.accepted: list[float] = []
        self.throttled = 0

    def send(self, timeout: float | None = None) -> httpx.Response:
        now = self.clock.now
        window = [t for t in self.accepted if now - t < 60.0]
        self.clock.now += self.request_seconds
        if len(window) >= self.limit:
            self.throttled += 1
            wait_ms = int((60.0 - (now - window[0])) * 1000) + 1
            return response(429, headers={"Retry-After": str(wait_ms)})
        self.accepted.append(now)
        return response(200, body={})


def simulate(tmp_path: Path, *, fields: int, limit: int, paced: bool) -> dict:
    clock = FakeClock()
    provider = SlidingWindowProvider(clock, limit)
    if not paced:                                  # pre-TASK_251: one request every ~2 s, no retry
        for index in range(fields):
            result = provider.send()
            clock.now += 0.5
            if result.status_code == 429:
                return {"completed": index, "throttled": provider.throttled, "stopped": True}
        return {"completed": fields, "throttled": provider.throttled, "stopped": False}
    gov = governor(tmp_path, clock, run_id="sim")
    start = clock.now
    for _ in range(fields):
        gov.post("statistical", provider.send)
    rates = pg.run_report(tmp_path, "sim")["request_rates"]["processing"]
    return {"completed": fields, "throttled": provider.throttled, "stopped": False,
            "minutes": round((clock.now - start) / 60, 1), "max_in_60s": rates["max_in_any_60_seconds"]}


def test_simulated_cycle_completes_at_the_observed_provider_limit(tmp_path):
    # Observed in production (TASK_250): throttling at ~21-25 requests/min.
    legacy = simulate(tmp_path / "legacy", fields=550, limit=21, paced=False)
    paced = simulate(tmp_path / "paced", fields=550, limit=21, paced=True)
    assert legacy["stopped"] and legacy["completed"] < 100
    assert paced["completed"] == 550 and paced["throttled"] == 0
    assert paced["max_in_60s"] <= 12 and 45 <= paced["minutes"] <= 47


def test_simulated_cycle_adapts_to_a_stricter_unknown_limit(tmp_path):
    # If the real limit were only 8/min, shared cooldowns keep the cycle going (slower, bounded).
    paced = simulate(tmp_path, fields=60, limit=8, paced=True)
    assert paced["completed"] == 60 and paced["throttled"] > 0
    assert paced["max_in_60s"] <= 12
