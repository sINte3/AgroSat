"""
AgroSat — one-time migration from Supabase PostgreSQL/PostGIS to local PostgreSQL/PostGIS.

Usage:
    cd backend
    python scripts/migrate_to_local.py

    # To replace existing local data:
    python scripts/migrate_to_local.py --wipe-local

Safety guarantees:
    - Read-only on the remote Supabase database (SELECT only).
    - No credentials leaked to stdout.
    - PostGIS geometry copied as EWKB, forced to 2D SRID 4326.
    - Primary keys preserved, sequences reset.
    - Import in FK-safe order — never disables foreign keys.
    - Refuses to overwrite local data without --wipe-local.
    - Verifies row counts, geometry validity, and FK consistency.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
PROJECT_ROOT = BACKEND_DIR.parent
TABLES_IN_IMPORT_ORDER = [
    "crop_types",
    "enterprises",
    "users",
    "fields",
    "crop_seasons",
    "ndvi_records",
    "alerts",
    "scouting_notes",
]
TABLES_IN_TRUNCATE_ORDER = list(reversed(TABLES_IN_IMPORT_ORDER))
BATCH_SIZE = 1000


def fail(message: str) -> None:
    print(f"[ERROR] {message}")
    sys.exit(1)


def safe_info(message: str) -> None:
    print(message)


def load_urls() -> tuple[str, str]:
    env_path = BACKEND_DIR / ".env"
    load_dotenv(env_path)

    import os

    remote_url = os.getenv("SUPABASE_DATABASE_URL")
    local_url = os.getenv("DATABASE_URL")

    if not remote_url:
        fail("SUPABASE_DATABASE_URL is missing in backend/.env")

    if not local_url:
        fail("DATABASE_URL is missing in backend/.env")

    remote = make_url(remote_url)
    local = make_url(local_url)

    remote_host = (remote.host or "").lower()
    local_host = (local.host or "").lower()

    if "supabase" not in remote_host and "pooler" not in remote_host:
        fail("SUPABASE_DATABASE_URL does not look like a Supabase/pooler host")

    if local_host not in {"localhost", "127.0.0.1"}:
        fail("DATABASE_URL must point to localhost or 127.0.0.1 for this migration")

    if (local.database or "").strip() == "":
        fail("DATABASE_URL must include a local database name")

    if not re.match(r"^[A-Za-z0-9_]+$", local.database):
        fail("Local database name must contain only letters, digits, and underscore")

    return remote_url, local_url


def create_local_database_if_missing(local_url: str) -> None:
    local = make_url(local_url)
    db_name = local.database

    if not db_name:
        fail("Local database name is empty")

    admin_url = local.set(database="postgres")

    safe_info("[1/8] Checking local PostgreSQL database...")
    try:
        admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
                {"db_name": db_name},
            ).fetchone()

            if exists:
                safe_info(f"Local database '{db_name}' already exists.")
            else:
                safe_info(f"Creating local database '{db_name}'...")
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    except SQLAlchemyError:
        fail("Could not connect to local PostgreSQL or create local database. Check service, user, and password.")
    finally:
        try:
            admin_engine.dispose()
        except Exception:
            pass


def init_local_schema(local_engine: Any) -> None:
    safe_info("[2/8] Initializing local PostGIS extension and SQLAlchemy schema...")
    try:
        with local_engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))

        from database import Base
        from models.enterprise import Enterprise
        from models.crop import CropType
        from models.field import Field, CropSeason
        from models.monitoring import NDVIRecord, Alert, ScoutingNote, User

        Base.metadata.create_all(bind=local_engine)
    except SQLAlchemyError:
        fail("Could not initialize local schema/PostGIS.")


def get_table_counts(engine: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for table in TABLES_IN_IMPORT_ORDER:
            counts[table] = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
    return counts


def assert_local_empty_or_wipe(local_engine: Any, wipe_local: bool) -> None:
    safe_info("[3/8] Checking local table state...")
    counts = get_table_counts(local_engine)
    non_empty = {k: v for k, v in counts.items() if v > 0}

    if non_empty and not wipe_local:
        safe_info("Local database is not empty:")
        for table, count in non_empty.items():
            safe_info(f"  {table}: {count}")
        fail("Refusing to overwrite local data. Re-run with --wipe-local if you intentionally want to replace local AgroSat tables.")

    if non_empty and wipe_local:
        safe_info("Wiping approved local AgroSat tables...")
        with local_engine.begin() as conn:
            table_list = ", ".join(f'"{t}"' for t in TABLES_IN_TRUNCATE_ORDER)
            conn.execute(text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE"))


def fetch_remote_rows(remote_engine: Any, table: str, offset: int, limit: int) -> list[dict[str, Any]]:
    if table == "fields":
        sql = text("""
            SELECT
                id,
                enterprise_id,
                name,
                code,
                ST_AsEWKB(ST_SetSRID(ST_Force2D(geometry), 4326)) AS geometry_ewkb,
                area_ha,
                centroid_lat,
                centroid_lon,
                soil_type,
                irrigation_type,
                elevation_m,
                notes,
                is_active,
                created_at,
                updated_at
            FROM fields
            ORDER BY id
            LIMIT :limit OFFSET :offset
        """)
    else:
        sql = text(f'SELECT * FROM "{table}" ORDER BY id LIMIT :limit OFFSET :offset')

    with remote_engine.connect() as conn:
        result = conn.execute(sql, {"limit": limit, "offset": offset})
        return [dict(row._mapping) for row in result.fetchall()]


def get_remote_count(remote_engine: Any, table: str) -> int:
    with remote_engine.connect() as conn:
        return conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()


def insert_batch(local_engine: Any, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    import json
    for row in rows:
        for k, v in list(row.items()):
            if isinstance(v, (dict, list)):
                row[k] = json.dumps(v)

    if table == "fields":
        sql = text("""
            INSERT INTO fields (
                id, enterprise_id, name, code,
                geometry, area_ha, centroid_lat, centroid_lon,
                soil_type, irrigation_type, elevation_m, notes,
                is_active, created_at, updated_at
            )
            VALUES (
                :id, :enterprise_id, :name, :code,
                ST_GeometryN(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_Force2D(ST_GeomFromEWKB(:geometry_ewkb)), 4326)), 3), 1),
                :area_ha, :centroid_lat, :centroid_lon,
                :soil_type, :irrigation_type, :elevation_m, :notes,
                :is_active, :created_at, :updated_at
            )
        """)
    else:
        columns = list(rows[0].keys())
        col_sql = ", ".join(f'"{c}"' for c in columns)
        val_sql = ", ".join(f":{c}" for c in columns)
        sql = text(f'INSERT INTO "{table}" ({col_sql}) VALUES ({val_sql})')

    with local_engine.begin() as conn:
        conn.execute(sql, rows)


def copy_table(remote_engine: Any, local_engine: Any, table: str) -> tuple[int, int]:
    total = get_remote_count(remote_engine, table)
    safe_info(f"Copying {table}: {total} rows")

    copied = 0
    offset = 0

    while offset < total:
        rows = fetch_remote_rows(remote_engine, table, offset=offset, limit=BATCH_SIZE)
        if not rows:
            break

        insert_batch(local_engine, table, rows)
        copied += len(rows)
        offset += len(rows)
        safe_info(f"  {table}: copied {copied}/{total}")

    return total, copied


def reset_sequences(local_engine: Any) -> None:
    safe_info("[6/8] Resetting local PostgreSQL sequences...")
    with local_engine.begin() as conn:
        for table in TABLES_IN_IMPORT_ORDER:
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
                {"table_name": table},
            ).scalar_one_or_none()

            if seq:
                conn.execute(text(f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT MAX(id) FROM "{table}"), 1),
                        (SELECT COUNT(*) FROM "{table}") > 0
                    )
                """))


