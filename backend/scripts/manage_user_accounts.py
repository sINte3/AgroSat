"""Audited, operator-invoked management of AgroSat non-admin user accounts.

Run it with the backend of the release whose database is the target, with the
runtime configuration selected exactly as the application selects it
(``AGROSAT_RUNTIME_ENV_FILE``, else ``backend/.env``)::

    python -m scripts.manage_user_accounts inspect --login ... --expected-database agrosat
    python -m scripts.manage_user_accounts create --login ... --full-name ... --role agronomist \\
        --enterprise-id 9 --expected-database agrosat --credential-file D:\\private\\x.env --reason ...
    ... create ... --apply --audit-dir D:\\evidence\\audit

Roles: manager, agronomist and viewer, each bound to one active enterprise.
Administrator accounts are refused in both directions; they stay under
``manage_admin_credentials.py``.

``create`` and ``reset-password`` are dry runs unless ``--apply`` is given: the
dry run validates everything inside a READ ONLY transaction and writes nothing.
With ``--apply`` the change is one transaction, serialised against other runs
of this tool, re-validated under that lock and verified after commit; a failed
verification removes the new account or restores the previous credential.

The credential is generated here with ``secrets``. It is never accepted on the
command line, printed or audited: it is written once, before the database
change, to a new file in an operator-supplied private directory, and that file
is removed again whenever the change does not complete.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import getpass
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
from typing import Any, Callable, Optional
import unicodedata
from urllib.parse import urlsplit


BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# The admin recovery CLI owns the credential policy and the login/HTTP helpers;
# sharing them keeps both tools verifying credentials identically.
from scripts import manage_admin_credentials as admin_tool  # noqa: E402


TOOL = "manage_user_accounts"

EXIT_INVALID_INPUT = 2
EXIT_DATABASE_IDENTITY = 10
EXIT_TARGET_IDENTITY = 11
EXIT_API_PREFLIGHT = 12
EXIT_DATABASE_UPDATE = 13
EXIT_VERIFICATION_ROLLED_BACK = 14
EXIT_CRITICAL_ROLLBACK = 15
EXIT_AUDIT_WRITE = 16
EXIT_ROLE_REJECTED = 17
EXIT_ENTERPRISE = 18
EXIT_CONFLICT = 19
EXIT_CREDENTIAL_OUTPUT = 20
EXIT_SCHEMA_MISMATCH = 21
EXIT_UNEXPECTED = 70

# Blocking reasons are reported together; the exit code is the most basic one.
_EXIT_PRIORITY = (
    EXIT_INVALID_INPUT,
    EXIT_DATABASE_IDENTITY,
    EXIT_SCHEMA_MISMATCH,
    EXIT_ROLE_REJECTED,
    EXIT_TARGET_IDENTITY,
    EXIT_ENTERPRISE,
    EXIT_CONFLICT,
    EXIT_CREDENTIAL_OUTPUT,
)

ADMIN_ROLE = "admin"
# Every tenant role of api/dependencies.py: each is scoped by users.enterprise_id.
MANAGED_ROLES = ("manager", "agronomist", "viewer")

# pg_advisory_xact_lock key: runs of this tool that change accounts serialise.
ACCOUNT_LOCK_KEY = 238_101_001

CREDENTIAL_LENGTH = 20
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_LOWER = "abcdefghijkmnopqrstuvwxyz"
_DIGITS = "23456789"
# dotenv-safe (no quote, '#', '$', '=', backslash or space) and easy to type.
_SYMBOLS = "!*-.?@_"

_LOGIN = re.compile(
    r"^(?=.{3,254}$)[a-z0-9](?:[a-z0-9._+-]{0,62}[a-z0-9])?"
    r"@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_CREDENTIAL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)
# LocalSystem, BUILTIN\Administrators and OWNER RIGHTS (the object's owner only;
# Python's own private-directory ACL on Windows). The operator's SID is added at
# runtime; any other principal makes the location unusable.
_TRUSTED_SIDS = frozenset({"S-1-5-18", "S-1-5-32-544", "S-1-3-4"})
_CREATOR_OWNER_SID = "S-1-3-0"
_ACL_TARGET_VARIABLE = "AGROSAT_CREDENTIAL_ACL_TARGET"
_ACL_QUERY = r"""
$ErrorActionPreference = 'Stop'
$acl = Get-Acl -LiteralPath $env:AGROSAT_CREDENTIAL_ACL_TARGET
$inheritOnly = [System.Security.AccessControl.PropagationFlags]::InheritOnly
$allow = [System.Security.AccessControl.AccessControlType]::Allow
$rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) | ForEach-Object {
    [pscustomobject]@{
        sid = $_.IdentityReference.Value
        allow = ($_.AccessControlType -eq $allow)
        inherit_only = (($_.PropagationFlags -band $inheritOnly) -eq $inheritOnly)
    }
})
[pscustomobject]@{
    protected = [bool]$acl.AreAccessRulesProtected
    user = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    rules = $rules
} | ConvertTo-Json -Compress -Depth 4
"""

AUDIT_FIELDS = (
    "timestamp_utc",
    "tool",
    "tool_commit",
    "hostname",
    "operator_os_user",
    "operation",
    "reason",
    "database_name",
    "database_server",
    "environment",
    "alembic_revision",
    "expected_alembic_revision",
    "target_user_id",
    "target_login",
    "target_full_name",
    "target_role",
    "target_enterprise_id",
    "target_is_active",
    "distinct_from_user_ids",
    "outcome",
    "result",
    "error_code",
    "error",
    "user_row_created",
    "password_hash_changed",
    "credential_file",
    "credential_file_written",
    "credential_file_removed",
    "database_verified",
    "api_health_verified",
    "api_login_verified",
    "api_me_verified",
    "rollback_attempted",
    "rollback_succeeded",
)
# Unchanged by a password reset; last_login may move with any sign-in.
PRESERVED_ON_RESET = (
    "email",
    "full_name",
    "phone",
    "role",
    "enterprise_id",
    "is_active",
    "created_at",
)


class AccountError(Exception):
    """Expected fail-closed outcome with a documented process exit code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# ─── identities ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Account:
    id: int
    login: str
    full_name: Optional[str]
    role: str
    enterprise_id: Optional[int]
    is_active: bool
    created_at: Any = None
    last_login: Any = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "login": self.login,
            "full_name": self.full_name,
            "role": self.role,
            "enterprise_id": self.enterprise_id,
            "is_active": self.is_active,
            "created_at": _timestamp(self.created_at),
            "last_login": _timestamp(self.last_login),
        }


@dataclass(frozen=True)
class Enterprise:
    id: int
    name: str
    is_active: bool


@dataclass(frozen=True)
class DatabaseTarget:
    database_name: str
    server_address: Optional[str]
    server_port: Optional[int]
    alembic_revision: Optional[str]


@dataclass(frozen=True)
class Plan:
    outcome: str
    code: int = 0
    reasons: tuple[str, ...] = ()
    existing: Optional[Account] = None
    person_matches: tuple[Account, ...] = ()


