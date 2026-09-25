"""Sanitized liveness, readiness, and collector operational state.

Readiness answers two independent questions:

* Is the database reachable and at exactly the schema revision this code
  requires? That revision is the head of the migration graph shipped with the
  running code (``services.migration_head``). Anything else makes the API
  unready.
* What did the standalone collector last report? The collector's status files
  are read in the formats the collector writes (schema 2, and schema 1 files
  still on disk), strictly and within fixed bounds, and published in one
  shape. Collector state is operational information; it never decides API
  readiness.

Every value published here is validated against a fixed vocabulary or
pattern, or is computed here. No file content, exception text, path or
credential is echoed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import time
from typing import Any, Callable

from sqlalchemy import text

from config import settings
from services.collection_failure import RUN_FAILURE_CATEGORIES
# The reader and the collector share one contract for provider batches and
# their summaries (services/collector_status.py). Its constants are the
# reader's own, so they stay importable from here (PROVIDER_COUNTER_FIELDS,
# for one, is not otherwise used in this module).
from services.collector_status import (
    CHILD_EXIT_CODES,
    COLLECTOR_PROVIDERS,
    MAX_COUNTER_VALUE,
    PROVIDER_COUNTER_FIELDS,
    STATUS_SCHEMA_VERSION,
    batch_record,
    provider_summary,
    summarize_provider_batches,
    valid_counters,
)
from services.migration_head import MigrationHead, expected_migration_head


MAX_STATUS_FILE_BYTES = 64 * 1024
MIN_STALE_AFTER_SECONDS = 60
MAX_STALE_AFTER_SECONDS = 30 * 24 * 60 * 60
# The collector's cycle timeout cap (MAX_CYCLE_TIMEOUT_SECONDS).
MAX_DURATION_SECONDS = 21600
# The collector writes its status on this host's clock. A time further ahead
# than this was not written by the collector, and would read as fresh forever.
MAX_FUTURE_SKEW_SECONDS = 300
STATUS_FILES = {
    "latest": "collector_latest_status.json",
    "last_success": "collector_last_success.json",
    "last_failure": "collector_last_failure.json",
}
RUN_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
REVISION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
ALLOWED_COLLECTOR_STATUSES = {"running", "succeeded", "failed", "cancelled"}
ALLOWED_MODES = {"dry-run", "diagnostic", "apply"}
# The reader accepts exactly what the collector can write and the database can
# store. Any second list here is a third vocabulary, which is the defect this
# indirection exists to prevent; see services/collection_failure.py.
ALLOWED_FAILURE_CATEGORIES = RUN_FAILURE_CATEGORIES
# Status schemas the reader accepts: the one the collector writes, and schema
# 1, which files written before TASK_229 still carry (``schema_version`` 1 or
# absent).
LEGACY_STATUS_SCHEMA_VERSION = 1
# The collector's own limits bound a provider path's batches: at most 10,000
# active fields (MAX_ACTIVE_FIELDS) in batches of at least 2, so at most 5,000
# batches. A schema 1 file lists one ``providers`` entry per provider path per
# batch (275 active fields at --batch-size 25 are 11 NDVI plus 11 multi-index
# entries); there the byte cap above applies first and is tighter. A schema 2
# summary states its ``batch_count``, which has the same bound.
MAX_BATCHES_PER_PROVIDER = 5_000
CODE_HEAD_REASONS = {
    "no_head": "code_head_missing",
    "multiple_heads": "code_head_multiple",
    "unreadable": "code_head_unreadable",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def safe_revision(value: Any) -> str:
    candidate = str(value or "")
    return candidate if REVISION_PATTERN.fullmatch(candidate) else "unknown"


def liveness_snapshot() -> dict[str, Any]:
    return {
        "status": "alive",
        "service": settings.app_name,
        "version": settings.app_version,
        "release_revision": safe_revision(settings.release_revision),
        "timestamp": utc_now().isoformat(),
    }


def _database_component(
    status: str,
    reason: str | None,
    migration_revision: str,
    expected_migration_revision: str,
    started: float,
) -> dict[str, Any]:
    return {
        "status": status,
        "migration_revision": migration_revision,
        "expected_migration_revision": expected_migration_revision,
        "revision_match": status == "ready",
        "reason": reason,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def database_readiness(
    engine,
    *,
    migration_head: MigrationHead | None = None,
) -> dict[str, Any]:
    """Prove the database is reachable and at the revision this code requires.

    Two constant, read-only statements. The expected revision comes from the
    code's own migration graph, never from the release identity.
    """
    head = migration_head if migration_head is not None else expected_migration_head()
    expected = head.revision if head.resolved else "unknown"
    started = time.perf_counter()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
            revisions = list(
                connection.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 2")
                ).scalars()
            )
    except Exception:
        return _database_component(
            "unavailable", "database_unreachable", "unknown", expected, started
        )
    observed = revisions[0] if len(revisions) == 1 else None
    published = "unknown" if observed is None else safe_revision(observed)
    if not head.resolved:
        return _database_component(
            "schema_unverified",
            CODE_HEAD_REASONS.get(head.problem, "code_head_unreadable"),
            published,
            "unknown",
            started,
        )
    if observed is None:
        return _database_component(
            "schema_mismatch",
            "database_revision_multiple" if revisions else "database_revision_missing",
            "unknown",
            expected,
            started,
        )
    if observed == head.revision:
        return _database_component("ready", None, published, expected, started)
    return _database_component(
        "schema_mismatch",
        "database_behind_code"
        if observed in head.known_revisions
        else "database_revision_unknown_to_code",
        published,
        expected,
        started,
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # An offset at either end of the calendar overflows on conversion.
        return None if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _exit_code_agrees(status: str, exit_code: Any) -> bool:
    """The collector derives its status from its exit code; both must agree."""
    if status == "running":
        return exit_code is None
    if type(exit_code) is not int:
        return False
    if status == "succeeded":
        return exit_code == 0
    if status == "cancelled":
        return exit_code == 130
    return exit_code in (1, 2, 3, 4)


def _provider_summaries(entries: Any) -> list[dict[str, Any]] | None:
    """Schema 1: validate every batch entry, then fold them per provider path.

    Each entry must be a ``batch_record``; the fold is the collector's own
    (``summarize_provider_batches``).
    """
    if not isinstance(entries, list) or len(entries) > (
        MAX_BATCHES_PER_PROVIDER * len(COLLECTOR_PROVIDERS)
    ):
        return None
    records = []
    per_provider = dict.fromkeys(COLLECTOR_PROVIDERS, 0)
    for entry in entries:
        record = batch_record(entry)
        if record is None:
            return None
        per_provider[record["provider"]] += 1
        if per_provider[record["provider"]] > MAX_BATCHES_PER_PROVIDER:
            return None
        records.append(record)
    return summarize_provider_batches(records)


def _count(value: Any, upper: int) -> bool:
    return type(value) is int and 0 <= value <= upper


def _compact_summary(entry: Any) -> dict[str, Any] | None:
    """One schema 2 ``providers`` entry, or None.

    The entry must be exactly what the collector's fold publishes for the
    batch counts it states: its status, failed batch count and timeout flag
    are derived again and must match in value and type. An exit code or
    counter totals that no set of batches could produce are rejected.
    """
    if not isinstance(entry, dict):
        return None
    provider = entry.get("provider")
    batches = entry.get("batch_count")
    succeeded = entry.get("succeeded_batch_count")
    timed_out = entry.get("timed_out_batch_count")
    exit_code = entry.get("exit_code")
    counters = entry.get("counters")
    covered = entry.get("counters_batch_count")
    if not (
        isinstance(provider, str)
        and provider in COLLECTOR_PROVIDERS
        and _count(batches, MAX_BATCHES_PER_PROVIDER)
        and batches > 0
        and _count(succeeded, batches)
        # A timed-out batch is always a failed one.
        and _count(timed_out, batches - succeeded)
        and _count(covered, batches)
        and type(exit_code) is int
        and exit_code in CHILD_EXIT_CODES
        # Clean batches exit 0; a failed batch that did not time out did not.
        and (exit_code == 0 or succeeded < batches)
        and (exit_code != 0 or batches - succeeded == timed_out)
        # Totals over the batches that reported counters, each at most
        # MAX_COUNTER_VALUE; none reported means no totals at all.
        and (
            counters is None
            if covered == 0
            else valid_counters(counters, covered * MAX_COUNTER_VALUE)
        )
    ):
        return None
    summary = provider_summary(
        provider,
        batch_count=batches,
        succeeded_batch_count=succeeded,
        timed_out_batch_count=timed_out,
        exit_code=exit_code,
        counters=counters,
        counters_batch_count=covered,
    )
    for key in ("status", "failed_batch_count", "timed_out"):
        stated = entry.get(key)
        if type(stated) is not type(summary[key]) or stated != summary[key]:
            return None
    return summary


def _compact_summaries(entries: Any) -> list[dict[str, Any]] | None:
    """Schema 2: at most one summary per provider path, published in run order."""
    if not isinstance(entries, list) or len(entries) > len(COLLECTOR_PROVIDERS):
        return None
    summaries: dict[str, dict[str, Any]] = {}
    for entry in entries:
        summary = _compact_summary(entry)
        if summary is None or summary["provider"] in summaries:
            return None
        summaries[summary["provider"]] = summary
    return [summaries[provider] for provider in COLLECTOR_PROVIDERS if provider in summaries]


def _collector_snapshot(payload: Any, now: datetime) -> dict[str, Any] | None:
    """Validate one decoded status document against the collector's contract."""
    if not isinstance(payload, dict):
        return None
    schema_version = payload.get("schema_version", LEGACY_STATUS_SCHEMA_VERSION)
    run_id = payload.get("run_id")
    status = payload.get("status")
    exit_code = payload.get("exit_code")
    failure_category = payload.get("failure_category")
    duration = payload.get("duration_seconds")
    started_at = _parse_timestamp(payload.get("started_at"))
    finished_value = payload.get("finished_at")
    finished_at = _parse_timestamp(finished_value)
    horizon = now + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS)
    if (
        type(schema_version) is not int
        or schema_version not in (LEGACY_STATUS_SCHEMA_VERSION, STATUS_SCHEMA_VERSION)
        or not isinstance(run_id, str)
        or not RUN_ID_PATTERN.fullmatch(run_id)
        or not isinstance(status, str)
        or status not in ALLOWED_COLLECTOR_STATUSES
        or not _exit_code_agrees(status, exit_code)
        or started_at is None
        or started_at > horizon
        or (finished_value is None) != (status == "running")
        or (
            finished_value is not None
            and (finished_at is None or finished_at > horizon)
        )
        or (
            failure_category is not None
            and (
                not isinstance(failure_category, str)
                or failure_category not in ALLOWED_FAILURE_CATEGORIES
                or status in {"running", "succeeded"}
            )
        )
        or (
            duration is not None
            and (
                type(duration) not in (int, float)
                or not 0 <= duration <= MAX_DURATION_SECONDS
            )
        )
    ):
        return None
    summarize = (
        _compact_summaries
        if schema_version == STATUS_SCHEMA_VERSION
        else _provider_summaries
    )
    providers = summarize(payload.get("providers", []))
    if providers is None:
        return None
    mode = payload.get("mode")
    return {
        "run_id": run_id,
        "mode": mode if isinstance(mode, str) and mode in ALLOWED_MODES else "unknown",
        "status": status,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat() if finished_at else None,
        "exit_code": exit_code,
        "failure_category": failure_category,
        "duration_seconds": duration,
        "providers": providers,
    }


