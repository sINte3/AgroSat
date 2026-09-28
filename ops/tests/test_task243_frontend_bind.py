"""TASK_243: the operator-controlled frontend bind address, end to end.

Contract: without ``AGROSAT_FRONTEND_BIND_ADDRESS`` the frontend listens on
127.0.0.1 exactly as before; with it, on exactly that one address. The backend
stays on 127.0.0.1, the API proxy still targets the loopback backend, client
forwarding headers never reach it, and the static root stays contained.

No test opens a socket on a LAN interface. Real listeners use 127.0.0.1 and
127.0.0.2 (loopback on Windows); the LAN value 10.103.25.14 is exercised only
where nothing binds it.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from controlplane import health, network
from controlplane.common import ControlPlaneError, read_json, sha256_file
from controlplane.controller import Controller, Deadlines, Request
from controlplane.tasks import TaskAction
from fakehost import authorize, build_world, definition

OPS = Path(__file__).resolve().parents[1]
SERVER = OPS / "qualification" / "Serve-ProgramR1QualificationFrontend.mjs"
NODE = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
LAN = "10.103.25.14"
SECOND_LOOPBACK = "127.0.0.2"
INDEX = b"<!doctype html><title>TASK243 fixture bundle</title>\n"
MARKER = "TASK243_FIXTURE_OUTSIDE_DIST"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


supervisor = load("task243_supervisor", OPS / "release" / "Run-AgroSatApplication.py")
cli = load("task243_controlplane_cli", OPS / "release" / "Invoke-AgroSatControlPlane.py")


def bindable(address: str) -> bool:
    try:
        with socket.socket() as probe:
            probe.bind((address, 0))
    except OSError:
        return False
    return True


needs_node = pytest.mark.skipif(not Path(NODE).is_file(), reason="node is not installed")
needs_second_loopback = pytest.mark.skipif(not bindable(SECOND_LOOPBACK), reason="127.0.0.2 is not loopback here")
windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows listener table and Job Objects")

# ---------------------------------------------------------------- one validation policy, three implementations

LOOPBACK_VALUES = ["127.0.0.1", SECOND_LOOPBACK, "127.10.20.30"]
LAN_VALUES = [LAN, "10.0.0.1", "172.16.0.5", "172.31.255.254", "192.168.1.10"]
REJECTED_VALUES = [
    "0.0.0.0", "", " 10.103.25.14", "10.103.25.14 ", "10.103.25.14\n", "10.103.25.14:5173", "010.103.25.14",
    "10.103.025.14", "10.103.25", "10.103.25.14.1", "256.1.1.1", "10.103.25.256", "localhost", "frontend-host",
    "frontend.example.invalid", "::", "::1", "[::1]", "::ffff:10.103.25.14", "0x0a.0x67.0x19.0x0e", "167975182",
    "10.103.25.14/24", "10.103.25.14;calc", "10.103.25.14 && calc", "$(calc)", "`calc`", "10.103.25.14|calc",
    "\uff11\uff10.103.25.14", "8.8.8.8", "1.1.1.1", "169.254.1.1", "224.0.0.1", "255.255.255.255",
    "172.32.0.1", "172.15.0.1", "100.64.0.1", "192.0.2.1", "198.18.0.1", "240.0.0.1",
]


def accepted(call) -> bool:
    try:
        call()
    except (ControlPlaneError, ValueError):
        return False
    return True


@pytest.mark.parametrize("value", LOOPBACK_VALUES + LAN_VALUES + REJECTED_VALUES + [None, 10, [LAN], {"a": LAN}])
def test_control_plane_and_supervisor_apply_one_policy(value):
    for production in (True, False):
        profile = "production" if production else "rehearsal"
        expected = value in LOOPBACK_VALUES or (production and value in LAN_VALUES)
        plane = accepted(lambda: network.validate_bind_address(value, production=production))
        launcher = accepted(lambda: supervisor.frontend_bind_address({"frontend_bind_address": value}, profile))
        assert (plane, launcher) == (expected, expected), (value, profile)


def node_environment(**values: str) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key.upper() in supervisor.SAFE_ENV_KEYS}
    environment.update({"QUALIFICATION_FRONTEND_PORT": str(free_port()), "QUALIFICATION_BACKEND_PORT": str(free_port())})
    environment.update(values)
    return environment


@needs_node
@pytest.mark.parametrize("value", LOOPBACK_VALUES + LAN_VALUES + REJECTED_VALUES)
def test_frontend_server_applies_the_same_policy_before_listening(tmp_path, value):
    # The bundle is absent, so an accepted address fails one check later and nothing ever listens.
    result = subprocess.run([NODE, str(SERVER)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=60, env=node_environment(QUALIFICATION_FRONTEND_BIND_ADDRESS=value,
                                                             QUALIFICATION_DIST_ROOT=str(tmp_path / "absent")))
    assert result.returncode != 0
    if value in LOOPBACK_VALUES + LAN_VALUES:
        assert "Production frontend bundle is unavailable" in result.stderr, result.stderr
    else:
        assert "Invalid frontend bind address" in result.stderr, result.stderr


# ---------------------------------------------------------------- supervisor composition

def production_settings(tmp_path: Path):
    return supervisor.Settings("production", tmp_path / "releases", tmp_path / "runtimes", tmp_path / "prod.env",
                               8000, 5173, Path(r"C:\Program Files\nodejs\node.exe"))


def application_config(profile: str = "production", **extra) -> dict:
    return {"schema_version": 2, "release_commit": "a" * 40, "manifest_sha256": "b" * 64, "wrapper_sha256": "c" * 64,
            "ownership_sha256": "d" * 64, "runtime_directory": "unused", "profile": profile, **extra}


def test_without_the_setting_the_frontend_stays_on_loopback(tmp_path):
    settings = supervisor.resolve_settings(application_config(), production_settings(tmp_path))
    assert settings.address("frontend") == settings.address("backend") == "127.0.0.1"
    release = tmp_path / ("a" * 40)
    environment = supervisor.child_environment("frontend", release, tmp_path, settings)
    assert environment["QUALIFICATION_FRONTEND_BIND_ADDRESS"] == "127.0.0.1"
    assert environment["QUALIFICATION_FRONTEND_PORT"] == "5173" and environment["QUALIFICATION_BACKEND_PORT"] == "8000"
    assert supervisor.child_command("frontend", release, settings) == [
        str(settings.node_executable), str(release / "ops/qualification/Serve-ProgramR1QualificationFrontend.mjs")]


def test_explicit_lan_address_moves_only_the_frontend(tmp_path):
    settings = supervisor.resolve_settings(application_config(frontend_bind_address=LAN), production_settings(tmp_path))
    assert settings.frontend_address == LAN and settings.address("frontend") == LAN and settings.port("frontend") == 5173
    release = tmp_path / ("a" * 40)
    frontend = supervisor.child_environment("frontend", release, tmp_path, settings)
    assert frontend["QUALIFICATION_FRONTEND_BIND_ADDRESS"] == LAN and frontend["QUALIFICATION_BACKEND_PORT"] == "8000"
    # The address reaches node only through its environment: never an argument, never the backend.
    assert all(LAN not in item for item in supervisor.child_command("frontend", release, settings))
    assert supervisor.child_command("backend", release, settings)[-5:] == [
        "--host", "127.0.0.1", "--port", "8000", "--no-access-log"]
    backend = supervisor.child_environment("backend", release, tmp_path, settings)
    assert "QUALIFICATION_FRONTEND_BIND_ADDRESS" not in backend and LAN not in backend.values()
    assert settings.address("backend") == "127.0.0.1" and settings.port("backend") == 8000


@pytest.mark.parametrize("value", ["0.0.0.0", "", "localhost", "::", "10.103.25.14:5173", "10.103.25.14 && calc",
                                   "8.8.8.8", None, 10])
def test_supervisor_refuses_a_malformed_wildcard_or_public_address(tmp_path, value):
    with pytest.raises(ValueError, match="APPLICATION_FRONTEND_BIND_ADDRESS_REJECTED"):
        supervisor.resolve_settings(application_config(frontend_bind_address=value), production_settings(tmp_path))


def test_a_rehearsal_frontend_never_leaves_loopback(tmp_path):
    rehearsal = {"release_root": str(tmp_path / "rel"), "runtime_root": str(tmp_path / "rt"),
                 "runtime_env_file": str(tmp_path / "r.env"), "backend_port": 58300, "frontend_port": 58301,
                 "node_executable": r"C:\Program Files\nodejs\node.exe"}
    settings = supervisor.resolve_settings(application_config("rehearsal", rehearsal=rehearsal,
                                                              frontend_bind_address=SECOND_LOOPBACK))
    assert settings.frontend_address == SECOND_LOOPBACK and settings.address("backend") == "127.0.0.1"
    with pytest.raises(ValueError, match="APPLICATION_FRONTEND_BIND_ADDRESS_REJECTED"):
        supervisor.resolve_settings(application_config("rehearsal", rehearsal=rehearsal, frontend_bind_address=LAN))


def test_unknown_configuration_keys_are_still_refused(tmp_path):
    with pytest.raises(ValueError, match="KEYSET"):
        supervisor.resolve_settings(application_config(frontend_host=LAN), production_settings(tmp_path))


def test_an_address_this_host_does_not_own_is_refused_before_start():
    supervisor.assert_address_local("127.0.0.1")
    with pytest.raises(RuntimeError, match="APPLICATION_FRONTEND_BIND_ADDRESS_NOT_LOCAL"):
        supervisor.assert_address_local("192.0.2.1")  # TEST-NET-1 is assigned to no host


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_a_start_at_boot_waits_for_its_address_then_fails_closed(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(supervisor, "time", clock)
    outcomes = iter([False, False, True])  # the NIC's address appears on the third check

    def appearing(address):
        if not next(outcomes):
            raise RuntimeError("APPLICATION_FRONTEND_BIND_ADDRESS_NOT_LOCAL")

    def never(address):
        raise RuntimeError("APPLICATION_FRONTEND_BIND_ADDRESS_NOT_LOCAL")

    monkeypatch.setattr(supervisor, "assert_address_local", appearing)
    assert supervisor.wait_for_local_address(LAN) == 2.0
    monkeypatch.setattr(supervisor, "assert_address_local", never)
    clock.now = 0.0
    with pytest.raises(RuntimeError, match="APPLICATION_FRONTEND_BIND_ADDRESS_NOT_LOCAL"):
        supervisor.wait_for_local_address(LAN)
    assert clock.now == supervisor.ADDRESS_WAIT_SECONDS  # bounded: it gave up, it did not wait forever


# ---------------------------------------------------------------- the operator setting

@pytest.mark.parametrize("production,lines,expected", [
    (True, [], {"address": "127.0.0.1", "source": "default"}),
    (True, [f"AGROSAT_FRONTEND_BIND_ADDRESS={LAN}"], {"address": LAN, "source": "runtime_env_file"}),
    (True, [f'AGROSAT_FRONTEND_BIND_ADDRESS="{LAN}"'], {"address": LAN, "source": "runtime_env_file"}),
    (True, [f"# AGROSAT_FRONTEND_BIND_ADDRESS={LAN}"], {"address": "127.0.0.1", "source": "default"}),
    (True, [f"AGROSAT_FRONTEND_BIND_ADDRESS={LAN}", "AGROSAT_FRONTEND_BIND_ADDRESS=127.0.0.1"],
     {"address": "127.0.0.1", "source": "runtime_env_file"}),
    (False, [f"AGROSAT_FRONTEND_BIND_ADDRESS={SECOND_LOOPBACK}"],
     {"address": SECOND_LOOPBACK, "source": "runtime_env_file"}),
])
def test_setting_is_read_from_the_runtime_environment_file(tmp_path, production, lines, expected):
    env = tmp_path / "runtime.env"
    env.write_text("\n".join(["DATABASE_URL=postgresql://u:p@localhost:5432/agrosat_task243_rel", *lines]) + "\n",
                   encoding="utf-8")
    assert network.configured_bind_address(SimpleNamespace(runtime_env_file=env, production=production)) == expected


@pytest.mark.parametrize("production,value", [(True, "0.0.0.0"), (True, ""), (True, f"{LAN} # pilot"),
                                              (True, "8.8.8.8"), (True, "frontend-host"), (False, LAN)])
def test_an_invalid_setting_is_refused_and_never_echoed(tmp_path, production, value):
    env = tmp_path / "runtime.env"
    env.write_text(f"AGROSAT_FRONTEND_BIND_ADDRESS={value}\n", encoding="utf-8")
    with pytest.raises(ControlPlaneError) as caught:
        network.configured_bind_address(SimpleNamespace(runtime_env_file=env, production=production))
    assert caught.value.code == "FRONTEND_BIND_ADDRESS_REJECTED"
    assert not value or value not in json.dumps(caught.value.evidence())


# ---------------------------------------------------------------- control plane on the in-memory host

FAST = Deadlines(stop_seconds=10, legacy_handoff_seconds=5, listener_seconds=10, health_seconds=10,
                 worker_idle_seconds=10, poll_seconds=0.5)


def run(world, release_id: str, **overrides):
    request = Request(operation="release", mode="rehearsal", release_id=release_id, candidate_sha=world.candidate,
                      expected_current_sha=world.current, authorization_path=world.authorization,
                      authorization_sha256=None, profile=world.profile, repository=world.repository,
                      backup_policy_path=world.policy, fetch=True)
    return Controller(replace(request, **overrides), world.host, deadlines=FAST, now=world.host.now()).run()


def set_setting(world, value: str) -> None:
    with world.profile.runtime_env_file.open("a", encoding="utf-8") as stream:
        stream.write(f"AGROSAT_FRONTEND_BIND_ADDRESS={value}\n")


def gate(world, data, name: str) -> dict:
    directory = world.profile.control_root / "releases" / data["release_id"]
    return read_json(directory / data["gate_results"][name]["evidence"][-1], "X")


def frontend_address(world) -> str | None:
    return world.host.addresses.get(world.profile.frontend_port)


def test_release_materializes_the_setting_and_proves_the_exact_frontend_address(tmp_path):
    world = build_world(tmp_path, legacy_current=False)
    set_setting(world, SECOND_LOOPBACK)
    authorize(world, release_id="R243-release-001")
    data = run(world, "R243-release-001")
    assert data["status"] == "completed", data["gate_results"]
    application = world.profile.runtime_root / world.candidate / "application"
    assert read_json(application / "application-release.json", "X")["frontend_bind_address"] == SECOND_LOOPBACK
    precheck = gate(world, data, "PRECHECK")
    assert precheck["frontend_bind"] == {"address": SECOND_LOOPBACK, "source": "runtime_env_file"}
    assert precheck["previous_health"] == {"backend": True, "frontend": True}
    assert gate(world, data, "MATERIALIZE")["summary"]["frontend_bind_address"] == SECOND_LOOPBACK
    verify = gate(world, data, "VERIFY_FRONTEND")
    assert verify["health"]["pass"] and verify["listener"]["observed"] == [SECOND_LOOPBACK]
    final = gate(world, data, "FINAL_HEALTH")
    assert final["listener_addresses"]["frontend"]["observed"] == [SECOND_LOOPBACK]
    assert final["listener_addresses"]["backend"]["observed"] == ["127.0.0.1"]
    assert read_json(world.profile.control_root / "current-release.json", "X")["frontend_bind_address"] == SECOND_LOOPBACK
    assert world.host.addresses == {world.profile.backend_port: "127.0.0.1", world.profile.frontend_port: SECOND_LOOPBACK}
    # The task is rebound exactly as before: the address lives in the configuration, not in the action.
    action = world.host.tasks[world.profile.tasks.frontend].definition.actions[0]
    assert action.arguments == (f'-B "{application / "Run-AgroSatApplication.py"}" --configuration '
                                f'"{application / "application-release.json"}" --component frontend')
    assert list(gate(world, data, "VALIDATE")["launcher_validate_only"]) == ["backend", "frontend"]


def test_without_the_setting_a_release_keeps_the_frontend_on_loopback(tmp_path):
    world = build_world(tmp_path, legacy_current=False)
    authorize(world, release_id="R243-release-002")
    data = run(world, "R243-release-002")
    assert data["status"] == "completed", data["gate_results"]
    config = read_json(world.profile.runtime_root / world.candidate / "application" / "application-release.json", "X")
    assert config["frontend_bind_address"] == "127.0.0.1"
    assert gate(world, data, "PRECHECK")["frontend_bind"] == {"address": "127.0.0.1", "source": "default"}
    assert gate(world, data, "FINAL_HEALTH")["listener_addresses"]["frontend"]["observed"] == ["127.0.0.1"]
    assert frontend_address(world) == "127.0.0.1"
    assert read_json(world.profile.control_root / "current-release.json", "X")["frontend_bind_address"] == "127.0.0.1"


@pytest.mark.parametrize("value", ["0.0.0.0", LAN, "localhost", "127.0.0.1;calc"])
def test_an_invalid_setting_stops_the_release_before_anything_changes(tmp_path, value):
    world = build_world(tmp_path, legacy_current=False)
    set_setting(world, value)  # a LAN address is refused too: a rehearsal never leaves loopback
    authorize(world, release_id="R243-release-003")
    before = len(world.host.actions_log)
    data = run(world, "R243-release-003")
    assert data["status"] == "failed" and data["rollback_required"] is False
    assert gate(world, data, "PRECHECK")["error"]["code"] == "FRONTEND_BIND_ADDRESS_REJECTED"
    assert world.host.actions_log[before:] == [] and world.host.backups == {}
    assert not (world.profile.runtime_root / world.candidate).exists()
    assert frontend_address(world) == "127.0.0.1" and world.host.serving[world.profile.frontend_port].sha == world.current


def test_a_frontend_on_any_other_address_is_refused_and_rolled_back(tmp_path):
    world = build_world(tmp_path, legacy_current=False)
    set_setting(world, SECOND_LOOPBACK)
    world.host.frontend_bind_override[world.candidate] = "0.0.0.0"  # a launcher that ignored its configuration
    authorize(world, release_id="R243-release-004")
    data = run(world, "R243-release-004")
    assert data["status"] == "rolled_back", data["rollback_result"]
    error = gate(world, data, "VERIFY_FRONTEND")["error"]
    assert error["code"] == "LISTENER_ADDRESS_MISMATCH"
    assert error["facts"] == {"expected": SECOND_LOOPBACK, "observed": ["0.0.0.0"]}
    result = data["rollback_result"]
    assert result["status"] == "rolled_back" and result["previous_health"]["frontend"]["pass"]
    assert result["previous_listeners"]["frontend"]["observed"] == ["127.0.0.1"]
    assert result["previous_listeners"]["backend"]["observed"] == ["127.0.0.1"]
    assert frontend_address(world) == "127.0.0.1" and world.host.serving[world.profile.frontend_port].sha == world.current


def test_explicit_rollback_returns_the_frontend_to_the_address_of_the_release_it_restores(tmp_path):
    world = build_world(tmp_path, legacy_current=False)
    set_setting(world, SECOND_LOOPBACK)
    authorize(world, release_id="R243-release-005")
    assert run(world, "R243-release-005")["status"] == "completed"
    assert frontend_address(world) == SECOND_LOOPBACK
    authorize(world, operation="rollback", release_id="R243-rollback-005", candidate=world.current,
              current=world.candidate)
    data = run(world, "R243-rollback-005", operation="rollback", candidate_sha=world.current,
               expected_current_sha=world.candidate)
    assert data["status"] == "completed", data["gate_results"]
    assert data["facts"]["target"]["frontend_bind_address"] == "127.0.0.1"
    assert gate(world, data, "VERIFY_FRONTEND")["listener"]["observed"] == ["127.0.0.1"]
    assert gate(world, data, "FINAL_HEALTH")["listener_addresses"]["frontend"]["observed"] == ["127.0.0.1"]
    assert frontend_address(world) == "127.0.0.1" and world.host.serving[world.profile.frontend_port].sha == world.current
    assert read_json(world.profile.control_root / "current-release.json", "X")["frontend_bind_address"] == "127.0.0.1"
    # A rollback reads the restored release's own configuration; the operator setting is not consulted.
    assert network.configured_bind_address(world.profile)["address"] == SECOND_LOOPBACK


def test_previous_release_is_probed_and_restored_on_its_own_address(tmp_path):
    world = build_world(tmp_path, legacy_current=False, current_frontend_address=SECOND_LOOPBACK)
    assert frontend_address(world) == SECOND_LOOPBACK
    authorize(world, release_id="R243-release-006")
    original_run = world.host.run

    def failing_dry_run(argv, **kwargs):
        if "dry-run" in " ".join(str(item) for item in argv):
            return subprocess.CompletedProcess(argv, 1, "", "dry run failed")
        return original_run(argv, **kwargs)

    world.host.run = failing_dry_run  # fails VERIFY_WORKERS, after the frontend switched
    data = run(world, "R243-release-006")
    assert data["status"] == "rolled_back", data["rollback_result"]
    assert data["facts"]["previous"]["frontend_bind_address"] == SECOND_LOOPBACK
    assert gate(world, data, "PRECHECK")["previous_health"]["frontend"] is True  # probed where it listens
    assert gate(world, data, "VERIFY_FRONTEND")["listener"]["observed"] == ["127.0.0.1"]  # no setting: loopback
    result = data["rollback_result"]
    assert result["previous_health"]["frontend"]["pass"]
    assert result["previous_listeners"]["frontend"]["observed"] == [SECOND_LOOPBACK]
    assert frontend_address(world) == SECOND_LOOPBACK and world.host.serving[world.profile.frontend_port].sha == world.current


def test_a_resumed_release_keeps_the_address_it_validated(tmp_path):
    world = build_world(tmp_path, legacy_current=False)
    set_setting(world, SECOND_LOOPBACK)
    authorize(world, release_id="R243-release-007")
    world.host.crash_on = f"start:{world.profile.tasks.backend}"
    with pytest.raises(KeyboardInterrupt):
        run(world, "R243-release-007")
    world.host.crash_on = None
    set_setting(world, "127.0.0.3")  # edited mid-release: never picked up by the open release
    data = run(world, "R243-release-007")
    assert data["status"] == "completed", data["gate_results"]
    assert data["gate_results"]["PRECHECK"]["starts"] == 1 and data["gate_results"]["MATERIALIZE"]["starts"] == 1
    assert frontend_address(world) == SECOND_LOOPBACK


def rehearsal_profile(tmp_path: Path, setting: str | None) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    (root / ".agrosat-rehearsal-root.json").write_text(json.dumps({"profile_id": "TASK243"}), encoding="utf-8")
    env = root / "env" / "r.env"
    env.parent.mkdir()
    lines = ["DATABASE_URL=postgresql://u:p@localhost:5432/agrosat_task243_rel"]
    env.write_text("\n".join(lines + ([f"AGROSAT_FRONTEND_BIND_ADDRESS={setting}"] if setting else [])) + "\n",
                   encoding="utf-8")
    document = {"schema_version": 1, "kind": "agrosat_rehearsal_profile", "profile_id": "TASK243",
                "rehearsal_root": str(root), "release_root": str(root / "releases"), "runtime_root": str(root / "runtime"),
                "control_root": str(root / "control"), "runtime_env_file": str(env),
                "database_name": "agrosat_task243_rel", "backend_port": 58440, "frontend_port": 58441,
                "tasks": {"backend": "\\AgroSat_TASK243_Backend", "frontend": "\\AgroSat_TASK243_Frontend",
                          "sentinel": "\\AgroSat_TASK243_SentinelCycle",
                          "notifications": "\\AgroSat_TASK243_OperationalNotifications"},
                "node_executable": r"C:\Program Files\nodejs\node.exe", "pg_bin": r"C:\Program Files\PostgreSQL\16\bin",
                "signing_thumbprints": ["816767BE400FE53327432B12B29FE4B5809CA4CA"],
                "timezone_id": "West Asia Standard Time", "fault_injection": None, "worker_dry_run": False,
                "backup_task": None}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.mark.parametrize("setting,status,expected", [
    (SECOND_LOOPBACK, "PASS", {"address": SECOND_LOOPBACK, "source": "runtime_env_file"}),
    (None, "PASS", {"address": "127.0.0.1", "source": "default"}),
    ("0.0.0.0", "BLOCKED", None),
])
def test_preflight_reports_the_address_a_release_would_materialize(tmp_path, monkeypatch, capsys, setting, status,
                                                                   expected):
    profile = rehearsal_profile(tmp_path, setting)
    created = datetime.now(timezone.utc) - timedelta(minutes=1)
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps({
        "schema_version": 1, "kind": "agrosat_release_authorization", "operation": "release",
        "release_id": "R243-preflight-001", "candidate_sha": "a" * 40, "expected_current_sha": "b" * 40,
        "database_name": "agrosat_task243_rel", "migration": {"authorized": False, "from_revision": None,
                                                              "to_revision": None},
        "database_rollback_strategy": "none", "restore_backup_sha256": None, "created_at": created.isoformat(),
        "expires_at": (created + timedelta(hours=1)).isoformat(), "authorized_by": "TASK_243 test operator"}),
        encoding="utf-8")
    kinds = {"\\AgroSat_TASK243_Backend": "backend", "\\AgroSat_TASK243_Frontend": "frontend",
             "\\AgroSat_TASK243_SentinelCycle": "sentinel", "\\AgroSat_TASK243_OperationalNotifications": "notifications"}
    host = SimpleNamespace(task_definition=lambda name: definition(name, kinds[name], TaskAction("python.exe", "-B x", "C:\\r")))
    monkeypatch.setattr(cli, "platform", lambda: host)
    code = cli.main(["preflight", "--mode", "rehearsal", "--profile", str(profile), "--operation", "release",
                     "--release-id", "R243-preflight-001", "--candidate", "a" * 40, "--expected-current", "b" * 40,
                     "--authorization", str(authorization)])
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["status"] == status and output["mutation_performed"] is False
    if expected is None:
        assert output["frontend_bind"]["error"]["code"] == "FRONTEND_BIND_ADDRESS_REJECTED"
    else:
        assert output["frontend_bind"] == expected


# ---------------------------------------------------------------- health probes and the listener table

def test_health_probes_reach_this_host_only():
    assert health.is_this_host("127.0.0.1") and health.is_this_host("localhost")
    for target in ("0.0.0.0", "192.0.2.1", "224.0.0.1", "example.invalid", "", None):
        assert not health.is_this_host(target), target
    for url in ("http://192.0.2.1:5173/", "http://0.0.0.0:5173/", "https://127.0.0.1:5173/"):
        assert health.http_get(url).error == "target_not_local", url


@needs_second_loopback
def test_health_probe_follows_a_local_frontend_address():
    refused = health.http_get(f"http://{SECOND_LOOPBACK}:{free_port()}/")
    assert refused.status is None and refused.error not in (None, "target_not_local")  # allowed; nothing listens


@windows
@needs_second_loopback
def test_listener_table_reports_every_listening_address():
    from controlplane import winproc
    with socket.socket() as server:
        server.bind((SECOND_LOOPBACK, 0))
        server.listen()
        port = server.getsockname()[1]
        assert winproc.listener_endpoints([port]) == {port: [(SECOND_LOOPBACK, os.getpid())]}
        assert winproc.listeners([port]) == {port: os.getpid()}
    assert winproc.listener_endpoints([port]) == {port: []}


# ---------------------------------------------------------------- the real frontend server on loopback addresses

def free_port() -> int:
    """A port free on both 127.0.0.1 and 127.0.0.2 (when that exists)."""
    for _ in range(50):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if not bindable(SECOND_LOOPBACK) or _bindable_port(SECOND_LOOPBACK, port):
            return port
    raise RuntimeError("no spare port")


def _bindable_port(address: str, port: int) -> bool:
    try:
        with socket.socket() as probe:
            probe.bind((address, port))
    except OSError:
        return False
    return True


def request(address: str, port: int, method: str, path: str, headers: dict | None = None,
            body: bytes | None = None) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(address, port, timeout=15)
    try:
        connection.putrequest(method, path, skip_accept_encoding=True)
        for name, value in (headers or {}).items():
            connection.putheader(name, value)
        if body is not None:
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, response.read()
    finally:
        connection.close()


class RecordingBackend:
    """A stand-in backend on 127.0.0.1 that records what the proxy sends it."""

    def __init__(self):
        self.requests: list[dict] = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                recorder.record(self)

            def do_POST(self):
                recorder.record(self)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def record(self, handler) -> None:
        length = int(handler.headers.get("content-length") or 0)
        self.requests.append({"path": handler.path, "peer": handler.client_address[0],
                              "headers": {key.lower(): value for key, value in handler.headers.items()},
                              "body": handler.rfile.read(length) if length else b""})
        payload = json.dumps({"status": "alive", "release_revision": "f" * 40}).encode()
        handler.send_response(200)
        handler.send_header("content-type", "application/json")
        handler.send_header("content-length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class FrontendServer:
    """The release frontend server, started the way the supervisor starts it."""

    def __init__(self, dist: Path, backend_port: int, address: str | None):
        self.port = free_port()
        values = {"QUALIFICATION_FRONTEND_PORT": str(self.port), "QUALIFICATION_BACKEND_PORT": str(backend_port),
                  "QUALIFICATION_DIST_ROOT": str(dist)}
        if address is not None:
            values["QUALIFICATION_FRONTEND_BIND_ADDRESS"] = address
        self.process = subprocess.Popen([NODE, str(SERVER)], env=node_environment(**values), stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        line = self.process.stdout.readline()
        if not line:
            raise AssertionError(self.process.stderr.read())
        self.ready = json.loads(line)
        threading.Thread(target=self.process.stdout.read, daemon=True).start()  # keep draining the proxy log

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.process.kill()
        self.process.wait(timeout=30)


@pytest.fixture
def release_tree(tmp_path):
    release = tmp_path / "rel"
    dist = release / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_bytes(INDEX)
    (dist / "assets" / "app-243.js").write_bytes(b"console.log('fixture');\n")
    (dist / "sw.js").write_bytes(b"// fixture service worker\n")
    for relative in ("frontend/package.json", "release-manifest.json", "backend/main.py", "backend/.env", ".env",
                     ".git/config", "ops/qualification/Serve-ProgramR1QualificationFrontend.mjs"):
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{MARKER} {relative}\n", encoding="utf-8")
    backend = RecordingBackend()
    yield SimpleNamespace(release=release, dist=dist, backend=backend)
    backend.close()


@needs_node
def test_default_frontend_listens_on_loopback_only(release_tree):
    with FrontendServer(release_tree.dist, release_tree.backend.port, None) as frontend:
        assert frontend.ready["address"] == "127.0.0.1" and frontend.ready["backendAddress"] == "127.0.0.1"
        assert request("127.0.0.1", frontend.port, "GET", "/")[::2] == (200, INDEX)
        if sys.platform == "win32":
            from controlplane import winproc
            assert winproc.listener_endpoints([frontend.port]) == {frontend.port: [("127.0.0.1", frontend.process.pid)]}


@needs_node
@needs_second_loopback
def test_explicit_address_is_the_only_listener_and_serves_the_release_bundle(release_tree):
    with FrontendServer(release_tree.dist, release_tree.backend.port, SECOND_LOOPBACK) as frontend:
        assert frontend.ready["address"] == SECOND_LOOPBACK and frontend.ready["backendAddress"] == "127.0.0.1"
        if sys.platform == "win32":
            from controlplane import winproc
            assert winproc.listener_endpoints([frontend.port]) == {
                frontend.port: [(SECOND_LOOPBACK, frontend.process.pid)]}
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", frontend.port), timeout=2).close()
        status, headers, body = request(SECOND_LOOPBACK, frontend.port, "GET", "/")
        assert (status, body, headers["cache-control"]) == (200, INDEX, "no-store")
        status, headers, body = request(SECOND_LOOPBACK, frontend.port, "GET", "/assets/app-243.js")
        assert status == 200 and body == b"console.log('fixture');\n"
        assert headers["cache-control"] == "public, max-age=31536000, immutable"
        assert request(SECOND_LOOPBACK, frontend.port, "GET", "/operational-center/cases/42")[::2] == (200, INDEX)
        assert request(SECOND_LOOPBACK, frontend.port, "POST", "/", body=b"x")[0] == 405
        # The control plane's own frontend contract, over real sockets, on the configured address.
        contract = health.probe_frontend(frontend.port, hashlib.sha256(INDEX).hexdigest(), "f" * 40,
                                         address=SECOND_LOOPBACK)
        assert contract["pass"], contract
        assert not health.probe_frontend(frontend.port, hashlib.sha256(INDEX).hexdigest(), "f" * 40)["pass"]


@needs_node
@needs_second_loopback
def test_api_proxy_targets_the_loopback_backend_and_drops_client_forwarding_headers(release_tree):
    backend = release_tree.backend
    spoofed = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "evil.example",
               "X-Forwarded-Port": "443", "X-Real-IP": "203.0.113.8", "Forwarded": "for=203.0.113.9;proto=https",
               "True-Client-IP": "203.0.113.10", "X-Client-IP": "203.0.113.11", "CF-Connecting-IP": "203.0.113.12",
               "X-Cluster-Client-IP": "203.0.113.13", "Fastly-Client-IP": "203.0.113.14",
               "Authorization": "Bearer task243-fixture", "X-Request-Id": "task243"}
    with FrontendServer(release_tree.dist, backend.port, SECOND_LOOPBACK) as frontend:
        status, _, body = request(SECOND_LOOPBACK, frontend.port, "GET", "/api/fields?limit=5", headers=spoofed)
        assert status == 200 and json.loads(body)["release_revision"] == "f" * 40
        seen = backend.requests[-1]
        assert seen["path"] == "/api/fields?limit=5" and seen["peer"] == "127.0.0.1"
        assert seen["headers"]["host"] == f"127.0.0.1:{backend.port}"
        assert seen["headers"]["authorization"] == "Bearer task243-fixture" and seen["headers"]["x-request-id"] == "task243"
        assert not [name for name in seen["headers"] if name.startswith("x-forwarded-") or name in (
            "forwarded", "x-real-ip", "true-client-ip", "x-client-ip", "cf-connecting-ip", "x-cluster-client-ip",
            "fastly-client-ip")]
        assert "203.0.113." not in json.dumps(seen["headers"])
        status, _, _ = request(SECOND_LOOPBACK, frontend.port, "POST", "/api/fields/import", body=b"note=task243",
                               headers={"Content-Type": "application/x-www-form-urlencoded",
                                        "X-Forwarded-For": "203.0.113.7"})
        assert status == 200 and backend.requests[-1]["body"] == b"note=task243"
        assert "x-forwarded-for" not in backend.requests[-1]["headers"]
        status, _, body = request(SECOND_LOOPBACK, frontend.port, "GET", "/health/live")
        assert status == 200 and backend.requests[-1]["path"] == "/health/live"


# TASK_242's containment corpus, plus malformed and NUL escapes.
CONTAINMENT_PROBES = [
    "/../package.json", "/../../release-manifest.json", "/%2e%2e/package.json", "/%2e%2e%2fpackage.json",
    "/..%2f..%2frelease-manifest.json", "/..%5cpackage.json", "/..%5c..%5crelease-manifest.json",
    "/%2e%2e%5c%2e%2e%5cbackend%5cmain.py", "/%252e%252e%252fpackage.json", "/assets/../../package.json",
    "/C:/AgroSat/backend/.env", "/C:%5CAgroSat%5Cbackend%5C.env", "/%5c%5c127.0.0.1%5cc$%5cWindows%5cwin.ini",
    "/.env", "/backend/.env", "/.git/config", "/assets/", "/assets", "/index.html::$DATA",
    "/../ops/qualification/Serve-ProgramR1QualificationFrontend.mjs", "/openapi.json",
    "/..%2f..%2fbackend%2f.env", "/%2e%2e%2f%2e%2e%2f.env", "/..%5c..%5c.env", "/%00", "/index.html%00.js",
    "/%E0%A4%A", "//127.0.0.1/../.env",
]


@needs_node
@needs_second_loopback
def test_static_root_stays_contained_on_the_explicit_address(release_tree):
    with FrontendServer(release_tree.dist, release_tree.backend.port, SECOND_LOOPBACK) as frontend:
        for path in CONTAINMENT_PROBES:
            status, _, body = request(SECOND_LOOPBACK, frontend.port, "GET", path)
            assert body == INDEX or 400 <= status < 500, (path, status)
            assert MARKER.encode() not in body and b"Index of" not in body, path
        assert request(SECOND_LOOPBACK, frontend.port, "GET", "/sw.js")[::2] == (200, b"// fixture service worker\n")
    assert not release_tree.backend.requests  # no probe was proxied


# ---------------------------------------------------------------- the real supervisor, node and Job Object

def command_line(pid: int) -> str:
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
                            capture_output=True, text=True, errors="replace", timeout=120)
    return result.stdout.strip()


def wait_for(predicate, seconds: float):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    return predicate()


@windows
@needs_node
@needs_second_loopback
def test_supervisor_owns_one_node_listener_on_exactly_the_configured_address(tmp_path):
    from controlplane import winproc
    sha = "e" * 40
    release_root, runtime_root = tmp_path / "rel", tmp_path / "rt"
    release = release_root / sha
    for relative in supervisor.RELEASE_ASSETS:
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    shutil.copyfile(SERVER, release / "ops/qualification/Serve-ProgramR1QualificationFrontend.mjs")
    (release / "frontend/dist/index.html").write_bytes(INDEX)
    manifest = release / "release-manifest.json"
    manifest.write_text(json.dumps({"git_sha": sha}), encoding="utf-8")
    application = runtime_root / sha / "application"
    application.mkdir(parents=True)
    for name in ("Run-AgroSatApplication.py", "agrosat_process_ownership.py"):
        shutil.copyfile(OPS / "release" / name, application / name)
    env_file = tmp_path / "rehearsal.env"
    env_file.write_text("# TASK_243 supervisor test: no credentials\n", encoding="utf-8")
    port = free_port()
    configuration = application / "application-release.json"
    configuration.write_text(json.dumps({
        "schema_version": 2, "release_commit": sha, "manifest_sha256": sha256_file(manifest),
        "wrapper_sha256": sha256_file(application / "Run-AgroSatApplication.py"),
        "ownership_sha256": sha256_file(application / "agrosat_process_ownership.py"),
        "runtime_directory": str(application.resolve()), "profile": "rehearsal",
        "frontend_bind_address": SECOND_LOOPBACK,
        "rehearsal": {"release_root": str(release_root.resolve()), "runtime_root": str(runtime_root.resolve()),
                      "runtime_env_file": str(env_file), "backend_port": free_port(), "frontend_port": port,
                      "node_executable": NODE}}), encoding="utf-8")
    command = [sys.executable, "-B", str(application / "Run-AgroSatApplication.py"), "--configuration",
               str(configuration), "--component", "frontend"]
    validated = subprocess.run(command + ["--validate-only"], capture_output=True, text=True, timeout=120)
    assert json.loads(validated.stdout.strip().splitlines()[-1]) == {
        "status": "PASS", "release": sha, "component": "frontend", "profile": "rehearsal", "port": port,
        "address": SECOND_LOOPBACK}
    expected_command = subprocess.list2cmdline(
        [NODE, str(release.resolve() / "ops/qualification/Serve-ProgramR1QualificationFrontend.mjs")])
    lifecycle = application / "frontend-lifecycle.jsonl"

    def events() -> list[dict]:
        if not lifecycle.exists():
            return []
        complete = lifecycle.read_text(encoding="utf-8").split("\n")[:-1]  # a line still being written is skipped
        return [json.loads(line) for line in complete if line.strip()]

    for cycle in (1, 2):  # start, prove and stop; then the same again: the restart lifecycle is unchanged
        owner = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            endpoints = wait_for(lambda: winproc.listener_endpoints([port])[port], 90)
            assert len(endpoints) == 1 and endpoints[0][0] == SECOND_LOOPBACK, endpoints  # one socket, exact address
            node_pid = endpoints[0][1]
            tree = winproc.lineage(owner.pid)
            assert [item.name.lower() for item in tree if item.pid == node_pid] == ["node.exe"]
            assert winproc.in_any_job(node_pid) is True
            assert command_line(node_pid) == expected_command  # the address is not on the command line
            assert request(SECOND_LOOPBACK, port, "GET", "/")[::2] == (200, INDEX)
            with pytest.raises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=2).close()
            assert wait_for(lambda: [item["status"] for item in events()].count("listening") == cycle, 30)
        finally:
            owner.kill()  # TerminateProcess, as Task Scheduler's stop does
            owner.wait(timeout=60)
        assert wait_for(lambda: not winproc.alive(tree), 30), "the supervised tree outlived its supervisor"
        assert wait_for(lambda: winproc.listener_endpoints([port])[port] == [], 30), "a listener survived the stop"
    recorded = events()
    assert [item["status"] for item in recorded] == ["started", "listening"] * 2
    assert all(item["address"] == SECOND_LOOPBACK and item["port"] == port for item in recorded)