def normalize_role(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def normalize_login(value: Optional[str]) -> str:
    """The login endpoint strips and lowercases the username, then compares exactly."""
    normalized = (value or "").strip().lower()
    if not normalized or "@" not in normalized:
        raise AccountError(EXIT_INVALID_INPUT, "A login in e-mail form is required.")
    return normalized


def login_is_well_formed(login: str) -> bool:
    local = login.split("@", 1)[0]
    return bool(_LOGIN.fullmatch(login)) and ".." not in local


def validate_new_login(value: Optional[str]) -> str:
    login = normalize_login(value)
    if not login_is_well_formed(login):
        raise AccountError(
            EXIT_INVALID_INPUT,
            "A new login must be a lowercase ASCII e-mail-form identifier "
            "(letters, digits and . _ + - in the local part).",
        )
    return login


def clean_full_name(value: Optional[str]) -> str:
    text = " ".join(unicodedata.normalize("NFC", value or "").split())
    if not text:
        raise AccountError(EXIT_INVALID_INPUT, "A full name is required.")
    if len(text) > 255:
        raise AccountError(EXIT_INVALID_INPUT, "The full name is longer than 255 characters.")
    if any(unicodedata.category(character).startswith("C") for character in text):
        raise AccountError(EXIT_INVALID_INPUT, "The full name contains control characters.")
    return text


def name_tokens(value: Optional[str]) -> tuple[str, ...]:
    text = unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")
    return tuple(sorted(token for token in re.split(r"[\W_]+", text) if token))


def same_person(requested: tuple[str, ...], existing: tuple[str, ...]) -> bool:
    """A strong full-name match: equal names, or one name inside the other.

    At least two name parts must coincide, so a shared first name alone (or a
    one-word name) never identifies a person.
    """
    if not requested or not existing:
        return False
    if requested == existing:
        return True
    overlap = sum((Counter(requested) & Counter(existing)).values())
    return overlap >= 2 and overlap == min(len(requested), len(existing))


def _timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _choose_code(codes: list[int]) -> int:
    for code in _EXIT_PRIORITY:
        if code in codes:
            return code
    return codes[0] if codes else 0


# ─── credential generation and hand-off file ─────────────────────────────────


def generate_password() -> str:
    """A 20-character credential from the ``secrets`` CSPRNG (about 120 bits)."""
    alphabet = _UPPER + _LOWER + _DIGITS + _SYMBOLS
    shuffler = secrets.SystemRandom()
    for _ in range(32):
        characters = [
            secrets.choice(_UPPER),
            secrets.choice(_LOWER),
            secrets.choice(_DIGITS),
            secrets.choice(_SYMBOLS),
        ]
        characters += [
            secrets.choice(alphabet) for _ in range(CREDENTIAL_LENGTH - len(characters))
        ]
        shuffler.shuffle(characters)
        candidate = "".join(characters)
        try:
            return admin_tool.validate_password(candidate)
        except admin_tool.RecoveryError:
            continue
    raise AccountError(EXIT_UNEXPECTED, "A policy-compliant credential could not be generated.")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_windows_acl(path: Path) -> dict[str, Any]:
    """The DACL of ``path`` by SID, read with Get-Acl (locale independent)."""
    environment = dict(os.environ)
    environment[_ACL_TARGET_VARIABLE] = str(path)
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _ACL_QUERY],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=environment,
            stdin=subprocess.DEVNULL,
        )
        payload = json.loads(completed.stdout.strip()) if completed.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        payload = None
    if not isinstance(payload, dict):
        raise AccountError(
            EXIT_CREDENTIAL_OUTPUT, "The access control list of the credential location could not be read."
        )
    rules = payload.get("rules") or []
    if isinstance(rules, dict):
        rules = [rules]
    return {
        "protected": bool(payload.get("protected")),
        "user": str(payload.get("user") or ""),
        "rules": [rule for rule in rules if isinstance(rule, dict)],
    }


def _acl_problem(acl: dict[str, Any], *, directory: bool) -> Optional[str]:
    allowed = set(_TRUSTED_SIDS)
    if acl.get("user"):
        allowed.add(acl["user"])
    if directory and not acl.get("protected"):
        return "it inherits permissions from its parent (protect it with icacls /inheritance:r)"
    for rule in acl.get("rules", ()):
        if not rule.get("allow"):
            continue
        sid = str(rule.get("sid") or "")
        if rule.get("inherit_only") and sid == _CREATOR_OWNER_SID:
            continue
        if sid not in allowed:
            return f"it grants access to {sid or 'an unknown principal'}"
    return None


def _privacy_problem(path: Path, *, directory: bool) -> Optional[str]:
    if os.name == "nt":
        return _acl_problem(_read_windows_acl(path), directory=directory)
    info = os.lstat(path)
    if info.st_uid != os.geteuid():
        return "it is not owned by the operator account"
    if info.st_mode & 0o077:
        return "group or other users have permissions on it"
    return None


def check_credential_path(value: Optional[str], *, audit_dir: Optional[Path] = None) -> Path:
    """Validate an operator-supplied credential file path without creating it."""
    if not value or not str(value).strip():
        raise AccountError(EXIT_CREDENTIAL_OUTPUT, "A credential file path is required.")
    raw = Path(str(value))
    if not raw.is_absolute():
        raise AccountError(EXIT_CREDENTIAL_OUTPUT, "The credential file path must be absolute.")
    if ".." in raw.parts:
        raise AccountError(EXIT_CREDENTIAL_OUTPUT, "The credential file path must not contain '..'.")
    name = raw.name
    if (
        not _CREDENTIAL_NAME.fullmatch(name)
        or name.endswith(".")
        or name.split(".", 1)[0].upper() in _RESERVED_NAMES
    ):
        raise AccountError(
            EXIT_CREDENTIAL_OUTPUT,
            "The credential file name must use only letters, digits, '.', '_' and '-'.",
        )
    try:
        directory = raw.parent.resolve(strict=True)
    except (OSError, RuntimeError):
        raise AccountError(EXIT_CREDENTIAL_OUTPUT, "The credential directory must already exist.") from None
    if not directory.is_dir():
        raise AccountError(EXIT_CREDENTIAL_OUTPUT, "The credential directory is not a directory.")
    if os.path.normcase(os.path.normpath(str(raw.parent))) != os.path.normcase(str(directory)):
        raise AccountError(
            EXIT_CREDENTIAL_OUTPUT,
            "The credential directory must be given by its real path (no link, junction or alias).",
        )
    target = directory / name
    if os.path.lexists(target):
        raise AccountError(
            EXIT_CREDENTIAL_OUTPUT, "The credential file already exists; it is never overwritten."
        )
    for ancestor in (directory, *directory.parents):
        if os.path.lexists(ancestor / ".git"):
            raise AccountError(
                EXIT_CREDENTIAL_OUTPUT, "The credential file must not be inside a Git repository or worktree."
            )
        if os.path.lexists(ancestor / "release-manifest.json"):
            raise AccountError(
                EXIT_CREDENTIAL_OUTPUT, "The credential file must not be inside an AgroSat release tree."
            )
    if _is_within(directory, REPOSITORY_ROOT.resolve()):
        raise AccountError(
            EXIT_CREDENTIAL_OUTPUT, "The credential file must not be inside the tool's source tree."
        )
    if audit_dir is not None and _is_within(directory, audit_dir):
        raise AccountError(
            EXIT_CREDENTIAL_OUTPUT,
            "The credential file must not be inside the audit directory (audit evidence is not secret).",
        )
    problem = _privacy_problem(directory, directory=True)
    if problem:
        raise AccountError(EXIT_CREDENTIAL_OUTPUT, f"The credential directory is not private: {problem}.")
    return target


