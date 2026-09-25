"""The explicit release/rollback authorization artifact (TASK_230 Part E).

One JSON file, written by a human outside every release source tree, binds a
single operation to exact identities and a short validity window:

    {
      "schema_version": 1,
      "kind": "agrosat_release_authorization",
      "operation": "release" | "rollback",
      "release_id": "R20260926-task231",
      "candidate_sha": "<40 hex: the release to run after the operation>",
      "expected_current_sha": "<40 hex: the release running before it>",
      "database_name": "agrosat",
      "migration": {"authorized": false, "from_revision": null, "to_revision": null},
      "database_rollback_strategy": "none" | "downgrade_reversible_only" | "restore_validated_backup",
      "restore_backup_sha256": null,
      "created_at": "2026-09-26T04:00:00+00:00",
      "expires_at": "2026-09-26T08:00:00+00:00",
      "authorized_by": "operator name or role (not a secret)"
    }

Validation fails closed on: any unknown or missing key, a malformed or wrong
identity, wrong operation, wrong database, a window that has not started, has
expired or is longer than MAX_VALIDITY, a file inside a source or release tree,
anything that looks like a credential, and replay of a release id that was
already used. A production run additionally needs the file's SHA-256 typed on
the command line, so a stale or unexpected file cannot be picked up by accident.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from .common import (
    ControlPlaneError, find_secrets, is_reparse_point, is_within, iso, parse_iso, read_json, require_release_id,
    require_sha, sha256_file, utc_now,
)

MAX_VALIDITY = timedelta(hours=24)
CLOCK_SKEW = timedelta(minutes=5)
KEYS = frozenset({
    "schema_version", "kind", "operation", "release_id", "candidate_sha", "expected_current_sha",
    "database_name", "migration", "database_rollback_strategy", "restore_backup_sha256",
    "created_at", "expires_at", "authorized_by",
})
MIGRATION_KEYS = frozenset({"authorized", "from_revision", "to_revision"})
OPERATIONS = ("release", "rollback")
DB_STRATEGIES = ("none", "downgrade_reversible_only", "restore_validated_backup")


def validate_authorization(path: Path, *, operation: str, release_id: str, candidate_sha: str,
                           expected_current_sha: str, database_name: str, forbidden_roots: list[Path],
                           used_release_ids: set[str], now=None, expected_sha256: str | None = None) -> dict[str, Any]:
    """Return the validated authorization with its SHA-256, or raise ControlPlaneError."""
    path = Path(path)
    if not path.is_absolute():
        raise ControlPlaneError("AUTHORIZATION_PATH_REJECTED", "absolute path required")
    if not path.is_file() or is_reparse_point(path):
        raise ControlPlaneError("AUTHORIZATION_MISSING")
    for root in forbidden_roots:
        if is_within(path, root):
            raise ControlPlaneError("AUTHORIZATION_INSIDE_SOURCE_TREE",
                                    "the authorization must live outside every release source tree")
    digest = sha256_file(path)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ControlPlaneError("AUTHORIZATION_SHA256_MISMATCH")
    document = read_json(path, "AUTHORIZATION_MALFORMED", max_bytes=16 * 1024)
    if not isinstance(document, dict) or set(document) != KEYS:
        raise ControlPlaneError("AUTHORIZATION_KEYSET_REJECTED")
    if document["schema_version"] != 1 or document["kind"] != "agrosat_release_authorization":
        raise ControlPlaneError("AUTHORIZATION_SCHEMA_REJECTED")
    if find_secrets(document):
        raise ControlPlaneError("AUTHORIZATION_CONTAINS_SECRET")
    if document["operation"] not in OPERATIONS or document["operation"] != operation:
        raise ControlPlaneError("AUTHORIZATION_OPERATION_MISMATCH")
    require_release_id(document["release_id"])
    if document["release_id"] != release_id:
        raise ControlPlaneError("AUTHORIZATION_RELEASE_ID_MISMATCH")
    require_sha(document["candidate_sha"], "AUTHORIZATION_IDENTITY_MALFORMED")
    require_sha(document["expected_current_sha"], "AUTHORIZATION_IDENTITY_MALFORMED")
    if document["candidate_sha"] != candidate_sha:
        raise ControlPlaneError("AUTHORIZATION_CANDIDATE_MISMATCH")
    if document["expected_current_sha"] != expected_current_sha:
        raise ControlPlaneError("AUTHORIZATION_CURRENT_SHA_MISMATCH")
    if document["candidate_sha"] == document["expected_current_sha"]:
        raise ControlPlaneError("AUTHORIZATION_IDENTITY_MALFORMED", "candidate equals current")
    if document["database_name"] != database_name:
        raise ControlPlaneError("AUTHORIZATION_DATABASE_MISMATCH")
    migration = document["migration"]
    if not isinstance(migration, dict) or set(migration) != MIGRATION_KEYS or not isinstance(migration["authorized"], bool):
        raise ControlPlaneError("AUTHORIZATION_MIGRATION_MALFORMED")
    if migration["authorized"]:
        for key in ("from_revision", "to_revision"):
            if not isinstance(migration[key], str) or not migration[key] or len(migration[key]) > 128:
                raise ControlPlaneError("AUTHORIZATION_MIGRATION_MALFORMED")
    elif migration["from_revision"] is not None or migration["to_revision"] is not None:
        raise ControlPlaneError("AUTHORIZATION_MIGRATION_MALFORMED", "revisions only with authorized=true")
    strategy = document["database_rollback_strategy"]
    if strategy not in DB_STRATEGIES:
        raise ControlPlaneError("AUTHORIZATION_DB_STRATEGY_REJECTED")
    backup = document["restore_backup_sha256"]
    if strategy == "restore_validated_backup" and operation == "rollback":
        if not isinstance(backup, str) or len(backup) != 64 or backup != backup.lower():
            raise ControlPlaneError("AUTHORIZATION_BACKUP_IDENTITY_REQUIRED")
    elif backup is not None:
        raise ControlPlaneError("AUTHORIZATION_BACKUP_IDENTITY_UNEXPECTED")
    if not isinstance(document["authorized_by"], str) or not 2 <= len(document["authorized_by"]) <= 120:
        raise ControlPlaneError("AUTHORIZATION_AUTHOR_REJECTED")
    created = parse_iso(document["created_at"], "AUTHORIZATION_TIME_MALFORMED")
    expires = parse_iso(document["expires_at"], "AUTHORIZATION_TIME_MALFORMED")
    now = now or utc_now()
    if expires <= created or expires - created > MAX_VALIDITY:
        raise ControlPlaneError("AUTHORIZATION_WINDOW_REJECTED", "the window must be positive and at most 24h")
    if now < created - CLOCK_SKEW:
        raise ControlPlaneError("AUTHORIZATION_NOT_YET_VALID")
    if now >= expires:
        raise ControlPlaneError("AUTHORIZATION_EXPIRED")
    if release_id in used_release_ids:
        raise ControlPlaneError("AUTHORIZATION_REPLAY_REJECTED", "this release id was already executed")
    return {**document, "sha256": digest, "validated_at": iso(now)}


def identity_of(authorization: dict[str, Any], *, mode: str, profile_id: str) -> dict[str, Any]:
    """The immutable identity a release id is bound to (expiry and author excluded)."""
    return {"release_id": authorization["release_id"], "operation": authorization["operation"],
            "mode": mode, "profile_id": profile_id, "candidate_sha": authorization["candidate_sha"],
            "expected_current_sha": authorization["expected_current_sha"],
            "database_name": authorization["database_name"], "migration": authorization["migration"],
            "database_rollback_strategy": authorization["database_rollback_strategy"],
            "restore_backup_sha256": authorization["restore_backup_sha256"]}
