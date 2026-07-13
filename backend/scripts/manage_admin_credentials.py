"""Audited, interactive recovery of one existing AgroSat administrator.

Run this script from the backend root.  It intentionally accepts secrets only
through two masked terminal prompts and never exposes them through HTTP.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


EXIT_INVALID_INPUT = 2
EXIT_DATABASE_IDENTITY = 10
EXIT_TARGET_IDENTITY = 11
EXIT_API_PREFLIGHT = 12
EXIT_DATABASE_UPDATE = 13
EXIT_VERIFICATION_ROLLED_BACK = 14
EXIT_CRITICAL_ROLLBACK = 15
EXIT_AUDIT_WRITE = 16

_KNOWN_SEED_PASSWORD_SHA256 = (
    "a4c3a85ec11d90a8a03360cd0f64b8626c04b222298365e369ff1539fbc1eb4f"
)
_OBVIOUS_PLACEHOLDERS = frozenset(
    {
        "admin",
        "administrator",
        "changeme",
        "change_me",
        "change_me_in_production",
        "default",
        "password",
        "replace_me",
        "secret",
        "test_secret",
    }
)
_AUDIT_FIELDS = (
    "timestamp_utc",
    "hostname",
    "operator_os_user",
    "database_name",
    "target_user_id",
    "target_email",
    "target_role",
    "reason",
    "was_active",
    "is_active",
    "activation_changed",
    "password_hash_changed",
    "database_hash_verified",
    "api_health_verified",
    "api_login_verified",
    "api_me_verified",
    "rollback_attempted",
    "rollback_succeeded",
    "result",
    "tool_commit",
)


class RecoveryError(Exception):
    """Expected fail-closed outcome with a documented process exit code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Runtime:
    SessionLocal: Callable[[], Any]
    User: Any
    pwd_context: Any
    text: Callable[[str], Any]
    func: Any


def _load_runtime() -> Runtime:
    """Load the active backend configuration and register all ORM models."""
    from sqlalchemy import func, text

    from api.auth import pwd_context
    from database import SessionLocal
    from models.monitoring import User

    return Runtime(SessionLocal, User, pwd_context, text, func)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or recover one existing AgroSat administrator."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Read-only identity check")
    inspect_parser.add_argument("--email", required=True)
    inspect_parser.add_argument("--expected-database", required=True)
    inspect_parser.add_argument("--json", action="store_true")

    reset_parser = subparsers.add_parser(
        "reset-password", help="Interactive audited credential recovery"
    )
    reset_parser.add_argument("--email", required=True)
    reset_parser.add_argument("--expected-user-id", required=True, type=int)
    reset_parser.add_argument("--expected-database", required=True)
    reset_parser.add_argument("--reason", required=True)
    reset_parser.add_argument("--audit-dir", required=True)
    reset_parser.add_argument("--prompt", required=True, action="store_true")
    reset_parser.add_argument("--verify-api-base", required=True)
    reset_parser.add_argument("--activate", action="store_true")
    return parser


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or "@" not in normalized:
        raise RecoveryError(EXIT_INVALID_INPUT, "A valid target email is required.")
    return normalized


