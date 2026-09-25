"""The production database backup contract (TASK_230 Parts N, O, P, Q).

Backup
    ``pg_dump --format=custom --no-owner --no-acl`` of one database into a new
    directory. A backup is valid only when the dump itself proves it: the
    restore list parses, every public table in the dump carries table data,
    the Alembic revision and per-table row counts are read back from the dump's
    own COPY data, and a normalized schema hash is computed from it. Metadata
    is written once, last; a directory without ``metadata.json`` is not a backup.

Retention
    Explicit policy values only. The newest validated backup, every backup a
    live release or rollback references, every unvalidated file and every
    backup younger than the minimum age are always kept. ``plan`` is a
    preview; ``apply`` executes only the exact plan whose SHA-256 it is given,
    deletes only direct children of the backup root that contain no reparse
    point, and records every deletion.

Secondary copy
    When ``secondary.required`` is true a backup is fully protected only after
    its dump and metadata are copied to the configured destination and the
    copy's SHA-256 equals the primary's. No destination is assumed.

Restore rehearsal
    Restores a validated backup into a NEW database named
    ``agrosat_taskNNN_*`` or ``agrosat_restore_rehearsal_*``, never overwriting
    and never dropping a failed target automatically, then validates revision,
    constraints, indexes, row counts and schema hash against the dump.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
from typing import Any

from .common import (
    ControlPlaneError, absolute, append_jsonl, assert_no_reparse_points, canonical_json, is_reparse_point,
    is_within, iso, read_json, sha256_bytes, sha256_file, targets_production, utc_now, write_json_atomic,
    write_json_immutable,
)
from .pgclient import DatabaseTarget, tool_version

BACKUP_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,62}-[0-9]{8}T[0-9]{6}Z-[a-z_]{3,20}-[0-9a-f]{6}$")
REHEARSAL_TARGET_PATTERN = re.compile(r"^agrosat_(task[0-9]{3}|restore_rehearsal)_[a-z0-9_]{1,40}$")
CLASSIFICATIONS = ("production", "rehearsal", "test")
REASONS = ("scheduled", "pre_release", "pre_rollback", "manual")
POLICY_KEYS = frozenset({"schema_version", "kind", "example_only", "database_name", "source_classification",
                         "runtime_env_file", "pg_bin", "backup_root", "retention", "secondary", "schedule"})
RETENTION_KEYS = frozenset({"keep_daily_count", "keep_weekly_count", "minimum_backup_count",
                            "minimum_age_before_delete_hours"})
RETENTION_BOUNDS = {"keep_daily_count": (1, 366), "keep_weekly_count": (0, 104),
                    "minimum_backup_count": (1, 1000), "minimum_age_before_delete_hours": (1, 8760)}
SCHEDULE_KEYS = frozenset({"task_name", "daily_at_local_time", "execution_sid", "execution_time_limit_minutes",
                           "restart_count", "restart_interval_minutes"})
CRITICAL_TABLES = ("enterprises", "fields", "users", "crop_types", "crop_seasons", "ndvi_records",
                   "satellite_index_records", "alerts", "scouting_notes", "field_inspections", "alembic_version")
COPY_HEADER = re.compile(r'^COPY (?:"?public"?\.)?"?([A-Za-z0-9_]+)"? \(.*\) FROM stdin;$')


@dataclass(frozen=True)
class BackupPolicy:
    database_name: str
    source_classification: str
    runtime_env_file: Path
    pg_bin: Path
    backup_root: Path
    retention: dict[str, int] | None
    secondary_required: bool
    secondary_destination: Path | None
    schedule: dict[str, Any] | None
    path: Path
    sha256: str

    def target(self) -> DatabaseTarget:
        return DatabaseTarget(self.runtime_env_file, self.database_name, self.pg_bin)

    def evidence(self) -> dict[str, Any]:
        return {"policy_path": str(self.path), "policy_sha256": self.sha256, "database_name": self.database_name,
                "source_classification": self.source_classification, "backup_root": str(self.backup_root),
                "retention": self.retention, "secondary_required": self.secondary_required,
                "secondary_destination": None if self.secondary_destination is None else str(self.secondary_destination),
                "schedule": self.schedule}


def _bounded_int(value: Any, low: int, high: int, code: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ControlPlaneError(code, f"expected an integer in [{low}, {high}]")
    return value


def load_policy(path: Path) -> BackupPolicy:
    path = absolute(str(path), "BACKUP_POLICY_PATH_REJECTED")
    document = read_json(path, "BACKUP_POLICY_UNREADABLE", max_bytes=64 * 1024)
    if not isinstance(document, dict) or set(document) != POLICY_KEYS:
        raise ControlPlaneError("BACKUP_POLICY_KEYSET_REJECTED")
    if document["schema_version"] != 1 or document["kind"] != "agrosat_database_backup_policy":
        raise ControlPlaneError("BACKUP_POLICY_SCHEMA_REJECTED")
    if document["example_only"] is not False:
        raise ControlPlaneError("BACKUP_POLICY_IS_EXAMPLE", "an example policy is never an operating policy")
    if "<" in json.dumps(document) or ">" in json.dumps(document):
        raise ControlPlaneError("BACKUP_POLICY_PLACEHOLDER", "replace every placeholder")
    classification = document["source_classification"]
    if classification not in CLASSIFICATIONS:
        raise ControlPlaneError("BACKUP_POLICY_CLASSIFICATION_REJECTED")
    database = document["database_name"]
    if not isinstance(database, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", database):
        raise ControlPlaneError("BACKUP_POLICY_DATABASE_REJECTED")
    if (classification == "production") != (database == "agrosat"):
        raise ControlPlaneError("BACKUP_POLICY_CLASSIFICATION_REJECTED",
                                "only the production database is classified production")
    root = absolute(document["backup_root"], "BACKUP_ROOT_REJECTED")
    for forbidden in (r"C:\AgroSat_releases", r"C:\AgroSat_runtime", r"C:\AgroSat"):
        if is_within(root, forbidden):
            raise ControlPlaneError("BACKUP_ROOT_REJECTED", "backups never live in release, runtime or source trees")
    if classification != "production" and targets_production(root):
        raise ControlPlaneError("BACKUP_ROOT_REJECTED")
    retention = document["retention"]
    if retention is not None:
        if not isinstance(retention, dict) or set(retention) != RETENTION_KEYS:
            raise ControlPlaneError("BACKUP_RETENTION_KEYSET_REJECTED")
        retention = {key: _bounded_int(retention[key], *RETENTION_BOUNDS[key], "BACKUP_RETENTION_VALUE_REJECTED")
                     for key in sorted(RETENTION_KEYS)}
        if retention["minimum_backup_count"] > retention["keep_daily_count"] + retention["keep_weekly_count"] + 1 \
                and retention["minimum_backup_count"] > 1:
            pass  # a larger minimum simply keeps more; it never deletes more
    secondary = document["secondary"]
    if not isinstance(secondary, dict) or set(secondary) != {"required", "destination"} \
            or not isinstance(secondary["required"], bool):
        raise ControlPlaneError("BACKUP_SECONDARY_KEYSET_REJECTED")
    destination = None
    if secondary["destination"] is not None:
        destination = absolute(secondary["destination"], "BACKUP_SECONDARY_DESTINATION_REJECTED")
        if is_within(destination, root) or is_within(root, destination):
            raise ControlPlaneError("BACKUP_SECONDARY_DESTINATION_REJECTED", "the secondary copy must be elsewhere")
    if secondary["required"] and destination is None:
        raise ControlPlaneError("BACKUP_SECONDARY_DESTINATION_REQUIRED",
                                "secondary copy is required but no destination is configured")
    schedule = document["schedule"]
    if schedule is not None:
        if not isinstance(schedule, dict) or set(schedule) != SCHEDULE_KEYS:
            raise ControlPlaneError("BACKUP_SCHEDULE_KEYSET_REJECTED")
        if not isinstance(schedule["daily_at_local_time"], str) or not re.fullmatch(
                r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]", schedule["daily_at_local_time"]):
            raise ControlPlaneError("BACKUP_SCHEDULE_TIME_REJECTED", "an explicit HH:MM:SS local time is required")
        if not isinstance(schedule["execution_sid"], str) or not re.fullmatch(r"S-1-[0-9-]+", schedule["execution_sid"]):
            raise ControlPlaneError("BACKUP_SCHEDULE_IDENTITY_REJECTED")
        _bounded_int(schedule["execution_time_limit_minutes"], 5, 240, "BACKUP_SCHEDULE_LIMIT_REJECTED")
        _bounded_int(schedule["restart_count"], 0, 3, "BACKUP_SCHEDULE_LIMIT_REJECTED")
        _bounded_int(schedule["restart_interval_minutes"], 1, 60, "BACKUP_SCHEDULE_LIMIT_REJECTED")
        if not isinstance(schedule["task_name"], str) or not re.fullmatch(r"\\AgroSat_[A-Za-z0-9_]{1,80}", schedule["task_name"]):
            raise ControlPlaneError("BACKUP_SCHEDULE_TASK_REJECTED")
        if (classification == "production") != (schedule["task_name"] == "\\AgroSat_PROGRAM_R3_DatabaseBackup"):
            raise ControlPlaneError("BACKUP_SCHEDULE_TASK_REJECTED",
                                    "the canonical task name belongs to the production policy only")
    return BackupPolicy(database, classification, absolute(document["runtime_env_file"], "BACKUP_POLICY_PATH_REJECTED"),
                        absolute(document["pg_bin"], "BACKUP_POLICY_PATH_REJECTED"), root, retention,
                        secondary["required"], destination, schedule, path, sha256_file(path))


# ------------------------------------------------------------------ dump facts

_CHECK = re.compile(r"^(\s*(?:ALTER TABLE .* ADD )?CONSTRAINT \S+ CHECK) .*?(,|;)?$")


def normalize_schema_sql(text: str) -> str:
    """A schema fingerprint that survives a dump/restore round trip.

    Comments, blank lines and per-dump restrict keys are dropped. PostgreSQL
    re-deparses CHECK and partial-index predicate expressions after they are
    replayed from a dump (cast placement inside ARRAY[...] changes), so those
    two are reduced to the constraint name and the index definition up to its
    predicate. Every table, column, type, default, key, foreign key, index
    column list, trigger, function and extension is compared as written.
    """
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--") or stripped.startswith(("\\restrict", "\\unrestrict")):
            continue
        line = line.rstrip()
        check = _CHECK.match(line)
        if check:
            line = f"{check.group(1)} <expression>{check.group(2) or ''}"
        elif re.match(r"^CREATE (UNIQUE )?INDEX ", line) and " WHERE " in line:
            line = line.split(" WHERE ", 1)[0] + " WHERE <predicate>;"
        kept.append(line)
    return "\n".join(kept) + "\n"


def dump_facts(target: DatabaseTarget, dump: Path, work: Path) -> dict[str, Any]:
    """Everything a backup proves about itself, read from the dump file only."""
    listing = target.run("pg_restore", ["--list", str(dump)], timeout=900)
    if listing.returncode != 0:
        raise ControlPlaneError("BACKUP_RESTORE_LIST_FAILED")
    list_text = listing.stdout.decode("utf-8", errors="replace")
    entries = [line for line in list_text.splitlines() if line.strip() and not line.startswith(";")]
    tables = sorted({line.split()[-2] for line in entries if " TABLE public " in line and " TABLE DATA " not in line})
    table_data = sorted({line.split()[-2] for line in entries if " TABLE DATA public " in line})
    data_path = work / "data.sql"
    data = target.run("pg_restore", ["--data-only", "--file=-", str(dump)], timeout=3600, stdout_path=data_path)
    if data.returncode != 0:
        raise ControlPlaneError("BACKUP_DATA_READBACK_FAILED")
    counts: dict[str, int] = {}
    revisions: list[str] = []
    current = None
    with data_path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            line = line.rstrip("\n")
            if current is None:
                match = COPY_HEADER.match(line)
                if match:
                    current = match.group(1)
                    counts[current] = 0
            elif line == "\\.":
                current = None
            else:
                counts[current] += 1
                if current == "alembic_version":
                    revisions.append(line.strip())
    data_path.unlink()
    schema_path = work / "schema.sql"
    schema = target.run("pg_restore", ["--schema-only", "--no-owner", "--no-acl", "--file=-", str(dump)],
                        timeout=900, stdout_path=schema_path)
    if schema.returncode != 0:
        raise ControlPlaneError("BACKUP_SCHEMA_READBACK_FAILED")
    schema_sha256 = sha256_bytes(normalize_schema_sql(schema_path.read_text(encoding="utf-8", errors="replace")).encode())
    schema_path.unlink()
    return {"restore_list": {"entries": len(entries), "sha256": sha256_bytes(list_text.encode()),
                             "tables": tables, "table_data": table_data,
                             "tables_missing_data": sorted(set(tables) - set(table_data))},
            "row_counts": dict(sorted(counts.items())), "alembic_revisions": revisions,
            "schema_sha256": schema_sha256, "list_text": list_text}


def _backup_id(database: str, reason: str, now: datetime) -> str:
    return f"{database}-{now.strftime('%Y%m%dT%H%M%SZ')}-{reason}-{secrets.token_hex(3)}"


def run_backup(policy: BackupPolicy, *, reason: str, release_id: str | None = None, now=None) -> dict[str, Any]:
    """Create, validate and seal one backup; return its metadata."""
    if reason not in REASONS:
        raise ControlPlaneError("BACKUP_REASON_REJECTED")
    root = policy.backup_root
    if not root.is_dir() or is_reparse_point(root):
        raise ControlPlaneError("BACKUP_ROOT_UNAVAILABLE")
    target = policy.target()
    started = now or utc_now()
    backup_id = _backup_id(policy.database_name, reason, started)
    staging = root / ".incomplete" / backup_id
    staging.mkdir(parents=True)
    dump = staging / f"{backup_id}.dump"
    server_version = target.server_version()
    result = target.run("pg_dump", ["--format=custom", "--no-owner", "--no-acl", "--no-password",
                                    f"--file={dump}"], timeout=4 * 3600)
    completed = utc_now()
    checks: dict[str, Any] = {"pg_dump_exit_zero": result.returncode == 0}
    facts: dict[str, Any] = {}
    if result.returncode == 0 and dump.is_file():
        try:
            facts = dump_facts(target, dump, staging)
        except ControlPlaneError as error:
            checks["readback"] = error.code
    list_text = facts.pop("list_text", "")
    (staging / f"{backup_id}.restore-list.txt").write_text(list_text, encoding="utf-8")
    listing = facts.get("restore_list", {})
    revisions = facts.get("alembic_revisions", [])
    checks.update({
        "dump_nonempty": dump.is_file() and dump.stat().st_size > 0,
        "restore_list_entries": listing.get("entries", 0) > 0,
        "every_table_has_data": bool(listing.get("tables")) and not listing.get("tables_missing_data"),
        "single_alembic_revision": len(revisions) == 1,
        "critical_tables_present": all(name in facts.get("row_counts", {}) for name in CRITICAL_TABLES),
    })
    passed = all(value is True for value in checks.values())
    digest = sha256_file(dump) if dump.is_file() else None
    if digest:
        (staging / f"{backup_id}.sha256").write_text(f"{digest}  {dump.name}\n", encoding="ascii")
    metadata = {
        "schema_version": 1, "kind": "agrosat_database_backup", "backup_id": backup_id,
        "database_name": policy.database_name, "source_classification": policy.source_classification,
        "reason": reason, "release_id": release_id, "created_at": iso(started), "completed_at": iso(completed),
        "db_revision": revisions[0] if len(revisions) == 1 else None, "server_version": server_version,
        "pg_dump_version": tool_version(target, "pg_dump"), "format": "custom", "owner_acl_excluded": True,
        "file": {"name": dump.name, "bytes": dump.stat().st_size if dump.is_file() else 0, "sha256": digest},
        "restore_list": listing, "row_counts": facts.get("row_counts", {}),
        "schema_sha256": facts.get("schema_sha256"), "policy_sha256": policy.sha256,
        "validation": {"result": "PASS" if passed else "FAIL", "checks": checks},
        "credential_values_logged": False,
    }
    final = root / backup_id if passed else root / ".quarantine" / backup_id
    write_json_immutable(staging / "metadata.json", metadata)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, final)
    if not passed:
        raise ControlPlaneError("BACKUP_VALIDATION_FAILED", "backup quarantined for inspection",
                                backup_id=backup_id, checks=checks)
    return {**metadata, "directory": str(final)}


def load_backup(policy: BackupPolicy, backup_id: str, *, rehash: bool = True) -> dict[str, Any]:
    """A sealed, validated backup, re-verified against its own metadata."""
    if not isinstance(backup_id, str) or not BACKUP_ID_PATTERN.fullmatch(backup_id):
        raise ControlPlaneError("BACKUP_ID_REJECTED")
    directory = policy.backup_root / backup_id
    if not directory.is_dir() or is_reparse_point(directory):
        raise ControlPlaneError("BACKUP_MISSING", backup_id)
    metadata = read_json(directory / "metadata.json", "BACKUP_METADATA_UNREADABLE")
    if metadata.get("backup_id") != backup_id or metadata.get("validation", {}).get("result") != "PASS":
        raise ControlPlaneError("BACKUP_NOT_VALIDATED", backup_id)
    if metadata.get("database_name") != policy.database_name:
        raise ControlPlaneError("BACKUP_DATABASE_MISMATCH", backup_id)
    dump = directory / metadata["file"]["name"]
    if rehash and (not dump.is_file() or sha256_file(dump) != metadata["file"]["sha256"]):
        raise ControlPlaneError("BACKUP_HASH_MISMATCH", backup_id)
    return {**metadata, "directory": str(directory)}


def inventory(policy: BackupPolicy) -> list[dict[str, Any]]:
    """Every direct child of the backup root, classified; never follows reparse points."""
    items = []
    for child in sorted(policy.backup_root.iterdir()):
        if child.name in (".incomplete", ".quarantine") or child.name.endswith(".jsonl") or child.name == "executions":
            continue
        entry = {"name": child.name, "path": str(child), "status": "unverified"}
        if is_reparse_point(child) or not child.is_dir() or not BACKUP_ID_PATTERN.fullmatch(child.name):
            entry["status"] = "foreign"
            items.append(entry)
            continue
        try:
            metadata = read_json(child / "metadata.json", "BACKUP_METADATA_UNREADABLE")
            if metadata.get("backup_id") == child.name and metadata.get("validation", {}).get("result") == "PASS" \
                    and metadata.get("database_name") == policy.database_name:
                entry.update(status="validated", created_at=metadata["created_at"],
                             sha256=metadata["file"]["sha256"], bytes=metadata["file"]["bytes"])
        except ControlPlaneError:
            pass
        items.append(entry)
    return items


# ------------------------------------------------------------------ secondary copy

def copy_secondary(policy: BackupPolicy, backup_id: str) -> dict[str, Any]:
    if policy.secondary_destination is None:
        return {"required": False, "status": "not_configured", "fully_protected": not policy.secondary_required}
    metadata = load_backup(policy, backup_id)
    source = Path(metadata["directory"])
    destination_root = policy.secondary_destination
    if not destination_root.is_dir() or is_reparse_point(destination_root):
        raise ControlPlaneError("BACKUP_SECONDARY_UNAVAILABLE")
    final = destination_root / backup_id
    staging = destination_root / f".incoming-{backup_id}"
    if final.exists():
        raise ControlPlaneError("BACKUP_SECONDARY_ALREADY_EXISTS", backup_id)
    shutil.copytree(source, staging)
    os.replace(staging, final)
    copied_sha = sha256_file(final / metadata["file"]["name"])
    metadata_equal = sha256_file(final / "metadata.json") == sha256_file(source / "metadata.json")
    record = {"schema_version": 1, "kind": "agrosat_database_backup_secondary", "backup_id": backup_id,
              "destination": str(final), "copied_at": iso(utc_now()), "primary_sha256": metadata["file"]["sha256"],
              "secondary_sha256": copied_sha, "metadata_copied": metadata_equal,
              "secondary_exists": (final / metadata["file"]["name"]).is_file(),
              "verified": copied_sha == metadata["file"]["sha256"] and metadata_equal}
    write_json_immutable(source / "secondary.json", record)
    write_json_immutable(final / "secondary.json", record)
    if not record["verified"]:
        raise ControlPlaneError("BACKUP_SECONDARY_VERIFICATION_FAILED", backup_id)
    return {**record, "required": policy.secondary_required, "fully_protected": True}


def protection_status(policy: BackupPolicy, backup_id: str) -> dict[str, Any]:
    metadata = load_backup(policy, backup_id)
    secondary_path = Path(metadata["directory"]) / "secondary.json"
    secondary = read_json(secondary_path, "BACKUP_SECONDARY_RECORD_UNREADABLE") if secondary_path.exists() else None
    verified = bool(secondary and secondary.get("verified"))
    return {"backup_id": backup_id, "primary_validated": True, "secondary_required": policy.secondary_required,
            "secondary_verified": verified,
            "fully_protected": verified or not policy.secondary_required}


# ------------------------------------------------------------------ retention

def plan_retention(policy: BackupPolicy, *, protected_ids: set[str], now=None) -> dict[str, Any]:
    if policy.retention is None:
        raise ControlPlaneError("BACKUP_RETENTION_NOT_CONFIGURED", "retention needs explicit policy values")
    now = now or utc_now()
    rules = policy.retention
    items = inventory(policy)
    validated = sorted((item for item in items if item["status"] == "validated"),
                       key=lambda item: item["created_at"], reverse=True)
    keep: dict[str, list[str]] = {}

    def mark(name: str, why: str) -> None:
        keep.setdefault(name, []).append(why)

    if validated:
        mark(validated[0]["name"], "newest_validated")
    for item in validated[:rules["minimum_backup_count"]]:
        mark(item["name"], "minimum_backup_count")
    days, weeks = [], []
    for item in validated:
        created = datetime.fromisoformat(item["created_at"]).astimezone(timezone.utc)
        day, week = created.date().isoformat(), "%04d-W%02d" % created.isocalendar()[:2]
        if day not in days and len(days) < rules["keep_daily_count"]:
            days.append(day)
            mark(item["name"], f"daily:{day}")
        if week not in weeks and len(weeks) < rules["keep_weekly_count"]:
            weeks.append(week)
            mark(item["name"], f"weekly:{week}")
        if now - created < timedelta(hours=rules["minimum_age_before_delete_hours"]):
            mark(item["name"], "younger_than_minimum_age")
        if item["name"] in protected_ids:
            mark(item["name"], "referenced_by_release_or_rollback")
    delete = [{"backup_id": item["name"], "sha256": item["sha256"], "bytes": item["bytes"],
               "created_at": item["created_at"]} for item in validated if item["name"] not in keep]
    plan = {"schema_version": 1, "kind": "agrosat_backup_retention_plan", "backup_root": str(policy.backup_root),
            "database_name": policy.database_name, "policy_sha256": policy.sha256, "rules": rules,
            "planned_at": iso(now), "keep": dict(sorted(keep.items())), "delete": delete,
            "never_deleted": sorted(item["name"] for item in items if item["status"] != "validated")}
    plan["plan_sha256"] = sha256_bytes(canonical_json({key: value for key, value in plan.items() if key != "planned_at"}))
    return plan


def apply_retention(policy: BackupPolicy, *, protected_ids: set[str], expected_plan_sha256: str,
                    now=None) -> dict[str, Any]:
    plan = plan_retention(policy, protected_ids=protected_ids, now=now)
    if plan["plan_sha256"] != expected_plan_sha256:
        raise ControlPlaneError("BACKUP_RETENTION_PLAN_CHANGED", "preview again; the plan changed since it was reviewed")
    root = policy.backup_root.resolve(strict=True)
    deleted = []
    for item in plan["delete"]:
        directory = policy.backup_root / item["backup_id"]
        resolved = directory.resolve(strict=True)
        if resolved.parent != root or not BACKUP_ID_PATTERN.fullmatch(directory.name):
            raise ControlPlaneError("BACKUP_RETENTION_PATH_ESCAPE", item["backup_id"])
        assert_no_reparse_points(directory, "BACKUP_RETENTION_REPARSE_POINT")
        metadata = load_backup(policy, item["backup_id"])
        if metadata["file"]["sha256"] != item["sha256"]:
            raise ControlPlaneError("BACKUP_RETENTION_IDENTITY_CHANGED", item["backup_id"])
        for child in sorted(directory.iterdir()):
            if not child.is_file():
                raise ControlPlaneError("BACKUP_RETENTION_UNEXPECTED_CONTENT", item["backup_id"])
        for child in sorted(directory.iterdir()):
            child.unlink()
        directory.rmdir()
        record = {"deleted_at": iso(utc_now()), "backup_id": item["backup_id"], "sha256": item["sha256"],
                  "bytes": item["bytes"], "plan_sha256": plan["plan_sha256"]}
        append_jsonl(policy.backup_root / "retention-log.jsonl", record)
        deleted.append(record)
    return {**plan, "applied": True, "deleted": deleted}


# ------------------------------------------------------------------ restore

def _restore_into(target: DatabaseTarget, dump: Path, database: str) -> None:
    if target.database_exists(database):
        raise ControlPlaneError("RESTORE_TARGET_EXISTS", "an existing database is never overwritten")
    created = target.run("createdb", ["--no-password", "--template=template0", database],
                         database="postgres", read_only=False, timeout=600)
    if created.returncode != 0:
        raise ControlPlaneError("RESTORE_TARGET_CREATE_FAILED")
    restored = target.run("pg_restore", ["--exit-on-error", "--single-transaction", "--no-owner", "--no-acl",
                                         "--no-password", f"--dbname={database}", str(dump)],
                          database=database, read_only=False, timeout=4 * 3600)
    if restored.returncode != 0:
        raise ControlPlaneError("RESTORE_FAILED", "the partial target is retained for inspection; nothing was dropped",
                                target=database)


def validate_restored(target: DatabaseTarget, database: str, metadata: dict[str, Any], work: Path) -> dict[str, Any]:
    revisions = target.revisions(database=database)
    invalid_constraints = int(target.query(
        "SELECT count(*) FROM pg_constraint WHERE connamespace = 'public'::regnamespace AND NOT convalidated",
        database=database)[0])
    invalid_indexes = int(target.query(
        "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND NOT i.indisvalid",
        database=database)[0])
    # Extension-owned tables (PostGIS spatial_ref_sys) are repopulated by CREATE
    # EXTENSION on restore; the dump holds only their user rows.
    extension_tables = set(target.query(
        "SELECT c.relname FROM pg_class c JOIN pg_depend d ON d.classid = 'pg_class'::regclass "
        "AND d.objid = c.oid AND d.deptype = 'e' WHERE c.relnamespace = 'public'::regnamespace "
        "AND c.relkind = 'r'", database=database))
    expected_counts = {table: count for table, count in metadata["row_counts"].items() if table not in extension_tables}
    counts = {}
    for table in sorted(expected_counts):
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
            raise ControlPlaneError("RESTORE_TABLE_NAME_REJECTED", table)
        counts[table] = int(target.query(f'SELECT count(*) FROM public."{table}"', database=database)[0])
    schema_path = work / f"{database}.schema.sql"
    schema = target.run("pg_dump", ["--schema-only", "--no-owner", "--no-acl", "--no-password", "--file=-"],
                        database=database, timeout=900, stdout_path=schema_path)
    if schema.returncode != 0:
        raise ControlPlaneError("RESTORE_SCHEMA_DUMP_FAILED")
    schema_sha256 = sha256_bytes(normalize_schema_sql(schema_path.read_text(encoding="utf-8", errors="replace")).encode())
    postgis = target.query("SELECT count(*) FROM pg_extension WHERE extname = 'postgis'", database=database)
    checks = {
        "alembic_revision_matches_backup": revisions == [metadata["db_revision"]],
        "no_invalid_constraints": invalid_constraints == 0,
        "no_invalid_indexes": invalid_indexes == 0,
        "row_counts_match_backup": counts == expected_counts,
        "critical_tables_restored": all(name in counts for name in CRITICAL_TABLES),
        "schema_hash_matches_backup": schema_sha256 == metadata["schema_sha256"],
        "postgis_extension_present": postgis == ["1"],
    }
    return {"database": database, "alembic_revisions": revisions, "invalid_constraint_count": invalid_constraints,
            "invalid_index_count": invalid_indexes, "row_counts": counts, "schema_sha256": schema_sha256,
            "extension_tables_not_compared": sorted(extension_tables), "checks": checks, "pass": all(checks.values())}


def restore_rehearsal(policy: BackupPolicy, backup_id: str, target_database: str, evidence_directory: Path) -> dict[str, Any]:
    if not REHEARSAL_TARGET_PATTERN.fullmatch(target_database) or target_database == policy.database_name:
        raise ControlPlaneError("RESTORE_TARGET_REJECTED", "restore rehearsals use a new agrosat_taskNNN_* database")
    metadata = load_backup(policy, backup_id)
    target = policy.target()
    evidence_directory.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    record = {"schema_version": 1, "kind": "agrosat_restore_rehearsal", "backup_id": backup_id,
              "backup_sha256": metadata["file"]["sha256"], "source_database": policy.database_name,
              "target_database": target_database, "started_at": iso(started), "target_created_by_tool": False}
    try:
        _restore_into(target, Path(metadata["directory"]) / metadata["file"]["name"], target_database)
        record["target_created_by_tool"] = True
        validation = validate_restored(target, target_database, metadata, evidence_directory)
        record.update(validation=validation, result="PASS" if validation["pass"] else "FAIL")
    except ControlPlaneError as error:
        record.update(result="FAIL", error=error.evidence(),
                      target_created_by_tool=error.code in ("RESTORE_FAILED",) or record["target_created_by_tool"])
    record["finished_at"] = iso(utc_now())
    record["target_retained"] = True
    write_json_immutable(evidence_directory / f"restore-rehearsal-{target_database}.json", record)
    return record


def drop_rehearsal_target(policy: BackupPolicy, target_database: str, evidence_path: Path) -> dict[str, Any]:
    """Explicit cleanup of a restore-rehearsal target whose evidence is already captured."""
    if not REHEARSAL_TARGET_PATTERN.fullmatch(target_database) or target_database == policy.database_name:
        raise ControlPlaneError("RESTORE_TARGET_REJECTED")
    evidence = read_json(evidence_path, "RESTORE_EVIDENCE_REQUIRED")
    if evidence.get("target_database") != target_database or not evidence.get("target_created_by_tool"):
        raise ControlPlaneError("RESTORE_EVIDENCE_REQUIRED", "only a target this tool created and recorded")
    target = policy.target()
    result = target.run("dropdb", ["--no-password", target_database], database="postgres", read_only=False)
    if result.returncode != 0:
        raise ControlPlaneError("RESTORE_TARGET_DROP_FAILED")
    return {"dropped": target_database, "evidence": str(evidence_path), "dropped_at": iso(utc_now())}


def restore_swap(policy: BackupPolicy, backup_id: str, expected_revision: str, tag: str,
                 evidence_directory: Path) -> dict[str, Any]:
    """Rollback case 3: put a validated backup in place WITHOUT destroying current data.

    The backup is restored into a new database, validated, and the two are
    swapped by renaming; the database that was live is kept under its aside
    name. Refuses while any other session uses the live database.
    """
    metadata = load_backup(policy, backup_id)
    if metadata["db_revision"] != expected_revision:
        raise ControlPlaneError("RESTORE_SWAP_REVISION_MISMATCH")
    database = policy.database_name
    restored, aside = f"{database}_rb_{tag}", f"{database}_pre_rb_{tag}"
    target = policy.target()
    for name in (restored, aside):
        if target.database_exists(name):
            raise ControlPlaneError("RESTORE_SWAP_NAME_EXISTS", name)
    evidence_directory.mkdir(parents=True, exist_ok=True)
    _restore_into(target, Path(metadata["directory"]) / metadata["file"]["name"], restored)
    validation = validate_restored(target, restored, metadata, evidence_directory)
    if not validation["pass"]:
        raise ControlPlaneError("RESTORE_SWAP_VALIDATION_FAILED", facts=validation["checks"])
    sessions = target.query(f"SELECT count(*) FROM pg_stat_activity WHERE datname IN ('{database}', '{restored}') "
                            "AND pid <> pg_backend_pid()", database="postgres")
    if sessions != ["0"]:
        raise ControlPlaneError("RESTORE_SWAP_DATABASE_IN_USE", "other sessions are connected; nothing was renamed")
    target.query(f'ALTER DATABASE "{database}" RENAME TO "{aside}"', database="postgres", read_only=False)
    try:
        target.query(f'ALTER DATABASE "{restored}" RENAME TO "{database}"', database="postgres", read_only=False)
    except ControlPlaneError:
        target.query(f'ALTER DATABASE "{aside}" RENAME TO "{database}"', database="postgres", read_only=False)
        raise
    record = {"backup_id": backup_id, "backup_sha256": metadata["file"]["sha256"], "restored_revision": expected_revision,
              "live_database": database, "previous_live_database_kept_as": aside, "validation": validation,
              "swapped_at": iso(utc_now()), "data_destroyed": False}
    write_json_atomic(evidence_directory / "restore-swap.json", record)
    return record
