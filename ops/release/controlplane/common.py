"""Shared primitives: errors, time, hashing, JSON evidence, path guards, secrets."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from typing import Any

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
MAX_JSON_BYTES = 4 * 1024 * 1024
# Production roots a rehearsal or a test may never write into.
PRODUCTION_PATH_ROOTS = (r"C:\AgroSat", r"C:\AgroSat_releases", r"C:\AgroSat_runtime")
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

# A value that looks like a credential never enters evidence.
_SECRET_PATTERNS = (
    re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s/]+@", re.IGNORECASE),  # URL with user:password@
    re.compile(r"(?i)\b(password|passwd|pgpassword|secret_key|api_key|client_secret|token)\s*[=:]\s*\S+"),
    re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_SECRET_KEYS = re.compile(r"(?i)(password|passwd|secret|token|api_key|credential|database_url|dsn)")


class ControlPlaneError(Exception):
    """A fail-closed stop. ``code`` is a stable UPPER_SNAKE identifier."""

    def __init__(self, code: str, detail: str | None = None, **facts: Any):
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.facts = facts

    def evidence(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, **({"facts": self.facts} if self.facts else {})}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse_iso(value: Any, code: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ControlPlaneError(code, "timestamp is not an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ControlPlaneError(code, "timestamp is not ISO-8601") from None
    if parsed.tzinfo is None:
        raise ControlPlaneError(code, "timestamp has no UTC offset")
    return parsed.astimezone(timezone.utc)


def require_sha(value: Any, code: str) -> str:
    if not isinstance(value, str) or not SHA_PATTERN.fullmatch(value):
        raise ControlPlaneError(code, "not an exact lowercase 40-character commit SHA")
    return value


def require_sha256(value: Any, code: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ControlPlaneError(code, "not a lowercase SHA-256")
    return value


def require_release_id(value: Any) -> str:
    if not isinstance(value, str) or not RELEASE_ID_PATTERN.fullmatch(value):
        raise ControlPlaneError("RELEASE_ID_MALFORMED", "8-64 characters: letters, digits, '.', '_', '-'")
    return value


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def find_secrets(value: Any, path: str = "$") -> list[str]:
    """Paths inside ``value`` whose key or string looks like a credential."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            where = f"{path}.{key}"
            if isinstance(key, str) and _SECRET_KEYS.search(key) and item not in (None, False, [], {}) \
                    and not isinstance(item, bool):
                if not (isinstance(item, str) and item in ("", "redacted")):
                    found.append(where)
                    continue
            found.extend(find_secrets(item, where))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(find_secrets(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
            found.append(path)
    return found


def assert_no_secrets(value: Any, code: str = "EVIDENCE_SECRET_REJECTED") -> None:
    found = find_secrets(value)
    if found:
        raise ControlPlaneError(code, "credential-like values must never be recorded", paths=found[:20])


def to_json_text(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def write_json_atomic(path: Path, value: Any, *, check_secrets: bool = True) -> None:
    """Replace ``path`` with ``value`` atomically (UTF-8, no BOM, LF)."""
    if check_secrets:
        assert_no_secrets(value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(to_json_text(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_json_immutable(path: Path, value: Any) -> str:
    """Write once. Re-writing identical content is a no-op; different content fails closed."""
    assert_no_secrets(value)
    path = Path(path)
    text = to_json_text(value)
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise ControlPlaneError("IMMUTABLE_RECORD_CONFLICT", str(path.name))
        return sha256_file(path)
    write_json_atomic(path, value)
    return sha256_file(path)


def read_json(path: Path, code: str, *, max_bytes: int = MAX_JSON_BYTES) -> Any:
    path = Path(path)
    try:
        if not path.is_file():
            raise ControlPlaneError(code, f"missing: {path.name}")
        size = path.stat().st_size
        if size > max_bytes:
            raise ControlPlaneError(code, f"larger than {max_bytes} bytes: {path.name}")
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except ControlPlaneError:
        raise
    except (OSError, ValueError):
        raise ControlPlaneError(code, f"unreadable or malformed JSON: {path.name}") from None


def append_jsonl(path: Path, value: Any) -> None:
    assert_no_secrets(value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def normalized_windows_path(value: str | os.PathLike) -> str:
    return ntpath.normpath(str(value)).replace("\\", "/").casefold().rstrip("/")


def is_within(child: str | os.PathLike, root: str | os.PathLike) -> bool:
    """True when ``child`` is ``root`` or below it (Windows semantics, case-insensitive)."""
    candidate, base = normalized_windows_path(child), normalized_windows_path(root)
    return candidate == base or candidate.startswith(base + "/")


def targets_production(path: str | os.PathLike) -> bool:
    candidates = [str(path)]
    if sys.platform == "win32":
        try:
            candidates.append(str(Path(path).resolve(strict=False)))
        except OSError:
            pass
    return any(is_within(item, root) for item in candidates for root in PRODUCTION_PATH_ROOTS)


def is_reparse_point(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def assert_no_reparse_points(root: Path, code: str) -> None:
    """``root`` and everything below it are plain files and directories."""
    root = Path(root)
    if is_reparse_point(root):
        raise ControlPlaneError(code, f"reparse point: {root.name}")
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            if is_reparse_point(Path(directory) / name):
                raise ControlPlaneError(code, f"reparse point below {root.name}: {name}")


MAX_PATH = 260


def extended(path: str | os.PathLike) -> str:
    """A path Win32 file APIs accept beyond MAX_PATH (\\\\?\\ prefix on Windows)."""
    text = str(Path(path).resolve(strict=False))
    if sys.platform == "win32" and not text.startswith("\\\\?\\"):
        return "\\\\?\\" + text
    return text


def copy_tree(source: Path, destination: Path) -> None:
    """Copy a directory tree whose deep paths may exceed MAX_PATH (a venv)."""
    shutil.copytree(extended(source), extended(destination), symlinks=True)


def remove_tree(path: Path) -> None:
    shutil.rmtree(extended(path))


def longest_path(root: Path, final_root: Path) -> int:
    """The longest file path ``root``'s contents will have once it lives at ``final_root``."""
    base = len(str(Path(final_root)))
    prefix = len(extended(root))
    longest = base
    for directory, _, files in os.walk(extended(root)):
        for name in files:
            longest = max(longest, base + len(os.path.join(directory, name)) - prefix)
    return longest


def absolute(value: Any, code: str) -> Path:
    if not isinstance(value, str) or not value or not ntpath.isabs(value) and not os.path.isabs(value):
        raise ControlPlaneError(code, "an absolute path is required")
    return Path(value)
