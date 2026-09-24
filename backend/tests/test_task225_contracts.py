"""TASK_225 static and unit contracts (no database).

The PostgreSQL suites prove behaviour; these pin the structural decisions so
they cannot drift back: one canonical inspection INSERT, one status projection,
no web-process collection, no blocking synchronous work in the touched async
surface, and the collector's detection step and terminalisation order.
"""

from __future__ import annotations

from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
import re
from types import SimpleNamespace
import tempfile
from unittest.mock import Mock, patch

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def production_sources():
    for folder in ("api", "services", "scripts"):
        for path in sorted((BACKEND / folder).glob("*.py")):
            yield path.relative_to(BACKEND).as_posix(), path.read_text(encoding="utf-8")


# ── one canonical inspection INSERT ────────────────────────────────────────

def test_only_the_canonical_service_inserts_inspections():
    writers = []
    for name, source in production_sources():
        if re.search(r"INSERT\s+INTO\s+field_inspections", source) or "'field_inspections', {" in source:
            writers.append(name)
    assert writers == ["services/anomaly_inspections.py"]
    source = (BACKEND / "services/anomaly_inspections.py").read_text(encoding="utf-8")
    assert source.count("INSERT INTO field_inspections") == 1


def test_canonical_insert_writes_the_0013_contract_columns():
    from services import anomaly_inspections

    body = inspect.getsource(anomaly_inspections.insert_inspection)
    columns = body[body.index("INSERT INTO field_inspections"):body.index("VALUES")]
    for column in ("source_kind", "source_reason", "priority", "status", "source_provider",
                   "source_item_id", "source_acquired_at", "source_index_name",
                   "source_sampled_value", "source_geometry_hash", "source_point", "source_zone",
                   "source_alert_id", "due_at", "follow_up_of_id", "source_snapshot_locked"):
        assert column in columns
    assert "_audit(db, actor, result, \"inspection_created\"" in body


@pytest.mark.parametrize("changes, message", [
    ({"kind": "legacy"}, "cannot be created"),
    ({"reason": "   "}, "5..2000"),
    ({"sampled_value": None}, "complete scene"),
    ({"zone_ewkb": None}, "complete scene"),
    ({"sampled_value": 1.5}, "sampled_value"),
    ({"delta": float("nan")}, "delta"),
    ({"acquired_at": datetime(2026, 9, 1)}, "timezone-aware"),
    ({"alert_id": 4}, "forbids an alert"),
    ({"point": (64.4, 39.7)}, "mutually exclusive"),
])
def test_source_snapshot_is_validated_before_any_sql(changes, message):
    from services.anomaly_inspections import InspectionSource, InspectionSourceError

    values = dict(
        kind="pixel_ndvi", reason="Field-wide robust NDVI drop", provider="sentinel2_field_statistics",
        item_id="ndvi_record:7", acquired_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        index_name="ndvi", sampled_value=0.28, comparison_value=0.64, delta=-0.36,
        geometry_hash="a" * 64, zone_ewkb="0106000020e6100000",
    )
    values.update(changes)
    with pytest.raises(InspectionSourceError, match=message):
        InspectionSource(**values).validate()


def test_only_uniqueness_races_are_reported_as_conflicts():
    from sqlalchemy.exc import IntegrityError

    from services.anomaly_inspections import is_unique_violation

    def error(code):
        return IntegrityError("INSERT", {}, SimpleNamespace(pgcode=code))

    assert is_unique_violation(error("23505"))
    for contract_violation in ("23502", "23514", "23503", None):
        assert not is_unique_violation(error(contract_violation))


# ── one remediation state machine ──────────────────────────────────────────

def test_retired_state_machines_have_no_write_functions():
    from services import anomaly_inspections, field_inspections, operational_closure

    for module, names in (
        (anomaly_inspections, ("create_action", "action_transition", "verify_action")),
        (field_inspections, ("create", "update", "transition")),
        (operational_closure, ("record_result", "attach_evidence", "create_action", "update_action",
                               "close_action", "reopen_action", "request_verification",
                               "resolve_verification")),
    ):
        for name in names:
            assert not hasattr(module, name), (module.__name__, name)
    for name, source in production_sources():
        if name.startswith(("services/", "api/")):
            assert not re.search(r"(INSERT\s+INTO|UPDATE)\s+corrective_actions", source), name
            assert not re.search(r"(INSERT\s+INTO|UPDATE)\s+action_verification_requests", source), name


