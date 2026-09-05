"""Production ownership regressions; no real application or database is started."""
import importlib.util
import json
from pathlib import Path
import socket

import pytest

spec = importlib.util.spec_from_file_location("task220_application", Path(__file__).resolve().parents[2] / "ops/release/Run-AgroSatApplication.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def configuration(tmp_path):
    commit = "a" * 40
    release_root, runtime_root = tmp_path / "releases", tmp_path / "runtime"
    release, runtime = release_root / commit, runtime_root / commit / "application"
    runtime.mkdir(parents=True)
    release.mkdir(parents=True)
    manifest = release / "release-manifest.json"
    manifest.write_text(json.dumps({"git_sha": commit}))
    for name in ("backend/venv/Scripts/python.exe", "backend/main.py", "frontend/dist/index.html", "ops/qualification/Serve-ProgramR1QualificationFrontend.mjs"):
        path = release / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    cfg = {"release_commit": commit, "manifest_sha256": app.sha256(manifest), "wrapper_sha256": "b" * 64, "runtime_directory": str(runtime)}
    return cfg, release_root, runtime_root, release


def test_manifest_tampering_and_unrelated_runtime_fail_closed(tmp_path):
    cfg, releases, runtimes, release = configuration(tmp_path)
    app.validate(cfg, release_root=releases, runtime_root=runtimes)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    with pytest.raises(ValueError, match="RUNTIME_PATH"):
        app.validate({**cfg, "runtime_directory": str(unrelated)}, release_root=releases, runtime_root=runtimes)
    (release / "release-manifest.json").write_text(json.dumps({"git_sha": "c" * 40}))
    with pytest.raises(ValueError, match="MANIFEST_HASH"):
        app.validate(cfg, release_root=releases, runtime_root=runtimes)


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


def test_live_listener_is_never_claimed():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        with pytest.raises(RuntimeError, match="ALREADY_OWNED"):
            app.assert_port_free(server.getsockname()[1])


def test_ownership_lock_rejects_second_supervisor(tmp_path):
    with app.ownership_lock(tmp_path / "backend.lock"):
        with pytest.raises(OSError):
            with app.ownership_lock(tmp_path / "backend.lock"):
                pytest.fail("Duplicate supervisor acquired ownership")
