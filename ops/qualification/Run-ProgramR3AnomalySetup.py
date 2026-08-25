"""Create and migrate the protected TASK_217 database without persisting secrets."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from dotenv import dotenv_values
import psycopg2
from psycopg2 import sql
from sqlalchemy.engine import make_url


TARGET_PREFIX = "agrosat_r3_task217_"
SOURCE_PREFIX = "agrosat_r3_d_pixel_ndvi_"
SENSITIVE = re.compile(r"(?i)(secret|token|password|api.?key|database.?url|authorization|cookie|pgpass)")


def assert_target(name: str) -> None:
    if name == "agrosat" or not name.startswith(TARGET_PREFIX):
        raise RuntimeError("TASK217_DATABASE_IDENTITY_REJECTED")
    suffix = name.removeprefix(TARGET_PREFIX)
    if not suffix or not suffix.replace("_", "").isalnum():
        raise RuntimeError("TASK217_DATABASE_IDENTITY_REJECTED")


def protected_values(path: Path) -> dict[str, str]:
    values = {key: str(value) for key, value in dotenv_values(path.resolve(strict=True)).items() if value is not None}
    source = make_url(values.get("DATABASE_URL") or values.get("SUPABASE_DATABASE_URL") or "")
    if source.database == "agrosat" or not str(source.database or "").startswith(SOURCE_PREFIX):
        raise RuntimeError("TASK217_SOURCE_DATABASE_IDENTITY_REJECTED")
    if len(values.get("SECRET_KEY", "").strip()) < 32:
        raise RuntimeError("TASK217_PROTECTED_SECRET_INVALID")
    return values


def child_environment(values: dict[str, str], runtime_env: Path, database_name: str,
                      media_root: Path, cache_root: Path) -> dict[str, str]:
    assert_target(database_name)
    source = make_url(values["DATABASE_URL"])
    target_url = source.set(database=database_name).render_as_string(hide_password=False)
    environment = {key: value for key, value in os.environ.items() if not SENSITIVE.search(key)}
    environment.update(values)
    environment.update({
        "DATABASE_URL": target_url,
        "AGROSAT_RUNTIME_ENV_FILE": str(runtime_env),
        "ENVIRONMENT": "task217-anomaly-qualification",
        "DEBUG": "false",
        "PUBLIC_REGISTRATION_ENABLED": "false",
        "WIALON_ENABLED": "false",
        "TELEGRAM_NOTIFICATIONS_ENABLED": "false",
        "INSPECTION_MEDIA_DIRECTORY": str(media_root),
        "PIXEL_NDVI_CACHE_DIRECTORY": str(cache_root),
        "RELEASE_REVISION": "task/program-r3-anomaly-inspection-workflow",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return environment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-runtime-env", type=Path, required=True)
    parser.add_argument("--browser-credentials", type=Path, required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument("--create-mode", choices=("empty", "clone"), default="clone")
    args = parser.parse_args()

    assert_target(args.database_name)
    runtime_env = args.source_runtime_env.resolve(strict=True)
    browser_credentials = args.browser_credentials.resolve(strict=True)
    backend_root = args.backend_root.resolve(strict=True)
    runtime_root = args.runtime_root.resolve()
    media_root = runtime_root / "media"
    cache_root = runtime_root / "cache"
    media_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    values = protected_values(runtime_env)
    source_url = make_url(values["DATABASE_URL"])

    admin_url = source_url.set(database="postgres").render_as_string(hide_password=False)
    connection = psycopg2.connect(admin_url)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", (args.database_name,))
            exists = cursor.fetchone() is not None
            if exists and not args.resume_existing:
                raise RuntimeError("TASK217_DATABASE_ALREADY_EXISTS")
            if not exists:
                assert_target(args.database_name)
                if args.create_mode == "empty":
                    cursor.execute(
                        sql.SQL("CREATE DATABASE {}").format(sql.Identifier(args.database_name))
                    )
                else:
                    cursor.execute(
                        sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                            sql.Identifier(args.database_name), sql.Identifier(source_url.database)
                        )
                    )
    finally:
        connection.close()

    environment = child_environment(values, runtime_env, args.database_name, media_root, cache_root)
    assert_target(args.database_name)
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=backend_root, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600,
    )
    if migration.returncode:
        sys.stderr.write(migration.stderr.decode("utf-8", errors="replace"))
        raise RuntimeError("TASK217_ALEMBIC_UPGRADE_FAILED")

    assert_target(args.database_name)
    target_url = make_url(environment["DATABASE_URL"]).render_as_string(hide_password=False)
    with psycopg2.connect(target_url) as connection:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            revision = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM ndvi_records WHERE field_id=4 AND satellite='Sentinel-2'")
            field_scene_count = int(cursor.fetchone()[0])
            cursor.execute("SELECT count(*) FROM pg_extension WHERE extname='postgis'")
            postgis = int(cursor.fetchone()[0]) == 1

    result = {
        "result": "PASS",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "database": args.database_name,
        "database_prefix_valid": True,
        "database_is_production": False,
        "source_database_class": (
            "fresh_empty_database" if args.create_mode == "empty"
            else "approved_task215_isolated_clone"
        ),
        "revision": revision,
        "postgis": postgis,
        "field_4_scene_count": field_scene_count,
        "browser_credentials_path": str(browser_credentials),
        "browser_credentials_size": browser_credentials.stat().st_size,
        "media_root": str(media_root),
        "cache_root": str(cache_root),
        "secret_values_logged": False,
        "secret_values_persisted_by_task217": False,
    }
    print(json.dumps(result, sort_keys=True))
    values.clear()
    environment.clear()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
