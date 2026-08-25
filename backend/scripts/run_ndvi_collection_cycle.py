#!/usr/bin/env python3
"""Bounded, durable process runner for the standalone NDVI collector."""
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

SCHEMA = 1
MAX_BATCH = 100
MAX_DRY = 25
MAX_ATTEMPTS = 5
MAX_BACKOFF = 300
CAPTURE = 16384
CHILD_JSON_LIMIT = 65536
DEFAULT_LOCK = Path(tempfile.gettempdir()) / "agrosat_ndvi_cycle.lock"
MUTEX = "Global\\AgroSatNdviCollectionCycle_v1"


class ValidationError(ValueError):
    """A bad command, child contract, or durable artifact contract."""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sanitize_text(value: Any, limit: int = 4000) -> str:
    text = str(value)[:limit]
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)\b(token|password|secret|api[_-]?key)\s*[:=]\s*[^\s,]+", r"\1=[REDACTED]", text)
    text = re.sub(r"(?i)\b(?:postgres(?:ql)?|https?)://[^\s@]+@[^\s]+", "[REDACTED_URL]", text)
    text = re.sub(r"(?i)(?:\\\\\?\\)?[a-z]:\\users\\[^\\]+(?:\\[^\s,]*)?", "[REDACTED_USER_PATH]", text)
    text = re.sub(r"(?i)\\device\\harddiskvolume\d+\\users\\[^\\]+(?:\\[^\s,]*)?", "[REDACTED_USER_PATH]", text)
    return text


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {sanitize_text(key, 200): sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    return sanitize_text(value) if isinstance(value, str) else value


def provider_failure_category(outcome: dict[str, Any], child: dict[str, Any]) -> str | None:
    """Return only a bounded operational class, never an upstream response body."""
    searchable = " ".join(
        [
            str(outcome.get("stdout", "")),
            str(outcome.get("stderr", "")),
            *(str(item) for item in child.get("diagnostics", [])),
        ]
    ).lower()
    markers = (
        ("authentication", ("category=authentication", "invalid_client", "unauthorized", " 401", " 403")),
        ("quota", ("category=quota", "rate limit", " 429")),
        ("network", ("category=network", "category=provider_unavailable", "connection", "dns")),
        ("timeout", ("category=timeout", "timeout", "timed out")),
        ("cloud", ("cloud", "quality_blocked")),
    )
    for category, values in markers:
        if any(value in searchable for value in values):
            return category
    return "provider_error" if child.get("error_count") else None


def external(raw: str | None, label: str, required: bool = False) -> Path | None:
    if raw is None:
        if required:
            raise ValidationError(f"{label} is required")
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise ValidationError(f"{label} must be an absolute path")
    try:
        path.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError:
        return path
    raise ValidationError(f"{label} must be outside repository")


def resolve_dates(date_from: str | None, date_to: str | None, days: int, today: date | None = None) -> tuple[date, date]:
    if not 1 <= days <= 30:
        raise ValidationError("--lookback-days must be 1..30")
    today = today or date.today()
    try:
        end = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today
        start = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else end - timedelta(days=days)
    except ValueError as exc:
        raise ValidationError("dates must use YYYY-MM-DD") from exc
    if end > today or start > end or (end - start).days > 30:
        raise ValidationError("date range is invalid or exceeds 30 days")
    return start, end


def parse_ids(raw: str) -> list[int]:
    values: list[int] = []
    for part in raw.split(","):
        if not part.strip():
            continue
        try:
            field_id = int(part.strip())
        except ValueError as exc:
            raise ValidationError("--field-ids must contain positive integers") from exc
        if field_id <= 0:
            raise ValidationError("--field-ids must contain positive integers")
        if field_id not in values:
            values.append(field_id)
    if not values or len(values) > MAX_BATCH:
        raise ValidationError("--field-ids must contain 1 to 100 unique positive integers")
    return values


def state_default() -> dict[str, Any]:
    return {"schema_version": 1, "next_offset": 0, "retry_field_ids": [], "last_completed_run_id": None, "updated_at": None}


def validate_state(state: Any) -> dict[str, Any]:
    expected = state_default()
    valid = isinstance(state, dict) and set(state) == set(expected) and state["schema_version"] == 1
    valid = valid and type(state["next_offset"]) is int and state["next_offset"] >= 0
    valid = valid and isinstance(state["retry_field_ids"], list) and len(state["retry_field_ids"]) <= MAX_BATCH
    valid = valid and all(type(item) is int and item > 0 for item in state["retry_field_ids"])
    valid = valid and len(set(state["retry_field_ids"])) == len(state["retry_field_ids"])
    valid = valid and (state["last_completed_run_id"] is None or isinstance(state["last_completed_run_id"], str))
    valid = valid and (state["updated_at"] is None or isinstance(state["updated_at"], str))
    if not valid:
        raise ValidationError("state schema is invalid")
    return state


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return state_default()
    try:
        return validate_state(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        raise ValidationError("state file is malformed") from exc


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(sanitize(data), handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=".jsonl", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(sanitize(record), ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def select_batch(active: list[int], state: dict[str, Any], size: int) -> tuple[list[int], int, int, int]:
    active = list(dict.fromkeys(active))
    active_set = set(active)
    if not active:
        return [], 0, 0, 0
    retries = [item for item in state["retry_field_ids"] if item in active_set]
    retry_capacity = size if len(retries) == len(active) else min(size // 2, size - 1)
    retry = retries[:retry_capacity]
    offset = state["next_offset"] % len(active)
    rotated = active[offset:] + active[:offset]
    rotation = [item for item in rotated if item not in retries][:size - len(retry)]
    scan = 0
    remaining = len(rotation)
    for item in rotated:
        scan += 1
        if item not in retries:
            remaining -= 1
            if remaining == 0:
                break
    return retry + rotation, len(retry), len(rotation), (offset + scan) % len(active)


def query_active() -> list[int]:
    from database import SessionLocal
    from sqlalchemy import text
    session = SessionLocal()
    try:
        session.execute(text("SET TRANSACTION READ ONLY"))
        return [int(row[0]) for row in session.execute(text("SELECT id FROM fields WHERE is_active=true AND geometry IS NOT NULL AND NOT ST_IsEmpty(geometry) ORDER BY id ASC")).all()]
    finally:
        session.rollback()
        session.close()


def command(field_id: int, start: date, end: date, current_mode: str, log: Path, lock: Path) -> list[str]:
    return [sys.executable, str((BACKEND / "scripts" / "collect_ndvi.py").resolve()), "--field-id", str(field_id), "--date-from", start.isoformat(), "--date-to", end.isoformat(), "--skip-existing", "--output-log", str(log), "--lock-file", str(lock), f"--{current_mode}"]


def read_capture(handle: Any, limit: int = CAPTURE) -> str:
    """Read at most limit + one byte, proving a child cannot fill memory."""
    raw = handle.read(limit + 1)
    if not isinstance(raw, bytes):
        raise ValidationError("process capture is not binary")
    return raw[:limit].decode("utf-8", errors="replace")


def execute(cmd: list[str], timeout: int) -> dict[str, Any]:
    out = tempfile.NamedTemporaryFile(prefix="agrosat_ndvi_out_", delete=False)
    err = tempfile.NamedTemporaryFile(prefix="agrosat_ndvi_err_", delete=False)
    output_path, error_path = Path(out.name), Path(err.name)
    try:
        process = subprocess.Popen(cmd, cwd=str(REPO_ROOT), shell=False, stdout=out, stderr=err)
        out.close()
        err.close()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        with output_path.open("rb") as output_handle, error_path.open("rb") as error_handle:
            return {"exit_code": 1 if timed_out else process.returncode, "timed_out": timed_out, "stdout": read_capture(output_handle), "stderr": read_capture(error_handle)}
    finally:
        for handle in (out, err):
            if not handle.closed:
                handle.close()
        for path in (output_path, error_path):
            try:
                path.unlink()
            except OSError:
                pass


def read_child_json_bounded(path: Path) -> Any:
    """Read a bounded UTF-8 child summary; invalid UTF-8 is rejected."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(CHILD_JSON_LIMIT + 1)
        if len(raw) > CHILD_JSON_LIMIT:
            raise ValidationError("child JSON summary exceeds size limit")
        return json.loads(raw.decode("utf-8", errors="strict"))
    except Exception as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError("child JSON summary is missing or malformed") from exc


def parse_child(path: Path, *, field_id: int | None = None, current_mode: str | None = None, start: date | None = None, end: date | None = None, process_exit: int | None = None) -> dict[str, Any]:
    data = read_child_json_bounded(path)
    required = {"schema_version", "field_id", "mode", "date_from", "date_to", "exit_code", "inserted_count", "skipped_existing_count", "quality_blocked_count", "error_count"}
    if not isinstance(data, dict) or not required.issubset(data):
        raise ValidationError("child JSON summary is malformed")
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA:
        raise ValidationError("child schema_version is invalid")
    if type(data["exit_code"]) is not int or data["exit_code"] not in (0, 1, 2, 3, 4):
        raise ValidationError("child exit_code is invalid")
    for key in ("inserted_count", "skipped_existing_count", "quality_blocked_count", "error_count"):
        if type(data[key]) is not int or data[key] < 0:
            raise ValidationError("child counts are invalid")
    if data["inserted_count"] > 1:
        raise ValidationError("child inserted_count is invalid")
    if field_id is not None and data["field_id"] != field_id:
        raise ValidationError("child field_id does not match invocation")
    if current_mode is not None and data["mode"] != current_mode:
        raise ValidationError("child mode does not match invocation")
    if start is not None and data["date_from"] != start.isoformat():
        raise ValidationError("child date_from does not match invocation")
    if end is not None and data["date_to"] != end.isoformat():
        raise ValidationError("child date_to does not match invocation")
    if process_exit is not None and data["exit_code"] != process_exit:
        raise ValidationError("child process and summary exit codes differ")
    return sanitize(data)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run bounded rotating standalone NDVI collection.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--write", action="store_true")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--field-ids")
    scope.add_argument("--all-active-fields", action="store_true")
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


def mode(args: argparse.Namespace) -> str:
    return "write" if args.write else "apply" if args.apply else "dry-run"


def validate(args: argparse.Namespace) -> tuple[str, Path | None, Path | None, Path | None]:
    current_mode = mode(args)
    if not 1 <= args.max_attempts <= MAX_ATTEMPTS or not 0 <= args.retry_base_seconds <= MAX_BACKOFF or not 1 <= args.field_timeout_seconds <= 900:
        raise ValidationError("retry or timeout bounds are invalid")
    if args.field_ids:
        parse_ids(args.field_ids)
    if not (args.field_ids or args.all_active_fields) and not (current_mode == "dry-run" and args.max_fields):
        raise ValidationError("an explicit scope or bounded dry-run --max-fields is required")
    if args.all_active_fields and (args.batch_size is None or not 2 <= args.batch_size <= MAX_BATCH):
        raise ValidationError("--all-active-fields requires --batch-size between 2 and 100")
    if args.max_fields is not None and (not 1 <= args.max_fields <= MAX_DRY or current_mode != "dry-run"):
        raise ValidationError("--max-fields is dry-run only and must be 1..25")
    return current_mode, external(args.state_file, "--state-file", args.all_active_fields and current_mode != "dry-run"), external(args.output_dir, "--output-dir", current_mode != "dry-run"), external(args.lock_file, "--lock-file")


def retry_after(state: dict[str, Any], selected: list[int], failed: list[int]) -> list[int]:
    values = [item for item in state["retry_field_ids"] if item not in selected] + failed
    return list(dict.fromkeys(values))[:MAX_BATCH]


def run(args: argparse.Namespace, *, field_query: Callable[[], list[int]] = query_active, child_runner: Callable[[list[str], int], dict[str, Any]] = execute, sleeper: Callable[[float], None] = time.sleep) -> int:
    started = time.monotonic()
    run_id = uuid.uuid4().hex
    final_exit_code = 4
    lock = None
    run_dir: Path | None = None
    state = state_default()
    next_state: dict[str, Any] | None = None
    results: list[dict[str, Any]] = []
    state_advanced = False
    summary: dict[str, Any] = {"schema_version": SCHEMA, "run_id": run_id, "exit_code": 4, "started_at": now(), "diagnostics": []}
    try:
        current_mode, state_file, output_dir, lock_override = validate(args)
        start, end = resolve_dates(args.date_from, args.date_to, args.lookback_days)
        if output_dir is not None:
            run_dir = output_dir / f"cycle_{run_id}"
            (run_dir / "per_field").mkdir(parents=True, exist_ok=False)
        try:
            lock = acquire_lock(str(lock_override or DEFAULT_LOCK), mutex_name=MUTEX)
        except SystemExit as exc:
            if exc.code == 3:
                final_exit_code = 3
                raise RuntimeError("LOCK_CONTENTION")
            raise
        state = load_state(state_file) if state_file else state_default()
        if args.field_ids:
            selected = parse_ids(args.field_ids)
            retry_count, rotation_count, next_offset = 0, 0, state["next_offset"]
        else:
            selected, retry_count, rotation_count, next_offset = select_batch(field_query(), state, args.batch_size or args.max_fields or MAX_DRY)
        if args.max_fields:
            selected = selected[:args.max_fields]
        if not selected:
            raise ValidationError("NO_ACTIVE_FIELDS_OR_EMPTY_BATCH")
        attempted: list[int] = []
        successful: list[int] = []
        failed: list[int] = []
        fatal_field_id: int | None = None
        terminal_provider_category: str | None = None
        totals = {"attempts": 0, "timeouts": 0, "inserted": 0, "skipped": 0, "blocked": 0}
        for field_id in selected:
            attempted.append(field_id)
            succeeded = False
            field_fatal_code: int | None = None
            for attempt in range(1, args.max_attempts + 1):
                # Keep the child IPC artifact below the Windows path budget.  The
                # parsed, sanitized contract is durably retained in field_results.
                temporary_log = True
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix="agrosat_ndvi_child_", suffix=".json"
                )
                os.close(descriptor)
                log = Path(temporary_name)
                try:
                    outcome = sanitize(child_runner(command(field_id, start, end, current_mode, log, Path(str(lock_override or DEFAULT_LOCK) + f".child.{field_id}")), args.field_timeout_seconds))
                    totals["attempts"] += 1
                    totals["timeouts"] += int(bool(outcome.get("timed_out")))
                    if outcome.get("timed_out"):
                        results.append({"field_id": field_id, "attempt": attempt, **outcome, "classification": "TIMEOUT_RETRYABLE"})
                        if attempt < args.max_attempts:
                            sleeper(min(args.retry_base_seconds * 2 ** (attempt - 1), MAX_BACKOFF))
                        continue
                    process_exit = outcome.get("exit_code")
                    if type(process_exit) is not int or process_exit not in (0, 1, 2, 3, 4):
                        raise ValidationError("child process exit code is invalid")
                    child = parse_child(log, field_id=field_id, current_mode=current_mode, start=start, end=end, process_exit=process_exit)
                    record = {"field_id": field_id, "attempt": attempt, **outcome, "child_summary": child}
                    category = provider_failure_category(outcome, child) if process_exit else None
                    if category is not None:
                        record["failure_category"] = category
                    totals["inserted"] += child["inserted_count"]
                    totals["skipped"] += child["skipped_existing_count"]
                    totals["blocked"] += child["quality_blocked_count"]
                    if process_exit == 0:
                        record["classification"] = "SUCCESS"
                        results.append(record)
                        succeeded = True
                        break
                    if process_exit in (2, 4):
                        record["classification"] = "FATAL_CHILD_CONTRACT_ERROR" if process_exit == 2 else "FATAL_CHILD_INTERNAL_ERROR"
                        results.append(record)
                        field_fatal_code = process_exit
                        break
                    record["classification"] = "RETRYABLE"
                    results.append(record)
                    if category in {"authentication", "quota"}:
                        terminal_provider_category = category
                        break
                    if attempt < args.max_attempts:
                        sleeper(min(args.retry_base_seconds * 2 ** (attempt - 1), MAX_BACKOFF))
                except ValidationError as exc:
                    results.append({"field_id": field_id, "attempt": attempt, "exit_code": 2, "diagnostic": sanitize_text(exc), "classification": "FATAL_CHILD_CONTRACT_ERROR"})
                    field_fatal_code = 2
                    break
                finally:
                    if temporary_log:
                        try:
                            log.unlink()
                        except OSError:
                            pass
            if succeeded:
                successful.append(field_id)
            else:
                failed.append(field_id)
            if field_fatal_code is not None:
                fatal_field_id = field_id
                final_exit_code = field_fatal_code
                break
            if terminal_provider_category is not None:
                final_exit_code = 1
                break
        unattempted = [item for item in selected if item not in attempted]
        if fatal_field_id is None:
            final_exit_code = 1 if failed else 0
        retry_queue = retry_after(state, selected, failed)
        summary.update(mode=current_mode, date_from=start.isoformat(), date_to=end.isoformat(), selected_field_ids=selected, attempted_field_ids=attempted, successful_field_ids=successful, failed_field_ids=failed, fatal_field_id=fatal_field_id, provider_failure_category=terminal_provider_category, unattempted_field_ids=unattempted, batch_source_counts={"retry": retry_count, "rotation": rotation_count}, attempt_counts=totals["attempts"], success_count=len(successful), failure_count=len(failed), unattempted_count=len(unattempted), timeout_count=totals["timeouts"], inserted_count=totals["inserted"], skipped_existing_count=totals["skipped"], quality_blocked_count=totals["blocked"], retry_queue_before=state["retry_field_ids"], retry_queue_after=retry_queue, state_advanced=False)
        if final_exit_code in (0, 1) and state_file is not None:
            next_state = {"schema_version": 1, "next_offset": next_offset, "retry_field_ids": retry_queue, "last_completed_run_id": run_id, "updated_at": now()}
    except (ValidationError, OSError) as exc:
        final_exit_code = 2
        summary["diagnostics"].append(sanitize_text(exc))
    except RuntimeError as exc:
        if str(exc) != "LOCK_CONTENTION":
            final_exit_code = 4
            summary["diagnostics"].append(sanitize_text(exc))
    except Exception as exc:
        final_exit_code = 4
        summary["diagnostics"].append(sanitize_text(exc))
    finally:
        summary.update(finished_at=now(), duration_seconds=round(time.monotonic() - started, 3))

        def write_summary() -> None:
            summary["exit_code"] = final_exit_code
            summary["state_advanced"] = state_advanced
            atomic_json(run_dir / "cycle_summary.json", summary)

        def restore_prior_state() -> bool:
            nonlocal state_advanced
            if not state_advanced or state_file is None:
                return True
            try:
                atomic_json(state_file, state)
                state_advanced = False
                summary["state_advanced"] = False
                return True
            except Exception as exc:
                summary["diagnostics"].append(sanitize_text(f"state restore failed: {exc}"))
                return False

        if run_dir is not None:
            try:
                atomic_jsonl(run_dir / "field_results.jsonl", results)
                # This provisional evidence is deliberately written before state changes.
                write_summary()
                if next_state is not None and final_exit_code in (0, 1):
                    atomic_json(state_file, next_state)
                    state_advanced = True
                    summary["state_advanced"] = True
            except Exception as exc:
                final_exit_code = 2
                summary["diagnostics"].append(sanitize_text(exc))
                restore_prior_state()
        if lock is not None:
            try:
                release_lock(lock)
            except Exception as exc:
                final_exit_code = 4
                summary["diagnostics"].append(sanitize_text(f"lock release failed: {exc}"))
        if final_exit_code in (2, 4):
            restore_prior_state()
        if run_dir is not None:
            try:
                write_summary()
            except Exception as exc:
                final_exit_code = 2
                summary["diagnostics"].append(sanitize_text(f"final summary write failed: {exc}"))
                restore_prior_state()
                # Exactly one recovery attempt: the final durable summary must match return.
                try:
                    write_summary()
                except Exception as recovery_exc:
                    summary["diagnostics"].append(sanitize_text(f"error summary write failed: {recovery_exc}"))
    return final_exit_code


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(parse_args(argv)))


if __name__ == "__main__":
    main()
