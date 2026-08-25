import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops" / "release"


def ps(script, *args, check=True, confirm_false=False):
    script_path = str(OPS / script)
    if confirm_false:
        quoted_script = script_path.replace("'", "''")
        assert len(args) % 2 == 0
        bound = []
        for index in range(0, len(args), 2):
            parameter = args[index]
            assert parameter.startswith("-") and parameter[1:].isidentifier()
            value = args[index + 1].replace("'", "''")
            bound.append(f"{parameter} '{value}'")
        invocation = ["powershell", "-NoProfile", "-NonInteractive", "-Command", f"& '{quoted_script}' -Confirm:$false " + " ".join(bound)]
    else:
        invocation = ["powershell", "-NoProfile", "-NonInteractive", "-File", script_path, *args]
    result = subprocess.run(invocation, capture_output=True, text=True, check=check, timeout=20)
    print(f"returncode={result.returncode}")
    print(f"stdout={result.stdout!r}")
    print(f"stderr={result.stderr!r}")
    return result


def marker_lines(path):
    return path.read_text().splitlines() if path.exists() else []


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def fixture(tmp_path, health="success", restore="success"):
    candidate, prior = "a" * 40, "b" * 40
    prior_hash = "c" * 64
    database = "agrosat_r3_task218_runtime"
    root = tmp_path / "root with spaces"
    scripts = root / "scripts with spaces"
    scripts.mkdir(parents=True)
    prior_dir = root / "release" / prior
    prior_dir.mkdir(parents=True)
    prior_pointer = {"release_candidate": prior, "release_directory": str(prior_dir), "archive_sha256": prior_hash}
    (root / "current-release.json").write_text(json.dumps(prior_pointer))
    (scripts / "migration.ps1").write_text('param($RehearsalDatabase,$ReleaseDirectory,$ReleaseCandidate)\nAdd-Content -LiteralPath (Join-Path $PSScriptRoot "migration.marker") -Value $ReleaseCandidate\n')
    health_body = 'Add-Content -LiteralPath (Join-Path $PSScriptRoot "health.marker") -Value $ReleaseCandidate' if health == "success" else "throw 'DUMMY_HEALTH_FAILURE'"
    (scripts / "health.ps1").write_text(f"param($BaseUrl,$ReleaseCandidate)\n{health_body}\n")
    restore_body = ('Add-Content -LiteralPath (Join-Path $PSScriptRoot "restore.marker") -Value $ReleaseCandidate\n'
                    '$pointer=Get-Content -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) "current-release.json") -Raw | ConvertFrom-Json\n'
                    'Set-Content -LiteralPath (Join-Path $PSScriptRoot "restore-observed-pointer.marker") -Value $pointer.release_candidate') if restore == "success" else "throw 'DUMMY_RESTORE_FAILURE'"
    (scripts / "restore.ps1").write_text(f"param($RehearsalDatabase,$ValidatedBackup,$ReleaseCandidate)\n{restore_body}\n")
    backup = root / "validated backup.dump"
    backup.write_bytes(b"isolated backup")
    backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    identity = root / "backup identity.json"
    identity.write_text(json.dumps({"backup_sha256": backup_hash.upper(), "rehearsal_database": database}))
    archive = root / "candidate archive.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("release-manifest.json", json.dumps({"schema_version": 1, "git_sha": candidate, "branch": "task/program-r3-mega-repair", "accepted_source_baseline": "40e8e379d9d29cb4bfb8afebdd9c489c19756fac", "created_utc": "2026-08-17T00:00:00Z"}))
        package.writestr("app.txt", "immutable")
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"release_candidate": candidate, "program_head": candidate, "origin_program_head": candidate, "program_worktree_clean": True, "origin_aligned": True, "source_main_unchanged": True, "source_archive_sha256": archive_hash}))
    return {"root": root, "scripts": scripts, "candidate": candidate, "prior": prior, "prior_hash": prior_hash, "database": database, "prior_pointer": prior_pointer, "backup": backup, "backup_hash": backup_hash, "identity": identity, "archive": archive, "archive_hash": archive_hash, "manifest": manifest}


