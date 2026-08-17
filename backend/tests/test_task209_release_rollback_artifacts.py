"""Read-only release manifest and rollback drill contracts."""

import json
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops" / "release"


def powershell(script: str, *arguments: str, check: bool = True):
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(OPS / script),
            *arguments,
        ],
        check=check,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_release_powershell_artifacts_parse():
    for script in sorted(OPS.glob("*.ps1")):
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


def test_release_archive_validator_covers_safe_immutable_archive_contract(tmp_path):
    import hashlib
    import zipfile

    candidate = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest = {
        "schema_version": 1,
        "git_sha": candidate,
        "branch": "task/program-r3-mega-repair",
        "accepted_source_baseline": "40e8e379d9d29cb4bfb8afebdd9c489c19756fac",
        "created_utc": "2026-08-17T00:00:00Z",
    }
    archive = tmp_path / "archive with spaces.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("release-manifest.json", json.dumps(manifest))
        package.writestr("app/readme.txt", "safe")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    destination = tmp_path / "extract with spaces"
    result = powershell(
        "Test-AgroSatReleaseArchive.ps1",
        "-ArchivePath",
        str(archive),
        "-ExpectedSha256",
        digest,
        "-ReleaseCandidate",
        candidate,
        "-DestinationPath",
        str(destination),
    )
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    assert (destination / "app" / "readme.txt").read_text() == "safe"


def test_release_archive_validator_fails_closed_for_identity_and_unsafe_path(tmp_path):
    import hashlib
    import zipfile

    candidate = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    archive = tmp_path / "unsafe.zip"
    manifest = {
        "schema_version": 1,
        "git_sha": candidate,
        "branch": "task/program-r3-mega-repair",
        "accepted_source_baseline": "40e8e379d9d29cb4bfb8afebdd9c489c19756fac",
        "created_utc": "2026-08-17T00:00:00Z",
    }
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("release-manifest.json", json.dumps(manifest))
        package.writestr("../escape.txt", "blocked")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    result = powershell(
        "Test-AgroSatReleaseArchive.ps1",
        "-ArchivePath",
        str(archive),
        "-ExpectedSha256",
        digest,
        "-ReleaseCandidate",
        candidate,
        check=False,
    )
    assert result.returncode != 0
    assert "RELEASE_ARCHIVE_UNSAFE_PATH" in result.stderr
    result = powershell(
        "Test-AgroSatReleaseArchive.ps1",
        "-ArchivePath",
        str(archive),
        "-ExpectedSha256",
        "0" * 64,
        "-ReleaseCandidate",
        candidate,
        check=False,
    )
    assert result.returncode != 0
    assert "RELEASE_ARCHIVE_HASH_MISMATCH" in result.stderr


def test_manifest_preview_proves_source_and_program_integrity():
    candidate = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    result = powershell("New-ReleaseManifest.ps1", "-ReleaseCandidate", candidate)
    report = json.loads(result.stdout)
    assert report["source_baseline"] == (
        "40e8e379d9d29cb4bfb8afebdd9c489c19756fac"
    )
    assert report["program_branch"] == "task/program-r3-mega-repair"
    assert report["observed_branch"] == "task/program-r3-mega-repair"
    assert report["release_candidate"] == candidate
    assert report["program_head"] == candidate
    assert report["origin_program_head"] == candidate
    assert report["source_main_unchanged"] is True
    assert report["origin_aligned"] is True
    assert report["production_deployed"] is False
    assert report["production_database_changed"] is False
    assert len(report["apply_order"]) == 8


def test_release_scripts_require_explicit_candidate_and_full_untracked_cleanliness():
    manifest = (OPS / "New-ReleaseManifest.ps1").read_text(encoding="utf-8")
    preflight = (OPS / "Test-AgroSatProductionPreflight.ps1").read_text(
        encoding="utf-8"
    )
    health = (OPS / "Test-AgroSatReleaseHealth.ps1").read_text(encoding="utf-8")
    release = (OPS / "Invoke-AgroSatRelease.ps1").read_text(encoding="utf-8")
    rollback = (OPS / "Invoke-AgroSatRollback.ps1").read_text(encoding="utf-8")

    assert "--untracked-files=all" in manifest
    assert "--untracked-files=no" not in manifest
    for script in (manifest, preflight, health, release, rollback):
        assert "ReleaseCandidate" in script
    for script in (preflight, health, release, rollback):
        assert "40e8e379d9d29cb4bfb8afebdd9c489c19756fac" not in script


def test_release_and_rollback_contracts_require_real_isolated_pointer_mutation():
    release = (OPS / "Invoke-AgroSatRelease.ps1").read_text(encoding="utf-8")
    rollback = (OPS / "Invoke-AgroSatRollback.ps1").read_text(encoding="utf-8")
    assert "current-release.json" in release
    assert "previous-release.json" in release
    assert "isolated_release_materialized_database_migrated_and_pointer_switched" in release
    assert "DatabaseMigrationScript" in release
    assert "current-release.json" in rollback
    assert "previous-release.json" in rollback
    assert "isolated_database_restored_and_release_pointer_restored" in rollback
    assert "DatabaseRestoreScript" in rollback
    assert "database_restore_executed = (-not $WhatIfPreference)" in rollback
    assert "mutation_performed = (-not $WhatIfPreference)" in rollback
    assert "mutation_performed=$false" not in rollback


def test_rollback_contract_covers_every_required_component():
    contract = json.loads(
        (OPS / "rollback-contract.json").read_text(encoding="utf-8")
    )
    assert {item["name"] for item in contract["components"]} == {
        "application",
        "migration",
        "collector",
        "frontend_assets",
        "scheduled_task",
    }
    assert all(item["automatic"] is False for item in contract["components"])
    classifications = {
        item["revision"]: item for item in contract["migration_classifications"]
    }
    closure = classifications["0006_operational_closure"]
    assert closure["strategy_after_data"] == "roll_forward_only"
    assert closure["safe_before_data"] is True
    assert closure["human_approval_required"] is True
    assert closure["production_applied"] is False


def test_rollback_drill_passes_for_clean_manifest_without_migrations():
    manifest = {
        "source_main_unchanged": True,
        "program_worktree_clean": True,
        "origin_aligned": True,
        "production_deployed": False,
        "production_database_changed": False,
        "changed_migrations": [],
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        result = powershell(
            "Test-RollbackReadiness.ps1",
            "-ManifestPath",
            str(path),
        )
    report = json.loads(result.stdout)
    assert report["decision"] == "PASS"
    assert report["application_rollback_executed"] is False
    assert report["migration_downgrade_executed"] is False
    assert report["scheduled_task_changed"] is False


def test_rollback_drill_blocks_unclassified_migration():
    manifest = {
        "source_main_unchanged": True,
        "program_worktree_clean": True,
        "origin_aligned": True,
        "production_deployed": False,
        "production_database_changed": False,
        "changed_migrations": ["backend/alembic/versions/example.py"],
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        result = powershell(
            "Test-RollbackReadiness.ps1",
            "-ManifestPath",
            str(path),
            check=False,
        )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["decision"] == "BLOCKED"
    assert report["unclassified_migrations"] == [
        "backend/alembic/versions/example.py"
    ]


def test_release_artifacts_contain_no_mutating_git_or_deployment_commands():
    combined = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in OPS.iterdir()
        if path.suffix in {".ps1", ".json"}
    )
    for forbidden in (
        "git reset",
        "git rebase",
        "git checkout",
        "git switch",
        "git push",
        "alembic upgrade",
        "alembic downgrade",
        "register-scheduledtask",
        "unregister-scheduledtask",
    ):
        assert forbidden not in combined
