"""Safety and preview contracts for TASK_209 database recovery artifacts."""

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops" / "database"


def read(name: str) -> str:
    return (OPS / name).read_text(encoding="utf-8-sig")


def run_preview(script: str, *arguments: str) -> dict:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(OPS / script),
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(result.stdout)


def run_script(script: str, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(OPS / script),
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_all_powershell_artifacts_parse():
    scripts = sorted(OPS.glob("*.ps1"))
    assert {path.name for path in scripts} == {
        "Backup-Database.ps1",
        "Restore-Database.ps1",
        "Task209-Database.Common.ps1",
        "Validate-RestoredDatabase.ps1",
    }
    for script in scripts:
        command = (
            "$errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{script}',[ref]$null,[ref]$errors) | Out-Null; "
            "if($errors.Count){exit 2}"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )


def test_backup_preview_is_read_only_and_tool_independent():
    report = run_preview(
        "Backup-Database.ps1",
        "-DatabaseName",
        "agrosat",
        "-OutputDirectory",
        r"C:\AgroSat_backups\task209_global_program\evidence\phase_04_reliability_recovery\backup_preview",
    )
    assert report["status"] == "preview"
    assert report["database_mutation"] is False
    assert report["source_confirmed_read_only"] is False


def test_restore_preview_enforces_isolated_target_without_tools():
    report = run_preview(
        "Restore-Database.ps1",
        "-DatabaseName",
        "agrosat_task209_preview",
        "-BackupPath",
        r"C:\AgroSat_backups\task209_global_program\evidence\phase_04_reliability_recovery\preview.dump",
        "-ExpectedSha256",
        "a" * 64,
        "-OutputDirectory",
        r"C:\AgroSat_backups\task209_global_program\evidence\phase_04_reliability_recovery\restore_preview",
    )
    assert report["status"] == "preview"
    assert report["database_mutation"] is True
    assert report["target_isolation_enforced"] is True
    assert report["automatic_drop_on_failure"] is False


def test_restore_rejects_nonisolated_target_before_tool_resolution():
    result = run_script(
        "Restore-Database.ps1",
        "-DatabaseName",
        "agrosat",
        "-BackupPath",
        r"C:\AgroSat_backups\task209_global_program\evidence\phase_04_reliability_recovery\preview.dump",
        "-ExpectedSha256",
        "a" * 64,
        "-OutputDirectory",
        r"C:\AgroSat_backups\task209_global_program\evidence\phase_04_reliability_recovery\restore_preview",
    )
    assert result.returncode != 0
    assert result.stdout == ""


def test_backup_apply_requires_read_only_source_confirmation():
    result = run_script(
        "Backup-Database.ps1",
        "-DatabaseName",
        "agrosat",
        "-OutputDirectory",
        r"C:\AgroSat_backups\task209_global_program\evidence\phase_04_reliability_recovery\backup_preview",
        "-Apply",
    )
    assert result.returncode != 0
    assert result.stdout == ""


def test_restore_validation_preview_is_read_only():
    report = run_preview(
        "Validate-RestoredDatabase.ps1",
        "-DatabaseName",
        "agrosat_task209_preview",
        "-OutputDirectory",
        r"C:\AgroSat_backups\task209_global_program\evidence\phase_04_reliability_recovery\restore_preview",
    )
    assert report["status"] == "preview"
    assert report["read_only"] is True


def test_recovery_contract_has_checksum_list_revision_counts_and_schema_hash():
    backup = read("Backup-Database.ps1")
    restore = read("Restore-Database.ps1")
    validate = read("Validate-RestoredDatabase.ps1")
    assert "Get-FileHash" in backup
    assert "--list" in backup
    assert "--format=custom" in backup
    assert "Get-FileHash" in restore
    assert "--single-transaction" in restore
    assert "Assert-OutsideSanitizedEvidence" in backup
    assert "Assert-OutsideSanitizedEvidence" in restore
    assert "Alembic" in validate
    assert "table_counts" in validate
    assert "invalid_constraint_count" in validate
    assert "invalid_index_count" in validate
    assert "schema_sha256" in validate


def test_recovery_scripts_have_no_destructive_or_credential_arguments():
    combined = "\n".join(
        read(name)
        for name in (
            "Backup-Database.ps1",
            "Restore-Database.ps1",
            "Validate-RestoredDatabase.ps1",
        )
    ).lower()
    assert "dropdb" not in combined
    assert "drop database" not in combined
    assert "remove-item" not in combined
    assert "database_url" not in combined
    assert "redis_url" not in combined
    assert "credential" not in combined
    assert "--no-password" in combined