def validate_password(password: str) -> str:
    if not password or not password.strip():
        raise RecoveryError(EXIT_INVALID_INPUT, "The new credential is missing.")
    if password != password.strip():
        raise RecoveryError(
            EXIT_INVALID_INPUT,
            "The new credential has leading or trailing whitespace.",
        )

    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
    if hmac.compare_digest(digest, _KNOWN_SEED_PASSWORD_SHA256):
        raise RecoveryError(EXIT_INVALID_INPUT, "A known insecure credential was rejected.")

    lowered = password.casefold()
    compact = "".join(character for character in lowered if character.isalnum())
    if lowered in _OBVIOUS_PLACEHOLDERS or compact in _OBVIOUS_PLACEHOLDERS:
        raise RecoveryError(EXIT_INVALID_INPUT, "An obvious placeholder was rejected.")
    if any(marker in compact for marker in ("changeme", "password", "agrosatdefault")):
        raise RecoveryError(EXIT_INVALID_INPUT, "An obvious placeholder was rejected.")
    if len(password) < 16:
        raise RecoveryError(
            EXIT_INVALID_INPUT, "The new credential must contain at least 16 characters."
        )
    if not any(character.isupper() for character in password):
        raise RecoveryError(EXIT_INVALID_INPUT, "The new credential needs uppercase text.")
    if not any(character.islower() for character in password):
        raise RecoveryError(EXIT_INVALID_INPUT, "The new credential needs lowercase text.")
    if not any(character.isdigit() for character in password):
        raise RecoveryError(EXIT_INVALID_INPUT, "The new credential needs a digit.")
    if not any(not character.isalnum() for character in password):
        raise RecoveryError(
            EXIT_INVALID_INPUT, "The new credential needs a non-alphanumeric character."
        )
    return password


def collect_password() -> str:
    first = getpass.getpass("New administrator credential: ")
    second = getpass.getpass("Confirm new administrator credential: ")
    if not hmac.compare_digest(first, second):
        raise RecoveryError(EXIT_INVALID_INPUT, "Credential confirmation did not match.")
    return validate_password(first)


def _role_value(role: Any) -> str:
    value = getattr(role, "value", role)
    return str(value)


def _database_name(session: Any, runtime: Runtime) -> str:
    try:
        result = session.execute(runtime.text("SELECT current_database()"))
        if hasattr(result, "scalar_one"):
            name = result.scalar_one()
        else:
            name = result.scalar()
    except Exception as exc:
        raise RecoveryError(
            EXIT_DATABASE_IDENTITY, "Database identity could not be verified."
        ) from exc
    if not isinstance(name, str) or not name:
        raise RecoveryError(
            EXIT_DATABASE_IDENTITY, "Database identity could not be verified."
        )
    return name


def _require_database(session: Any, runtime: Runtime, expected: str) -> str:
    expected_name = expected.strip()
    if not expected_name:
        raise RecoveryError(EXIT_INVALID_INPUT, "Expected database is required.")
    actual = _database_name(session, runtime)
    if not hmac.compare_digest(actual, expected_name):
        raise RecoveryError(EXIT_DATABASE_IDENTITY, "Database identity mismatch.")
    return actual


def _find_matches(
    session: Any, runtime: Runtime, normalized_email: str, *, lock: bool
) -> list[Any]:
    query = session.query(runtime.User).filter(
        runtime.func.lower(runtime.func.btrim(runtime.User.email)) == normalized_email
    )
    if lock:
        query = query.with_for_update()
    return list(query.limit(2).all())


def _require_target(matches: list[Any], expected_user_id: Optional[int] = None) -> Any:
    if len(matches) != 1:
        raise RecoveryError(
            EXIT_TARGET_IDENTITY, "Exactly one matching administrator is required."
        )
    user = matches[0]
    if expected_user_id is not None and user.id != expected_user_id:
        raise RecoveryError(EXIT_TARGET_IDENTITY, "Target user identity mismatch.")
    if _role_value(user.role) != "admin":
        raise RecoveryError(EXIT_TARGET_IDENTITY, "Target role is not admin.")
    return user


def _timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _inspect_payload(
    database_name: str, matches: list[Any], runtime: Runtime
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "database_name": database_name,
        "target_exists": bool(matches),
        "match_count": len(matches),
        "exactly_one_match": len(matches) == 1,
        "user_id": None,
        "email": None,
        "role": None,
        "is_active": None,
        "enterprise_id": None,
        "created_at": None,
        "last_login": None,
        "password_hash_scheme_supported": False,
    }
    if len(matches) == 1:
        user = matches[0]
        payload.update(
            {
                "user_id": user.id,
                "email": str(user.email).strip().lower(),
                "role": _role_value(user.role),
                "is_active": bool(user.is_active),
                "enterprise_id": user.enterprise_id,
                "created_at": _timestamp(user.created_at),
                "last_login": _timestamp(user.last_login),
                "password_hash_scheme_supported": (
                    runtime.pwd_context.identify(user.hashed_password) is not None
                ),
            }
        )
    return payload


