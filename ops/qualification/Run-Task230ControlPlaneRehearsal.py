"""TASK_230 isolated end-to-end rehearsal of the production operations control plane.

Phases (all by default, in this order; each writes machine-readable evidence):

``database``  isolated source database agrosat_task230_src_<run> at the current
              Alembic head with synthetic rows; validated backup, verified
              secondary copy (second LOCAL root: not off-host), restore
              rehearsal into agrosat_task230_restore_<run>, retention plan+apply.
``release``   Run 1. Release A = 4cd8ea7 (production's SHA) runs with a
              production-shaped legacy supervisor on spare ports against
              agrosat_task230_rel1_<run> (restored from the backup). The control
              plane releases B (the TASK_230 commit), then rolls back to A with
              the explicit rollback operation.
``failure``   Run 2. Release A' = 387eaeda (Alembic head 0015) against
              agrosat_task230_rel2_<run> at 0015. The control plane releases B
              with an authorized 0015->0016 migration and a forced post-switch
              failure (rehearsal fault injection); the automatic rollback must
              restore the validated pre-release backup by swap and return A'.
``backuptask`` The scheduled backup task installed from B's release, run once,
              inspected, removed.
``cleanup``   Rehearsal tasks removed (their trees ended through ownership
              only), restore-rehearsal target dropped with its evidence,
              credential-bearing rehearsal env files deleted.

Guards: Scheduled Tasks are \\AgroSat_TASK230_* only, databases agrosat_task230_*
only, ports 58400-58499 only; production releases are read, never written;
the production environment file is read only to derive the isolated database
URL on the same server, and that copy is deleted at the end.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "ops" / "release"))

from controlplane import health, winproc  # noqa: E402
from controlplane.common import copy_tree, read_json, sha256_file, write_json_atomic  # noqa: E402
from controlplane.gitmaterial import Git, extract_archive  # noqa: E402
from controlplane.pgclient import DatabaseTarget, read_env_value  # noqa: E402
from controlplane.tasks import WindowsTasks, powershell  # noqa: E402

# Short on purpose: release venvs nest ~165 characters deep and long paths are disabled on this host.
BASE = Path(r"C:\AgroSat_rehearsal\T230")
PRODUCTION_ENV = Path(r"C:\AgroSat\backend\.env")
PRODUCTION_RELEASES = Path(r"C:\AgroSat_releases\PROGRAM_R3")
PG_BIN = Path(r"C:\Program Files\PostgreSQL\16\bin")
NODE = Path(r"C:\Program Files\nodejs\node.exe")
DEV_PYTHON = Path(r"C:\AgroSat\backend\venv\Scripts\python.exe")
CLI = REPOSITORY / "ops" / "release" / "Invoke-AgroSatControlPlane.py"
THUMBPRINT = "816767BE400FE53327432B12B29FE4B5809CA4CA"
A_SHA = "4cd8ea7240bbd2307488ad4871e504a672a812a6"
A2_SHA = "387eaeda6bcbcc3ef0a2e2951b8f87bbf75ad927"
TASK_PREFIX = "\\AgroSat_TASK230_"
DATABASE_PREFIX = "agrosat_task230_"
PORTS = range(58400, 58500)
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def now() -> str:
    return datetime.now().astimezone().isoformat()


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def guard_database(name: str) -> str:
    if not re.fullmatch(r"agrosat_task230_[a-z0-9_]{1,40}", name):
        raise SystemExit(f"TASK230_DATABASE_GUARD:{name}")
    return name


def guard_task(name: str) -> str:
    if not name.startswith(TASK_PREFIX):
        raise SystemExit(f"TASK230_TASK_GUARD:{name}")
    return name


def spare_ports(count: int, taken: set[int]) -> list[int]:
    found = []
    for port in PORTS:
        if port in taken or port in (8000, 5173):
            continue
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        found.append(port)
        taken.add(port)
        if len(found) == count:
            return found
    raise SystemExit("TASK230_NO_SPARE_PORTS")


def cli(*arguments: str, evidence: Path | None = None, timeout: int = 3600) -> tuple[int, dict]:
    result = subprocess.run([str(DEV_PYTHON), "-B", str(CLI), *arguments], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    try:
        document = json.loads(result.stdout)
    except ValueError:
        document = {"unparsed_stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]}
    if evidence is not None:
        write_json_atomic(evidence, {"arguments": list(arguments), "exit_code": result.returncode, "output": document},
                          check_secrets=True)
    return result.returncode, document


def restrict_acl(path: Path) -> None:
    subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", "*S-1-5-32-544:F", "/grant:r", "*S-1-5-18:F"],
                   check=True, capture_output=True)


def write_env(path: Path, database: str) -> Path:
    url = read_env_value(PRODUCTION_ENV, "DATABASE_URL")
    parts = urlsplit(url)
    isolated = urlunsplit((parts.scheme, parts.netloc, "/" + guard_database(database), parts.query, parts.fragment))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([
        "# TASK_230 rehearsal runtime environment: isolated database only; deleted at cleanup",
        f"DATABASE_URL={isolated}",
        f"SECRET_KEY={secrets.token_urlsafe(48)}",
        "REDIS_URL=redis://127.0.0.1:1/0",
        "OBSERVATION_DETECTION_ENABLED=false",
        "TELEGRAM_NOTIFICATIONS_ENABLED=false",
        "WIALON_ENABLED=false",
        "",
    ]), encoding="utf-8")
    restrict_acl(path)
    return path


def target(env: Path, database: str) -> DatabaseTarget:
    return DatabaseTarget(env, guard_database(database), PG_BIN)


def create_database(env: Path, database: str) -> None:
    db = target(env, database)
    if db.database_exists(database):
        raise SystemExit(f"TASK230_DATABASE_EXISTS:{database}")
    db.query(f'CREATE DATABASE "{database}"', database="postgres", read_only=False)
    db.query("CREATE EXTENSION IF NOT EXISTS postgis", read_only=False)


def alembic_upgrade(env: Path, revision: str) -> None:
    environment = {key: value for key, value in os.environ.items() if key.upper() not in ("DATABASE_URL", "PYTHONPATH")}
    environment["AGROSAT_RUNTIME_ENV_FILE"] = str(env)
    result = subprocess.run([str(DEV_PYTHON), "-B", "-m", "alembic", "upgrade", revision], cwd=REPOSITORY / "backend",
                            env=environment, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise SystemExit("TASK230_ALEMBIC_UPGRADE_FAILED:" + result.stderr[-800:])


SEED = """
INSERT INTO enterprises (name, code, is_active, created_at, updated_at)
  VALUES ('TASK230 rehearsal enterprise', 'T230', true, now(), now());
