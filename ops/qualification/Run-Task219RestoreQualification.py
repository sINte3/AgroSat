#!/usr/bin/env python3
"""Restore the accepted production-like backup into a safe TASK_219 database."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

SAFE_DATABASE = re.compile(r"^agrosat_r3_task219_[a-z0-9_]+$")


def assert_safe_database(name: str) -> str:
    if name == "agrosat" or not SAFE_DATABASE.fullmatch(name):
        raise RuntimeError("database name is outside the TASK_219 isolated prefix")
    return name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--runtime-env", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--drop-existing", action="store_true")
    args = parser.parse_args()
    database = assert_safe_database(args.database)
    backup = args.backup.resolve(strict=True)
    actual_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    if actual_hash != args.expected_sha256.lower():
        raise RuntimeError("backup SHA-256 mismatch")
    os.environ["AGROSAT_RUNTIME_ENV_FILE"] = str(args.runtime_env.resolve(strict=True))
    backend = args.backend.resolve(strict=True)
    sys.path.insert(0, str(backend))
    from config import settings
    source = make_url(settings.database_url)
    target = source.set(database=database)
    admin = source.set(database="postgres")
    admin_engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        exists = connection.execute(text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name":database}).scalar()
        if exists and not args.drop_existing:
            raise RuntimeError("isolated restore database already exists")
        if exists:
            assert_safe_database(database)
            connection.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name AND pid<>pg_backend_pid()"), {"name":database})
            connection.exec_driver_sql(f'DROP DATABASE "{database}"')
        assert_safe_database(database)
        connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
    admin_engine.dispose()
    pg_env = os.environ.copy()
    pg_env.update({"PGHOST": source.host or "localhost", "PGPORT": str(source.port or 5432),
                   "PGUSER": source.username or "", "PGPASSWORD": source.password or "", "PGDATABASE": database})
    if source.query.get("sslmode"):
        pg_env["PGSSLMODE"] = source.query["sslmode"]
    pg_restore = args.pg_bin.resolve(strict=True) / "pg_restore.exe"
    listing = subprocess.run([str(pg_restore), "--list", str(backup)], env=pg_env, capture_output=True, text=True, timeout=120)
    if listing.returncode:
        raise RuntimeError("pg_restore --list failed")
    restore = subprocess.run([str(pg_restore), "--exit-on-error", "--no-owner", "--no-privileges", "--dbname", database, str(backup)], env=pg_env, capture_output=True, text=True, timeout=900)
    if restore.returncode:
        raise RuntimeError("pg_restore failed: " + re.sub(r"(?i)password=\S+", "password=[REDACTED]", restore.stderr[-4000:]))
    engine = create_engine(target)
    tables = {"enterprises":"enterprises","fields":"fields","users":"users","ndvi_records":"ndvi_records","alerts":"alerts","field_inspections":"field_inspections"}
    with engine.connect() as connection:
        before_revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        counts_before = {key: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() for key,table in tables.items()}
        orphan_fields = connection.execute(text("SELECT count(*) FROM fields f LEFT JOIN enterprises e ON e.id=f.enterprise_id WHERE e.id IS NULL")).scalar_one()
        orphan_users = connection.execute(text("SELECT count(*) FROM users u LEFT JOIN enterprises e ON e.id=u.enterprise_id WHERE u.enterprise_id IS NOT NULL AND e.id IS NULL")).scalar_one()
    if before_revision not in {"0012_commercial_tenant_boundary", "0013_anomaly_inspection_workflow"} or orphan_fields or orphan_users:
        raise RuntimeError("restored baseline integrity failed")
    child_env = os.environ.copy()
    child_env["DATABASE_URL"] = target.render_as_string(hide_password=False)
    child_env.pop("AGROSAT_RUNTIME_ENV_FILE", None)
    intermediate_revision = before_revision
    if before_revision == "0012_commercial_tenant_boundary":
        intermediate = subprocess.run([sys.executable,"-m","alembic","-c","alembic.ini","upgrade","0013_anomaly_inspection_workflow"], cwd=backend, env=child_env, capture_output=True, text=True, timeout=900)
        if intermediate.returncode:
            raise RuntimeError("restore intermediate upgrade to 0013 failed")
        with engine.connect() as connection:
            intermediate_revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            intermediate_counts = {key: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() for key,table in tables.items()}
        if intermediate_revision != "0013_anomaly_inspection_workflow" or intermediate_counts != counts_before:
            raise RuntimeError("restore intermediate 0013 reconciliation failed")
    upgrade = subprocess.run([sys.executable,"-m","alembic","-c","alembic.ini","upgrade","head"], cwd=backend, env=child_env, capture_output=True, text=True, timeout=900)
    if upgrade.returncode:
        raise RuntimeError("restore upgrade failed")
    with engine.connect() as connection:
        after_revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        counts_after = {key: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() for key,table in tables.items()}
        new_tables = connection.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('monitoring_rule_versions','satellite_collection_runs','satellite_field_freshness','autonomous_anomaly_candidates','autonomous_anomaly_transitions')")).scalar_one()
        invalid_indexes = connection.execute(text("SELECT count(*) FROM pg_index WHERE NOT indisvalid")).scalar_one()
    engine.dispose()
    if after_revision != "0014_autonomous_satellite_monitoring" or counts_before != counts_after or new_tables != 5 or invalid_indexes:
        raise RuntimeError("upgraded restore reconciliation failed")
    result = {"status":"PASS","database":database,"backup_sha256":actual_hash,"pg_restore_list":True,
              "revision_restored":before_revision,"revision_before":intermediate_revision,"revision_after":after_revision,"counts_before":counts_before,
              "counts_after":counts_after,"tenant_orphans":{"fields":orphan_fields,"users":orphan_users},
              "new_table_count":new_tables,"invalid_indexes":invalid_indexes,"database_url_included":False,
              "database_retained":True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"status":"PASS","database":database,"revision_before":before_revision,"revision_after":after_revision,"counts":counts_after}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
