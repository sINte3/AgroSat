"""PostgreSQL client tools with credentials confined to child environments.

The connection comes from the runtime environment file's ``DATABASE_URL``,
which is the only place AgroSat keeps database credentials. It is parsed in
memory, checked against the database name the caller expects, and handed to
``psql``/``pg_dump``/``pg_restore``/``createdb`` only through PG* environment
variables of that child. Credentials never appear in argv, output, evidence
or exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

from .common import ControlPlaneError

DATABASE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_STRIPPED_ENVIRONMENT = re.compile(r"(?i)^(PG[A-Z]*|DATABASE_URL|SUPABASE_DATABASE_URL|.*PASSWORD.*)$")


def read_env_value(env_file: Path, key: str) -> str | None:
    """One value from a dotenv-style file (``KEY=value``, optional quotes, '#' comments)."""
    try:
        lines = Path(env_file).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        raise ControlPlaneError("RUNTIME_ENV_FILE_UNREADABLE") from None
    value = None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, separator, rest = line.partition("=")
        if separator and name.strip() == key:
            rest = rest.strip()
            if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in "\"'":
                rest = rest[1:-1]
            value = rest
    return value


@dataclass(frozen=True)
class DatabaseTarget:
    """A database reached through a runtime environment file."""

    env_file: Path
    database_name: str
    pg_bin: Path

    def tool(self, name: str) -> Path:
        path = Path(self.pg_bin) / f"{name}.exe"
        if not path.is_file():
            path = Path(self.pg_bin) / name
        if not path.is_file():
            raise ControlPlaneError("POSTGRESQL_CLIENT_TOOL_MISSING", name)
        return path

    def environment(self, *, database: str | None = None, read_only: bool = True,
                    application: str = "agrosat_controlplane") -> dict[str, str]:
        """A child environment that connects to ``database`` (default: the target)."""
        url = read_env_value(self.env_file, "DATABASE_URL")
        if not url:
            raise ControlPlaneError("RUNTIME_DATABASE_URL_MISSING")
        try:
            parts = urlsplit(url)
            configured = unquote(parts.path.lstrip("/"))
            host, port = parts.hostname, parts.port
            user = unquote(parts.username or "")
            password = unquote(parts.password or "")
        except ValueError:
            raise ControlPlaneError("RUNTIME_DATABASE_URL_MALFORMED") from None
        finally:
            url = None
        if not parts.scheme.startswith("postgres"):
            raise ControlPlaneError("RUNTIME_DATABASE_URL_MALFORMED", "not a PostgreSQL URL")
        if configured != self.database_name:
            raise ControlPlaneError("RUNTIME_DATABASE_IDENTITY_MISMATCH",
                                    "the runtime environment file points at a different database",
                                    expected=self.database_name)
        name = database or self.database_name
        if not DATABASE_NAME_PATTERN.fullmatch(name):
            raise ControlPlaneError("DATABASE_NAME_REJECTED", name)
        environment = {key: value for key, value in os.environ.items() if not _STRIPPED_ENVIRONMENT.match(key)}
        environment.update({
            "PGHOST": host or "localhost", "PGPORT": str(port or 5432), "PGUSER": user,
            "PGPASSWORD": password, "PGDATABASE": name, "PGAPPNAME": application,
            "PGCONNECT_TIMEOUT": "15", "PGCLIENTENCODING": "UTF8",
        })
        if read_only:
            environment["PGOPTIONS"] = "-c default_transaction_read_only=on -c statement_timeout=120000"
        return environment

    def run(self, tool: str, arguments: list[str], *, database: str | None = None, read_only: bool = True,
            timeout: int = 3600, stdout_path: Path | None = None) -> subprocess.CompletedProcess:
        """Run a client tool. Output never contains credentials; stderr is bounded."""
        environment = self.environment(database=database, read_only=read_only)
        try:
            if stdout_path is not None:
                with Path(stdout_path).open("xb") as stream:
                    result = subprocess.run([str(self.tool(tool)), *arguments], env=environment,
                                            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.PIPE,
                                            timeout=timeout)
            else:
                result = subprocess.run([str(self.tool(tool)), *arguments], env=environment,
                                        stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        finally:
            environment.clear()
        return result

    def query(self, sql: str, *, database: str | None = None, read_only: bool = True) -> list[str]:
        """Rows of a one-column query, via psql (unaligned, tuples only)."""
        result = self.run("psql", ["-X", "-A", "-t", "-q", "-v", "ON_ERROR_STOP=1", "-c", sql],
                          database=database, read_only=read_only, timeout=300)
        if result.returncode != 0:
            raise ControlPlaneError("DATABASE_QUERY_FAILED", _bounded(result.stderr))
        return [line for line in result.stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]

    def database_exists(self, name: str) -> bool:
        if not DATABASE_NAME_PATTERN.fullmatch(name):
            raise ControlPlaneError("DATABASE_NAME_REJECTED", name)
        rows = self.query(f"SELECT 1 FROM pg_database WHERE datname = '{name}'", database="postgres")
        return rows == ["1"]

    def revisions(self, *, database: str | None = None) -> list[str]:
        """The Alembic revision rows (empty when the version table is absent)."""
        present = self.query("SELECT to_regclass('public.alembic_version') IS NOT NULL", database=database)
        if present != ["t"]:
            return []
        return self.query("SELECT version_num FROM public.alembic_version ORDER BY version_num", database=database)

    def server_version(self) -> str:
        return self.query("SHOW server_version")[0].strip()


def _bounded(stderr: bytes | str | None) -> str:
    text = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else (stderr or "")
    # Client tools never print passwords, but URLs and 'password=' fragments are cut defensively.
    text = re.sub(r"(?i)(password\s*[=:]\s*)\S+", r"\1[redacted]", text)
    text = re.sub(r"://[^@\s/]+@", "://[redacted]@", text)
    return " ".join(text.split())[:400]


def tool_version(target: DatabaseTarget, tool: str) -> str:
    result = subprocess.run([str(target.tool(tool)), "--version"], capture_output=True, text=True, timeout=60)
    return result.stdout.strip()