def test_current_status_readers_do_not_read_the_retired_lifecycle():
    from services import operational_center

    assert "corrective_actions" not in operational_center.CASES_CTE
    from services import anomaly_inspections

    queue = inspect.getsource(anomaly_inspections.list_queue)
    assert not re.search(r"(FROM|JOIN)\s+corrective_actions", queue)
    assert "JOIN agronomy_plans" in queue


# ── canonical status projection ────────────────────────────────────────────

def test_projection_vocabulary_is_published_once():
    import typing

    from schemas.operational_center import OperationalStatus, RemediationStatus
    from services import remediation_status

    assert set(typing.get_args(RemediationStatus)) == set(remediation_status.REMEDIATION_STATES)
    assert set(remediation_status.OPERATIONAL_STATUS.values()) <= set(typing.get_args(OperationalStatus))
    assert set(remediation_status.OPERATIONAL_STATUS) == (
        set(remediation_status.REMEDIATION_STATES) - {remediation_status.DATA_UNAVAILABLE}
    )


@pytest.mark.parametrize("inspection, plan, verification, expected", [
    ("new", None, None, "needs_inspection"),
    ("pending", None, None, "needs_inspection"),
    ("in_progress", None, None, "inspection_active"),
    ("submitted", None, None, "awaiting_review"),
    ("confirmed", None, None, "awaiting_decision"),
    ("confirmed", "draft", "PENDING_DATA", "plan_active"),
    ("confirmed", "in_progress", "PENDING_DATA", "work_active"),
    ("confirmed", "pending_verification", "TOO_EARLY", "awaiting_satellite_verification"),
    ("confirmed", "pending_verification", "QUALITY_BLOCKED", "verification_blocked"),
    ("confirmed", "pending_verification", "INCONCLUSIVE", "verification_blocked"),
    ("confirmed", "pending_verification", "IMPROVED", "improved_awaiting_closure"),
    ("confirmed", "pending_verification", "WORSENED", "not_improved"),
    ("confirmed", "rework", "PENDING_DATA", "reopened"),
    ("confirmed", "closed", "IMPROVED", "improved_closed"),
    ("confirmed", "closed", "WORSENED", "closed_without_improvement"),
    ("confirmed", "cancelled", "PENDING_DATA", "awaiting_decision"),
    ("rejected", None, None, "rejected"),
    ("completed", None, None, "inspection_closed"),
])
def test_projection_decision_table(inspection, plan, verification, expected):
    from services.remediation_status import inspection_status

    assert inspection_status(inspection, plan, verification) == expected


# ── no web-process collection, no blocking sync work on the loop ───────────

def test_web_process_never_collects_satellite_data():
    import api.ndvi as ndvi

    source = inspect.getsource(ndvi)
    for forbidden in ("get_satellite_service", "INSERT INTO ndvi_records", "subprocess", "db.commit"):
        assert forbidden not in source
    main = (BACKEND / "main.py").read_text(encoding="utf-8")
    assert "APScheduler" not in main and "collect_satellite" not in main


def test_touched_handlers_and_auth_dependencies_run_in_the_threadpool():
    from api import anomaly_inspections, auth, closed_loop_agronomy, ndvi

    for function in (auth.get_current_user, auth.get_current_active_user,
                     ndvi.get_ndvi_history, ndvi.get_ndvi_latest, ndvi.refresh_ndvi,
                     anomaly_inspections.upload_photo, closed_loop_agronomy.upload_evidence):
        assert not inspect.iscoroutinefunction(function), function.__qualname__


def test_detection_never_calls_a_provider():
    from services import autonomous_monitoring

    source = "".join(inspect.getsource(function) for function in (
        autonomous_monitoring.detect_observation_candidates,
        autonomous_monitoring._detection_series,
        autonomous_monitoring._supporting_agreement,
        autonomous_monitoring._persistence,
    ))
    for forbidden in ("get_satellite_service", "httpx", "requests", "get_artifact", "subprocess"):
        assert forbidden not in source
    assert "LIMIT :per_field" in source and ":lookback_from" in source


