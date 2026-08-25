"""Drop one explicitly named TASK_217 qualification database safely."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import dotenv_values
import psycopg2
from psycopg2 import sql
from sqlalchemy.engine import make_url


TARGET_PREFIX = "agrosat_r3_task217_"
SOURCE_PREFIX = "agrosat_r3_d_pixel_ndvi_"


def assert_target(name: str) -> None:
    if name == "agrosat" or not name.startswith(TARGET_PREFIX):
        raise RuntimeError("TASK217_DATABASE_IDENTITY_REJECTED")
    suffix = name.removeprefix(TARGET_PREFIX)
    if not suffix or not suffix.replace("_", "").isalnum():
        raise RuntimeError("TASK217_DATABASE_IDENTITY_REJECTED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-runtime-env", type=Path, required=True)
    parser.add_argument("--database-name", required=True)
    args = parser.parse_args()

    assert_target(args.database_name)
    values = {
        key: str(value)
        for key, value in dotenv_values(args.source_runtime_env.resolve(strict=True)).items()
        if value is not None
    }
    source = make_url(values.get("DATABASE_URL") or values.get("SUPABASE_DATABASE_URL") or "")
    if source.database == "agrosat" or not str(source.database or "").startswith(SOURCE_PREFIX):
        raise RuntimeError("TASK217_SOURCE_DATABASE_IDENTITY_REJECTED")

    admin_url = source.set(database="postgres").render_as_string(hide_password=False)
    dropped = False
    connection = psycopg2.connect(admin_url)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", (args.database_name,))
            if cursor.fetchone() is not None:
                assert_target(args.database_name)
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=%s AND pid<>pg_backend_pid()",
                    (args.database_name,),
                )
                assert_target(args.database_name)
                cursor.execute(
                    sql.SQL("DROP DATABASE {}").format(sql.Identifier(args.database_name))
                )
                dropped = True
    finally:
        connection.close()

    values.clear()
    print(json.dumps({
        "result": "PASS",
        "database": args.database_name,
        "database_prefix_valid": True,
        "database_is_production": False,
        "dropped": dropped,
        "secret_values_logged": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
