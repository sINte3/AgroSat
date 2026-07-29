#!/usr/bin/env python3
"""Bounded standalone productivity-zone CLI; dry-run is the default."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
from threading import Event
from typing import Any
import uuid
from uuid import UUID


BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.collector_locking import acquire_lock, release_lock
from services.productivity_zone_algorithm import (
    YieldMeasurement,
    analyze_productivity,
    result_payload,
)
from services.productivity_zones import compute_field


SCHEMA_VERSION = 1
MAX_FIELDS = 100
MAX_SEASONS = 5
MUTEX_NAME = "Global\\AgroSatProductivityZones_v1"


class CliContractError(ValueError):
    """Invalid bounded productivity-zone invocation."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sanitize_text(value: Any, limit: int = 1000) -> str:
    output = str(value)[:limit]
    output = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,]+",
        r"\1[REDACTED]",
        output,
    )
    output = re.sub(
        r"(?i)\b(token|password|secret|api[_-]?key)\s*[:=]\s*[^\s,]+",
        r"\1=[REDACTED]",
        output,
    )
    output = re.sub(
        r"(?i)\b(?:postgres(?:ql)?|redis|https?)://[^\s@]+@[^\s]+",
        "[REDACTED_URL]",
        output,
    )
    return output


def external_path(raw: str | None, label: str, *, required=False):
    if not raw:
        if required:
            raise CliContractError(f"{label} is required")
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise CliContractError(f"{label} must be an absolute path")
    resolved = path.resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return resolved
    raise CliContractError(f"{label} must be outside the repository")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}",
        suffix=".tmp",
        dir=path.parent,
    )
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


def _checkpoint(path: Path | None):
    if path is None or not path.exists():
        return {"schema_version": SCHEMA_VERSION, "completed": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliContractError("checkpoint is unreadable") from exc
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or not isinstance(value.get("completed"), dict)
    ):
        raise CliContractError("checkpoint schema is unsupported")
    return value


def load_fixture(path: Path, maximum: int):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_fields = payload["fields"]
    except (OSError, KeyError, json.JSONDecodeError, TypeError) as exc:
        raise CliContractError("invalid fixture") from exc
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise CliContractError("fixture schema is unsupported")
    if not isinstance(raw_fields, list) or not 1 <= len(raw_fields) <= maximum:
        raise CliContractError("fixture exceeds bounded field batch")
    loaded = []
    try:
        for item in raw_fields:
            enterprise_id = int(item["enterprise_id"])
            field_id = int(item["field_id"])
            if enterprise_id <= 0 or field_id <= 0:
                raise ValueError
            measurements = [
                YieldMeasurement(
                    import_id=int(row["import_id"]),
                    source_sha256=str(row["source_sha256"]),
                    season_year=int(row["season_year"]),
                    longitude=float(row["longitude"]),
                    latitude=float(row["latitude"]),
                    yield_t_ha=float(row["yield_t_ha"]),
                )
                for row in item["measurements"]
            ]
            loaded.append((enterprise_id, field_id, measurements))
    except (KeyError, TypeError, ValueError) as exc:
        raise CliContractError("fixture contains invalid measured yield") from exc
    identities = [(enterprise, field) for enterprise, field, _ in loaded]
    if len(set(identities)) != len(identities):
        raise CliContractError("fixture contains duplicate fields")
    return loaded


