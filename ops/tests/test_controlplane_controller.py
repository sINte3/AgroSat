"""The release and rollback state machines, driven end to end on a fake host."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path

import pytest

from controlplane.common import ControlPlaneError, find_secrets, read_json, sha256_file
from controlplane.controller import Controller, Deadlines, Request
from controlplane.profiles import PRODUCTION
from controlplane.state import RELEASE_GATES, controller_lock
from fakehost import authorize, build_world

FAST = Deadlines(stop_seconds=10, legacy_handoff_seconds=5, listener_seconds=10, health_seconds=10,
                 worker_idle_seconds=10, poll_seconds=0.5)


def request(world, *, operation="release", release_id="R230-rehearsal-001", candidate=None, current=None,
            policy=True, authorization=None, **overrides):
    return Request(operation=operation, mode="rehearsal", release_id=release_id,
                   candidate_sha=candidate or world.candidate, expected_current_sha=current or world.current,
                   authorization_path=authorization or world.authorization, authorization_sha256=None,
                   profile=world.profile, repository=world.repository,
                   backup_policy_path=world.policy if policy else None, fetch=True, **overrides)


def run(world, **kwargs):
    return Controller(request(world, **kwargs), world.host, deadlines=FAST, now=world.host.now()).run()


def state_dir(world, release_id="R230-rehearsal-001") -> Path:
    return world.profile.control_root / "releases" / release_id


def task(world, kind):
    return world.host.tasks[world.profile.tasks.all()[kind]]


def serving(world, kind):
    port = world.profile.backend_port if kind == "backend" else world.profile.frontend_port
    return world.host.serving.get(port)


def test_release_passes_every_gate_with_evidence_and_commits(tmp_path):
    world = build_world(tmp_path)
    authorize(world)
    data = run(world)
    assert data["status"] == "completed", data["gate_results"]
    assert [data["gate_results"][gate]["status"] for gate in RELEASE_GATES] == ["passed"] * len(RELEASE_GATES)
    for gate in RELEASE_GATES:
        evidence = state_dir(world) / data["gate_results"][gate]["evidence"][-1]
        assert read_json(evidence, "X")["status"] == "passed"
    for field in ("release_id", "previous_sha", "candidate_sha", "db_revision_before", "db_revision_after",
                  "started_at", "completed_at", "current_gate", "gate_results", "rollback_required", "rollback_result"):
        assert field in data
    assert data["previous_sha"] == world.current and data["candidate_sha"] == world.candidate
    assert data["db_revision_before"] == data["db_revision_after"] == "0016_operational_command_center"
    assert data["rollback_required"] is False and data["backup"]["backup_id"]
    pointer = read_json(world.profile.control_root / "current-release.json", "X")
    assert pointer["sha"] == world.candidate and pointer["release_id"] == "R230-rehearsal-001"
    assert read_json(world.profile.control_root / "previous-release.json", "X")["sha"] == world.current
    assert serving(world, "backend").sha == world.candidate and serving(world, "frontend").sha == world.candidate
    for kind in ("sentinel", "notifications"):
        assert str(world.profile.runtime_root / world.candidate) in task(world, kind).definition.actions[0].arguments
    assert not find_secrets(data)
    assert (state_dir(world) / "summary.md").exists()
    manifest = read_json(world.profile.release_root / world.candidate / "release-manifest.json", "X")
    assert manifest["schema_version"] == 2 and manifest["git_sha"] == world.candidate
    assert manifest["expected_current_sha"] == world.current and manifest["alembic"]["head"] == "0016_operational_command_center"
    assert "program_branch" not in json.dumps(manifest) and "macrostage" not in json.dumps(manifest)
    config = read_json(world.profile.runtime_root / world.candidate / "application" / "application-release.json", "X")
    assert config["schema_version"] == 2 and config["profile"] == "rehearsal"
    assert config["ownership_sha256"] == sha256_file(world.profile.runtime_root / world.candidate / "application"
                                                     / "agrosat_process_ownership.py")


def test_legacy_supervisor_handoff_ends_only_the_captured_descendants(tmp_path):
    world = build_world(tmp_path, legacy_current=True)
    authorize(world)
    foreign = world.host._spawn(1, "python.exe")  # an unrelated process on the host
    data = run(world)
    assert data["status"] == "completed"
    switch = read_json(state_dir(world) / data["gate_results"]["SWITCH_BACKEND"]["evidence"][-1], "X")
    terminated = [row["pid"] for row in switch["stop"]["terminated_by_legacy_handoff"]]
    captured = {row["pid"] for row in switch["stop"]["captured"]}
    assert terminated and set(terminated) <= captured
    assert foreign.pid in world.host.processes and foreign.pid not in world.host.legacy_terminated


def test_job_owned_supervisor_needs_no_handoff(tmp_path):
    world = build_world(tmp_path, legacy_current=False)
    authorize(world)
    data = run(world)
    assert data["status"] == "completed"
    switch = read_json(state_dir(world) / data["gate_results"]["SWITCH_BACKEND"]["evidence"][-1], "X")
    assert switch["stop"]["clean_after_stop"] is True and switch["stop"]["terminated_by_legacy_handoff"] == []
    assert world.host.legacy_terminated == []


def test_completed_release_id_never_executes_again(tmp_path):
    world = build_world(tmp_path)
    authorize(world)
    assert run(world)["status"] == "completed"
    log_length = len(world.host.actions_log)
    authorize(world)  # a fresh, valid authorization for the same release id
    with pytest.raises(ControlPlaneError) as caught:
        run(world)
    assert caught.value.code == "AUTHORIZATION_REPLAY_REJECTED"
    assert len(world.host.actions_log) == log_length


def test_interrupted_release_resumes_where_safe_without_second_listener(tmp_path):
    world = build_world(tmp_path)
    authorize(world)
    world.host.crash_on = f"start:{world.profile.tasks.backend}"
    with pytest.raises(KeyboardInterrupt):
        run(world)
    interrupted = read_json(state_dir(world) / "state.json", "X")
    assert interrupted["status"] == "in_progress" and interrupted["current_gate"] == "SWITCH_BACKEND"
    world.host.crash_on = None
    data = run(world)
    assert data["status"] == "completed"
    assert data["gate_results"]["SWITCH_BACKEND"]["starts"] == 2
    assert data["gate_results"]["SWITCH_BACKEND"]["interruptions"] == 1
    assert data["gate_results"]["MATERIALIZE"]["starts"] == 1 and data["gate_results"]["BACKUP"]["starts"] == 1
    rebinds = [entry for entry in world.host.actions_log if entry[:2] == ("set_action", world.profile.tasks.backend)]
    assert len(rebinds) == 1
    assert list(world.host.ports).count(world.profile.backend_port) == 1
    assert len(world.host.backups) == 1


def test_contradictory_identity_for_an_open_release_id_is_refused(tmp_path):
    world = build_world(tmp_path)
    authorize(world)
    world.host.crash_on = f"start:{world.profile.tasks.backend}"
    with pytest.raises(KeyboardInterrupt):
        run(world)
    world.host.crash_on = None
    authorize(world, strategy="downgrade_reversible_only")  # same id, different bound identity
    with pytest.raises(ControlPlaneError) as caught:
        run(world)
    assert caught.value.code == "RELEASE_IDENTITY_CONTRADICTION"


def test_post_switch_health_failure_rolls_back_to_a_healthy_previous_release(tmp_path):
    world = build_world(tmp_path, fault="backend_unready_after_switch")
    authorize(world)
    before = task(world, "backend").definition.actions[0]
    data = run(world)
    assert data["status"] == "rolled_back"
    assert data["gate_results"]["SWITCH_BACKEND"]["status"] == "passed"
    assert data["gate_results"]["VERIFY_BACKEND"]["status"] == "failed"
    result = data["rollback_required"], data["rollback_result"]
    assert result[0] is True and result[1]["status"] == "rolled_back"
    assert result[1]["cause_gate"] == "VERIFY_BACKEND" and result[1]["database_decision"]["case"] == 1
    assert result[1]["candidate_processes_captured"] > 0 and result[1]["candidate_processes_alive"] == []
    assert result[1]["previous_health"]["backend"]["pass"] and result[1]["previous_health"]["frontend"]["pass"]
    assert task(world, "backend").definition.actions[0] == before
    assert serving(world, "backend").sha == world.current
    assert not (world.profile.control_root / "current-release.json").exists()
    registry = (world.profile.control_root / "completed-releases.jsonl").read_text().splitlines()
    assert json.loads(registry[-1])["status"] == "rolled_back"


def test_failure_before_the_switch_changes_no_service(tmp_path):
    world = build_world(tmp_path)
    world.host.revisions = ["9999_foreign_revision"]
    authorize(world)
    started = len(world.host.actions_log)
    data = run(world)
    assert data["status"] == "failed" and data["rollback_required"] is False
    assert data["gate_results"]["MIGRATION_PLAN"]["status"] == "failed"
    assert not [entry for entry in world.host.actions_log[started:] if entry[0] in ("stop", "set_action", "start", "alembic")]
    assert serving(world, "backend").sha == world.current


def test_needed_migration_must_be_exactly_authorized(tmp_path):
    world = build_world(tmp_path, current_head="0015_closed_loop_agronomy")
    authorize(world)
    data = run(world)
    assert data["status"] == "failed"
    evidence = read_json(state_dir(world) / data["gate_results"]["MIGRATION_PLAN"]["evidence"][-1], "X")
    assert evidence["error"]["code"] == "MIGRATION_NOT_AUTHORIZED"
    assert not [entry for entry in world.host.actions_log if entry[0] == "alembic"]


def test_authorized_migration_runs_once_before_the_switch(tmp_path):
    world = build_world(tmp_path, current_head="0015_closed_loop_agronomy")
    authorize(world, migration={"authorized": True, "from_revision": "0015_closed_loop_agronomy",
                                "to_revision": "0016_operational_command_center"})
    data = run(world)
    assert data["status"] == "completed"
    assert data["db_revision_before"] == "0015_closed_loop_agronomy"
    assert data["db_revision_after"] == "0016_operational_command_center"
    alembic = [entry for entry in world.host.actions_log if entry[0] == "alembic"]
    assert alembic == [("alembic", "upgrade", "0016_operational_command_center")]
    order = [entry[0] for entry in world.host.actions_log if entry[0] in ("alembic", "set_action")]
    assert order[0] == "alembic"


def test_destructive_migration_never_auto_downgrades_without_restore_authorization(tmp_path):
    world = build_world(tmp_path, current_head="0015_closed_loop_agronomy", fault="backend_unready_after_switch")
    authorize(world, migration={"authorized": True, "from_revision": "0015_closed_loop_agronomy",
                                "to_revision": "0016_operational_command_center"})
    data = run(world)
    assert data["status"] == "manual_recovery_required"
    assert data["rollback_result"]["database_decision"]["case"] == 3
    assert data["rollback_result"]["error"]["code"] == "MANUAL_RECOVERY_REQUIRED"
    assert not [entry for entry in world.host.actions_log if entry[:2] == ("alembic", "downgrade")]
    assert world.host.revisions == ["0016_operational_command_center"]  # no data destroyed


def test_destructive_migration_rolls_back_by_validated_backup_swap_when_authorized(tmp_path):
    world = build_world(tmp_path, current_head="0015_closed_loop_agronomy", fault="backend_unready_after_switch")
    authorize(world, migration={"authorized": True, "from_revision": "0015_closed_loop_agronomy",
                                "to_revision": "0016_operational_command_center"},
              strategy="restore_validated_backup")
    data = run(world)
    assert data["status"] == "rolled_back", data["rollback_result"]
    assert world.host.swaps == [{"backup_id": data["backup"]["backup_id"], "revision": "0015_closed_loop_agronomy"}]
    assert not [entry for entry in world.host.actions_log if entry[:2] == ("alembic", "downgrade")]
    assert serving(world, "backend").sha == world.current


def test_reversible_migration_is_downgraded_automatically_only_when_authorized(tmp_path):
    world = build_world(tmp_path, current_head="478de3d1f6d0", candidate_head="0004_repair_core_constraints",
                        fault="backend_unready_after_switch")
    authorize(world, migration={"authorized": True, "from_revision": "478de3d1f6d0",
                                "to_revision": "0004_repair_core_constraints"}, strategy="downgrade_reversible_only")
    data = run(world)
    assert data["status"] == "rolled_back", data["rollback_result"]
    assert data["rollback_result"]["database_decision"]["case"] == 2
    assert ("alembic", "downgrade", "478de3d1f6d0") in world.host.actions_log
    assert world.host.revisions == ["478de3d1f6d0"]


def test_foreign_listener_is_never_killed_and_forces_manual_recovery(tmp_path):
    world = build_world(tmp_path, legacy_current=False)
    authorize(world)
    port = world.profile.backend_port
    original_stop = world.host.stop_task

    def stop_and_squat(name):
        original_stop(name)
        if name == world.profile.tasks.backend and port not in world.host.ports:
            squatter = world.host._spawn(1, "python.exe")
            world.host.ports[port] = squatter.pid
            world.extra["squatter"] = squatter.pid

    world.host.stop_task = stop_and_squat
    data = run(world)
    assert data["status"] == "manual_recovery_required"
    assert world.extra["squatter"] in world.host.processes  # never terminated
    assert world.host.legacy_terminated == []


def test_rebind_that_changes_the_schedule_identity_is_rejected(tmp_path):
    world = build_world(tmp_path)
    authorize(world)
    original = world.host.set_task_action

    def rebind_with_drift(name, action):
        original(name, action)
        if name == world.profile.tasks.sentinel:
            item = world.host.tasks[name]
            item.definition = replace(item.definition, triggers=({"kind": "CalendarTrigger", "enabled": True,
                                                                  "start_boundary": "2026-09-26T18:00:00+05:00",
                                                                  "days_interval": "1", "repetition_interval": None,
                                                                  "repetition_duration": None},))

    world.host.set_task_action = rebind_with_drift
    data = run(world)
    evidence = read_json(state_dir(world) / data["gate_results"]["REBIND_WORKERS"]["evidence"][-1], "X")
    assert evidence["error"]["code"] == "TASK_REBIND_VIOLATION"
    assert data["status"] in ("rolled_back", "manual_recovery_required")


def test_second_controller_is_refused_while_one_holds_the_control_root(tmp_path):
    world = build_world(tmp_path)
    authorize(world)
    with controller_lock(world.profile.control_root):
        with pytest.raises(ControlPlaneError) as caught:
            run(world)
    assert caught.value.code == "CONTROLLER_BUSY"


def test_expired_authorization_is_rejected_before_any_side_effect(tmp_path):
    world = build_world(tmp_path)
    authorize(world, created=world.host.now() - timedelta(hours=5), hours=4)
    with pytest.raises(ControlPlaneError) as caught:
        run(world)
    assert caught.value.code == "AUTHORIZATION_EXPIRED"
    assert not (world.profile.control_root / "releases").exists()


def test_backup_policy_is_required_before_any_switch(tmp_path):
    world = build_world(tmp_path)
    authorize(world)
    data = Controller(request(world, policy=False), world.host, deadlines=FAST, now=world.host.now()).run()
    assert data["status"] == "failed" and data["gate_results"]["BACKUP"]["status"] == "failed"
    assert serving(world, "backend").sha == world.current


def test_explicit_rollback_returns_to_the_recorded_bindings(tmp_path):
    world = build_world(tmp_path)
    authorize(world)
    assert run(world)["status"] == "completed"
    tasks_before = read_json(state_dir(world) / "snapshots" / "tasks-before.json", "X")
    authorize(world, operation="rollback", release_id="R230-rollback-001", candidate=world.current,
              current=world.candidate)
    data = run(world, operation="rollback", release_id="R230-rollback-001", candidate=world.current,
               current=world.candidate)
    assert data["status"] == "completed", data["gate_results"]
    assert data["rollback_plan"]["decision"]["case"] == 1
    for kind in ("backend", "frontend", "sentinel", "notifications"):
        assert task(world, kind).definition.actions[0].evidence() == tasks_before[kind]["actions"][0]
    assert serving(world, "backend").sha == world.current
    assert read_json(world.profile.control_root / "current-release.json", "X")["sha"] == world.current


def test_production_mode_requires_the_typed_authorization_hash(tmp_path):
    world = build_world(tmp_path)
    authorize(world)
    production = Request(operation="release", mode="production", release_id="R230-production-001",
                         candidate_sha=world.candidate, expected_current_sha=world.current,
                         authorization_path=world.authorization, authorization_sha256=None, profile=PRODUCTION,
                         repository=world.repository, backup_policy_path=None)
    existed = PRODUCTION.control_root.exists()
    with pytest.raises(ControlPlaneError) as caught:
        Controller(production, world.host).run()
    assert caught.value.code == "PRODUCTION_AUTHORIZATION_SHA256_REQUIRED"
    wrong = replace(production, authorization_sha256="0" * 64)
    with pytest.raises(ControlPlaneError) as caught:
        Controller(wrong, world.host).run()
    assert caught.value.code in ("AUTHORIZATION_SHA256_MISMATCH", "AUTHORIZATION_DATABASE_MISMATCH")
    assert PRODUCTION.control_root.exists() == existed
    mismatched = replace(production, profile=world.profile)
    with pytest.raises(ControlPlaneError) as caught:
        Controller(mismatched, world.host).run()
    assert caught.value.code == "CONTROLLER_REQUEST_REJECTED"


def test_installed_backup_task_follows_the_release_and_every_worker_rolls_back(tmp_path):
    world = build_world(tmp_path, backup_task=True)
    authorize(world)
    backup_before = world.host.tasks[world.profile.backup_task].definition.actions[0]
    original_run = world.host.run

    def failing_dry_run(argv, **kwargs):
        if "dry-run" in " ".join(str(item) for item in argv):
            return __import__("subprocess").CompletedProcess(argv, 1, "", "dry run failed")
        return original_run(argv, **kwargs)

    world.host.run = failing_dry_run
    data = run(world)
    assert data["status"] == "rolled_back", data["rollback_result"]
    assert data["gate_results"]["REBIND_WORKERS"]["status"] == "passed"
    assert data["gate_results"]["VERIFY_WORKERS"]["status"] == "failed"
    assert world.host.tasks[world.profile.backup_task].definition.actions[0] == backup_before
    for kind in ("sentinel", "notifications"):
        assert str(world.profile.runtime_root / world.current) in task(world, kind).definition.actions[0].arguments             or "scheduler" in task(world, kind).definition.actions[0].arguments


def test_installed_backup_task_is_rebound_to_the_new_release(tmp_path):
    world = build_world(tmp_path, backup_task=True)
    authorize(world)
    assert run(world)["status"] == "completed"
    action = world.host.tasks[world.profile.backup_task].definition.actions[0]
    assert str(world.profile.release_root / world.candidate) in action.command
    assert action.working_directory == str(world.profile.release_root / world.candidate)
    assert "backup scheduled --policy" in action.arguments and world.current not in action.arguments
