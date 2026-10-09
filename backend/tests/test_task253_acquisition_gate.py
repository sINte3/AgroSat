"""TASK_253: acquisition-aware preflight of the canonical Sentinel collector.

Every provider answer here is a deterministic fixture: a simulated Catalog with a
publication timeline, fake batch children that write the real completion evidence
(tests/collector_evidence.py) and, where governance is under test, the real
SentinelHubService and ProviderGovernor with ``httpx.post`` replaced. No Sentinel Hub,
Catalog or OAuth request is ever sent. Test names carry the TASK_253 §11 case numbers.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from collector_evidence import write_cycle_summary
from task253_support import ELSEWHERE, START, WEST_SWATH, Catalog, FakeDate, feature, md5

BACKEND = Path(__file__).resolve().parents[1]

from scripts import collect_satellite as collector  # noqa: E402
from services import acquisition_gate as gate  # noqa: E402
from services import provider_governor as pg  # noqa: E402

FIELD_IDS = (11, 12, 13, 14, 15)
AOI = (64.357, 39.912, 64.537, 40.048)
HULL = {"type": "Polygon", "coordinates": [[[64.36, 39.92], [64.53, 39.92], [64.53, 40.04], [64.36, 40.04],
                                            [64.36, 39.92]]]}


def make_scope(field_ids=FIELD_IDS, edited: int | None = None) -> gate.Scope:
    rows = [(field_id, md5(f"geometry-{field_id}{'-edited' if field_id == edited else ''}")) for field_id in field_ids]
    return gate.scope_from_rows(rows, AOI, HULL)


class Env:
    """One collector deployment: state, output and lock directories that persist across cycles."""

    def __init__(self, root: Path, monkeypatch):
        self.root = root
        self.catalog = Catalog()
        self.scope = make_scope()
        self.identity = ("cdse", "official_cdse_sentinel_hub_https")
        self.backend_root = BACKEND
        self.children: list[list[str]] = []
        self.child_codes: dict[str, int] = {}
        self.child_counters: dict[str, dict] = {}
        self.factory_calls = 0
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(collector, "date", FakeDate)
        monkeypatch.setattr(collector, "resolve_active_field_ids", lambda: list(self.scope.field_ids))
        FakeDate.current = START

    def advance(self, days: int = 1):
        FakeDate.current = FakeDate.current + timedelta(days=days)
        self.catalog.today = FakeDate.current

    def args(self, *extra, gate_on=True, indices="ndvi,savi,evi,ndmi,ndre"):
        flags = ["--apply", "--all-active-fields", "--batch-size", "2", "--indices", indices,
                 "--output-dir", str(self.root / "output"), "--state-dir", str(self.root / "state"),
                 "--lock-dir", str(self.root / "locks"), *extra]
        if gate_on:
            flags.append("--acquisition-gate")
        return collector.parse_args(flags)

    def child(self, command, timeout):
        self.children.append(command)
        provider = "ndvi" if "run_ndvi_collection_cycle.py" in command[1] else "multi"
        code = self.child_codes.get(provider, 0)
        ids = command[command.index("--field-ids") + 1].split(",")
        counters = self.child_counters.get(provider) or (
            {"success_count": len(ids)} if code == 0 else {"success_count": len(ids) - 1, "failure_count": 1})
        write_cycle_summary(command, code, counters)
        return {"exit_code": code, "timed_out": False, "stdout": "", "stderr": ""}

    def gate_factory(self, plan, args, run_id, cycle_deadline):
        self.factory_calls += 1
        return collector.build_acquisition_gate(
            plan, args, run_id, cycle_deadline, scope_resolver=lambda: self.scope,
            search_factory=lambda: (self.catalog.search, self.catalog), identity=lambda: self.identity,
            backend_root=self.backend_root)

    def cycle(self, *extra, gate_on=True, indices="ndvi,savi,evi,ndmi,ndre", gate_factory=None):
        self.children = []
        self.catalog.calls = []
        self.catalog.counters.requests = {}
        run_id = f"run{FakeDate.current.strftime('%m%d')}{len(list((self.root / 'output').glob('run_*'))):02d}"
        with patch("services.autonomous_monitoring.begin_apply_run", return_value=None):
            code, summary = collector.run(
                self.args(*extra, gate_on=gate_on, indices=indices), child_runner=self.child,
                lock_acquire=lambda *a, **k: "lock", lock_release=lambda lock: None,
                run_id_factory=lambda: run_id, gate_factory=gate_factory or self.gate_factory)
        latest = json.loads((self.root / "output" / collector.LATEST_STATUS_FILENAME).read_text(encoding="utf-8"))
        return SimpleNamespace(code=code, summary=summary, latest=latest, children=list(self.children),
                               gate=summary.get("acquisition_gate"), catalog_calls=list(self.catalog.calls))

    def checkpoint(self):
        document, problem = gate.load_checkpoint(self.root / "state")
        return document, problem


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def baseline(env: Env, *days_ago: int) -> SimpleNamespace:
    """Publish acquisitions and run the first verified full cycle."""
    for offset in days_ago or (2, 7):
        env.catalog.publish(feature(START - timedelta(days=offset)), START - timedelta(days=offset - 1))
    result = env.cycle()
    assert result.code == 0 and result.summary["run_status"] == "SUCCESS", result.summary
    return result


def assert_full(result, *reasons):
    assert result.gate["outcome"] == gate.OUTCOME_COLLECT, result.gate
    assert result.gate["statistical_collection"] == "performed"
    assert len(result.children) == 6                      # 3 NDVI + 3 multi batches of 5 fields
    for reason in reasons:
        assert reason in result.gate["reasons"], result.gate["reasons"]


def assert_skip(result):
    assert result.gate["outcome"] == gate.OUTCOME_SKIP, result.gate
    assert result.children == []
    assert result.code == 0 and result.summary["run_status"] == "NO_NEW_ACQUISITIONS"
    assert result.summary["stages"] == {}
    assert result.latest["status"] == "succeeded" and result.latest["providers"] == []
    assert result.latest["acquisition_gate"] == {"outcome": "NO_NEW_ACQUISITIONS", "statistical_collection": "skipped"}


# ═══ 1-3: checkpoint lifecycle ═══════════════════════════════════════════════

def test_01_initial_execution_without_a_checkpoint_collects_in_full(env):
    env.catalog.publish(feature(START - timedelta(days=2)), START - timedelta(days=1))
    result = env.cycle()
    assert_full(result, gate.REASON_NO_CHECKPOINT)
    assert result.gate["discovery"]["complete"] is True
    assert result.gate["checkpoint_before"] == "absent"


def test_02_the_first_complete_cycle_establishes_a_verified_checkpoint(env):
    result = baseline(env, 2, 7)
    document, problem = env.checkpoint()
    assert problem is None and document["status"] == gate.STATE_VERIFIED
    assert document["chain_run_id"] == result.summary["run_id"]
    verified = document["verified"]
    assert verified["full_run_id"] == result.summary["run_id"] and verified["skips_since_full"] == 0
    assert [entry["day"] for entry in verified["inventory"]] == ["2026-10-03", "2026-10-08"]
    assert verified["full_date_to"] == "2026-10-10" and verified["full_date_from"] == "2026-09-26"
    assert result.gate["checkpoint_after"] == gate.STATE_VERIFIED


def test_03_an_unchanged_inventory_skips_the_statistical_stages(env):
    baseline(env, 2, 7)
    env.advance()
    result = env.cycle()
    assert_skip(result)
    assert result.gate["reasons"] == []
    assert len(result.catalog_calls) == 1
    document, _ = env.checkpoint()
    assert document["status"] == gate.STATE_VERIFIED and document["verified"]["skips_since_full"] == 1
    assert document["chain_run_id"] == result.summary["run_id"]


# ═══ 4-5, 16-18: acquisition inventory changes ═══════════════════════════════

def test_04_a_new_acquisition_after_the_baseline_triggers_a_full_collection(env):
    baseline(env, 2, 7)
    env.advance()
    env.catalog.publish(feature(START))
    result = env.cycle()
    assert_full(result, gate.REASON_NEW_ACQUISITION)
    assert result.summary["run_status"] == "SUCCESS"
    document, _ = env.checkpoint()
    assert "2026-10-10" in [entry["day"] for entry in document["verified"]["inventory"]]


def test_05_a_new_acquisition_over_one_part_of_the_area_collects_every_field(env):
    baseline(env, 2, 7)
    env.advance()
    env.catalog.publish(feature(START, tile="T41SQB", geometry=WEST_SWATH))
    result = env.cycle()
    assert_full(result, gate.REASON_NEW_ACQUISITION)
    planned = sorted(int(value) for command in result.children if "run_ndvi" in command[1]
                     for value in command[command.index("--field-ids") + 1].split(","))
    assert planned == list(FIELD_IDS)                     # the whole scope, not only the swath


def test_05b_an_acquisition_outside_the_area_is_not_requested_at_all(env):
    baseline(env, 2, 7)
    env.advance()
    env.catalog.publish(feature(START, geometry=ELSEWHERE))
    assert_skip(env.cycle())


def test_16_delayed_catalog_publication_is_collected_on_the_day_it_appears(env):
    baseline(env, 2, 7)
    late = START - timedelta(days=1)
    env.catalog.publish(feature(late), START + timedelta(days=2))       # sensed 10-09, listed 10-12
    env.advance()
    assert_skip(env.cycle())                                            # 10-11: not listed yet
    env.advance()
    result = env.cycle()                                                # 10-12: listed, collected
    assert_full(result, gate.REASON_NEW_ACQUISITION)
    assert result.gate["detail"]["new_items"] == [feature(late)["id"]]


def test_17_a_previously_unseen_older_acquisition_inside_the_lookback_triggers_collection(env):
    baseline(env, 2, 7)
    env.advance()
    env.catalog.publish(feature(START - timedelta(days=12), tile="T41SPC"))
    assert_full(env.cycle(), gate.REASON_NEW_ACQUISITION)


def test_18_reprocessing_or_metadata_change_triggers_collection(env):
    original = feature(START - timedelta(days=2))
    env.catalog.publish(original, START - timedelta(days=1))
    env.cycle()
    env.advance()
    changed = json.loads(json.dumps(original))
    changed["properties"]["eo:cloud_cover"] = 3.0                       # same id, new metadata
    env.catalog.published = [(START, changed)]
    assert_full(env.cycle(), gate.REASON_ACQUISITION_CHANGED)
    env.advance()
    reprocessed = feature(START - timedelta(days=2), baseline="N0512")  # new product id, old one gone
    env.catalog.published = [(START, reprocessed)]
    result = env.cycle()
    assert_full(result, gate.REASON_NEW_ACQUISITION, gate.REASON_ACQUISITION_REMOVED)


def test_18b_a_listed_acquisition_that_disappears_inside_the_window_triggers_collection(env):
    baseline(env, 2, 7)
    env.catalog.published = env.catalog.published[:1]
    env.advance()
    assert_full(env.cycle(), gate.REASON_ACQUISITION_REMOVED)


# ═══ 6-11: scope and contract changes ════════════════════════════════════════

@pytest.mark.parametrize("change", ["activated", "deactivated", "geometry"])
def test_06_07_08_scope_changes_trigger_collection(env, change):
    baseline(env, 2, 7)
    env.advance()
    env.scope = {"activated": make_scope(FIELD_IDS + (16,)), "deactivated": make_scope(FIELD_IDS[:-1]),
                 "geometry": make_scope(edited=13)}[change]
    result = env.cycle()
    assert result.gate["outcome"] == gate.OUTCOME_COLLECT
    assert gate.REASON_SCOPE_CHANGED in result.gate["reasons"]
    assert result.children                                              # the normal path ran


def test_09_a_changed_provider_triggers_collection(env):
    baseline(env, 2, 7)
    env.advance()
    env.identity = ("planet", "official_planet_sentinel_hub_https")
    result = env.cycle()
    assert_full(result, gate.REASON_CONTRACT_CHANGED)
    assert {"provider", "catalog_endpoint_class"} <= set(result.gate["detail"]["contract_changes"])


def test_10_changed_processing_code_or_constants_trigger_collection(env, tmp_path):
    copy = tmp_path / "backend_copy"
    for relative in gate.PROCESSING_MODULES:
        (copy / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BACKEND / relative, copy / relative)
    env.backend_root = copy
    baseline(env, 2, 7)
    env.advance()
    target = copy / "services" / "observation_quality.py"
    target.write_text(target.read_text(encoding="utf-8").replace("MIN_FIELD_VALID_SAMPLES = 10",
                                                                 "MIN_FIELD_VALID_SAMPLES = 11"), encoding="utf-8")
    result = env.cycle()
    assert_full(result, gate.REASON_CONTRACT_CHANGED)
    assert result.gate["detail"]["contract_changes"] == ["processing_fingerprint"]


def test_10b_line_endings_alone_do_not_change_the_processing_fingerprint(tmp_path):
    for relative in gate.PROCESSING_MODULES:
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_bytes((BACKEND / relative).read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert gate.processing_fingerprint(tmp_path, indices=["ndvi"], lookback_days=14) == \
        gate.processing_fingerprint(BACKEND, indices=["ndvi"], lookback_days=14)


def test_11_a_changed_set_of_indices_or_lookback_triggers_collection(env):
    baseline(env, 2, 7)
    env.advance()
    result = env.cycle(indices="ndvi,savi")
    assert result.gate["outcome"] == gate.OUTCOME_COLLECT
    assert "indices" in result.gate["detail"]["contract_changes"]
    env.advance()
    result = env.cycle("--lookback-days", "12")
    assert "lookback_days" in result.gate["detail"]["contract_changes"]


# ═══ 12-15: incomplete previous work ═════════════════════════════════════════

@pytest.mark.parametrize("label,ndvi_code,counters,expected", [
    ("partial", 1, None, "PARTIAL"),
    ("degraded", 1, {"success_count": 1, "failure_count": 1}, "DEGRADED"),
    ("failed", 4, {"success_count": 0}, "FAILED"),
])
def test_12_13_14_an_incomplete_previous_cycle_is_never_skipped(env, label, ndvi_code, counters, expected):
    env.catalog.publish(feature(START - timedelta(days=2)), START - timedelta(days=1))
    env.child_codes = {"ndvi": ndvi_code}
    if counters:
        env.child_counters = {"ndvi": counters}
    if label == "degraded":
        # A throttle that outlasted the retry budget stops the stage (TASK_251).
        original = env.child

        def throttled(command, timeout):
            outcome = original(command, timeout)
            if "run_ndvi" in command[1]:
                summary_path = next(Path(command[command.index("--output-dir") + 1]).glob("cycle_*/cycle_summary.json"))
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
                payload["provider_failure_category"] = "rate_limited"
                summary_path.write_text(json.dumps(payload), encoding="utf-8")
            return outcome
        env.child = throttled
    first = env.cycle()
    assert first.summary["run_status"] == expected, first.summary
    document, _ = env.checkpoint()
    assert document["status"] == gate.STATE_INVALID and document["verified"] is None
    env.child_codes, env.child_counters = {}, {}
    env.child = Env.child.__get__(env)
    env.advance()
    second = env.cycle()
    assert_full(second, gate.REASON_CHECKPOINT_INVALID)


def test_15_pending_retry_work_is_never_skipped(env):
    baseline(env, 2, 7)
    (env.root / "state" / "ndvi_state.json").write_text(json.dumps({
        "schema_version": 1, "next_offset": 0, "retry_field_ids": [13], "last_completed_run_id": None,
        "updated_at": None}), encoding="utf-8")
    env.advance()
    result = env.cycle()
    assert_full(result, gate.REASON_RETRY_PENDING)
    assert result.gate["detail"]["retry_pending"] == ["ndvi_state.json:1"]


def test_15b_an_unreadable_retry_queue_counts_as_pending(env):
    baseline(env, 2, 7)
    (env.root / "state" / "multi_state.json").write_text("{not json", encoding="utf-8")
    env.advance()
    assert_full(env.cycle(), gate.REASON_RETRY_PENDING)


def test_15c_a_cycle_run_without_the_gate_breaks_the_chain(env):
    baseline(env, 2, 7)
    env.advance()
    disabled = env.cycle(gate_on=False)
    assert disabled.gate is None and len(disabled.children) == 6
    env.advance()
    assert_full(env.cycle(), gate.REASON_CHAIN_BROKEN)


# ═══ 19-21: checkpoint integrity ═════════════════════════════════════════════

def test_19_a_missing_checkpoint_collects(env):
    baseline(env, 2, 7)
    (env.root / "state" / gate.CHECKPOINT_FILENAME).unlink()
    env.advance()
    assert_full(env.cycle(), gate.REASON_NO_CHECKPOINT)


@pytest.mark.parametrize("corrupt", ["truncated", "garbage", "digest", "extra_key", "bad_entry"])
def test_20_a_corrupt_checkpoint_is_never_trusted(env, corrupt):
    baseline(env, 2, 7)
    path = env.root / "state" / gate.CHECKPOINT_FILENAME
    text = path.read_text(encoding="utf-8")
    document = json.loads(text)
    if corrupt == "truncated":
        path.write_text(text[: len(text) // 2], encoding="utf-8")
    elif corrupt == "garbage":
        path.write_bytes(b"\x00\xff" * 40)
    elif corrupt == "digest":
        document["verified"]["inventory"][0]["day"] = "2026-10-09"
        path.write_text(json.dumps(document), encoding="utf-8")
    elif corrupt == "extra_key":
        document["trusted"] = True
        path.write_text(json.dumps(document), encoding="utf-8")
    else:
        document["verified"]["inventory"].append({"id": "x", "day": "2026-10-01"})
        path.write_text(json.dumps(document), encoding="utf-8")
    env.advance()
    result = env.cycle()
    assert_full(result, gate.REASON_CHECKPOINT_UNREADABLE)
    assert result.gate["detail"]["checkpoint_problem"].startswith("invalid")


def test_21_an_incompatible_checkpoint_version_is_never_trusted(env):
    baseline(env, 2, 7)
    path = env.root / "state" / gate.CHECKPOINT_FILENAME
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = 2
    path.write_text(json.dumps(document), encoding="utf-8")
    env.advance()
    assert_full(env.cycle(), gate.REASON_CHECKPOINT_UNREADABLE)


def test_21b_an_oversized_checkpoint_is_refused_without_reading_it_whole(tmp_path):
    (tmp_path / gate.CHECKPOINT_FILENAME).write_bytes(b" " * (gate.MAX_CHECKPOINT_BYTES + 10))
    assert gate.load_checkpoint(tmp_path) == (None, "oversized")


# ═══ 22-27: uncertain discovery ══════════════════════════════════════════════

def _real_catalog_env(env: Env, responses: list, clock=None):
    """The real SentinelHubService.get_catalog_response with the real governor; only
    httpx.post is replaced. Token requests are answered 200 unless queued otherwise."""
    from services.satellite import SentinelHubService

    clock = clock or _Clock()
    calls = {"oauth": 0, "catalog": 0}
    queue = list(responses)

    def fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        if "openid-connect" in url:
            calls["oauth"] += 1
            if queue and isinstance(queue[0], tuple) and queue[0][0] == "oauth":
                return queue.pop(0)[1]
            return httpx.Response(200, request=request, json={"access_token": "fixture", "expires_in": 3600})
        calls["catalog"] += 1
        assert url == "https://sh.dataspace.copernicus.eu/catalog/v1/search"
        assert kwargs["headers"]["Accept"] == "application/geo+json"
        if not queue:
            return env.catalog.search(kwargs["json"])
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer if isinstance(answer, httpx.Response) else env.catalog.search(kwargs["json"])

    def factory(plan, args, run_id, cycle_deadline):
        def search_factory():
            service = SentinelHubService("fixture-id", "fixture-secret", provider="cdse")
            service.provider_governor = pg.ProviderGovernor(pg.ProviderPolicy(), plan["state_dir"], run_id=run_id,
                                                            stage="catalog", clock=clock, sleep=clock.sleep,
                                                            deadline=clock() + gate.DISCOVERY_BUDGET_SECONDS)
            return service.get_catalog_response, service.provider_governor
        return collector.build_acquisition_gate(plan, args, run_id, cycle_deadline, scope_resolver=lambda: env.scope,
                                                search_factory=search_factory, identity=lambda: env.identity)
    patcher = patch("services.satellite.httpx.post", side_effect=fake_post)
    return factory, patcher, calls


class _Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _status(code, **kwargs):
    return httpx.Response(code, request=httpx.Request("POST", "https://sh.dataspace.copernicus.eu/catalog/v1/search"),
                          **kwargs)


def test_22_a_catalog_429_is_retried_by_the_governor_and_recovers(env):
    baseline(env, 2, 7)
    env.advance()
    factory, patcher, calls = _real_catalog_env(env, [_status(429, headers={"Retry-After": "2500"}), "ok"])
    with patcher:
        result = env.cycle(gate_factory=factory)
    assert_skip(result)
    assert result.gate["requests"] == {"oauth": 1, "catalog": 2}
    ledger = result.summary["provider_resilience"]["ledger"]
    assert ledger["stages"]["catalog"]["http_429"] == {"catalog": 1}
    assert ledger["stages"]["catalog"]["retries"] == 1
    assert ledger["stages"]["catalog"]["retry_after_seconds"] == [2.5]   # Sentinel Hub milliseconds


def test_22b_a_persistent_catalog_429_falls_back_to_the_normal_collection(env, tmp_path):
    baseline(env, 2, 7)
    env.advance()
    factory, patcher, calls = _real_catalog_env(env, [_status(429, headers={"Retry-After": "1000"})] * 6)
    with patcher:
        result = env.cycle(gate_factory=factory)
    assert_full(result, gate.REASON_DISCOVERY_FAILED)
    assert result.gate["discovery"]["category"] == "rate_limited"
    assert result.summary["run_status"] == "SUCCESS"
    document, _ = env.checkpoint()
    assert document["status"] == gate.STATE_INVALID and document["reason"] == gate.REASON_DISCOVERY_FAILED
    # TASK_251: the shared cooldown the gate left is what the Statistical children inherit.
    state = json.loads((env.root / "state" / pg.STATE_FILENAME).read_text(encoding="utf-8"))
    assert state["budgets"]["processing"]["cooldown_until"] > 0


@pytest.mark.parametrize("status", [401, 403])
def test_23_a_catalog_authorisation_refusal_with_a_valid_token_falls_back(env, status):
    baseline(env, 2, 7)
    env.advance()
    factory, patcher, _ = _real_catalog_env(env, [_status(status, json={"code": status, "description": "Insufficient permissions."})])
    with patcher:
        result = env.cycle(gate_factory=factory)
    assert_full(result, gate.REASON_DISCOVERY_FAILED)
    assert result.gate["discovery"]["category"] == "authentication"


def test_23b_a_refused_token_is_a_provider_wide_authentication_stop(env):
    baseline(env, 2, 7)
    env.advance()
    refused = httpx.Response(401, request=httpx.Request("POST", "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"),
                             json={"error": "invalid_client"})
    factory, patcher, calls = _real_catalog_env(env, [("oauth", refused)])
    with patcher:
        result = env.cycle(gate_factory=factory)
    assert result.gate["outcome"] == gate.OUTCOME_STOP and result.gate["stop_category"] == "authentication"
    assert result.children == [] and calls["catalog"] == 0
    assert result.code == 1 and result.summary["run_status"] == "DEGRADED"
    assert result.summary["stage_stops"] == {"catalog": "authentication"}
    assert result.latest["status"] == "failed" and result.latest["failure_category"] == "auth"
    assert result.latest["acquisition_gate"]["statistical_collection"] == "not_started"
    document, _ = env.checkpoint()
    assert document["status"] == gate.STATE_INVALID and document["reason"] == "provider_stop:authentication"


def test_23c_a_suspected_quota_is_a_provider_wide_quota_stop(env):
    baseline(env, 2, 7)
    env.advance()
    exhausted = _status(429, headers={"Retry-After": "3600000"},
                        json={"status": 429, "reason": "Too Many Requests", "message": "Monthly quota exhausted"})
    factory, patcher, _ = _real_catalog_env(env, [exhausted])
    with patcher:
        result = env.cycle(gate_factory=factory)
    assert result.gate["outcome"] == gate.OUTCOME_STOP and result.gate["stop_category"] == "quota"
    assert result.children == [] and result.latest["failure_category"] == "quota"
    assert result.summary["run_status"] == "DEGRADED"


@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.ConnectError("down")])
def test_24_a_catalog_timeout_or_network_error_falls_back(env, error):
    baseline(env, 2, 7)
    env.advance()
    factory, patcher, _ = _real_catalog_env(env, [error])
    with patcher:
        result = env.cycle(gate_factory=factory)
    assert_full(result, gate.REASON_DISCOVERY_FAILED)
    assert result.gate["discovery"]["category"] in ("timeout", "network")


def test_24b_a_discovery_that_cannot_fit_its_deadline_reports_instead_of_sleeping(env):
    baseline(env, 2, 7)
    env.advance()
    clock = _Clock()
    factory, patcher, _ = _real_catalog_env(env, [_status(429, headers={"Retry-After": "59000"})] * 6, clock=clock)
    with patcher:
        result = env.cycle(gate_factory=factory)
    assert_full(result, gate.REASON_DISCOVERY_FAILED)
    assert clock.now - 1_800_000_000.0 <= gate.DISCOVERY_BUDGET_SECONDS          # never waited past it


@pytest.mark.parametrize("mutate", [
    lambda doc: {**doc, "features": "x"},
    lambda doc: {**doc, "features": [{**doc["features"][0], "id": "bad id with spaces"}]},
    lambda doc: {**doc, "features": [{**doc["features"][0], "properties": {**doc["features"][0]["properties"], "datetime": "2026-10-08T06:22:15"}}]},
    lambda doc: {**doc, "features": [{**doc["features"][0], "properties": {**doc["features"][0]["properties"], "datetime": "2025-01-01T00:00:00Z"}}]},
    lambda doc: {**doc, "features": [{**doc["features"][0], "geometry": None}]},
    lambda doc: {**doc, "features": [{**doc["features"][0], "collection": "sentinel-1-grd"}]},
    lambda doc: {**doc, "context": {**doc["context"], "returned": 7}},
    lambda doc: [doc],
])
def test_25_a_malformed_catalog_response_falls_back(env, mutate):
    baseline(env, 2, 7)
    env.advance()
    env.catalog.page_hook = mutate
    result = env.cycle()
    assert_full(result, gate.REASON_DISCOVERY_FAILED)
    assert result.gate["discovery"]["reason"].startswith("malformed")


def test_25b_a_non_json_or_wrong_type_page_falls_back(env):
    baseline(env, 2, 7)
    env.advance()
    env.catalog.fail = lambda body: httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
    assert_full(env.cycle(), gate.REASON_DISCOVERY_FAILED)


def test_26_incomplete_pagination_is_never_an_empty_result(env):
    for offset in range(1, 14):
        env.catalog.publish(feature(START - timedelta(days=offset), tile=f"T41S{offset:02d}"), START - timedelta(days=offset))
    first = env.cycle()
    assert first.gate["discovery"]["complete"] and first.gate["discovery"]["pages"] == 1
    env.advance()
    calls = []

    def repeat_token(body):
        calls.append(body)
        document = {"type": "FeatureCollection", "features": [], "context": {"next": 5, "limit": 100, "returned": 0}}
        return httpx.Response(200, json=document)
    env.catalog.fail = repeat_token
    assert_full(env.cycle(), gate.REASON_DISCOVERY_FAILED)            # a "next" with no progress
    env.advance()
    env.catalog.fail = None
    env.catalog.page_hook = lambda doc: {**doc, "links": [{"rel": "next", "href": "x", "method": "POST"}]}
    assert_full(env.cycle(), gate.REASON_DISCOVERY_FAILED)            # a next link without its token


def test_26b_multi_page_results_are_read_to_the_end(env, monkeypatch):
    monkeypatch.setattr(gate, "CATALOG_PAGE_LIMIT", 3)
    for offset in range(1, 9):
        env.catalog.publish(feature(START - timedelta(days=offset), tile=f"T41S{offset:02d}"), START - timedelta(days=offset))
    discovery = gate.discover(env.catalog.search, env.scope, START - timedelta(days=14), START, page_limit=3)
    assert discovery.complete and discovery.pages == 3 and len(discovery.items) == 8
    assert [call.get("next") for call in env.catalog.calls] == [None, 3, 6]


def test_26c_a_page_budget_that_runs_out_is_incomplete(env):
    for offset in range(1, 9):
        env.catalog.publish(feature(START - timedelta(days=offset), tile=f"T41S{offset:02d}"), START - timedelta(days=offset))
    discovery = gate.discover(env.catalog.search, env.scope, START - timedelta(days=14), START, page_limit=2, max_pages=3)
    assert not discovery.complete and discovery.reason == "page_budget_exhausted"


def test_26d_continuation_sources_that_disagree_are_ambiguous(env):
    for offset in range(1, 5):
        env.catalog.publish(feature(START - timedelta(days=offset), tile=f"T41S{offset:02d}"), START - timedelta(days=offset))
    env.catalog.page_hook = lambda doc: {**doc, "links": [{"rel": "next", "body": {"next": 99}, "method": "POST"}]} \
        if "next" in doc["context"] else doc
    discovery = gate.discover(env.catalog.search, env.scope, START - timedelta(days=14), START, page_limit=2)
    assert not discovery.complete and "disagree" in discovery.reason


def test_27_the_search_area_covers_every_field_with_a_margin(env):
    env.catalog.publish(feature(START - timedelta(days=2)), START - timedelta(days=1))
    result = env.cycle()
    body = result.catalog_calls[0]
    west, south, east, north = body["bbox"]
    assert (west, south, east, north) == pytest.approx((AOI[0] - 0.01, AOI[1] - 0.01, AOI[2] + 0.01, AOI[3] + 0.01))
    assert west < AOI[0] and south < AOI[1] and east > AOI[2] and north > AOI[3]
    assert body["collections"] == ["sentinel-2-l2a"] and body["limit"] == 100
    # The closed interval reaches midnight after date_to: every acquisition the
    # Statistical requests' [date_from, date_to + 1 day) can hold.
    assert body["datetime"] == "2026-09-26T00:00:00Z/2026-10-11T00:00:00Z"


def test_27b_footprint_policy_partial_coverage_after_a_departure_collects(env):
    env.catalog.publish(feature(START - timedelta(days=14)), START - timedelta(days=13))        # full footprint
    env.catalog.publish(feature(START - timedelta(days=3), geometry=WEST_SWATH), START - timedelta(days=2))
    first = env.cycle("--acquisition-gate-policy", "footprint")
    assert first.summary["run_status"] == "SUCCESS"
    env.advance()                       # the full-footprint day leaves the window; only a partial swath remains
    result = env.cycle("--acquisition-gate-policy", "footprint")
    assert_full(result, gate.REASON_WINDOW_COMPOSITION)
    assert result.gate["detail"]["covering_days"] == []


def test_27c_footprint_policy_skips_a_departure_when_a_covering_day_remains(env):
    env.catalog.publish(feature(START - timedelta(days=14)), START - timedelta(days=13))
    env.catalog.publish(feature(START - timedelta(days=3)), START - timedelta(days=2))
    env.cycle("--acquisition-gate-policy", "footprint")
    env.advance()
    result = env.cycle("--acquisition-gate-policy", "footprint")
    assert_skip(result)
    assert result.gate["detail"]["covering_days"] == ["2026-10-07"]


def test_27d_footprint_policy_ignores_items_above_the_cloud_ceiling(env):
    env.catalog.publish(feature(START - timedelta(days=3)), START - timedelta(days=2))
    env.cycle("--acquisition-gate-policy", "footprint")
    env.advance()
    env.catalog.publish(feature(START, cloud=95.0))
    assert_skip(env.cycle("--acquisition-gate-policy", "footprint"))
    env.advance()
    env.catalog.publish(feature(START + timedelta(days=1), cloud=None, tile="T41SQB"))   # unknown cloud counts
    assert_full(env.cycle("--acquisition-gate-policy", "footprint"), gate.REASON_NEW_ACQUISITION)


def test_27e_strict_policy_collects_whenever_an_acquisition_leaves_the_window(env):
    env.catalog.publish(feature(START - timedelta(days=14)), START - timedelta(days=13))
    env.catalog.publish(feature(START - timedelta(days=3)), START - timedelta(days=2))
    env.cycle()
    env.advance()
    result = env.cycle()
    assert_full(result, gate.REASON_WINDOW_COMPOSITION)
    assert result.gate["detail"]["departed_items"] == 1


def test_27f_strict_policy_counts_cloudy_items(env):
    baseline(env, 2, 7)
    env.advance()
    env.catalog.publish(feature(START, cloud=99.0))
    assert_full(env.cycle(), gate.REASON_NEW_ACQUISITION)


def test_27g_switching_policy_invalidates_the_checkpoint(env):
    baseline(env, 2, 7)
    env.advance()
    result = env.cycle("--acquisition-gate-policy", "footprint")
    assert "departure_policy" in result.gate["detail"]["contract_changes"]


# ═══ 28: periodic reconciliation ═════════════════════════════════════════════

@pytest.mark.parametrize("reconcile_days", [2, 3, 7])
def test_28_periodic_reconciliation_forces_a_full_collection(env, reconcile_days):
    env.catalog.publish(feature(START - timedelta(days=1)), START)
    extra = ("--acquisition-gate-reconcile-days", str(reconcile_days))
    env.cycle(*extra)
    outcomes = []
    for _ in range(reconcile_days + 1):
        env.advance()
        outcomes.append(env.cycle(*extra).gate)
    assert [item["outcome"] for item in outcomes] == \
        [gate.OUTCOME_SKIP] * (reconcile_days - 1) + [gate.OUTCOME_COLLECT, gate.OUTCOME_SKIP]
    assert outcomes[reconcile_days - 1]["reasons"] == [gate.REASON_RECONCILIATION_DUE]


def test_28a_a_one_day_interval_collects_every_day(env):
    env.catalog.publish(feature(START - timedelta(days=1)), START)
    env.cycle("--acquisition-gate-reconcile-days", "1")
    for _ in range(3):
        env.advance()
        assert_full(env.cycle("--acquisition-gate-reconcile-days", "1"), gate.REASON_RECONCILIATION_DUE)


@pytest.mark.parametrize("value,lookback", [("0", "14"), ("8", "14"), ("5", "9")])
def test_28b_the_reconciliation_interval_is_bounded_by_the_lookback(env, value, lookback):
    args = env.args("--acquisition-gate-reconcile-days", value, "--lookback-days", lookback)
    with pytest.raises(collector.ContractError):
        collector.validate(args)


def test_28c_a_clock_that_moves_back_collects(env):
    baseline(env, 2, 7)
    FakeDate.current = START - timedelta(days=1)
    env.catalog.today = FakeDate.current
    result = env.cycle()
    assert result.gate["outcome"] == gate.OUTCOME_COLLECT
    assert gate.REASON_CLOCK_REGRESSION in result.gate["reasons"]


# ═══ 29-32: crash safety, consecutive runs, restart ══════════════════════════

def test_29_a_failure_to_commit_the_checkpoint_leaves_it_untrusted(env, monkeypatch):
    baseline(env, 2, 7)
    env.advance()
    env.catalog.publish(feature(START))
    real = collector.atomic_json

    def failing(path, value, **kwargs):
        if Path(path).name == gate.CHECKPOINT_FILENAME and value.get("status") != gate.STATE_PENDING:
            raise OSError(28, "No space left on device")
        return real(path, value, **kwargs)
    monkeypatch.setattr(collector, "atomic_json", failing)
    result = env.cycle()
    assert result.summary["run_status"] == "SUCCESS" and result.code == 0     # the collection itself stands
    assert any("acquisition checkpoint persistence failed" in item for item in result.summary["diagnostics"])
    document, _ = env.checkpoint()
    assert document["status"] == gate.STATE_PENDING
    monkeypatch.setattr(collector, "atomic_json", real)
    env.advance()
    assert_full(env.cycle(), gate.REASON_CHECKPOINT_PENDING)


def test_30_a_crash_after_a_successful_collection_before_the_checkpoint_recovers(env):
    baseline(env, 2, 7)
    env.advance()
    env.catalog.publish(feature(START))
    # The cycle's process dies after the Statistical stages: evaluate() ran and marked
    # the checkpoint pending, finalize() and the status publication never happened.
    plan = collector.validate(env.args())
    crashed = env.gate_factory(plan, env.args(), "crashedrun", 2e9)
    assert crashed.evaluate(FIELD_IDS).outcome == gate.OUTCOME_COLLECT
    document, _ = env.checkpoint()
    assert document["status"] == gate.STATE_PENDING and document["chain_run_id"] == "crashedrun"
    env.advance()
    result = env.cycle()
    assert_full(result, gate.REASON_CHECKPOINT_PENDING)     # no trusted inventory to compare with
    assert result.summary["run_status"] == "SUCCESS"
    document, _ = env.checkpoint()
    assert "2026-10-10" in [entry["day"] for entry in document["verified"]["inventory"]]
    env.advance()
    assert_skip(env.cycle())                                                  # recovered


def test_30b_a_crash_during_a_skipped_cycle_costs_one_full_collection(env):
    baseline(env, 2, 7)
    env.advance()
    plan = collector.validate(env.args())
    crashed = env.gate_factory(plan, env.args(), "crashedskip", 2e9)
    assert crashed.evaluate(FIELD_IDS).outcome == gate.OUTCOME_SKIP
    env.advance()
    assert_full(env.cycle(), gate.REASON_CHECKPOINT_PENDING)


def test_30c_a_failed_status_publication_breaks_the_chain(env, monkeypatch):
    baseline(env, 2, 7)
    env.advance()
    real = collector.atomic_json

    def failing(path, value, **kwargs):
        if Path(path).name in (collector.LATEST_STATUS_FILENAME, collector.LAST_SUCCESS_FILENAME) and "finished_at" in value \
                and value.get("finished_at"):
            raise OSError(28, "No space left on device")
        return real(path, value, **kwargs)
    monkeypatch.setattr(collector, "atomic_json", failing)
    with patch("services.autonomous_monitoring.begin_apply_run", return_value=None):
        code, summary = collector.run(env.args(), child_runner=env.child, lock_acquire=lambda *a, **k: "lock",
                                      lock_release=lambda lock: None, run_id_factory=lambda: "lostrun",
                                      gate_factory=env.gate_factory)
    assert code == 4 and summary["acquisition_gate"]["outcome"] == gate.OUTCOME_SKIP
    document, _ = env.checkpoint()
    assert document["status"] == gate.STATE_VERIFIED and document["chain_run_id"] == "lostrun"
    monkeypatch.setattr(collector, "atomic_json", real)
    env.advance()
    assert_full(env.cycle(), gate.REASON_CHAIN_BROKEN)


def test_31_two_consecutive_cycles(env):
    first = baseline(env, 2, 7)
    env.advance()
    second = env.cycle()
    assert_skip(second)
    assert first.summary["run_id"] != second.summary["run_id"]
    assert len(first.catalog_calls) == len(second.catalog_calls) == 1


def test_32_the_checkpoint_survives_a_process_restart(env, tmp_path):
    baseline(env, 2, 7)
    # A fresh interpreter reads and validates the persisted checkpoint on its own.
    script = (
        "import sys, json; sys.path.insert(0, sys.argv[1]);"
        "from services import acquisition_gate as g;"
        "doc, problem = g.load_checkpoint(sys.argv[2]);"
        "print(json.dumps({'status': doc and doc['status'], 'problem': problem,"
        " 'days': doc and [e['day'] for e in doc['verified']['inventory']]}))"
    )
    output = subprocess.run([sys.executable, "-c", script, str(BACKEND), str(env.root / "state")],
                            capture_output=True, text=True, timeout=120, check=True)
    assert json.loads(output.stdout) == {"status": "verified", "problem": None, "days": ["2026-10-03", "2026-10-08"]}
    env.advance()
    assert_skip(env.cycle())


# ═══ 35-37: no fabricated evidence ═══════════════════════════════════════════

def test_35_36_a_skip_never_fabricates_observations_or_success(env):
    baseline(env, 2, 7)
    env.advance()
    result = env.cycle()
    assert_skip(result)
    assert result.summary["run_status"] != "SUCCESS"
    assert result.summary["children"] == []
    assert collector.aggregate_counters(result.summary) == {field: 0 for field in collector.PROVIDER_COUNTER_FIELDS}
    assert collector.provider_status({**result.summary, "exit_code": 0}) == "not_called"
    assert collector.gate_counters(result.summary)["acquisition_gate"]["statistical_collection"] == "skipped"
    # The listed acquisition days stay metadata: no stage, batch or field outcome mentions them.
    assert "stages" in result.summary and result.summary["stages"] == {}


def test_36b_a_skip_with_a_failed_finish_is_never_no_new_acquisitions(env):
    assert collector.run_status(4, {}, {}, statistical_skipped=True) == "FAILED"
    assert collector.run_status(1, {}, {}, statistical_skipped=True) == "PARTIAL"
    assert collector.run_status(0, {}, {}, statistical_skipped=True) == "NO_NEW_ACQUISITIONS"
    assert collector.run_status(0, {}, {}) == "SUCCESS"


# ═══ 38-40: compatibility with TASK_251/251A and the Windows CLI ═════════════

def test_38_39_children_after_the_gate_keep_the_shared_policy_and_cycle_deadline(env):
    baseline(env, 2, 7)
    env.advance()
    env.catalog.publish(feature(START))
    seen = []
    original = env.child

    def capture(command, timeout):
        seen.append({key: os.environ.get(key) for key in (pg.POLICY_ENV, pg.PACER_DIR_ENV, pg.RUN_ID_ENV,
                                                          pg.STAGE_ENV, pg.CYCLE_DEADLINE_ENV)})
        return original(command, timeout)
    env.child = capture
    result = env.cycle()
    assert result.summary["run_status"] == "SUCCESS"
    assert {item[pg.STAGE_ENV] for item in seen} == {"ndvi", "multi"}
    assert all(item[pg.POLICY_ENV] == pg.ProviderPolicy().to_json() and item[pg.CYCLE_DEADLINE_ENV] for item in seen)
    assert {item[pg.RUN_ID_ENV] for item in seen} == {result.summary["run_id"]}
    assert os.environ.get(pg.POLICY_ENV) is None                              # restored after the cycle


def test_40_the_production_scheduled_arguments_are_unchanged_and_the_gate_is_off(tmp_path):
    runner = next((BACKEND.parent / "ops").rglob("Invoke-Collector.ps1")).read_text(encoding="utf-8-sig")
    assert "acquisition-gate" not in runner
    production = ["--apply", "--all-active-fields", "--indices", "ndvi,savi,evi,ndmi,ndre", "--batch-size", "25",
                  "--lookback-days", "14", "--max-attempts", "2", "--retry-base-seconds", "2",
                  "--field-timeout-seconds", "180", "--cycle-timeout-seconds", "21600",
                  "--output-dir", str(tmp_path / "o"), "--state-dir", str(tmp_path / "s"), "--lock-dir", str(tmp_path / "l")]
    plan = collector.validate(collector.parse_args(production))
    assert plan["acquisition_gate"] == {"enabled": False, "reconcile_days": 3, "policy": "strict"}
    gated = collector.validate(collector.parse_args([*production, "--acquisition-gate"]))
    assert gated["acquisition_gate"]["enabled"] is True


def test_40b_the_cli_documents_the_gate_and_refuses_ad_hoc_scopes(tmp_path):
    output = subprocess.run([sys.executable, str(BACKEND / "scripts" / "collect_satellite.py"), "--help"],
                            capture_output=True, text=True, timeout=120, check=True,
                            env={**os.environ, "PYTHONUTF8": "1"})
    assert "--acquisition-gate" in output.stdout and "--acquisition-gate-reconcile-days" in output.stdout
    for scope in (["--field-ids", "4"], ["--all-active-fields", "--batch-size", "2", "--date-to", "2026-10-01"]):
        args = collector.parse_args(["--apply", *scope, "--indices", "ndvi", "--output-dir", str(tmp_path / "o"),
                                     "--state-dir", str(tmp_path / "s"), "--lock-dir", str(tmp_path / "l"),
                                     "--acquisition-gate"])
        with pytest.raises(collector.ContractError):
            collector.validate(args)


@pytest.mark.parametrize("mode", ["--dry-run", "--diagnostic"])
def test_40c_dry_run_and_diagnostic_never_evaluate_the_gate(env, mode):
    flags = [mode, "--all-active-fields", "--batch-size", "2", "--indices", "ndvi", "--output-dir",
             str(env.root / "output"), "--state-dir", str(env.root / "state"), "--lock-dir", str(env.root / "locks"),
             "--acquisition-gate"]
    code, summary = collector.run(collector.parse_args(flags), child_runner=env.child,
                                  lock_acquire=lambda *a, **k: "lock", lock_release=lambda lock: None,
                                  gate_factory=lambda *a: pytest.fail("gate built outside apply mode"))
    assert summary["acquisition_gate"]["evaluated"] is False
    assert not (env.root / "state" / gate.CHECKPOINT_FILENAME).exists()


# ═══ 41-43: request accounting ═══════════════════════════════════════════════

def test_41_42_43_statistical_catalog_and_oauth_accounting(env):
    baseline(env, 2, 7)
    env.advance()
    factory, patcher, calls = _real_catalog_env(env, [])
    with patcher:
        skipped = env.cycle(gate_factory=factory)
    assert_skip(skipped)
    assert calls == {"oauth": 1, "catalog": 1}
    assert skipped.gate["requests"] == {"oauth": 1, "catalog": 1}
    ledger = skipped.summary["provider_resilience"]["ledger"]
    assert set(ledger["stages"]) == {"catalog"}                                # no Statistical stage at all
    assert ledger["stages"]["catalog"]["requests"] == {"oauth": 1, "catalog": 1}
    assert ledger["request_rates"]["processing"]["requests"] == 1              # the Catalog page only
    assert collector.gate_counters(skipped.summary)["acquisition_gate"] == {
        "outcome": "NO_NEW_ACQUISITIONS", "statistical_collection": "skipped", "reasons": [],
        "catalog_requests": 1, "oauth_requests": 1}
    env.advance()
    env.catalog.publish(feature(FakeDate.current))
    factory, patcher, calls = _real_catalog_env(env, [])
    with patcher:
        full = env.cycle(gate_factory=factory)
    assert full.gate["outcome"] == gate.OUTCOME_COLLECT
    assert calls == {"oauth": 1, "catalog": 1} and len(full.children) == 6


# ═══ 44: disabled means unchanged ════════════════════════════════════════════

def test_44_a_disabled_gate_changes_nothing(env):
    env.catalog.publish(feature(START - timedelta(days=2)), START - timedelta(days=1))
    result = env.cycle(gate_on=False, gate_factory=lambda *a: pytest.fail("gate built while disabled"))
    assert env.catalog.calls == [] and result.gate is None
    assert "acquisition_gate" not in result.summary and "acquisition_gate" not in result.latest
    assert set(result.latest) == {"schema_version", "run_id", "mode", "status", "started_at", "finished_at",
                                  "duration_seconds", "exit_code", "failure_category", "providers"}
    assert result.summary["run_status"] == "SUCCESS" and len(result.children) == 6
    assert not (env.root / "state" / gate.CHECKPOINT_FILENAME).exists()
    for command in result.children:
        assert not any("acquisition" in part for part in command)
    assert collector.gate_counters(result.summary) == {}
    assert collector.provider_status({**result.summary, "exit_code": 0}) == "no_scene"


# ═══ Unit contracts ══════════════════════════════════════════════════════════

def test_scope_requires_every_active_geometry():
    with pytest.raises(gate.GateError):
        gate.scope_from_rows([(1, md5("a")), (2, None)], AOI, HULL)
    with pytest.raises(gate.GateError):
        gate.scope_from_rows([(1, md5("a")), (1, md5("b"))], AOI, HULL)
    with pytest.raises(gate.GateError):
        gate.scope_from_rows([(1, md5("a"))], (64.5, 39.9, 64.4, 40.0), HULL)


def test_a_scope_resolver_that_disagrees_with_the_cycle_collects(env):
    baseline(env, 2, 7)
    env.advance()
    plan = collector.validate(env.args())
    run = env.gate_factory(plan, env.args(), "mismatch", 2e9)
    decision = run.evaluate(FIELD_IDS[:-1])
    assert decision.outcome == gate.OUTCOME_COLLECT and gate.REASON_SCOPE_UNAVAILABLE in decision.reasons


def test_a_gate_defect_never_skips_or_stops(env, monkeypatch):
    baseline(env, 2, 7)
    env.advance()
    monkeypatch.setattr(gate, "decide", lambda **kwargs: 1 / 0)
    result = env.cycle()
    assert result.gate["outcome"] == gate.OUTCOME_COLLECT and result.gate["reasons"] == ["gate_error"]
    assert result.summary["run_status"] == "SUCCESS"


def test_the_checkpoint_holds_no_credentials_or_paths(env):
    baseline(env, 2, 7)
    text = (env.root / "state" / gate.CHECKPOINT_FILENAME).read_text(encoding="utf-8")
    for marker in ("fixture-secret", "Bearer", "access_token", "client_secret", str(env.root), "\\Users\\"):
        assert marker not in text
    assert len(text) < 64 * 1024


def test_the_relevance_ceiling_matches_both_statistical_requests():
    from services import satellite, satellite_collection
    import inspect

    assert gate.RELEVANT_MAX_CLOUD_PCT == satellite.SENTINEL_MAX_CLOUD_COVERAGE
    assert inspect.signature(satellite_collection.build_statistical_payload).parameters["max_cloud_coverage"].default \
        == gate.RELEVANT_MAX_CLOUD_PCT
    assert gate.COLLECTION_ID == satellite.SENTINEL_DATASET


def test_the_web_catalog_path_keeps_its_own_timeout_without_a_governor():
    from services.satellite import SentinelHubService

    service = SentinelHubService("fixture-id", "fixture-secret", provider="cdse")
    seen = {}

    def fake_post(url, **kwargs):
        if "openid-connect" in url:
            return httpx.Response(200, request=httpx.Request("POST", url), json={"access_token": "t", "expires_in": 3600})
        seen.update(kwargs)
        return httpx.Response(200, request=httpx.Request("POST", url), json={"features": []})
    with patch("services.satellite.httpx.post", side_effect=fake_post):
        service.get_catalog_response({"collections": ["sentinel-2-l2a"]})
    assert seen["timeout"] == httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
    assert seen["follow_redirects"] is False


def test_a_gate_that_cannot_be_built_never_blocks_the_collection(env):
    env.catalog.publish(feature(START - timedelta(days=2)), START - timedelta(days=1))

    def broken(*args):
        raise RuntimeError("cannot build")
    result = env.cycle(gate_factory=broken)
    assert result.gate == {"enabled": True, "evaluated": False, "reason": "gate_unavailable:RuntimeError"}
    assert result.summary["run_status"] == "SUCCESS" and len(result.children) == 6
    assert collector.gate_counters(result.summary) == {}