@dataclass
class CredentialFile:
    path: Path
    written: bool = False

    def remove(self) -> bool:
        try:
            os.remove(self.path)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True


def _credential_body(login: str, password: str, *, operation: str, role: str,
                     enterprise_id: Optional[int], user_id: Optional[int]) -> str:
    identity = f"login={login} role={role} enterprise_id={enterprise_id} operation={operation}"
    if user_id is not None:
        identity += f" user_id={user_id}"
    written = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return (
        "# AgroSat account credential for in-person hand-off. Never e-mail, copy or commit it.\n"
        "# Delete this file once the account holder has signed in.\n"
        f"# {identity} written_utc={written}\n"
        f"AGROSAT_LOGIN_USERNAME={login}\n"
        f"AGROSAT_LOGIN_PASSWORD={password}\n"
    )


def write_credential_file(target: Path, body: str) -> CredentialFile:
    """Create ``target`` exclusively, prove it private while empty, then write."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        descriptor = os.open(target, flags, 0o600)
    except FileExistsError:
        raise AccountError(
            EXIT_CREDENTIAL_OUTPUT, "The credential file already exists; it is never overwritten."
        ) from None
    except OSError:
        raise AccountError(EXIT_CREDENTIAL_OUTPUT, "The credential file could not be created.") from None
    handle = CredentialFile(target)
    completed = False
    try:
        problem = _privacy_problem(target, directory=False)
        if problem:
            raise AccountError(EXIT_CREDENTIAL_OUTPUT, f"The new credential file is not private: {problem}.")
        data = memoryview(body.encode("utf-8"))
        while data:
            written = os.write(descriptor, data)
            data = data[written:]
        os.fsync(descriptor)
        completed = True
    except OSError:
        raise AccountError(EXIT_CREDENTIAL_OUTPUT, "The credential file could not be written.") from None
    finally:
        os.close(descriptor)
        if not completed:
            handle.remove()
    handle.written = True
    return handle


# ─── audit ───────────────────────────────────────────────────────────────────


def _tool_commit() -> Optional[str]:
    commit = admin_tool._tool_commit()
    if commit:
        return commit
    try:
        manifest = json.loads((REPOSITORY_ROOT / "release-manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    value = manifest.get("git_sha") if isinstance(manifest, dict) else None
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) else None


def prepare_audit_dir(value: Optional[str]) -> Path:
    if not value or not str(value).strip():
        raise AccountError(EXIT_AUDIT_WRITE, "--audit-dir is required with --apply.")
    path = Path(str(value)).expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".{TOOL}_probe_{secrets.token_hex(8)}"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError:
        raise AccountError(EXIT_AUDIT_WRITE, "The audit directory is not writable.") from None
    return path


def _new_audit(operation: str, reason: str) -> dict[str, Any]:
    audit: dict[str, Any] = {name: None for name in AUDIT_FIELDS}
    audit.update(
        {
            "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "tool": TOOL,
            "tool_commit": _tool_commit(),
            "hostname": socket.gethostname(),
            "operator_os_user": getpass.getuser(),
            "operation": operation,
            "reason": reason,
            "result": "BLOCKED",
            "user_row_created": False,
            "password_hash_changed": False,
            "credential_file_written": False,
            "credential_file_removed": False,
            "database_verified": False,
            "api_health_verified": False,
            "api_login_verified": False,
            "api_me_verified": False,
            "rollback_attempted": False,
            "rollback_succeeded": False,
        }
    )
    return audit


def write_audit(audit_dir: Path, audit: dict[str, Any]) -> tuple[Path, Path]:
    sanitized = {name: audit.get(name) for name in AUDIT_FIELDS}
    stamp = re.sub(r"[^0-9TZ]", "", str(sanitized["timestamp_utc"]))
    base = f"AGROSAT_USER_ACCOUNT_{str(sanitized['operation']).upper().replace('-', '_')}_{stamp}_{secrets.token_hex(3)}"
    json_path = audit_dir / f"{base}.json"
    text_path = audit_dir / f"{base}.txt"
    temporary = [audit_dir / f".{json_path.name}.tmp", audit_dir / f".{text_path.name}.tmp"]
    try:
        temporary[0].write_text(
            json.dumps(sanitized, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        temporary[1].write_text(
            "\n".join(f"{name}={sanitized[name]}" for name in AUDIT_FIELDS) + "\n", encoding="utf-8"
        )
        os.replace(temporary[0], json_path)
        os.replace(temporary[1], text_path)
    except OSError:
        for path in temporary:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise AccountError(EXIT_AUDIT_WRITE, "The sanitized audit record could not be written.") from None
    return json_path, text_path


# ─── database access ─────────────────────────────────────────────────────────


class SqlStore:
    """One SQLAlchemy session over the application's own User model."""

    def __init__(self, session: Any, user_model: Any, text: Callable[[str], Any]) -> None:
        self.session = session
        self.User = user_model
        self.text = text

    def begin_read_only(self) -> None:
        self.session.execute(self.text("SET TRANSACTION READ ONLY"))

    def database_name(self) -> str:
        return self.session.execute(self.text("SELECT current_database()")).scalar()

    def target(self) -> DatabaseTarget:
        row = self.session.execute(self.text(
            "SELECT current_database() AS name, host(inet_server_addr()) AS address, "
            "inet_server_port() AS port, to_regclass('public.alembic_version') IS NOT NULL AS versioned"
        )).mappings().one()
        revision = None
        if row["versioned"]:
            revisions = self.session.execute(self.text("SELECT version_num FROM alembic_version")).scalars().all()
            revision = revisions[0] if len(revisions) == 1 else None
        return DatabaseTarget(row["name"], row["address"], row["port"], revision)

    def find_enterprise(self, enterprise_id: int) -> Optional[Enterprise]:
        row = self.session.execute(
            self.text("SELECT id, name, is_active FROM enterprises WHERE id = :id"), {"id": enterprise_id}
        ).mappings().first()
        if row is None:
            return None
        return Enterprise(int(row["id"]), str(row["name"]), bool(row["is_active"]))

    def _account(self, user: Any) -> Account:
        return Account(
            id=int(user.id),
            login=str(user.email),
            full_name=user.full_name,
            role=normalize_role(user.role),
            enterprise_id=user.enterprise_id,
            is_active=bool(user.is_active),
            created_at=user.created_at,
            last_login=user.last_login,
        )

    def accounts_by_login(self, login: str, *, lock: bool = False) -> list[Account]:
        from sqlalchemy import func

        query = self.session.query(self.User).filter(
            func.lower(func.btrim(self.User.email)) == login
        ).order_by(self.User.id)
        if lock:
            query = query.with_for_update()
        return [self._account(user) for user in query.limit(3).all()]

    def accounts(self) -> list[Account]:
        return [self._account(user) for user in self.session.query(self.User).order_by(self.User.id).all()]

    def lock_account_changes(self) -> None:
        self.session.execute(self.text("SELECT pg_advisory_xact_lock(:key)"), {"key": ACCOUNT_LOCK_KEY})

    def insert_account(self, *, login: str, full_name: str, role: str, enterprise_id: int,
                       password_hash: str) -> int:
        user = self.User(
            email=login,
            full_name=full_name,
            role=role,
            enterprise_id=enterprise_id,
            hashed_password=password_hash,
            is_active=True,
        )
        self.session.add(user)
        self.session.flush()
        return int(user.id)

    def credential_state(self, user_id: int, *, lock: bool = False) -> Optional[tuple[dict[str, Any], str]]:
        """The preserved profile fields and the stored hash; the hash never leaves this module."""
        query = self.session.query(self.User).filter(self.User.id == user_id)
        if lock:
            query = query.with_for_update()
        user = query.one_or_none()
        if user is None:
            return None
        profile = {name: getattr(user, name) for name in PRESERVED_ON_RESET}
        profile["role"] = normalize_role(profile["role"])
        return profile, str(user.hashed_password)

    def replace_password_hash(self, user_id: int, expected_hash: str, new_hash: str) -> bool:
        from sqlalchemy import update

        table = self.User.__table__
        result = self.session.execute(
            update(table)
            .where(table.c.id == user_id, table.c.hashed_password == expected_hash)
            .values(hashed_password=new_hash)
        )
        return result.rowcount == 1

    def delete_created_account(self, user_id: int, login: str, expected_hash: str) -> bool:
        from sqlalchemy import delete

        table = self.User.__table__
        result = self.session.execute(
            delete(table).where(
                table.c.id == user_id,
                table.c.email == login,
                table.c.hashed_password == expected_hash,
            )
        )
        return result.rowcount == 1

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def close(self) -> None:
        self.session.close()