def _read_collector_file(
    path: Path,
    now: datetime,
) -> tuple[str, dict[str, Any] | None]:
    """Return ``("valid", snapshot)``, ``("absent", None)`` or a rejection.

    A rejection is ``(reason, None)`` with reason ``unreadable``,
    ``oversized``, ``malformed_json`` or ``invalid_contract``. At most
    ``MAX_STATUS_FILE_BYTES`` + 1 bytes are ever read.
    """
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_STATUS_FILE_BYTES + 1)
    except FileNotFoundError:
        return "absent", None
    except OSError:
        return "unreadable", None
    if len(raw) > MAX_STATUS_FILE_BYTES:
        return "oversized", None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return "malformed_json", None
    snapshot = _collector_snapshot(payload, now)
    if snapshot is None:
        return "invalid_contract", None
    return "valid", snapshot


def collector_readiness(
    directory: str,
    stale_after_seconds: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not directory:
        return {"status": "unconfigured", "required_for_api_readiness": False}
    root = Path(directory)
    if not root.is_absolute():
        return {"status": "invalid", "required_for_api_readiness": False}
    stale_after = min(
        max(int(stale_after_seconds), MIN_STALE_AFTER_SECONDS),
        MAX_STALE_AFTER_SECONDS,
    )
    current = (now or utc_now()).astimezone(timezone.utc)
    loaded = {
        label: _read_collector_file(root / filename, current)
        for label, filename in STATUS_FILES.items()
    }
    state, latest = loaded["latest"]
    history = {
        "last_success": loaded["last_success"][1],
        "last_failure": loaded["last_failure"][1],
    }
    if latest is None:
        if state == "absent":
            return {
                "status": "missing",
                "required_for_api_readiness": False,
                "latest": None,
                **history,
            }
        return {
            "status": "rejected",
            "reason": state,
            "required_for_api_readiness": False,
            "latest": None,
            **history,
        }
    reference = _parse_timestamp(latest["finished_at"] or latest["started_at"])
    age_seconds = max(0, int((current - reference).total_seconds()))
    component_status = "stale" if age_seconds > stale_after else latest["status"]
    return {
        "status": component_status,
        "required_for_api_readiness": False,
        "age_seconds": age_seconds,
        "stale_after_seconds": stale_after,
        "latest": latest,
        **history,
    }


def readiness_snapshot(
    *,
    engine,
    cache_check: Callable[[], bool],
    collector_directory: str,
    collector_stale_after_seconds: int,
    migration_head: MigrationHead | None = None,
) -> dict[str, Any]:
    database = database_readiness(engine, migration_head=migration_head)
    try:
        cache_available = bool(cache_check())
    except Exception:
        cache_available = False
    cache = {
        "status": "available" if cache_available else "unavailable",
        "required_for_api_readiness": False,
        "source_of_truth": False,
    }
    collector = collector_readiness(
        collector_directory,
        collector_stale_after_seconds,
    )
    is_ready = database["status"] == "ready"
    return {
        "status": "ready" if is_ready else "not_ready",
        "service": settings.app_name,
        "version": settings.app_version,
        "release_revision": safe_revision(settings.release_revision),
        "environment": settings.environment,
        "timestamp": utc_now().isoformat(),
        "components": {
            "database": database,
            "cache": cache,
            "collector": collector,
        },
    }


def current_readiness_snapshot() -> dict[str, Any]:
    from database import engine
    from services.cache import cache_probe

    return readiness_snapshot(
        engine=engine,
        cache_check=cache_probe,
        collector_directory=settings.collector_status_directory,
        collector_stale_after_seconds=settings.collector_stale_after_seconds,
    )
