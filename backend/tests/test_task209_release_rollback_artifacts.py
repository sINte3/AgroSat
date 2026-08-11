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


def test_manifest_preview_proves_source_and_program_integrity():
    result = powershell("New-ReleaseManifest.ps1")
    report = json.loads(result.stdout)
    assert report["source_baseline"] == (
        "40e8e379d9d29cb4bfb8afebdd9c489c19756fac"
    )
    assert report["program_branch"] == "task/program-r3-mega-repair"
    assert report["observed_branch"] == "task/program-r3-mega-repair"
    assert report["source_main_unchanged"] is True
    assert isinstance(report["origin_aligned"], bool)
    assert report["production_deployed"] is False
    assert report["production_database_changed"] is False
    assert len(report["apply_order"]) == 8


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
