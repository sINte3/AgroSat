"""An in-memory Windows host for exercising the release controller offline.

Scheduled Tasks, process trees, listeners, health endpoints and the database
revision are simulated faithfully enough to drive every gate: a Job-Object
launcher (schema 2 configuration) takes its whole tree with it when stopped,
while a legacy launcher (schema 1) leaves its children listening, exactly as
measured on the real host. Git, archives, manifests and release material are
real files in a temporary directory.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import itertools
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from controlplane import backup as backup_module
from controlplane.common import sha256_file, write_json_atomic
from controlplane.gitmaterial import extract_archive
from controlplane.health import HttpResult
from controlplane.profiles import TaskNames, Profile
from controlplane.tasks import TaskAction, TaskDefinition
from controlplane.winproc import ProcessIdentity

REPOSITORY = Path(__file__).resolve().parents[2]
THUMBPRINT = "816767BE400FE53327432B12B29FE4B5809CA4CA"
REVISIONS = ["0001_baseline_existing_schema", "0002_create_satellite_index_records", "0003_add_ndvi_unique_constraint",
             "478de3d1f6d0", "0004_repair_core_constraints", "0005_field_inspections", "0006_operational_closure",
             "0007_pixel_anomalies", "0008_irrigation_context", "0009_yield_map_imports", "0010_productivity_zones",
             "0011_variable_rate_recommendations", "0012_commercial_tenant_boundary",
             "0013_anomaly_inspection_workflow", "0014_autonomous_satellite_monitoring",
             "0015_closed_loop_agronomy", "0016_operational_command_center"]


PATHS = {item["revision"]: item["path"] for item in json.loads(
    (REPOSITORY / "ops" / "release" / "rollback-contract.json").read_text(encoding="utf-8"))["migrations"]}


def graph_until(head: str) -> dict[str, Any]:
    """The real revision chain up to ``head``, in ``alembic_graph`` shape (head first)."""
    chain = REVISIONS[:REVISIONS.index(head) + 1]
    return {"heads": [head], "revisions": [
        {"revision": revision, "down_revisions": [] if index == 0 else [chain[index - 1]],
         "dependencies": [], "branch_labels": [], "path": PATHS[revision]}
        for index, revision in reversed(list(enumerate(chain)))]}


@dataclass
class FakeTask:
    definition: TaskDefinition
    state: str = "Ready"
    engine: int | None = None


@dataclass
class Serving:
    sha: str
    head: str
    fault: bool
    dist_index: bytes | None = None
    backend_port: int | None = None


class FakeHost:
    def __init__(self, world: "World"):
        self.world = world
        self.tasks: dict[str, FakeTask] = {}
        self.processes: dict[int, ProcessIdentity] = {}
        self.ports: dict[int, int] = {}
        self.serving: dict[int, Serving] = {}
        self.revisions: list[str] = []
        self.pids = itertools.count(4000, 4)
        self.clock = 0.0
        self.base = datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc)
        self.actions_log: list[tuple] = []
        self.fail_alembic = False
        self.backups: dict[str, dict] = {}
        self.swaps: list[dict] = []
        self.legacy_terminated: list[int] = []
        self.crash_on: str | None = None

    # time
    def now(self):
        return self.base + timedelta(seconds=self.clock)

    def monotonic(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += seconds

    # tasks
    def task_definition(self, name):
        task = self.tasks.get(name)
        return None if task is None else task.definition

    def task_state(self, name):
        task = self.tasks.get(name)
        return None if task is None else task.state

    def _spawn(self, parent: int, name: str) -> ProcessIdentity:
        pid = next(self.pids)
        identity = ProcessIdentity(pid, parent, name, int(self.clock * 1000) + pid)
        self.processes[pid] = identity
        return identity

    def start_task(self, name):
        self.actions_log.append(("start", name))
        if self.crash_on == f"start:{name}":
            raise KeyboardInterrupt("simulated controller crash")
        task = self.tasks[name]
        if task.state == "Running":
            return
        engine = self._spawn(1588, "python.exe")
        task.engine, task.state = engine.pid, "Running"
        action = task.definition.actions[0]
        match = re.search(r'--configuration "([^"]+)" --component (\w+)', action.arguments)
        if not match:
            return  # worker tasks are not started by the controller
        config = json.loads(Path(match.group(1)).read_text(encoding="utf-8-sig"))
        component = match.group(2)
        launcher = self._spawn(engine.pid, "python.exe")
        sha = config["release_commit"]
        port = self.world.profile.backend_port if component == "backend" else self.world.profile.frontend_port
        if port in self.ports:
            # The launcher refuses a port it does not own (APPLICATION_LISTENER_ALREADY_OWNED) and exits 70.
            self._kill(launcher)
            self._kill(engine)
            task.state, task.engine = "Ready", None
            return
        child = self._spawn(launcher.pid, "python.exe" if component == "backend" else "node.exe")
        self.ports[port] = child.pid
        env_file = (config.get("rehearsal") or {}).get("runtime_env_file", "")
        release = self.world.profile.release_root / sha
        self.serving[port] = Serving(sha=sha, head=self.world.heads[sha], fault="fault" in env_file,
                                     dist_index=(release / "frontend/dist/index.html").read_bytes()
                                     if component == "frontend" else None,
                                     backend_port=self.world.profile.backend_port)

    def _descendants(self, pid: int) -> list[ProcessIdentity]:
        found, frontier = [], [pid]
        while frontier:
            parent = frontier.pop()
            for identity in list(self.processes.values()):
                if identity.ppid == parent and identity not in found:
                    found.append(identity)
                    frontier.append(identity.pid)
        return found

    def _kill(self, identity: ProcessIdentity) -> None:
        self.processes.pop(identity.pid, None)
        for port, owner in list(self.ports.items()):
            if owner == identity.pid:
                del self.ports[port]
                self.serving.pop(port, None)

    def stop_task(self, name):
        self.actions_log.append(("stop", name))
        task = self.tasks[name]
        if task.state != "Running":
            return
        tree = [self.processes[task.engine]] + self._descendants(task.engine)
        action = task.definition.actions[0]
        match = re.search(r'--configuration "([^"]+)"', action.arguments)
        legacy = bool(match) and json.loads(Path(match.group(1)).read_text(encoding="utf-8-sig")).get("schema_version") != 2
        # Task Scheduler ends the engine; the venv launcher's job ends the supervisor.
        # A Job-Object supervisor then takes its children; a legacy one leaves them running.
        for identity in (tree[:2] if legacy else tree):
            self._kill(identity)
        task.state, task.engine = "Ready", None

    def set_task_action(self, name, action):
        self.actions_log.append(("set_action", name, action.arguments))
        task = self.tasks[name]
        task.definition = replace(task.definition, actions=(action,))

    def set_task_enabled(self, name, enabled):
        task = self.tasks[name]
        task.definition = replace(task.definition, enabled=enabled)

    def task_engine_pids(self, name):
        task = self.tasks[name]
        return [task.engine] if task.engine is not None else []

    # processes
    def listeners(self, ports):
        return {port: self.ports.get(port) for port in ports}

    def lineage(self, anchor):
        return ([self.processes[anchor]] if anchor in self.processes else []) + self._descendants(anchor)

    def alive(self, identities):
        return [identity for identity in identities if self.processes.get(identity.pid) == identity]

    def terminate_verified(self, identity):
        if self.processes.get(identity.pid) != identity:
            return False
        self.legacy_terminated.append(identity.pid)
        self._kill(identity)
        return True

    # http
    def http_get(self, url):
        match = re.match(r"http://127\.0\.0\.1:(\d+)(/.*)$", url)
        port, path = int(match.group(1)), match.group(2)
        serving = self.serving.get(port)
        if serving is None:
            return HttpResult(None, b"", "URLError")
        if serving.dist_index is not None:  # frontend
            if path == "/":
                return HttpResult(200, serving.dist_index)
            return self.http_get(f"http://127.0.0.1:{serving.backend_port}{path}")
        if path == "/health/live":
            return HttpResult(200, json.dumps({"status": "alive", "release_revision": serving.sha}).encode())
        database = {"status": "ready", "migration_revision": self.revisions[0] if self.revisions else "unknown",
                    "expected_migration_revision": serving.head, "reason": None}
        if serving.fault:
            database.update(status="unavailable", migration_revision="unknown", reason="database_unreachable")
        elif self.revisions != [serving.head]:
            database.update(status="schema_mismatch", reason="database_behind_code")
        if serving.sha == self.world.current and self.world.extra.get("current_pre_task228", True):
            # Production 4cd8ea7 predates TASK_228: status and migration_revision only.
            database.pop("expected_migration_revision")
            if database["status"] == "schema_mismatch":
                database["status"] = "ready"  # the old readiness never compared revisions
        else:
            database["revision_match"] = database["status"] == "ready"
        ready = database["status"] == "ready"
        body = {"status": "ready" if ready else "not_ready", "release_revision": serving.sha,
                "components": {"database": database,
                               "collector": {"status": "missing", "required_for_api_readiness": False},
                               "cache": {"status": "unavailable", "required_for_api_readiness": False}}}
        return HttpResult(200 if ready else 503, json.dumps(body).encode())

    # database and migrations
    def database(self, env_file, database_name, pg_bin):
        host = self

        class Database:
            def revisions(self_inner, database=None):
                return list(host.revisions)

        return Database()

    def alembic_graph(self, python, backend_directory):
        return json.loads((Path(backend_directory) / "fake-graph.json").read_text(encoding="utf-8"))

    def run_alembic(self, python, backend_directory, env_file, arguments):
        self.actions_log.append(("alembic", *arguments))
        if self.fail_alembic:
            return subprocess.CompletedProcess(arguments, 1, "", "simulated failure")
        self.revisions = [arguments[1]]
        return subprocess.CompletedProcess(arguments, 0, "", "")

    def run(self, argv, *, cwd=None, timeout=600, env=None):
        text = " ".join(str(item) for item in argv)
        if "platform.python_version" in text:
            return subprocess.CompletedProcess(argv, 0, "3.14.5\n", "")
        if text.endswith("--version"):
            return subprocess.CompletedProcess(argv, 0, "v24.16.0\n", "")
        if "--validate-only" in text:
            component = argv[argv.index("--component") + 1]
            return subprocess.CompletedProcess(argv, 0, json.dumps({"status": "PASS", "component": component}) + "\n", "")
        if "-Mode" in text and "dry-run" in text:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected command: {text}")

    def pg_tool_version(self, target, tool):
        return "pg_dump (PostgreSQL) 16.14"

    # backups
    def backup_run(self, policy, *, reason, release_id=None):
        backup_id = f"{policy.database_name}-20260926T040000Z-{reason}-{len(self.backups):06x}"
        record = {"backup_id": backup_id, "file": {"sha256": hashlib.sha256(backup_id.encode()).hexdigest()},
                  "db_revision": self.revisions[0], "directory": str(policy.backup_root / backup_id)}
        self.backups[backup_id] = record
        return record

    def backup_load(self, policy, backup_id):
        return self.backups[backup_id]

    def backup_secondary(self, policy, backup_id):
        return {"verified": True}

    def backup_protection(self, policy, backup_id):
        return {"backup_id": backup_id, "primary_validated": True, "secondary_required": policy.secondary_required,
                "secondary_verified": False, "fully_protected": not policy.secondary_required}

    def backup_inventory(self, policy):
        return [{"name": key, "status": "validated", "sha256": value["file"]["sha256"]} for key, value in self.backups.items()]

    def backup_restore_swap(self, policy, backup_id, expected_revision, tag, evidence_directory):
        self.swaps.append({"backup_id": backup_id, "revision": expected_revision})
        self.revisions = [expected_revision]
        return {"backup_id": backup_id, "restored_revision": expected_revision, "data_destroyed": False}

    # signing
    def sign(self, paths, thumbprint):
        self.actions_log.append(("sign", len(paths)))

    def signatures(self, paths):
        return {str(path): {"status": "Valid", "thumbprint": THUMBPRINT} for path in paths}

    def validate_worker_config(self, common_script, function, configuration):
        json.loads(Path(configuration).read_text(encoding="utf-8-sig"))


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-C", str(repository), *arguments], check=True, capture_output=True,
                          text=True).stdout.strip()


def definition(name: str, kind: str, action: TaskAction) -> TaskDefinition:
    triggers = ()  # rehearsal tasks have no triggers
    limits = {"backend": ("PT0S", 3, "PT1M"), "frontend": ("PT0S", 3, "PT1M"), "sentinel": ("PT6H", 3, "PT15M"),
              "notifications": ("PT10M", 3, "PT5M"), "backup": ("PT2H", 2, "PT15M")}[kind]
    return TaskDefinition(name=name, principal_user="S-1-5-18", run_level="HighestAvailable",
                          multiple_instances="IgnoreNew", execution_time_limit=limits[0], restart_count=limits[1],
                          restart_interval=limits[2], start_when_available=True, enabled=True, triggers=triggers,
                          actions=(action,))


@dataclass
class World:
    root: Path
    profile: Profile
    repository: Path
    current: str
    candidate: str
    heads: dict[str, str]
    authorization: Path = None
    policy: Path = None
    host: FakeHost = None
    extra: dict = field(default_factory=dict)


def _commit_tree(repository: Path, head: str, marker: str, *, frontend: str = "v1") -> str:
    (repository / "backend").mkdir(exist_ok=True)
    (repository / "backend" / "requirements.txt").write_text("fastapi==0.137.0\n", encoding="utf-8")
    (repository / "backend" / "fake-graph.json").write_text(json.dumps(graph_until(head)), encoding="utf-8")
    (repository / "backend" / "main.py").write_text(f"# {marker}\n", encoding="utf-8")
    (repository / "frontend").mkdir(exist_ok=True)
    (repository / "frontend" / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3}), encoding="utf-8")
    (repository / "frontend" / "index.html").write_text(f"<title>{frontend}</title>\n", encoding="utf-8")
    for folder, names in (("ops/release", ("Run-AgroSatApplication.py", "agrosat_process_ownership.py")),
                          ("ops/windows-task", ("Invoke-Collector.ps1", "Task209-CollectorTask.Common.ps1",
                                                "Invoke-OperationalNotifications.ps1",
                                                "Task221-OperationalNotifications.Common.ps1")),
                          ("ops/qualification", ("Serve-ProgramR1QualificationFrontend.mjs",))):
        (repository / folder).mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copyfile(REPOSITORY / folder / name, repository / folder / name)
    git(repository, "add", "-A")
    git(repository, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", marker)
    return git(repository, "rev-parse", "HEAD")


def build_world(tmp_path: Path, *, current_head: str = "0016_operational_command_center",
                candidate_head: str = "0016_operational_command_center", frontend_changes: bool = False,
                legacy_current: bool = True, fault: str | None = None, backup_task: bool = False) -> World:
    root = tmp_path / "rehearsal"
    root.mkdir()
    (root / ".agrosat-rehearsal-root.json").write_text(json.dumps({"profile_id": "TASK230"}), encoding="utf-8")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    repository = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    git(repository, "config", "core.autocrlf", "false")
    git(repository, "remote", "add", "origin", str(origin))
    current = _commit_tree(repository, current_head, "current")
    candidate = _commit_tree(repository, candidate_head, "candidate", frontend="v2" if frontend_changes else "v1")
    git(repository, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(repository, "fetch", "-q", "origin")
    env_file = root / "env" / "rehearsal.env"
    env_file.parent.mkdir()
    env_file.write_text("DATABASE_URL=postgresql://user:pw@localhost:5432/agrosat_task230_rel\n", encoding="utf-8")
    profile = Profile(kind="rehearsal", profile_id="TASK230", release_root=root / "releases", runtime_root=root / "runtime",
                      control_root=root / "control", runtime_env_file=env_file, database_name="agrosat_task230_rel",
                      backend_port=58240, frontend_port=58241,
                      tasks=TaskNames(backend="\\AgroSat_TASK230_Backend", frontend="\\AgroSat_TASK230_Frontend",
                                      sentinel="\\AgroSat_TASK230_SentinelCycle",
                                      notifications="\\AgroSat_TASK230_OperationalNotifications"),
                      node_executable=Path(r"C:\Program Files\nodejs\node.exe"), pg_bin=Path(r"C:\pg"),
                      signing_thumbprints=(THUMBPRINT,), timezone_id="West Asia Standard Time", rehearsal_root=root,
                      backup_task="\\AgroSat_TASK230_DatabaseBackup" if backup_task else None,
                      fault_injection=fault, worker_dry_run=True)
    for directory in (profile.release_root, profile.runtime_root, profile.control_root):
        directory.mkdir()
    # The running (current) release, materialized the way production has it.
    release = profile.release_root / current
    subprocess.run(["git", "-C", str(repository), "archive", "--format=zip", "-o", str(tmp_path / "c.zip"), current],
                   check=True)
    extract_archive(tmp_path / "c.zip", release)
    (release / "backend" / "venv" / "Scripts").mkdir(parents=True)
    (release / "backend" / "venv" / "Scripts" / "python.exe").write_bytes(b"fake interpreter")
    (release / "frontend" / "dist").mkdir()
    (release / "frontend" / "dist" / "index.html").write_text("<title>dist v1</title>\n", encoding="utf-8")
    (release / "release-manifest.json").write_text(json.dumps({"schema_version": 1, "git_sha": current,
                                                               "alembic_head": current_head}), encoding="utf-8")
    runtime = profile.runtime_root / current
    application = runtime / "application"
    application.mkdir(parents=True)
    app_config = {"release_commit": current, "manifest_sha256": sha256_file(release / "release-manifest.json"),
                  "wrapper_sha256": "0" * 64, "runtime_directory": str(application)}
    if not legacy_current:
        app_config.update(schema_version=2, ownership_sha256="0" * 64, profile="rehearsal")
    write_json_atomic(application / "application-release.json", app_config)
    (runtime / "collector" / "state").mkdir(parents=True)
    (runtime / "collector" / "state" / "collector_state.json").write_text("{}", encoding="utf-8")
    scheduler = profile.runtime_root / "scheduler"
    scheduler.mkdir()
    sentinel_config = {"schema_version": 1, "task_name": profile.tasks.sentinel, "release_commit": current,
                       "release_manifest_sha256": app_config["manifest_sha256"], "working_directory": str(release),
                       "python_executable": str(release / "backend/venv/Scripts/python.exe"),
                       "runner_script": str(scheduler / "Invoke-Collector.ps1"),
                       "schedule": {"daily_at_local_times": ["06:00:00"]},
                       "collector": {"output_directory": str(runtime / "collector/runs"),
                                     "state_directory": str(runtime / "collector/state"),
                                     "lock_directory": str(runtime / "collector/locks")}}
    write_json_atomic(scheduler / "collector-task.json", sentinel_config)
    notifications = runtime / "operational-notifications"
    notifications.mkdir()
    notification_config = {"schema_version": 1, "task_name": profile.tasks.notifications, "release_commit": current,
                           "release_manifest_sha256": app_config["manifest_sha256"], "working_directory": str(release),
                           "python_executable": str(release / "backend/venv/Scripts/python.exe"),
                           "runner_script": str(notifications / "Invoke-OperationalNotifications.ps1"),
                           "schedule": {"interval_minutes": 15},
                           "reconciliation": {"limit": 200, "output_directory": str(notifications)}}
    write_json_atomic(notifications / "operational-notifications-task.json", notification_config)
    world = World(root=root, profile=profile, repository=repository, current=current, candidate=candidate,
                  heads={current: current_head, candidate: candidate_head})
    host = FakeHost(world)
    host.revisions = [current_head]
    python = str(release / "backend" / "venv" / "Scripts" / "python.exe")
    actions = {
        "backend": TaskAction(python, f'-B "{application / "Run-AgroSatApplication.py"}" --configuration '
                                      f'"{application / "application-release.json"}" --component backend', str(release)),
        "frontend": TaskAction(python, f'-B "{application / "Run-AgroSatApplication.py"}" --configuration '
                                       f'"{application / "application-release.json"}" --component frontend', str(release)),
        "sentinel": TaskAction("powershell.exe", f'-NoProfile -NonInteractive -ExecutionPolicy AllSigned -File '
                               f'"{scheduler / "Invoke-Collector.ps1"}" -ConfigurationPath '
                               f'"{scheduler / "collector-task.json"}" -Mode apply', str(release)),
        "notifications": TaskAction("powershell.exe", f'-NoProfile -NonInteractive -ExecutionPolicy AllSigned -File '
                                    f'"{notifications / "Invoke-OperationalNotifications.ps1"}" -ConfigurationPath '
                                    f'"{notifications / "operational-notifications-task.json"}" -Mode apply', str(release)),
    }
    for kind, name in profile.tasks.all().items():
        host.tasks[name] = FakeTask(definition(name, kind, actions[kind]))
    if backup_task:
        backup_action = TaskAction(python, f'-B "{release / "ops" / "release" / "Invoke-AgroSatControlPlane.py"}" backup '
                                           f'scheduled --policy "{tmp_path / "policy.json"}" --control-root '
                                           f'"{profile.control_root}"', str(release))
        host.tasks[profile.backup_task] = FakeTask(definition(profile.backup_task, "backup", backup_action))
    for kind in ("backend", "frontend"):
        host.start_task(profile.tasks.all()[kind])
    world.host = host
    backups = root / "backups"
    backups.mkdir()
    policy = {"schema_version": 1, "kind": "agrosat_database_backup_policy", "example_only": False,
              "database_name": profile.database_name, "source_classification": "rehearsal",
              "runtime_env_file": str(env_file), "pg_bin": str(profile.pg_bin), "backup_root": str(backups),
              "retention": None, "secondary": {"required": False, "destination": None}, "schedule": None}
    world.policy = tmp_path / "policy.json"
    world.policy.write_text(json.dumps(policy), encoding="utf-8")
    return world


def authorize(world: World, *, operation: str = "release", release_id: str = "R230-rehearsal-001",
              candidate: str | None = None, current: str | None = None, migration=None,
              strategy: str = "none", backup_sha256: str | None = None, hours: float = 4,
              created: datetime | None = None, **overrides) -> Path:
    created = created or world.host.now() - timedelta(minutes=1)
    document = {"schema_version": 1, "kind": "agrosat_release_authorization", "operation": operation,
                "release_id": release_id, "candidate_sha": candidate or world.candidate,
                "expected_current_sha": current or world.current, "database_name": world.profile.database_name,
                "migration": migration or {"authorized": False, "from_revision": None, "to_revision": None},
                "database_rollback_strategy": strategy, "restore_backup_sha256": backup_sha256,
                "created_at": created.isoformat(), "expires_at": (created + timedelta(hours=hours)).isoformat(),
                "authorized_by": "TASK_230 test operator", **overrides}
    path = world.root.parent / f"authorization-{release_id}-{operation}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    world.authorization = path
    return path