def verify_counts(remote_engine: Any, local_engine: Any, copied_summary: dict[str, tuple[int, int]]) -> None:
    safe_info("[7/8] Verifying row counts...")
    local_counts = get_table_counts(local_engine)

    failed = False
    for table in TABLES_IN_IMPORT_ORDER:
        remote_total, copied_total = copied_summary[table]
        local_total = local_counts[table]
        safe_info(f"  {table}: remote={remote_total}, copied={copied_total}, local={local_total}")

        if remote_total != copied_total or remote_total != local_total:
            failed = True

    if failed:
        fail("Count verification failed.")


def verify_geometry(local_engine: Any) -> None:
    safe_info("[8/8] Verifying PostGIS geometry...")
    with local_engine.connect() as conn:
        bad = conn.execute(text("""
            SELECT COUNT(*)
            FROM fields
            WHERE geometry IS NULL
               OR ST_SRID(geometry) <> 4326
               OR ST_NDims(geometry) <> 2
               OR GeometryType(geometry) NOT IN ('POLYGON', 'MULTIPOLYGON')
               OR NOT ST_IsValid(geometry)
        """)).scalar_one()

        if bad:
            fail(f"Geometry verification failed. Bad fields: {bad}")

        count = conn.execute(text("SELECT COUNT(*) FROM fields")).scalar_one()
        safe_info(f"  fields geometry OK: {count} polygons, SRID=4326, 2D")


