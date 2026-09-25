"""The current release manifest (TASK_230 Part D).

A release is identified by exact immutable facts only: the full candidate SHA
and its Git tree, the exact production SHA it replaces, remote-tracking refs
fetched at manifest time, ancestry between the two, the SHA-256 of the exact
source archive, the Alembic head read from the candidate's own migration
graph, and the runtime/tool contract. No branch name, task name or historical
worktree is part of the identity; any commit that passes the contract can be a
release candidate.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from . import CONTROL_PLANE_VERSION
from .common import ControlPlaneError, iso, read_json, require_sha, sha256_file, utc_now
from .gitmaterial import Git, excluded_tracked_content
from .migration import ordered_revisions, single_head

RUNTIME_CONTRACT = Path(__file__).resolve().parents[1] / "runtime-contract.json"
MANIFEST_SCHEMA_VERSION = 2


def load_runtime_contract(path: Path = RUNTIME_CONTRACT) -> dict[str, Any]:
    contract = read_json(path, "RUNTIME_CONTRACT_UNREADABLE")
    if contract.get("schema_version") != 1 or contract.get("kind") != "agrosat_runtime_contract":
        raise ControlPlaneError("RUNTIME_CONTRACT_SCHEMA_REJECTED")
    return contract


def source_identity(git: Git, *, candidate_sha: str, expected_current_sha: str, remote: str = "origin",
                    fetch: bool = True) -> dict[str, Any]:
    """Candidate and current identities as the fetched remote knows them."""
    require_sha(candidate_sha, "CANDIDATE_SHA_MALFORMED")
    require_sha(expected_current_sha, "CURRENT_SHA_MALFORMED")
    if candidate_sha == expected_current_sha:
        raise ControlPlaneError("CANDIDATE_EQUALS_CURRENT")
    fetched_at = None
    if fetch:
        git.fetch(remote)
        fetched_at = iso(utc_now())
    git.commit(candidate_sha)
    git.commit(expected_current_sha)
    containing = git.remote_refs_containing(candidate_sha, remote)
    if not containing:
        raise ControlPlaneError("CANDIDATE_NOT_PUBLISHED", f"no {remote}/* ref contains the candidate")
    if not git.is_ancestor(expected_current_sha, candidate_sha):
        raise ControlPlaneError("CANDIDATE_ANCESTRY_REJECTED",
                                "the current production commit must be an ancestor of the candidate")
    main = git.ref(f"{remote}/main")
    files = git.tracked_files(candidate_sha)
    excluded = excluded_tracked_content(files)
    if excluded:
        raise ControlPlaneError("CANDIDATE_MATERIAL_UNSAFE", "secrets or environments are tracked", paths=excluded)
    changed = git.changed_files(expected_current_sha, candidate_sha)
    return {
        "candidate_sha": candidate_sha,
        "tree_sha": git.tree(candidate_sha),
        "expected_current_sha": expected_current_sha,
        "remote": remote,
        "fetched_at": fetched_at,
        "containing_remote_refs": containing,
        "remote_main": main,
        "remote_main_is_expected_current": main == expected_current_sha,
        "candidate_fast_forwards_remote_main": main is not None and git.is_ancestor(main, candidate_sha),
        "commits_since_current": git.commit_count(expected_current_sha, candidate_sha),
        "tracked_file_count": len(files),
        "changed_since_current": {
            "files": len(changed),
            "migrations": sorted(name for name in changed if name.startswith("backend/alembic/versions/")),
            "requirements_changed": any(name in ("backend/requirements.txt",) for name in changed),
            "frontend_changed": any(name.startswith("frontend/") for name in changed),
            "ops_runtime_changed": sorted(name for name in changed if name.startswith(("ops/release/", "ops/windows-task/"))),
        },
        "frontend_tree_sha": git.tree(candidate_sha, "frontend"),
        "current_frontend_tree_sha": git.tree(expected_current_sha, "frontend"),
        "requirements_blob_sha": git.tree(candidate_sha, "backend/requirements.txt"),
        "current_requirements_blob_sha": git.tree(expected_current_sha, "backend/requirements.txt"),
    }


def build_manifest(*, identity: dict[str, Any], archive: dict[str, Any], graph: dict[str, Any],
                   runtime_contract: dict[str, Any], release_id: str) -> dict[str, Any]:
    head = single_head(graph)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": "agrosat_release_manifest",
        # Kept under this name: the application launcher and the worker runners bind to it.
        "git_sha": identity["candidate_sha"],
        "tree_sha": identity["tree_sha"],
        "expected_current_sha": identity["expected_current_sha"],
        "release_id": release_id,
        "source": {key: identity[key] for key in (
            "remote", "fetched_at", "containing_remote_refs", "remote_main", "remote_main_is_expected_current",
            "candidate_fast_forwards_remote_main", "commits_since_current", "tracked_file_count",
            "changed_since_current")},
        "source_archive": archive,
        "alembic": {"head": head, "head_count": 1, "revisions": ordered_revisions(graph),
                    "resolved_with": "alembic.script.ScriptDirectory of the candidate"},
        "runtime_contract": runtime_contract,
        "created_at": iso(utc_now()),
        "control_plane_version": CONTROL_PLANE_VERSION,
    }


def validate_manifest(manifest: dict[str, Any], *, candidate_sha: str, expected_current_sha: str | None = None,
                      archive_sha256: str | None = None, alembic_head: str | None = None) -> None:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION \
            or manifest.get("kind") != "agrosat_release_manifest":
        raise ControlPlaneError("RELEASE_MANIFEST_SCHEMA_REJECTED")
    if manifest.get("git_sha") != candidate_sha:
        raise ControlPlaneError("RELEASE_MANIFEST_CANDIDATE_MISMATCH")
    if expected_current_sha is not None and manifest.get("expected_current_sha") != expected_current_sha:
        raise ControlPlaneError("RELEASE_MANIFEST_CURRENT_MISMATCH")
    if archive_sha256 is not None and manifest.get("source_archive", {}).get("sha256") != archive_sha256:
        raise ControlPlaneError("RELEASE_MANIFEST_ARCHIVE_MISMATCH")
    if alembic_head is not None and manifest.get("alembic", {}).get("head") != alembic_head:
        raise ControlPlaneError("RELEASE_MANIFEST_ALEMBIC_MISMATCH")


def read_release_identity(release_directory: Path) -> dict[str, Any]:
    """The identity of any release directory, current (schema 2) or earlier (schema 1)."""
    manifest_path = Path(release_directory) / "release-manifest.json"
    manifest = read_json(manifest_path, "RELEASE_MANIFEST_UNREADABLE")
    sha = manifest.get("git_sha")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha) or Path(release_directory).name != sha:
        raise ControlPlaneError("RELEASE_MANIFEST_IDENTITY_REJECTED", Path(release_directory).name)
    return {"git_sha": sha, "schema_version": manifest.get("schema_version"), "manifest_sha256": sha256_file(manifest_path),
            "alembic_head": (manifest.get("alembic") or {}).get("head") or manifest.get("alembic_head")}


def check_runtime(contract: dict[str, Any], *, python_version: str, node_version: str | None,
                  pg_dump_version: str | None, lockfile_version: int | None) -> dict[str, Any]:
    """Compare observed tool versions with the runtime contract."""
    checks = {
        "python_series": python_version.startswith(contract["python"]["series"] + "."),
        "node_major": node_version is None or node_version.lstrip("v").split(".")[0] == str(contract["node"]["major"]),
        "pg_client_major": pg_dump_version is None or f" {contract['postgresql']['client_major']}." in f" {pg_dump_version.split()[-1]}",
        "npm_lockfile_version": lockfile_version is None or lockfile_version == contract["npm"]["lockfile_version"],
    }
    return {"pass": all(checks.values()), "checks": checks,
            "observed": {"python": python_version, "node": node_version, "pg_dump": pg_dump_version,
                         "npm_lockfile_version": lockfile_version}}
