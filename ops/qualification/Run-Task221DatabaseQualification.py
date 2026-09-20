#!/usr/bin/env python3
"""Fail-closed fresh/migration/dump/restore qualification for TASK_221."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


DATABASE_NAME = re.compile(r"^agrosat_r3_task221_[a-z0-9_]+$")
HEAD = "0016_operational_command_center"
TABLES = ("operational_notifications", "operational_notification_events")
REQUIRED_INDEXES = {
    "ix_operational_notifications_case",
    "ix_operational_notifications_recipient_inbox",
    "ix_operational_notifications_reconcile",
    "ix_operational_notification_events_timeline",
}
REQUIRED_CONSTRAINTS = {
    "fk_operational_notifications_field_enterprise",
    "uq_operational_notifications_dedupe",
    "fk_operational_notification_events_notification",
    "uq_operational_notification_events_command",
}


def safe_database(name: str) -> str:
    if name == "agrosat" or not DATABASE_NAME.fullmatch(name):
        raise RuntimeError("TASK221_DATABASE_IDENTITY_REJECTED")
    return name


def quote_database(name: str) -> str:
    return '"' + safe_database(name).replace('"', '""') + '"'


def sanitize(value: str) -> str:
    value = re.sub(r"(?i)(postgres(?:ql)?://)[^\s]+", r"\1[REDACTED]", value or "")
    value = re.sub(
        r"(?i)(password|token|secret|authorization)=\S+",
        r"\1=[REDACTED]",
        value,
    )
    return value[-8000:]


def child(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 900):
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    result = {
        "command": [Path(command[0]).name, *command[1:]],
        "exit_code": completed.returncode,
        "stdout": sanitize(completed.stdout),
        "stderr": sanitize(completed.stderr),
    }
    if completed.returncode:
        raise RuntimeError(f"TASK221_CHILD_COMMAND_FAILED:{result}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--restore-database", required=True)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--runtime-env", type=Path, required=True)
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    database = safe_database(args.database)
    restored = safe_database(args.restore_database)
    if database == restored:
        raise RuntimeError("TASK221_DISTINCT_DATABASES_REQUIRED")
    backend = args.backend.resolve(strict=True)
    pg_bin = args.pg_bin.resolve(strict=True)
    os.environ["AGROSAT_RUNTIME_ENV_FILE"] = str(args.runtime_env.resolve(strict=True))
    sys.path.insert(0, str(backend))

    from config import settings

    source = make_url(settings.database_url)
    if source.database != "agrosat":
        raise RuntimeError("TASK221_SOURCE_DATABASE_IDENTITY_REJECTED")
    admin = create_engine(
        source.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    with admin.connect() as connection:
        for name in (database, restored):
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:name AND pid<>pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote_database(name)}")
            connection.exec_driver_sql(f"CREATE DATABASE {quote_database(name)}")

    target = source.set(database=database)
    environment = os.environ.copy()
    environment["DATABASE_URL"] = target.render_as_string(hide_password=False)
    environment.pop("AGROSAT_RUNTIME_ENV_FILE", None)
    engine = create_engine(target, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))

    commands = []
    for alembic_args in (
        ("heads",),
        ("upgrade", "head"),
        ("current",),
        ("check",),
        ("downgrade", "0015_closed_loop_agronomy"),
        ("upgrade", "head"),
    ):
        commands.append(
            child(
                [sys.executable, "-m", "alembic", "-c", "alembic.ini", *alembic_args],
                cwd=backend,
                env=environment,
            )
        )

    import models.registry  # noqa: F401
    from database import Base

    inspector = inspect(engine)
    metadata = {
        name: sorted(column.name for column in Base.metadata.tables[name].columns)
        for name in TABLES
    }
    actual = {
        name: sorted(column["name"] for column in inspector.get_columns(name))
        for name in TABLES
    }
    if metadata != actual:
        raise RuntimeError(f"TASK221_SCHEMA_METADATA_MISMATCH:{metadata!r}:{actual!r}")

    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        invalid_indexes = connection.execute(
            text("SELECT count(*) FROM pg_index WHERE NOT indisvalid")
        ).scalar_one()
        invalid_constraints = connection.execute(
            text("SELECT count(*) FROM pg_constraint WHERE NOT convalidated")
        ).scalar_one()
        indexes = set(
            connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
                    "AND tablename=ANY(:tables)"
                ),
                {"tables": list(TABLES)},
            ).scalars()
        )
        constraints = set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid=ANY(" 
                    "SELECT oid FROM pg_class WHERE relname=ANY(:tables))"
                ),
                {"tables": list(TABLES)},
            ).scalars()
        )
        postgis_srid = connection.execute(
            text(
                "SELECT count(*) FROM geometry_columns WHERE srid<>4326 "
                "AND f_table_schema='public'"
            )
        ).scalar_one()
    if (
        revision != HEAD
        or invalid_indexes
        or invalid_constraints
        or postgis_srid
        or not REQUIRED_INDEXES.issubset(indexes)
        or not REQUIRED_CONSTRAINTS.issubset(constraints)
    ):
        raise RuntimeError("TASK221_FRESH_SCHEMA_INTEGRITY_FAILED")

    args.dump.parent.mkdir(parents=True, exist_ok=True)
    pg_environment = environment.copy()
    pg_environment.update(
        {
            "PGHOST": source.host or "localhost",
            "PGPORT": str(source.port or 5432),
            "PGUSER": source.username or "",
            "PGPASSWORD": source.password or "",
            "PGDATABASE": database,
        }
    )
    child(
        [
            str(pg_bin / "pg_dump.exe"),
            "-Fc",
            "--no-owner",
            "--no-privileges",
            "-f",
            str(args.dump),
            database,
        ],
        cwd=backend,
        env=pg_environment,
    )
    listing = child(
        [str(pg_bin / "pg_restore.exe"), "--list", str(args.dump)],
        cwd=backend,
        env=pg_environment,
    )
    restore_environment = {**pg_environment, "PGDATABASE": restored}
    child(
        [
            str(pg_bin / "pg_restore.exe"),
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            restored,
            str(args.dump),
        ],
        cwd=backend,
        env=restore_environment,
    )
    restored_engine = create_engine(source.set(database=restored), pool_pre_ping=True)
    with restored_engine.connect() as connection:
        restored_revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        restored_invalid_indexes = connection.execute(
            text("SELECT count(*) FROM pg_index WHERE NOT indisvalid")
        ).scalar_one()
        restored_tables = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=ANY(:names)"
            ),
            {"names": list(TABLES)},
        ).scalar_one()
    engine.dispose()
    restored_engine.dispose()
    admin.dispose()
    if restored_revision != HEAD or restored_invalid_indexes or restored_tables != len(TABLES):
        raise RuntimeError("TASK221_RESTORED_SCHEMA_INTEGRITY_FAILED")

    result = {
        "status": "PASS",
        "database": database,
        "restore_database": restored,
        "head": revision,
        "restored_head": restored_revision,
        "alembic_commands": commands,
        "empty_downgrade_upgrade_rehearsed": True,
        "metadata_columns_match": True,
        "invalid_indexes": invalid_indexes,
        "invalid_constraints": invalid_constraints,
        "required_indexes": sorted(REQUIRED_INDEXES),
        "required_constraints": sorted(REQUIRED_CONSTRAINTS),
        "non_4326_geometry_columns": postgis_srid,
        "dump_sha256": sha256(args.dump),
        "pg_restore_list_entries": len(listing["stdout"].splitlines()),
        "restore_table_count": restored_tables,
        "restore_invalid_indexes": restored_invalid_indexes,
        "database_urls_included": False,
        "databases_retained": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "status",
                    "database",
                    "restore_database",
                    "head",
                    "restored_head",
                    "dump_sha256",
                )
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
