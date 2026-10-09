#!/usr/bin/env python3
"""Canonical bounded AgroSat satellite collection entrypoint.

This command owns orchestration only. NDVI and multi-index persistence remain
behind their existing, independently idempotent child collectors.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
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

from services import acquisition_gate as gate
from services.collection_failure import canonical_failure_category
from services.collector_locking import acquire_lock, release_lock
from services.collector_status import (
    PROVIDER_COUNTER_FIELDS,
    STATUS_SCHEMA_VERSION,
    batch_record,
    summarize_provider_batches,
)
from services.provider_governor import (
    CYCLE_FAILURE_CATEGORIES,
    RUN_TERMINAL_CATEGORIES,
    ProviderEnvironment,
    ProviderPolicy,
    ProviderPolicyError,
    run_report,
    timeout_contract,
)


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
# Windows system error codes of a sharing collision on a file replacement.
ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
# Pauses between attempts to replace a published file that a reader or a
# scanner holds open: doubling from 5 ms to 80 ms, then every 80 ms, so 15
# attempts within 0.875 s. A reader holds a status file for one bounded read;
# the flat tail keeps finding such a gap even while readers keep returning.
REPLACE_RETRY_DELAYS_SECONDS = (0.005, 0.01, 0.02, 0.04, *(0.08,) * 10)
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


def transient_replace_error(error: OSError, destination: Path) -> bool:
    """Whether a failed replacement is a collision that clears by itself.

    Python opens files on Windows without FILE_SHARE_DELETE. While any reader
    holds the destination open (services.health reads status files that
    way), replacing it fails with ERROR_ACCESS_DENIED; while another process,
    such as a scanner, holds the new temporary file, with
    ERROR_SHARING_VIOLATION. A directory or read-only destination also fails
    with ERROR_ACCESS_DENIED and never clears, so that code counts only while
    the destination is a regular, writable file. An access-control denial on
    the file itself looks the same; it is raised once the schedule is spent.
    Other platforms replace an open file without error, so nothing is retried
    there.
    """
    code = getattr(error, "winerror", None)
    if code == ERROR_SHARING_VIOLATION:
        return True
    if code != ERROR_ACCESS_DENIED:
        return False
    try:
        state = os.stat(destination)
    except OSError:
        return False
    return stat.S_ISREG(state.st_mode) and not (
        getattr(state, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_READONLY
    )


def replace_file(
    source: str,
    destination: Path,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Atomically replace ``destination`` with ``source``.

    A transient collision is retried on REPLACE_RETRY_DELAYS_SECONDS with the
    same source file; the destination keeps its previous complete content until
    the one replacement that succeeds. Any other error, or a collision that
    outlasts the schedule, is raised.
    """
    for delay in REPLACE_RETRY_DELAYS_SECONDS:
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            if not transient_replace_error(error, destination):
                raise
        sleep(delay)
    os.replace(source, destination)