def inspect_account(args: argparse.Namespace, runtime: Optional[Runtime] = None) -> dict[str, Any]:
    runtime = runtime or _load_runtime()
    email = normalize_email(args.email)
    session = runtime.SessionLocal()
    try:
        database_name = _require_database(session, runtime, args.expected_database)
        matches = _find_matches(session, runtime, email, lock=False)
        payload = _inspect_payload(database_name, matches, runtime)
        session.rollback()
        return payload
    except RecoveryError:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        raise RecoveryError(EXIT_DATABASE_UPDATE, "Read-only inspection failed.") from exc
    finally:
        session.close()


def _tool_commit() -> Optional[str]:
    repository = Path(__file__).resolve().parents[2]
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        value = completed.stdout.strip()
        return value if len(value) == 40 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _new_audit(args: argparse.Namespace, email: str) -> dict[str, Any]:
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "hostname": socket.gethostname(),
        "operator_os_user": getpass.getuser(),
        "database_name": None,
        "target_user_id": args.expected_user_id,
        "target_email": email,
        "target_role": None,
        "reason": args.reason,
        "was_active": None,
        "is_active": None,
        "activation_changed": False,
        "password_hash_changed": False,
        "database_hash_verified": False,
        "api_health_verified": False,
        "api_login_verified": False,
        "api_me_verified": False,
        "rollback_attempted": False,
        "rollback_succeeded": False,
        "result": "BLOCKED",
        "tool_commit": _tool_commit(),
    }


def _prepare_audit_dir(path_value: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".task175_probe_", dir=path, delete=False) as probe:
            probe_path = Path(probe.name)
        probe_path.unlink()
    except OSError as exc:
        raise RecoveryError(EXIT_AUDIT_WRITE, "Audit directory is not writable.") from exc
    return path


