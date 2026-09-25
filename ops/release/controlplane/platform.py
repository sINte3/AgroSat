"""The one seam between the controller and the host.

``WindowsPlatform`` performs real operations. Tests substitute a fake with the
same methods, which is how every gate, resume path and rollback branch is
exercised without touching a Scheduled Task, a port or a database.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
import time
from typing import Any

from . import backup, health, migration, winproc
from .common import ControlPlaneError, utc_now
from .pgclient import DatabaseTarget, tool_version
from .tasks import TaskAction, TaskDefinition, WindowsTasks, powershell


class WindowsPlatform:
    def __init__(self):
        self.tasks = WindowsTasks()

    # time
    def now(self) -> datetime:
        return utc_now()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    # scheduled tasks
    def task_definition(self, name: str) -> TaskDefinition | None:
        return self.tasks.export(name)

    def task_state(self, name: str) -> str | None:
        return self.tasks.state(name)

    def stop_task(self, name: str) -> None:
        self.tasks.stop(name)

    def start_task(self, name: str) -> None:
        self.tasks.start(name)

    def set_task_action(self, name: str, action: TaskAction) -> None:
        self.tasks.set_action(name, action)

    def set_task_enabled(self, name: str, enabled: bool) -> None:
        self.tasks.set_enabled(name, enabled)

    def task_engine_pids(self, name: str) -> list[int]:
        return self.tasks.engine_pids(name)

    # processes and listeners
    def listeners(self, ports: list[int]) -> dict[int, int | None]:
        return winproc.listeners(ports)

    def lineage(self, anchor: int) -> list[winproc.ProcessIdentity]:
        return winproc.lineage(anchor)

    def alive(self, identities: list[winproc.ProcessIdentity]) -> list[winproc.ProcessIdentity]:
        return winproc.alive(identities)

    def terminate_verified(self, identity: winproc.ProcessIdentity) -> bool:
        return winproc.terminate_verified(identity)

    # http
    def http_get(self, url: str) -> health.HttpResult:
        return health.http_get(url)

    # database and migrations
    def database(self, env_file: Path, database_name: str, pg_bin: Path) -> DatabaseTarget:
        return DatabaseTarget(env_file, database_name, pg_bin)

    def alembic_graph(self, python: Path, backend_directory: Path) -> dict[str, Any]:
        return migration.alembic_graph(python, backend_directory)

    def run_alembic(self, python: Path, backend_directory: Path, env_file: Path, arguments: list[str]):
        return migration.run_alembic(python, backend_directory, env_file, arguments)

    # database backups (the backup contract; see controlplane.backup)
    def backup_run(self, policy, *, reason: str, release_id: str | None = None) -> dict[str, Any]:
        return backup.run_backup(policy, reason=reason, release_id=release_id)

    def backup_load(self, policy, backup_id: str) -> dict[str, Any]:
        return backup.load_backup(policy, backup_id)

    def backup_secondary(self, policy, backup_id: str) -> dict[str, Any]:
        return backup.copy_secondary(policy, backup_id)

    def backup_protection(self, policy, backup_id: str) -> dict[str, Any]:
        return backup.protection_status(policy, backup_id)

    def backup_inventory(self, policy) -> list[dict[str, Any]]:
        return backup.inventory(policy)

    def backup_restore_swap(self, policy, backup_id: str, expected_revision: str, tag: str,
                            evidence_directory: Path) -> dict[str, Any]:
        return backup.restore_swap(policy, backup_id, expected_revision, tag, evidence_directory)

    # generic child processes (tool versions, launcher validation, worker dry runs)
    def run(self, argv: list[str], *, cwd: Path | None = None, timeout: int = 600,
            env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([str(item) for item in argv], cwd=None if cwd is None else str(cwd), env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)

    def pg_tool_version(self, target: DatabaseTarget, tool: str) -> str:
        return tool_version(target, tool)

    # Authenticode
    def sign(self, paths: list[Path], thumbprint: str) -> None:
        joined = ",".join("'" + str(path).replace("'", "''") + "'" for path in paths)
        powershell(
            f"$c = Get-Item ('Cert:\\LocalMachine\\My\\' + '{thumbprint}'); "
            f"foreach ($f in @({joined})) {{ $r = Set-AuthenticodeSignature -FilePath $f -Certificate $c -HashAlgorithm SHA256; "
            "if ($r.Status -ne 'Valid') { throw ('SIGNING_FAILED:' + [IO.Path]::GetFileName($f)) } }")

    def signatures(self, paths: list[Path]) -> dict[str, dict[str, str | None]]:
        joined = ",".join("'" + str(path).replace("'", "''") + "'" for path in paths)
        output = powershell(
            f"@(foreach ($f in @({joined})) {{ $s = Get-AuthenticodeSignature -FilePath $f; "
            "[pscustomobject]@{ path = $f; status = [string]$s.Status; "
            "thumbprint = $(if ($s.SignerCertificate) { $s.SignerCertificate.Thumbprint } else { $null }) } }) "
            "| ConvertTo-Json -Compress")
        rows = json.loads(output) if output.strip() else []
        rows = [rows] if isinstance(rows, dict) else rows
        return {row["path"]: {"status": row["status"], "thumbprint": row["thumbprint"]} for row in rows}

    def validate_worker_config(self, common_script: Path, function: str, configuration: Path) -> None:
        """Run a worker configuration validator under AllSigned, as the task would."""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "AllSigned", "-Command",
             f"$ErrorActionPreference='Stop'; . '{common_script}'; "
             f"{function} -ConfigurationPath '{configuration}' -RequireResolved | Out-Null; exit 0"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        if result.returncode != 0:
            raise ControlPlaneError("WORKER_CONFIGURATION_REJECTED",
                                    " ".join((result.stderr or result.stdout).split())[:400])
