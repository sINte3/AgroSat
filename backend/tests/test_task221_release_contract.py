"""Release, qualification, and rollback safety contracts for TASK_221."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "ops" / "release"
QUALIFICATION = ROOT / "ops" / "qualification"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_task221_release_roots_and_database_prefix_are_explicitly_guarded():
    preflight = read(RELEASE / "Test-AgroSatProductionPreflight.ps1")
    archive = read(RELEASE / "New-AgroSatReleaseArchive.ps1")
    manifest = read(RELEASE / "New-ReleaseManifest.ps1")
    expected_root = "PROGRAM_R3_MACROSTAGE_G_OPERATIONAL_COMMAND_CENTER"
    expected_worktree = "program-r3-macrostage-g-operational-command-center"
    assert expected_root in preflight and expected_root in archive and expected_root in manifest
    assert expected_worktree in archive and expected_worktree in manifest
    assert "^agrosat_r3_task221_[a-z0-9_]+$" in preflight
    assert "task/program-r3-macrostage-g-operational-command-center" in manifest
    assert "387eaeda6bcbcc3ef0a2e2951b8f87bbf75ad927" in manifest


def test_task221_preflight_accepts_only_matching_g_database_namespace():
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is required")
    run_root = Path(
        r"C:\AgroSat_backups\PROGRAM_R3_MACROSTAGE_G_OPERATIONAL_COMMAND_CENTER"
    ) / "RUNS" / "contract-fixture"
    candidate = "a" * 40
    accepted = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(RELEASE / "Test-AgroSatProductionPreflight.ps1"),
            "-ReleaseCandidate",
            candidate,
            "-RehearsalRoot",
            str(run_root),
            "-RehearsalDatabase",
            "agrosat_r3_task221_contract",
        ],
        capture_output=True,
        text=True,
    )
    assert accepted.returncode == 0, accepted.stderr
    rejected = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(RELEASE / "Test-AgroSatProductionPreflight.ps1"),
            "-ReleaseCandidate",
            candidate,
            "-RehearsalRoot",
            str(run_root),
            "-RehearsalDatabase",
            "agrosat_r3_task220_contract",
        ],
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "REHEARSAL_DATABASE_GUARD_FAILED" in rejected.stderr


def test_task221_rollback_contract_classifies_populated_history_as_restore_only():
    contract = json.loads(read(RELEASE / "rollback-contract.json"))
    classifications = {
        item["revision"]: item for item in contract["migration_classifications"]
    }
    task221 = classifications["0016_operational_command_center"]
    assert task221["path"] == "backend/alembic/versions/0016_operational_command_center.py"
    assert task221["safe_before_data"] is True
    assert task221["human_approval_required"] is True
    assert task221["production_applied"] is False
    assert task221["strategy_after_data"] == "restore_validated_pre_task221_backup_or_roll_forward"


def test_task221_protected_qualification_tools_are_fail_closed():
    protected = read(QUALIFICATION / "Run-Task221Protected.py")
    database = read(QUALIFICATION / "Run-Task221DatabaseQualification.py")
    runtime = read(QUALIFICATION / "Prepare-Task221RuntimeDatabase.py")
    browser = read(QUALIFICATION / "Run-Task221BrowserProtected.py")
    workflow = read(QUALIFICATION / "Run-Task221WorkflowQualification.py")
    for source in (protected, database, runtime, browser, workflow):
        assert "agrosat_r3_task221_" in source
        assert "TASK221_DATABASE_IDENTITY_REJECTED" in source
    assert '"WIALON_ENABLED": "false"' in protected
    assert '"TELEGRAM_NOTIFICATIONS_ENABLED": "false"' in protected
    assert "0016_operational_command_center" in database
    assert "0016_operational_command_center" in runtime
    assert "populated operational history downgrade must fail closed" in workflow
    assert '"database_urls_included": False' in database


def test_task221_release_and_qualification_sources_do_not_mutate_git_history():
    combined = "\n".join(
        read(path).lower()
        for root in (RELEASE, QUALIFICATION)
        for path in root.iterdir()
        if path.suffix in {".ps1", ".py", ".json", ".mjs"}
    )
    for forbidden in (
        "git reset",
        "git rebase",
        "git checkout",
        "git switch",
        "git push --force",
        "git commit --amend",
    ):
        assert forbidden not in combined
