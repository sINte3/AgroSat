"""Run a TASK_219 child with approved credentials kept in process memory."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess

from dotenv import dotenv_values
from sqlalchemy.engine import make_url


PREFIX = "agrosat_r3_task219_"
SENSITIVE = re.compile(r"(?i)(secret|token|password|api.?key|database.?url|authorization|cookie|pgpass)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-runtime-env", type=Path, required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.database_name == "agrosat" or not args.database_name.startswith(PREFIX):
        raise RuntimeError("TASK219_DATABASE_IDENTITY_REJECTED")
    if not args.command:
        raise RuntimeError("TASK219_CHILD_COMMAND_MISSING")
    values = {key: str(value) for key, value in dotenv_values(args.source_runtime_env.resolve(strict=True)).items() if value is not None}
    source = make_url(values.get("DATABASE_URL") or values.get("SUPABASE_DATABASE_URL") or "")
    if source.database != "agrosat":
        raise RuntimeError("TASK219_SOURCE_DATABASE_IDENTITY_REJECTED")
    environment = {key: value for key, value in os.environ.items() if not SENSITIVE.search(key)}
    environment.update(values)
    environment.update({
        "DATABASE_URL": source.set(database=args.database_name).render_as_string(hide_password=False),
        "AGROSAT_RUNTIME_ENV_FILE": str(args.source_runtime_env.resolve(strict=True)),
        "ENVIRONMENT": "development",
        "DEBUG": "false", "PUBLIC_REGISTRATION_ENABLED": "false",
        "WIALON_ENABLED": "false", "TELEGRAM_NOTIFICATIONS_ENABLED": "false",
        "INSPECTION_MEDIA_DIRECTORY": str(args.media_root.resolve()),
        "PIXEL_NDVI_CACHE_DIRECTORY": str(args.cache_root.resolve()),
        "RELEASE_REVISION": os.environ.get("AGROSAT_RELEASE_COMMIT", "f" * 40),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(args.cwd.resolve(strict=True)),
    })
    completed = subprocess.run(args.command, cwd=args.cwd.resolve(strict=True), env=environment, stdin=subprocess.DEVNULL)
    values.clear(); environment.clear()
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
