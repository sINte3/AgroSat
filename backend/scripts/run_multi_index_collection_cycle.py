#!/usr/bin/env python3
"""Standalone, bounded rotating orchestrator for the multi-index collector.

The default mode is dry-run.  This module deliberately keeps child output and
all persisted diagnostics bounded and sanitized because it is used by a
scheduler-facing process boundary.
"""

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
CHILD_OUTPUT_LIMIT = 16_384
DEFAULT_LOCK_FILE = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp") / "agrosat_multi_index_cycle.lock"
CYCLE_MUTEX_NAME = "Global\\AgroSatMultiIndexCollectionCycle_v1"


class CycleValidationError(ValueError):
    pass


class CycleLockContentionError(RuntimeError):
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
    if not values or len(values) > MAX_BATCH_SIZE:
        raise CycleValidationError("--field-ids must contain 1 to 100 unique positive integers")
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
    if end > today or start > end or (end - start).days > 30:
        raise CycleValidationError("date range is invalid or exceeds 30 days")
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
            json.dump(sanitize_value(payload), handle, ensure_ascii=False, indent=2, sort_keys=True)
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
    """Reserve rotation capacity whenever a non-retry active field exists."""
    active = list(dict.fromkeys(active_ids))
    if not active:
        return [], 0, 0, 0
    active_set = set(active)
    retries = [item for item in state["retry_field_ids"] if item in active_set]
    nonretry_exists = any(item not in set(retries) for item in active)
    retry_limit = batch_size if not nonretry_exists else min(batch_size // 2, batch_size - 1)
    retry_selected = retries[:retry_limit]
    offset = state["next_offset"] % len(active)
    rotated = active[offset:] + active[:offset]
    rotation = [item for item in rotated if item not in retry_selected][:batch_size - len(retry_selected)]
    # Cursor reflects all active positions inspected to obtain rotation items,
    # including retry positions skipped while scanning the ordered active set.
    scanned = 0
    needed = len(rotation)
    for item in rotated:
        scanned += 1
        if item not in retry_selected:
            needed -= 1
            if needed == 0:
                break
    return retry_selected + rotation, len(retry_selected), len(rotation), (offset + scanned) % len(active)


def query_active_field_ids() -> list[int]:
    from database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        return [int(row[0]) for row in db.execute(text("SELECT id FROM fields WHERE is_active = true ORDER BY id ASC")).all()]
    finally:
        db.close()


def sanitize_text(value: str, limit: int = 4000) -> str:
    value = str(value)[:limit]
    value = re.sub(r"(?i)(authorization\s*[:=]\s*)(\S+)", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(token|password|secret|api[_-]?key)\s*[:=]\s*[^\s,]+", r"\1=[REDACTED]", value)
    value = re.sub(r"\b\w+(?:\+\w+)?://[^\s@]+@[^\s]+", "[REDACTED_URL]", value)
    value = re.sub(r"(?i)(?:\\\\[?.]\\)?[a-z]:\\users\\[^\\]+", "[REDACTED_USER_PATH]", value)
    return value


def sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {sanitize_text(str(key), 200): sanitize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_value(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def build_child_command(field_id: int, indices: list[str], start: date, end: date, mode: str, output_log: Path) -> list[str]:
    return [sys.executable, str((BACKEND / "scripts" / "collect_satellite_indices.py").resolve()), "--field-id", str(field_id), "--indices", ",".join(indices), "--date-from", start.isoformat(), "--date-to", end.isoformat(), "--max-fields", "1", "--skip-existing", "--output-log", str(output_log), f"--{mode}"]


def _read_capture(path: Path) -> str:
    with path.open("rb") as handle:
        return sanitize_text(handle.read(CHILD_OUTPUT_LIMIT + 1).decode("utf-8", errors="replace"), CHILD_OUTPUT_LIMIT)


def execute_child(command: list[str], timeout_seconds: int) -> dict[str, Any]:
    stdout_file = tempfile.NamedTemporaryFile(prefix="agrosat_cycle_stdout_", delete=False)
    stderr_file = tempfile.NamedTemporaryFile(prefix="agrosat_cycle_stderr_", delete=False)
    stdout_path, stderr_path = Path(stdout_file.name), Path(stderr_file.name)
    try:
        process = subprocess.Popen(command, cwd=str(REPO_ROOT), shell=False, stdout=stdout_file, stderr=stderr_file)
        stdout_file.close(); stderr_file.close()
        timed_out = False
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
        return {"exit_code": 1 if timed_out else process.returncode, "timed_out": timed_out, "stdout": _read_capture(stdout_path), "stderr": _read_capture(stderr_path)}
    finally:
        for handle in (stdout_file, stderr_file):
            if not handle.closed:
                handle.close()
        for path in (stdout_path, stderr_path):
            try:
                path.unlink()
            except OSError:
                pass


def parse_child_log(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CycleValidationError("child JSON summary is missing or malformed") from exc
    if not isinstance(data, dict):
        raise CycleValidationError("child JSON summary is malformed")
    return sanitize_value(data)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a bounded rotating multi-index collection cycle.")
    mode = parser.add_mutually_exclusive_group(); mode.add_argument("--dry-run", action="store_true"); mode.add_argument("--apply", action="store_true"); mode.add_argument("--write", action="store_true")
    scope = parser.add_mutually_exclusive_group(); scope.add_argument("--field-ids"); scope.add_argument("--all-active-fields", action="store_true")
    parser.add_argument("--indices"); parser.add_argument("--batch-size", type=int); parser.add_argument("--max-fields", type=int); parser.add_argument("--state-file"); parser.add_argument("--lock-file"); parser.add_argument("--output-dir")
    parser.add_argument("--date-from"); parser.add_argument("--date-to"); parser.add_argument("--lookback-days", type=int, default=14); parser.add_argument("--max-attempts", type=int, default=3); parser.add_argument("--retry-base-seconds", type=int, default=2); parser.add_argument("--field-timeout-seconds", type=int, default=180)
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> tuple[str, list[str], Path | None, Path | None]:
    mode = "write" if args.write else "apply" if args.apply else "dry-run"
    indices = parse_indices(args.indices)
    if args.max_attempts < 1 or args.max_attempts > MAX_ATTEMPTS or args.retry_base_seconds < 0 or args.retry_base_seconds > MAX_BACKOFF_SECONDS or args.field_timeout_seconds < 1 or args.field_timeout_seconds > 900:
        raise CycleValidationError("retry or timeout bounds are invalid")
    if args.field_ids:
        parse_field_ids(args.field_ids)
    if not (args.field_ids or args.all_active_fields):
        if mode != "dry-run" or args.max_fields is None:
            raise CycleValidationError("an explicit scope or bounded dry-run --max-fields is required")
    if args.all_active_fields:
        if args.batch_size is None or args.batch_size < 2 or args.batch_size > MAX_BATCH_SIZE:
            raise CycleValidationError("--all-active-fields requires --batch-size between 2 and 100")
        state_file = external_absolute_path(args.state_file, "--state-file", required=mode != "dry-run")
    else:
        state_file = None
    if args.max_fields is not None and (args.max_fields < 1 or args.max_fields > MAX_DRY_RUN_FIELDS):
        raise CycleValidationError("--max-fields must be between 1 and 25")
    if args.max_fields is not None and mode != "dry-run":
        raise CycleValidationError("--max-fields is dry-run only")
    output_dir = external_absolute_path(args.output_dir, "--output-dir", required=mode != "dry-run")
    if args.lock_file:
        external_absolute_path(args.lock_file, "--lock-file")
    return mode, indices, state_file, output_dir


def _write_results(path: Path, results: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(sanitize_value(result), ensure_ascii=False) + "\n")
        handle.flush(); os.fsync(handle.fileno())


def run(args: argparse.Namespace, *, field_query: Callable[[], list[int]] = query_active_field_ids, child_runner: Callable[[list[str], int], dict[str, Any]] = execute_child, sleeper: Callable[[float], None] = time.sleep) -> int:
    started, run_id, lock_path, run_dir = time.monotonic(), uuid.uuid4().hex, None, None
    final_exit_code, state, next_state = 4, default_state(), None
    results: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"schema_version": 1, "run_id": run_id, "exit_code": 4, "started_at": utc_now(), "diagnostics": []}
    try:
        mode, indices, state_file, output_dir = validate_args(args)
        start_date, end_date = resolve_dates(args.date_from, args.date_to, args.lookback_days)
        if output_dir:
            run_dir = output_dir / f"cycle_{run_id}"
            try:
                (run_dir / "per_field").mkdir(parents=True, exist_ok=False)
            except OSError as exc:
                run_dir = None
                raise CycleValidationError("output run directory could not be created") from exc
        lock_file = str(external_absolute_path(args.lock_file, "--lock-file") or DEFAULT_LOCK_FILE)
        try:
            lock_path = acquire_lock(lock_file, mutex_name=CYCLE_MUTEX_NAME)
        except SystemExit as exc:
            if exc.code == 3:
                raise CycleLockContentionError("cycle lock is held") from exc
            raise
        state = load_state(state_file) if state_file else default_state()
        if args.field_ids:
            selected, retry_count, rotation_count, next_offset = parse_field_ids(args.field_ids), 0, 0, state["next_offset"]
        else:
            active = field_query()
            selected, retry_count, rotation_count, next_offset = select_rotating_batch(active, state, args.batch_size or args.max_fields or MAX_DRY_RUN_FIELDS)
        if args.max_fields is not None:
            selected = selected[:args.max_fields]
        if not selected:
            raise CycleValidationError("NO_ACTIVE_FIELDS_OR_EMPTY_BATCH")
        failures: list[int] = []; successes: list[int] = []; totals = {"inserted": 0, "skipped_existing": 0, "quality_blocked": 0, "timeouts": 0, "attempts": 0}; fatal = False
        for field_id in selected:
            success = False
            for attempt in range(1, args.max_attempts + 1):
                temporary_log = run_dir is None
                if run_dir:
                    child_log = run_dir / "per_field" / f"{field_id}_attempt_{attempt}.json"
                else:
                    descriptor, name = tempfile.mkstemp(prefix="agrosat_cycle_child_", suffix=".json")
                    os.close(descriptor)
                    child_log = Path(name)
                try:
                    outcome = sanitize_value(child_runner(build_child_command(field_id, indices, start_date, end_date, mode, child_log), args.field_timeout_seconds))
                    totals["attempts"] += 1
                    if outcome.get("timed_out"):
                        totals["timeouts"] += 1
                    record = {"field_id": field_id, "attempt": attempt, **outcome}
                    try:
                        child = parse_child_log(child_log)
                        record["child_summary"] = child
                        totals["inserted"] += int(child.get("db_inserted", 0)); totals["skipped_existing"] += int(child.get("db_skipped_existing", child.get("db_skipped", 0))); totals["quality_blocked"] += int(child.get("quality_blocked", 0))
                    except CycleValidationError as exc:
                        record["classification"] = "FATAL_LOG_CONTRACT_ERROR"; record["exit_code"] = 2; record["diagnostic"] = sanitize_text(str(exc)); results.append(record); fatal = True; break
                    code = int(record.get("exit_code", 4))
                    record["exit_code"] = code
                    record["classification"] = "SUCCESS" if code == 0 else "FATAL_CHILD_CONTRACT_ERROR" if code == 2 else "RETRYABLE_LOCK_CONTENTION" if code == 3 else "RETRYABLE_FAILURE"
                    results.append(record)
                    if code == 0:
                        success = True; break
                    if code == 2:
                        fatal = True; break
                    if attempt < args.max_attempts:
                        sleeper(min(args.retry_base_seconds * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS))
                finally:
                    if temporary_log:
                        try:
                            child_log.unlink()
                        except OSError:
                            pass
            if success:
                successes.append(field_id)
            else:
                failures.append(field_id)
            if fatal:
                break
        retry_after = [item for item in state["retry_field_ids"] if item not in selected and item not in failures]
        retry_after.extend(item for item in failures if item not in retry_after)
        retry_after = retry_after[:MAX_BATCH_SIZE]
        final_exit_code = 2 if fatal else (1 if failures else 0)
        unattempted = [field_id for field_id in selected if field_id not in successes and field_id not in failures]
        summary.update({"mode": mode, "indices": indices, "date_from": start_date.isoformat(), "date_to": end_date.isoformat(), "selected_field_ids": selected, "successful_field_ids": successes, "failed_field_ids": failures, "unattempted_field_ids": unattempted, "unattempted_count": len(unattempted), "batch_source_counts": {"retry": retry_count, "rotation": rotation_count}, "attempt_counts": totals["attempts"], "success_count": len(successes), "failure_count": len(failures), "timeout_count": totals["timeouts"], "inserted_count": totals["inserted"], "skipped_existing_count": totals["skipped_existing"], "quality_blocked_count": totals["quality_blocked"], "retry_queue_before": state["retry_field_ids"], "retry_queue_after": retry_after, "state_advanced": False})
        if not fatal and state_file:
            next_state = {"schema_version": STATE_SCHEMA_VERSION, "next_offset": next_offset, "retry_field_ids": retry_after, "last_completed_run_id": run_id, "updated_at": utc_now()}
    except CycleValidationError as exc:
        final_exit_code = 2; summary["classification"] = "NO_ACTIVE_FIELDS_OR_EMPTY_BATCH" if str(exc) == "NO_ACTIVE_FIELDS_OR_EMPTY_BATCH" else "VALIDATION_ERROR"; summary["diagnostics"].append(sanitize_text(str(exc)))
    except CycleLockContentionError as exc:
        final_exit_code = 3; summary["classification"] = "LOCK_CONTENTION"; summary["diagnostics"].append(sanitize_text(str(exc)))
    except Exception as exc:
        final_exit_code = 4; summary["classification"] = "ORCHESTRATION_ERROR"; summary["diagnostics"].append(sanitize_text(str(exc)))
    finally:
        summary["finished_at"] = utc_now(); summary["duration_seconds"] = round(time.monotonic() - started, 3)
        if run_dir:
            try:
                _write_results(run_dir / "field_results.jsonl", results)
            except Exception as exc:
                final_exit_code = 2; summary["diagnostics"].append(sanitize_text(f"field results persistence failed: {exc}"))
            summary["exit_code"] = final_exit_code
            try:
                atomic_json_write(run_dir / "cycle_summary.json", summary)
            except Exception as exc:
                final_exit_code = 2; summary["diagnostics"].append(sanitize_text(f"cycle summary persistence failed: {exc}"))
            if next_state is not None and final_exit_code in (0, 1):
                try:
                    atomic_json_write(state_file, next_state)
                    summary["state_advanced"] = True
                except Exception as exc:
                    final_exit_code = 2; summary["diagnostics"].append(sanitize_text(f"state persistence failed: {exc}"))
                summary["exit_code"] = final_exit_code
                try:
                    atomic_json_write(run_dir / "cycle_summary.json", summary)
                except Exception:
                    final_exit_code = 2
        if lock_path:
            try:
                release_lock(lock_path)
            except Exception as exc:
                if final_exit_code in (0, 1, 3, 4):
                    final_exit_code = 4
                summary["diagnostics"].append(sanitize_text(f"lock release failed: {exc}"))
                if run_dir:
                    summary["exit_code"] = final_exit_code
                    try:
                        atomic_json_write(run_dir / "cycle_summary.json", summary)
                    except Exception:
                        final_exit_code = 2
        if run_dir:
            summary["exit_code"] = final_exit_code
            try:
                atomic_json_write(run_dir / "cycle_summary.json", summary)
            except Exception:
                final_exit_code = 2
    return final_exit_code


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(parse_args(argv)))


if __name__ == "__main__":
    main()