INSERT INTO crop_types (code, name_ru) VALUES ('t230_cotton', 'TASK230 rehearsal crop');
INSERT INTO fields (enterprise_id, name, code, geometry, area_ha, is_active, created_at, updated_at)
  SELECT id, 'TASK230 rehearsal field', 'T230-F1',
         ST_GeomFromText('POLYGON((64.40 39.77, 64.41 39.77, 64.41 39.78, 64.40 39.78, 64.40 39.77))', 4326),
         85.0, true, now(), now()
  FROM enterprises WHERE code = 'T230';
"""


def seed(env: Path, database: str) -> None:
    db = target(env, database)
    result = db.run("psql", ["-X", "-q", "-v", "ON_ERROR_STOP=1", "-c", SEED], read_only=False)
    if result.returncode != 0:
        raise SystemExit("TASK230_SEED_FAILED")


def backup_policy(path: Path, *, database: str, env: Path, root: Path, schedule: dict | None = None) -> Path:
    (root / "primary").mkdir(parents=True, exist_ok=True)
    (root / "secondary").mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, {
        "schema_version": 1, "kind": "agrosat_database_backup_policy", "example_only": False,
        "database_name": database, "source_classification": "rehearsal", "runtime_env_file": str(env),
        "pg_bin": str(PG_BIN), "backup_root": str(root / "primary"),
        "retention": {"keep_daily_count": 7, "keep_weekly_count": 4, "minimum_backup_count": 3,
                      "minimum_age_before_delete_hours": 72},
        "secondary": {"required": True, "destination": str(root / "secondary")}, "schedule": schedule})
    return path


# ------------------------------------------------------------------ Scheduled Tasks

SETTINGS = {"backend": (0, 3, 1), "frontend": (0, 3, 1), "sentinel": (360, 3, 15), "notifications": (10, 3, 5)}


def register(name: str, kind: str, execute: str, arguments: str, working: str) -> None:
    guard_task(name)
    limit, count, interval = SETTINGS[kind]
    leaf = name[1:]
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"  # noqa: E731
    powershell(
        f"if (Get-ScheduledTask -TaskPath '\\' -TaskName {quote(leaf)} -ErrorAction SilentlyContinue) {{ throw 'TASK230_TASK_EXISTS' }}; "
        f"$a = New-ScheduledTaskAction -Execute {quote(execute)} -Argument {quote(arguments)} -WorkingDirectory {quote(working)}; "
        "$p = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest; "
        f"$s = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes {limit}) "
        f"-RestartCount {count} -RestartInterval (New-TimeSpan -Minutes {interval}) -StartWhenAvailable "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        f"Register-ScheduledTask -TaskPath '\\' -TaskName {quote(leaf)} -Action $a -Principal $p -Settings $s "
        "-Description 'TASK_230 isolated control-plane rehearsal; no trigger' | Out-Null")


def stop_owned_tree(name: str) -> dict:
    """Stop a rehearsal task; end only descendants captured below its engine PID if they outlive the stop."""
    tasks = WindowsTasks()
    captured = [identity for engine in tasks.engine_pids(guard_task(name)) for identity in winproc.lineage(engine)]
    if tasks.state(name) == "Running":
        tasks.stop(name)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and winproc.alive(captured):
        time.sleep(0.5)
    survivors = winproc.alive(captured)
    terminated = [identity.evidence() for identity in reversed(survivors) if winproc.terminate_verified(identity)]
    return {"task": name, "captured": len(captured), "terminated_after_stop": terminated,
            "alive_after": [identity.evidence() for identity in winproc.alive(captured)]}


def unregister(name: str) -> None:
    leaf = guard_task(name)[1:]
    powershell(f"$t = Get-ScheduledTask -TaskPath '\\' -TaskName '{leaf}' -ErrorAction SilentlyContinue; "
               f"if ($t) {{ Unregister-ScheduledTask -TaskPath '\\' -TaskName '{leaf}' -Confirm:$false }}")


def sign(paths: list[Path]) -> None:
    joined = ",".join("'" + str(path) + "'" for path in paths)
    powershell(f"$c = Get-Item 'Cert:\\LocalMachine\\My\\{THUMBPRINT}'; foreach ($f in @({joined})) {{ "
               "$r = Set-AuthenticodeSignature -FilePath $f -Certificate $c -HashAlgorithm SHA256; "
               "if ($r.Status -ne 'Valid') { throw 'SIGNING_FAILED' } }")


# ------------------------------------------------------------------ rehearsal world

LEGACY_REPLACEMENTS = [
    ('RELEASE_ROOT = Path(r"C:\\AgroSat_releases\\PROGRAM_R3")', 'RELEASE_ROOT = Path(r"{releases}")'),
    ('RUNTIME_ROOT = Path(r"C:\\AgroSat_runtime\\PROGRAM_R3")', 'RUNTIME_ROOT = Path(r"{runtimes}")'),
    ('RUNTIME_ENV = Path(r"C:\\AgroSat\\backend\\.env")', 'RUNTIME_ENV = Path(r"{env}")'),
    ('assert_port_free(8000 if component == "backend" else 5173)',
     'assert_port_free({backend} if component == "backend" else {frontend})'),
    ('"--host", "127.0.0.1", "--port", "8000"', '"--host", "127.0.0.1", "--port", "{backend}"'),
    ('{{"QUALIFICATION_FRONTEND_PORT": "5173",', '{{"QUALIFICATION_FRONTEND_PORT": "{frontend}",'),
    ('"QUALIFICATION_BACKEND_PORT": "8000",', '"QUALIFICATION_BACKEND_PORT": "{backend}",'),
]


class Rehearsal:
    """One isolated rehearsal root with its own tasks, ports, database and running release A."""

    def __init__(self, root: Path, label: str, database: str, ports: list[int]):
        self.root, self.label, self.database = root, label, guard_database(database)
        self.backend_port, self.frontend_port = ports
        self.releases, self.runtimes, self.control = root / "releases", root / "runtime", root / "control"
        self.env = root / "env" / "rehearsal.env"
        self.tasks = {kind: f"{TASK_PREFIX}{label}_{name}" for kind, name in (
            ("backend", "Backend"), ("frontend", "Frontend"), ("sentinel", "SentinelCycle"),
            ("notifications", "OperationalNotifications"))}
        for directory in (self.releases, self.runtimes, self.control):
            directory.mkdir(parents=True, exist_ok=True)
        write_json_atomic(root / ".agrosat-rehearsal-root.json", {"profile_id": f"TASK230_{label}"})

    def profile(self, *, fault: str | None = None, dry_run: bool = True) -> Path:
        path = self.root / "profile.json"
        write_json_atomic(path, {
            "schema_version": 1, "kind": "agrosat_rehearsal_profile", "profile_id": f"TASK230_{self.label}",
            "rehearsal_root": str(self.root), "release_root": str(self.releases), "runtime_root": str(self.runtimes),
            "control_root": str(self.control), "runtime_env_file": str(self.env), "database_name": self.database,
            "backend_port": self.backend_port, "frontend_port": self.frontend_port, "tasks": self.tasks,
            "node_executable": str(NODE), "pg_bin": str(PG_BIN), "signing_thumbprints": [THUMBPRINT],
            "timezone_id": "West Asia Standard Time", "fault_injection": fault, "worker_dry_run": dry_run,
            "backup_task": None})
        return path

    def materialize_legacy_release(self, sha: str, head: str) -> Path:
        """Release A exactly as production materialized it (schema 1 manifest, copied venv and dist)."""
        release = self.releases / sha
        archive = self.root / "material" / f"{sha[:12]}.zip"
        Git(REPOSITORY).archive(sha, archive)
        extract_archive(archive, release)
        source = PRODUCTION_RELEASES / sha
        copy_tree(source / "backend" / "venv", release / "backend" / "venv")
        copy_tree(source / "frontend" / "dist", release / "frontend" / "dist")
        write_json_atomic(release / "release-manifest.json", {
            "schema_version": 1, "git_sha": sha, "purpose": f"TASK_230 rehearsal {self.label} release A",
            "alembic_head": head, "created_utc": now()})
        return release

    def legacy_runtime(self, sha: str, notifications_source: Path) -> dict:
        """Production-shaped runtime for A: legacy supervisor (constants only), shared scheduler, signed runners."""
        release, runtime = self.releases / sha, self.runtimes / sha
        application = runtime / "application"
        application.mkdir(parents=True)
        source = (release / "ops" / "release" / "Run-AgroSatApplication.py").read_text(encoding="utf-8")
        for old, new in LEGACY_REPLACEMENTS:
            if source.count(old.replace("{{", "{").replace("}}", "}")) != 1:
                raise SystemExit("TASK230_LEGACY_LAUNCHER_SHAPE_CHANGED")
            source = source.replace(old.replace("{{", "{").replace("}}", "}"), new.format(
                releases=self.releases, runtimes=self.runtimes, env=self.env, backend=self.backend_port,
                frontend=self.frontend_port))
        (application / "Run-AgroSatApplication.py").write_text(source, encoding="utf-8")
        manifest_sha = sha256_file(release / "release-manifest.json")
        write_json_atomic(application / "application-release.json", {
            "release_commit": sha, "manifest_sha256": manifest_sha,
            "wrapper_sha256": sha256_file(application / "Run-AgroSatApplication.py"),
            "runtime_directory": str(application)})
        for name in ("runs", "state", "locks"):
            (runtime / "collector" / name).mkdir(parents=True)
        scheduler = self.runtimes / "scheduler"
        scheduler.mkdir(exist_ok=True)
        for name in ("Invoke-Collector.ps1", "Task209-CollectorTask.Common.ps1"):
            shutil.copyfile(release / "ops" / "windows-task" / name, scheduler / name)
        notifications = runtime / "operational-notifications"
        notifications.mkdir()
        for name in ("Invoke-OperationalNotifications.ps1", "Task221-OperationalNotifications.Common.ps1"):
            shutil.copyfile(notifications_source / name, notifications / name)
        sign([scheduler / "Invoke-Collector.ps1", scheduler / "Task209-CollectorTask.Common.ps1",
              notifications / "Invoke-OperationalNotifications.ps1",
              notifications / "Task221-OperationalNotifications.Common.ps1"])
        python = str(release / "backend" / "venv" / "Scripts" / "python.exe")
        common = {"execution_identity": "NT AUTHORITY\\SYSTEM", "execution_sid": "S-1-5-18",
                  "expected_windows_timezone_id": "West Asia Standard Time", "release_commit": sha,
                  "immutable_release_root": str(self.releases), "release_manifest_sha256": manifest_sha,
                  "working_directory": str(release), "python_executable": python, "runtime_env_file": str(self.env)}
        # Configurations carry the canonical identities, exactly as production's do; the
        # registered rehearsal tasks are \AgroSat_TASK230_* and never fire by themselves.
        collector_config = {"schema_version": 1, "task_name": "\\AgroSat_PROGRAM_R3_SentinelCycle",
                            "task_description": "TASK_230 rehearsal Sentinel", **common,
                            "runner_script": str(scheduler / "Invoke-Collector.ps1"),
                            "schedule": {"daily_at_local_times": ["06:00:00"], "start_when_available": True,
                                         "restart_count": 3, "restart_interval_minutes": 15,
                                         "execution_time_limit_hours": 6, "multiple_instances": "IgnoreNew"},
                            "collector": {"indices": "ndvi", "batch_size": 25, "lookback_days": 14,
                                          "max_attempts": 2, "retry_base_seconds": 2, "field_timeout_seconds": 180,
                                          "cycle_timeout_seconds": 21600,
                                          "output_directory": str(runtime / "collector" / "runs"),
                                          "state_directory": str(runtime / "collector" / "state"),
                                          "lock_directory": str(runtime / "collector" / "locks")}}
        write_bom_json(scheduler / "collector-task.json", collector_config)
        notification_config = {"schema_version": 1, "task_name": "\\AgroSat_PROGRAM_R3_OperationalNotifications",
                               "task_description": "TASK_230 rehearsal notifications", **common,
                               "runner_script": str(notifications / "Invoke-OperationalNotifications.ps1"),
                               "schedule": {"interval_minutes": 15, "start_when_available": True, "restart_count": 3,
                                            "restart_interval_minutes": 5, "execution_time_limit_minutes": 10,
                                            "multiple_instances": "IgnoreNew"},
                               "reconciliation": {"limit": 200, "output_directory": str(notifications)}}
        write_bom_json(notifications / "operational-notifications-task.json", notification_config)
        app_args = (f'-B "{application / "Run-AgroSatApplication.py"}" --configuration '
                    f'"{application / "application-release.json"}" --component ')
        register(self.tasks["backend"], "backend", python, app_args + "backend", str(release))
        register(self.tasks["frontend"], "frontend", python, app_args + "frontend", str(release))
        register(self.tasks["sentinel"], "sentinel", "powershell.exe",
                 f'-NoProfile -NonInteractive -ExecutionPolicy AllSigned -File "{scheduler / "Invoke-Collector.ps1"}" '
                 f'-ConfigurationPath "{scheduler / "collector-task.json"}" -Mode apply', str(release))
        register(self.tasks["notifications"], "notifications", "powershell.exe",
                 f'-NoProfile -NonInteractive -ExecutionPolicy AllSigned -File '
                 f'"{notifications / "Invoke-OperationalNotifications.ps1"}" -ConfigurationPath '
                 f'"{notifications / "operational-notifications-task.json"}" -Mode apply', str(release))
        return {"release": str(release), "runtime": str(runtime), "tasks": self.tasks}

    def start_application(self, sha: str, head: str) -> dict:
        tasks = WindowsTasks()
        for kind in ("backend", "frontend"):
            tasks.start(self.tasks[kind])
        # Releases A predate TASK_228 and publish the older readiness payload.
        return self.wait_healthy(sha, head, self.releases / sha, pre_task228=True)

    def wait_healthy(self, sha: str, head: str, release: Path, seconds: float = 180, *, pre_task228: bool = False) -> dict:
        index = sha256_file(release / "frontend" / "dist" / "index.html")
        backend = health.wait_for(lambda: health.probe_backend(self.backend_port, sha, head,
                                                               pre_task228_compatible=pre_task228),
                                  deadline_seconds=seconds)
        frontend = health.wait_for(lambda: health.probe_frontend(self.frontend_port, index, sha), deadline_seconds=seconds)
        return {"backend": backend, "frontend": frontend, "pass": backend["pass"] and frontend["pass"]}

    def listener_ownership(self) -> dict:
        tasks = WindowsTasks()
        owners = winproc.listeners([self.backend_port, self.frontend_port])
        report = {}
        for kind, port in (("backend", self.backend_port), ("frontend", self.frontend_port)):
            lineage = {identity.pid for engine in tasks.engine_pids(self.tasks[kind]) for identity in winproc.lineage(engine)}
            report[kind] = {"port": port, "listener_pid": owners[port], "owned_by_task_lineage": owners[port] in lineage}
        return report

    def bindings(self) -> dict:
        tasks = WindowsTasks()
        return {kind: tasks.export(name).actions[0].evidence() for kind, name in self.tasks.items()}

    def cleanup(self) -> dict:
        report = {"stops": [], "unregistered": []}
        for kind in ("frontend", "backend", "sentinel", "notifications"):
            name = self.tasks[kind]
            if WindowsTasks().state(name) is not None:
                report["stops"].append(stop_owned_tree(name))
                unregister(name)
                report["unregistered"].append(name)
        report["listeners_after"] = winproc.listeners([self.backend_port, self.frontend_port])
        return report


def write_bom_json(path: Path, value: dict) -> None:
    text = json.dumps(value, indent=4) + "\r\n"
    path.write_bytes(b"\xef\xbb\xbf" + text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))


def alive_from_evidence(state_directory: Path, gate_prefix: str) -> list:
    files = sorted((state_directory / "gates").glob(f"*_{gate_prefix}-attempt*.json"))
    if not files:
        return []
    record = read_json(files[-1], "X")
    rows = (record.get("stop") or {}).get("captured", [])
    return [identity.evidence() for identity in winproc.alive([winproc.ProcessIdentity(**row) for row in rows])]


def evidence_inventory(state_directory: Path) -> dict:
    gates = sorted(path.name for path in (state_directory / "gates").glob("*.json"))
    return {"state": (state_directory / "state.json").exists(), "events": (state_directory / "events.jsonl").exists(),
            "summary": (state_directory / "summary.md").exists(), "gate_files": gates,
            "snapshots": sorted(path.name for path in (state_directory / "snapshots").glob("*"))}


def authorize(output: Path, *, operation: str, release_id: str, candidate: str, current: str, database: str,
              migration: tuple[str, str] | None = None, strategy: str = "none", backup_sha256: str | None = None,
              evidence: Path) -> tuple[Path, str]:
    arguments = ["authorize", "--operation", operation, "--release-id", release_id, "--candidate", candidate,
                 "--expected-current", current, "--database", database, "--output", str(output),
                 "--authorized-by", "TASK_230 rehearsal operator", "--valid-hours", "4",
                 "--database-rollback-strategy", strategy]
    if migration:
        arguments += ["--migration-from", migration[0], "--migration-to", migration[1]]
    if backup_sha256:
        arguments += ["--restore-backup-sha256", backup_sha256]
    code, document = cli(*arguments, evidence=evidence)
    if code != 0:
        raise SystemExit(f"TASK230_AUTHORIZE_FAILED:{document}")
    return output, document["sha256"]


# ------------------------------------------------------------------ phases

def phase_database(context: dict) -> dict:
    run = context["run"]
    root = context["root"] / "database"
    env = write_env(root / "env" / "source.env", context["source_db"])
    context["env_files"].append(env)
    create_database(env, context["source_db"])
    alembic_upgrade(env, "head")
    seed(env, context["source_db"])
    policy = backup_policy(root / "backup-policy.json", database=context["source_db"], env=env, root=root / "backups")
    evidence = context["evidence"] / "database"
    code, backup = cli("backup", "run", "--policy", str(policy), evidence=evidence / "01-backup-run.json")
    assert code == 0, backup
    backup_id = backup["backup_id"]
    code, secondary = cli("backup", "secondary", "--policy", str(policy), "--backup-id", backup_id,
                          evidence=evidence / "02-backup-secondary.json")
    code2, protection = cli("backup", "protection", "--policy", str(policy), "--backup-id", backup_id,
                            evidence=evidence / "03-backup-protection.json")
    restore_target = guard_database(f"agrosat_task230_restore_{run}")
    code3, restore = cli("backup", "restore-rehearsal", "--policy", str(policy), "--backup-id", backup_id,
                         "--target", restore_target, "--evidence", str(evidence / "restore"),
                         evidence=evidence / "04-restore-rehearsal.json")
    code4, plan = cli("backup", "retention-plan", "--policy", str(policy), evidence=evidence / "05-retention-plan.json")
    code5, applied = cli("backup", "retention-apply", "--policy", str(policy), "--plan-sha256", plan.get("plan_sha256", ""),
                         evidence=evidence / "06-retention-apply.json")
    context.update(source_env=env, source_policy=policy, source_backup_id=backup_id, restore_target=restore_target,
                   restore_evidence=evidence / "restore" / f"restore-rehearsal-{restore_target}.json")
    return {"backup_id": backup_id, "backup_sha256": backup["file"]["sha256"], "db_revision": backup["db_revision"],
            "row_counts": backup["row_counts"], "validation": backup["validation"],
            "secondary": {"exit_code": code, "verified": secondary.get("verified")},
            "protection": protection, "restore_rehearsal": {"exit_code": code3, "result": restore.get("result"),
                                                           "checks": (restore.get("validation") or {}).get("checks")},
            "retention": {"plan_exit": code4, "apply_exit": code5, "deleted": applied.get("deleted"),
                          "kept": sorted((plan.get("keep") or {}).keys())},
            "pass": code == 0 and code2 == 0 and protection.get("fully_protected") is True and code3 == 0
            and restore.get("result") == "PASS" and code4 == 0 and code5 == 0}


def restored_database(context: dict, env: Path, database: str, evidence: Path) -> None:
    """A rehearsal database restored from the validated source backup."""
    code, restore = cli("backup", "restore-rehearsal", "--policy", str(context["source_policy"]), "--backup-id",
                        context["source_backup_id"], "--target", database, "--evidence", str(evidence),
                        evidence=evidence / f"restore-{database}.json")
    if code != 0 or restore.get("result") != "PASS":
        raise SystemExit(f"TASK230_REHEARSAL_DATABASE_RESTORE_FAILED:{restore}")


def phase_release(context: dict) -> dict:
    run, candidate = context["run"], context["candidate"]
    ports = spare_ports(2, context["ports"])
    rehearsal = Rehearsal(context["root"] / "r1", "R1", f"agrosat_task230_rel1_{run}", ports)
    context["rehearsals"].append(rehearsal)
    evidence = context["evidence"] / "run1"
    write_env(rehearsal.env, rehearsal.database)
    context["env_files"].append(rehearsal.env)
    restored_database(context, rehearsal.env, rehearsal.database, evidence / "database")
    head = "0016_operational_command_center"
    rehearsal.materialize_legacy_release(A_SHA, head)
    setup = rehearsal.legacy_runtime(A_SHA, rehearsal.releases / A_SHA / "ops" / "windows-task")
    a_health = rehearsal.start_application(A_SHA, head)
    write_json_atomic(evidence / "00-release-A-running.json", {"setup": setup, "health": a_health,
                                                               "listeners": rehearsal.listener_ownership()})
    if not a_health["pass"]:
        raise SystemExit("TASK230_RELEASE_A_NOT_HEALTHY")
    policy = backup_policy(rehearsal.root / "backup-policy.json", database=rehearsal.database, env=rehearsal.env,
                           root=rehearsal.root / "backups")
    profile = rehearsal.profile()
    release_id = f"R230-{run}-release"
    auth, auth_sha = authorize(context["root"] / "authorizations" / f"{release_id}.json", operation="release",
                               release_id=release_id, candidate=candidate, current=A_SHA, database=rehearsal.database,
                               evidence=evidence / "01-authorize-release.json")
    started = time.monotonic()
    code, result = cli("release", "--mode", "rehearsal", "--profile", str(profile), "--release-id", release_id,
                       "--candidate", candidate, "--expected-current", A_SHA, "--authorization", str(auth),
                       "--authorization-sha256", auth_sha, "--repository", str(REPOSITORY), "--backup-policy", str(policy),
                       evidence=evidence / "02-release.json")
    release_seconds = round(time.monotonic() - started, 1)
    state_directory = rehearsal.control / "releases" / release_id
    b_health = rehearsal.wait_healthy(candidate, head, rehearsal.releases / candidate, seconds=30)
    after_release = {"cli_exit": code, "status": result.get("status"), "seconds": release_seconds, "health": b_health,
                     "listeners": rehearsal.listener_ownership(), "bindings": rehearsal.bindings(),
                     "release_a_backend_processes_alive": alive_from_evidence(state_directory, "SWITCH_BACKEND"),
                     "release_a_frontend_processes_alive": alive_from_evidence(state_directory, "SWITCH_FRONTEND"),
                     "legacy_handoff": read_json(sorted((state_directory / "gates").glob("*_SWITCH_BACKEND-attempt*.json"))[-1],
                                                 "X")["stop"]["terminated_by_legacy_handoff"],
                     "evidence": evidence_inventory(state_directory)}
    write_json_atomic(evidence / "03-after-release.json", after_release)
    rollback_id = f"R230-{run}-rollback"
    auth, auth_sha = authorize(context["root"] / "authorizations" / f"{rollback_id}.json", operation="rollback",
                               release_id=rollback_id, candidate=A_SHA, current=candidate, database=rehearsal.database,
                               evidence=evidence / "04-authorize-rollback.json")
    code2, rolled = cli("rollback", "--mode", "rehearsal", "--profile", str(profile), "--release-id", rollback_id,
                        "--candidate", A_SHA, "--expected-current", candidate, "--authorization", str(auth),
                        "--authorization-sha256", auth_sha, "--backup-policy", str(policy),
                        evidence=evidence / "05-rollback.json")
    rollback_directory = rehearsal.control / "releases" / rollback_id
    rollback_state = read_json(rollback_directory / "state.json", "X")
    snapshot = read_json(state_directory / "snapshots" / "tasks-before.json", "X")
    a_after = rehearsal.wait_healthy(A_SHA, head, rehearsal.releases / A_SHA, seconds=30, pre_task228=True)
    bindings = rehearsal.bindings()
    after_rollback = {"cli_exit": code2, "status": rolled.get("status"), "health": a_after,
                      "listeners": rehearsal.listener_ownership(), "bindings_equal_recorded_A": all(
                          bindings[kind] == snapshot[kind]["actions"][0] for kind in bindings),
                      "rollback_case": rollback_state["rollback_plan"]["decision"]["case"],
                      "db_revision_before_after": [rollback_state["db_revision_before"], rollback_state["db_revision_after"]],
                      "release_b_backend_processes_alive": alive_from_evidence(rollback_directory, "SWITCH_BACKEND"),
                      "release_b_frontend_processes_alive": alive_from_evidence(rollback_directory, "SWITCH_FRONTEND"),
                      "evidence": evidence_inventory(rollback_directory)}
    write_json_atomic(evidence / "06-after-rollback.json", after_rollback)
    context["run1"] = {"rehearsal": rehearsal, "policy": policy}
    passed = (code == 0 and result.get("status") == "PASS" and b_health["pass"]
              and all(item["owned_by_task_lineage"] for item in after_release["listeners"].values())
              and not after_release["release_a_backend_processes_alive"]
              and not after_release["release_a_frontend_processes_alive"]
              and code2 == 0 and rolled.get("status") == "PASS" and a_after["pass"]
              and after_rollback["bindings_equal_recorded_A"]
              and all(item["owned_by_task_lineage"] for item in after_rollback["listeners"].values())
              and not after_rollback["release_b_backend_processes_alive"]
              and not after_rollback["release_b_frontend_processes_alive"])
    return {"release": {key: after_release[key] for key in ("cli_exit", "status", "seconds", "legacy_handoff")},
            "rollback": {key: after_rollback[key] for key in ("cli_exit", "status", "rollback_case",
                                                              "db_revision_before_after", "bindings_equal_recorded_A")},
            "pass": passed}


def phase_failure(context: dict) -> dict:
    run, candidate = context["run"], context["candidate"]
    ports = spare_ports(2, context["ports"])
    rehearsal = Rehearsal(context["root"] / "r2", "R2", f"agrosat_task230_rel2_{run}", ports)
    context["rehearsals"].append(rehearsal)
    evidence = context["evidence"] / "run2"
    write_env(rehearsal.env, rehearsal.database)
    context["env_files"].append(rehearsal.env)
    create_database(rehearsal.env, rehearsal.database)
    alembic_upgrade(rehearsal.env, "0015_closed_loop_agronomy")
    seed(rehearsal.env, rehearsal.database)
    head_a, head_b = "0015_closed_loop_agronomy", "0016_operational_command_center"
    rehearsal.materialize_legacy_release(A2_SHA, head_a)
    # 387eaeda predates the notification runner; bind A's task to the current runner files.
    setup = rehearsal.legacy_runtime(A2_SHA, REPOSITORY / "ops" / "windows-task")
    a_health = rehearsal.start_application(A2_SHA, head_a)
    write_json_atomic(evidence / "00-release-A2-running.json", {"setup": setup, "health": a_health})
    if not a_health["pass"]:
        raise SystemExit("TASK230_RELEASE_A2_NOT_HEALTHY")
    policy = backup_policy(rehearsal.root / "backup-policy.json", database=rehearsal.database, env=rehearsal.env,
                           root=rehearsal.root / "backups")
    profile = rehearsal.profile(fault="backend_unready_after_switch", dry_run=False)
    release_id = f"R230-{run}-forced-failure"
    auth, auth_sha = authorize(context["root"] / "authorizations" / f"{release_id}.json", operation="release",
                               release_id=release_id, candidate=candidate, current=A2_SHA, database=rehearsal.database,
                               migration=(head_a, head_b), strategy="restore_validated_backup",
                               evidence=evidence / "01-authorize.json")
    code, result = cli("release", "--mode", "rehearsal", "--profile", str(profile), "--release-id", release_id,
                       "--candidate", candidate, "--expected-current", A2_SHA, "--authorization", str(auth),
                       "--authorization-sha256", auth_sha, "--repository", str(REPOSITORY), "--backup-policy", str(policy),
                       "--fast-deadlines", evidence=evidence / "02-release-forced-failure.json")
    state_directory = rehearsal.control / "releases" / release_id
    state = read_json(state_directory / "state.json", "X")
    database = DatabaseTarget(rehearsal.env, rehearsal.database, PG_BIN)
    kept = database.query(f"SELECT datname FROM pg_database WHERE datname LIKE '{rehearsal.database}_pre_rb_%'",
                          database="postgres")
    kept_revision = database.revisions(database=kept[0]) if kept else None
    a_after = rehearsal.wait_healthy(A2_SHA, head_a, rehearsal.releases / A2_SHA, seconds=60, pre_task228=True)
    report = {
        "cli_exit": code, "status": result.get("status"), "state_status": state["status"],
        "failed_gate": state["rollback_result"]["cause_gate"] if state.get("rollback_result") else None,
        "migration": {"before": state["db_revision_before"], "after": state["db_revision_after"],
                      "applied_steps": [step["revision"] for step in state["migration"]["applied_steps"]]},
        "rollback_result": state.get("rollback_result"),
        "database_now": database.revisions(), "previous_live_database_kept_as": kept,
        "kept_database_revision": kept_revision, "health_after": a_after,
        "listeners": rehearsal.listener_ownership(),
        "evidence": evidence_inventory(state_directory),
    }
    write_json_atomic(evidence / "03-after-forced-failure.json", report)
    rollback = state.get("rollback_result") or {}
    passed = (code == 3 and state["status"] == "rolled_back" and rollback.get("status") == "rolled_back"
              and rollback.get("database_decision", {}).get("case") == 3
              and any("database" in step and step["database"] == "restore_swap" for step in rollback.get("steps", []))
              and rollback.get("candidate_processes_alive") == [] and report["database_now"] == [head_a]
              and kept_revision == [head_b] and a_after["pass"]
              and all(item["owned_by_task_lineage"] for item in report["listeners"].values()))
    return {"status": state["status"], "cause_gate": report["failed_gate"], "database_case": 3 if passed else rollback.get(
        "database_decision"), "database_now": report["database_now"], "kept": kept, "pass": passed}


def phase_backuptask(context: dict) -> dict:
    run1 = context["run1"]["rehearsal"]
    release = run1.releases / context["candidate"]
    root = context["root"] / "backuptask"
    task = f"{TASK_PREFIX}DatabaseBackup"
    at = (datetime.now().replace(second=0, microsecond=0)).strftime("%H:%M:%S")
    policy = backup_policy(root / "backup-policy.json", database=context["source_db"], env=context["source_env"],
                           root=root / "backups", schedule={"task_name": task, "daily_at_local_time": "03:17:00",
                                                            "execution_sid": "S-1-5-18",
                                                            "execution_time_limit_minutes": 60, "restart_count": 1,
                                                            "restart_interval_minutes": 5})
    evidence = context["evidence"] / "backuptask"
    evidence.mkdir(parents=True, exist_ok=True)
    installer = REPOSITORY / "ops" / "database" / "Install-DatabaseBackupTask.ps1"
    base = [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(installer),
            "-PolicyPath", str(policy), "-ReleaseDirectory", str(release), "-ControlRoot", str(run1.control)]
    preview = subprocess.run(base, capture_output=True, text=True)
    applied = subprocess.run(base + ["-Apply", "-Confirm:$false"], capture_output=True, text=True)
    powershell(f"Enable-ScheduledTask -TaskPath '\\' -TaskName '{task[1:]}' | Out-Null; "
               f"Start-ScheduledTask -TaskPath '\\' -TaskName '{task[1:]}'")
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline and WindowsTasks().state(task) == "Running":
        time.sleep(2)
    time.sleep(2)
    last_result = WindowsTasks().last_result(task)
    inspect = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                              str(REPOSITORY / "ops" / "database" / "Inspect-DatabaseBackupTask.ps1"), "-PolicyPath",
                              str(policy)], capture_output=True, text=True)
    executions = sorted((root / "backups" / "primary" / "executions").glob("*.json"))
    execution = read_json(executions[-1], "X") if executions else None
    unregister(task)
    report = {"preview_exit": preview.returncode, "preview": preview.stdout[-2000:], "apply_exit": applied.returncode,
              "apply": applied.stdout[-1000:] + applied.stderr[-1000:], "last_task_result": last_result,
              "inspect_exit": inspect.returncode, "inspect": inspect.stdout[-2000:], "execution": execution,
              "note": "03:17:00 is a rehearsal value only; no production backup time is defined"}
    write_json_atomic(evidence / "backup-task.json", report)
    return {"install_exit": applied.returncode, "last_task_result": last_result, "inspect_exit": inspect.returncode,
            "execution_result": None if execution is None else execution.get("result"),
            "pass": preview.returncode == 0 and applied.returncode == 0 and last_result == 0 and inspect.returncode == 0
            and execution is not None and execution.get("result") == "PASS"}


def phase_cleanup(context: dict) -> dict:
    report = {"rehearsals": {}, "env_files_deleted": []}
    for rehearsal in context["rehearsals"]:
        report["rehearsals"][rehearsal.label] = rehearsal.cleanup()
    if context.get("restore_target") and context.get("restore_evidence") and Path(context["restore_evidence"]).exists():
        code, dropped = cli("backup", "drop-rehearsal-target", "--policy", str(context["source_policy"]), "--target",
                            context["restore_target"], "--evidence", str(context["restore_evidence"]),
                            evidence=context["evidence"] / "cleanup-drop-restore-target.json")
        report["restore_target_dropped"] = {"exit": code, "target": context["restore_target"]}
    for env in context["env_files"]:
        if env.exists():
            env.unlink()
            report["env_files_deleted"].append(str(env))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--run-id", default=datetime.now().strftime("%d%H%M"))
    parser.add_argument("--phases", default="database,release,failure,backuptask,cleanup")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.candidate):
        raise SystemExit("TASK230_CANDIDATE_MALFORMED")
    hour = datetime.now().hour + datetime.now().minute / 60
    if 5.5 <= hour <= 7.0 or WindowsTasks().state("\\AgroSat_PROGRAM_R3_SentinelCycle") == "Running":
        raise SystemExit("TASK230_REHEARSAL_REFUSED_NEAR_PRODUCTION_SENTINEL_CYCLE")
    root = BASE / args.run_id
    root.mkdir(parents=True, exist_ok=False)
    context = {"run": args.run_id, "root": root, "evidence": args.evidence, "candidate": args.candidate, "ports": set(),
               "rehearsals": [], "env_files": [], "source_db": f"agrosat_task230_src_{args.run_id}"}
    results = {"run_id": args.run_id, "candidate": args.candidate, "started_at": now(), "phases": {}}
    phases = args.phases.split(",")
    try:
        for phase in [item for item in phases if item != "cleanup"]:
            log(f"phase {phase}")
            try:
                results["phases"][phase] = globals()[f"phase_{phase}"](context)
            except (Exception, SystemExit) as error:
                results["phases"][phase] = {"pass": False, "error": f"{type(error).__name__}: {error}"[:2000]}
            log(f"phase {phase}: pass={results['phases'][phase].get('pass')}")
            write_json_atomic(args.evidence / "summary.json", results)
    finally:
        if "cleanup" in phases:
            log("phase cleanup")
            results["phases"]["cleanup"] = phase_cleanup(context)
        results["finished_at"] = now()
        results["pass"] = all(item.get("pass", True) for name, item in results["phases"].items() if name != "cleanup")
        write_json_atomic(args.evidence / "summary.json", results)
    log(f"PASS={results['pass']}")
    return 0 if results["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