def atomic_json(
    path: Path,
    value: dict[str, Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
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
        replace_file(temporary, path, sleep=sleep)
    except BaseException:
        # Also when interrupted during a pause between attempts.
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def persistence_failure(label: str, error: BaseException) -> str:
    """Why publishing ``label`` failed: the error and its code, never a path.

    An OSError's text carries the full paths of both files of a replacement;
    the directories are known from the configuration, so only the name of the
    file concerned is kept.
    """
    if not isinstance(error, OSError):
        return sanitize_text(f"{label} persistence failed: {type(error).__name__}: {error}", 500)
    code = getattr(error, "winerror", None)
    target = error.filename2 if error.filename2 is not None else error.filename
    parts = [
        type(error).__name__,
        f"[WinError {code}]"
        if code is not None
        else f"[Errno {error.errno}]"
        if error.errno is not None
        else "",
        error.strerror if error.strerror is not None else str(error),
        f"({os.path.basename(os.fsdecode(target))})"
        if isinstance(target, (str, bytes, os.PathLike))
        else "",
    ]
    detail = " ".join(part for part in parts if part)
    return sanitize_text(f"{label} persistence failed: {detail}", 500)


def classify_failure(summary: dict[str, Any]) -> str | None:
    """Name why the cycle failed, in the vocabulary the database stores.

    The condition is recognised below in the collector's own terms; the label
    is then resolved through services.collection_failure, which is the only
    place that knows what satellite_collection_runs will accept.
    """
    return canonical_failure_category(_failure_condition(summary))


def _failure_condition(summary: dict[str, Any]) -> str | None:
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
    if any(child.get("failure_category") == "rate_limited" for child in children):
        # TASK_251: a short-window throttle that outlasted the bounded retry budget.
        return "rate_limited"
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
        "schema_version": STATUS_SCHEMA_VERSION,
        "run_id": summary["run_id"],
        "mode": summary.get("mode"),
        "status": status,
        "started_at": summary["started_at"],
        "finished_at": None if running else summary.get("finished_at"),
        "duration_seconds": None if running else summary.get("duration_seconds"),
        "exit_code": exit_code,
        "failure_category": None if running else classify_failure(summary),
        # One bounded summary per provider path, whatever the field count;
        # every batch child stays in the run's collector_summary.json. run()
        # records only valid batches, so nothing is left out of a real run.
        "providers": summarize_provider_batches(
            record
            for record in map(batch_record, summary.get("children", []))
            if record is not None
        ),
    }
    report = summary.get("acquisition_gate")
    if isinstance(report, dict):
        # TASK_253, optional and only with the gate enabled: a skipped cycle is visible
        # as such, next to an empty provider list, instead of passing for a collection.
        snapshot["acquisition_gate"] = {
            "outcome": report.get("outcome"),
            "statistical_collection": report.get("statistical_collection"),
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
    # TASK_251 provider pacing and HTTP 429 handling. The defaults are the production
    # policy (the Scheduled Task passes none of these); bounds: services.provider_governor.
    defaults = ProviderPolicy()
    parser.add_argument("--provider-min-interval-seconds", type=float, default=defaults.min_interval_seconds,
                        help="Minimum spacing of Statistical requests across all collector children (1-120).")
    parser.add_argument("--provider-oauth-min-interval-seconds", type=float, default=defaults.oauth_min_interval_seconds,
                        help="Minimum spacing of OAuth token requests (0-60).")
    parser.add_argument("--provider-429-max-retries", type=int, default=defaults.max_retries,
                        help="HTTP 429 retries per provider request (0-10).")
    parser.add_argument("--provider-429-retry-budget-seconds", type=float, default=defaults.retry_budget_seconds,
                        help="Total HTTP 429 wait per provider request (10-1800).")
    parser.add_argument("--provider-429-max-retry-after-seconds", type=float, default=defaults.max_retry_after_seconds,
                        help="A longer provider Retry-After is treated as a suspected quota (30-3600).")
    # TASK_253 acquisition-aware preflight. Off unless --acquisition-gate is passed; the
    # Scheduled Task passes none of these, so the production cycle is unchanged.
    parser.add_argument("--acquisition-gate", action="store_true",
                        help="Skip the Statistical stages when a complete Catalog search proves that no "
                             "acquisition changed since a verified complete cycle (apply mode only).")
    parser.add_argument("--acquisition-gate-reconcile-days", type=int, default=gate.DEFAULT_RECONCILE_DAYS,
                        help="Force a full collection when the last verified one is this many days old (1-7).")
    parser.add_argument("--acquisition-gate-policy", choices=gate.POLICIES, default=gate.DEFAULT_POLICY,
                        help="strict (default): any acquisition leaving the window means a collection; "
                             "footprint: needs live qualification first (see services/acquisition_gate.py).")
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
    defaults = ProviderPolicy()
    try:
        provider_policy = ProviderPolicy(
            min_interval_seconds=getattr(args, "provider_min_interval_seconds", defaults.min_interval_seconds),
            oauth_min_interval_seconds=getattr(args, "provider_oauth_min_interval_seconds", defaults.oauth_min_interval_seconds),
            max_retries=getattr(args, "provider_429_max_retries", defaults.max_retries),
            retry_budget_seconds=getattr(args, "provider_429_retry_budget_seconds", defaults.retry_budget_seconds),
            max_retry_after_seconds=getattr(args, "provider_429_max_retry_after_seconds", defaults.max_retry_after_seconds),
        )
    except ProviderPolicyError as exc:
        raise ContractError(f"provider policy: {exc}") from exc
    # TASK_251A: a retry budget that cannot finish inside one child's field timeout would
    # be cut short by the runner's kill; refuse it before any provider request. A dry run
    # makes no provider request, so it only reports the contract.
    try:
        contract = timeout_contract(provider_policy, args.field_timeout_seconds)
    except ProviderPolicyError as exc:
        if selected_mode(args) != "dry-run":
            raise ContractError(f"provider timeout contract: {exc}") from exc
        contract = {"field_timeout_seconds": args.field_timeout_seconds, "violation": sanitize_text(exc, 300)}
    acquisition_gate = validate_acquisition_gate(args)
    return {
        "acquisition_gate": acquisition_gate,
        "provider_policy": provider_policy,
        "timeout_contract": contract,
        "mode": selected_mode(args),
        "indices": indices,
        "field_ids": field_ids,
        "date_from": start,
        "date_to": end,
        "output_dir": external_directory(args.output_dir, "--output-dir"),
        "state_dir": external_directory(args.state_dir, "--state-dir"),
        "lock_dir": external_directory(args.lock_dir, "--lock-dir"),
    }


def validate_acquisition_gate(args: argparse.Namespace) -> dict[str, Any]:
    """TASK_253: the gate is for the rolling all-fields cycle only, with a reconciliation
    interval that leaves a missed acquisition inside the lookback for a second attempt."""
    enabled = bool(getattr(args, "acquisition_gate", False))
    reconcile_days = getattr(args, "acquisition_gate_reconcile_days", gate.DEFAULT_RECONCILE_DAYS)
    policy = getattr(args, "acquisition_gate_policy", gate.DEFAULT_POLICY)
    if type(reconcile_days) is not int or not gate.MIN_RECONCILE_DAYS <= reconcile_days <= gate.MAX_RECONCILE_DAYS:
        raise ContractError("--acquisition-gate-reconcile-days must be between 1 and 7")
    if policy not in gate.POLICIES:
        raise ContractError("--acquisition-gate-policy is unknown")
    if enabled:
        if not args.all_active_fields or args.date_from or args.date_to:
            raise ContractError(
                "--acquisition-gate requires --all-active-fields and the rolling --lookback-days window"
            )
        if args.lookback_days < 2 * reconcile_days:
            raise ContractError("--lookback-days must be at least twice --acquisition-gate-reconcile-days")
    return {"enabled": enabled, "reconcile_days": reconcile_days, "policy": policy}


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


def resolve_acquisition_scope() -> gate.Scope:
    """TASK_253: active fields, a fingerprint of every geometry and their extent, read in
    one READ ONLY transaction. An active field without a usable geometry has no
    fingerprint, so the gate never trusts such a scope."""
    from database import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        rows = db.execute(text(
            "SELECT id, CASE WHEN geometry IS NULL OR ST_IsEmpty(geometry) THEN NULL "
            "ELSE md5(ST_AsBinary(geometry)) END FROM fields WHERE is_active=true ORDER BY id"
        )).fetchall()
        extent = db.execute(text(
            "SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e), hull FROM ("
            "SELECT ST_Extent(geometry) AS e, ST_AsGeoJSON(ST_ConvexHull(ST_Collect(geometry))) AS hull "
            "FROM fields WHERE is_active=true AND geometry IS NOT NULL AND NOT ST_IsEmpty(geometry)) s"
        )).first()
    finally:
        db.rollback()
        db.close()
    if extent is None:
        raise gate.GateError("active-field extent is unavailable")
    return gate.scope_from_rows([(int(row[0]), row[1]) for row in rows], extent[:4], extent[4])


def provider_identity() -> tuple[str, str]:
    """The configured provider preset, without credentials."""
    from config import settings
    from services.sentinel_provider import resolve_sentinel_provider

    endpoints = resolve_sentinel_provider(settings.sentinel_hub_provider)
    return endpoints.name, endpoints.endpoint_class


def catalog_search_factory(plan: dict[str, Any], run_id: str, deadline: float):
    """TASK_253: an in-process Catalog client whose token and search requests go through
    the run's shared provider governor (pacing, 429 cooldown, deadline), stage ``catalog``."""
    from services.provider_governor import ProviderGovernor
    from services.satellite import get_satellite_service
    from services.satellite_safety import require_real_service

    def factory():
        service = get_satellite_service()
        require_real_service(service)
        governor = ProviderGovernor(plan["provider_policy"], plan["state_dir"], run_id=run_id,
                                    stage="catalog", deadline=deadline)
        service.provider_governor = governor
        return service.get_catalog_response, governor
    return factory


def build_acquisition_gate(plan: dict[str, Any], args: argparse.Namespace, run_id: str,
                           cycle_deadline: float, *,
                           scope_resolver: Callable[[], gate.Scope] = resolve_acquisition_scope,
                           search_factory: Callable[[], Any] | None = None,
                           identity: Callable[[], tuple[str, str]] = provider_identity,
                           backend_root: Path = BACKEND) -> gate.GateRun:
    """The gate of one applying cycle. The keyword arguments exist for tests; the
    collector always uses the defaults."""
    deadline = min(time.time() + gate.DISCOVERY_BUDGET_SECONDS, cycle_deadline)
    return gate.GateRun(
        state_dir=plan["state_dir"], output_dir=plan["output_dir"], run_id=run_id, indices=plan["indices"],
        lookback_days=args.lookback_days, date_from=plan["date_from"], date_to=plan["date_to"],
        reconcile_days=plan["acquisition_gate"]["reconcile_days"], policy=plan["acquisition_gate"]["policy"],
        backend_root=backend_root, provider_identity=identity, scope_resolver=scope_resolver,
        search_factory=search_factory or catalog_search_factory(plan, run_id, deadline), write_json=atomic_json,
        history_files=(LAST_SUCCESS_FILENAME, LAST_FAILURE_FILENAME),
        retry_files=("ndvi_state.json", "multi_state.json"),
    )


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
        # Scheduled beats that could not be persisted, and why the last one
        # could not.
        self.missed_beats = 0
        self.last_miss: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="collector-heartbeat", daemon=True)

    def _publish(self) -> None:
        atomic_json(self.path, {
            "schema_version": 1, "run_id": self.run_id, "pid": os.getpid(),
            "release_commit": self.release_commit, "heartbeat_at": utc_now(),
        })

    def _beat(self) -> None:
        """One scheduled beat.

        A beat that cannot be persisted must not end the heartbeat: the next
        one is due at the same cadence, and stop() decides whether the final
        one persisted.
        """
        try:
            self._publish()
        except Exception as exc:
            self.missed_beats += 1
            self.last_miss = persistence_failure("heartbeat", exc)

    def _loop(self) -> None:
        while not self._stop.wait(30):
            self._beat()

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
    return value if value in CYCLE_FAILURE_CATEGORIES else None


#: TASK_251A: the completion evidence every batch runner writes (cycle_summary.json).
#: Both runners (scripts/run_ndvi_collection_cycle.py, run_multi_index_collection_cycle.py)
#: record these keys on every path that exits 0 or 1.
EVIDENCE_ID_LISTS = ("selected_field_ids", "successful_field_ids", "failed_field_ids", "unattempted_field_ids")
EVIDENCE_COUNTS = ("success_count", "failure_count", "unattempted_count", "skipped_existing_count",
                   "quality_blocked_count", "exit_code")
MAX_UNVERIFIED_REPORTED = 50


def cycle_summary(provider_output: Path) -> tuple[dict[str, Any] | None, str | None]:
    """The batch's cycle summary, or why it cannot be used as evidence."""
    summaries = list(provider_output.glob("cycle_*/cycle_summary.json"))
    if not summaries:
        return None, "missing_summary"
    if len(summaries) != 1:
        return None, "multiple_summaries"
    try:
        if summaries[0].stat().st_size > MAX_CYCLE_SUMMARY_BYTES:
            return None, "oversized_summary"
        payload = json.loads(summaries[0].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "unreadable_summary"
    if not isinstance(payload, dict):
        return None, "unreadable_summary"
    return payload, None


def verify_batch(batch: list[int], exit_code: int, provider_output: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Completion evidence of one batch, reconciled with the planned field IDs.

    An exit code alone proves nothing: the summary must account for exactly the planned
    fields (successful + failed + unattempted, disjoint), with matching counts and exit
    code, and a clean exit must leave no field failed or unattempted.
    """
    payload, problem = cycle_summary(provider_output)
    if problem:
        return None, problem
    lists: dict[str, list[int]] = {}
    for key in EVIDENCE_ID_LISTS:
        value = payload.get(key)
        if (not isinstance(value, list) or len(value) > MAX_FIELDS
                or any(type(item) is not int or item <= 0 for item in value) or len(set(value)) != len(value)):
            return None, f"incomplete_summary:{key}"
        lists[key] = value
    for key in EVIDENCE_COUNTS:
        value = payload.get(key)
        if type(value) is not int or not 0 <= value <= 10_000_000:
            return None, f"incomplete_summary:{key}"
    planned = set(batch)
    if len(lists["selected_field_ids"]) != len(batch) or set(lists["selected_field_ids"]) != planned:
        return None, "field_set_mismatch"
    done, failed, todo = (set(lists[key]) for key in EVIDENCE_ID_LISTS[1:])
    if done & failed or done & todo or failed & todo or (done | failed | todo) != planned:
        return None, "outcome_partition_mismatch"
    for count_key, list_key in (("success_count", "successful_field_ids"), ("failure_count", "failed_field_ids"),
                                ("unattempted_count", "unattempted_field_ids")):
        if payload[count_key] != len(lists[list_key]):
            return None, f"count_mismatch:{count_key}"
    if payload["exit_code"] != exit_code:
        return None, "exit_code_mismatch"
    if exit_code == 0 and (failed or todo):
        return None, "exit_zero_with_unfinished_fields"
    return payload, None


def _field_ids(value: Any) -> list[int]:
    return [item for item in value if type(item) is int and item > 0] if isinstance(value, list) else []


def stage_report(batches: list[list[int]], ran: dict[int, tuple[Path, int]]) -> dict[str, Any]:
    """One provider stage: field outcomes and every field ID it did not finish (TASK_251/251A).

    A field counts as finished only with verifiable evidence: a batch that never ran, or
    whose summary is missing, unreadable, incomplete or contradicts the plan, leaves all
    its fields unfinished, whatever its exit code. Nothing is silently dropped.
    """
    report = {"batches_planned": len(batches), "batches_run": len(ran), "attempted": 0, "completed": 0,
              "skipped_existing": 0, "quality_blocked": 0, "failed": 0}
    unfinished: list[int] = []
    unverified: list[dict[str, Any]] = []
    for number, batch in enumerate(batches, start=1):
        if number not in ran:
            unfinished.extend(batch)
            continue
        output, exit_code = ran[number]
        payload, problem = verify_batch(batch, exit_code, output)
        if problem:
            unfinished.extend(batch)
            unverified.append({"batch": number, "exit_code": exit_code, "reason": problem})
            continue
        report["completed"] += payload["success_count"]
        report["failed"] += payload["failure_count"]
        report["attempted"] += payload["success_count"] + payload["failure_count"]
        report["skipped_existing"] += payload["skipped_existing_count"]
        report["quality_blocked"] += payload["quality_blocked_count"]
        unfinished.extend(_field_ids(payload["failed_field_ids"]))
        unfinished.extend(_field_ids(payload["unattempted_field_ids"]))
    report["unfinished_field_ids"] = list(dict.fromkeys(unfinished))
    report["unfinished_count"] = len(report["unfinished_field_ids"])
    report["unverified_batch_count"] = len(unverified)
    report["unverified_batches"] = unverified[:MAX_UNVERIFIED_REPORTED]
    return report


def run_status(final_code: int, stage_stops: dict[str, str], stages: dict[str, Any], *,
               statistical_skipped: bool = False) -> str:
    """SUCCESS only for a complete cycle; never for unfinished work (TASK_251).

    TASK_253: a cycle whose Statistical stages the acquisition gate skipped is
    NO_NEW_ACQUISITIONS, never SUCCESS.
    """
    if final_code not in (0, 1):
        return "FAILED"
    if stage_stops:
        return "DEGRADED"
    if statistical_skipped:
        return gate.OUTCOME_SKIP if final_code == 0 else "PARTIAL"
    if final_code == 1 or any(stage.get("unfinished_count") or stage.get("unverified_batch_count")
                              for stage in stages.values()):
        return "PARTIAL"
    return "SUCCESS"


def gate_counters(summary: dict[str, Any]) -> dict[str, Any]:
    """TASK_253: the gate outcome in the run row's counters, only when the gate ran."""
    report = summary.get("acquisition_gate")
    if not isinstance(report, dict) or not report.get("evaluated"):
        return {}
    requests = report.get("requests") or {}
    return {"acquisition_gate": {
        "outcome": report.get("outcome"),
        "statistical_collection": report.get("statistical_collection"),
        "reasons": list(report.get("reasons") or [])[:10],
        "catalog_requests": requests.get("catalog", 0),
        "oauth_requests": requests.get("oauth", 0),
    }}


def statistical_skipped(summary: dict[str, Any]) -> bool:
    report = summary.get("acquisition_gate")
    return isinstance(report, dict) and report.get("statistical_collection") == "skipped"


def provider_status(summary: dict[str, Any]) -> str:
    if summary.get("exit_code") == 0 and statistical_skipped(summary):
        # TASK_253: the Statistical provider was not called; the database vocabulary
        # (ck_collection_runs_provider) already names exactly that.
        return "not_called"
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
    gate_factory: Callable[..., Any] = build_acquisition_gate,
) -> tuple[int, dict[str, Any]]:
    started = time.monotonic()
    started_epoch = time.time()
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
    provider_environment = contextlib.ExitStack()
    plan: dict[str, Any] | None = None
    stage_plan: tuple[list[str], list[list[int]]] | None = None
    stage_runs: dict[str, dict[int, tuple[Path, int]]] = {}
    stage_stops: dict[str, str] = {}
    gate_run: gate.GateRun | None = None
    try:
        plan = validate(args)
        # TASK_251A: the cycle budget as an absolute deadline every batch runner can read.
        cycle_deadline = started_epoch + args.cycle_timeout_seconds
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
        gate_stop = None
        if plan["acquisition_gate"]["enabled"]:
            # TASK_253: before any Statistical request, decide whether this cycle's
            # collection is provably redundant. Only an applying cycle persists
            # observations, so only it may skip or touch the checkpoint.
            if plan["mode"] != "apply":
                summary["acquisition_gate"] = {"enabled": True, "evaluated": False,
                                               "reason": f"{plan['mode']}_mode"}
            else:
                try:
                    gate_run = gate_factory(plan, args, run_id, cycle_deadline)
                except Exception as exc:
                    # The gate can only ever remove redundant work: without it, collect.
                    summary["acquisition_gate"] = {"enabled": True, "evaluated": False,
                                                   "reason": f"gate_unavailable:{type(exc).__name__}"}
                    decision = None
                else:
                    decision = gate_run.evaluate(active_ids)
                    summary["acquisition_gate"] = gate_run.report()
                if decision is None:
                    pass
                elif decision.outcome == gate.OUTCOME_SKIP:
                    providers = []
                elif decision.outcome == gate.OUTCOME_STOP:
                    providers = []
                    gate_stop = decision.stop_category
                    stage_stops["catalog"] = gate_stop
                    summary["diagnostics"].append(
                        f"acquisition gate provider stop category={gate_stop}: no Statistical request was sent"
                    )
        provider_env = None
        if providers:
            # TASK_251: one provider policy, one shared pacer and one 429 cooldown for every
            # child of this run, across both stages.
            provider_env = provider_environment.enter_context(
                ProviderEnvironment(plan["provider_policy"], plan["state_dir"], run_id, providers[0],
                                    cycle_deadline=cycle_deadline)
            )
        stage_plan = (providers, field_batches)
        final_code = 0 if gate_stop is None else 1
        stop = False
        for provider in providers:
            provider_env.set_stage(provider)
            stage_runs[provider] = {}
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
                stage_runs[provider][batch_number] = (provider_output, code)
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
                category = summary["children"][-1]["failure_category"]
                if code == 1 and category in RUN_TERMINAL_CATEGORIES:
                    # Provider-wide: authentication refused or a quota is suspected.
                    stage_stops[provider] = category
                    stop = True
                    break
                if code == 1 and category == "rate_limited":
                    # TASK_251: a throttle outlasted the bounded retry budget. Stop this
                    # stage only; the next stage still runs, after the shared cooldown.
                    stage_stops[provider] = category
                    break
            if stop:
                break
        # TASK_251A: completion is proven by evidence, never by an exit code alone.
        summary["stages"] = {
            provider: stage_report(field_batches, stage_runs.get(provider, {}))
            for provider in providers
        }
        summary["stage_stops"] = stage_stops
        unverified = sum(stage["unverified_batch_count"] for stage in summary["stages"].values())
        if unverified and final_code == 0:
            final_code = 1
            summary["diagnostics"].append(
                f"batch completion evidence missing or invalid for {unverified} batch(es); "
                "their fields are reported as unfinished"
            )
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
        provider_environment.close()
        if stage_plan is not None and "stages" not in summary:
            stage_providers, stage_batches = stage_plan
            summary["stages"] = {
                provider: stage_report(stage_batches, stage_runs.get(provider, {}))
                for provider in stage_providers
            }
            summary["stage_stops"] = stage_stops
        if plan is not None and plan["mode"] != "dry-run":
            summary["provider_resilience"] = {
                "policy": json.loads(plan["provider_policy"].to_json()),
                "timeout_contract": plan["timeout_contract"],
                "provider_limit": "UNKNOWN",
                "ledger": run_report(plan["state_dir"], run_id),
            }
        # The heartbeat and status files are operational state, published
        # around the collection and never into it. Failing to persist them is
        # still an operational failure (exit code 4), but it is applied only
        # after the database run is terminalized with its own outcome.
        state_persistence_failed = False
        if heartbeat_publisher is not None:
            try:
                heartbeat_publisher.stop()
            except Exception as exc:
                state_persistence_failed = True
                summary["diagnostics"].append(persistence_failure("heartbeat", exc))
            if heartbeat_publisher.missed_beats:
                summary["diagnostics"].append(
                    f"{heartbeat_publisher.last_miss} "
                    f"({heartbeat_publisher.missed_beats} scheduled beat(s) missed)"
                )
        if apply_run is not None:
            from services.autonomous_monitoring import finish_apply_run

            provisional = {**summary, "exit_code": final_code}
            status = provider_status(provisional)
            try:
                from config import settings as runtime_settings
                from services.autonomous_monitoring import (
                    detect_observation_candidates, reconcile_pixel_candidates, refresh_freshness,
                )
                freshness_count = refresh_freshness(
                    apply_run,
                    last_outcome="provider_degraded" if status == "degraded" else
                    "quality_blocked" if status == "quality_blocked" else None,
                )
                # Accepted observations are persisted and freshness is current:
                # the deterministic producer records this cycle's candidates,
                # then promotion opens canonical inspections for them. The
                # deployment enables the producer explicitly (see config).
                detection = {"enabled": False, "assessed_fields": 0, "observation_candidates": 0}
                if final_code in {0, 1} and runtime_settings.observation_detection_enabled:
                    detection = {"enabled": True, **detect_observation_candidates(apply_run)}
                anomaly = reconcile_pixel_candidates(apply_run) if final_code in {0, 1} else {
                    "inserted_candidates": 0, "automatic_inspections": 0,
                    "spike_guard_triggered": False,
                }
                verification = {
                    "eligible": 0, "improved": 0, "unchanged": 0, "worsened": 0,
                    "pending_quality_provider": 0, "conflicts": 0, "failures": 0,
                    "reopened": 0,
                }
                if final_code in {0, 1}:
                    from database import SessionLocal
                    from services.closed_loop_agronomy import reconcile_pending
                    verification = reconcile_pending(SessionLocal, limit=100)
                summary["monitoring"] = {
                    "freshness_rows": freshness_count, "detection": detection, **anomaly,
                    "verifications": verification,
                }
            except Exception as exc:
                final_code = 4
                summary["diagnostics"].append(sanitize_text(f"monitoring reconciliation failed: {exc}"))
                provisional = {**summary, "exit_code": final_code}
                try:
                    apply_run.session.rollback()
                except Exception:
                    pass
            # A monitoring failure must still terminalize the run: a row left
            # 'running' reads as an in-flight cycle to the reconciler.
            try:
                finish_apply_run(
                    apply_run, exit_code=final_code, provider_status=status,
                    counters={**aggregate_counters(summary), **summary.get("monitoring", {}),
                              **gate_counters(summary)},
                    failure_category=classify_failure(provisional),
                )
            except Exception as exc:
                final_code = 4
                summary["diagnostics"].append(sanitize_text(f"collection run finish failed: {exc}"))
        if gate_run is not None and gate_run.decision is not None:
            # TASK_253: the checkpoint is written while the canonical lock is still held,
            # from the outcome this cycle has reached so far; a later persistence failure
            # breaks the chain the next cycle checks, so it can only cost a skip.
            outcome_code = 4 if state_persistence_failed else final_code
            try:
                gate_run.finalize(final_code=outcome_code, run_status=run_status(
                    outcome_code, stage_stops, summary.get("stages", {}),
                    statistical_skipped=statistical_skipped(summary)))
            except Exception as exc:
                summary["diagnostics"].append(persistence_failure("acquisition checkpoint", exc))
            if isinstance(summary.get("acquisition_gate"), dict):
                summary["acquisition_gate"]["checkpoint_after"] = gate_run.checkpoint_after
        if lock is not None:
            try:
                lock_release(lock)
            except Exception as exc:
                final_code = 4
                summary["diagnostics"].append(
                    sanitize_text(f"lock release failed: {exc}")
                )
        if state_persistence_failed:
            final_code = 4
        summary.update(
            {
                "finished_at": utc_now(),
                "duration_seconds": round(time.monotonic() - started, 3),
                "exit_code": final_code,
                "run_status": run_status(final_code, stage_stops, summary.get("stages", {}),
                                         statistical_skipped=statistical_skipped(summary)),
            }
        )
        summary_persisted = False
        if summary_path is not None:
            try:
                atomic_json(summary_path, summary)
                summary_persisted = True
            except Exception as exc:
                final_code = 4
                summary["exit_code"] = final_code
                summary["run_status"] = run_status(final_code, stage_stops, summary.get("stages", {}),
                                                   statistical_skipped=statistical_skipped(summary))
                summary["diagnostics"].append(persistence_failure("summary", exc))
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
                summary["run_status"] = run_status(final_code, stage_stops, summary.get("stages", {}),
                                                   statistical_skipped=statistical_skipped(summary))
                summary["diagnostics"].append(persistence_failure("latest status", exc))
                if summary_persisted:
                    # The run's forensic record states the failure as well.
                    try:
                        atomic_json(summary_path, summary)
                    except Exception as error:
                        summary["diagnostics"].append(persistence_failure("summary", error))
    return final_code, sanitize(summary)


def main(argv: list[str] | None = None) -> None:
    code, summary = run(parse_args(argv))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
