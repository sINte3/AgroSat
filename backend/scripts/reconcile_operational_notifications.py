#!/usr/bin/env python3
"""Run one bounded Operational Center notification reconciliation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid
from typing import Any


BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from database import SessionLocal
from services.operational_notifications import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    acquire_advisory_lock,
    reconcile_notifications,
)


LATEST_STATUS = "operational_notifications_latest_status.json"
HEARTBEAT = "operational_notifications_heartbeat.json"
HEX_SHA = re.compile(r"^[0-9a-f]{40}$")


class ContractError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sanitize_text(value: Any, limit: int = 1000) -> str:
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


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def external_directory(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise ContractError("--output-dir must be absolute")
    resolved = path.resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return resolved
    raise ContractError("--output-dir must be outside the repository")


def release_commit() -> str:
    value = os.environ.get("AGROSAT_RELEASE_COMMIT", "unknown").strip().lower()
    return value if HEX_SHA.fullmatch(value) else "unknown"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Plan only; no business write.")
    mode.add_argument("--apply", action="store_true", help="Apply idempotent notification writes.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    started = time.monotonic()
    run_id = uuid.uuid4().hex
    mode = "apply" if args.apply else "dry-run"
    summary: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "mode": mode,
        "status": "running",
        "release_commit": release_commit(),
        "started_at": utc_now(),
        "finished_at": None,
        "exit_code": None,
        "failure_category": None,
        "reconciliation": None,
    }
    db = None
    locked = False
    output_dir: Path | None = None
    exit_code = 4
    try:
        if type(args.limit) is not int or not 1 <= args.limit <= MAX_LIMIT:
            raise ContractError(f"--limit must be between 1 and {MAX_LIMIT}")
        output_dir = external_directory(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(output_dir / LATEST_STATUS, summary)
        atomic_json(output_dir / HEARTBEAT, {
            "schema_version": 1,
            "run_id": run_id,
            "status": "running",
            "release_commit": summary["release_commit"],
            "heartbeat_at": utc_now(),
        })
        db = SessionLocal()
        locked = acquire_advisory_lock(db)
        if not locked:
            exit_code = 3
            summary["failure_category"] = "lock_contention"
        else:
            summary["reconciliation"] = reconcile_notifications(
                db, apply=args.apply, limit=args.limit
            )
            exit_code = 0
    except ContractError as exc:
        exit_code = 2
        summary["failure_category"] = "contract"
        summary["diagnostic"] = sanitize_text(exc)
    except Exception as exc:
        exit_code = 4
        summary["failure_category"] = "operational"
        summary["diagnostic"] = sanitize_text(exc)
    finally:
        if db is not None:
            db.close()
        summary.update({
            "status": "succeeded" if exit_code == 0 else "skipped" if exit_code == 3 else "failed",
            "finished_at": utc_now(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "exit_code": exit_code,
        })
        if output_dir is not None:
            try:
                atomic_json(output_dir / LATEST_STATUS, summary)
                atomic_json(output_dir / HEARTBEAT, {
                    "schema_version": 1,
                    "run_id": run_id,
                    "status": summary["status"],
                    "release_commit": summary["release_commit"],
                    "heartbeat_at": utc_now(),
                })
            except Exception as exc:
                exit_code = 4
                summary["status"] = "failed"
                summary["exit_code"] = exit_code
                summary["failure_category"] = "status_persistence"
                summary["diagnostic"] = sanitize_text(exc)
    return exit_code, summary


def main(argv: list[str] | None = None) -> None:
    code, summary = run(parse_args(argv))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
