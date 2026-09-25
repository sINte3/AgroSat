"""Manifest-bound application supervisor for the AgroSat application tasks.

One supervisor runs one component (backend or frontend) of one immutable
release. It never collects, migrates or reads credentials: the backend reads
its own runtime environment file, whose path is the only secret-adjacent value
passed on.

Process ownership (TASK_230): before any child starts, the supervisor places
itself in a Windows Job Object that kills every member when its only handle
closes and allows no breakaway (``agrosat_process_ownership``). uvicorn, node
and everything they start are members. When the supervisor ends, for any
reason, the kernel terminates the whole tree and its listeners go with it.

Configuration (``application-release.json``, schema 2) binds, by SHA-256, the
release manifest, this file and the ownership helper next to it. The
``production`` profile uses fixed production roots and ports; the
``rehearsal`` profile names its own roots and spare ports and is refused if
any of them is a production value.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import ntpath
import os
from pathlib import Path, PureWindowsPath
import re
import socket
import subprocess
import sys
import time

SCHEMA_VERSION = 2
OWNERSHIP_HELPER = "agrosat_process_ownership.py"
BASE_KEYS = frozenset({
    "schema_version", "release_commit", "manifest_sha256", "wrapper_sha256",
    "ownership_sha256", "runtime_directory", "profile",
})
REHEARSAL_KEYS = frozenset({
    "release_root", "runtime_root", "runtime_env_file", "backend_port",
    "frontend_port", "node_executable",
})
SAFE_ENV_KEYS = {"COMSPEC", "PATH", "PATHEXT", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"}
RELEASE_ASSETS = (
    "backend/venv/Scripts/python.exe", "backend/main.py", "frontend/dist/index.html",
    "ops/qualification/Serve-ProgramR1QualificationFrontend.mjs",
)
LISTENER_WAIT_SECONDS = 180
ERROR_CODE = re.compile(r"[A-Za-z0-9_:=-]{3,120}")


@dataclass(frozen=True)
class Settings:
    """Where one profile's releases, runtimes, secrets file and listeners are."""

    profile: str
    release_root: Path
    runtime_root: Path
    runtime_env_file: Path
    backend_port: int
    frontend_port: int
    node_executable: Path

    def port(self, component: str) -> int:
        return self.backend_port if component == "backend" else self.frontend_port


PRODUCTION = Settings(
    profile="production",
    release_root=Path(r"C:\AgroSat_releases\PROGRAM_R3"),
    runtime_root=Path(r"C:\AgroSat_runtime\PROGRAM_R3"),
    runtime_env_file=Path(r"C:\AgroSat\backend\.env"),
    backend_port=8000,
    frontend_port=5173,
    node_executable=Path(r"C:\Program Files\nodejs\node.exe"),
)
# Paths a rehearsal may never live in, and listeners it may never claim.
PRODUCTION_PATH_ROOTS = (r"C:\AgroSat", r"C:\AgroSat_releases", r"C:\AgroSat_runtime")
PRODUCTION_PORTS = frozenset({PRODUCTION.backend_port, PRODUCTION.frontend_port})


class LockHeld(OSError):
    """Another supervisor already owns this component."""


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _normalized(path: str) -> str:
    return ntpath.normpath(path).replace("\\", "/").casefold().rstrip("/")


def targets_production(path: str) -> bool:
    """True when ``path`` is, or is inside, a production root (literally or resolved)."""
    candidates = {_normalized(path)}
    if sys.platform == "win32":
        candidates.add(_normalized(str(Path(path).resolve(strict=False))))
    for root in PRODUCTION_PATH_ROOTS:
        base = _normalized(root)
        if any(item == base or item.startswith(base + "/") for item in candidates):
            return True
    return False


def _rehearsal_port(value: object) -> int:
    if type(value) is not int or not 1024 <= value <= 65535 or value in PRODUCTION_PORTS:
        raise ValueError("APPLICATION_REHEARSAL_PORT_REJECTED")
    return value


