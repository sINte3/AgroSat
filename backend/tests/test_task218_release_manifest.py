"""The manifest identity of this very checkout (TASK_218 intent, TASK_230 manifest).

The manifest takes production's reference from fetched remote refs only, never
from a local checkout's branch or HEAD. Once this checkout's commit is
published, its identity against origin/main is computed exactly.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
CLI = REPOSITORY / "ops" / "release" / "Invoke-AgroSatControlPlane.py"


def git(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPOSITORY), *arguments], capture_output=True, text=True)


def test_task218_manifest_takes_production_identity_from_remote_refs_only(capsys):
    if not (REPOSITORY / ".git").exists():
        pytest.skip("requires a Git checkout")
    candidate = git("rev-parse", "HEAD").stdout.strip()
    origin_main = git("rev-parse", "--verify", "origin/main").stdout.strip()
    published = git("for-each-ref", "--contains", candidate, "--format=%(refname)", "refs/remotes/origin/").stdout.split()
    if not origin_main or not published or candidate == origin_main:
        pytest.skip("the manifest identity is qualified once this commit is published beyond origin/main")
    spec = importlib.util.spec_from_file_location("controlplane_cli", CLI)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.main(["manifest", "--repository", str(REPOSITORY), "--candidate", candidate,
                     "--expected-current", origin_main, "--no-fetch"]) == 0
    report = json.loads(capsys.readouterr().out)
    identity = report["identity"]
    assert identity["candidate_sha"] == candidate and identity["expected_current_sha"] == origin_main
    assert identity["remote_main"] == origin_main and identity["remote_main_is_expected_current"] is True
    assert identity["candidate_fast_forwards_remote_main"] is True
    assert identity["containing_remote_refs"] and all(ref.startswith("origin/") for ref in identity["containing_remote_refs"])
    assert "source_checkout" not in json.dumps(report) and report["mutation_performed"] is False
