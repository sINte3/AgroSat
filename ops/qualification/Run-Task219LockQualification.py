"""Exercise TASK_219 PostgreSQL and filesystem overlap locks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from services.autonomous_monitoring import ADVISORY_LOCK_KEY
from services.collector_locking import acquire_lock, release_lock


PREFIX = "agrosat_r3_task219_"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock-file", type=Path, required=True)
    parser.add_argument("--probe-only", action="store_true")
    args = parser.parse_args()
    if not args.lock_file.is_absolute():
        raise RuntimeError("LOCK_PATH_MUST_BE_ABSOLUTE")
    url = make_url(os.environ["DATABASE_URL"])
    if url.database == "agrosat" or not (url.database or "").startswith(PREFIX):
        raise RuntimeError("TASK219_DATABASE_IDENTITY_REJECTED")
    if args.probe_only:
        held = acquire_lock(
            str(args.lock_file), mutex_name="Global\\AgroSatTask219LockQualification"
        )
        release_lock(held)
        return 0
    engine = create_engine(url)
    first = engine.connect()
    second = engine.connect()
    file_first = None
    try:
        pg_first = first.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
        ).scalar_one()
        pg_overlap = second.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
        ).scalar_one()
        first.execute(
            text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
        )
        pg_after_release = second.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
        ).scalar_one()
        second.execute(
            text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
        )
        file_first = acquire_lock(
            str(args.lock_file), mutex_name="Global\\AgroSatTask219LockQualification"
        )
        probe = [
            sys.executable, str(Path(__file__).resolve()),
            "--lock-file", str(args.lock_file), "--probe-only",
        ]
        file_overlap_code = subprocess.run(
            probe, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            check=False,
        ).returncode
        release_lock(file_first)
        file_first = None
        file_after_release_code = subprocess.run(
            probe, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            check=False,
        ).returncode
        passed = (
            bool(pg_first and not pg_overlap and pg_after_release)
            and file_overlap_code == 3
            and file_after_release_code == 0
        )
        print(json.dumps({
            "database_identity": url.database,
            "postgres_first_acquired": bool(pg_first),
            "postgres_overlap_acquired": bool(pg_overlap),
            "postgres_after_release_acquired": bool(pg_after_release),
            "filesystem_overlap_exit_code": file_overlap_code,
            "filesystem_after_release_exit_code": file_after_release_code,
            "filesystem_after_release_acquired": file_after_release_code == 0,
            "pass": passed,
        }, sort_keys=True))
        return 0 if passed else 1
    finally:
        if file_first is not None:
            release_lock(file_first)
        first.close()
        second.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
