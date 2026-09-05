"""Manifest-bound application ownership for the existing Windows application tasks.

Uses the accepted application/production-frontend entry points. Never collects or
migrates. No credentials are accepted as arguments or persisted in lifecycle output.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

RELEASE_ROOT = Path(r"C:\AgroSat_releases\PROGRAM_R3")
RUNTIME_ROOT = Path(r"C:\AgroSat_runtime\PROGRAM_R3")
RUNTIME_ENV = Path(r"C:\AgroSat\backend\.env")
CONFIG_KEYS = {"release_commit", "manifest_sha256", "wrapper_sha256", "runtime_directory"}
SAFE_ENV_KEYS = {"COMSPEC", "PATH", "PATHEXT", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate(config: dict, *, release_root: Path = RELEASE_ROOT,
             runtime_root: Path = RUNTIME_ROOT, wrapper: Path | None = None) -> tuple[Path, Path]:
    if set(config) != CONFIG_KEYS:
        raise ValueError("APPLICATION_CONFIGURATION_KEYSET_REJECTED")
    commit = config["release_commit"]
    if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("APPLICATION_RELEASE_IDENTITY_REJECTED")
    for name in ("manifest_sha256", "wrapper_sha256"):
        if not isinstance(config[name], str) or not re.fullmatch(r"[a-f0-9]{64}", config[name]):
            raise ValueError("APPLICATION_HASH_IDENTITY_REJECTED")
    release = (release_root / commit).resolve(strict=True)
    if release.parent != release_root.resolve(strict=True):
        raise ValueError("APPLICATION_RELEASE_PATH_REJECTED")
    runtime = Path(config["runtime_directory"]).resolve(strict=True)
    if runtime != (runtime_root / commit / "application").resolve(strict=True):
        raise ValueError("APPLICATION_RUNTIME_PATH_REJECTED")
    manifest = release / "release-manifest.json"
    if sha256(manifest) != config["manifest_sha256"]:
        raise ValueError("APPLICATION_MANIFEST_HASH_REJECTED")
    if json.loads(manifest.read_text(encoding="utf-8-sig")).get("git_sha") != commit:
        raise ValueError("APPLICATION_MANIFEST_COMMIT_REJECTED")
    if wrapper is not None and sha256(wrapper) != config["wrapper_sha256"]:
        raise ValueError("APPLICATION_WRAPPER_HASH_REJECTED")
    for relative in ("backend/venv/Scripts/python.exe", "backend/main.py",
                     "frontend/dist/index.html", "ops/qualification/Serve-ProgramR1QualificationFrontend.mjs"):
        if not (release / relative).is_file():
            raise ValueError("APPLICATION_RELEASE_ASSET_MISSING")
    return release, runtime


def child_environment(component: str, release: Path, runtime: Path) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key.upper() in SAFE_ENV_KEYS}
    if component == "backend":
        environment.update({
            "AGROSAT_RUNTIME_ENV_FILE": str(RUNTIME_ENV),
            "ENVIRONMENT": "production", "RELEASE_REVISION": release.name,
            "DEBUG": "false", "PUBLIC_REGISTRATION_ENABLED": "false",
            "WIALON_ENABLED": "false", "TELEGRAM_NOTIFICATIONS_ENABLED": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
            "COLLECTOR_STATUS_DIRECTORY": str(runtime.parent / "collector/runs"),
        })
    elif component == "frontend":
        environment.update({"QUALIFICATION_FRONTEND_PORT": "5173",
                            "QUALIFICATION_BACKEND_PORT": "8000",
                            "QUALIFICATION_DIST_ROOT": str(release / "frontend/dist")})
    else:
        raise ValueError("APPLICATION_COMPONENT_REJECTED")
    return environment


@contextmanager
def ownership_lock(path: Path):
    import msvcrt
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def assert_port_free(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(2)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError("APPLICATION_LISTENER_ALREADY_OWNED")


def event(runtime: Path, component: str, **fields) -> None:
    value = {"component": component, "supervisor_pid": os.getpid(),
             "time": datetime.now(timezone.utc).isoformat(), **fields}
    with (runtime / f"{component}-lifecycle.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--component", choices=("backend", "frontend"), required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.configuration.read_text(encoding="utf-8-sig"))
    release, runtime = validate(config, wrapper=Path(__file__))
    if not RUNTIME_ENV.is_file():
        raise RuntimeError("APPLICATION_PROTECTED_RUNTIME_MISSING")
    if args.validate_only:
        print(json.dumps({"status": "PASS", "release": release.name, "component": args.component}))
        return 0
    component = args.component
    with ownership_lock(runtime / f"{component}.lock"):
        assert_port_free(8000 if component == "backend" else 5173)
        command = ([str(release / "backend/venv/Scripts/python.exe"), "-m", "uvicorn", "main:app",
                    "--host", "127.0.0.1", "--port", "8000", "--no-access-log"] if component == "backend"
                   else [r"C:\Program Files\nodejs\node.exe", str(release / "ops/qualification/Serve-ProgramR1QualificationFrontend.mjs")])
        environment = child_environment(component, release, runtime)
        child = subprocess.Popen(command, cwd=release / ("backend" if component == "backend" else "frontend"),
                                 env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        environment.clear()
        event(runtime, component, status="started", child_pid=child.pid, release=release.name)
        code = child.wait()
        event(runtime, component, status="exited", child_pid=child.pid, exit_code=code, release=release.name)
        return code if code != 0 else 1  # A persistent application's exit must trigger bounded task recovery.


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__, "details_suppressed": True}))
        raise SystemExit(70)
