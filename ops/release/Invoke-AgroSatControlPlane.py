"""AgroSat production operations control plane: the one operator entry point.

    release | rollback       gated, resumable state machines (production needs
                             --authorization-sha256; rehearsal needs --profile)
    preflight                read-only authorization, identity and task checks
    manifest                 current release manifest preview for a candidate
    migration-plan           database revision vs candidate head (read-only)
    rollback-contract        prove every shipped migration is classified
    tasks                    read-only Scheduled Task contract inspection
    authorize                write an authorization artifact (outside source trees)
    status                   print a release's recorded state
    backup ...               run | scheduled | verify | protection | secondary |
                             retention-plan | retention-apply | restore-rehearsal |
                             drop-rehearsal-target

Every command prints one JSON document. Exit code 0 means PASS; 2 means a
fail-closed stop with a stable error code; 3 means the release ended but did
not complete (failed, rolled back or manual recovery required).
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from controlplane import backup as backup_module  # noqa: E402
from controlplane import migration  # noqa: E402
from controlplane.authorization import MAX_VALIDITY, validate_authorization  # noqa: E402
from controlplane.common import (  # noqa: E402
    ControlPlaneError, find_secrets, iso, is_within, read_json, require_release_id, require_sha, utc_now,
    write_json_immutable,
)
from controlplane.controller import Controller, Deadlines, Request  # noqa: E402
from controlplane.gitmaterial import Git  # noqa: E402
from controlplane.manifest import load_runtime_contract, source_identity  # noqa: E402
from controlplane.profiles import PRODUCTION, load_rehearsal_profile  # noqa: E402
from controlplane.state import used_release_ids  # noqa: E402
from controlplane.tasks import contract_violations  # noqa: E402

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def emit(document: dict) -> None:
    if find_secrets(document):
        document = {"status": "FAIL", "error": {"code": "OUTPUT_SECRET_REJECTED"}}
    print(json.dumps(document, indent=2, default=str))


def target_profile(args):
    if args.mode == "production":
        if getattr(args, "profile", None):
            raise ControlPlaneError("PRODUCTION_PROFILE_IS_FIXED", "production takes no profile file")
        return PRODUCTION
    if not getattr(args, "profile", None):
        raise ControlPlaneError("REHEARSAL_PROFILE_REQUIRED")
    return load_rehearsal_profile(Path(args.profile))


def platform():
    from controlplane.platform import WindowsPlatform
    return WindowsPlatform()


def command_operation(args) -> int:
    profile = target_profile(args)
    if args.mode == "production" and (args.no_fetch or args.fast_deadlines):
        raise ControlPlaneError("PRODUCTION_OPTION_REJECTED", "--no-fetch and --fast-deadlines are rehearsal-only")
    request = Request(operation=args.command, mode=args.mode, release_id=require_release_id(args.release_id),
                      candidate_sha=require_sha(args.candidate, "CANDIDATE_SHA_MALFORMED"),
                      expected_current_sha=require_sha(args.expected_current, "CURRENT_SHA_MALFORMED"),
                      authorization_path=Path(args.authorization), authorization_sha256=args.authorization_sha256,
                      profile=profile, repository=None if args.repository is None else Path(args.repository),
                      backup_policy_path=None if args.backup_policy is None else Path(args.backup_policy),
                      fetch=not args.no_fetch)
    deadlines = Deadlines(stop_seconds=30, legacy_handoff_seconds=20, listener_seconds=90, health_seconds=45,
                          worker_idle_seconds=120) if args.fast_deadlines else Deadlines()
    data = Controller(request, platform(), deadlines=deadlines).run()
    emit({"status": "PASS" if data["status"] == "completed" else data["status"].upper(),
          "release_id": data["release_id"], "operation": data["operation"], "state": str(
              profile.control_root / "releases" / data["release_id"] / "state.json"),
          "current_gate": data["current_gate"], "rollback_required": data["rollback_required"],
          "rollback_result": data["rollback_result"], "db_revision_before": data["db_revision_before"],
          "db_revision_after": data["db_revision_after"]})
    return 0 if data["status"] == "completed" else 3


def command_preflight(args) -> int:
    profile = target_profile(args)
    forbidden = [profile.release_root, profile.runtime_root, REPOSITORY_ROOT]
    authorization = validate_authorization(
        Path(args.authorization), operation=args.operation, release_id=require_release_id(args.release_id),
        candidate_sha=args.candidate, expected_current_sha=args.expected_current, database_name=profile.database_name,
        forbidden_roots=forbidden, used_release_ids=used_release_ids(profile.control_root),
        expected_sha256=args.authorization_sha256)
    host = platform()
    tasks = {}
    for kind, name in profile.tasks.all().items():
        definition = host.task_definition(name)
        tasks[kind] = None if definition is None else {
            "violations": contract_violations(definition, kind, production=profile.production),
            "action": definition.actions[0].evidence() if definition.actions else None}
    emit({"status": "PASS" if all(item and not item["violations"] for item in tasks.values()) else "BLOCKED",
          "mode": args.mode, "authorization": {key: authorization[key] for key in (
              "release_id", "operation", "candidate_sha", "expected_current_sha", "database_name", "expires_at",
              "sha256")}, "tasks": tasks, "mutation_performed": False})
    return 0


def command_manifest(args) -> int:
    git = Git(Path(args.repository))
    identity = source_identity(git, candidate_sha=args.candidate, expected_current_sha=args.expected_current,
                               fetch=not args.no_fetch)
    emit({"status": "PASS", "identity": identity, "runtime_contract": load_runtime_contract(),
          "mutation_performed": False})
    return 0


def command_migration_plan(args) -> int:
    graph = migration.alembic_graph(Path(args.python), Path(args.backend))
    target = platform().database(Path(args.env_file), args.database, Path(args.pg_bin))
    plan = migration.plan_migration(graph, target.revisions(), migration.load_rollback_contract())
    emit({"status": "PASS" if plan["status"] != "blocked" else "BLOCKED", "plan": plan, "read_only": True})
    return 0 if plan["status"] != "blocked" else 2


def command_rollback_contract(args) -> int:
    graph = migration.alembic_graph(Path(args.python), Path(args.backend))
    coverage = migration.verify_contract_covers_graph(migration.load_rollback_contract(), graph)
    emit({"status": "PASS", "head": migration.single_head(graph), "coverage": coverage})
    return 0


def command_tasks(args) -> int:
    profile = target_profile(args)
    host = platform()
    report = {}
    for kind, name in profile.tasks.all().items():
        definition = host.task_definition(name)
        report[kind] = {"task": name, "installed": definition is not None}
        if definition is not None:
            report[kind].update(state=host.task_state(name), definition=definition.evidence(),
                                violations=contract_violations(definition, kind, production=profile.production))
    ok = all(item["installed"] and not item["violations"] for item in report.values())
    emit({"status": "PASS" if ok else "BLOCKED", "mode": args.mode, "tasks": report, "read_only": True})
    return 0 if ok else 2


def command_authorize(args) -> int:
    output = Path(args.output)
    for root in (REPOSITORY_ROOT, PRODUCTION.release_root, PRODUCTION.runtime_root):
        if is_within(output, root):
            raise ControlPlaneError("AUTHORIZATION_INSIDE_SOURCE_TREE")
    hours = float(args.valid_hours)
    if not 0 < hours <= MAX_VALIDITY.total_seconds() / 3600:
        raise ControlPlaneError("AUTHORIZATION_WINDOW_REJECTED")
    created = utc_now()
    document = {
        "schema_version": 1, "kind": "agrosat_release_authorization", "operation": args.operation,
        "release_id": require_release_id(args.release_id), "candidate_sha": require_sha(args.candidate, "CANDIDATE_SHA_MALFORMED"),
        "expected_current_sha": require_sha(args.expected_current, "CURRENT_SHA_MALFORMED"),
        "database_name": args.database,
        "migration": {"authorized": bool(args.migration_from), "from_revision": args.migration_from,
                      "to_revision": args.migration_to},
        "database_rollback_strategy": args.database_rollback_strategy,
        "restore_backup_sha256": args.restore_backup_sha256,
        "created_at": iso(created), "expires_at": iso(created + timedelta(hours=hours)),
        "authorized_by": args.authorized_by,
    }
    digest = write_json_immutable(output, document)
    emit({"status": "PASS", "authorization": str(output), "sha256": digest, "expires_at": document["expires_at"]})
    return 0


def command_status(args) -> int:
    root = PRODUCTION.control_root if args.mode == "production" else target_profile(args).control_root
    emit(read_json(root / "releases" / require_release_id(args.release_id) / "state.json", "RELEASE_STATE_UNREADABLE"))
    return 0


def protected_backup_ids(control_root: Path | None) -> set[str]:
    """Backups a live or current/previous release or rollback record references."""
    protected: set[str] = set()
    if control_root is None or not (control_root / "releases").is_dir():
        return protected
    pointers = set()
    for name in ("current-release.json", "previous-release.json"):
        path = control_root / name
        if path.exists():
            pointers.add(read_json(path, "CURRENT_POINTER_UNREADABLE").get("release_id"))
    for child in (control_root / "releases").iterdir():
        path = child / "state.json"
        if not path.exists():
            continue
        record = read_json(path, "RELEASE_STATE_UNREADABLE")
        backup = record.get("backup") or {}
        if backup.get("backup_id") and (record.get("status") in ("in_progress", "manual_recovery_required", "rolled_back")
                                        or child.name in pointers):
            protected.add(backup["backup_id"])
    return protected


def command_backup(args) -> int:
    policy = backup_module.load_policy(Path(args.policy))
    control_root = None if args.control_root is None else Path(args.control_root)
    if args.action == "run":
        document = backup_module.run_backup(policy, reason="manual")
    elif args.action == "scheduled":
        document = scheduled_backup(policy, control_root)
    elif args.action == "verify":
        document = backup_module.load_backup(policy, args.backup_id)
    elif args.action == "protection":
        document = backup_module.protection_status(policy, args.backup_id)
    elif args.action == "secondary":
        document = backup_module.copy_secondary(policy, args.backup_id)
    elif args.action == "retention-plan":
        document = backup_module.plan_retention(policy, protected_ids=protected_backup_ids(control_root))
    elif args.action == "retention-apply":
        document = backup_module.apply_retention(policy, protected_ids=protected_backup_ids(control_root),
                                                 expected_plan_sha256=args.plan_sha256)
    elif args.action == "restore-rehearsal":
        document = backup_module.restore_rehearsal(policy, args.backup_id, args.target, Path(args.evidence))
    else:
        document = backup_module.drop_rehearsal_target(policy, args.target, Path(args.evidence))
    emit({"status": "PASS" if document.get("result", "PASS") == "PASS" else "FAIL", **document})
    return 0 if document.get("result", "PASS") == "PASS" else 2


def scheduled_backup(policy, control_root: Path | None) -> dict:
    """One scheduled execution: backup, secondary copy, retention; one immutable record."""
    started = utc_now()
    record: dict = {"schema_version": 1, "kind": "agrosat_scheduled_backup_execution", "started_at": iso(started),
                    "policy_sha256": policy.sha256}
    try:
        metadata = backup_module.run_backup(policy, reason="scheduled")
        record["backup"] = {"backup_id": metadata["backup_id"], "sha256": metadata["file"]["sha256"],
                            "db_revision": metadata["db_revision"], "bytes": metadata["file"]["bytes"]}
        if policy.secondary_destination is not None:
            record["secondary"] = backup_module.copy_secondary(policy, metadata["backup_id"])
        record["protection"] = backup_module.protection_status(policy, metadata["backup_id"])
        if policy.retention is not None:
            protected = protected_backup_ids(control_root)
            plan = backup_module.plan_retention(policy, protected_ids=protected)
            record["retention"] = backup_module.apply_retention(policy, protected_ids=protected,
                                                                expected_plan_sha256=plan["plan_sha256"])
        record["result"] = "PASS" if record["protection"]["fully_protected"] else "PRIMARY_ONLY"
    except ControlPlaneError as error:
        record["result"] = "FAIL"
        record["error"] = error.evidence()
    record["finished_at"] = iso(utc_now())
    executions = policy.backup_root / "executions"
    executions.mkdir(exist_ok=True)
    write_json_immutable(executions / f"{started.strftime('%Y%m%dT%H%M%SZ')}.json", record)
    if record["result"] == "FAIL":
        raise ControlPlaneError("SCHEDULED_BACKUP_FAILED", facts=record.get("error"))
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("release", "rollback"):
        sub = commands.add_parser(name)
        sub.add_argument("--mode", choices=("production", "rehearsal"), required=True)
        sub.add_argument("--profile")
        sub.add_argument("--release-id", required=True)
        sub.add_argument("--candidate", required=True, help="release: the new SHA; rollback: the SHA to return to")
        sub.add_argument("--expected-current", required=True)
        sub.add_argument("--authorization", required=True)
        sub.add_argument("--authorization-sha256")
        sub.add_argument("--repository")
        sub.add_argument("--backup-policy")
        sub.add_argument("--no-fetch", action="store_true", help="rehearsal only")
        sub.add_argument("--fast-deadlines", action="store_true", help="rehearsal only")
        sub.set_defaults(handler=command_operation)
    sub = commands.add_parser("preflight")
    sub.add_argument("--mode", choices=("production", "rehearsal"), required=True)
    sub.add_argument("--profile")
    sub.add_argument("--operation", choices=("release", "rollback"), required=True)
    for option in ("--release-id", "--candidate", "--expected-current", "--authorization"):
        sub.add_argument(option, required=True)
    sub.add_argument("--authorization-sha256")
    sub.set_defaults(handler=command_preflight)
    sub = commands.add_parser("manifest")
    for option in ("--repository", "--candidate", "--expected-current"):
        sub.add_argument(option, required=True)
    sub.add_argument("--no-fetch", action="store_true")
    sub.set_defaults(handler=command_manifest)
    sub = commands.add_parser("migration-plan")
    for option in ("--python", "--backend", "--env-file", "--database"):
        sub.add_argument(option, required=True)
    sub.add_argument("--pg-bin", default=str(PRODUCTION.pg_bin))
    sub.set_defaults(handler=command_migration_plan)
    sub = commands.add_parser("rollback-contract")
    sub.add_argument("--python", default=sys.executable)
    sub.add_argument("--backend", default=str(REPOSITORY_ROOT / "backend"))
    sub.set_defaults(handler=command_rollback_contract)
    sub = commands.add_parser("tasks")
    sub.add_argument("--mode", choices=("production", "rehearsal"), required=True)
    sub.add_argument("--profile")
    sub.set_defaults(handler=command_tasks)
    sub = commands.add_parser("authorize")
    sub.add_argument("--operation", choices=("release", "rollback"), required=True)
    for option in ("--release-id", "--candidate", "--expected-current", "--database", "--output", "--authorized-by"):
        sub.add_argument(option, required=True)
    sub.add_argument("--valid-hours", default="4")
    sub.add_argument("--migration-from")
    sub.add_argument("--migration-to")
    sub.add_argument("--database-rollback-strategy", default="none",
                     choices=("none", "downgrade_reversible_only", "restore_validated_backup"))
    sub.add_argument("--restore-backup-sha256")
    sub.set_defaults(handler=command_authorize)
    sub = commands.add_parser("status")
    sub.add_argument("--mode", choices=("production", "rehearsal"), required=True)
    sub.add_argument("--profile")
    sub.add_argument("--release-id", required=True)
    sub.set_defaults(handler=command_status)
    sub = commands.add_parser("backup")
    sub.add_argument("action", choices=("run", "scheduled", "verify", "protection", "secondary", "retention-plan",
                                        "retention-apply", "restore-rehearsal", "drop-rehearsal-target"))
    sub.add_argument("--policy", required=True)
    sub.add_argument("--control-root")
    sub.add_argument("--backup-id")
    sub.add_argument("--plan-sha256")
    sub.add_argument("--target")
    sub.add_argument("--evidence")
    sub.set_defaults(handler=command_backup)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except ControlPlaneError as error:
        emit({"status": "FAIL", "error": error.evidence()})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