def execute(args, *, cancel_event: Event, session_factory=None):
    if not 1 <= args.max_fields <= MAX_FIELDS:
        raise CliContractError("--max-fields must be between 1 and 100")
    seasons = tuple(sorted(set(args.season or ())))
    if len(seasons) > MAX_SEASONS:
        raise CliContractError("at most five --season values are supported")
    if any(not 2000 <= value <= 2200 for value in seasons):
        raise CliContractError("--season is outside 2000..2200")
    fixture_path = external_path(args.fixture, "--fixture")
    checkpoint_path = external_path(
        args.checkpoint,
        "--checkpoint",
        required=args.write or args.resume,
    )
    log_path = external_path(args.log_file, "--log-file", required=args.write)
    if args.resume and checkpoint_path is None:
        raise CliContractError("--resume requires --checkpoint")
    if fixture_path and args.write:
        raise CliContractError("fixture input is rejected with --write")
    if fixture_path and args.field:
        raise CliContractError("--fixture and --field are mutually exclusive")
    if not fixture_path and not args.field:
        raise CliContractError("provide --fixture or one or more --field")
    if not fixture_path and (
        args.enterprise_id is None or args.enterprise_id <= 0
    ):
        raise CliContractError("--enterprise-id must be positive")
    if args.field and not 1 <= len(set(args.field)) <= args.max_fields:
        raise CliContractError("field batch exceeds --max-fields")

    checkpoint = _checkpoint(checkpoint_path if args.resume else None)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(uuid.uuid4()),
        "mode": "write" if args.write else "dry_run",
        "started_at": utc_now(),
        "finished_at": None,
        "processed": 0,
        "skipped_checkpoint": 0,
        "created_runs": 0,
        "replayed_runs": 0,
        "status_counts": {},
        "failure_categories": {},
        "exit_code": None,
    }
    statuses = Counter()
    failures = Counter()
    db = None
    if fixture_path:
        work = load_fixture(fixture_path, args.max_fields)
    else:
        if session_factory is None:
            from database import SessionLocal

            session_factory = SessionLocal
        db = session_factory()
        work = [
            (int(args.enterprise_id), int(field_id), None)
            for field_id in sorted(set(args.field))
        ]

    try:
        for enterprise_id, field_id, fixture_measurements in work:
            key = f"{enterprise_id}:{field_id}:{','.join(map(str, seasons))}"
            if cancel_event.is_set():
                failures["cancelled"] += 1
                break
            if args.resume and key in checkpoint["completed"]:
                summary["skipped_checkpoint"] += 1
                continue
            try:
                if fixture_measurements is not None:
                    result = analyze_productivity(field_id, fixture_measurements)
                    outcome = {
                        "created": False,
                        "run_key": result.run_key,
                        "status": result.status,
                        "result": result_payload(result),
                    }
                else:
                    outcome = compute_field(
                        db,
                        enterprise_id,
                        field_id,
                        seasons=seasons,
                        run_uuid=UUID(summary["run_id"]),
                        write=args.write,
                    )
                status = (
                    outcome["result"]["status"]
                    if "result" in outcome
                    else outcome["status"]
                )
                statuses[status] += 1
                summary["processed"] += 1
                if args.write:
                    summary[
                        "created_runs" if outcome["created"] else "replayed_runs"
                    ] += 1
                if checkpoint_path:
                    checkpoint["completed"][key] = {
                        "run_key": (
                            outcome["result"]["run_key"]
                            if "result" in outcome
                            else outcome["run_key"]
                        ),
                        "status": status,
                        "completed_at": utc_now(),
                    }
                    atomic_json(checkpoint_path, checkpoint)
            except KeyboardInterrupt:
                cancel_event.set()
                failures["cancelled"] += 1
                break
            except ValueError:
                failures["contract"] += 1
            except Exception:
                failures["database"] += 1
        summary["status_counts"] = dict(sorted(statuses.items()))
        summary["failure_categories"] = dict(sorted(failures.items()))
        summary["finished_at"] = utc_now()
        if cancel_event.is_set():
            exit_code = 130
        elif failures and not statuses:
            exit_code = 5
        elif failures:
            exit_code = 5
        else:
            exit_code = 0
        summary["exit_code"] = exit_code
        if log_path:
            atomic_json(log_path, summary)
        return exit_code, summary
    finally:
        if db is not None:
            db.close()


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--enterprise-id", type=int)
    value.add_argument("--field", type=int, action="append")
    value.add_argument("--season", type=int, action="append")
    value.add_argument("--fixture")
    value.add_argument("--write", action="store_true")
    value.add_argument("--max-fields", type=int, default=MAX_FIELDS)
    value.add_argument("--checkpoint")
    value.add_argument("--resume", action="store_true")
    value.add_argument("--log-file")
    value.add_argument("--lock-file")
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    cancel_event = Event()

    def cancel(_signum, _frame):
        cancel_event.set()

    signal.signal(signal.SIGINT, cancel)
    signal.signal(signal.SIGTERM, cancel)
    lock_path = None
    try:
        lock_file = external_path(args.lock_file, "--lock-file")
        lock_path = acquire_lock(
            str(lock_file) if lock_file else None,
            mutex_name=MUTEX_NAME,
        )
        exit_code, summary = execute(args, cancel_event=cancel_event)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return exit_code
    except CliContractError as exc:
        print(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "exit_code": 2,
            "failure_category": "contract",
            "detail": sanitize_text(exc),
        }, sort_keys=True), file=sys.stderr)
        return 2
    finally:
        if lock_path:
            release_lock(lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
