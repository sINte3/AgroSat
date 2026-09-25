"""Safety contracts of the one database backup contract (TASK_209 intent, TASK_230 implementation).

The TASK_209 review scripts were replaced by ops/release/controlplane/backup.py
(driven by ``Invoke-AgroSatControlPlane.py backup ...``) plus the Scheduled Task
installer and inspector in ops/database. These tests keep TASK_209's
guarantees: custom-format dump, restore-list validation, SHA-256, sanitized
metadata, isolated non-overwriting restores that are never dropped
automatically, and no credential on any command line or in any evidence.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops" / "database"
BACKUP = ROOT / "ops" / "release" / "controlplane" / "backup.py"
PGCLIENT = ROOT / "ops" / "release" / "controlplane" / "pgclient.py"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
if str(ROOT / "ops" / "release") not in sys.path:
    sys.path.insert(0, str(ROOT / "ops" / "release"))

from controlplane import backup  # noqa: E402


def test_database_ops_directory_holds_only_the_current_contract():
    assert {path.name for path in OPS.iterdir()} == {
        "README.md", "database-backup-policy.example.json", "Install-DatabaseBackupTask.ps1",
        "Inspect-DatabaseBackupTask.ps1"}


def test_backup_is_valid_only_when_the_dump_proves_itself():
    source = BACKUP.read_text(encoding="utf-8")
    for fragment in ('"--format=custom"', '"--no-owner"', '"--no-acl"', '"--list"', "TABLE DATA public",
                     "every_table_has_data", "single_alembic_revision", "critical_tables_present", "schema_sha256",
                     "sha256_file(dump)", '"validation"', '"PASS" if passed else "FAIL"', ".quarantine",
                     "write_json_immutable"):
        assert fragment in source, fragment
    assert "pg_dump_exit_zero" in source  # recorded, but never sufficient on its own


def test_restore_creates_new_isolated_targets_and_never_drops_automatically():
    for accepted in ("agrosat_task230_restore", "agrosat_task209_preview", "agrosat_restore_rehearsal_nightly"):
        assert backup.REHEARSAL_TARGET_PATTERN.fullmatch(accepted)
    for rejected in ("agrosat", "postgres", "agrosat_h0a_task229", "agrosat_task23_x", "agrosat_task230"):
        assert not backup.REHEARSAL_TARGET_PATTERN.fullmatch(rejected)
    source = BACKUP.read_text(encoding="utf-8")
    restore = source[source.index("def restore_rehearsal"):source.index("def drop_rehearsal_target")]
    assert "dropdb" not in restore and "RESTORE_TARGET_EXISTS" in source and "--single-transaction" in source
    assert "target_retained" in restore
    drop = source[source.index("def drop_rehearsal_target"):source.index("def restore_swap")]
    assert "RESTORE_EVIDENCE_REQUIRED" in drop and "target_created_by_tool" in drop
    swap = source[source.index("def restore_swap"):]
    assert "previous_live_database_kept_as" in swap and '"data_destroyed": False' in swap
    assert "pg_stat_activity" in swap and "DROP DATABASE" not in swap.upper() and "RENAME TO" in swap


def test_credentials_never_reach_a_command_line_or_evidence():
    backup_source, client = BACKUP.read_text(encoding="utf-8"), PGCLIENT.read_text(encoding="utf-8")
    assert "PGPASSWORD" not in backup_source and "DATABASE_URL" not in backup_source
    assert client.count('"PGPASSWORD"') == 1  # set only in the child environment builder
    environment = client[client.index("def environment"):client.index("def run(")]
    assert '"PGPASSWORD": password' in environment
    assert "environment.clear()" in client and '"credential_values_logged": False' in backup_source


def test_backup_task_installer_needs_an_explicit_schedule_and_installs_disabled():
    source = (OPS / "Install-DatabaseBackupTask.ps1").read_text(encoding="utf-8")
    for fragment in ("BACKUP_POLICY_IS_EXAMPLE", "BACKUP_POLICY_PLACEHOLDER", "BACKUP_SCHEDULE_REQUIRED",
                     "BACKUP_SCHEDULE_TIME_REJECTED", "-MultipleInstances IgnoreNew", "-ExecutionTimeLimit",
                     "-RestartCount", "Disable-ScheduledTask", "if (-not $Apply)", "$PSCmdlet.ShouldProcess",
                     "backup scheduled --policy"):
        assert fragment in source, fragment
    assert "-Force" not in source and "Unregister-ScheduledTask" not in source


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required")
def test_backup_task_installer_refuses_the_example_policy(tmp_path):
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                             str(OPS / "Install-DatabaseBackupTask.ps1"), "-PolicyPath",
                             str(OPS / "database-backup-policy.example.json"), "-ReleaseDirectory", str(tmp_path),
                             "-ControlRoot", str(tmp_path)], capture_output=True, text=True, timeout=60)
    assert result.returncode != 0 and "BACKUP_POLICY_IS_EXAMPLE" in result.stderr + result.stdout


def test_example_policy_invents_no_production_schedule_or_destination():
    example = json.loads((OPS / "database-backup-policy.example.json").read_text(encoding="utf-8"))
    assert example["example_only"] is True
    assert example["schedule"]["daily_at_local_time"].startswith("<CONFIGURE")
    assert example["backup_root"].startswith("<CONFIGURE")
    assert example["secondary"]["destination"].startswith("<CONFIGURE")