@dataclass(frozen=True)
class Runtime:
    open_store: Callable[[], Any]
    pwd_context: Any
    environment: str
    expected_head: Callable[[], Optional[str]]


def _load_runtime() -> Runtime:
    """Bind to the running configuration, User model and password context."""
    from sqlalchemy import text

    from api.auth import pwd_context
    from config import settings
    from database import SessionLocal
    from models.monitoring import User
    from services.migration_head import expected_migration_head

    return Runtime(
        open_store=lambda: SqlStore(SessionLocal(), User, text),
        pwd_context=pwd_context,
        environment=str(settings.environment),
        expected_head=lambda: expected_migration_head().revision,
    )


def _commit(store: Any) -> None:
    store.commit()


def _require_database(store: Any, expected: str) -> str:
    expected_name = (expected or "").strip()
    if not expected_name:
        raise AccountError(EXIT_INVALID_INPUT, "--expected-database is required.")
    try:
        actual = store.database_name()
    except Exception:
        raise AccountError(EXIT_DATABASE_IDENTITY, "The database identity could not be verified.") from None
    if not isinstance(actual, str) or not hmac.compare_digest(actual, expected_name):
        raise AccountError(EXIT_DATABASE_IDENTITY, "Database identity mismatch; nothing was read or written.")
    return actual


def _target_payload(target: DatabaseTarget, runtime: Runtime) -> dict[str, Any]:
    expected = runtime.expected_head()
    server = None
    if target.server_address:
        server = f"{target.server_address}:{target.server_port}"
    return {
        "database_name": target.database_name,
        "server": server,
        "environment": runtime.environment,
        "alembic_revision": target.alembic_revision,
        "expected_alembic_revision": expected,
        "revision_match": bool(expected) and target.alembic_revision == expected,
    }


def _schema_problem(target: DatabaseTarget, runtime: Runtime) -> Optional[str]:
    expected = runtime.expected_head()
    if not expected:
        return "The code's migration head could not be resolved; refusing to change accounts."
    if target.alembic_revision != expected:
        return (
            f"The database is at {target.alembic_revision}, but this code requires {expected}; "
            "run the tool from the release that matches the database."
        )
    return None


# ─── planning (pure) ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CreateRequest:
    login: str
    full_name: str
    role: str
    enterprise_id: int
    distinct_from: tuple[int, ...] = ()

    @property
    def tokens(self) -> tuple[str, ...]:
        return name_tokens(self.full_name)


@dataclass(frozen=True)
class CreateState:
    target: DatabaseTarget
    enterprise_record: Optional[Enterprise]
    login_matches: tuple[Account, ...]
    people: tuple[Account, ...]


def create_request_from(args: argparse.Namespace) -> CreateRequest:
    role = normalize_role(args.role)
    if role == ADMIN_ROLE:
        raise AccountError(
            EXIT_ROLE_REJECTED,
            "Administrator accounts are not managed by this tool; use manage_admin_credentials.py.",
        )
    if role not in MANAGED_ROLES:
        raise AccountError(EXIT_INVALID_INPUT, f"Unsupported role; use one of: {', '.join(MANAGED_ROLES)}.")
    if args.enterprise_id is None or args.enterprise_id <= 0:
        raise AccountError(EXIT_INVALID_INPUT, "A positive --enterprise-id is required for every managed role.")
    distinct = tuple(sorted(set(args.distinct_from or ())))
    if any(value <= 0 for value in distinct):
        raise AccountError(EXIT_INVALID_INPUT, "--distinct-from takes positive user ids.")
    if not (args.reason or "").strip():
        raise AccountError(EXIT_INVALID_INPUT, "A --reason is required.")
    return CreateRequest(
        login=validate_new_login(args.login),
        full_name=clean_full_name(args.full_name),
        role=role,
        enterprise_id=int(args.enterprise_id),
        distinct_from=distinct,
    )


def _describe(account: Account) -> str:
    return (
        f"account {account.id} ({account.login}, {account.role}, enterprise {account.enterprise_id}, "
        f"{'active' if account.is_active else 'inactive'})"
    )


def plan_create(request: CreateRequest, state: CreateState, runtime: Runtime) -> Plan:
    blocking: list[tuple[int, str]] = []
    login_ids = {account.id for account in state.login_matches}
    matches = tuple(
        account
        for account in state.people
        if account.id not in login_ids and same_person(request.tokens, name_tokens(account.full_name))
    )
    unknown = sorted(set(request.distinct_from) - {account.id for account in matches})
    if unknown:
        blocking.append((EXIT_INVALID_INPUT, f"--distinct-from {unknown} does not name an account whose full "
                                              "name matches this person."))
    undecided = [account for account in matches if account.id not in request.distinct_from]
    if undecided:
        blocking.append((EXIT_CONFLICT, "An account whose full name matches this person already exists: "
                                         + "; ".join(_describe(account) for account in undecided)
                                         + ". Operator decision required: reuse that account, or repeat with "
                                           "--distinct-from <id> if this is a different person."))

    if len(state.login_matches) == 1:
        existing = state.login_matches[0]
        identical = (
            existing.login == request.login
            and name_tokens(existing.full_name) == request.tokens
            and existing.role == request.role
            and existing.enterprise_id == request.enterprise_id
        )
        if identical and existing.is_active:
            if not blocking:
                return Plan("ALREADY_EXISTS", 0, (), existing, matches)
        elif identical:
            blocking.append((EXIT_CONFLICT, f"{_describe(existing)} already has this identity but is inactive; "
                                             "reactivation is a separate operator decision."))
        else:
            blocking.append((EXIT_CONFLICT, f"The login is already used by {_describe(existing)}; choose another "
                                             "login or reconcile that account first."))
    elif len(state.login_matches) > 1:
        blocking.append((EXIT_CONFLICT, "Several accounts already use this login in different letter case; "
                                         "resolve them before creating another."))

    schema = _schema_problem(state.target, runtime)
    if schema:
        blocking.append((EXIT_SCHEMA_MISMATCH, schema))
    if state.enterprise_record is None:
        blocking.append((EXIT_ENTERPRISE, f"Enterprise {request.enterprise_id} does not exist."))
    elif not state.enterprise_record.is_active:
        blocking.append((EXIT_ENTERPRISE, f"Enterprise {request.enterprise_id} is not active."))

    if state.login_matches and not blocking:
        blocking.append((EXIT_CONFLICT, "The login is already in use."))
    if blocking:
        return Plan(
            "BLOCKED",
            _choose_code([code for code, _ in blocking]),
            tuple(message for _, message in blocking),
            None,
            matches,
        )
    return Plan("CREATE", 0, (), None, matches)


