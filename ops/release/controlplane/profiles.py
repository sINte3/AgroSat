"""Target profiles: the fixed production identity and guarded rehearsal targets.

Production is not configurable. Every production root, port, task name and the
database name are constants here; a mistyped file cannot redirect a
production run anywhere else, and a rehearsal cannot name any of them.

A rehearsal profile is an explicit JSON document. It is accepted only when:

* its ``rehearsal_root`` carries the marker file ``.agrosat-rehearsal-root.json``
  naming the same ``profile_id``;
* every root lies inside ``rehearsal_root`` and outside every production root;
* the database is ``agrosat_taskNNN_*`` (never ``agrosat``);
* ports are spare (never 8000 or 5173) and distinct;
* every task name is ``\\AgroSat_TASKNNN_*`` and never a production name;
* the runtime environment file lies inside the rehearsal root and points at
  exactly the profile's database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from .common import ControlPlaneError, absolute, is_within, read_json, targets_production
from .pgclient import read_env_value

REHEARSAL_MARKER = ".agrosat-rehearsal-root.json"
REHEARSAL_DATABASE_PATTERN = re.compile(r"^agrosat_task[0-9]{3}_[a-z0-9_]{1,40}$")
REHEARSAL_TASK_PATTERN = re.compile(r"^\\AgroSat_TASK[0-9]{3}_[A-Za-z0-9_]{1,64}$")
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,40}$")
PRODUCTION_PORTS = frozenset({8000, 5173})
SIGNING_THUMBPRINT_PATTERN = re.compile(r"^[0-9A-F]{40}$")
FAULTS = {"backend_unready_after_switch"}


@dataclass(frozen=True)
class TaskNames:
    backend: str
    frontend: str
    sentinel: str
    notifications: str

    def all(self) -> dict[str, str]:
        return {"backend": self.backend, "frontend": self.frontend,
                "sentinel": self.sentinel, "notifications": self.notifications}


@dataclass(frozen=True)
class Profile:
    kind: str
    profile_id: str
    release_root: Path
    runtime_root: Path
    control_root: Path
    runtime_env_file: Path
    database_name: str
    backend_port: int
    frontend_port: int
    tasks: TaskNames
    node_executable: Path
    pg_bin: Path
    signing_thumbprints: tuple[str, ...]
    timezone_id: str
    rehearsal_root: Path | None = None
    backup_task: str | None = None
    fault_injection: str | None = None
    worker_dry_run: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def production(self) -> bool:
        return self.kind == "production"

    def evidence(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "profile_id": self.profile_id, "release_root": str(self.release_root),
            "runtime_root": str(self.runtime_root), "control_root": str(self.control_root),
            "runtime_env_file": str(self.runtime_env_file), "database_name": self.database_name,
            "backend_port": self.backend_port, "frontend_port": self.frontend_port, "tasks": self.tasks.all(),
            "backup_task": self.backup_task, "fault_injection": self.fault_injection,
            "worker_dry_run": self.worker_dry_run,
        }


PRODUCTION_TASKS = TaskNames(
    backend="\\AgroSat_PROGRAM_R3_Stabilization_Backend",
    frontend="\\AgroSat_PROGRAM_R3_Stabilization_Frontend",
    sentinel="\\AgroSat_PROGRAM_R3_SentinelCycle",
    notifications="\\AgroSat_PROGRAM_R3_OperationalNotifications",
)
PRODUCTION_BACKUP_TASK = "\\AgroSat_PROGRAM_R3_DatabaseBackup"
PRODUCTION = Profile(
    kind="production",
    profile_id="PROGRAM_R3",
    release_root=Path(r"C:\AgroSat_releases\PROGRAM_R3"),
    runtime_root=Path(r"C:\AgroSat_runtime\PROGRAM_R3"),
    control_root=Path(r"C:\AgroSat_runtime\PROGRAM_R3\control"),
    runtime_env_file=Path(r"C:\AgroSat\backend\.env"),
    database_name="agrosat",
    backend_port=8000,
    frontend_port=5173,
    tasks=PRODUCTION_TASKS,
    node_executable=Path(r"C:\Program Files\nodejs\node.exe"),
    pg_bin=Path(r"C:\Program Files\PostgreSQL\16\bin"),
    signing_thumbprints=("816767BE400FE53327432B12B29FE4B5809CA4CA",),
    timezone_id="West Asia Standard Time",
    backup_task=PRODUCTION_BACKUP_TASK,
)
PRODUCTION_TASK_NAMES = frozenset(PRODUCTION_TASKS.all().values()) | {PRODUCTION_BACKUP_TASK}

REHEARSAL_KEYS = frozenset({
    "schema_version", "kind", "profile_id", "rehearsal_root", "release_root", "runtime_root", "control_root",
    "runtime_env_file", "database_name", "backend_port", "frontend_port", "tasks", "node_executable",
    "pg_bin", "signing_thumbprints", "timezone_id", "fault_injection", "worker_dry_run", "backup_task",
})


def _port(value: Any) -> int:
    if type(value) is not int or not 1024 <= value <= 65535 or value in PRODUCTION_PORTS:
        raise ControlPlaneError("REHEARSAL_PORT_REJECTED", str(value))
    return value


def load_rehearsal_profile(path: Path) -> Profile:
    document = read_json(path, "REHEARSAL_PROFILE_UNREADABLE", max_bytes=64 * 1024)
    if not isinstance(document, dict) or set(document) != REHEARSAL_KEYS:
        raise ControlPlaneError("REHEARSAL_PROFILE_KEYSET_REJECTED")
    if document["schema_version"] != 1 or document["kind"] != "agrosat_rehearsal_profile":
        raise ControlPlaneError("REHEARSAL_PROFILE_SCHEMA_REJECTED")
    profile_id = document["profile_id"]
    if not isinstance(profile_id, str) or not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ControlPlaneError("REHEARSAL_PROFILE_ID_REJECTED")
    root = absolute(document["rehearsal_root"], "REHEARSAL_ROOT_REJECTED")
    if targets_production(root):
        raise ControlPlaneError("REHEARSAL_ROOT_GUARD_FAILED", "rehearsal root is inside a production root")
    marker = read_json(root / REHEARSAL_MARKER, "REHEARSAL_ROOT_GUARD_FAILED", max_bytes=4096)
    if not isinstance(marker, dict) or marker.get("profile_id") != profile_id:
        raise ControlPlaneError("REHEARSAL_ROOT_GUARD_FAILED", "rehearsal marker does not name this profile")
    paths = {}
    for name in ("release_root", "runtime_root", "control_root", "runtime_env_file"):
        value = absolute(document[name], "REHEARSAL_PATH_REJECTED")
        if not is_within(value, root) or targets_production(value):
            raise ControlPlaneError("REHEARSAL_ROOT_GUARD_FAILED", f"{name} must stay inside the rehearsal root")
        paths[name] = value
    database = document["database_name"]
    if not isinstance(database, str) or not REHEARSAL_DATABASE_PATTERN.fullmatch(database):
        raise ControlPlaneError("REHEARSAL_DATABASE_GUARD_FAILED", "rehearsal databases are agrosat_taskNNN_*")
    backend, frontend = _port(document["backend_port"]), _port(document["frontend_port"])
    if backend == frontend:
        raise ControlPlaneError("REHEARSAL_PORT_REJECTED", "backend and frontend ports must differ")
    tasks = document["tasks"]
    if not isinstance(tasks, dict) or set(tasks) != {"backend", "frontend", "sentinel", "notifications"}:
        raise ControlPlaneError("REHEARSAL_TASKS_REJECTED")
    for name in tasks.values():
        if not isinstance(name, str) or not REHEARSAL_TASK_PATTERN.fullmatch(name) or name in PRODUCTION_TASK_NAMES:
            raise ControlPlaneError("REHEARSAL_TASK_IDENTITY_REJECTED", str(name))
    if len(set(tasks.values())) != 4:
        raise ControlPlaneError("REHEARSAL_TASKS_REJECTED", "task names must be distinct")
    backup_task = document["backup_task"]
    if backup_task is not None and (not isinstance(backup_task, str) or not REHEARSAL_TASK_PATTERN.fullmatch(backup_task)
                                    or backup_task in PRODUCTION_TASK_NAMES or backup_task in tasks.values()):
        raise ControlPlaneError("REHEARSAL_TASK_IDENTITY_REJECTED", str(backup_task))
    env_database = read_env_value(paths["runtime_env_file"], "DATABASE_URL")
    if not env_database or not env_database.rstrip("/").split("?")[0].endswith("/" + database):
        raise ControlPlaneError("REHEARSAL_DATABASE_GUARD_FAILED",
                                "the rehearsal environment file must point at the rehearsal database")
    thumbprints = document["signing_thumbprints"]
    if not isinstance(thumbprints, list) or not thumbprints or not all(
        isinstance(item, str) and SIGNING_THUMBPRINT_PATTERN.fullmatch(item) for item in thumbprints
    ):
        raise ControlPlaneError("REHEARSAL_SIGNING_REJECTED")
    fault = document["fault_injection"]
    if fault is not None and fault not in FAULTS:
        raise ControlPlaneError("REHEARSAL_FAULT_REJECTED", str(fault))
    if not isinstance(document["worker_dry_run"], bool):
        raise ControlPlaneError("REHEARSAL_PROFILE_KEYSET_REJECTED", "worker_dry_run must be boolean")
    return Profile(
        kind="rehearsal", profile_id=profile_id,
        release_root=paths["release_root"], runtime_root=paths["runtime_root"],
        control_root=paths["control_root"], runtime_env_file=paths["runtime_env_file"],
        database_name=database, backend_port=backend, frontend_port=frontend,
        tasks=TaskNames(**tasks),
        node_executable=absolute(document["node_executable"], "REHEARSAL_PATH_REJECTED"),
        pg_bin=absolute(document["pg_bin"], "REHEARSAL_PATH_REJECTED"),
        signing_thumbprints=tuple(thumbprints), timezone_id=str(document["timezone_id"]),
        rehearsal_root=root, backup_task=backup_task, fault_injection=fault,
        worker_dry_run=document["worker_dry_run"],
    )
