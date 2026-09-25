"""The database backup contract: policy, retention, secondary copy and restore guards (offline)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from controlplane import backup
from controlplane.common import ControlPlaneError, write_json_immutable

NOW = datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc)
EXAMPLE = Path(__file__).resolve().parents[1] / "database" / "database-backup-policy.example.json"


def policy_file(tmp_path: Path, **overrides) -> Path:
    root = tmp_path / "primary"
    root.mkdir(exist_ok=True)
    document = {"schema_version": 1, "kind": "agrosat_database_backup_policy", "example_only": False,
                "database_name": "agrosat_task230_src", "source_classification": "rehearsal",
                "runtime_env_file": str(tmp_path / "rehearsal.env"), "pg_bin": r"C:\Program Files\PostgreSQL\16\bin",
                "backup_root": str(root),
                "retention": {"keep_daily_count": 3, "keep_weekly_count": 2, "minimum_backup_count": 2,
                              "minimum_age_before_delete_hours": 24},
                "secondary": {"required": False, "destination": None}, "schedule": None}
    document.update(overrides)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def seal(policy, created: datetime, *, valid: bool = True, tag: str = "scheduled") -> str:
    backup_id = f"{policy.database_name}-{created.strftime('%Y%m%dT%H%M%SZ')}-{tag}-{hashlib.sha1(created.isoformat().encode()).hexdigest()[:6]}"
    directory = policy.backup_root / backup_id
    directory.mkdir()
    dump = directory / f"{backup_id}.dump"
    dump.write_bytes(backup_id.encode())
    write_json_immutable(directory / "metadata.json", {
        "backup_id": backup_id, "database_name": policy.database_name, "created_at": created.isoformat(),
        "db_revision": "0016_operational_command_center",
        "file": {"name": dump.name, "bytes": dump.stat().st_size, "sha256": hashlib.sha256(dump.read_bytes()).hexdigest()},
        "validation": {"result": "PASS" if valid else "FAIL"}})
    return backup_id


def test_example_policy_is_never_an_operating_policy():
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert document["example_only"] is True
    with pytest.raises(ControlPlaneError) as caught:
        backup.load_policy(EXAMPLE)
    assert caught.value.code == "BACKUP_POLICY_IS_EXAMPLE"


@pytest.mark.parametrize("overrides,code", [
    ({"backup_root": r"C:\AgroSat_runtime\PROGRAM_R3\backups"}, "BACKUP_ROOT_REJECTED"),
    ({"retention": {"keep_daily_count": 0, "keep_weekly_count": 1, "minimum_backup_count": 1,
                    "minimum_age_before_delete_hours": 1}}, "BACKUP_RETENTION_VALUE_REJECTED"),
    ({"retention": {"keep_daily_count": 3}}, "BACKUP_RETENTION_KEYSET_REJECTED"),
    ({"secondary": {"required": True, "destination": None}}, "BACKUP_SECONDARY_DESTINATION_REQUIRED"),
    ({"source_classification": "production"}, "BACKUP_POLICY_CLASSIFICATION_REJECTED"),
    ({"database_name": "agrosat"}, "BACKUP_POLICY_CLASSIFICATION_REJECTED"),
    ({"schedule": {"task_name": "\\AgroSat_PROGRAM_R3_DatabaseBackup", "daily_at_local_time": "02:30:00",
                   "execution_sid": "S-1-5-18", "execution_time_limit_minutes": 120, "restart_count": 1,
                   "restart_interval_minutes": 15}}, "BACKUP_SCHEDULE_TASK_REJECTED"),
    ({"schedule": {"task_name": "\\AgroSat_TASK230_DatabaseBackup", "daily_at_local_time": "2:30",
                   "execution_sid": "S-1-5-18", "execution_time_limit_minutes": 120, "restart_count": 1,
                   "restart_interval_minutes": 15}}, "BACKUP_SCHEDULE_TIME_REJECTED"),
    ({"backup_root": "<CONFIGURE>"}, "BACKUP_POLICY_PLACEHOLDER"),
])
def test_policy_fails_closed(tmp_path, overrides, code):
    with pytest.raises(ControlPlaneError) as caught:
        backup.load_policy(policy_file(tmp_path, **overrides))
    assert caught.value.code == code


def test_retention_keeps_what_the_rules_require_and_deletes_only_validated_backups(tmp_path):
    policy = backup.load_policy(policy_file(tmp_path))
    ids = [seal(policy, NOW - timedelta(days=days)) for days in (0, 1, 2, 3, 10, 20, 30, 40)]
    unverified = seal(policy, NOW - timedelta(days=50), valid=False)
    foreign = policy.backup_root / "notes"
    foreign.mkdir()
    plan = backup.plan_retention(policy, protected_ids={ids[7]}, now=NOW)
    kept = plan["keep"]
    assert "newest_validated" in kept[ids[0]]
    assert ids[1] in kept and ids[2] in kept  # three newest days
    assert ids[7] in kept and "referenced_by_release_or_rollback" in kept[ids[7]]
    deleted = {item["backup_id"] for item in plan["delete"]}
    assert unverified not in deleted and unverified in plan["never_deleted"] and "notes" in plan["never_deleted"]
    assert deleted and deleted.isdisjoint(kept)
    applied = backup.apply_retention(policy, protected_ids={ids[7]}, expected_plan_sha256=plan["plan_sha256"], now=NOW)
    assert {item["backup_id"] for item in applied["deleted"]} == deleted
    for backup_id in deleted:
        assert not (policy.backup_root / backup_id).exists()
    assert (policy.backup_root / unverified).exists() and foreign.exists()
    log = [json.loads(line) for line in (policy.backup_root / "retention-log.jsonl").read_text().splitlines()]
    assert {entry["backup_id"] for entry in log} == deleted


def test_retention_never_deletes_the_newest_or_below_the_minimum_age(tmp_path):
    policy = backup.load_policy(policy_file(tmp_path, retention={
        "keep_daily_count": 1, "keep_weekly_count": 0, "minimum_backup_count": 1,
        "minimum_age_before_delete_hours": 72}))
    recent = [seal(policy, NOW - timedelta(hours=hours)) for hours in (1, 30, 60)]
    plan = backup.plan_retention(policy, protected_ids=set(), now=NOW)
    assert plan["delete"] == []
    assert all(backup_id in plan["keep"] for backup_id in recent)


def test_retention_apply_requires_the_previewed_plan(tmp_path):
    policy = backup.load_policy(policy_file(tmp_path))
    for days in (0, 10, 20, 30, 40):
        seal(policy, NOW - timedelta(days=days))
    plan = backup.plan_retention(policy, protected_ids=set(), now=NOW)
    seal(policy, NOW - timedelta(days=50))  # the world changed after the preview
    with pytest.raises(ControlPlaneError) as caught:
        backup.apply_retention(policy, protected_ids=set(), expected_plan_sha256=plan["plan_sha256"], now=NOW)
    assert caught.value.code == "BACKUP_RETENTION_PLAN_CHANGED"


def test_retention_refuses_a_tampered_backup(tmp_path):
    policy = backup.load_policy(policy_file(tmp_path, retention={
        "keep_daily_count": 1, "keep_weekly_count": 0, "minimum_backup_count": 1, "minimum_age_before_delete_hours": 1}))
    seal(policy, NOW)
    old = seal(policy, NOW - timedelta(days=30))
    plan = backup.plan_retention(policy, protected_ids=set(), now=NOW)
    assert [item["backup_id"] for item in plan["delete"]] == [old]
    (policy.backup_root / old / f"{old}.dump").write_bytes(b"tampered")
    with pytest.raises(ControlPlaneError) as caught:
        backup.apply_retention(policy, protected_ids=set(), expected_plan_sha256=plan["plan_sha256"], now=NOW)
    assert caught.value.code == "BACKUP_HASH_MISMATCH"
    assert (policy.backup_root / old).exists()


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are a Windows reparse point")
def test_retention_refuses_a_reparse_point_escape(tmp_path):
    policy = backup.load_policy(policy_file(tmp_path, retention={
        "keep_daily_count": 1, "keep_weekly_count": 0, "minimum_backup_count": 1, "minimum_age_before_delete_hours": 1}))
    seal(policy, NOW)
    old = seal(policy, NOW - timedelta(days=30))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("must survive")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(policy.backup_root / old / "escape"), str(outside)],
                   check=True, capture_output=True)
    plan = backup.plan_retention(policy, protected_ids=set(), now=NOW)
    with pytest.raises(ControlPlaneError) as caught:
        backup.apply_retention(policy, protected_ids=set(), expected_plan_sha256=plan["plan_sha256"], now=NOW)
    assert caught.value.code == "BACKUP_RETENTION_REPARSE_POINT"
    assert (outside / "keep.txt").read_text() == "must survive"
    os.rmdir(policy.backup_root / old / "escape")


def test_secondary_copy_must_verify_before_a_backup_is_fully_protected(tmp_path):
    secondary = tmp_path / "secondary"
    secondary.mkdir()
    policy = backup.load_policy(policy_file(tmp_path, secondary={"required": True, "destination": str(secondary)}))
    backup_id = seal(policy, NOW)
    assert backup.protection_status(policy, backup_id)["fully_protected"] is False
    record = backup.copy_secondary(policy, backup_id)
    assert record["verified"] and record["primary_sha256"] == record["secondary_sha256"] and record["metadata_copied"]
    assert (secondary / backup_id / f"{backup_id}.dump").is_file()
    assert backup.protection_status(policy, backup_id)["fully_protected"] is True
    with pytest.raises(ControlPlaneError) as caught:
        backup.copy_secondary(policy, backup_id)
    assert caught.value.code == "BACKUP_SECONDARY_ALREADY_EXISTS"


def test_secondary_destination_must_be_distinct_and_present(tmp_path):
    with pytest.raises(ControlPlaneError) as caught:
        backup.load_policy(policy_file(tmp_path, secondary={"required": True, "destination": str(tmp_path / "primary" / "x")}))
    assert caught.value.code == "BACKUP_SECONDARY_DESTINATION_REJECTED"
    policy = backup.load_policy(policy_file(tmp_path, secondary={"required": True, "destination": str(tmp_path / "missing")}))
    with pytest.raises(ControlPlaneError) as caught:
        backup.copy_secondary(policy, seal(policy, NOW))
    assert caught.value.code == "BACKUP_SECONDARY_UNAVAILABLE"


def test_an_unvalidated_backup_is_not_a_backup(tmp_path):
    policy = backup.load_policy(policy_file(tmp_path))
    backup_id = seal(policy, NOW, valid=False)
    with pytest.raises(ControlPlaneError) as caught:
        backup.load_backup(policy, backup_id)
    assert caught.value.code == "BACKUP_NOT_VALIDATED"


@pytest.mark.parametrize("target", ["agrosat", "agrosat_task230_src", "agrosat_h0a_task229", "other_db", "agrosat_task23_x"])
def test_restore_rehearsal_targets_only_new_isolated_databases(tmp_path, target):
    policy = backup.load_policy(policy_file(tmp_path))
    with pytest.raises(ControlPlaneError) as caught:
        backup.restore_rehearsal(policy, seal(policy, NOW), target, tmp_path / "evidence")
    assert caught.value.code == "RESTORE_TARGET_REJECTED"


def test_rehearsal_target_drop_requires_recorded_evidence(tmp_path):
    policy = backup.load_policy(policy_file(tmp_path))
    evidence = tmp_path / "restore.json"
    evidence.write_text(json.dumps({"target_database": "agrosat_task230_restore", "target_created_by_tool": False}))
    with pytest.raises(ControlPlaneError) as caught:
        backup.drop_rehearsal_target(policy, "agrosat_task230_restore", evidence)
    assert caught.value.code == "RESTORE_EVIDENCE_REQUIRED"


def test_schema_normalization_ignores_dump_noise_only():
    first = "-- Dumped by pg_dump 16.14\n\\restrict abc\nCREATE TABLE a (id int);\n\n\\unrestrict abc\n"
    second = "-- Dumped by pg_dump 16.15\n\\restrict xyz\nCREATE TABLE a (id int);\n"
    assert backup.normalize_schema_sql(first) == backup.normalize_schema_sql(second)
    assert backup.normalize_schema_sql(first) != backup.normalize_schema_sql("CREATE TABLE a (id bigint);\n")


def schema(check: str, predicate: str, *, column="status character varying(20) NOT NULL", name="ck_status",
           index_columns="(field_id)") -> str:
    return (f"CREATE TABLE public.t (\n    id integer NOT NULL,\n    {column},\n"
            f"    CONSTRAINT {name} CHECK ({check})\n);\n"
            f"CREATE UNIQUE INDEX uq_active ON public.t USING btree {index_columns} WHERE ({predicate});\n")


def test_schema_fingerprint_survives_expression_redeparse_but_not_real_changes():
    # What pg_dump prints for the source catalog, and for the same constraint after a restore.
    source = schema("((status)::text = ANY ((ARRAY['a'::character varying, 'b'::character varying])::text[]))",
                    "(status)::text = ANY ((ARRAY['a'::character varying])::text[])")
    restored = schema("((status)::text = ANY (ARRAY[('a'::character varying)::text, ('b'::character varying)::text]))",
                      "(status)::text = ANY (ARRAY[('a'::character varying)::text])")
    fingerprint = backup.normalize_schema_sql
    assert fingerprint(source) == fingerprint(restored)
    assert fingerprint(source) != fingerprint(schema("true", "true", column="status text NOT NULL"))
    assert fingerprint(source) != fingerprint(schema("true", "true", name="ck_other"))
    assert fingerprint(source) != fingerprint(schema("true", "true", index_columns="(id)"))
    assert fingerprint(source) != fingerprint(source.replace("    CONSTRAINT ck_status CHECK", "    -- dropped"))