def resolve_settings(config: dict, production: Settings = PRODUCTION) -> Settings:
    profile = config.get("profile")
    if profile == "production":
        if set(config) != BASE_KEYS:
            raise ValueError("APPLICATION_CONFIGURATION_KEYSET_REJECTED")
        return production
    if profile != "rehearsal" or set(config) != BASE_KEYS | {"rehearsal"}:
        raise ValueError("APPLICATION_CONFIGURATION_KEYSET_REJECTED")
    block = config["rehearsal"]
    if not isinstance(block, dict) or set(block) != REHEARSAL_KEYS:
        raise ValueError("APPLICATION_REHEARSAL_KEYSET_REJECTED")
    paths = {}
    for name in ("release_root", "runtime_root", "runtime_env_file", "node_executable"):
        value = block[name]
        if not isinstance(value, str) or not PureWindowsPath(value).is_absolute():
            raise ValueError("APPLICATION_REHEARSAL_PATH_REJECTED")
        paths[name] = value
    for name in ("release_root", "runtime_root", "runtime_env_file"):
        if targets_production(paths[name]):
            raise ValueError("APPLICATION_REHEARSAL_TARGETS_PRODUCTION")
    backend = _rehearsal_port(block["backend_port"])
    frontend = _rehearsal_port(block["frontend_port"])
    if backend == frontend:
        raise ValueError("APPLICATION_REHEARSAL_PORT_REJECTED")
    return Settings("rehearsal", Path(paths["release_root"]), Path(paths["runtime_root"]),
                    Path(paths["runtime_env_file"]), backend, frontend,
                    Path(paths["node_executable"]))


def validate(config: dict, *, production: Settings = PRODUCTION, wrapper: Path | None = None,
             ownership_helper: Path | None = None) -> tuple[Path, Path, Settings]:
    """Check the whole identity chain; return the release, runtime and settings."""
    if not isinstance(config, dict) or config.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("APPLICATION_CONFIGURATION_SCHEMA_REJECTED")
    settings = resolve_settings(config, production)
    commit = config["release_commit"]
    if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("APPLICATION_RELEASE_IDENTITY_REJECTED")
    for name in ("manifest_sha256", "wrapper_sha256", "ownership_sha256"):
        if not isinstance(config[name], str) or not re.fullmatch(r"[a-f0-9]{64}", config[name]):
            raise ValueError("APPLICATION_HASH_IDENTITY_REJECTED")
    if not isinstance(config["runtime_directory"], str):
        raise ValueError("APPLICATION_RUNTIME_PATH_REJECTED")
    release = (settings.release_root / commit).resolve(strict=True)
    if release.parent != settings.release_root.resolve(strict=True):
        raise ValueError("APPLICATION_RELEASE_PATH_REJECTED")
    runtime = Path(config["runtime_directory"]).resolve(strict=True)
    if runtime != (settings.runtime_root / commit / "application").resolve(strict=True):
        raise ValueError("APPLICATION_RUNTIME_PATH_REJECTED")
    manifest = release / "release-manifest.json"
    if sha256(manifest) != config["manifest_sha256"]:
        raise ValueError("APPLICATION_MANIFEST_HASH_REJECTED")
    if json.loads(manifest.read_text(encoding="utf-8-sig")).get("git_sha") != commit:
        raise ValueError("APPLICATION_MANIFEST_COMMIT_REJECTED")
    if wrapper is not None and sha256(wrapper) != config["wrapper_sha256"]:
        raise ValueError("APPLICATION_WRAPPER_HASH_REJECTED")
    if ownership_helper is not None and (
        not ownership_helper.is_file() or sha256(ownership_helper) != config["ownership_sha256"]
    ):
        raise ValueError("APPLICATION_OWNERSHIP_HELPER_HASH_REJECTED")
    for relative in RELEASE_ASSETS:
        if not (release / relative).is_file():
            raise ValueError("APPLICATION_RELEASE_ASSET_MISSING")
    return release, runtime, settings


