"""Unit contracts: authorization, migration planning, task schedules, health, state, profiles."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

from controlplane import health, migration
from controlplane.authorization import validate_authorization
from controlplane.common import ControlPlaneError, find_secrets
from controlplane.manifest import check_runtime, load_runtime_contract
from controlplane.profiles import PRODUCTION, load_rehearsal_profile
from controlplane.state import ReleaseState, used_release_ids
from controlplane.tasks import TaskAction, contract_violations, parse_task_xml, rebind_violations
from fakehost import graph_until

NOW = datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc)
CANDIDATE, CURRENT = "a" * 40, "b" * 40


# ---------------------------------------------------------------- authorization

def write_authorization(tmp_path: Path, **overrides) -> Path:
    document = {"schema_version": 1, "kind": "agrosat_release_authorization", "operation": "release",
                "release_id": "R20260926-task231", "candidate_sha": CANDIDATE, "expected_current_sha": CURRENT,
                "database_name": "agrosat",
                "migration": {"authorized": False, "from_revision": None, "to_revision": None},
                "database_rollback_strategy": "none", "restore_backup_sha256": None,
                "created_at": (NOW - timedelta(minutes=5)).isoformat(),
                "expires_at": (NOW + timedelta(hours=3)).isoformat(), "authorized_by": "release manager"}
    document.update(overrides)
    for key in [key for key, value in overrides.items() if value is KeyError]:
        document.pop(key)
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def validate(path: Path, **overrides):
    arguments = dict(operation="release", release_id="R20260926-task231", candidate_sha=CANDIDATE,
                     expected_current_sha=CURRENT, database_name="agrosat",
                     forbidden_roots=[PRODUCTION.release_root, PRODUCTION.runtime_root], used_release_ids=set(),
                     now=NOW)
    arguments.update(overrides)
    return validate_authorization(path, **arguments)


def test_production_authorization_parses_against_the_fixed_production_identity(tmp_path):
    path = write_authorization(tmp_path)
    result = validate(path, database_name=PRODUCTION.database_name)
    assert result["sha256"] and result["database_name"] == "agrosat"


@pytest.mark.parametrize("overrides,call,code", [
    ({"candidate_sha": "c" * 40}, {}, "AUTHORIZATION_CANDIDATE_MISMATCH"),
    ({"expected_current_sha": "c" * 40}, {}, "AUTHORIZATION_CURRENT_SHA_MISMATCH"),
    ({"expires_at": (NOW - timedelta(seconds=1)).isoformat()}, {}, "AUTHORIZATION_EXPIRED"),
    ({"created_at": (NOW + timedelta(hours=1)).isoformat(), "expires_at": (NOW + timedelta(hours=2)).isoformat()}, {},
     "AUTHORIZATION_NOT_YET_VALID"),
    ({"expires_at": (NOW + timedelta(hours=30)).isoformat()}, {}, "AUTHORIZATION_WINDOW_REJECTED"),
    ({"database_name": "agrosat_task230_x"}, {}, "AUTHORIZATION_DATABASE_MISMATCH"),
    ({"operation": "rollback"}, {}, "AUTHORIZATION_OPERATION_MISMATCH"),
    ({"candidate_sha": "A" * 40}, {"candidate_sha": "A" * 40}, "AUTHORIZATION_IDENTITY_MALFORMED"),
    ({"release_id": "x"}, {"release_id": "x"}, "RELEASE_ID_MALFORMED"),
    ({"release_id": "R20260926-other"}, {}, "AUTHORIZATION_RELEASE_ID_MISMATCH"),
    ({"unexpected": True}, {}, "AUTHORIZATION_KEYSET_REJECTED"),
    ({"authorized_by": KeyError}, {}, "AUTHORIZATION_KEYSET_REJECTED"),
    ({"authorized_by": "postgresql://user:secret@db/agrosat"}, {}, "AUTHORIZATION_CONTAINS_SECRET"),
    ({"migration": {"authorized": False, "from_revision": "x", "to_revision": None}}, {}, "AUTHORIZATION_MIGRATION_MALFORMED"),
    ({"database_rollback_strategy": "drop_everything"}, {}, "AUTHORIZATION_DB_STRATEGY_REJECTED"),
    ({"restore_backup_sha256": "d" * 64}, {}, "AUTHORIZATION_BACKUP_IDENTITY_UNEXPECTED"),
    ({}, {"used_release_ids": {"R20260926-task231"}}, "AUTHORIZATION_REPLAY_REJECTED"),
    ({}, {"expected_sha256": "0" * 64}, "AUTHORIZATION_SHA256_MISMATCH"),
])
def test_authorization_fails_closed(tmp_path, overrides, call, code):
    path = write_authorization(tmp_path, **overrides)
    with pytest.raises(ControlPlaneError) as caught:
        validate(path, **call)
    assert caught.value.code == code


def test_rollback_restore_requires_backup_identity(tmp_path):
    path = write_authorization(tmp_path, operation="rollback", database_rollback_strategy="restore_validated_backup")
    with pytest.raises(ControlPlaneError) as caught:
        validate(path, operation="rollback")
    assert caught.value.code == "AUTHORIZATION_BACKUP_IDENTITY_REQUIRED"
    path = write_authorization(tmp_path, operation="rollback", database_rollback_strategy="restore_validated_backup",
                               restore_backup_sha256="e" * 64)
    assert validate(path, operation="rollback")["restore_backup_sha256"] == "e" * 64


def test_authorization_inside_a_source_tree_is_rejected(tmp_path):
    path = write_authorization(tmp_path)
    with pytest.raises(ControlPlaneError) as caught:
        validate(path, forbidden_roots=[tmp_path])
    assert caught.value.code == "AUTHORIZATION_INSIDE_SOURCE_TREE"


# ---------------------------------------------------------------- migration

CONTRACT = migration.load_rollback_contract()


def test_shipped_migrations_are_all_classified_by_alembic_graph_semantics():
    backend = Path(__file__).resolve().parents[2] / "backend"
    graph = migration.alembic_graph(Path(sys.executable), backend)
    assert migration.single_head(graph) == "0016_operational_command_center"
    assert migration.verify_contract_covers_graph(CONTRACT, graph) == {"classified": 17, "graph_revisions": 17}


def test_destructive_migrations_can_never_auto_downgrade():
    for item in CONTRACT["migrations"]:
        automatic = item["classification"] == "reversible_without_data_loss"
        assert item["automatic_downgrade_allowed"] is automatic
        if item["downgrade_drops_data_written_after_upgrade"] or item["upgrade_destroys_existing_data"]:
            assert item["automatic_downgrade_allowed"] is False, item["revision"]
            assert item["rollback_strategy"] == "restore_validated_pre_release_backup_or_roll_forward"
    classes = {item["revision"]: item["classification"] for item in CONTRACT["migrations"]}
    assert classes["0001_baseline_existing_schema"] == "restore_backup_or_roll_forward_only"
    assert classes["0013_anomaly_inspection_workflow"] == "restore_backup_or_roll_forward_only"
    assert {revision for revision, value in classes.items() if value == "reversible_without_data_loss"} == {
        "0003_add_ndvi_unique_constraint", "0004_repair_core_constraints"}


def test_contract_rejects_an_unclassified_or_misplaced_migration():
    graph = graph_until("0016_operational_command_center")
    graph["revisions"].insert(0, {"revision": "0017_new", "down_revisions": ["0016_operational_command_center"],
                                  "dependencies": [], "branch_labels": [], "path": "backend/alembic/versions/0017_new.py"})
    graph["heads"] = ["0017_new"]
    with pytest.raises(ControlPlaneError) as caught:
        migration.verify_contract_covers_graph(CONTRACT, graph)
    assert "unclassified:0017_new" in caught.value.facts["problems"]
    plan = migration.plan_migration(graph, ["0016_operational_command_center"], CONTRACT)
    assert plan["status"] == "blocked" and plan["reason"] == "migration_unclassified"


def test_migration_plan_rules():
    graph = graph_until("0016_operational_command_center")
    assert migration.plan_migration(graph, ["0016_operational_command_center"], CONTRACT)["status"] == "noop"
    upgrade = migration.plan_migration(graph, ["0014_autonomous_satellite_monitoring"], CONTRACT)
    assert upgrade["status"] == "upgrade"
    assert upgrade["upgrade_path"] == ["0015_closed_loop_agronomy", "0016_operational_command_center"]
    assert upgrade["automatic_downgrade_allowed"] is False
    assert migration.plan_migration(graph, ["0099_ahead"], CONTRACT)["reason"] == "database_revision_unknown_to_candidate"
    assert migration.plan_migration(graph, [], CONTRACT)["reason"] == "database_revision_missing"
    assert migration.plan_migration(graph, ["a", "b"], CONTRACT)["reason"] == "database_multiple_heads"
    two_heads = dict(graph, heads=["0016_operational_command_center", "0015_closed_loop_agronomy"])
    assert migration.plan_migration(two_heads, ["0015_closed_loop_agronomy"], CONTRACT)["reason"] == "candidate_head_count"
    reversible = migration.plan_migration(graph_until("0004_repair_core_constraints"), ["478de3d1f6d0"], CONTRACT)
    assert reversible["automatic_downgrade_allowed"] is True


def test_downgrade_decision_cases():
    assert migration.downgrade_decision([])["case"] == 1
    assert migration.downgrade_decision([{"revision": "0004", "classification": "reversible_without_data_loss"}])["case"] == 2
    decision = migration.downgrade_decision([{"revision": "0004", "classification": "reversible_without_data_loss"},
                                             {"revision": "0016", "classification": "destructive_after_data"}])
    assert decision["case"] == 3 and decision["automatic_downgrade"] == "forbidden"
    assert decision["blocking_steps"] == ["0016"]


# ---------------------------------------------------------------- scheduled task contracts

def task_xml(triggers: str, *, limit="PT6H", count=3, interval="PT15M", user="S-1-5-18",
             command="powershell.exe") -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Principals><Principal id="Author"><UserId>{user}</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings><ExecutionTimeLimit>{limit}</ExecutionTimeLimit><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <RestartOnFailure><Count>{count}</Count><Interval>{interval}</Interval></RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable></Settings>
  <Triggers>{triggers}</Triggers>
  <Actions Context="Author"><Exec><Command>{command}</Command><Arguments>-File "x"</Arguments><WorkingDirectory>C:\\r</WorkingDirectory></Exec></Actions>
</Task>"""


