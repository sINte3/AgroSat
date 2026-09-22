#!/usr/bin/env python3
"""Recompute satellite freshness from already-persisted observations.

After the H0-A accepted-observation contract repair, ``satellite_field_freshness``
still holds the statuses that the previous, NULL-hostile predicate produced.
This command recomputes those derived rows from observations that are already
in the database.

What it does NOT do:

* it never contacts a satellite provider and imports no provider module;
* it never recollects or rewrites an observation;
* it writes nothing outside ``satellite_field_freshness``, which is derived
  state that the collector already rewrites on every cycle;
* it installs no scheduler and no Scheduled Task.

It is a dry run unless ``--apply`` is passed. Both modes share one SQL
definition with the collector's own freshness refresh, so the preview cannot
disagree with the write.

Safety: ``--apply`` takes the collector's PostgreSQL advisory lock, so this
command and a running collection cycle are mutually exclusive.

Exit codes: 0 ok, 2 contract/validation error, 3 lock contention,
4 operational error.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from typing import Any


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import text

from config import settings
from database import SessionLocal
from services.autonomous_monitoring import (
    ADVISORY_LOCK_KEY,
    ApplyRun,
    freshness_preview,
    refresh_freshness,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sanitize_text(value: Any, limit: int = 1000) -> str:
    """Strip anything credential-shaped out of an operator-facing message."""
    result = str(value)[:limit]
    result = re.sub(
        r"(?i)\b(token|password|secret|api[_-]?key|authorization)\s*[:=]\s*[^\s,]+",
        r"\1=[REDACTED]",
        result,
    )
    result = re.sub(
        r"(?i)\b(?:postgres(?:ql)?|redis|https?)://[^\s@]+@[^\s]+",
        "[REDACTED_URL]",
        result,
    )
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute satellite_field_freshness from persisted observations. "
            "Dry run unless --apply is given."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change and write nothing (default)",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="rewrite derived freshness rows",
    )
    return parser.parse_args(argv)


def _status_counts(session) -> dict[str, int]:
    rows = session.execute(
        text(
            "SELECT status, count(*)::bigint AS row_count "
            "FROM satellite_field_freshness GROUP BY 1 ORDER BY 1"
        )
    ).all()
    return {str(row[0]): int(row[1]) for row in rows}


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    started = time.monotonic()
    apply_mode = bool(args.apply)
    summary: dict[str, Any] = {
        "schema_version": 1,
        "command": "recompute_satellite_freshness",
        "mode": "apply" if apply_mode else "dry-run",
        "started_at": utc_now(),
        "status": "failed",
        "exit_code": 4,
        "provider_calls": 0,
        "observations_written": 0,
    }

    if not str(settings.database_url or "").strip():
        summary.update(
            status="failed",
            exit_code=2,
            failure_category="configuration",
            diagnostic="DATABASE_URL is not configured",
        )
        return 2, summary

    session = SessionLocal()
    try:
        summary["freshness_before"] = _status_counts(session)
        transitions = freshness_preview(session)
        summary["transitions"] = [
            {
                "computed_status": row["computed_status"],
                "stored_status": row["stored_status"],
                "row_count": int(row["row_count"]),
            }
            for row in transitions
        ]
        summary["rows_evaluated"] = sum(
            item["row_count"] for item in summary["transitions"]
        )
        summary["rows_changing"] = sum(
            item["row_count"]
            for item in summary["transitions"]
            if item["computed_status"] != item["stored_status"]
        )

        if apply_mode:
            # Freshness is owned by the collector during a cycle. Refuse while a
            # cycle is genuinely in flight.
            in_flight = session.execute(
                text(
                    "SELECT count(*) FROM satellite_collection_runs "
                    "WHERE status = 'running' "
                    "AND heartbeat_at > now() - interval '6 hours'"
                )
            ).scalar_one()
            if in_flight:
                summary.update(
                    status="lock_contended",
                    exit_code=3,
                    failure_category="lock_contention",
                    diagnostic="a satellite collection cycle is currently running",
                )
                return 3, summary

            # Transaction-scoped lock, taken in the same transaction as the
            # write. A session-scoped lock would be unreliable here because
            # SQLAlchemy returns the connection to the pool on commit.
            session.rollback()
            locked = bool(
                session.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": ADVISORY_LOCK_KEY},
                ).scalar()
            )
            if not locked:
                session.rollback()
                summary.update(
                    status="lock_contended",
                    exit_code=3,
                    failure_category="lock_contention",
                    diagnostic="another freshness writer holds the advisory lock",
                )
                return 3, summary

            # refresh_freshness writes and commits inside this transaction, so
            # the lock covers the write and is released with it.
            written = refresh_freshness(
                ApplyRun(session=session, run_id=None, run_key="freshness-reconcile"),
                last_outcome=None,
            )
            summary["observations_written"] = int(written)
            summary["freshness_after"] = _status_counts(session)
        else:
            session.rollback()

        summary.update(status="succeeded", exit_code=0)
        return 0, summary
    except Exception as exc:
        try:
            session.rollback()
        except Exception:
            pass
        summary.update(
            status="failed",
            exit_code=4,
            failure_category="operational",
            diagnostic=sanitize_text(exc),
        )
        return 4, summary
    finally:
        session.close()
        summary["finished_at"] = utc_now()
        summary["duration_seconds"] = round(time.monotonic() - started, 3)


def main(argv: list[str] | None = None) -> None:
    code, summary = run(parse_args(argv))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