# ── collector: detection order and terminalisation ─────────────────────────

def apply_invocation(root: Path):
    from scripts import collect_satellite as collector

    return collector.parse_args([
        "--apply", "--field-ids", "4", "--indices", "ndvi",
        "--output-dir", str(root / "output"), "--state-dir", str(root / "state"),
        "--lock-dir", str(root / "locks"),
    ])


def healthy_child(command, timeout):
    provider_output = Path(command[command.index("--output-dir") + 1])
    (provider_output / "cycle_1").mkdir()
    (provider_output / "cycle_1" / "cycle_summary.json").write_text(json.dumps({
        "success_count": 1, "failure_count": 0, "inserted_count": 1, "skipped_existing_count": 0,
        "quality_blocked_count": 0, "timeout_count": 0}), encoding="utf-8")
    return {"exit_code": 0, "timed_out": False, "stdout": "ok", "stderr": ""}


def run_apply(detect_effect=None, *, enabled=True):
    from config import settings
    from scripts import collect_satellite as collector

    calls = []
    run = SimpleNamespace(session=Mock())

    def record(name, result=None, effect=None):
        def step(*args, **kwargs):
            calls.append((name, kwargs))
            if effect is not None:
                raise effect
            return result
        return step

    with tempfile.TemporaryDirectory() as directory, \
            patch("services.autonomous_monitoring.begin_apply_run", record("begin", run)), \
            patch("services.autonomous_monitoring.heartbeat", record("heartbeat")), \
            patch("services.autonomous_monitoring.refresh_freshness", record("freshness", 5)), \
            patch("services.autonomous_monitoring.detect_observation_candidates",
                  record("detect", {"assessed_fields": 1, "observation_candidates": 1}, detect_effect)), \
            patch("services.autonomous_monitoring.reconcile_pixel_candidates",
                  record("promote", {"inserted_candidates": 0, "automatic_inspections": 1})), \
            patch("services.closed_loop_agronomy.reconcile_pending", record("verify", {"eligible": 0})), \
            patch("services.autonomous_monitoring.finish_apply_run", record("finish")), \
            patch.dict("os.environ", {"AGROSAT_RELEASE_COMMIT": "a" * 40}),             patch.object(settings, "observation_detection_enabled", enabled):
        code, summary = collector.run(
            apply_invocation(Path(directory)), child_runner=healthy_child,
            lock_acquire=lambda *args, **kwargs: "lock", lock_release=lambda lock: None,
            run_id_factory=lambda: "run225",
        )
    return code, summary, calls


def test_collector_detects_after_freshness_and_before_promotion():
    code, summary, calls = run_apply()
    order = [name for name, _ in calls if name != "heartbeat"]
    assert order == ["begin", "freshness", "detect", "promote", "verify", "finish"]
    assert code == 0
    assert summary["monitoring"]["detection"]["observation_candidates"] == 1
    assert summary["monitoring"]["detection"]["enabled"] is True
    finish = dict(calls)["finish"]
    assert finish["exit_code"] == 0 and finish["counters"]["detection"]["assessed_fields"] == 1


def test_detection_is_off_until_the_deployment_enables_it():
    from config import Settings

    assert Settings.model_fields["observation_detection_enabled"].default is False
    code, summary, calls = run_apply(enabled=False)
    order = [name for name, _ in calls if name != "heartbeat"]
    assert order == ["begin", "freshness", "promote", "verify", "finish"]
    assert code == 0
    assert summary["monitoring"]["detection"] == {
        "enabled": False, "assessed_fields": 0, "observation_candidates": 0,
    }


def test_a_failed_monitoring_step_still_terminalises_the_run():
    code, summary, calls = run_apply(detect_effect=RuntimeError("detector exploded"))
    order = [name for name, _ in calls if name != "heartbeat"]
    assert order == ["begin", "freshness", "detect", "finish"]
    assert code == 4
    assert dict(calls)["finish"]["exit_code"] == 4
    assert any("monitoring reconciliation failed" in item for item in summary["diagnostics"])