def daily(time: str) -> str:
    return (f"<CalendarTrigger><StartBoundary>2026-09-23T{time}+05:00</StartBoundary>"
            "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>")


def sentinel(triggers: str, **kwargs):
    return parse_task_xml("\\AgroSat_PROGRAM_R3_SentinelCycle", task_xml(triggers, **kwargs))


def test_production_sentinel_contract_is_exactly_one_06_00_trigger():
    assert contract_violations(sentinel(daily("06:00:00")), "sentinel", production=True) == []
    assert "sentinel_trigger_count:2" in contract_violations(
        sentinel(daily("06:00:00") + daily("18:00:00")), "sentinel", production=True)
    assert "sentinel_trigger_count:0" in contract_violations(sentinel(""), "sentinel", production=True)
    assert "sentinel_trigger_time:07:00:00" in contract_violations(sentinel(daily("07:00:00")), "sentinel", production=True)
    assert "principal:S-1-5-21-1" in contract_violations(sentinel(daily("06:00:00"), user="S-1-5-21-1"),
                                                         "sentinel", production=True)


def test_notifications_contract_is_every_15_minutes():
    repetition = ("<TimeTrigger><StartBoundary>2026-09-23T13:06:19+05:00</StartBoundary><Repetition>"
                  "<Interval>{}</Interval><Duration>P3650D</Duration></Repetition></TimeTrigger>")
    ok = parse_task_xml("\\n", task_xml(repetition.format("PT15M"), limit="PT10M", interval="PT5M"))
    assert contract_violations(ok, "notifications", production=True) == []
    slow = parse_task_xml("\\n", task_xml(repetition.format("PT30M"), limit="PT10M", interval="PT5M"))
    assert "notifications_interval:PT30M" in contract_violations(slow, "notifications", production=True)
    longer = parse_task_xml("\\n", task_xml(repetition.format("PT15M"), limit="PT1H", interval="PT5M"))
    assert "execution_time_limit:PT1H" in contract_violations(longer, "notifications", production=True)


