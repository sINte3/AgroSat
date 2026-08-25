#!/usr/bin/env python3
"""Canonical bounded AgroSat satellite collection entrypoint.

This command owns orchestration only. NDVI and multi-index persistence remain
behind their existing, independently idempotent child collectors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
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
MAX_ACTIVE_FIELDS = 10_000
MAX_DATE_DAYS = 30
MAX_ATTEMPTS = 5
MAX_TIMEOUT_SECONDS = 900
MAX_CYCLE_TIMEOUT_SECONDS = 21600
LATEST_STATUS_FILENAME = "collector_latest_status.json"
LAST_SUCCESS_FILENAME = "collector_last_success.json"
LAST_FAILURE_FILENAME = "collector_last_failure.json"
HEARTBEAT_FILENAME = "collector_heartbeat.json"
MAX_CYCLE_SUMMARY_BYTES = 1024 * 1024
PROVIDER_COUNTER_FIELDS = (
    "success_count",
    "failure_count",
    "inserted_count",
    "skipped_existing_count",
    "quality_blocked_count",
    "timeout_count",
)
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
        prefix=".tmp-",
        suffix=".json",
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


def classify_failure(summary: dict[str, Any]) -> str | None:
    exit_code = summary.get("exit_code")
    if exit_code == 0:
        return None
    if exit_code == 130:
        return "cancelled"
    if exit_code == 3:
        return "lock_contention"
    children = summary.get("children", [])
    if any(bool(child.get("timed_out")) for child in children):
        return "network"
    searchable = " ".join(
        [
            *(str(item) for item in summary.get("diagnostics", [])),
            *(
                str(child.get(key, ""))
                for child in children
                for key in ("stdout", "stderr", "failure_category")
            ),
        ]
    ).lower()
    if any(marker in searchable for marker in ("authentication", "invalid_client", "unauthorized", "401", "403")):
        return "auth"
    if any(marker in searchable for marker in ("quota", "rate limit", "429")):
        return "quota"
    if any(marker in searchable for marker in ("cloud", "quality_blocked")):
        return "cloud"
    if any(
        marker in searchable
        for marker in ("timeout", "connection", "network", "dns")
    ):
        return "network"
    if exit_code == 2:
        return "contract"
    if exit_code == 1:
        return "partial"
    return "operational"


def operational_snapshot(
    summary: dict[str, Any],
    *,
    running: bool = False,
) -> dict[str, Any]:
    exit_code = None if running else summary.get("exit_code")
    status = (
        "running"
        if running
        else "succeeded"
        if exit_code == 0
        else "cancelled"
        if exit_code == 130
        else "failed"
    )
    snapshot = {
        "schema_version": 1,
        "run_id": summary["run_id"],
        "mode": summary.get("mode"),
        "status": status,
        "started_at": summary["started_at"],
        "finished_at": None if running else summary.get("finished_at"),
        "duration_seconds": None if running else summary.get("duration_seconds"),
        "exit_code": exit_code,
        "failure_category": None if running else classify_failure(summary),
        "providers": [
            {
                "provider": child.get("provider"),
                "exit_code": child.get("exit_code"),
                "timed_out": bool(child.get("timed_out")),
                "counters": child.get("counters"),
            }
            for child in summary.get("children", [])
        ],
    }
    return sanitize(snapshot)


def provider_counters(provider_output: Path) -> dict[str, int] | None:
    summaries = list(provider_output.glob("cycle_*/cycle_summary.json"))
    if len(summaries) != 1:
        return None
    path = summaries[0]
    try:
        if path.stat().st_size > MAX_CYCLE_SUMMARY_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    counters: dict[str, int] = {}
    for field in PROVIDER_COUNTER_FIELDS:
        value = payload.get(field)
        if type(value) is not int or not 0 <= value <= 10_000_000:
            return None
        counters[field] = value
    return counters


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
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--retry-base-seconds", type=int, default=2)
    parser.add_argument("--field-timeout-seconds", type=int, default=180)
    parser.add_argument("--cycle-timeout-seconds", type=int, default=21600)
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


def resolve_active_field_ids() -> list[int]:
    """Read the exact active-field scope once; never rely on rotating child state."""
    from database import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        values = [int(row[0]) for row in db.execute(
            text("SELECT id FROM fields WHERE is_active=true ORDER BY id")
        ).fetchall()]
    finally:
        db.close()
    if not values or len(values) > MAX_ACTIVE_FIELDS or len(values) != len(set(values)):
        raise ContractError("active-field scope is empty, duplicated, or exceeds the safety cap")
    return values


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


class HeartbeatPublisher:
    """Atomically publish liveness while a provider child is running."""

    def __init__(self, path: Path, run_id: str, release_commit: str):
        self.path = path
        self.run_id = run_id
        self.release_commit = release_commit
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="collector-heartbeat", daemon=True)

    def _publish(self) -> None:
        atomic_json(self.path, {
            "schema_version": 1, "run_id": self.run_id, "pid": os.getpid(),
            "release_commit": self.release_commit, "heartbeat_at": utc_now(),
        })

    def _loop(self) -> None:
        while not self._stop.wait(30):
            self._publish()

    def start(self) -> None:
        self._publish()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        self._publish()


def aggregate_counters(summary: dict[str, Any]) -> dict[str, int]:
    counters = {field: 0 for field in PROVIDER_COUNTER_FIELDS}
    for child in summary.get("children", []):
        values = child.get("counters") or {}
        for field in counters:
            value = values.get(field)
            if type(value) is int and value >= 0:
                counters[field] += value
    return counters


def provider_failure_category(provider_output: Path) -> str | None:
    summaries = list(provider_output.glob("cycle_*/cycle_summary.json"))
    if len(summaries) != 1:
        return None
    try:
        path = summaries[0]
        if path.stat().st_size > MAX_CYCLE_SUMMARY_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8")).get(
            "provider_failure_category"
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if value in {"authentication", "quota", "network", "timeout", "cloud", "provider_error"} else None


def provider_status(summary: dict[str, Any]) -> str:
    failure = classify_failure(summary)
    counters = aggregate_counters(summary)
    if failure in {"auth", "quota", "network", "operational", "partial"}:
        return "degraded"
    if counters["success_count"] == 0 and counters["quality_blocked_count"] > 0:
        return "quality_blocked"
    if summary.get("exit_code") == 0 and counters["inserted_count"] == 0:
        return "no_scene"
    return "healthy" if summary.get("exit_code") == 0 else "degraded"


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
        "mode": selected_mode(args),
        "started_at": utc_now(),
        "exit_code": 4,
        "diagnostics": [],
        "children": [],
    }
    final_code = 4
    lock = None
    summary_path: Path | None = None
    latest_status_path: Path | None = None
    apply_run = None
    heartbeat_publisher: HeartbeatPublisher | None = None
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
        latest_status_path = plan["output_dir"] / LATEST_STATUS_FILENAME
        atomic_json(
            latest_status_path,
            operational_snapshot(summary, running=True),
        )
        lock = lock_acquire(
            str(plan["lock_dir"] / "canonical_satellite.lock"),
            mutex_name=MUTEX_NAME,
        )
        release_commit = os.environ.get("AGROSAT_RELEASE_COMMIT", "unknown").lower()
        heartbeat_publisher = HeartbeatPublisher(
            plan["state_dir"] / HEARTBEAT_FILENAME, run_id, release_commit,
        )
        heartbeat_publisher.start()
        if plan["mode"] == "apply":
            from config import settings
            from services.autonomous_monitoring import begin_apply_run

            release_commit = os.environ.get(
                "AGROSAT_RELEASE_COMMIT", settings.release_revision,
            ).lower()
            run_key = hashlib.sha256(
                f"{release_commit}|{run_id}|{plan['date_from']}|{plan['date_to']}".encode()
            ).hexdigest()
            apply_run = begin_apply_run(
                run_key=run_key,
                release_commit=release_commit,
                audit_identity=os.environ.get(
                    "AGROSAT_COLLECTOR_AUDIT_IDENTITY",
                    "AgroSat_PROGRAM_R3_SentinelCycle",
                ),
            )
        field_batches = [plan["field_ids"]]
        if args.all_active_fields:
            active_ids = resolve_active_field_ids()
            field_batches = [
                active_ids[offset:offset + args.batch_size]
                for offset in range(0, len(active_ids), args.batch_size)
            ]
        summary["scope"] = {
            "active_field_count": sum(len(batch) for batch in field_batches),
            "batch_size": args.batch_size if args.all_active_fields else len(plan["field_ids"]),
            "batch_count": len(field_batches),
        }
        providers = []
        if "ndvi" in plan["indices"]:
            providers.append("ndvi")
        if any(code in MULTI_INDICES for code in plan["indices"]):
            providers.append("multi")
        final_code = 0
        stop = False
        for provider in providers:
            for batch_number, field_batch in enumerate(field_batches, start=1):
                provider_output = run_dir / f"{provider}_batch_{batch_number:04d}"
                provider_output.mkdir(parents=False, exist_ok=False)
                command = build_child_command(
                    provider, args, {**plan, "field_ids": field_batch}, provider_output,
                )
                remaining = args.cycle_timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    outcome = {"exit_code": 1, "timed_out": True, "stderr": "cycle timeout"}
                else:
                    outcome = sanitize(child_runner(command, max(1, int(remaining))))
                code = outcome.get("exit_code")
                if type(code) is not int or code not in (0, 1, 2, 3, 4):
                    raise ContractError("child returned an invalid exit code")
                summary["children"].append(
                    {
                        "provider": provider, "batch": batch_number,
                        "field_count": len(field_batch), "exit_code": code,
                        "timed_out": bool(outcome.get("timed_out")),
                        "counters": provider_counters(provider_output),
                        "failure_category": provider_failure_category(provider_output),
                        "stdout": outcome.get("stdout", ""),
                        "stderr": outcome.get("stderr", ""),
                    }
                )
                if apply_run is not None:
                    from services.autonomous_monitoring import heartbeat
                    heartbeat(apply_run, aggregate_counters(summary))
                if code in (2, 3, 4):
                    final_code = code; stop = True; break
                if code == 1 and final_code == 0:
                    final_code = code
                if code == 1 and summary["children"][-1]["failure_category"] in {
                    "authentication", "quota",
                }:
                    stop = True
                    break
            if stop:
                break
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
        detail = sanitize_text(exc)
        final_code = 3 if "advisory lock contention" in detail.lower() else 4
        summary["diagnostics"].append(
            "LOCK_CONTENTION" if final_code == 3 else detail
        )
    finally:
        if heartbeat_publisher is not None:
            try:
                heartbeat_publisher.stop()
            except Exception as exc:
                final_code = 4
                summary["diagnostics"].append(sanitize_text(f"heartbeat persistence failed: {exc}"))
        if apply_run is not None:
            try:
                from services.autonomous_monitoring import (
                    finish_apply_run, reconcile_pixel_candidates, refresh_freshness,
                )
                provisional = {**summary, "exit_code": final_code}
                status = provider_status(provisional)
                freshness_count = refresh_freshness(
                    apply_run,
                    last_outcome="provider_degraded" if status == "degraded" else
                    "quality_blocked" if status == "quality_blocked" else None,
                )
                anomaly = reconcile_pixel_candidates(apply_run) if final_code in {0, 1} else {
                    "inserted_candidates": 0, "automatic_inspections": 0,
                    "spike_guard_triggered": False,
                }
                summary["monitoring"] = {"freshness_rows": freshness_count, **anomaly}
                finish_apply_run(
                    apply_run, exit_code=final_code, provider_status=status,
                    counters={**aggregate_counters(summary), **summary["monitoring"]},
                    failure_category=classify_failure(provisional),
                )
            except Exception as exc:
                final_code = 4
                summary["diagnostics"].append(sanitize_text(f"monitoring reconciliation failed: {exc}"))
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
        if latest_status_path is not None:
            try:
                snapshot = operational_snapshot(summary)
                atomic_json(latest_status_path, snapshot)
                history_path = latest_status_path.with_name(
                    LAST_SUCCESS_FILENAME
                    if snapshot["status"] == "succeeded"
                    else LAST_FAILURE_FILENAME
                )
                atomic_json(history_path, snapshot)
            except Exception as exc:
                final_code = 4
                summary["exit_code"] = final_code
                summary["diagnostics"].append(
                    sanitize_text(f"latest status persistence failed: {exc}")
                )
    return final_code, sanitize(summary)


def main(argv: list[str] | None = None) -> None:
    code, summary = run(parse_args(argv))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