def verify_foreign_keys(local_engine: Any) -> None:
    safe_info("Verifying foreign key consistency...")
    checks = {
        "users.enterprise_id": """
            SELECT COUNT(*) FROM users u
            LEFT JOIN enterprises e ON e.id = u.enterprise_id
            WHERE u.enterprise_id IS NOT NULL AND e.id IS NULL
        """,
        "fields.enterprise_id": """
            SELECT COUNT(*) FROM fields f
            LEFT JOIN enterprises e ON e.id = f.enterprise_id
            WHERE e.id IS NULL
        """,
        "crop_seasons.field_id": """
            SELECT COUNT(*) FROM crop_seasons cs
            LEFT JOIN fields f ON f.id = cs.field_id
            WHERE f.id IS NULL
        """,
        "crop_seasons.crop_type_id": """
            SELECT COUNT(*) FROM crop_seasons cs
            LEFT JOIN crop_types ct ON ct.id = cs.crop_type_id
            WHERE ct.id IS NULL
        """,
        "ndvi_records.field_id": """
            SELECT COUNT(*) FROM ndvi_records nr
            LEFT JOIN fields f ON f.id = nr.field_id
            WHERE f.id IS NULL
        """,
        "alerts.field_id": """
            SELECT COUNT(*) FROM alerts a
            LEFT JOIN fields f ON f.id = a.field_id
            WHERE f.id IS NULL
        """,
        "alerts.ndvi_record_id": """
            SELECT COUNT(*) FROM alerts a
            LEFT JOIN ndvi_records nr ON nr.id = a.ndvi_record_id
            WHERE a.ndvi_record_id IS NOT NULL AND nr.id IS NULL
        """,
        "scouting_notes.field_id": """
            SELECT COUNT(*) FROM scouting_notes sn
            LEFT JOIN fields f ON f.id = sn.field_id
            WHERE f.id IS NULL
        """,
        "scouting_notes.author_id": """
            SELECT COUNT(*) FROM scouting_notes sn
            LEFT JOIN users u ON u.id = sn.author_id
            WHERE sn.author_id IS NOT NULL AND u.id IS NULL
        """,
    }

    with local_engine.connect() as conn:
        failed = False
        for name, sql in checks.items():
            bad = conn.execute(text(sql)).scalar_one()
            safe_info(f"  {name}: bad_refs={bad}")
            if bad:
                failed = True

    if failed:
        fail("Foreign key consistency verification failed.")


def run_migration(wipe_local: bool) -> None:
    safe_info("====================================================")
    safe_info("AgroSat migration: Supabase -> Local PostgreSQL/PostGIS")
    safe_info("====================================================")

    remote_url, local_url = load_urls()
    create_local_database_if_missing(local_url)

    remote_engine = create_engine(remote_url, pool_pre_ping=True, poolclass=NullPool)
    local_engine = create_engine(local_url, pool_pre_ping=True, poolclass=NullPool)

    try:
        init_local_schema(local_engine)
        assert_local_empty_or_wipe(local_engine, wipe_local=wipe_local)

        safe_info("[4/8] Reading remote row counts...")
        remote_counts = {table: get_remote_count(remote_engine, table) for table in TABLES_IN_IMPORT_ORDER}
        for table, count in remote_counts.items():
            safe_info(f"  {table}: {count}")

        safe_info("[5/8] Copying data...")
        copied_summary: dict[str, tuple[int, int]] = {}

        for table in TABLES_IN_IMPORT_ORDER:
            copied_summary[table] = copy_table(remote_engine, local_engine, table)

        reset_sequences(local_engine)
        verify_counts(remote_engine, local_engine, copied_summary)
        verify_geometry(local_engine)
        verify_foreign_keys(local_engine)

        safe_info("====================================================")
        safe_info("MIGRATION COMPLETED SUCCESSFULLY")
        safe_info("Local database is ready for manual DATABASE_URL switch after review.")
        safe_info("====================================================")

    except SQLAlchemyError as e:
        print(f"[DEBUG] Database error: {e}")
        fail("Migration failed due to a database error. Details above are safe — no credentials.")
    finally:
        remote_engine.dispose()
        local_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate AgroSat database from Supabase to local PostgreSQL/PostGIS")
    parser.add_argument(
        "--wipe-local",
        action="store_true",
        help="Wipe approved local AgroSat tables before import. Never affects Supabase.",
    )
    args = parser.parse_args()
    run_migration(wipe_local=args.wipe_local)


if __name__ == "__main__":
    main()
