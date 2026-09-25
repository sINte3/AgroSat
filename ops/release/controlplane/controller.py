"""Release and rollback state machines (TASK_230 Parts F, G, H, I, K).

Release gates, in order, each producing machine-readable evidence:

    PRECHECK -> BACKUP -> MATERIALIZE -> VALIDATE -> MIGRATION_PLAN -> MIGRATE
    -> SWITCH_BACKEND -> VERIFY_BACKEND -> SWITCH_FRONTEND -> VERIFY_FRONTEND
    -> REBIND_WORKERS -> VERIFY_WORKERS -> FINAL_HEALTH -> COMMIT

Nothing continues after a failed gate. A failure before any service or the
database changed ends the release as ``failed``. A failure from
SWITCH_BACKEND on, or after a migration started, runs the automatic rollback:
the application, frontend and workers return to the exact recorded previous
task actions, and the database follows the rollback contract (case 1 nothing,
case 2 authorized downgrade of reversible steps only, case 3 authorized
non-destructive restore swap, otherwise MANUAL_RECOVERY_REQUIRED). Every gate
is written to resume: it inspects the recorded state and the live system and
never repeats a completed switch, a completed migration, or starts a second
listener.

The explicit rollback operation follows the same identity rules with its own
gates (PRECHECK, ROLLBACK_PLAN, BACKUP, DATABASE, then the switch, verify and
commit gates) and binds tasks back to the actions recorded when the release
being undone replaced its predecessor.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Callable

from . import backup as backup_module
from . import health, manifest as manifest_module, migration
from .authorization import identity_of, validate_authorization
from .common import (
    ControlPlaneError, assert_no_reparse_points, is_reparse_point, iso, read_json, sha256_file,
    write_json_atomic, write_json_immutable,
)
from .gitmaterial import Git, extract_archive
from .profiles import Profile
from .state import ROLLBACK_GATES, RELEASE_GATES, ReleaseState, controller_lock, register_terminal, \
    used_release_ids, write_summary
from .tasks import TaskAction, TaskDefinition, contract_violations, rebind_violations
from .winproc import ProcessIdentity

APPLICATION_KINDS = ("backend", "frontend")
WORKER_KINDS = ("sentinel", "notifications")
MUTATION_GATES = ("SWITCH_BACKEND", "VERIFY_BACKEND", "SWITCH_FRONTEND", "VERIFY_FRONTEND", "REBIND_WORKERS",
                  "VERIFY_WORKERS", "FINAL_HEALTH", "COMMIT", "DATABASE")
WORKER_FILES = {
    "sentinel": ("scheduler", "Invoke-Collector.ps1", "Task209-CollectorTask.Common.ps1", "collector-task.json",
                 "Get-Task209CollectorConfiguration"),
    "notifications": ("operational-notifications", "Invoke-OperationalNotifications.ps1",
                      "Task221-OperationalNotifications.Common.ps1", "operational-notifications-task.json",
                      "Get-Task221OperationalNotificationsConfiguration"),
}
CONFIGURATION_ARGUMENT = re.compile(r'(?:--configuration|-ConfigurationPath)\s+"([^"]+)"')


@dataclass(frozen=True)
class Deadlines:
    stop_seconds: float = 60.0
    legacy_handoff_seconds: float = 30.0
    listener_seconds: float = 180.0
    health_seconds: float = 180.0
    worker_idle_seconds: float = 600.0
    poll_seconds: float = 0.5


@dataclass(frozen=True)
class Request:
    operation: str
    mode: str
    release_id: str
    candidate_sha: str
    expected_current_sha: str
    authorization_path: Path
    authorization_sha256: str | None
    profile: Profile
    repository: Path | None
    backup_policy_path: Path | None
    fetch: bool = True


class GateStop(Exception):
    """Raised after a failed gate has been recorded and handled."""


def configuration_path(action: TaskAction) -> Path:
    match = CONFIGURATION_ARGUMENT.search(action.arguments)
    if not match:
        raise ControlPlaneError("TASK_ACTION_CONFIGURATION_UNRESOLVED")
    return Path(match.group(1))


def write_json_with_bom(path: Path, value: Any) -> None:
    """Worker configurations are read by Windows PowerShell 5.1: UTF-8 with BOM."""
    text = json.dumps(value, indent=4, ensure_ascii=False) + "\r\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(b"\xef\xbb\xbf" + text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))


class Controller:
    def __init__(self, request: Request, platform, *, deadlines: Deadlines = Deadlines(), now=None):
        self.request = request
        self.profile = request.profile
        self.platform = platform
        self.deadlines = deadlines
        self.now = now
        self.authorization: dict[str, Any] | None = None
        self.policy = None

    # ================================================================ entry
    def run(self) -> dict[str, Any]:
        request, profile = self.request, self.profile
        if request.operation not in ("release", "rollback") or request.mode != profile.kind:
            raise ControlPlaneError("CONTROLLER_REQUEST_REJECTED", "mode must match the target profile")
        if profile.production and not request.authorization_sha256:
            raise ControlPlaneError("PRODUCTION_AUTHORIZATION_SHA256_REQUIRED",
                                    "production runs require the authorization file's SHA-256 on the command line")
        forbidden = [profile.release_root, profile.runtime_root]
        if request.repository is not None:
            forbidden.append(request.repository)

        def authorize() -> dict[str, Any]:
            return validate_authorization(
                request.authorization_path, operation=request.operation, release_id=request.release_id,
                candidate_sha=request.candidate_sha, expected_current_sha=request.expected_current_sha,
                database_name=profile.database_name, forbidden_roots=forbidden,
                used_release_ids=used_release_ids(profile.control_root), now=self.now,
                expected_sha256=request.authorization_sha256)

        # Read-only first: a rejected authorization never creates or locks anything.
        authorize()
        with controller_lock(profile.control_root):
            self.authorization = authorize()  # again under the lock: replay cannot race
            if request.backup_policy_path is not None:
                self.policy = backup_module.load_policy(request.backup_policy_path)
            identity = identity_of(self.authorization, mode=request.mode, profile_id=profile.profile_id)
            gates = RELEASE_GATES if request.operation == "release" else ROLLBACK_GATES
            state = ReleaseState.open(profile.control_root, request.release_id, identity, gates,
                                      operation=request.operation)
            state.event("authorized", authorization_sha256=self.authorization["sha256"],
                        expires_at=self.authorization["expires_at"])
            try:
                for gate in state.pending_gates():
                    self._run_gate(state, gate)
                state.finish("completed")
                register_terminal(profile.control_root, state)
            except GateStop:
                pass
            finally:
                write_summary(state)
            return state.data

    def _run_gate(self, state: ReleaseState, gate: str) -> None:
        state.begin(gate)
        try:
            evidence = getattr(self, "gate_" + gate.lower())(state)
        except ControlPlaneError as error:
            state.record(gate, "failed", {"error": error.evidence()})
            self._handle_failure(state, gate, error)
            raise GateStop from None
        state.record(gate, "passed", evidence)
        state.advance(gate)

    def _handle_failure(self, state: ReleaseState, gate: str, error: ControlPlaneError) -> None:
        mutated = gate in MUTATION_GATES or state.data["migration"]["started"]
        if self.request.operation == "rollback":
            state.finish("manual_recovery_required" if mutated else "failed")
        elif mutated:
            self._automatic_rollback(state, gate, error)
        else:
            state.finish("failed")
        register_terminal(self.profile.control_root, state)

    # ================================================================ helpers
    def _database(self):
        return self.platform.database(self.profile.runtime_env_file, self.profile.database_name, self.profile.pg_bin)

    def _release_dir(self, sha: str) -> Path:
        return self.profile.release_root / sha

    def _runtime_dir(self, sha: str) -> Path:
        return self.profile.runtime_root / sha

    def _python(self, sha: str) -> Path:
        return self._release_dir(sha) / "backend" / "venv" / "Scripts" / "python.exe"

    def _port(self, kind: str) -> int:
        return self.profile.backend_port if kind == "backend" else self.profile.frontend_port

    def _task(self, kind: str) -> str:
        if kind == "backup":
            return self.profile.backup_task
        return self.profile.tasks.all()[kind]

    def _workers(self, state: ReleaseState) -> tuple[str, ...]:
        """Worker kinds bound to the release: always Sentinel and notifications, backup when installed."""
        return tuple(state.data["facts"].get("bound_workers", WORKER_KINDS))

    def _wait(self, predicate: Callable[[], Any], seconds: float) -> tuple[bool, float]:
        started = self.platform.monotonic()
        while True:
            if predicate():
                return True, round(self.platform.monotonic() - started, 3)
            if self.platform.monotonic() - started >= seconds:
                return False, round(self.platform.monotonic() - started, 3)
            self.platform.sleep(self.deadlines.poll_seconds)

    def _definition(self, kind: str) -> TaskDefinition:
        definition = self.platform.task_definition(self._task(kind))
        if definition is None:
            raise ControlPlaneError("TASK_MISSING", self._task(kind))
        return definition

    def application_action(self, sha: str, kind: str) -> TaskAction:
        application = self._runtime_dir(sha) / "application"
        return TaskAction(str(self._python(sha)),
                          f'-B "{application / "Run-AgroSatApplication.py"}" --configuration '
                          f'"{application / "application-release.json"}" --component {kind}',
                          str(self._release_dir(sha)))

    def worker_action(self, sha: str, kind: str) -> TaskAction:
        folder, runner, _, config, _ = WORKER_FILES[kind]
        directory = self._runtime_dir(sha) / folder
        return TaskAction("powershell.exe",
                          f'-NoProfile -NonInteractive -ExecutionPolicy AllSigned -File "{directory / runner}" '
                          f'-ConfigurationPath "{directory / config}" -Mode apply',
                          str(self._release_dir(sha)))

    def target_action(self, state: ReleaseState, kind: str) -> TaskAction:
        if self.request.operation == "release":
            sha = state.data["candidate_sha"]
            if kind == "backup":
                # Same backup command and policy, run from the new immutable release.
                before = TaskAction(**state.data["facts"]["tasks_before"]["backup"])
                old, new = str(self._release_dir(state.data["previous_sha"])), str(self._release_dir(sha))
                return TaskAction(before.command.replace(old, new), before.arguments.replace(old, new),
                                  before.working_directory.replace(old, new))
            return self.application_action(sha, kind) if kind in APPLICATION_KINDS else self.worker_action(sha, kind)
        recorded = state.read_snapshot("target-bindings.json")[kind]
        return TaskAction(**recorded)

    def bound_release(self, kind: str, definition: TaskDefinition) -> str:
        """The release a task is bound to, read from its configuration."""
        if kind == "backup":
            return Path(definition.actions[0].working_directory).name
        config = read_json(configuration_path(definition.actions[0]), "TASK_CONFIGURATION_UNREADABLE")
        sha = config.get("release_commit")
        if kind in WORKER_KINDS and definition.actions[0].working_directory != str(self._release_dir(sha or "")):
            raise ControlPlaneError("TASK_BINDING_INCONSISTENT", kind)
        return sha

    # ================================================================ PRECHECK
    def gate_precheck(self, state: ReleaseState) -> dict[str, Any]:
        profile, current = self.profile, state.data["previous_sha"]
        for root in (profile.release_root, profile.runtime_root):
            if not root.is_dir() or is_reparse_point(root):
                raise ControlPlaneError("PROFILE_ROOT_UNAVAILABLE", str(root))
        if not profile.runtime_env_file.is_file():
            raise ControlPlaneError("RUNTIME_ENV_FILE_MISSING")
        definitions, bindings, violations = {}, {}, {}
        workers = WORKER_KINDS
        if profile.backup_task and self.platform.task_definition(profile.backup_task) is not None:
            workers = WORKER_KINDS + ("backup",)
        state.data["facts"]["bound_workers"] = list(workers)
        for kind in APPLICATION_KINDS + workers:
            definition = self._definition(kind)
            definitions[kind] = definition
            problems = contract_violations(definition, kind, production=profile.production)
            if problems:
                violations[kind] = problems
            bindings[kind] = self.bound_release(kind, definition)
        if violations:
            raise ControlPlaneError("TASK_CONTRACT_VIOLATION", facts=violations)
        wrong = {kind: sha for kind, sha in bindings.items() if sha != current}
        if wrong:
            raise ControlPlaneError("CURRENT_RELEASE_MISMATCH", "the running release is not the authorized current SHA",
                                    bindings=wrong)
        pointer_path = profile.control_root / "current-release.json"
        if pointer_path.exists() and read_json(pointer_path, "CURRENT_POINTER_UNREADABLE").get("sha") != current:
            raise ControlPlaneError("CURRENT_POINTER_MISMATCH")
        previous = manifest_module.read_release_identity(self._release_dir(current))
        previous_graph = self.platform.alembic_graph(self._python(current), self._release_dir(current) / "backend")
        previous_head = migration.single_head(previous_graph)
        state.data["facts"]["previous"] = {"sha": current, "alembic_head": previous_head,
                                           "manifest_sha256": previous["manifest_sha256"],
                                           "dist_index_sha256": sha256_file(self._release_dir(current) / "frontend" / "dist" / "index.html")}
        if self.platform.task_state(self._task("sentinel")) == "Running":
            raise ControlPlaneError("SENTINEL_CYCLE_RUNNING", "never release during a collection cycle")
        idle, waited = self._wait(lambda: self.platform.task_state(self._task("notifications")) != "Running",
                                  self.deadlines.worker_idle_seconds)
        if not idle:
            raise ControlPlaneError("NOTIFICATIONS_RUN_NOT_FINISHING")
        state.snapshot("tasks-before.json", {kind: definition.evidence() for kind, definition in definitions.items()})
        state.snapshot("worker-configs-before.json", {
            kind: read_json(configuration_path(definitions[kind].actions[0]), "TASK_CONFIGURATION_UNREADABLE")
            for kind in WORKER_KINDS})
        state.data["facts"]["tasks_before"] = {kind: definition.actions[0].evidence()
                                               for kind, definition in definitions.items()}
        health_now = {
            "backend": health.probe_backend(profile.backend_port, current, previous_head, get=self.platform.http_get,
                                            pre_task228_compatible=True),
            "frontend": health.probe_frontend(profile.frontend_port, state.data["facts"]["previous"]["dist_index_sha256"],
                                              current, get=self.platform.http_get),
        }
        evidence: dict[str, Any] = {
            "profile": profile.evidence(), "authorization_sha256": self.authorization["sha256"],
            "current_bindings": bindings, "previous_release": state.data["facts"]["previous"],
            "task_contracts": "all_pass", "previous_health": {kind: value["pass"] for kind, value in health_now.items()},
            "notifications_idle_wait_seconds": waited,
        }
        if self.request.operation == "release":
            if self.request.repository is None:
                raise ControlPlaneError("RELEASE_REPOSITORY_REQUIRED")
            identity = manifest_module.source_identity(Git(self.request.repository), candidate_sha=state.data["candidate_sha"],
                                                       expected_current_sha=current, fetch=self.request.fetch)
            state.data["facts"]["source"] = identity
            evidence["source_identity"] = identity
        else:
            evidence["rollback_target"] = self._precheck_rollback_target(state)
        state.save()
        evidence["summary"] = {"current": current, "candidate": state.data["candidate_sha"],
                               "previous_healthy": all(value["pass"] for value in health_now.values())}
        return evidence

    def _precheck_rollback_target(self, state: ReleaseState) -> dict[str, Any]:
        target, current = state.data["candidate_sha"], state.data["previous_sha"]
        identity = manifest_module.read_release_identity(self._release_dir(target))
        source = None
        for child in sorted((self.profile.control_root / "releases").iterdir()):
            record_path = child / "state.json"
            if not record_path.exists() or child.name == state.data["release_id"]:
                continue
            record = read_json(record_path, "RELEASE_STATE_UNREADABLE")
            if record.get("operation") == "release" and record.get("status") == "completed" \
                    and record.get("candidate_sha") == current and record.get("previous_sha") == target:
                source = child
        if source is None:
            raise ControlPlaneError("ROLLBACK_TARGET_BINDINGS_UNKNOWN",
                                    "no completed release recorded the target's task actions")
        before = read_json(source / "snapshots" / "tasks-before.json", "RELEASE_SNAPSHOT_MISSING")
        bindings = {kind: {"command": before[kind]["actions"][0]["command"],
                           "arguments": before[kind]["actions"][0]["arguments"],
                           "working_directory": before[kind]["actions"][0]["working_directory"]}
                    for kind in APPLICATION_KINDS + WORKER_KINDS + ("backup",) if kind in before}
        state.snapshot("target-bindings.json", bindings)
        graph = self.platform.alembic_graph(self._python(target), self._release_dir(target) / "backend")
        state.data["facts"]["target"] = {"sha": target, "alembic_head": migration.single_head(graph),
                                         "manifest_sha256": identity["manifest_sha256"], "recorded_by": source.name,
                                         "dist_index_sha256": sha256_file(self._release_dir(target) / "frontend" / "dist" / "index.html")}
        return state.data["facts"]["target"]

    # ================================================================ BACKUP
    def gate_backup(self, state: ReleaseState) -> dict[str, Any]:
        if self.policy is None:
            raise ControlPlaneError("BACKUP_POLICY_REQUIRED", "a validated backup precedes every release and rollback")
        if self.policy.database_name != self.profile.database_name or \
                str(self.policy.runtime_env_file).lower() != str(self.profile.runtime_env_file).lower():
            raise ControlPlaneError("BACKUP_POLICY_TARGET_MISMATCH")
        recorded = state.data.get("backup")
        if recorded:
            metadata = self.platform.backup_load(self.policy, recorded["backup_id"])  # resume: re-verified
        else:
            metadata = self.platform.backup_run(
                self.policy, reason="pre_release" if self.request.operation == "release" else "pre_rollback",
                release_id=self.request.release_id)
        protection = None
        if self.policy.secondary_destination is not None and not (Path(metadata["directory"]) / "secondary.json").exists():
            protection = self.platform.backup_secondary(self.policy, metadata["backup_id"])
        status = self.platform.backup_protection(self.policy, metadata["backup_id"])
        if self.policy.secondary_required and not status["fully_protected"]:
            raise ControlPlaneError("BACKUP_NOT_FULLY_PROTECTED")
        state.data["backup"] = {"backup_id": metadata["backup_id"], "sha256": metadata["file"]["sha256"],
                                "db_revision": metadata["db_revision"], "directory": metadata["directory"],
                                "fully_protected": status["fully_protected"]}
        state.save()
        return {"required": True, "reason": "every release and rollback is preceded by a validated backup",
                "backup": state.data["backup"], "secondary": protection, "protection": status,
                "summary": {"backup_id": metadata["backup_id"], "db_revision": metadata["db_revision"]}}

    # ================================================================ MATERIALIZE
    def gate_materialize(self, state: ReleaseState) -> dict[str, Any]:
        candidate, current = state.data["candidate_sha"], state.data["previous_sha"]
        git = Git(self.request.repository)
        release = self._release_dir(candidate)
        evidence: dict[str, Any] = {}
        if release.exists():
            existing = read_json(release / "release-manifest.json", "IMMUTABLE_RELEASE_CONFLICT")
            manifest_module.validate_manifest(existing, candidate_sha=candidate)
            git.verify_worktree_material(candidate, release)
            evidence["release"] = {"directory": str(release), "reused_existing": True}
        else:
            evidence["release"] = self._materialize_release(state, git, release)
        manifest_sha256 = sha256_file(release / "release-manifest.json")
        state.data["facts"]["candidate"] = {"manifest_sha256": manifest_sha256,
                                            "alembic_head": read_json(release / "release-manifest.json",
                                                                      "RELEASE_MANIFEST_UNREADABLE")["alembic"]["head"],
                                            "dist_index_sha256": sha256_file(release / "frontend" / "dist" / "index.html")}
        runtime = self._runtime_dir(candidate)
        if runtime.exists():
            inventory = state.data["facts"].get("runtime_inventory")
            if inventory is None or self._inventory(runtime) != inventory:
                raise ControlPlaneError("RUNTIME_DIRECTORY_EXISTS_UNOWNED", str(runtime))
            evidence["runtime"] = {"directory": str(runtime), "reused_existing": True}
        else:
            evidence["runtime"] = self._materialize_runtime(state, release, runtime, manifest_sha256, current)
        state.save()
        evidence["summary"] = {"release": str(release), "runtime": str(runtime), "manifest_sha256": manifest_sha256}
        return evidence

    def _clean_own_staging(self, staging: Path) -> None:
        if staging.exists():
            assert_no_reparse_points(staging, "STAGING_REPARSE_POINT")
            shutil.rmtree(staging)

    def _materialize_release(self, state: ReleaseState, git: Git, release: Path) -> dict[str, Any]:
        candidate, current = state.data["candidate_sha"], state.data["previous_sha"]
        source = state.data["facts"]["source"]
        staging = self.profile.release_root / ".staging" / f"{candidate}-{state.data['release_id']}"
        self._clean_own_staging(staging)
        archive_path = state.directory / "material" / f"source-{candidate[:12]}.zip"
        if archive_path.exists():
            archive_path.unlink()
        archive = git.archive(candidate, archive_path)
        files = extract_archive(archive_path, staging)
        previous = self._release_dir(current)
        if source["requirements_blob_sha"] != source["current_requirements_blob_sha"]:
            raise ControlPlaneError("VENV_REBUILD_REQUIRED",
                                    "backend/requirements.txt changed; provide a qualified venv release first")
        shutil.copytree(previous / "backend" / "venv", staging / "backend" / "venv")
        if source["frontend_tree_sha"] == source["current_frontend_tree_sha"]:
            shutil.copytree(previous / "frontend" / "dist", staging / "frontend" / "dist")
            dist = {"origin": "copied_from_previous_release", "reason": "frontend tree unchanged"}
        else:
            dist = self._build_frontend(staging)
        python = staging / "backend" / "venv" / "Scripts" / "python.exe"
        graph = self.platform.alembic_graph(python, staging / "backend")
        manifest = manifest_module.build_manifest(
            identity=source, archive=archive, graph=graph,
            runtime_contract=manifest_module.load_runtime_contract(), release_id=state.data["release_id"])
        write_json_immutable(staging / "release-manifest.json", manifest)
        verification = git.verify_worktree_material(candidate, staging)
        os.replace(staging, release)
        return {"directory": str(release), "archive": archive, "extracted_files": files, "venv": "copied_from_previous_release",
                "dist": dist, "alembic_head": manifest["alembic"]["head"], "verification": verification}

    def _build_frontend(self, staging: Path) -> dict[str, Any]:
        workspace = Path(os.environ.get("SystemDrive", "C:") + "\\") / "agsb" / staging.name[:12]
        self._clean_own_staging(workspace)
        shutil.copytree(staging / "frontend", workspace)
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if npm is None:
            raise ControlPlaneError("NPM_UNAVAILABLE")
        for arguments in (["ci", "--no-audit", "--no-fund"], ["run", "build"]):
            result = self.platform.run([npm, *arguments], cwd=workspace, timeout=1800)
            if result.returncode != 0:
                raise ControlPlaneError("FRONTEND_BUILD_FAILED", " ".join(arguments))
        shutil.copytree(workspace / "dist", staging / "frontend" / "dist")
        assert_no_reparse_points(workspace / "dist", "STAGING_REPARSE_POINT")
        shutil.rmtree(workspace, ignore_errors=False)
        return {"origin": "built_from_candidate", "command": "npm ci && npm run build",
                "index_sha256": sha256_file(staging / "frontend" / "dist" / "index.html")}

    @staticmethod
    def _inventory(root: Path) -> dict[str, str]:
        return {str(path.relative_to(root)).replace("\\", "/"): sha256_file(path)
                for path in sorted(root.rglob("*")) if path.is_file() and path.suffix in (".py", ".ps1", ".json")
                and "collector" not in path.relative_to(root).parts[:1]}

    def _materialize_runtime(self, state: ReleaseState, release: Path, runtime: Path, manifest_sha256: str,
                             current: str) -> dict[str, Any]:
        candidate = state.data["candidate_sha"]
        staging = self.profile.runtime_root / ".staging" / f"{candidate}-{state.data['release_id']}"
        self._clean_own_staging(staging)
        application = staging / "application"
        application.mkdir(parents=True)
        for name in ("Run-AgroSatApplication.py", "agrosat_process_ownership.py"):
            shutil.copyfile(release / "ops" / "release" / name, application / name)
        env_file = self.profile.runtime_env_file
        fault = None
        if self.profile.fault_injection == "backend_unready_after_switch":
            # Rehearsal only: this release's backend reads a database that does not exist.
            fault_env = staging / "fault" / "backend-unready.env"
            fault_env.parent.mkdir(parents=True)
            text, count = re.subn(r"(DATABASE_URL=\S+/)" + re.escape(self.profile.database_name) + r"\b",
                                  r"\g<1>" + self.profile.database_name + "_fault_absent",
                                  env_file.read_text(encoding="utf-8-sig"))
            if count != 1:
                raise ControlPlaneError("REHEARSAL_FAULT_INJECTION_UNAPPLIED")
            fault_env.write_text(text, encoding="utf-8")
            env_file, fault = runtime / "fault" / "backend-unready.env", "backend_unready_after_switch"
        config: dict[str, Any] = {
            "schema_version": 2, "release_commit": candidate, "manifest_sha256": manifest_sha256,
            "wrapper_sha256": sha256_file(application / "Run-AgroSatApplication.py"),
            "ownership_sha256": sha256_file(application / "agrosat_process_ownership.py"),
            "runtime_directory": str(runtime / "application"),
            "profile": "production" if self.profile.production else "rehearsal",
        }
        if not self.profile.production:
            config["rehearsal"] = {"release_root": str(self.profile.release_root),
                                   "runtime_root": str(self.profile.runtime_root),
                                   "runtime_env_file": str(env_file), "backend_port": self.profile.backend_port,
                                   "frontend_port": self.profile.frontend_port,
                                   "node_executable": str(self.profile.node_executable)}
        write_json_atomic(application / "application-release.json", config)
        collector = staging / "collector"
        for name in ("runs", "state", "locks"):
            (collector / name).mkdir(parents=True)
        previous_state = self._runtime_dir(current) / "collector" / "state"
        copied_state = []
        if previous_state.is_dir():
            for item in sorted(previous_state.glob("*.json")):
                shutil.copyfile(item, collector / "state" / item.name)
                copied_state.append(item.name)
        before = state.read_snapshot("worker-configs-before.json")
        signed = []
        for kind in WORKER_KINDS:
            folder, runner, common, config_name, _ = WORKER_FILES[kind]
            directory = staging / folder
            directory.mkdir(parents=True, exist_ok=True)
            for name in (runner, common):
                shutil.copyfile(release / "ops" / "windows-task" / name, directory / name)
                signed.append(directory / name)
            final_directory = runtime / folder
            document = dict(before[kind])
            document.update({
                "release_commit": candidate, "release_manifest_sha256": manifest_sha256,
                "working_directory": str(release),
                "python_executable": str(release / "backend" / "venv" / "Scripts" / "python.exe"),
                "runner_script": str(final_directory / runner),
            })
            if kind == "sentinel":
                document["collector"] = dict(document["collector"], **{
                    "output_directory": str(runtime / "collector" / "runs"),
                    "state_directory": str(runtime / "collector" / "state"),
                    "lock_directory": str(runtime / "collector" / "locks")})
            else:
                document["reconciliation"] = dict(document["reconciliation"], output_directory=str(final_directory))
            write_json_with_bom(directory / config_name, document)
        self.platform.sign(signed, self.profile.signing_thumbprints[0])
        state.data["facts"]["runtime_inventory"] = self._inventory(staging)
        state.save()
        os.replace(staging, runtime)
        return {"directory": str(runtime), "application_config": config, "collector_state_copied": copied_state,
                "signed_runners": [str(path.relative_to(staging)) for path in signed], "fault_injection": fault}

    # ================================================================ VALIDATE
    def gate_validate(self, state: ReleaseState) -> dict[str, Any]:
        candidate = state.data["candidate_sha"]
        release, runtime = self._release_dir(candidate), self._runtime_dir(candidate)
        document = read_json(release / "release-manifest.json", "RELEASE_MANIFEST_UNREADABLE")
        manifest_module.validate_manifest(document, candidate_sha=candidate,
                                          expected_current_sha=state.data["previous_sha"])
        verification = Git(self.request.repository).verify_worktree_material(candidate, release)
        python = self._python(candidate)
        version = self.platform.run([python, "-c", "import platform; print(platform.python_version())"]).stdout.strip()
        node = self.platform.run([self.profile.node_executable, "--version"]).stdout.strip()
        pg_dump = self.platform.pg_tool_version(self._database(), "pg_dump")
        lockfile = read_json(release / "frontend" / "package-lock.json", "PACKAGE_LOCK_UNREADABLE").get("lockfileVersion")
        runtime_check = manifest_module.check_runtime(document["runtime_contract"], python_version=version,
                                                      node_version=node, pg_dump_version=pg_dump,
                                                      lockfile_version=lockfile)
        if not runtime_check["pass"]:
            raise ControlPlaneError("RUNTIME_CONTRACT_MISMATCH", facts=runtime_check)
        launcher = {}
        for kind in APPLICATION_KINDS:
            result = self.platform.run([python, "-B", runtime / "application" / "Run-AgroSatApplication.py",
                                        "--configuration", runtime / "application" / "application-release.json",
                                        "--component", kind, "--validate-only"], timeout=120)
            report = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else {}
            if result.returncode != 0 or report.get("status") != "PASS":
                raise ControlPlaneError("LAUNCHER_VALIDATION_FAILED", kind, report=report)
            launcher[kind] = report
        signatures = self._verify_worker_runtime(runtime)
        graph = self.platform.alembic_graph(python, release / "backend")
        head = migration.single_head(graph)
        if head != document["alembic"]["head"]:
            raise ControlPlaneError("RELEASE_MANIFEST_ALEMBIC_MISMATCH")
        coverage = migration.verify_contract_covers_graph(migration.load_rollback_contract(), graph, exact=False)
        return {"manifest": {"schema_version": document["schema_version"], "git_sha": document["git_sha"]},
                "material_verification": verification, "runtime_contract": runtime_check,
                "launcher_validate_only": launcher, "worker_runtime": signatures,
                "alembic_head": head, "rollback_contract_coverage": coverage,
                "summary": {"alembic_head": head, "python": version, "node": node}}

    def _verify_worker_runtime(self, runtime: Path) -> dict[str, Any]:
        files = []
        for kind in WORKER_KINDS:
            folder, runner, common, _, _ = WORKER_FILES[kind]
            files += [runtime / folder / runner, runtime / folder / common]
        signatures = self.platform.signatures(files)
        bad = {path: value for path, value in signatures.items()
               if value["status"] != "Valid" or value["thumbprint"] not in self.profile.signing_thumbprints}
        if bad or len(signatures) != len(files):
            raise ControlPlaneError("WORKER_RUNNER_SIGNATURE_REJECTED", facts=bad)
        for kind in WORKER_KINDS:
            folder, _, common, config, function = WORKER_FILES[kind]
            self.platform.validate_worker_config(runtime / folder / common, function, runtime / folder / config)
        return {"signatures": signatures, "configurations": "validated_under_AllSigned"}

    # ================================================================ MIGRATION
    def gate_migration_plan(self, state: ReleaseState) -> dict[str, Any]:
        candidate = state.data["candidate_sha"]
        graph = self.platform.alembic_graph(self._python(candidate), self._release_dir(candidate) / "backend")
        revisions = self._database().revisions()
        plan = migration.plan_migration(graph, revisions, migration.load_rollback_contract())
        state.data["db_revision_before"] = plan["db_revision_before"]
        state.data["migration_plan"] = plan
        state.save()
        if plan["status"] == "blocked":
            raise ControlPlaneError("MIGRATION_PLAN_BLOCKED", plan["reason"], plan=plan)
        authorized = self.authorization["migration"]
        if plan["status"] == "noop" and authorized["authorized"]:
            raise ControlPlaneError("MIGRATION_AUTHORIZATION_MISMATCH", "a migration was authorized but none is needed")
        if plan["status"] == "upgrade" and (not authorized["authorized"] or authorized["from_revision"] != plan["db_revision_before"]
                                            or authorized["to_revision"] != plan["target_revision"]):
            raise ControlPlaneError("MIGRATION_NOT_AUTHORIZED", "the exact upgrade path must be authorized")
        if plan["status"] == "upgrade" and not state.data.get("backup"):
            raise ControlPlaneError("MIGRATION_WITHOUT_BACKUP")
        return {"plan": plan, "summary": {"status": plan["status"], "from": plan["db_revision_before"],
                                          "to": plan["target_revision"]}}

    def gate_migrate(self, state: ReleaseState) -> dict[str, Any]:
        plan = state.data["migration_plan"]
        if plan["status"] == "noop":
            state.data["db_revision_after"] = plan["db_revision_before"]
            state.save()
            return {"action": "none", "reason": "database already at candidate head",
                    "summary": {"migrated": False}}
        database, before, target = self._database(), plan["db_revision_before"], plan["target_revision"]
        revisions = database.revisions()
        if revisions == [target] and state.data["migration"]["started"]:
            state.data["migration"]["applied_steps"] = plan["steps"]
            state.data["db_revision_after"] = target
            state.save()
            return {"action": "already_applied", "summary": {"migrated": True, "resumed": True}}
        if revisions != [before]:
            raise ControlPlaneError("MIGRATION_STATE_CONTRADICTION", revisions=revisions)
        state.data["migration"]["started"] = True
        state.save()
        candidate = state.data["candidate_sha"]
        result = self.platform.run_alembic(self._python(candidate), self._release_dir(candidate) / "backend",
                                           self.profile.runtime_env_file, ["upgrade", target])
        after = database.revisions()
        if after == [before]:
            state.data["migration"]["started"] = False
            state.save()
            raise ControlPlaneError("MIGRATION_FAILED_DATABASE_UNCHANGED", f"alembic exited {result.returncode}")
        if result.returncode != 0 or after != [target]:
            raise ControlPlaneError("MIGRATION_PARTIAL", "MANUAL_RECOVERY_REQUIRED", revisions=after)
        state.data["migration"]["applied_steps"] = plan["steps"]
        state.data["db_revision_after"] = target
        state.save()
        return {"action": "upgraded", "from": before, "to": target, "steps": plan["steps"],
                "summary": {"migrated": True}}

    # ================================================================ SWITCH / VERIFY
    def _stop_owned(self, state: ReleaseState, kind: str) -> dict[str, Any]:
        """Stop one task and prove its whole tree and listener are gone.

        The lineage below the task's engine process is captured and persisted
        before the stop, so a resumed run knows exactly what the task owned.
        """
        name, port = self._task(kind), self._port(kind)
        definition = self._definition(kind)
        legacy = False
        if kind in APPLICATION_KINDS:
            config = read_json(configuration_path(definition.actions[0]), "TASK_CONFIGURATION_UNREADABLE")
            legacy = config.get("schema_version") != 2
        persisted = state.data["facts"].setdefault("pre_stop_capture", {})
        captured: list[ProcessIdentity] = []
        was_running = self.platform.task_state(name) == "Running"
        if was_running:
            for engine in self.platform.task_engine_pids(name):
                captured.extend(self.platform.lineage(engine))
            persisted[kind] = [item.evidence() for item in captured]
            state.save()
            self.platform.stop_task(name)
        elif persisted.get(kind):
            captured = [ProcessIdentity(**row) for row in persisted[kind]]
        ports = [port] if kind in APPLICATION_KINDS else []

        def stopped() -> bool:
            return (self.platform.task_state(name) != "Running" and not self.platform.alive(captured)
                    and not any(self.platform.listeners(ports).values()))

        clean, waited = self._wait(stopped, self.deadlines.stop_seconds)
        record: dict[str, Any] = {"task": name, "was_running": was_running, "legacy_supervisor": legacy,
                                  "captured": [item.evidence() for item in captured], "clean_after_stop": clean,
                                  "seconds": waited, "terminated_by_legacy_handoff": []}
        if not clean:
            survivors = self.platform.alive(captured)
            holder = self.platform.listeners(ports).get(port) if ports else None
            if legacy and survivors:
                # Pre-TASK_230 supervisors leave their children running. End exactly the
                # descendants captured below this task's engine while it ran, deepest first.
                for identity in reversed(survivors):
                    if self.platform.terminate_verified(identity):
                        record["terminated_by_legacy_handoff"].append(identity.evidence())
                clean, waited = self._wait(stopped, self.deadlines.legacy_handoff_seconds)
                record["clean_after_legacy_handoff"] = clean
            if not clean:
                raise ControlPlaneError("SUPERVISED_TREE_SURVIVED_STOP", name,
                                        holder=holder, survivors=[item.evidence() for item in self.platform.alive(captured)])
        if ports and any(self.platform.listeners(ports).values()):
            raise ControlPlaneError("PORT_HELD_BY_UNOWNED_PROCESS", str(port))
        state.data["facts"].setdefault("stopped", {}).setdefault(kind, [])
        state.data["facts"]["stopped"][kind].extend(record["captured"])
        persisted.pop(kind, None)
        state.save()
        return record

    def _start_and_prove(self, kind: str) -> dict[str, Any]:
        name, port = self._task(kind), self._port(kind)
        self.platform.start_task(name)
        listening, waited = self._wait(lambda: self.platform.listeners([port]).get(port) is not None,
                                       self.deadlines.listener_seconds)
        if not listening:
            raise ControlPlaneError("LISTENER_NOT_STARTED", name)
        owner = self.platform.listeners([port])[port]
        lineage = [identity for engine in self.platform.task_engine_pids(name)
                   for identity in self.platform.lineage(engine)]
        if owner not in {identity.pid for identity in lineage}:
            raise ControlPlaneError("LISTENER_NOT_OWNED_BY_TASK", name, owner=owner)
        return {"task": name, "port": port, "listener_pid": owner, "listening_after_seconds": waited,
                "lineage": [identity.evidence() for identity in lineage]}

    def _switch(self, state: ReleaseState, kind: str) -> dict[str, Any]:
        name, port = self._task(kind), self._port(kind)
        target = self.target_action(state, kind)
        current = self._definition(kind)
        if current.actions == (target,) and self.platform.task_state(name) == "Running":
            owner = self.platform.listeners([port]).get(port)
            lineage = {identity.pid for engine in self.platform.task_engine_pids(name)
                       for identity in self.platform.lineage(engine)}
            if owner in lineage:
                return {"task": name, "already_switched": True, "listener_pid": owner,
                        "summary": {"already_switched": True}}
        stop = self._stop_owned(state, kind)
        if current.actions != (target,):
            self.platform.set_task_action(name, target)
        after = self._definition(kind)
        problems = rebind_violations(current, after, target) + contract_violations(after, kind, production=self.profile.production)
        if problems:
            raise ControlPlaneError("TASK_REBIND_VIOLATION", name, problems=problems)
        started = self._start_and_prove(kind)
        return {"stop": stop, "bound_action": target.evidence(), "start": started,
                "summary": {"task": name, "listener_pid": started["listener_pid"]}}

    def gate_switch_backend(self, state: ReleaseState) -> dict[str, Any]:
        return self._switch(state, "backend")

    def gate_switch_frontend(self, state: ReleaseState) -> dict[str, Any]:
        return self._switch(state, "frontend")

    def _expected(self, state: ReleaseState) -> tuple[str, str, str]:
        """(sha, alembic head, dist index sha256) the switched release must report."""
        if self.request.operation == "release":
            facts = state.data["facts"]["candidate"]
            return state.data["candidate_sha"], state.data["migration_plan"]["target_revision"], facts["dist_index_sha256"]
        facts = state.data["facts"]["target"]
        return facts["sha"], facts["alembic_head"], facts["dist_index_sha256"]

    def gate_verify_backend(self, state: ReleaseState) -> dict[str, Any]:
        sha, head, _ = self._expected(state)
        compatible = self.request.operation == "rollback"  # a rollback target may predate TASK_228
        result = health.wait_for(lambda: health.probe_backend(self.profile.backend_port, sha, head, get=self.platform.http_get,
                                                              pre_task228_compatible=compatible),
                                 deadline_seconds=self.deadlines.health_seconds, interval_seconds=1.0,
                                 clock=self.platform.monotonic, sleep=self.platform.sleep)
        if not result["pass"]:
            raise ControlPlaneError("BACKEND_HEALTH_FAILED", facts=result)
        return {"health": result, "summary": {"release_revision": sha, "migration_revision": head}}

    def gate_verify_frontend(self, state: ReleaseState) -> dict[str, Any]:
        sha, _, index = self._expected(state)
        result = health.wait_for(lambda: health.probe_frontend(self.profile.frontend_port, index, sha, get=self.platform.http_get),
                                 deadline_seconds=self.deadlines.health_seconds, interval_seconds=1.0,
                                 clock=self.platform.monotonic, sleep=self.platform.sleep)
        if not result["pass"]:
            raise ControlPlaneError("FRONTEND_HEALTH_FAILED", facts=result)
        return {"health": result, "summary": {"index_sha256": index}}

    # ================================================================ WORKERS
    def gate_rebind_workers(self, state: ReleaseState) -> dict[str, Any]:
        evidence = {}
        for kind in self._workers(state):
            name = self._task(kind)
            target = self.target_action(state, kind)
            current = self._definition(kind)
            if current.actions == (target,):
                evidence[kind] = {"already_bound": True}
                continue
            running = self.platform.task_state(name) == "Running"
            if running and kind == "notifications":
                idle, _ = self._wait(lambda: self.platform.task_state(name) != "Running", self.deadlines.worker_idle_seconds)
                if not idle:
                    raise ControlPlaneError("NOTIFICATIONS_RUN_NOT_FINISHING")
            self.platform.set_task_action(name, target)
            after = self._definition(kind)
            problems = rebind_violations(current, after, target) + contract_violations(after, kind, production=self.profile.production)
            if problems:
                raise ControlPlaneError("TASK_REBIND_VIOLATION", name, problems=problems)
            evidence[kind] = {"bound_action": target.evidence(), "instance_running_during_rebind": running,
                              "schedule_identity_unchanged": True}
        return {"workers": evidence, "summary": {kind: "bound" for kind in self._workers(state)}}

    def gate_verify_workers(self, state: ReleaseState) -> dict[str, Any]:
        evidence = {}
        for kind in self._workers(state):
            definition = self._definition(kind)
            target = self.target_action(state, kind)
            if definition.actions != (target,):
                raise ControlPlaneError("WORKER_BINDING_MISMATCH", kind)
            problems = contract_violations(definition, kind, production=self.profile.production)
            if problems:
                raise ControlPlaneError("TASK_CONTRACT_VIOLATION", kind, problems=problems)
            if not definition.enabled:
                raise ControlPlaneError("WORKER_TASK_DISABLED", kind)
            evidence[kind] = {"definition": definition.evidence()}
        sha = self._expected(state)[0]
        runtime = configuration_path(self.target_action(state, "sentinel")).parent.parent
        if self.request.operation == "release":
            evidence["runtime"] = self._verify_worker_runtime(runtime)
        if self.profile.worker_dry_run:
            dry = {}
            for kind in WORKER_KINDS:
                action = self.target_action(state, kind)
                runner = re.search(r'-File "([^"]+)"', action.arguments).group(1)
                result = self.platform.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                                            "AllSigned", "-File", runner, "-ConfigurationPath",
                                            str(configuration_path(action)), "-Mode", "dry-run"],
                                           cwd=Path(action.working_directory), timeout=1800)
                dry[kind] = {"exit_code": result.returncode}
                if result.returncode != 0:
                    raise ControlPlaneError("WORKER_DRY_RUN_FAILED", kind)
            evidence["dry_runs"] = dry
        return {"workers": evidence, "summary": {"release": sha}}

    # ================================================================ FINAL / COMMIT
    def gate_final_health(self, state: ReleaseState) -> dict[str, Any]:
        sha, head, index = self._expected(state)
        backend = health.probe_backend(self.profile.backend_port, sha, head, get=self.platform.http_get,
                                       pre_task228_compatible=self.request.operation == "rollback")
        frontend = health.probe_frontend(self.profile.frontend_port, index, sha, get=self.platform.http_get)
        if not backend["pass"] or not frontend["pass"]:
            raise ControlPlaneError("FINAL_HEALTH_FAILED", facts={"backend": backend["checks"], "frontend": frontend["checks"]})
        ownership = {}
        for kind in APPLICATION_KINDS:
            owner = self.platform.listeners([self._port(kind)]).get(self._port(kind))
            lineage = {identity.pid for engine in self.platform.task_engine_pids(self._task(kind))
                       for identity in self.platform.lineage(engine)}
            if owner not in lineage:
                raise ControlPlaneError("LISTENER_NOT_OWNED_BY_TASK", kind, owner=owner)
            ownership[kind] = owner
        stopped = state.data["facts"].get("stopped", {})
        survivors = {kind: [item.evidence() for item in self.platform.alive([ProcessIdentity(**row) for row in rows])]
                     for kind, rows in stopped.items()}
        if any(survivors.values()):
            raise ControlPlaneError("PREVIOUS_PROCESSES_SURVIVED", facts=survivors)
        return {"backend": backend, "frontend": frontend, "listener_owners": ownership,
                "previous_processes_alive": survivors, "summary": {"release": sha, "healthy": True}}

    def gate_commit(self, state: ReleaseState) -> dict[str, Any]:
        sha, head, _ = self._expected(state)
        root = self.profile.control_root
        pointer_path, previous_path = root / "current-release.json", root / "previous-release.json"
        previous_pointer = read_json(pointer_path, "CURRENT_POINTER_UNREADABLE") if pointer_path.exists() else {
            "sha": state.data["previous_sha"], "release_id": None, "recorded_from": "task bindings before this release",
            "bindings": state.data["facts"]["tasks_before"]}
        pointer = {"sha": sha, "release_id": state.data["release_id"], "operation": state.data["operation"],
                   "alembic_head": head, "committed_at": iso(self.platform.now()),
                   "bindings": {kind: self._definition(kind).actions[0].evidence()
                                for kind in APPLICATION_KINDS + self._workers(state)}}
        write_json_atomic(previous_path, previous_pointer)
        write_json_atomic(pointer_path, pointer)
        return {"current_pointer": pointer, "previous_pointer": previous_pointer, "summary": {"committed": sha}}

    # ================================================================ ROLLBACK OPERATION GATES
    def gate_rollback_plan(self, state: ReleaseState) -> dict[str, Any]:
        current, target = state.data["previous_sha"], state.data["candidate_sha"]
        target_head = state.data["facts"]["target"]["alembic_head"]
        graph = self.platform.alembic_graph(self._python(current), self._release_dir(current) / "backend")
        revisions = self._database().revisions()
        if len(revisions) != 1:
            raise ControlPlaneError("ROLLBACK_DATABASE_REVISION_UNRESOLVED", revisions=revisions)
        order = migration.ordered_revisions(graph)
        db_revision = revisions[0]
        if db_revision not in order or target_head not in order or order.index(target_head) > order.index(db_revision):
            raise ControlPlaneError("ROLLBACK_PLAN_BLOCKED", "the target head is not an ancestor of the database revision")
        contract = {item["revision"]: item for item in migration.load_rollback_contract()["migrations"]}
        steps = [{"revision": revision, "classification": contract[revision]["classification"]}
                 for revision in order[order.index(target_head) + 1:order.index(db_revision) + 1]]
        decision = migration.downgrade_decision(steps)
        strategy = self.authorization["database_rollback_strategy"]
        if decision["case"] == 2 and strategy not in ("downgrade_reversible_only", "restore_validated_backup"):
            raise ControlPlaneError("ROLLBACK_DATABASE_STRATEGY_NOT_AUTHORIZED")
        if decision["case"] == 3 and strategy != "restore_validated_backup":
            raise ControlPlaneError("ROLLBACK_REQUIRES_BACKUP_RESTORE", "automatic downgrade is forbidden for these steps",
                                    blocking=decision["blocking_steps"])
        restore_backup = None
        if decision["case"] == 3:
            # The authorized backup is found and re-verified before anything is stopped.
            restore_backup = self._find_backup(self.authorization["restore_backup_sha256"], target_head)
        state.data["db_revision_before"] = db_revision
        state.data["rollback_plan"] = {"steps": steps, "decision": decision, "target_head": target_head,
                                       "strategy": strategy, "restore_backup_id": restore_backup}
        state.save()
        return {"plan": state.data["rollback_plan"], "summary": {"case": decision["case"]}}

    def gate_database(self, state: ReleaseState) -> dict[str, Any]:
        plan = state.data["rollback_plan"]
        decision, target_head = plan["decision"], plan["target_head"]
        database = self._database()
        if decision["case"] == 1:
            state.data["db_revision_after"] = state.data["db_revision_before"]
            state.save()
            return {"action": "none", "summary": {"case": 1}}
        if database.revisions() == [target_head]:
            state.data["db_revision_after"] = target_head
            state.save()
            return {"action": "already_at_target", "summary": {"case": decision["case"], "resumed": True}}
        stops = [self._stop_owned(state, kind) for kind in ("frontend", "backend")]
        idle, _ = self._wait(lambda: self.platform.task_state(self._task("notifications")) != "Running",
                             self.deadlines.worker_idle_seconds)
        if not idle:
            raise ControlPlaneError("NOTIFICATIONS_RUN_NOT_FINISHING")
        current = state.data["previous_sha"]
        if decision["case"] == 2:
            result = self.platform.run_alembic(self._python(current), self._release_dir(current) / "backend",
                                               self.profile.runtime_env_file, ["downgrade", target_head])
            if result.returncode != 0 or database.revisions() != [target_head]:
                raise ControlPlaneError("ROLLBACK_DOWNGRADE_FAILED", "MANUAL_RECOVERY_REQUIRED")
            action = {"action": "alembic_downgrade", "to": target_head}
        else:
            backup_id = plan["restore_backup_id"]
            action = {"action": "restore_swap", **self.platform.backup_restore_swap(
                self.policy, backup_id, target_head, state.data["release_id"].lower().replace("-", "_")[-12:],
                state.directory / "database")}
        state.data["db_revision_after"] = target_head
        state.save()
        return {**action, "stops": stops, "summary": {"case": decision["case"]}}

    def _find_backup(self, sha256: str, revision: str) -> str:
        if self.policy is None:
            raise ControlPlaneError("BACKUP_POLICY_REQUIRED")
        for item in self.platform.backup_inventory(self.policy):
            if item["status"] == "validated" and item.get("sha256") == sha256:
                metadata = self.platform.backup_load(self.policy, item["name"])
                if metadata["db_revision"] != revision:
                    raise ControlPlaneError("RESTORE_BACKUP_REVISION_MISMATCH")
                return item["name"]
        raise ControlPlaneError("RESTORE_BACKUP_NOT_FOUND", "no validated backup has the authorized SHA-256")

    # ================================================================ AUTOMATIC ROLLBACK
    def _automatic_rollback(self, state: ReleaseState, gate: str, error: ControlPlaneError) -> None:
        state.data["rollback_required"] = True
        result: dict[str, Any] = {"cause_gate": gate, "cause": error.evidence(), "started_at": iso(self.platform.now()),
                                  "steps": []}
        state.data["rollback_result"] = result
        state.save()
        state.event("automatic_rollback_started", gate=gate)
        before = state.read_snapshot("tasks-before.json")
        previous = state.data["facts"]["previous"]
        candidate_trees: list[ProcessIdentity] = []
        try:
            switched = [kind for kind in ("frontend", "backend")
                        if self._definition(kind).actions[0].evidence() != before[kind]["actions"][0]]
            for kind in switched:
                stop = self._stop_owned(state, kind)
                candidate_trees.extend(ProcessIdentity(**row) for row in stop["captured"])
                result["steps"].append({"stop_candidate": kind, **stop})
            applied = state.data["migration"]["applied_steps"]
            decision = migration.downgrade_decision(applied)
            result["database_decision"] = decision
            if state.data["migration"]["started"] and not applied:
                raise ControlPlaneError("MANUAL_RECOVERY_REQUIRED", "migration outcome unknown")
            if decision["case"] == 2:
                if self.authorization["database_rollback_strategy"] == "none":
                    raise ControlPlaneError("MANUAL_RECOVERY_REQUIRED", "downgrade not authorized")
                candidate = state.data["candidate_sha"]
                run = self.platform.run_alembic(self._python(candidate), self._release_dir(candidate) / "backend",
                                                self.profile.runtime_env_file, ["downgrade", state.data["db_revision_before"]])
                if run.returncode != 0 or self._database().revisions() != [state.data["db_revision_before"]]:
                    raise ControlPlaneError("MANUAL_RECOVERY_REQUIRED", "reversible downgrade failed")
                result["steps"].append({"database": "alembic_downgrade", "to": state.data["db_revision_before"]})
            elif decision["case"] == 3:
                if self.authorization["database_rollback_strategy"] != "restore_validated_backup":
                    raise ControlPlaneError("MANUAL_RECOVERY_REQUIRED",
                                            "automatic downgrade is forbidden and backup restore is not authorized",
                                            backup=state.data.get("backup"))
                idle, _ = self._wait(lambda: self.platform.task_state(self._task("notifications")) != "Running",
                                     self.deadlines.worker_idle_seconds)
                if not idle:
                    raise ControlPlaneError("MANUAL_RECOVERY_REQUIRED", "notifications still running; database not swapped")
                swap = self.platform.backup_restore_swap(self.policy, state.data["backup"]["backup_id"],
                                                  state.data["db_revision_before"],
                                                  state.data["release_id"].lower().replace("-", "_")[-12:],
                                                  state.directory / "database")
                result["steps"].append({"database": "restore_swap", "record": swap})
            for kind in ("backend", "frontend"):
                action = TaskAction(**before[kind]["actions"][0])
                current = self._definition(kind)
                if kind in switched:
                    self.platform.set_task_action(self._task(kind), action)
                    problems = rebind_violations(current, self._definition(kind), action)
                    if problems:
                        raise ControlPlaneError("MANUAL_RECOVERY_REQUIRED", "rebind violated the schedule identity",
                                                problems=problems)
                    result["steps"].append({"restore_binding": kind, **self._start_and_prove(kind)})
                elif self.platform.task_state(self._task(kind)) != "Running":
                    # Stopped mid-switch but never rebound: start the previous release again.
                    result["steps"].append({"restart_previous": kind, **self._start_and_prove(kind)})
            for kind in self._workers(state):
                action = TaskAction(**before[kind]["actions"][0])
                current = self._definition(kind)
                if current.actions != (action,):
                    self.platform.set_task_action(self._task(kind), action)
                    problems = rebind_violations(current, self._definition(kind), action)
                    if problems:
                        raise ControlPlaneError("MANUAL_RECOVERY_REQUIRED", "worker rebind violated schedule identity")
                    result["steps"].append({"restore_binding": kind})
            backend = health.wait_for(lambda: health.probe_backend(self.profile.backend_port, previous["sha"],
                                                                   previous["alembic_head"], get=self.platform.http_get,
                                                                   pre_task228_compatible=True),
                                      deadline_seconds=self.deadlines.health_seconds, interval_seconds=1.0,
                                      clock=self.platform.monotonic, sleep=self.platform.sleep)
            frontend = health.wait_for(lambda: health.probe_frontend(self.profile.frontend_port, previous["dist_index_sha256"],
                                                                     previous["sha"], get=self.platform.http_get),
                                       deadline_seconds=self.deadlines.health_seconds, interval_seconds=1.0,
                                       clock=self.platform.monotonic, sleep=self.platform.sleep)
            result["previous_health"] = {"backend": backend, "frontend": frontend}
            if not backend["pass"] or not frontend["pass"]:
                raise ControlPlaneError("MANUAL_RECOVERY_REQUIRED", "the previous release is not healthy after rollback")
            candidate_alive = [item.evidence() for item in self.platform.alive(candidate_trees)]
            result["candidate_processes_captured"] = len(candidate_trees)
            result["candidate_processes_alive"] = candidate_alive
            if candidate_alive:
                raise ControlPlaneError("MANUAL_RECOVERY_REQUIRED", "candidate processes survived the rollback")
            result["status"] = "rolled_back"
            result["finished_at"] = iso(self.platform.now())
            state.data["rollback_result"] = result
            state.finish("rolled_back")
        except ControlPlaneError as failure:
            result["status"] = "MANUAL_RECOVERY_REQUIRED"
            result["error"] = failure.evidence()
            result["finished_at"] = iso(self.platform.now())
            state.data["rollback_result"] = result
            state.finish("manual_recovery_required")
        state.event("automatic_rollback_finished", status=result["status"])