def test_application_task_contract_and_rehearsal_tasks_never_self_trigger():
    backend = parse_task_xml("\\b", task_xml("<BootTrigger />", limit="PT0S", interval="PT1M", command="python.exe"))
    assert contract_violations(backend, "backend", production=True) == []
    assert "rehearsal_trigger_count:1" in contract_violations(backend, "backend", production=False)


def test_rebind_may_change_only_the_action():
    before = sentinel(daily("06:00:00"))
    action = TaskAction("powershell.exe", "-File new", "C:\\new")
    from dataclasses import replace
    after = replace(before, actions=(action,))
    assert rebind_violations(before, after, action) == []
    drifted = replace(after, triggers=before.triggers + before.triggers)
    assert "schedule_identity_changed" in rebind_violations(before, drifted, action)
    assert "action_not_bound_as_expected" in rebind_violations(before, before, action)


# ---------------------------------------------------------------- health

def result(status, body):
    return health.HttpResult(status, json.dumps(body).encode())


def ready_body(**database):
    component = {"status": "ready", "migration_revision": "0016", "expected_migration_revision": "0016",
                 "revision_match": True, "reason": None, **database}
    return {"status": "ready" if component["status"] == "ready" else "not_ready", "release_revision": CANDIDATE,
            "components": {"database": component,
                           "collector": {"status": "missing", "required_for_api_readiness": False},
                           "cache": {"status": "unavailable", "required_for_api_readiness": False}}}


