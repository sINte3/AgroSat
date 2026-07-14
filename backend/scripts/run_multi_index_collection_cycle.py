#!/usr/bin/env python3
"""Standalone, bounded rotating orchestrator for the hardened multi-index collector."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.collector_locking import acquire_lock, release_lock

DEFAULT_INDICES = ("savi", "evi", "ndmi", "ndre")
STATE_SCHEMA_VERSION = 1
MAX_BATCH_SIZE = 100
MAX_DRY_RUN_FIELDS = 25
MAX_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 300
DEFAULT_LOCK_FILE = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp") / "agrosat_multi_index_cycle.lock"
CYCLE_MUTEX_NAME = "Global\\AgroSatMultiIndexCollectionCycle_v1"


class CycleValidationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_field_ids(raw: str) -> list[int]:
    values: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError as exc:
            raise CycleValidationError("--field-ids must contain positive integers") from exc
        if value <= 0:
            raise CycleValidationError("--field-ids must contain positive integers")
        if value not in values:
            values.append(value)
    if not values:
        raise CycleValidationError("--field-ids must not be empty")
    return values


def parse_indices(raw: str | None) -> list[str]:
    parts = DEFAULT_INDICES if raw is None else tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    if not parts:
        raise CycleValidationError("--indices must not be empty")
    result: list[str] = []
    for code in parts:
        if code == "ndvi":
            raise CycleValidationError("ndvi is outside the multi-index cycle")
        if code not in DEFAULT_INDICES:
            raise CycleValidationError(f"unsupported index code: {code}")
        if code in result:
            raise CycleValidationError("duplicate index codes are not allowed")
        result.append(code)
    return result


def external_absolute_path(value: str | None, label: str, required: bool = False) -> Path | None:
    if value is None:
        if required:
            raise CycleValidationError(f"{label} is required")
        return None
    path = Path(value)
    if not path.is_absolute():
        raise CycleValidationError(f"{label} must be an absolute path")
    try:
        path.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError:
        return path
    raise CycleValidationError(f"{label} must be outside the repository")


def resolve_dates(date_from_raw: str | None, date_to_raw: str | None, lookback_days: int, today: date | None = None) -> tuple[date, date]:
    if lookback_days < 1:
        raise CycleValidationError("--lookback-days must be positive")
    today = today or date.today()
    try:
        end = datetime.strptime(date_to_raw, "%Y-%m-%d").date() if date_to_raw else today
        start = datetime.strptime(date_from_raw, "%Y-%m-%d").date() if date_from_raw else end - timedelta(days=lookback_days)
    except ValueError as exc:
        raise CycleValidationError("dates must use YYYY-MM-DD") from exc
    if end > today:
        raise CycleValidationError("--date-to must not be in the future")
    if start > end:
        raise CycleValidationError("date range is inverted")
    if (end - start).days > 30:
        raise CycleValidationError("date range must not exceed 30 days")
    return start, end


def default_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA_VERSION, "next_offset": 0, "retry_field_ids": [], "last_completed_run_id": None, "updated_at": None}


def validate_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict) or set(state) != set(default_state()):
        raise CycleValidationError("state schema is invalid")
    if state["schema_version"] != STATE_SCHEMA_VERSION or not isinstance(state["next_offset"], int) or state["next_offset"] < 0:
        raise CycleValidationError("state version or offset is invalid")
    retry = state["retry_field_ids"]
    if not isinstance(retry, list) or len(retry) > MAX_BATCH_SIZE or any(not isinstance(item, int) or item <= 0 for item in retry) or len(set(retry)) != len(retry):
        raise CycleValidationError("state retry queue is invalid")
    if state["last_completed_run_id"] is not None and not isinstance(state["last_completed_run_id"], str):
        raise CycleValidationError("state completed run id is invalid")
    if state["updated_at"] is not None and not isinstance(state["updated_at"], str):
        raise CycleValidationError("state timestamp is invalid")
    return state


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_state()
    try:
        return validate_state(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, CycleValidationError) as exc:
        raise CycleValidationError("state file is malformed") from exc


def atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def select_rotating_batch(active_ids: list[int], state: dict[str, Any], batch_size: int) -> tuple[list[int], int, int, int]:
    if not active_ids:
        return [], 0, 0, 0
    active = list(dict.fromkeys(active_ids))
    retry = [item for item in state["retry_field_ids"] if item in set(active)]
    retry_selected = retry[:batch_size]
    remaining = batch_size - len(retry_selected)
    offset = state["next_offset"] % len(active)
    rotated = active[offset:] + active[:offset]
    rotation = [item for item in rotated if item not in retry_selected][:remaining]
    return retry_selected + rotation, len(retry_selected), len(rotation), (offset + len(rotation)) % len(active)


def query_active_field_ids() -> list[int]:
    from database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        return [int(row[0]) for row in db.execute(text("SELECT id FROM fields WHERE is_active = true ORDER BY id ASC")).all()]
    finally:
        db.close()


def sanitize_text(value: str, limit: int = 4000) -> str:
    value = value[:limit]
    value = re.sub(r"(?i)(authorization\s*[:=]\s*)(\S+)", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(token|password|secret|api[_-]?key)\s*[:=]\s*[^\s,]+", r"\1=[REDACTED]", value)
    value = re.sub(r"\b\w+(?:\+\w+)?://[^\s@]+@[^\s]+", "[REDACTED_URL]", value)
    value = re.sub(r"[A-Za-z]:\\Users\\[^\\\s]+", "[REDACTED_USER_PATH]", value)
    return value


def build_child_command(field_id: int, indices: list[str], start: date, end: date, mode: str, output_log: Path) -> list[str]:
    command = [sys.executable, str((BACKEND / "scripts" / "collect_satellite_indices.py").resolve()), "--field-id", str(field_id), "--indices", ",".join(indices), "--date-from", start.isoformat(), "--date-to", end.isoformat(), "--max-fields", "1", "--skip-existing", "--output-log", str(output_log), f"--{mode}"]
    return command


def execute_child(command: list[str], timeout_seconds: int) -> dict[str, Any]:
    process = subprocess.Popen(
        command,
        cwd=str(REPO_ROOT),
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return {"exit_code": process.returncode, "timed_out": False, "stdout": sanitize_text(stdout), "stderr": sanitize_text(stderr)}
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        return {"exit_code": 1, "timed_out": True, "stdout": sanitize_text(stdout), "stderr": sanitize_text(stderr)}


def parse_child_log(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CycleValidationError("child JSON summary is missing or malformed") from exc
    if not isinstance(data, dict):
        raise CycleValidationError("child JSON summary is malformed")
    return data


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a bounded rotating multi-index collection cycle.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--write", action="store_true")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--field-ids")
    scope.add_argument("--all-active-fields", action="store_true")
    parser.add_argument("--indices")
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--max-fields", type=int)
    parser.add_argument("--state-file")
    parser.add_argument("--lock-file")
    parser.add_argument("--output-dir")
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--retry-base-seconds", type=int, default=2)
    parser.add_argument("--field-timeout-seconds", type=int, default=180)
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> tuple[str, list[str], Path | None, Path | None]:
    mode = "write" if args.write else "apply" if args.apply else "dry-run"
    indices = parse_indices(args.indices)
    if args.max_attempts < 1 or args.max_attempts > MAX_ATTEMPTS or args.retry_base_seconds < 0 or args.retry_base_seconds > MAX_BACKOFF_SECONDS or args.field_timeout_seconds < 1 or args.field_timeout_seconds > 900:
        raise CycleValidationError("retry or timeout bounds are invalid")
    if args.field_ids:
        parse_field_ids(args.field_ids)
    if mode != "dry-run" and not (args.field_ids or args.all_active_fields):
        raise CycleValidationError("apply/write requires an explicit scope")
    if args.all_active_fields:
        if args.batch_size is None or args.batch_size < 1 or args.batch_size > MAX_BATCH_SIZE:
            raise CycleValidationError("--all-active-fields requires a bounded --batch-size")
        state_file = external_absolute_path(args.state_file, "--state-file", required=mode != "dry-run")
    else:
        state_file = None
    if args.max_fields is not None and (args.max_fields < 1 or args.max_fields > MAX_DRY_RUN_FIELDS):
        raise CycleValidationError("--max-fields must be between 1 and 25")
    if args.max_fields is not None and mode != "dry-run":
        raise CycleValidationError("--max-fields is dry-run only")
    output_dir = external_absolute_path(args.output_dir, "--output-dir", required=mode != "dry-run")
    external_absolute_path(args.lock_file, "--lock-file") if args.lock_file else None
    return mode, indices, state_file, output_dir


def run(args: argparse.Namespace, *, field_query: Callable[[], list[int]] = query_active_field_ids, child_runner: Callable[[list[str], int], dict[str, Any]] = execute_child, sleeper: Callable[[float], None] = time.sleep) -> int:
    lock_path: str | None = None
    started = time.monotonic()
    run_id = uuid.uuid4().hex
    summary: dict[str, Any] = {"schema_version": 1, "run_id": run_id, "exit_code": 4, "started_at": utc_now()}
    run_dir: Path | None = None
    try:
        mode, indices, state_file, output_dir = validate_args(args)
        start_date, end_date = resolve_dates(args.date_from, args.date_to, args.lookback_days)
        if output_dir:
            run_dir = output_dir / f"cycle_{run_id}"
            (run_dir / "per_field").mkdir(parents=True, exist_ok=False)
        lock_file = str(external_absolute_path(args.lock_file, "--lock-file") or DEFAULT_LOCK_FILE)
        try:
            lock_path = acquire_lock(lock_file, mutex_name=CYCLE_MUTEX_NAME)
        except SystemExit as exc:
            if exc.code == 3:
                return 3
            raise
        state = load_state(state_file) if state_file else default_state()
        if args.field_ids:
            selected = parse_field_ids(args.field_ids)
            retry_count = rotation_count = 0
            next_offset = state["next_offset"]
        else:
            active = field_query()
            selected, retry_count, rotation_count, next_offset = select_rotating_batch(active, state, args.batch_size or args.max_fields or MAX_DRY_RUN_FIELDS)
        if args.max_fields is not None:
            selected = selected[:args.max_fields]
        results: list[dict[str, Any]] = []
        failures: list[int] = []
        totals = {"inserted": 0, "skipped_existing": 0, "quality_blocked": 0, "timeouts": 0, "attempts": 0}
        for field_id in selected:
            success = False
            for attempt in range(1, args.max_attempts + 1):
                child_log = (run_dir / "per_field" / f"{field_id}_attempt_{attempt}.json") if run_dir else Path(tempfile.gettempdir()) / f"agrosat_cycle_{run_id}_{field_id}_{attempt}.json"
                outcome = child_runner(build_child_command(field_id, indices, start_date, end_date, mode, child_log), args.field_timeout_seconds)
                totals["attempts"] += 1
                record = {"field_id": field_id, "attempt": attempt, **outcome}
                if outcome.get("timed_out"):
                    totals["timeouts"] += 1
                try:
                    child = parse_child_log(child_log)
                    record["child_summary"] = child
                    totals["inserted"] += int(child.get("db_inserted", 0))
                    totals["skipped_existing"] += int(child.get("db_skipped_existing", child.get("db_skipped", 0)))
                    totals["quality_blocked"] += int(child.get("quality_blocked", 0))
                except CycleValidationError as exc:
                    record["log_error"] = str(exc)
                    outcome["exit_code"] = 1
                results.append(record)
                if outcome["exit_code"] == 0:
                    success = True
                    break
                if outcome["exit_code"] == 2:
                    break
                if attempt < args.max_attempts:
                    sleeper(min(args.retry_base_seconds * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS))
            if not success:
                failures.append(field_id)
        retry_after = [item for item in state["retry_field_ids"] if item not in selected and item not in failures]
        retry_after.extend(item for item in failures if item not in retry_after)
        retry_after = retry_after[:MAX_BATCH_SIZE]
        if state_file:
            next_state = {"schema_version": STATE_SCHEMA_VERSION, "next_offset": next_offset, "retry_field_ids": retry_after, "last_completed_run_id": run_id, "updated_at": utc_now()}
            atomic_json_write(state_file, next_state)
        exit_code = 1 if failures else 0
        summary.update({"mode": mode, "indices": indices, "date_from": start_date.isoformat(), "date_to": end_date.isoformat(), "selected_field_ids": selected, "batch_source_counts": {"retry": retry_count, "rotation": rotation_count}, "attempt_counts": totals["attempts"], "success_count": len(selected) - len(failures), "failure_count": len(failures), "timeout_count": totals["timeouts"], "inserted_count": totals["inserted"], "skipped_existing_count": totals["skipped_existing"], "quality_blocked_count": totals["quality_blocked"], "retry_queue_before": state["retry_field_ids"], "retry_queue_after": retry_after, "state_advanced": bool(state_file), "exit_code": exit_code})
        if run_dir:
            with (run_dir / "field_results.jsonl").open("w", encoding="utf-8") as handle:
                for result in results:
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        return exit_code
    except CycleValidationError:
        summary["exit_code"] = 2
        return 2
    except Exception:
        summary["exit_code"] = 4
        return 4
    finally:
        summary["finished_at"] = utc_now()
        summary["duration_seconds"] = round(time.monotonic() - started, 3)
        if run_dir:
            try:
                atomic_json_write(run_dir / "cycle_summary.json", summary)
            except Exception:
                summary["exit_code"] = 2
        if lock_path:
            release_lock(lock_path)


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(parse_args(argv)))


if __name__ == "__main__":
    main()
