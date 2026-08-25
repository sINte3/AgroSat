#!/usr/bin/env python3
"""Fail-closed isolated PostgreSQL qualification for TASK_219."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


SAFE_DATABASE = re.compile(r"^agrosat_r3_task219_[a-z0-9_]+$")
EXPECTED_HEAD = "0014_autonomous_satellite_monitoring"


def assert_safe_database(name: str) -> str:
    if name == "agrosat" or not SAFE_DATABASE.fullmatch(name):
        raise RuntimeError("database name is outside the TASK_219 isolated prefix")
    return name


def quoted_identifier(value: str) -> str:
    assert_safe_database(value)
    return '"' + value.replace('"', '""') + '"'


def sanitized(value: str) -> str:
    value = re.sub(r"(?i)(postgres(?:ql)?://)[^\s]+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(password|token|secret|authorization)=\S+", r"\1=[REDACTED]", value)
    return value[-12000:]


def run_alembic(backend: Path, target_url, *arguments: str) -> dict:
    env = os.environ.copy()
    env["DATABASE_URL"] = target_url.render_as_string(hide_password=False)
    env.pop("AGROSAT_RUNTIME_ENV_FILE", None)
    process = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=backend, env=env, capture_output=True, text=True, timeout=900, check=False,
    )
    result = {"arguments": list(arguments), "exit_code": process.returncode,
              "stdout": sanitized(process.stdout), "stderr": sanitized(process.stderr)}
    if process.returncode != 0:
        raise RuntimeError(f"alembic {' '.join(arguments)} failed: {result['stderr']}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--runtime-env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--drop-existing", action="store_true")
    args = parser.parse_args()
    database = assert_safe_database(args.database)
    backend = args.backend.resolve(strict=True)
    runtime_env = args.runtime_env.resolve(strict=True)
    os.environ["AGROSAT_RUNTIME_ENV_FILE"] = str(runtime_env)
    sys.path.insert(0, str(backend))
    from config import settings

    source = make_url(settings.database_url)
    if not source.database:
        raise RuntimeError("approved runtime configuration has no database identity")
    admin = source.set(database="postgres")
    target = source.set(database=database)
    admin_engine = create_engine(admin, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin_engine.connect() as connection:
        exists = connection.execute(text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": database}).scalar()
        if exists and not args.drop_existing:
            raise RuntimeError("isolated database already exists")
        if exists:
            assert_safe_database(database)
            connection.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name AND pid<>pg_backend_pid()"), {"name": database})
            connection.exec_driver_sql(f"DROP DATABASE {quoted_identifier(database)}")
        assert_safe_database(database)
        connection.exec_driver_sql(f"CREATE DATABASE {quoted_identifier(database)}")
    admin_engine.dispose()

    engine = create_engine(target, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    commands = [run_alembic(backend, target, "upgrade", "head")]
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        extensions = [row[0] for row in connection.execute(text("SELECT extname FROM pg_extension ORDER BY extname"))]
        tables = connection.execute(text("""
          SELECT count(*) FROM information_schema.tables WHERE table_schema='public'
            AND table_name IN ('monitoring_rule_versions','satellite_collection_runs',
              'satellite_field_freshness','autonomous_anomaly_candidates','autonomous_anomaly_transitions')
        """)).scalar_one()
        invalid_indexes = connection.execute(text("SELECT count(*) FROM pg_index WHERE NOT indisvalid")).scalar_one()
    if revision != EXPECTED_HEAD or tables != 5 or "postgis" not in extensions or invalid_indexes:
        raise RuntimeError("fresh upgrade integrity contract failed")
    commands.append(run_alembic(backend, target, "check"))
    commands.append(run_alembic(backend, target, "downgrade", "0013_anomaly_inspection_workflow"))
    with engine.connect() as connection:
        revision_after_down = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        remaining = connection.execute(text("""
          SELECT count(*) FROM information_schema.tables WHERE table_schema='public'
            AND table_name LIKE 'autonomous_%' OR table_schema='public' AND table_name IN
              ('monitoring_rule_versions','satellite_collection_runs','satellite_field_freshness')
        """)).scalar_one()
    if revision_after_down != "0013_anomaly_inspection_workflow" or remaining:
        raise RuntimeError("downgrade removal contract failed")
    commands.append(run_alembic(backend, target, "upgrade", "head"))
    with engine.connect() as connection:
        final_revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        rule = connection.execute(text("SELECT version,configuration_hash,is_active FROM monitoring_rule_versions")).mappings().one()
    engine.dispose()
    result = {
        "status": "PASS", "database": database, "database_url_included": False,
        "runtime_environment_included": False, "fresh_revision": revision,
        "downgrade_revision": revision_after_down, "final_revision": final_revision,
        "postgis": True, "invalid_indexes": invalid_indexes, "new_table_count": tables,
        "rule": dict(rule), "commands": commands, "database_retained": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status","database","fresh_revision","downgrade_revision","final_revision")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