def test_backend_health_requires_exact_release_and_schema_but_not_collector_or_cache():
    live = result(200, {"status": "alive", "release_revision": CANDIDATE})
    passing = health.evaluate_backend(live, result(200, ready_body()), CANDIDATE, "0016")
    assert passing["pass"] and passing["non_blocking"]["collector_status"] == "missing"
    assert passing["non_blocking"]["cache_status"] == "unavailable"
    wrong_release = health.evaluate_backend(result(200, {"status": "alive", "release_revision": CURRENT}),
                                            result(200, ready_body()), CANDIDATE, "0016")
    assert not wrong_release["pass"] and not wrong_release["checks"]["live_release_revision"]
    mismatch = health.evaluate_backend(live, result(503, ready_body(status="schema_mismatch", revision_match=False,
                                                                    migration_revision="0015")), CANDIDATE, "0016")
    assert not mismatch["pass"] and not mismatch["checks"]["database_revision_match"]
    wrong_expected = health.evaluate_backend(live, result(200, ready_body()), CANDIDATE, "0017")
    assert not wrong_expected["pass"]


def test_pre_task228_payload_is_accepted_only_for_previous_releases_and_still_needs_the_exact_head():
    live = result(200, {"status": "alive", "release_revision": CANDIDATE})
    legacy = {"status": "ready", "release_revision": CANDIDATE,
              "components": {"database": {"status": "ready", "migration_revision": "0016"}}}
    assert not health.evaluate_backend(live, result(200, legacy), CANDIDATE, "0016")["pass"]
    compatible = health.evaluate_backend(live, result(200, legacy), CANDIDATE, "0016", pre_task228_compatible=True)
    assert compatible["pass"] and compatible["readiness_contract"] == "pre_task228_compatible"
    behind = health.evaluate_backend(live, result(200, legacy), CANDIDATE, "0017", pre_task228_compatible=True)
    assert not behind["pass"]
    modern_mismatch = health.evaluate_backend(live, result(503, ready_body(status="schema_mismatch", revision_match=False)),
                                              CANDIDATE, "0016", pre_task228_compatible=True)
    assert not modern_mismatch["pass"] and modern_mismatch["readiness_contract"] == "task228"


def test_frontend_health_requires_the_release_bundle_and_candidate_backend():
    index = b"<!doctype html>"
    import hashlib
    ok = health.evaluate_frontend(health.HttpResult(200, index), result(200, {"release_revision": CANDIDATE}),
                                  hashlib.sha256(index).hexdigest(), CANDIDATE)
    assert ok["pass"]
    stale = health.evaluate_frontend(health.HttpResult(200, b"old"), result(200, {"release_revision": CANDIDATE}),
                                     hashlib.sha256(index).hexdigest(), CANDIDATE)
    assert not stale["checks"]["index_is_release_bundle"]


def test_health_probes_refuse_non_loopback_targets():
    assert health.http_get("http://10.0.0.5:8000/health/live").error == "target_not_loopback"


# ---------------------------------------------------------------- state, profiles, runtime

def test_state_identity_and_terminal_refusal(tmp_path):
    identity = {"release_id": "R-state-0001", "candidate_sha": CANDIDATE, "expected_current_sha": CURRENT}
    state = ReleaseState.open(tmp_path, "R-state-0001", identity, ("A", "B"), operation="release")
    with pytest.raises(ControlPlaneError) as caught:
        ReleaseState.open(tmp_path, "R-state-0001", dict(identity, candidate_sha="c" * 40), ("A", "B"), operation="release")
    assert caught.value.code == "RELEASE_IDENTITY_CONTRADICTION"
    state.finish("completed")
    with pytest.raises(ControlPlaneError) as caught:
        ReleaseState.open(tmp_path, "R-state-0001", identity, ("A", "B"), operation="release")
    assert caught.value.code == "RELEASE_ALREADY_COMPLETED"
    assert "R-state-0001" in used_release_ids(tmp_path)


