"""Create the isolated TASK_215 database and protected qualification runtime."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

from dotenv import dotenv_values
import psycopg2
from sqlalchemy.engine import make_url


DATABASE_PREFIX = "agrosat_r3_d_pixel_ndvi_"


def assert_target(name: str) -> None:
    if not name.startswith(DATABASE_PREFIX) or name == "agrosat":
        raise RuntimeError("ISOLATED_DATABASE_IDENTITY_REJECTED")
    suffix = name.removeprefix(DATABASE_PREFIX)
    if not suffix or not suffix.replace("_", "").isalnum():
        raise RuntimeError("ISOLATED_DATABASE_IDENTITY_REJECTED")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_environment(url) -> dict[str, str]:
    names = ("COMSPEC", "PATH", "PATHEXT", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "WINDIR")
    environment = {name: os.environ[name] for name in names if name in os.environ}
    if url.password:
        environment["PGPASSWORD"] = url.password
    environment["PGCONNECT_TIMEOUT"] = "15"
    return environment


def connection_args(url, database: str) -> list[str]:
    return [
        "-h", url.host or "localhost",
        "-p", str(url.port or 5432),
        "-U", url.username or "postgres",
        "-d", database,
    ]


def run(command: list[str], *, environment: dict[str, str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=True,
    )


def quote_env(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def write_protected_runtime_env(path: Path, values: dict[str, str]) -> None:
    payload = "".join(f"{key}={quote_env(value)}\n" for key, value in sorted(values.items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_protected_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def seed_identities(database_url: str, database_name: str, backend_root: Path, credentials_path: Path) -> dict:
    assert_target(database_name)
    sys.path.insert(0, str(backend_root))
    from api.auth import get_password_hash  # imported only after runtime config exists

    passwords = {role: secrets.token_urlsafe(24) for role in ("agronomist", "admin", "other")}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT enterprise_id FROM fields WHERE id = 4")
            field = cursor.fetchone()
            if not field:
                raise RuntimeError("ISOLATED_FIELD_4_MISSING")
            tenant_id = int(field[0])
            cursor.execute("SELECT id FROM enterprises WHERE id <> %s ORDER BY id LIMIT 1", (tenant_id,))
            other = cursor.fetchone()
            if not other:
                raise RuntimeError("ISOLATED_SECOND_TENANT_MISSING")
            other_tenant_id = int(other[0])
            definitions = (
                ("agronomist", tenant_id, "agronomist"),
                ("admin", None, "admin"),
                ("other", other_tenant_id, "viewer"),
            )
            identities: dict[str, dict] = {}
            for label, enterprise_id, role in definitions:
                email = f"task215-{label}-{stamp}@qualification.invalid"
                cursor.execute(
                    """
                    INSERT INTO users (enterprise_id, email, full_name, role, hashed_password, is_active, created_at)
                    VALUES (%s, %s, %s, %s, %s, true, now())
                    RETURNING id
                    """,
                    (enterprise_id, email, f"TASK 215 {label}", role, get_password_hash(passwords[label])),
                )
                identities[label] = {
                    "id": int(cursor.fetchone()[0]),
                    "enterprise_id": enterprise_id,
                    "email": email,
                    "password": passwords[label],
                    "role": role,
                }
        connection.commit()
    write_protected_json(
        credentials_path,
        {"database": database_name, "field_id": 4, "identities": identities},
    )
    sanitized = {
        "field_id": 4,
        "tenant_id": tenant_id,
        "other_tenant_id": other_tenant_id,
        "identity_ids": {name: value["id"] for name, value in identities.items()},
    }
    passwords.clear()
    identities.clear()
    return sanitized


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--production-env", type=Path, required=True)
    parser.add_argument("--cdse-env", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--backup-metadata", type=Path, required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--resume-existing", action="store_true")
    args = parser.parse_args()

    assert_target(args.database_name)
    runtime_root = args.runtime_root.resolve()
    backend_root = args.backend_root.resolve(strict=True)
    backup = args.backup.resolve(strict=True)
    metadata = json.loads(args.backup_metadata.resolve(strict=True).read_text("utf-8"))
    if not metadata.get("usable") or metadata.get("source_database") != "agrosat":
        raise RuntimeError("VALIDATED_BACKUP_IDENTITY_REJECTED")
    backup_sha = sha256(backup)
    if backup_sha != metadata.get("sha256"):
        raise RuntimeError("VALIDATED_BACKUP_SHA256_MISMATCH")

    production = {key: str(value) for key, value in dotenv_values(args.production_env.resolve(strict=True)).items() if value is not None}
    cdse = {key: str(value) for key, value in dotenv_values(args.cdse_env.resolve(strict=True)).items() if value is not None}
    source_url_text = production.get("DATABASE_URL") or production.get("SUPABASE_DATABASE_URL")
    if not source_url_text:
        raise RuntimeError("PROTECTED_DATABASE_CONFIGURATION_MISSING")
    source_url = make_url(source_url_text)
    if source_url.database != "agrosat":
        raise RuntimeError("PRODUCTION_DATABASE_IDENTITY_MISMATCH")
    if set(cdse) != {"SENTINEL_HUB_CLIENT_ID", "SENTINEL_HUB_CLIENT_SECRET"} or not all(cdse.values()):
        raise RuntimeError("PROTECTED_CDSE_CONFIGURATION_INVALID")

    psql = args.pg_bin / "psql.exe"
    createdb = args.pg_bin / "createdb.exe"
    pg_restore = args.pg_bin / "pg_restore.exe"
    if not all(path.is_file() for path in (psql, createdb, pg_restore)):
        raise RuntimeError("POSTGRESQL_NATIVE_TOOLS_MISSING")
    pg_environment = safe_environment(source_url)
    safe_name = args.database_name

    assert_target(safe_name)
    exists = run(
        [str(psql), *connection_args(source_url, "postgres"), "-X", "-v", "ON_ERROR_STOP=1", "-tAc", f"SELECT 1 FROM pg_database WHERE datname='{safe_name}'"],
        environment=pg_environment,
    ).stdout.decode("utf-8", errors="replace").strip()
    if exists and not args.resume_existing:
        raise RuntimeError("ISOLATED_DATABASE_ALREADY_EXISTS")
    if not exists:
        assert_target(safe_name)
        run(
            [str(createdb), "-h", source_url.host or "localhost", "-p", str(source_url.port or 5432), "-U", source_url.username or "postgres", safe_name],
            environment=pg_environment,
        )

        assert_target(safe_name)
        run(
            [str(pg_restore), *connection_args(source_url, safe_name), "--no-owner", "--no-privileges", "--single-transaction", str(backup)],
            environment=pg_environment,
            timeout=1800,
        )

    isolated_url = source_url.set(database=safe_name).render_as_string(hide_password=False)
    runtime_env = runtime_root / "config" / "runtime.env"
    cache_root = runtime_root / "cache"
    browser_credentials = runtime_root / "config" / "browser-credentials.json"
    runtime_values = {
        "DATABASE_URL": isolated_url,
        "SECRET_KEY": production.get("SECRET_KEY", ""),
        "ENVIRONMENT": "pixel-ndvi-qualification",
        "DEBUG": "false",
        "PUBLIC_REGISTRATION_ENABLED": "false",
        "WIALON_ENABLED": "false",
        "TELEGRAM_NOTIFICATIONS_ENABLED": "false",
        "SENTINEL_HUB_PROVIDER": "cdse",
        "SENTINEL_HUB_CLIENT_ID": cdse["SENTINEL_HUB_CLIENT_ID"],
        "SENTINEL_HUB_CLIENT_SECRET": cdse["SENTINEL_HUB_CLIENT_SECRET"],
        "PIXEL_NDVI_CACHE_DIRECTORY": str(cache_root),
        "REDIS_URL": "redis://127.0.0.1:6399/15",
        "RELEASE_REVISION": "task/program-r3-pixel-ndvi-workspace",
    }
    if len(runtime_values["SECRET_KEY"].strip()) < 32:
        raise RuntimeError("PROTECTED_SECRET_KEY_INVALID")
    write_protected_runtime_env(runtime_env, runtime_values)
    cache_root.mkdir(parents=True, exist_ok=True)

    assert_target(safe_name)
    sensitive_name = re.compile(r"(?i)(secret|token|password|api.?key|database.?url|authorization|cookie|pgpass)")
    application_environment = {
        name: value for name, value in os.environ.items() if not sensitive_name.search(name)
    }
    application_environment.update({
        "AGROSAT_RUNTIME_ENV_FILE": str(runtime_env),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        environment=application_environment,
        cwd=backend_root,
        timeout=600,
    )

    assert_target(safe_name)
    os.environ["AGROSAT_RUNTIME_ENV_FILE"] = str(runtime_env)
    seeded = seed_identities(isolated_url, safe_name, backend_root, browser_credentials)

    with psycopg2.connect(isolated_url) as connection:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            revision = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM ndvi_records WHERE field_id = 4 AND satellite = 'Sentinel-2' AND mean_ndvi IS NOT NULL")
            field_scene_count = int(cursor.fetchone()[0])

    result = {
        "result": "PASS",
        "database": safe_name,
        "database_prefix_valid": True,
        "database_is_production": False,
        "backup_sha256": backup_sha,
        "revision": revision,
        "field_4_scene_count": field_scene_count,
        "runtime_env_path": str(runtime_env),
        "browser_credentials_path": str(browser_credentials),
        "cache_root": str(cache_root),
        "seeded": seeded,
        "secret_values_logged": False,
    }
    print(json.dumps(result, sort_keys=True))
    for mapping in (production, cdse, runtime_values):
        mapping.clear()
    pg_environment.clear()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
