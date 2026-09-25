"""Production ownership regressions; no real application or database is started."""
import importlib.util
import json
from pathlib import Path
import socket
import sys

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / "ops/release/Run-AgroSatApplication.py"
spec = importlib.util.spec_from_file_location("task220_application", LAUNCHER)
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app  # dataclasses resolve the module's annotations through sys.modules
spec.loader.exec_module(app)
HELPER = LAUNCHER.parent / "agrosat_process_ownership.py"


def configuration(tmp_path, *, profile="rehearsal"):
    commit = "a" * 40
    release_root, runtime_root = tmp_path / "releases", tmp_path / "runtime"
    release, runtime = release_root / commit, runtime_root / commit / "application"
    runtime.mkdir(parents=True)
    release.mkdir(parents=True)
    manifest = release / "release-manifest.json"
    manifest.write_text(json.dumps({"git_sha": commit}))
    for name in app.RELEASE_ASSETS:
        path = release / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    env = tmp_path / "rehearsal.env"
    env.write_text("# no credentials\n")
    cfg = {"schema_version": 2, "release_commit": commit, "manifest_sha256": app.sha256(manifest),
           "wrapper_sha256": "b" * 64, "ownership_sha256": app.sha256(HELPER), "runtime_directory": str(runtime),
           "profile": profile}
    if profile == "rehearsal":
        cfg["rehearsal"] = {"release_root": str(release_root), "runtime_root": str(runtime_root),
                            "runtime_env_file": str(env), "backend_port": 58300, "frontend_port": 58301,
                            "node_executable": r"C:\Program Files\nodejs\node.exe"}
    return cfg, release_root, runtime_root, release


def test_manifest_tampering_and_unrelated_runtime_fail_closed(tmp_path):
    # Keep the nested fake release below Win32 MAX_PATH even when pytest's
    # canonical TASK run basetemp is already long.
    cfg, releases, runtimes, release = configuration(tmp_path.parent / "m")
    released, _, settings = app.validate(cfg, ownership_helper=HELPER)
    assert released.name == "a" * 40 and settings.profile == "rehearsal" and settings.backend_port == 58300
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    with pytest.raises(ValueError, match="RUNTIME_PATH"):
        app.validate({**cfg, "runtime_directory": str(unrelated)})
    (release / "release-manifest.json").write_text(json.dumps({"git_sha": "c" * 40}))
    with pytest.raises(ValueError, match="MANIFEST_HASH"):
        app.validate(cfg)


def test_production_profile_uses_fixed_roots_and_ports(tmp_path):
    cfg, releases, runtimes, _ = configuration(tmp_path.parent / "p", profile="production")
    production = app.Settings("production", releases, runtimes, tmp_path / "prod.env", 8000, 5173,
                              Path(r"C:\Program Files\nodejs\node.exe"))
    _, _, settings = app.validate(cfg, production=production)
    assert (settings.backend_port, settings.frontend_port) == (8000, 5173)
    assert app.PRODUCTION.release_root == Path(r"C:\AgroSat_releases\PROGRAM_R3")
    with pytest.raises(ValueError, match="KEYSET"):
        app.validate({**cfg, "rehearsal": {}}, production=production)


def test_ownership_helper_is_bound_by_hash(tmp_path):
    cfg, *_ = configuration(tmp_path.parent / "h")
    tampered = tmp_path / "agrosat_process_ownership.py"
    tampered.write_text(HELPER.read_text() + "\n# changed\n")
    with pytest.raises(ValueError, match="OWNERSHIP_HELPER_HASH"):
        app.validate(cfg, ownership_helper=tampered)


def test_legacy_schema_1_configuration_is_rejected(tmp_path):
    cfg, *_ = configuration(tmp_path.parent / "l")
    legacy = {key: cfg[key] for key in ("release_commit", "manifest_sha256", "wrapper_sha256", "runtime_directory")}
    with pytest.raises(ValueError, match="SCHEMA"):
        app.validate(legacy)


@pytest.mark.parametrize("change", [
    {"backend_port": 8000}, {"frontend_port": 5173}, {"backend_port": 58301},
    {"release_root": r"C:\AgroSat_releases\PROGRAM_R3"}, {"runtime_root": r"C:\AgroSat_runtime\PROGRAM_R3"},
    {"runtime_env_file": r"C:\AgroSat\backend\.env"}, {"release_root": "relative\\path"},
])
def test_rehearsal_profile_can_never_target_production(tmp_path, change):
    cfg, *_ = configuration(tmp_path / "r")
    cfg["rehearsal"] = {**cfg["rehearsal"], **change}
    with pytest.raises(ValueError, match="REHEARSAL"):
        app.resolve_settings(cfg)


def test_environment_injection_is_not_forwarded(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "must-not-forward")
    monkeypatch.setenv("PYTHONPATH", "must-not-forward")
    monkeypatch.setenv("NODE_OPTIONS", "must-not-forward")
    monkeypatch.setenv("RELEASE_REVISION", "old-release")
    release = tmp_path / ("a" * 40)
    for component in ("backend", "frontend"):
        env = app.child_environment(component, release, tmp_path / "runtime")
        assert not {"DATABASE_URL", "PYTHONPATH", "NODE_OPTIONS"} & env.keys()
    assert app.child_environment("backend", release, tmp_path)["RELEASE_REVISION"] == release.name
    assert app.child_environment("backend", release, tmp_path)["AGROSAT_RUNTIME_ENV_FILE"] == str(app.PRODUCTION.runtime_env_file)


def test_child_commands_listen_only_on_the_profile_ports(tmp_path):
    settings = app.Settings("rehearsal", tmp_path, tmp_path, tmp_path / "e.env", 58300, 58301, Path("node.exe"))
    backend = app.child_command("backend", tmp_path, settings)
    assert backend[-4:] == ["127.0.0.1", "--port", "58300", "--no-access-log"]
    frontend_env = app.child_environment("frontend", tmp_path, tmp_path, settings)
    assert frontend_env["QUALIFICATION_FRONTEND_PORT"] == "58301" and frontend_env["QUALIFICATION_BACKEND_PORT"] == "58300"


def test_live_listener_is_never_claimed():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        with pytest.raises(RuntimeError, match="ALREADY_OWNED"):
            app.assert_port_free(server.getsockname()[1])


@pytest.mark.skipif(sys.platform != "win32", reason="msvcrt byte-range locks")
def test_ownership_lock_rejects_second_supervisor(tmp_path):
    with app.ownership_lock(tmp_path / "backend.lock"):
        with pytest.raises(OSError, match="APPLICATION_OWNERSHIP_LOCK_HELD"):
            with app.ownership_lock(tmp_path / "backend.lock"):
                pytest.fail("Duplicate supervisor acquired ownership")