def _write_audit(audit_dir: Path, audit: dict[str, Any]) -> tuple[Path, Path]:
    sanitized = {field: audit.get(field) for field in _AUDIT_FIELDS}
    timestamp = str(sanitized["timestamp_utc"]).replace(":", "").replace("-", "")
    timestamp = timestamp.replace(".", "").replace("+", "").replace("Z", "Z")
    json_path = audit_dir / f"TASK175_ADMIN_RECOVERY_{timestamp}.json"
    text_path = audit_dir / f"TASK175_ADMIN_RECOVERY_{timestamp}.txt"
    temp_json = audit_dir / f".{json_path.name}.tmp"
    temp_text = audit_dir / f".{text_path.name}.tmp"
    try:
        temp_json.write_text(
            json.dumps(sanitized, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        lines = [f"{field}={sanitized[field]}" for field in _AUDIT_FIELDS]
        temp_text.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temp_json, json_path)
        os.replace(temp_text, text_path)
    except OSError as exc:
        for temporary in (temp_json, temp_text):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise RecoveryError(EXIT_AUDIT_WRITE, "Sanitized audit evidence could not be written.") from exc
    return json_path, text_path


def _http_json(
    method: str,
    url: str,
    *,
    data: Optional[dict[str, str]] = None,
    bearer: Optional[str] = None,
    timeout: float = 8.0,
) -> tuple[int, dict[str, Any]]:
    body = None
    headers = {"Accept": "application/json"}
    if data is not None:
        body = urlparse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if bearer is not None:
        headers["Authorization"] = "Bearer " + bearer
    request = urlrequest.Request(url, data=body, headers=headers, method=method)
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read()
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("API request failed.") from exc
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("API response was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("API response shape was invalid.")
    return status, payload


def _api_base(value: str) -> str:
    base = value.strip().rstrip("/")
    parsed = urlparse.urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RecoveryError(EXIT_INVALID_INPUT, "A valid API base is required.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RecoveryError(EXIT_INVALID_INPUT, "API base must not contain credentials or extras.")
    return base


def _health_preflight(base: str, audit: dict[str, Any]) -> None:
    try:
        status, _ = _http_json("GET", base + "/health")
    except RuntimeError as exc:
        raise RecoveryError(EXIT_API_PREFLIGHT, "API health preflight failed.") from exc
    if status != 200:
        raise RecoveryError(EXIT_API_PREFLIGHT, "API health preflight failed.")
    audit["api_health_verified"] = True


def _verify_api(
    base: str,
    email: str,
    password: str,
    expected_user_id: int,
    expected_active: bool,
    audit: dict[str, Any],
) -> None:
    try:
        status, login = _http_json(
            "POST",
            base + "/api/auth/login",
            data={"username": email, "password": password},
        )
        token = login.get("access_token")
        if status != 200 or not isinstance(token, str) or not token:
            raise RuntimeError("Login verification failed.")
        audit["api_login_verified"] = True
        status, current = _http_json("GET", base + "/api/auth/me", bearer=token)
        token = None
        if status != 200:
            raise RuntimeError("Identity verification failed.")
        identity_ok = (
            current.get("id") == expected_user_id
            and str(current.get("email", "")).strip().lower() == email
            and _role_value(current.get("role")) == "admin"
            and current.get("is_active") is expected_active
        )
        if not identity_ok:
            raise RuntimeError("Identity verification failed.")
        audit["api_me_verified"] = True
    except RuntimeError as exc:
        raise RecoveryError(
            EXIT_VERIFICATION_ROLLED_BACK, "Post-commit API verification failed."
        ) from exc


def _restore_previous(
    runtime: Runtime,
    email: str,
    expected_user_id: int,
    written_hash: str,
    written_active: bool,
    previous_hash: str,
    previous_active: bool,
    audit: dict[str, Any],
) -> None:
    audit["rollback_attempted"] = True
    session = runtime.SessionLocal()
    try:
        matches = _find_matches(session, runtime, email, lock=True)
        user = _require_target(matches, expected_user_id)
        current_state_is_ours = hmac.compare_digest(user.hashed_password, written_hash)
        current_state_is_ours = (
            current_state_is_ours and bool(user.is_active) is written_active
        )
        if not current_state_is_ours:
            session.rollback()
            raise RecoveryError(
                EXIT_CRITICAL_ROLLBACK,
                "Concurrent credential change prevents safe rollback; manual intervention is required.",
            )
        user.hashed_password = previous_hash
        user.is_active = previous_active
        session.commit()
    except RecoveryError:
        raise
    except Exception as exc:
        session.rollback()
        raise RecoveryError(
            EXIT_CRITICAL_ROLLBACK,
            "Rollback failed; manual intervention is required.",
        ) from exc
    finally:
        session.close()

    verify_session = runtime.SessionLocal()
    try:
        matches = _find_matches(verify_session, runtime, email, lock=False)
        user = _require_target(matches, expected_user_id)
        restored = hmac.compare_digest(user.hashed_password, previous_hash)
        restored = restored and bool(user.is_active) is previous_active
        verify_session.rollback()
        if not restored:
            raise RecoveryError(
                EXIT_CRITICAL_ROLLBACK,
                "Rollback verification failed; manual intervention is required.",
            )
        audit["rollback_succeeded"] = True
        audit["is_active"] = previous_active
    finally:
        verify_session.close()


def reset_password(
    args: argparse.Namespace,
    runtime: Optional[Runtime] = None,
) -> tuple[dict[str, Any], tuple[Path, Path]]:
    runtime = runtime or _load_runtime()
    email = normalize_email(args.email)
    if args.expected_user_id <= 0:
        raise RecoveryError(EXIT_INVALID_INPUT, "Expected user ID must be positive.")
    if not args.reason.strip():
        raise RecoveryError(EXIT_INVALID_INPUT, "A recovery reason is required.")
    audit_dir = _prepare_audit_dir(args.audit_dir)
    audit = _new_audit(args, email)
    exit_error: Optional[RecoveryError] = None

    try:
        password = collect_password()
        base = _api_base(args.verify_api_base)
        _health_preflight(base, audit)

        previous_hash: Optional[str] = None
        previous_active: Optional[bool] = None
        written_hash: Optional[str] = None
        session = runtime.SessionLocal()
        try:
            database_name = _require_database(session, runtime, args.expected_database)
            audit["database_name"] = database_name
            matches = _find_matches(session, runtime, email, lock=True)
            user = _require_target(matches, args.expected_user_id)
            audit["target_role"] = _role_value(user.role)
            previous_hash = user.hashed_password
            previous_active = bool(user.is_active)
            audit["was_active"] = previous_active
            already_matches = runtime.pwd_context.verify(password, previous_hash)
            if already_matches:
                written_hash = previous_hash
            else:
                written_hash = runtime.pwd_context.hash(password)
                user.hashed_password = written_hash
                audit["password_hash_changed"] = True
            desired_active = True if args.activate else previous_active
            if desired_active is not previous_active:
                user.is_active = desired_active
                audit["activation_changed"] = True
            audit["is_active"] = desired_active
            session.commit()
        except RecoveryError:
            session.rollback()
            raise
        except Exception as exc:
            session.rollback()
            raise RecoveryError(EXIT_DATABASE_UPDATE, "Credential update failed.") from exc
        finally:
            session.close()

        verify_session = runtime.SessionLocal()
        try:
            matches = _find_matches(verify_session, runtime, email, lock=False)
            user = _require_target(matches, args.expected_user_id)
            hash_ok = hmac.compare_digest(user.hashed_password, written_hash)
            hash_ok = hash_ok and runtime.pwd_context.verify(password, user.hashed_password)
            verify_session.rollback()
            if not hash_ok:
                raise RecoveryError(
                    EXIT_VERIFICATION_ROLLED_BACK,
                    "Post-commit database verification failed.",
                )
            audit["database_hash_verified"] = True
            _verify_api(
                base,
                email,
                password,
                args.expected_user_id,
                bool(audit["is_active"]),
                audit,
            )
        except Exception as verification_exception:
            verify_session.rollback()
            if isinstance(verification_exception, RecoveryError):
                verification_error = verification_exception
            else:
                verification_error = RecoveryError(
                    EXIT_VERIFICATION_ROLLED_BACK,
                    "Post-commit database verification failed.",
                )
            try:
                _restore_previous(
                    runtime,
                    email,
                    args.expected_user_id,
                    written_hash,
                    bool(audit["is_active"]),
                    previous_hash,
                    previous_active,
                    audit,
                )
            except RecoveryError as rollback_error:
                raise rollback_error from verification_error
            raise RecoveryError(
                EXIT_VERIFICATION_ROLLED_BACK,
                "Post-commit verification failed and the previous credential was restored.",
            ) from verification_error
        finally:
            verify_session.close()

        audit["result"] = "PASS"
    except RecoveryError as exc:
        exit_error = exc
        audit["result"] = "BLOCKED"
    finally:
        password = None
        previous_hash = None
        written_hash = None

    try:
        evidence = _write_audit(audit_dir, audit)
    except RecoveryError:
        raise
    if exit_error is not None:
        raise exit_error
    return audit, evidence


def _emit_inspect(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True, ensure_ascii=False))
        return
    for key, value in payload.items():
        print(f"{key}={value}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "inspect":
            payload = inspect_account(args)
            _emit_inspect(payload, args.json)
        else:
            audit, evidence = reset_password(args)
            print(f"result={audit['result']}")
            print(f"audit_json={evidence[0]}")
            print(f"audit_summary={evidence[1]}")
        return 0
    except RecoveryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
