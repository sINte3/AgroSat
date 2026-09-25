"""Release runtime contract (TASK_212 intent, TASK_230 production control plane).

The rehearsal-era PowerShell release scripts were replaced by the control plane
(ops/release/controlplane). These tests keep every TASK_212 guarantee against
the new state machine, driven on an in-memory Windows host with real Git
material: migration before switch, health before commit, database restore
before the previous release returns, manual recovery when a restore fails,
identity checks before any restore, and isolated rehearsal targets only.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

OPS = Path(__file__).resolve().parents[2] / "ops"
for path in (OPS / "release", OPS / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from controlplane.common import ControlPlaneError, read_json  # noqa: E402
from controlplane.controller import Controller, Deadlines, Request  # noqa: E402
from controlplane.profiles import load_rehearsal_profile  # noqa: E402
from fakehost import authorize, build_world  # noqa: E402

FAST = Deadlines(stop_seconds=10, legacy_handoff_seconds=5, listener_seconds=10, health_seconds=10,
                 worker_idle_seconds=10, poll_seconds=0.5)
MIGRATION = {"authorized": True, "from_revision": "0015_closed_loop_agronomy",
             "to_revision": "0016_operational_command_center"}


def run(world, *, operation="release", release_id="R212-release-0001", candidate=None, current=None,
        authorization=None, authorization_sha256=None):
    request = Request(operation=operation, mode="rehearsal", release_id=release_id,
                      candidate_sha=candidate or world.candidate, expected_current_sha=current or world.current,
                      authorization_path=authorization or world.authorization,
                      authorization_sha256=authorization_sha256, profile=world.profile,
                      repository=world.repository, backup_policy_path=world.policy)
    return Controller(request, world.host, deadlines=FAST, now=world.host.now()).run()


def pointer(world):
    path = world.profile.control_root / "current-release.json"
    return read_json(path, "X") if path.exists() else None


def backend_action(world):
    return world.host.tasks[world.profile.tasks.backend].definition.actions[0]


def serving_sha(world):
    return world.host.serving[world.profile.backend_port].sha


def test_successful_isolated_release_runs_migration_health_and_switches_exact_pointers(tmp_path):
    world = build_world(tmp_path, current_head="0015_closed_loop_agronomy")
    authorize(world, release_id="R212-release-0001", migration=MIGRATION)
    data = run(world)
    assert data["status"] == "completed"
    assert [entry for entry in world.host.actions_log if entry[0] == "alembic"] == [
        ("alembic", "upgrade", "0016_operational_command_center")]
    assert data["gate_results"]["VERIFY_BACKEND"]["status"] == "passed"
    assert data["gate_results"]["VERIFY_FRONTEND"]["status"] == "passed"
    assert pointer(world)["sha"] == world.candidate
    assert read_json(world.profile.control_root / "previous-release.json", "X")["sha"] == world.current
    assert serving_sha(world) == world.candidate


def test_isolated_release_health_failure_restores_database_before_previous_release_restarts(tmp_path):
    world = build_world(tmp_path, current_head="0015_closed_loop_agronomy", fault="backend_unready_after_switch")
    authorize(world, release_id="R212-release-0001", migration=MIGRATION, strategy="restore_validated_backup")
    order = []
    swap, start = world.host.backup_restore_swap, world.host.start_task

    def record_swap(*args):
        order.append("restore")
        return swap(*args)

    def record_start(name):
        order.append(("start", name))
        return start(name)

    world.host.backup_restore_swap, world.host.start_task = record_swap, record_start
    data = run(world)
    assert data["status"] == "rolled_back"
    restore_index = order.index("restore")
    assert order[restore_index + 1:].count(("start", world.profile.tasks.backend)) == 1
    assert world.host.swaps[0]["revision"] == "0015_closed_loop_agronomy"
    assert world.host.revisions == ["0015_closed_loop_agronomy"]
    assert pointer(world) is None and serving_sha(world) == world.current


def test_failed_health_recovery_restore_stops_for_manual_recovery_without_moving_the_pointer(tmp_path):
    world = build_world(tmp_path, current_head="0015_closed_loop_agronomy", fault="backend_unready_after_switch")
    authorize(world, release_id="R212-release-0001", migration=MIGRATION, strategy="restore_validated_backup")

    def failing_restore(*args):
        raise ControlPlaneError("RESTORE_FAILED", "the partial target is retained for inspection; nothing was dropped")

    world.host.backup_restore_swap = failing_restore
    data = run(world)
    assert data["status"] == "manual_recovery_required"
    assert data["rollback_result"]["status"] == "MANUAL_RECOVERY_REQUIRED"
    assert data["rollback_result"]["error"]["code"] == "RESTORE_FAILED"
    assert pointer(world) is None
    assert world.host.revisions == ["0016_operational_command_center"]  # nothing destroyed, nothing hidden


def released_world(tmp_path):
    world = build_world(tmp_path, current_head="0015_closed_loop_agronomy")
    authorize(world, release_id="R212-release-0001", migration=MIGRATION)
    data = run(world)
    assert data["status"] == "completed"
    return world, data["backup"]["sha256"]


def rollback(world, **kwargs):
    return run(world, operation="rollback", release_id="R212-rollback-0001", candidate=world.current,
               current=world.candidate, **kwargs)


def authorize_rollback(world, **overrides):
    return authorize(world, operation="rollback", release_id="R212-rollback-0001", candidate=world.current,
                     current=world.candidate, strategy="restore_validated_backup", **overrides)


def test_explicit_rollback_identity_mismatch_precedes_any_restore_or_pointer_mutation(tmp_path):
    world, _ = released_world(tmp_path)
    before = (backend_action(world), pointer(world))
    authorize_rollback(world, backup_sha256="d" * 64)
    data = rollback(world)
    assert data["status"] == "failed"
    evidence = read_json(world.profile.control_root / "releases" / "R212-rollback-0001" /
                         data["gate_results"]["ROLLBACK_PLAN"]["evidence"][-1], "X")
    assert evidence["error"]["code"] == "RESTORE_BACKUP_NOT_FOUND"
    assert world.host.swaps == [] and (backend_action(world), pointer(world)) == before


def test_successful_explicit_rollback_restores_exact_bindings_and_pointer(tmp_path):
    world, backup_sha256 = released_world(tmp_path)
    recorded = read_json(world.profile.control_root / "releases" / "R212-release-0001" / "snapshots" /
                         "tasks-before.json", "X")
    authorize_rollback(world, backup_sha256=backup_sha256)
    data = rollback(world)
    assert data["status"] == "completed", data["gate_results"]
    assert data["rollback_plan"]["decision"]["case"] == 3
    assert world.host.swaps and world.host.swaps[-1]["revision"] == "0015_closed_loop_agronomy"
    assert backend_action(world).evidence() == recorded["backend"]["actions"][0]
    assert pointer(world)["sha"] == world.current and serving_sha(world) == world.current


@pytest.mark.parametrize("case,expected", [
    ("missing", "AUTHORIZATION_MISSING"),
    ("malformed", "AUTHORIZATION_MALFORMED"),
    ("backup", "RESTORE_BACKUP_NOT_FOUND"),
    ("sha", "AUTHORIZATION_SHA256_MISMATCH"),
    ("database", "AUTHORIZATION_DATABASE_MISMATCH"),
    ("outside", "AUTHORIZATION_INSIDE_SOURCE_TREE"),
])
def test_recovery_identity_and_restore_authorization_fail_before_restore(tmp_path, case, expected):
    world, backup_sha256 = released_world(tmp_path)
    path = authorize_rollback(world, backup_sha256="d" * 64 if case == "backup" else backup_sha256)
    kwargs = {}
    if case == "missing":
        kwargs["authorization"] = path.with_name("missing.json")
    elif case == "malformed":
        path.write_text("not-json", encoding="utf-8")
    elif case == "sha":
        kwargs["authorization_sha256"] = "0" * 64
    elif case == "database":
        document = json.loads(path.read_text())
        document["database_name"] = "agrosat_task230_other"
        path.write_text(json.dumps(document))
    elif case == "outside":
        inside = world.repository / "authorization.json"
        inside.write_text(path.read_text())
        kwargs["authorization"] = inside
    before = backend_action(world)
    try:
        data = rollback(world, **kwargs)
        code = read_json(world.profile.control_root / "releases" / "R212-rollback-0001" /
                         data["gate_results"]["ROLLBACK_PLAN"]["evidence"][-1], "X")["error"]["code"]
    except ControlPlaneError as error:
        code = error.code
    assert code == expected
    assert world.host.swaps == [] and backend_action(world) == before


def rehearsal_profile(tmp_path, **overrides):
    root = tmp_path / "allowed"
    root.mkdir(exist_ok=True)
    (root / ".agrosat-rehearsal-root.json").write_text(json.dumps({"profile_id": "TASK212"}))
    env = root / "rehearsal.env"
    env.write_text("DATABASE_URL=postgresql://u:p@localhost:5432/agrosat_task212_runtime\n")
    document = {"schema_version": 1, "kind": "agrosat_rehearsal_profile", "profile_id": "TASK212",
                "rehearsal_root": str(root), "release_root": str(root / "releases"), "runtime_root": str(root / "runtime"),
                "control_root": str(root / "control"), "runtime_env_file": str(env),
                "database_name": "agrosat_task212_runtime", "backend_port": 58212, "frontend_port": 58213,
                "tasks": {"backend": "\\AgroSat_TASK212_Backend", "frontend": "\\AgroSat_TASK212_Frontend",
                          "sentinel": "\\AgroSat_TASK212_Sentinel", "notifications": "\\AgroSat_TASK212_Notifications"},
                "node_executable": r"C:\Program Files\nodejs\node.exe", "pg_bin": r"C:\Program Files\PostgreSQL\16\bin",
                "signing_thumbprints": ["816767BE400FE53327432B12B29FE4B5809CA4CA"], "timezone_id": "West Asia Standard Time",
                "fault_injection": None, "worker_dry_run": False, "backup_task": None}
    document.update(overrides)
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(document))
    return path


def test_rehearsal_guards_reject_product_database_and_nonisolated_roots(tmp_path):
    assert load_rehearsal_profile(rehearsal_profile(tmp_path)).database_name == "agrosat_task212_runtime"
    for overrides, code in (({"database_name": "agrosat"}, "REHEARSAL_DATABASE_GUARD_FAILED"),
                            ({"database_name": "staging"}, "REHEARSAL_DATABASE_GUARD_FAILED"),
                            ({"rehearsal_root": "C:\\AgroSat"}, "REHEARSAL_ROOT_GUARD_FAILED"),
                            ({"rehearsal_root": "C:\\AgroSat_staging"}, "REHEARSAL_ROOT_GUARD_FAILED")):
        with pytest.raises(ControlPlaneError) as caught:
            load_rehearsal_profile(rehearsal_profile(tmp_path, **overrides))
        assert caught.value.code == code, overrides