def release_args(f, **overrides):
    values = {"RehearsalRoot": f["root"], "RehearsalDatabase": f["database"], "SourceArchive": f["archive"], "SourceArchiveSha256": f["archive_hash"], "ManifestPath": f["manifest"], "DatabaseMigrationScript": f["scripts"] / "migration.ps1", "HealthCheckScript": f["scripts"] / "health.ps1", "HealthBaseUrl": "http://127.0.0.1:1", "ValidatedBackup": f["backup"], "ValidatedBackupSha256": f["backup_hash"], "BackupIdentityPath": f["identity"], "DatabaseRestoreScript": f["scripts"] / "restore.ps1", "ReleaseCandidate": f["candidate"]}
    values.update(overrides)
    return [item for key, value in values.items() for item in (f"-{key}", str(value))]


def rollback_args(f, expected_hash=None):
    values = {"ReleaseCandidate": f["candidate"], "RehearsalRoot": f["root"], "RehearsalDatabase": f["database"], "ValidatedBackup": f["backup"], "ValidatedBackupSha256": f["backup_hash"], "BackupIdentityPath": f["identity"], "ManifestPath": f["manifest"], "DatabaseRestoreScript": f["scripts"] / "restore.ps1", "ExpectedPreviousArchiveSha256": expected_hash or f["prior_hash"].upper()}
    return [item for key, value in values.items() for item in (f"-{key}", str(value))]


def test_successful_isolated_release_runs_migration_health_and_switches_exact_pointers(tmp_path):
    f = fixture(tmp_path)
    result = ps("Invoke-AgroSatRelease.ps1", *release_args(f), confirm_false=True)
    assert json.loads(result.stdout)["status"] == "PASS"
    assert marker_lines(f["scripts"] / "migration.marker") == [f["candidate"]]
    assert marker_lines(f["scripts"] / "health.marker") == [f["candidate"]]
    assert read_json(f["root"] / "current-release.json")["release_candidate"] == f["candidate"]
    assert read_json(f["root"] / "previous-release.json") == f["prior_pointer"]


def test_isolated_release_health_failure_restores_database_before_previous_pointer(tmp_path):
    f = fixture(tmp_path, health="fail")
    result = ps("Invoke-AgroSatRelease.ps1", *release_args(f), check=False, confirm_false=True)
    assert result.returncode != 0 and "ISOLATED_POST_SWITCH_HEALTH_FAILED_ROLLED_BACK" in result.stderr
    assert marker_lines(f["scripts"] / "migration.marker") == [f["candidate"]]
    assert marker_lines(f["scripts"] / "restore.marker") == [f["candidate"]]
    assert (f["scripts"] / "restore-observed-pointer.marker").read_text().strip() == f["candidate"]
    assert read_json(f["root"] / "current-release.json") == f["prior_pointer"]


def test_failed_health_recovery_restore_leaves_candidate_pointer_for_manual_recovery(tmp_path):
    f = fixture(tmp_path, health="fail", restore="fail")
    result = ps("Invoke-AgroSatRelease.ps1", *release_args(f), check=False, confirm_false=True)
    assert "HEALTH_FAILURE_DATABASE_RESTORE_FAILED_MANUAL_RECOVERY_REQUIRED" in result.stderr
    assert read_json(f["root"] / "current-release.json")["release_candidate"] == f["candidate"]


def test_explicit_rollback_hash_mismatch_precedes_restore_and_pointer_mutation(tmp_path):
    f = fixture(tmp_path)
    ps("Invoke-AgroSatRelease.ps1", *release_args(f), confirm_false=True)
    before = (f["root"] / "current-release.json").read_text()
    result = ps("Invoke-AgroSatRollback.ps1", *rollback_args(f, "d" * 64), check=False, confirm_false=True)
    assert "ISOLATED_PREVIOUS_RELEASE_HASH_MISMATCH" in result.stderr
    assert not (f["scripts"] / "restore.marker").exists()
    assert (f["root"] / "current-release.json").read_text() == before


