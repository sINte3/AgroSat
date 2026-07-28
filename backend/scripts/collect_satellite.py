#!/usr/bin/env python3
"""Canonical bounded AgroSat satellite collection entrypoint.

This command owns orchestration only. NDVI and multi-index persistence remain
behind their existing, independently idempotent child collectors.
"""

from __future__ import annotations

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


SCHEMA_VERSION = 1
SUPPORTED_INDICES = ("ndvi", "savi", "evi", "ndmi", "ndre")
MULTI_INDICES = frozenset(SUPPORTED_INDICES[1:])
MAX_FIELDS = 100
MAX_DATE_DAYS = 30
MAX_ATTEMPTS = 5
MAX_TIMEOUT_SECONDS = 900
MAX_CYCLE_TIMEOUT_SECONDS = 21600
MAX_CAPTURE_BYTES = 16384
MUTEX_NAME = "Global\\AgroSatCanonicalSatelliteCollector_v1"


class ContractError(ValueError):
    """Invalid canonical invocation or child-process contract."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sanitize_text(value: Any, limit: int = 4000) -> str:
    text = str(value)[:limit]
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)\b(token|password|secret|api[_-]?key)\s*[:=]\s*[^\s,]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)\b(?:postgres(?:ql)?|redis|https?)://[^\s@]+@[^\s]+",
        "[REDACTED_URL]",
        text,
    )
    text = re.sub(
        r"(?i)(?:\\\\\?\\)?[a-z]:\\users\\[^\\]+(?:\\[^\s,]*)?",
        "[REDACTED_USER_PATH]",
        text,
    )
    return text


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {sanitize_text(key, 200): sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    return sanitize_text(value) if isinstance(value, str) else value


def external_directory(raw: str | None, label: str) -> Path:
    if not raw:
        raise ContractError(f"{label} is required")
    path = Path(raw)
    if not path.is_absolute():
        raise ContractError(f"{label} must be an absolute path")
    resolved = path.resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return resolved
    raise ContractError(f"{label} must be outside the repository")


def parse_indices(raw: str | None) -> list[str]:
    if not raw:
        raise ContractError("--indices is required")
    indices: list[str] = []
    for part in raw.split(","):
        code = part.strip().lower()
        if not code or code not in SUPPORTED_INDICES:
            raise ContractError(
                f"--indices supports only {','.join(SUPPORTED_INDICES)}"
            )
        if code not in indices:
            indices.append(code)
    return indices


def parse_field_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    values: list[int] = []
    for part in raw.split(","):
        try:
            field_id = int(part.strip())
        except ValueError as exc:
            raise ContractError("--field-ids must contain positive integers") from exc
        if field_id <= 0:
            raise ContractError("--field-ids must contain positive integers")
        if field_id not in values:
            values.append(field_id)
    if not values or len(values) > MAX_FIELDS:
        raise ContractError("--field-ids must contain 1 to 100 unique values")
    return values


def resolve_dates(
    date_from_raw: str | None,
    date_to_raw: str | None,
    lookback_days: int,
    today: date | None = None,
) -> tuple[date, date]:
    if not 1 <= lookback_days <= MAX_DATE_DAYS:
        raise ContractError("--lookback-days must be between 1 and 30")
    today = today or date.today()
    try:
        end = (
            datetime.strptime(date_to_raw, "%Y-%m-%d").date()
            if date_to_raw
            else today
        )
        start = (
            datetime.strptime(date_from_raw, "%Y-%m-%d").date()
            if date_from_raw
            else end - timedelta(days=lookback_days)
        )
    except ValueError as exc:
        raise ContractError("dates must use YYYY-MM-DD") from exc
    if end > today or start > end or (end - start).days > MAX_DATE_DAYS:
        raise ContractError("date range is invalid or exceeds 30 days")
    return start, end


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(sanitize(value), handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="collect_satellite",
        description="Run one bounded, resumable AgroSat satellite collection cycle.",
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true", help="No provider call and no write.")
    modes.add_argument(
        "--diagnostic",
        action="store_true",
        help="Read-only provider diagnostic; never persist observations.",
    )
    modes.add_argument(
        "--apply",
        action="store_true",
        help="Real provider collection with idempotent database writes.",
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--field-ids")
    scope.add_argument("--all-active-fields", action="store_true")
    parser.add_argument("--indices", required=True)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--retry-base-seconds", type=int, default=2)
    parser.add_argument("--field-timeout-seconds", type=int, default=180)
    parser.add_argument("--cycle-timeout-seconds", type=int, default=3600)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--lock-dir", required=True)
    return parser.parse_args(argv)


def selected_mode(args: argparse.Namespace) -> str:
    if args.apply:
        return "apply"
    if args.diagnostic:
        return "diagnostic"
    return "dry-run"


def validate(args: argparse.Namespace) -> dict[str, Any]:
    indices = parse_indices(args.indices)
    field_ids = parse_field_ids(args.field_ids)
    if args.all_active_fields:
        if args.batch_size is None or not 2 <= args.batch_size <= MAX_FIELDS:
            raise ContractError(
                "--all-active-fields requires --batch-size between 2 and 100"
            )
    elif args.batch_size is not None:
        raise ContractError("--batch-size is valid only with --all-active-fields")
    if not 1 <= args.max_attempts <= MAX_ATTEMPTS:
        raise ContractError("--max-attempts must be between 1 and 5")
    if not 0 <= args.retry_base_seconds <= 300:
        raise ContractError("--retry-base-seconds must be between 0 and 300")
    if not 1 <= args.field_timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ContractError("--field-timeout-seconds must be between 1 and 900")
    if not 1 <= args.cycle_timeout_seconds <= MAX_CYCLE_TIMEOUT_SECONDS:
        raise ContractError("--cycle-timeout-seconds must be between 1 and 21600")
    start, end = resolve_dates(
        args.date_from,
        args.date_to,
        args.lookback_days,
    )
    return {
        "mode": selected_mode(args),
        "indices": indices,
        "field_ids": field_ids,
        "date_from": start,
        "date_to": end,
        "output_dir": external_directory(args.output_dir, "--output-dir"),
        "state_dir": external_directory(args.state_dir, "--state-dir"),
        "lock_dir": external_directory(args.lock_dir, "--lock-dir"),
    }


def child_mode(mode: str) -> str:
    return {
        "dry-run": "--dry-run",
        "diagnostic": "--apply",
        "apply": "--write",
    }[mode]


def build_child_command(
    provider: str,
    args: argparse.Namespace,
    plan: dict[str, Any],
    provider_output: Path,
) -> list[str]:
    script = (
        BACKEND / "scripts" / "run_ndvi_collection_cycle.py"
        if provider == "ndvi"
        else BACKEND / "scripts" / "run_multi_index_collection_cycle.py"
    )
    command = [
        sys.executable,
        str(script.resolve()),
        child_mode(plan["mode"]),
        "--date-from",
        plan["date_from"].isoformat(),
        "--date-to",
        plan["date_to"].isoformat(),
        "--max-attempts",
        str(args.max_attempts),
        "--retry-base-seconds",
        str(args.retry_base_seconds),
        "--field-timeout-seconds",
        str(args.field_timeout_seconds),
        "--output-dir",
        str(provider_output),
        "--state-file",
        str(plan["state_dir"] / f"{provider}_state.json"),
        "--lock-file",
        str(plan["lock_dir"] / f"{provider}_cycle.lock"),
    ]
    if plan["field_ids"]:
        command.extend(
            ["--field-ids", ",".join(str(value) for value in plan["field_ids"])]
        )
    else:
        command.extend(
            ["--all-active-fields", "--batch-size", str(args.batch_size)]
        )
    if provider == "multi":
        command.extend(
            [
                "--indices",
                ",".join(code for code in plan["indices"] if code in MULTI_INDICES),
            ]
        )
    return command


def _read_bounded(path: Path) -> str:
    with path.open("rb") as handle:
        raw = handle.read(MAX_CAPTURE_BYTES + 1)
    return sanitize_text(raw[:MAX_CAPTURE_BYTES].decode("utf-8", errors="replace"))


def execute_child(command: list[str], timeout_seconds: int) -> dict[str, Any]:
    stdout = tempfile.NamedTemporaryFile(prefix="agrosat_canonical_out_", delete=False)
    stderr = tempfile.NamedTemporaryFile(prefix="agrosat_canonical_err_", delete=False)
    stdout_path, stderr_path = Path(stdout.name), Path(stderr.name)
    process: subprocess.Popen | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            shell=False,
            stdout=stdout,
            stderr=stderr,
        )
        stdout.close()
        stderr.close()
        timed_out = False
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        return {
            "exit_code": 1 if timed_out else process.returncode,
            "timed_out": timed_out,
            "stdout": _read_bounded(stdout_path),
            "stderr": _read_bounded(stderr_path),
        }
    except KeyboardInterrupt:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    finally:
        for handle in (stdout, stderr):
            if not handle.closed:
                handle.close()
        for path in (stdout_path, stderr_path):
            try:
                path.unlink()
            except OSError:
                pass


def run(
    args: argparse.Namespace,
    *,
    child_runner: Callable[[list[str], int], dict[str, Any]] = execute_child,
    lock_acquire: Callable[..., Any] = acquire_lock,
    lock_release: Callable[[Any], None] = release_lock,
    run_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> tuple[int, dict[str, Any]]:
    started = time.monotonic()
    run_id = run_id_factory()
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": utc_now(),
        "exit_code": 4,
        "diagnostics": [],
        "children": [],
    }
    final_code = 4
    lock = None
    summary_path: Path | None = None
    try:
        plan = validate(args)
        for directory in (
            plan["output_dir"],
            plan["state_dir"],
            plan["lock_dir"],
        ):
            directory.mkdir(parents=True, exist_ok=True)
        run_dir = plan["output_dir"] / f"run_{run_id}"
        run_dir.mkdir(parents=False, exist_ok=False)
        summary_path = run_dir / "collector_summary.json"
        lock = lock_acquire(
            str(plan["lock_dir"] / "canonical_satellite.lock"),
            mutex_name=MUTEX_NAME,
        )
        providers = []
        if "ndvi" in plan["indices"]:
            providers.append("ndvi")
        if any(code in MULTI_INDICES for code in plan["indices"]):
            providers.append("multi")
        final_code = 0
        for provider in providers:
            provider_output = run_dir / provider
            provider_output.mkdir(parents=False, exist_ok=False)
            command = build_child_command(provider, args, plan, provider_output)
            outcome = sanitize(child_runner(command, args.cycle_timeout_seconds))
            code = outcome.get("exit_code")
            if type(code) is not int or code not in (0, 1, 2, 3, 4):
                raise ContractError("child returned an invalid exit code")
            summary["children"].append(
                {
                    "provider": provider,
                    "exit_code": code,
                    "timed_out": bool(outcome.get("timed_out")),
                    "stdout": outcome.get("stdout", ""),
                    "stderr": outcome.get("stderr", ""),
                }
            )
            if code in (2, 3, 4):
                final_code = code
                break
            if code == 1 and final_code == 0:
                final_code = code
    except ContractError as exc:
        final_code = 2
        summary["diagnostics"].append(sanitize_text(exc))
    except KeyboardInterrupt:
        final_code = 130
        summary["diagnostics"].append("CANCELLED")
    except SystemExit as exc:
        final_code = 3 if exc.code == 3 else 4
        summary["diagnostics"].append("LOCK_CONTENTION" if exc.code == 3 else "LOCK_ERROR")
    except Exception as exc:
        final_code = 4
        summary["diagnostics"].append(sanitize_text(exc))
    finally:
        if lock is not None:
            try:
                lock_release(lock)
            except Exception as exc:
                final_code = 4
                summary["diagnostics"].append(
                    sanitize_text(f"lock release failed: {exc}")
                )
        summary.update(
            {
                "finished_at": utc_now(),
                "duration_seconds": round(time.monotonic() - started, 3),
                "exit_code": final_code,
            }
        )
        if summary_path is not None:
            try:
                atomic_json(summary_path, summary)
            except Exception as exc:
                final_code = 4
                summary["exit_code"] = final_code
                summary["diagnostics"].append(
                    sanitize_text(f"summary persistence failed: {exc}")
                )
    return final_code, sanitize(summary)


def main(argv: list[str] | None = None) -> None:
    code, summary = run(parse_args(argv))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