@dataclass(frozen=True)
class ResetRequest:
    login: str
    expected_user_id: int
    verify_api: bool


@dataclass(frozen=True)
class ResetState:
    target: DatabaseTarget
    login_matches: tuple[Account, ...]


def reset_request_from(args: argparse.Namespace) -> ResetRequest:
    if args.expected_user_id is None or args.expected_user_id <= 0:
        raise AccountError(EXIT_INVALID_INPUT, "A positive --expected-user-id is required.")
    if not (args.reason or "").strip():
        raise AccountError(EXIT_INVALID_INPUT, "A --reason is required.")
    return ResetRequest(
        login=normalize_login(args.login),
        expected_user_id=int(args.expected_user_id),
        verify_api=bool(getattr(args, "verify_api_base", None)),
    )


def plan_reset(request: ResetRequest, state: ResetState, runtime: Runtime) -> Plan:
    blocking: list[tuple[int, str]] = []
    account: Optional[Account] = None
    if not state.login_matches:
        blocking.append((EXIT_TARGET_IDENTITY, "No account uses this login."))
    elif len(state.login_matches) > 1:
        blocking.append((EXIT_TARGET_IDENTITY, "Several accounts match this login ignoring letter case; "
                                                "the target is ambiguous."))
    else:
        account = state.login_matches[0]
        if account.id != request.expected_user_id:
            blocking.append((EXIT_TARGET_IDENTITY, "The login belongs to a different account than "
                                                    "--expected-user-id."))
        elif account.role == ADMIN_ROLE:
            blocking.append((EXIT_ROLE_REJECTED, "Administrator credentials are reset only with "
                                                  "manage_admin_credentials.py."))
        elif account.role not in MANAGED_ROLES:
            blocking.append((EXIT_TARGET_IDENTITY, "The account has no supported non-admin role."))
        elif account.login != request.login:
            blocking.append((EXIT_TARGET_IDENTITY, "The stored login is not in its normalized form, so the "
                                                    "account cannot sign in; that needs a separate decision."))
        elif request.verify_api and not account.is_active:
            blocking.append((EXIT_INVALID_INPUT, "--verify-api-base cannot verify an inactive account, whose "
                                                  "sign-in is refused."))
    schema = _schema_problem(state.target, runtime)
    if schema:
        blocking.append((EXIT_SCHEMA_MISMATCH, schema))
    if blocking:
        return Plan(
            "BLOCKED",
            _choose_code([code for code, _ in blocking]),
            tuple(message for _, message in blocking),
            account,
        )
    return Plan("RESET", 0, (), account)


# ─── read-only phases ────────────────────────────────────────────────────────


def _read_only(runtime: Runtime, expected_database: str, reader: Callable[[Any], Any]) -> Any:
    store = runtime.open_store()
    try:
        store.begin_read_only()
        _require_database(store, expected_database)
        result = reader(store)
        store.rollback()
        return result
    except AccountError:
        store.rollback()
        raise
    except Exception:
        store.rollback()
        raise AccountError(EXIT_DATABASE_UPDATE, "The read-only database check failed.") from None
    finally:
        store.close()


def _read_create_state(runtime: Runtime, expected_database: str, request: CreateRequest) -> CreateState:
    return _read_only(runtime, expected_database, lambda store: CreateState(
        target=store.target(),
        enterprise_record=store.find_enterprise(request.enterprise_id),
        login_matches=tuple(store.accounts_by_login(request.login)),
        people=tuple(store.accounts()),
    ))


def _read_reset_state(runtime: Runtime, expected_database: str, request: ResetRequest) -> ResetState:
    return _read_only(runtime, expected_database, lambda store: ResetState(
        target=store.target(),
        login_matches=tuple(store.accounts_by_login(request.login)),
    ))


def inspect_accounts(args: argparse.Namespace, runtime: Optional[Runtime] = None) -> dict[str, Any]:
    runtime = runtime or _load_runtime()
    login = normalize_login(args.login)
    tokens = name_tokens(args.full_name) if getattr(args, "full_name", None) else ()

    def read(store: Any) -> tuple[DatabaseTarget, list[Account], list[Account]]:
        return store.target(), store.accounts_by_login(login), (store.accounts() if tokens else [])

    target, matches, people = _read_only(runtime, args.expected_database, read)
    login_ids = {account.id for account in matches}
    return {
        "tool": TOOL,
        "operation": "inspect",
        "mutation": False,
        "database_target": _target_payload(target, runtime),
        "login": login,
        "login_well_formed_for_new_account": login_is_well_formed(login),
        "login_matches": [account.public() for account in matches],
        "exact_login_match": [account.id for account in matches if account.login == login],
        "full_name": getattr(args, "full_name", None),
        "person_matches": [
            account.public()
            for account in people
            if account.id not in login_ids and same_person(tokens, name_tokens(account.full_name))
        ],
    }


# ─── API verification (optional, shared with the admin CLI) ──────────────────