def test_evidence_refuses_credentials(tmp_path):
    assert find_secrets({"url": "postgresql://u:p@host/db"}) == ["$.url"]
    assert find_secrets({"DATABASE_URL": "anything"}) == ["$.DATABASE_URL"]
    assert find_secrets({"credential_values_logged": False, "database_name": "agrosat"}) == []


def rehearsal_profile(tmp_path, **overrides):
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    (root / ".agrosat-rehearsal-root.json").write_text(json.dumps({"profile_id": "TASK230"}), encoding="utf-8")
    env = root / "env" / "r.env"
    env.parent.mkdir(exist_ok=True)
    env.write_text("DATABASE_URL=postgresql://u:p@localhost:5432/agrosat_task230_rel\n", encoding="utf-8")
    document = {"schema_version": 1, "kind": "agrosat_rehearsal_profile", "profile_id": "TASK230",
                "rehearsal_root": str(root), "release_root": str(root / "releases"), "runtime_root": str(root / "runtime"),
                "control_root": str(root / "control"), "runtime_env_file": str(env),
                "database_name": "agrosat_task230_rel", "backend_port": 58240, "frontend_port": 58241,
                "tasks": {"backend": "\\AgroSat_TASK230_Backend", "frontend": "\\AgroSat_TASK230_Frontend",
                          "sentinel": "\\AgroSat_TASK230_SentinelCycle",
                          "notifications": "\\AgroSat_TASK230_OperationalNotifications"},
                "node_executable": r"C:\Program Files\nodejs\node.exe", "pg_bin": r"C:\Program Files\PostgreSQL\16\bin",
                "signing_thumbprints": ["816767BE400FE53327432B12B29FE4B5809CA4CA"],
                "timezone_id": "West Asia Standard Time", "fault_injection": None, "worker_dry_run": False,
                "backup_task": "\\AgroSat_TASK230_DatabaseBackup"}
    document.update(overrides)
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_rehearsal_profile_accepts_only_isolated_targets(tmp_path):
    assert load_rehearsal_profile(rehearsal_profile(tmp_path)).database_name == "agrosat_task230_rel"
    cases = [
        ({"database_name": "agrosat"}, "REHEARSAL_DATABASE_GUARD_FAILED"),
        ({"database_name": "agrosat_h0a_task229"}, "REHEARSAL_DATABASE_GUARD_FAILED"),
        ({"backend_port": 8000}, "REHEARSAL_PORT_REJECTED"),
        ({"frontend_port": 5173}, "REHEARSAL_PORT_REJECTED"),
        ({"release_root": r"C:\AgroSat_releases\PROGRAM_R3"}, "REHEARSAL_ROOT_GUARD_FAILED"),
        ({"rehearsal_root": r"C:\AgroSat"}, "REHEARSAL_ROOT_GUARD_FAILED"),
        ({"tasks": {"backend": "\\AgroSat_PROGRAM_R3_Stabilization_Backend", "frontend": "\\AgroSat_TASK230_F",
                    "sentinel": "\\AgroSat_TASK230_S", "notifications": "\\AgroSat_TASK230_N"}},
         "REHEARSAL_TASK_IDENTITY_REJECTED"),
        ({"fault_injection": "drop_database"}, "REHEARSAL_FAULT_REJECTED"),
        ({"backup_task": "\\AgroSat_PROGRAM_R3_DatabaseBackup"}, "REHEARSAL_TASK_IDENTITY_REJECTED"),
    ]
    for overrides, code in cases:
        with pytest.raises(ControlPlaneError) as caught:
            load_rehearsal_profile(rehearsal_profile(tmp_path, **overrides))
        assert caught.value.code == code, overrides


def test_rehearsal_root_needs_its_marker(tmp_path):
    path = rehearsal_profile(tmp_path)
    (tmp_path / "root" / ".agrosat-rehearsal-root.json").unlink()
    with pytest.raises(ControlPlaneError) as caught:
        load_rehearsal_profile(path)
    assert caught.value.code == "REHEARSAL_ROOT_GUARD_FAILED"


def test_runtime_contract_matches_the_supported_toolchain():
    contract = load_runtime_contract()
    assert check_runtime(contract, python_version="3.14.5", node_version="v24.16.0",
                         pg_dump_version="pg_dump (PostgreSQL) 16.14", lockfile_version=3)["pass"]
    assert not check_runtime(contract, python_version="3.11.9", node_version="v24.16.0",
                             pg_dump_version=None, lockfile_version=3)["pass"]
    assert not check_runtime(contract, python_version="3.14.5", node_version="v20.1.0",
                             pg_dump_version=None, lockfile_version=3)["pass"]