def child_environment(component: str, release: Path, runtime: Path,
                      settings: Settings = PRODUCTION) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key.upper() in SAFE_ENV_KEYS}
    if component == "backend":
        environment.update({
            "AGROSAT_RUNTIME_ENV_FILE": str(settings.runtime_env_file),
            "ENVIRONMENT": "production", "RELEASE_REVISION": release.name,
            "DEBUG": "false", "PUBLIC_REGISTRATION_ENABLED": "false",
            "WIALON_ENABLED": "false", "TELEGRAM_NOTIFICATIONS_ENABLED": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
            "COLLECTOR_STATUS_DIRECTORY": str(runtime.parent / "collector/runs"),
        })
    elif component == "frontend":
        environment.update({"QUALIFICATION_FRONTEND_PORT": str(settings.frontend_port),
                            "QUALIFICATION_BACKEND_PORT": str(settings.backend_port),
                            "QUALIFICATION_DIST_ROOT": str(release / "frontend/dist")})
    else:
        raise ValueError("APPLICATION_COMPONENT_REJECTED")
    return environment


def child_command(component: str, release: Path, settings: Settings = PRODUCTION) -> list[str]:
    if component == "backend":
        return [str(release / "backend/venv/Scripts/python.exe"), "-m", "uvicorn", "main:app",
                "--host", "127.0.0.1", "--port", str(settings.backend_port), "--no-access-log"]
    if component == "frontend":
        return [str(settings.node_executable),
                str(release / "ops/qualification/Serve-ProgramR1QualificationFrontend.mjs")]
    raise ValueError("APPLICATION_COMPONENT_REJECTED")


@contextmanager
def ownership_lock(path: Path):
    import msvcrt
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise LockHeld("APPLICATION_OWNERSHIP_LOCK_HELD") from None
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def assert_port_free(port: int) -> None:
    if listening(port):
        raise RuntimeError("APPLICATION_LISTENER_ALREADY_OWNED")


def event(runtime: Path, component: str, **fields) -> None:
    value = {"component": component, "supervisor_pid": os.getpid(),
             "time": datetime.now(timezone.utc).isoformat(), **fields}
    with (runtime / f"{component}-lifecycle.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value) + "\n")


def load_ownership_module(path: Path):
    """Import the helper from the exact file whose hash was just verified."""
    spec = importlib.util.spec_from_file_location("agrosat_process_ownership", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--component", choices=("backend", "frontend"), required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.configuration.read_text(encoding="utf-8-sig"))
    helper = Path(__file__).resolve().parent / OWNERSHIP_HELPER
    release, runtime, settings = validate(config, wrapper=Path(__file__), ownership_helper=helper)
    if not settings.runtime_env_file.is_file():
        raise RuntimeError("APPLICATION_PROTECTED_RUNTIME_MISSING")
    component = args.component
    if component == "frontend" and not settings.node_executable.is_file():
        raise RuntimeError("APPLICATION_NODE_RUNTIME_MISSING")
    port = settings.port(component)
    if args.validate_only:
        print(json.dumps({"status": "PASS", "release": release.name, "component": component,
                          "profile": settings.profile, "port": port}))
        return 0
    ownership_module = load_ownership_module(helper)
    with ownership_lock(runtime / f"{component}.lock"):
        assert_port_free(port)
        # Ownership before any child: from here on every descendant is a member.
        ownership = ownership_module.ProcessTreeOwnership.adopt_current_process()
        environment = child_environment(component, release, runtime, settings)
        child = subprocess.Popen(child_command(component, release, settings),
                                 cwd=release / ("backend" if component == "backend" else "frontend"),
                                 env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        environment.clear()
        event(runtime, component, status="started", child_pid=child.pid, release=release.name,
              profile=settings.profile, port=port, ownership=ownership.evidence())
        deadline = time.monotonic() + LISTENER_WAIT_SECONDS
        while child.poll() is None and time.monotonic() < deadline:
            if listening(port):
                event(runtime, component, status="listening", child_pid=child.pid,
                      release=release.name, port=port, ownership=ownership.evidence())
                break
            time.sleep(0.5)
        code = child.wait()
        event(runtime, component, status="exited", child_pid=child.pid, exit_code=code,
              release=release.name, ownership=ownership.evidence())
        # A persistent application's exit is a failure. Returning ends this
        # process, which closes the job's only handle and so terminates
        # anything the child left behind.
        return code if code != 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        message = str(exc)
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__,
                          "error_code": message if ERROR_CODE.fullmatch(message) else None,
                          "details_suppressed": True}))
        raise SystemExit(70)