def _api_base(value: str) -> str:
    try:
        base = admin_tool._api_base(value)
    except admin_tool.RecoveryError as exc:
        raise AccountError(EXIT_INVALID_INPUT, str(exc)) from None
    parts = urlsplit(base)
    # The generated credential is sent to this address: loopback or TLS only.
    if parts.scheme != "https" and (parts.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise AccountError(EXIT_INVALID_INPUT, "--verify-api-base must be a loopback address or use https.")
    return base


def _api_preflight(base: str, audit: dict[str, Any]) -> None:
    try:
        status, _ = admin_tool._http_json("GET", base + "/health")
    except RuntimeError:
        status = None
    if status != 200:
        raise AccountError(EXIT_API_PREFLIGHT, "The API health preflight failed; nothing was written.")
    audit["api_health_verified"] = True


def _verify_api(base: str, login: str, password: str, expected: dict[str, Any], audit: dict[str, Any]) -> None:
    status, payload = admin_tool._http_json(
        "POST", base + "/api/auth/login", data={"username": login, "password": password}
    )
    token = payload.get("access_token")
    if status != 200 or not isinstance(token, str) or not token:
        raise RuntimeError("login verification failed")
    audit["api_login_verified"] = True
    status, current = admin_tool._http_json("GET", base + "/api/auth/me", bearer=token)
    token = None
    identity_ok = status == 200 and all(
        (normalize_role(current.get(key)) if key == "role" else current.get(key)) == value
        for key, value in expected.items()
    )
    if not identity_ok:
        raise RuntimeError("identity verification failed")
    audit["api_me_verified"] = True


# ─── output ──────────────────────────────────────────────────────────────────


def _emit(document: dict[str, Any]) -> None:
    try:
        print(json.dumps(document, ensure_ascii=False, sort_keys=True, default=str), flush=True)
    except UnicodeEncodeError:
        print(json.dumps(document, ensure_ascii=True, sort_keys=True, default=str), flush=True)


def _stderr(message: str) -> None:
    try:
        print(message, file=sys.stderr, flush=True)
    except UnicodeEncodeError:
        print(message.encode("ascii", "backslashreplace").decode("ascii"), file=sys.stderr, flush=True)


@dataclass
class Outcome:
    exit_code: int
    preflight: dict[str, Any]
    result: Optional[dict[str, Any]] = None
    audit_paths: tuple[Path, ...] = ()
    error: Optional[str] = None


def _credential_check(value: str, audit_dir: Optional[Path]) -> tuple[Optional[Path], dict[str, Any], Optional[AccountError]]:
    try:
        target = check_credential_path(value, audit_dir=audit_dir)
    except AccountError as exc:
        return None, {"path": value, "usable": False, "problem": str(exc)}, exc
    return target, {"path": str(target), "usable": True, "problem": None}, None


_UNEXPECTED_FAILURE = "Unexpected failure; details are withheld to keep secrets out of logs."


def _record_error(audit: dict[str, Any], error: AccountError, *, credential_live: bool) -> None:
    audit["error_code"] = error.code
    audit["error"] = str(error)
    audit["outcome"] = {
        EXIT_VERIFICATION_ROLLED_BACK: "ROLLED_BACK",
        EXIT_CRITICAL_ROLLBACK: "CRITICAL_MANUAL_INTERVENTION",
    }.get(error.code, "CRITICAL_MANUAL_INTERVENTION" if credential_live else "NOT_CHANGED")


def _conclude(operation: str, preflight: dict[str, Any], audit: dict[str, Any], audit_dir: Path,
              error: Optional[AccountError]) -> Outcome:
    """Write the audit record; a failure to write it never hides the result."""
    try:
        paths = write_audit(audit_dir, audit)
    except AccountError as audit_error:
        result = _result_document(operation, audit, ())
        result["audit_write_failed"] = True
        if error is None:
            return Outcome(EXIT_AUDIT_WRITE, preflight, result, (), str(audit_error))
        return Outcome(error.code, preflight, result, (), f"{error} {audit_error}")
    result = _result_document(operation, audit, paths)
    if error is not None:
        return Outcome(error.code, preflight, result, paths, str(error))
    return Outcome(0, preflight, result, paths)


def _result_document(operation: str, audit: dict[str, Any], paths: tuple[Path, ...]) -> dict[str, Any]:
    keys = (
        "result", "outcome", "error_code", "error", "target_user_id", "target_login", "target_full_name",
        "target_role", "target_enterprise_id", "target_is_active", "user_row_created", "password_hash_changed",
        "credential_file", "credential_file_written", "credential_file_removed", "database_verified",
        "api_login_verified", "api_me_verified", "rollback_attempted", "rollback_succeeded",
    )
    document = {
        "stage": "result",
        "tool": TOOL,
        "operation": operation,
        "database_changed": bool(audit.get("user_row_created") or audit.get("password_hash_changed")),
    }
    document.update({key: audit.get(key) for key in keys})
    document["audit_files"] = [str(path) for path in paths]
    return document


# ─── create ──────────────────────────────────────────────────────────────────


def run_create(args: argparse.Namespace, runtime: Optional[Runtime] = None) -> Outcome:
    request = create_request_from(args)
    apply = bool(args.apply)
    audit_dir = prepare_audit_dir(args.audit_dir) if apply else None
    base = _api_base(args.verify_api_base) if args.verify_api_base else None
    runtime = runtime or _load_runtime()

    state = _read_create_state(runtime, args.expected_database, request)
    plan = plan_create(request, state, runtime)
    credential_target, credential_report, credential_error = (None, {"path": args.credential_file,
                                                                     "usable": None,
                                                                     "problem": "not needed: no credential is set"},
                                                              None)
    if plan.outcome != "ALREADY_EXISTS":
        credential_target, credential_report, credential_error = _credential_check(args.credential_file, audit_dir)
    reasons = list(plan.reasons)
    codes = [plan.code] if plan.code else []
    if credential_error is not None:
        reasons.append(str(credential_error))
        codes.append(credential_error.code)
    exit_code = _choose_code(codes)
    preflight = {
        "stage": "preflight",
        "tool": TOOL,
        "operation": "create",
        "mode": "apply" if apply else "dry-run",
        "mutation": False,
        "database_target": _target_payload(state.target, runtime),
        "request": {
            "login": request.login,
            "full_name": request.full_name,
            "role": request.role,
            "enterprise_id": request.enterprise_id,
            "distinct_from": list(request.distinct_from),
        },
        "enterprise": None if state.enterprise_record is None else {
            "id": state.enterprise_record.id, "name": state.enterprise_record.name, "is_active": state.enterprise_record.is_active,
        },
        "login_matches": [account.public() for account in state.login_matches],
        "person_matches": [account.public() for account in plan.person_matches],
        "credential_file": credential_report,
        "plan": "BLOCKED" if exit_code else plan.outcome,
        "blocking_reasons": reasons,
        "exit_code": exit_code,
    }
    if not apply:
        return Outcome(exit_code, preflight, error=reasons[0] if reasons else None)

    audit = _new_audit("create", args.reason.strip())
    audit.update({
        "database_name": state.target.database_name,
        "database_server": preflight["database_target"]["server"],
        "environment": runtime.environment,
        "alembic_revision": state.target.alembic_revision,
        "expected_alembic_revision": preflight["database_target"]["expected_alembic_revision"],
        "target_login": request.login,
        "target_full_name": request.full_name,
        "target_role": request.role,
        "target_enterprise_id": request.enterprise_id,
        "distinct_from_user_ids": list(request.distinct_from),
    })
    credential: Optional[CredentialFile] = None
    # The hand-off file is kept exactly while the database holds its credential.
    credential_live = False
    password: Optional[str] = None
    written_hash: Optional[str] = None
    error: Optional[AccountError] = None
    try:
        if exit_code:
            raise AccountError(exit_code, reasons[0])
        if plan.outcome == "ALREADY_EXISTS":
            audit.update({
                "result": "PASS",
                "outcome": "NO_CHANGE_ALREADY_EXISTS",
                "target_user_id": plan.existing.id,
                "target_is_active": plan.existing.is_active,
            })
        else:
            if base:
                _api_preflight(base, audit)
            password = generate_password()
            credential = write_credential_file(credential_target, _credential_body(
                request.login, password, operation="create", role=request.role,
                enterprise_id=request.enterprise_id, user_id=None,
            ))
            audit["credential_file"] = str(credential.path)
            audit["credential_file_written"] = True
            written_hash = runtime.pwd_context.hash(password)
            user_id = _create_in_transaction(runtime, args.expected_database, request, written_hash)
            credential_live = True
            audit.update({"user_row_created": True, "target_user_id": user_id, "target_is_active": True})
            try:
                _verify_created(runtime, args.expected_database, request, user_id, password, written_hash)
                audit["database_verified"] = True
                if base:
                    _verify_api(base, request.login, password, {
                        "id": user_id, "email": request.login, "role": request.role,
                        "enterprise_id": request.enterprise_id, "is_active": True,
                    }, audit)
            except Exception:
                audit["rollback_attempted"] = True
                _undo_create(runtime, request.login, user_id, written_hash)
                credential_live = False
                audit["rollback_succeeded"] = True
                audit["user_row_created"] = False
                raise AccountError(
                    EXIT_VERIFICATION_ROLLED_BACK,
                    "Post-commit verification failed; the new account was removed again.",
                ) from None
            audit.update({"result": "PASS", "outcome": "CREATED"})
    except AccountError as exc:
        error = exc
        _record_error(audit, exc, credential_live=credential_live)
    except Exception:  # never echo the text: it may carry SQL parameters
        error = AccountError(EXIT_UNEXPECTED, _UNEXPECTED_FAILURE)
        _record_error(audit, error, credential_live=credential_live)
    finally:
        password = None
        written_hash = None
        if credential is not None and not credential_live:
            audit["credential_file_removed"] = credential.remove()
    return _conclude("create", preflight, audit, audit_dir, error)


def _create_in_transaction(runtime: Runtime, expected_database: str, request: CreateRequest,
                           password_hash: str) -> int:
    store = runtime.open_store()
    try:
        _require_database(store, expected_database)
        store.lock_account_changes()
        state = CreateState(
            target=store.target(),
            enterprise_record=store.find_enterprise(request.enterprise_id),
            login_matches=tuple(store.accounts_by_login(request.login, lock=True)),
            people=tuple(store.accounts()),
        )
        plan = plan_create(request, state, runtime)
        if plan.outcome != "CREATE":
            raise AccountError(
                plan.code or EXIT_CONFLICT,
                "The accounts changed after the preflight; nothing was written. "
                + " ".join(plan.reasons or ("The account now exists.",)),
            )
        user_id = store.insert_account(
            login=request.login,
            full_name=request.full_name,
            role=request.role,
            enterprise_id=request.enterprise_id,
            password_hash=password_hash,
        )
        _commit(store)
        return user_id
    except AccountError:
        store.rollback()
        raise
    except Exception:
        store.rollback()
        raise AccountError(EXIT_DATABASE_UPDATE, "Account creation failed; no account was created.") from None
    finally:
        store.close()


def _verify_created(runtime: Runtime, expected_database: str, request: CreateRequest, user_id: int,
                    password: str, written_hash: str) -> None:
    def read(store: Any) -> tuple[Optional[tuple[dict[str, Any], str]], list[Account]]:
        return store.credential_state(user_id), store.accounts_by_login(request.login)

    state, matches = _read_only(runtime, expected_database, read)
    if state is None:
        raise RuntimeError("created account not found")
    profile, stored_hash = state
    checks = (
        hmac.compare_digest(stored_hash, written_hash),
        bool(runtime.pwd_context.verify(password, stored_hash)),
        profile["email"] == request.login,
        profile["full_name"] == request.full_name,
        profile["role"] == request.role,
        profile["enterprise_id"] == request.enterprise_id,
        profile["is_active"] is True,
        [account.id for account in matches] == [user_id],
    )
    if not all(checks):
        raise RuntimeError("created account verification failed")


def _undo_create(runtime: Runtime, login: str, user_id: int, written_hash: str) -> None:
    store = runtime.open_store()
    try:
        removed = store.delete_created_account(user_id, login, written_hash)
        if not removed:
            store.rollback()
            raise AccountError(
                EXIT_CRITICAL_ROLLBACK,
                f"Account {user_id} changed before it could be removed; manual intervention is required.",
            )
        _commit(store)
    except AccountError:
        raise
    except Exception:
        store.rollback()
        raise AccountError(
            EXIT_CRITICAL_ROLLBACK,
            f"Removing unverified account {user_id} failed; manual intervention is required.",
        ) from None
    finally:
        store.close()
    check = runtime.open_store()
    try:
        check.begin_read_only()
        remaining = check.credential_state(user_id)
        check.rollback()
    except Exception:
        remaining = True
    finally:
        check.close()
    if remaining is not None:
        raise AccountError(
            EXIT_CRITICAL_ROLLBACK,
            f"Account {user_id} is still present after its removal; manual intervention is required.",
        )


# ─── reset-password ──────────────────────────────────────────────────────────


def run_reset(args: argparse.Namespace, runtime: Optional[Runtime] = None) -> Outcome:
    request = reset_request_from(args)
    apply = bool(args.apply)
    audit_dir = prepare_audit_dir(args.audit_dir) if apply else None
    base = _api_base(args.verify_api_base) if args.verify_api_base else None
    runtime = runtime or _load_runtime()

    state = _read_reset_state(runtime, args.expected_database, request)
    plan = plan_reset(request, state, runtime)
    credential_target, credential_report, credential_error = _credential_check(args.credential_file, audit_dir)
    reasons = list(plan.reasons)
    codes = [plan.code] if plan.code else []
    if credential_error is not None:
        reasons.append(str(credential_error))
        codes.append(credential_error.code)
    exit_code = _choose_code(codes)
    account = plan.existing
    preflight = {
        "stage": "preflight",
        "tool": TOOL,
        "operation": "reset-password",
        "mode": "apply" if apply else "dry-run",
        "mutation": False,
        "database_target": _target_payload(state.target, runtime),
        "request": {"login": request.login, "expected_user_id": request.expected_user_id},
        "login_matches": [item.public() for item in state.login_matches],
        "credential_file": credential_report,
        "plan": "BLOCKED" if exit_code else plan.outcome,
        "blocking_reasons": reasons,
        "exit_code": exit_code,
    }
    if not apply:
        return Outcome(exit_code, preflight, error=reasons[0] if reasons else None)

    audit = _new_audit("reset-password", args.reason.strip())
    audit.update({
        "database_name": state.target.database_name,
        "database_server": preflight["database_target"]["server"],
        "environment": runtime.environment,
        "alembic_revision": state.target.alembic_revision,
        "expected_alembic_revision": preflight["database_target"]["expected_alembic_revision"],
        "target_user_id": request.expected_user_id,
        "target_login": request.login,
    })
    if account is not None:
        audit.update({
            "target_full_name": account.full_name,
            "target_role": account.role,
            "target_enterprise_id": account.enterprise_id,
            "target_is_active": account.is_active,
        })
    credential: Optional[CredentialFile] = None
    # The hand-off file is kept exactly while the database holds its credential.
    credential_live = False
    password: Optional[str] = None
    new_hash: Optional[str] = None
    previous_hash: Optional[str] = None
    error: Optional[AccountError] = None
    try:
        if exit_code:
            raise AccountError(exit_code, reasons[0])
        if base:
            _api_preflight(base, audit)
        password = generate_password()
        credential = write_credential_file(credential_target, _credential_body(
            request.login, password, operation="reset-password", role=account.role,
            enterprise_id=account.enterprise_id, user_id=account.id,
        ))
        audit["credential_file"] = str(credential.path)
        audit["credential_file_written"] = True
        new_hash = runtime.pwd_context.hash(password)
        profile, previous_hash = _reset_in_transaction(runtime, args.expected_database, request, new_hash)
        credential_live = True
        audit["password_hash_changed"] = True
        try:
            _verify_reset(runtime, args.expected_database, request, profile, password, new_hash)
            audit["database_verified"] = True
            if base:
                _verify_api(base, request.login, password, {
                    "id": request.expected_user_id, "email": request.login, "role": profile["role"],
                    "enterprise_id": profile["enterprise_id"], "is_active": profile["is_active"],
                }, audit)
        except Exception:
            audit["rollback_attempted"] = True
            _restore_credential(runtime, request.expected_user_id, new_hash, previous_hash)
            credential_live = False
            audit["rollback_succeeded"] = True
            audit["password_hash_changed"] = False
            raise AccountError(
                EXIT_VERIFICATION_ROLLED_BACK,
                "Post-commit verification failed; the previous credential was restored.",
            ) from None
        audit.update({"result": "PASS", "outcome": "PASSWORD_RESET"})
    except AccountError as exc:
        error = exc
        _record_error(audit, exc, credential_live=credential_live)
    except Exception:  # never echo the text: it may carry SQL parameters
        error = AccountError(EXIT_UNEXPECTED, _UNEXPECTED_FAILURE)
        _record_error(audit, error, credential_live=credential_live)
    finally:
        password = None
        new_hash = None
        previous_hash = None
        if credential is not None and not credential_live:
            audit["credential_file_removed"] = credential.remove()
    return _conclude("reset-password", preflight, audit, audit_dir, error)


def _reset_in_transaction(runtime: Runtime, expected_database: str, request: ResetRequest,
                          new_hash: str) -> tuple[dict[str, Any], str]:
    store = runtime.open_store()
    try:
        _require_database(store, expected_database)
        store.lock_account_changes()
        state = ResetState(
            target=store.target(),
            login_matches=tuple(store.accounts_by_login(request.login, lock=True)),
        )
        plan = plan_reset(request, state, runtime)
        if plan.outcome != "RESET":
            raise AccountError(
                plan.code or EXIT_TARGET_IDENTITY,
                "The account changed after the preflight; nothing was written. " + " ".join(plan.reasons),
            )
        current = store.credential_state(request.expected_user_id, lock=True)
        if current is None:
            raise AccountError(EXIT_TARGET_IDENTITY, "The account disappeared; nothing was written.")
        profile, previous_hash = current
        if not store.replace_password_hash(request.expected_user_id, previous_hash, new_hash):
            raise AccountError(EXIT_DATABASE_UPDATE, "The credential changed concurrently; nothing was written.")
        _commit(store)
        return profile, previous_hash
    except AccountError:
        store.rollback()
        raise
    except Exception:
        store.rollback()
        raise AccountError(
            EXIT_DATABASE_UPDATE, "The credential update failed; the previous credential is unchanged."
        ) from None
    finally:
        store.close()


def _verify_reset(runtime: Runtime, expected_database: str, request: ResetRequest,
                  profile_before: dict[str, Any], password: str, new_hash: str) -> None:
    def read(store: Any) -> tuple[Optional[tuple[dict[str, Any], str]], list[Account]]:
        return store.credential_state(request.expected_user_id), store.accounts_by_login(request.login)

    state, matches = _read_only(runtime, expected_database, read)
    if state is None:
        raise RuntimeError("account not found after reset")
    profile, stored_hash = state
    checks = [
        hmac.compare_digest(stored_hash, new_hash),
        bool(runtime.pwd_context.verify(password, stored_hash)),
        [account.id for account in matches] == [request.expected_user_id],
    ]
    checks += [profile[name] == profile_before[name] for name in PRESERVED_ON_RESET]
    if not all(checks):
        raise RuntimeError("reset verification failed")


def _restore_credential(runtime: Runtime, user_id: int, new_hash: str, previous_hash: str) -> None:
    store = runtime.open_store()
    try:
        if not store.replace_password_hash(user_id, new_hash, previous_hash):
            store.rollback()
            raise AccountError(
                EXIT_CRITICAL_ROLLBACK,
                "The credential changed concurrently; the previous one could not be restored safely. "
                "Manual intervention is required.",
            )
        _commit(store)
    except AccountError:
        raise
    except Exception:
        store.rollback()
        raise AccountError(
            EXIT_CRITICAL_ROLLBACK, "Restoring the previous credential failed; manual intervention is required."
        ) from None
    finally:
        store.close()
    check = runtime.open_store()
    try:
        check.begin_read_only()
        state = check.credential_state(user_id)
        check.rollback()
    except Exception:
        state = None
    finally:
        check.close()
    if state is None or not hmac.compare_digest(state[1], previous_hash):
        raise AccountError(
            EXIT_CRITICAL_ROLLBACK, "The previous credential could not be confirmed; manual intervention is required."
        )


# ─── command line ────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manage_user_accounts",
        description=(
            "Inspect, create or reset the password of AgroSat non-admin accounts "
            f"({', '.join(MANAGED_ROLES)}). create and reset-password are dry runs unless --apply is given."
        ),
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser("inspect", help="Read-only identity and duplicate check", allow_abbrev=False)
    inspect.add_argument("--login", required=True)
    inspect.add_argument("--full-name")
    inspect.add_argument("--expected-database", required=True)

    create = commands.add_parser(
        "create", help="Create one non-admin account (dry run unless --apply)", allow_abbrev=False
    )
    create.add_argument("--login", required=True)
    create.add_argument("--full-name", required=True)
    create.add_argument("--role", required=True, help=f"one of: {', '.join(MANAGED_ROLES)}")
    create.add_argument("--enterprise-id", required=True, type=int)
    create.add_argument("--distinct-from", type=int, action="append", default=[], metavar="USER_ID",
                        help="an existing account with a matching full name that is a different person")

    reset = commands.add_parser(
        "reset-password",
        help="Replace the credential of one non-admin account (dry run unless --apply)",
        allow_abbrev=False,
    )
    reset.add_argument("--login", required=True)
    reset.add_argument("--expected-user-id", required=True, type=int)

    for command in (create, reset):
        command.add_argument("--expected-database", required=True)
        command.add_argument("--credential-file", required=True,
                             help="new file in an existing private directory; never overwritten")
        command.add_argument("--reason", required=True)
        command.add_argument("--audit-dir", help="required with --apply; receives the sanitized audit record")
        command.add_argument("--verify-api-base", help="optionally prove sign-in through the running API")
        command.add_argument("--apply", action="store_true", help="perform the change (default: dry run)")
    return parser


def _finish(outcome: Outcome) -> int:
    _emit(outcome.preflight)
    if outcome.result is not None:
        _emit(outcome.result)
    if outcome.exit_code and outcome.error:
        _stderr(f"ERROR: {outcome.error}")
    return outcome.exit_code


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect":
            _emit(inspect_accounts(args))
            return 0
        if args.command == "create":
            return _finish(run_create(args))
        return _finish(run_reset(args))
    except AccountError as exc:
        _stderr(f"ERROR: {exc}")
        return exc.code
    except Exception as exc:  # the text of an unexpected error may carry SQL parameters
        _stderr(f"ERROR: unexpected {type(exc).__name__}; details are withheld to keep secrets out of logs.")
        return EXIT_UNEXPECTED


if __name__ == "__main__":
    raise SystemExit(main())