def test_successful_explicit_rollback_accepts_normalized_hash_and_restores_exact_pointer(tmp_path):
    f = fixture(tmp_path)
    ps("Invoke-AgroSatRelease.ps1", *release_args(f), confirm_false=True)
    result = ps("Invoke-AgroSatRollback.ps1", *rollback_args(f), confirm_false=True)
    assert json.loads(result.stdout)["status"] == "PASS"
    assert marker_lines(f["scripts"] / "restore.marker") == [f["candidate"]]
    assert read_json(f["root"] / "current-release.json") == f["prior_pointer"]


@pytest.mark.parametrize("case,expected", [("missing", "IDENTITY_MISSING"), ("malformed", "IDENTITY_MALFORMED"), ("backup", "BACKUP_HASH_MISMATCH"), ("sha", "IDENTITY_SHA_MISMATCH"), ("database", "IDENTITY_DATABASE_MISMATCH"), ("outside", "RESTORE_SCRIPT_OUTSIDE")])
def test_health_recovery_identity_and_restore_path_fail_before_restore(tmp_path, case, expected):
    f = fixture(tmp_path, health="fail")
    overrides = {}
    if case == "missing": overrides["BackupIdentityPath"] = f["root"] / "missing.json"
    elif case == "malformed": f["identity"].write_text("not-json")
    elif case == "backup": overrides["ValidatedBackupSha256"] = "d" * 64
    elif case == "sha": f["identity"].write_text(json.dumps({"backup_sha256": "d" * 64, "rehearsal_database": f["database"]}))
    elif case == "database": f["identity"].write_text(json.dumps({"backup_sha256": f["backup_hash"], "rehearsal_database": "agrosat_r3_task218_other"}))
    else:
        outside = tmp_path / "outside restore.ps1"
        outside.write_text("param($RehearsalDatabase,$ValidatedBackup,$ReleaseCandidate)\n")
        overrides["DatabaseRestoreScript"] = outside
    result = ps("Invoke-AgroSatRelease.ps1", *release_args(f, **overrides), check=False, confirm_false=True)
    assert expected in result.stderr
    assert not (f["scripts"] / "restore.marker").exists()


def test_rehearsal_guards_reject_product_database_and_nonisolated_roots(tmp_path):
    candidate = "a" * 40
    allowed = tmp_path / "allowed"
    bad_db = ps("Test-AgroSatProductionPreflight.ps1", "-ReleaseCandidate", candidate, "-RehearsalRoot", str(allowed), "-RehearsalDatabase", "agrosat", check=False)
    staging_db = ps("Test-AgroSatProductionPreflight.ps1", "-ReleaseCandidate", candidate, "-RehearsalRoot", str(allowed), "-RehearsalDatabase", "staging", check=False)
    bad_root = ps("Test-AgroSatProductionPreflight.ps1", "-ReleaseCandidate", candidate, "-RehearsalRoot", "C:\\AgroSat", "-RehearsalDatabase", "agrosat_r3_rc_test", check=False)
    staging_root = ps("Test-AgroSatProductionPreflight.ps1", "-ReleaseCandidate", candidate, "-RehearsalRoot", "C:\\AgroSat_staging", "-RehearsalDatabase", "agrosat_r3_rc_test", check=False)
    assert "REHEARSAL_DATABASE_GUARD_FAILED" in bad_db.stderr
    assert "REHEARSAL_DATABASE_GUARD_FAILED" in staging_db.stderr
    assert "REHEARSAL_ROOT_GUARD_FAILED" in bad_root.stderr
    assert "REHEARSAL_ROOT_GUARD_FAILED" in staging_root.stderr
