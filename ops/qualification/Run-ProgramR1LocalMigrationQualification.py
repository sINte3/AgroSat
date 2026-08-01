#!/usr/bin/env python3
"""Qualify the local-transfer Alembic boundary against a disposable database."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse


DATABASE_PREFIX = "agrosat_r1_local_migration_"


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-env", required=True)
    parser.add_argument("--evidence-path", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-postgres-port", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    worktree = Path(__file__).resolve().parents[2]
    backend = worktree / "backend"
    runtime_env = Path(args.runtime_env).resolve(strict=True)
    evidence_path = Path(args.evidence_path).resolve()
    if not str(evidence_path).lower().startswith(
        "c:\\agrosat_backups\\program_r1_completion_run\\"
    ):
        raise RuntimeError("unexpected evidence path")

    os.environ["AGROSAT_RUNTIME_ENV_FILE"] = str(runtime_env)
    sys.path.insert(0, str(backend))

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool

    from config import settings
    from scripts import migrate_to_local

    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=worktree, text=True
    ).strip()
    if head != args.expected_head:
        raise RuntimeError("unexpected worktree HEAD")
    parsed = urlparse(settings.database_url)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("database target is not loopback")
    if parsed.port != args.expected_postgres_port:
        raise RuntimeError("database target port is unexpected")

    database_name = f"{DATABASE_PREFIX}{os.getpid()}"
    if not re.fullmatch(r"agrosat_r1_local_migration_[0-9]+", database_name):
        raise RuntimeError("unsafe disposable database name")
    source_url = make_url(settings.database_url)
    admin_url = source_url.set(database="postgres")
    candidate_url = source_url.set(database=database_name)
    admin_engine = create_engine(
        admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    candidate_engine = None
    created = False
    cleaned = False
    measurements: dict[str, Any] = {}
    failure_class: str | None = None
    try:
        with admin_engine.connect() as connection:
            existing = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname=:name"),
                {"name": database_name},
            ).scalar_one_or_none()
            if existing is not None:
                raise RuntimeError("disposable database unexpectedly exists")
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
            created = True

        migrate_to_local.init_local_schema(candidate_url.render_as_string(hide_password=False))
        candidate_engine = create_engine(candidate_url, poolclass=NullPool)
        with candidate_engine.connect() as connection:
            measurements = {
                "alembicRevision": connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one(),
                "postgisInstalled": bool(
                    connection.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_extension "
                            "WHERE extname='postgis')"
                        )
                    ).scalar_one()
                ),
                "applicationTableCount": int(
                    connection.execute(
                        text(
                            "SELECT count(*) FROM information_schema.tables "
                            "WHERE table_schema='public' "
                            "AND table_type='BASE TABLE' "
                            "AND table_name <> 'spatial_ref_sys'"
                        )
                    ).scalar_one()
                ),
                "invalidConstraintCount": int(
                    connection.execute(
                        text(
                            "SELECT count(*) FROM pg_constraint "
                            "WHERE connamespace='public'::regnamespace AND NOT convalidated"
                        )
                    ).scalar_one()
                ),
                "invalidIndexCount": int(
                    connection.execute(
                        text(
                            "SELECT count(*) FROM pg_index i "
                            "JOIN pg_class c ON c.oid=i.indexrelid "
                            "JOIN pg_namespace n ON n.oid=c.relnamespace "
                            "WHERE n.nspname='public' AND NOT i.indisvalid"
                        )
                    ).scalar_one()
                ),
            }
    except BaseException as error:  # cleanup must include the tool's SystemExit path
        failure_class = type(error).__name__
    finally:
        if candidate_engine is not None:
            candidate_engine.dispose()
        if created:
            with admin_engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=:name AND pid <> pg_backend_pid()"
                    ),
                    {"name": database_name},
                )
                connection.execute(text(f'DROP DATABASE "{database_name}"'))
                cleaned = True
        admin_engine.dispose()

    status = (
        "PASS"
        if failure_class is None
        and measurements.get("postgisInstalled") is True
        and measurements.get("applicationTableCount", 0) > 0
        and measurements.get("invalidConstraintCount") == 0
        and measurements.get("invalidIndexCount") == 0
        and cleaned
        else "FAIL"
    )
    result = {
        "schemaVersion": 1,
        "gate": "GATE5",
        "head": head,
        "operation": "local_transfer_canonical_alembic_chain",
        "runtimeClass": "disposable_loopback_postgresql_postgis",
        "postgresqlPort": args.expected_postgres_port,
        "databaseNameClass": DATABASE_PREFIX + "<process_id>",
        "databaseCreated": created,
        "databaseCleaned": cleaned,
        "measurements": measurements,
        "failureClassification": failure_class,
        "credentialsIncluded": False,
        "databaseUrlIncluded": False,
        "productionWrites": 0,
        "status": status,
        "marker": (
            "PASS_GATE5_LOCAL_TRANSFER_ALEMBIC_BOUNDARY"
            if status == "PASS"
            else "FAIL_GATE5_LOCAL_TRANSFER_ALEMBIC_BOUNDARY"
        ),
    }
    atomic_json(evidence_path, result)
    print(json.dumps({"status": status, "head": head, "cleaned": cleaned}))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
