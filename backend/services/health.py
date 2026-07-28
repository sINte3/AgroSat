"""Sanitized liveness, readiness, and collector operational state."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Any, Callable

from sqlalchemy import text

from config import settings


MAX_STATUS_FILE_BYTES = 64 * 1024
MIN_STALE_AFTER_SECONDS = 60
MAX_STALE_AFTER_SECONDS = 30 * 24 * 60 * 60
STATUS_FILES = {
    "latest": "collector_latest_status.json",
    "last_success": "collector_last_success.json",
    "last_failure": "collector_last_failure.json",
}
RUN_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
REVISION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
ALLOWED_COLLECTOR_STATUSES = {"running", "succeeded", "failed", "cancelled"}
ALLOWED_FAILURE_CATEGORIES = {
    "auth",
    "quota",
    "cloud",
    "network",
    "contract",
    "partial",
    "operational",
    "lock_contention",
    "cancelled",
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


def database_readiness(engine) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
            revisions = list(
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
        if len(revisions) != 1:
            raise RuntimeError("migration revision is not singular")
        return {
            "status": "ready",
            "migration_revision": safe_revision(revisions[0]),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except Exception:
        return {
            "status": "unavailable",
            "migration_revision": "unknown",
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _read_collector_file(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_STATUS_FILE_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    run_id = payload.get("run_id")
    status = payload.get("status")
    started_at = _parse_timestamp(payload.get("started_at"))
    finished_at = _parse_timestamp(payload.get("finished_at"))
    failure_category = payload.get("failure_category")
    if (
        not isinstance(run_id, str)
        or not RUN_ID_PATTERN.fullmatch(run_id)
        or status not in ALLOWED_COLLECTOR_STATUSES
        or started_at is None
        or (status != "running" and finished_at is None)
        or (
            failure_category is not None
            and failure_category not in ALLOWED_FAILURE_CATEGORIES
        )
    ):
        return None
    exit_code = payload.get("exit_code")
    if exit_code is not None and (
        type(exit_code) is not int or exit_code not in (0, 1, 2, 3, 4, 130)
    ):
        return None
    return {
        "run_id": run_id,
        "mode": payload.get("mode")
        if payload.get("mode") in {"dry-run", "diagnostic", "apply"}
        else "unknown",
        "status": status,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat() if finished_at else None,
        "exit_code": exit_code,
        "failure_category": failure_category,
    }


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
    snapshots = {
        label: _read_collector_file(root / filename)
        for label, filename in STATUS_FILES.items()
    }
    latest = snapshots["latest"]
    if latest is None:
        return {
            "status": "missing",
            "required_for_api_readiness": False,
            "latest": None,
            "last_success": snapshots["last_success"],
            "last_failure": snapshots["last_failure"],
        }
    reference = _parse_timestamp(latest["finished_at"] or latest["started_at"])
    current = (now or utc_now()).astimezone(timezone.utc)
    age_seconds = max(0, int((current - reference).total_seconds()))
    component_status = "stale" if age_seconds > stale_after else latest["status"]
    return {
        "status": component_status,
        "required_for_api_readiness": False,
        "age_seconds": age_seconds,
        "stale_after_seconds": stale_after,
        "latest": latest,
        "last_success": snapshots["last_success"],
        "last_failure": snapshots["last_failure"],
    }


def readiness_snapshot(
    *,
    engine,
    cache_check: Callable[[], bool],
    collector_directory: str,
    collector_stale_after_seconds: int,
) -> dict[str, Any]:
    database = database_readiness(engine)
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
