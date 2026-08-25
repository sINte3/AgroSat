import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_CHECKOUT = Path(r"C:\AgroSat")
SCRIPT = REPOSITORY / "ops" / "release" / "New-ReleaseManifest.ps1"


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_task218_manifest_separates_origin_main_from_local_checkout_identity():
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None or not (REPOSITORY / ".git").exists():
        pytest.skip("TASK_218 release-branch identity test requires a Git worktree on Windows")
    if not (SOURCE_CHECKOUT / ".git").exists():
        pytest.skip("TASK_218 production source checkout is not available")

    candidate = git(REPOSITORY, "rev-parse", "HEAD")
    branch = git(REPOSITORY, "branch", "--show-current")
    source_head = git(SOURCE_CHECKOUT, "rev-parse", "HEAD")
    origin_main = git(REPOSITORY, "rev-parse", "origin/main")
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT),
            "-ProgramWorktree",
            str(REPOSITORY),
            "-SourceCheckout",
            str(SOURCE_CHECKOUT),
            "-SourceBaseline",
            origin_main,
            "-SourceCheckoutBaseline",
            source_head,
            "-ProgramBranch",
            branch,
            "-ReleaseCandidate",
            candidate,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout.lstrip("\ufeff"))

    assert report["source_baseline"] == origin_main
    assert report["source_checkout_baseline"] == source_head
    assert report["source_checkout_head"] == source_head
    assert report["source_origin_main"] == origin_main
    assert report["source_main_unchanged"] is True

