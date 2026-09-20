#!/usr/bin/env python3
"""Restore an accepted backup into a protected TASK_221 runtime database."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


DATABASE_NAME = re.compile(r"^agrosat_r3_task221_[a-z0-9_]+$")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--runtime-env", type=Path, required=True)
    parser.add_argument("--pg-bin", type=Path, required=True)
    args = parser.parse_args()
    if args.database == "agrosat" or not DATABASE_NAME.fullmatch(args.database):
        raise RuntimeError("TASK221_DATABASE_IDENTITY_REJECTED")
    backup = args.backup.resolve(strict=True)
    expected_hash = args.expected_sha256.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or file_hash(backup) != expected_hash:
        raise RuntimeError("TASK221_BACKUP_HASH_REJECTED")

    os.environ["AGROSAT_RUNTIME_ENV_FILE"] = str(args.runtime_env.resolve(strict=True))
    backend = args.backend.resolve(strict=True)
    sys.path.insert(0, str(backend))
    from config import settings

    source = make_url(settings.database_url)
    if source.database != "agrosat":
        raise RuntimeError("TASK221_SOURCE_IDENTITY_REJECTED")
    admin = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=:name AND pid<>pg_backend_pid()"
            ),
            {"name": args.database},
        )
        connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{args.database}"')
        connection.exec_driver_sql(f'CREATE DATABASE "{args.database}"')

    environment = os.environ.copy()
    environment.update(
        {
            "PGHOST": source.host or "localhost",
            "PGPORT": str(source.port or 5432),
            "PGUSER": source.username or "",
            "PGPASSWORD": source.password or "",
            "PGDATABASE": args.database,
        }
    )
    restore = subprocess.run(
        [
            str(args.pg_bin.resolve(strict=True) / "pg_restore.exe"),
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            args.database,
            str(backup),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if restore.returncode:
        raise RuntimeError("TASK221_RESTORE_FAILED")
    environment["DATABASE_URL"] = source.set(database=args.database).render_as_string(
        hide_password=False
    )
    environment.pop("AGROSAT_RUNTIME_ENV_FILE", None)
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=backend,
        env=environment,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if migration.returncode:
        raise RuntimeError("TASK221_UPGRADE_FAILED")

    engine = create_engine(source.set(database=args.database))
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        enterprises = connection.execute(text("SELECT count(*) FROM enterprises")).scalar_one()
        fields = connection.execute(text("SELECT count(*) FROM fields")).scalar_one()
        users = connection.execute(text("SELECT count(*) FROM users")).scalar_one()
        owned_tables = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name IN ('operational_notifications','operational_notification_events')"
            )
        ).scalar_one()
    engine.dispose()
    admin.dispose()
    if revision != "0016_operational_command_center" or min(enterprises, fields, users) < 1 or owned_tables != 2:
        raise RuntimeError("TASK221_RUNTIME_BASELINE_REJECTED")
    print(
        {
            "status": "PASS",
            "database": args.database,
            "revision": revision,
            "counts": {
                "enterprises": enterprises,
                "fields": fields,
                "users": users,
                "task221_tables": owned_tables,
            },
            "backup_sha256": expected_hash,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
