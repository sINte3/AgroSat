#!/usr/bin/env python3
"""Collect one real Sentinel-2 NDVI observation with safe persistence."""
import argparse
import json
import math
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.collector_locking import acquire_lock, release_lock

DEFAULT_LOCK = Path(tempfile.gettempdir()) / "agrosat_ndvi_collector.lock"
MUTEX = "Global\\AgroSatNdviCollector_v1"
SCHEMA = 1


class ValidationError(ValueError):
    """A bad invocation, payload, or persistence contract."""


def sanitize_text(value: Any, limit: int = 1000) -> str:
    """Return diagnostic text without credentials or a Windows user path."""
    text = str(value)[:limit]
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)\b(token|password|secret|api[_-]?key)\s*[:=]\s*[^\s,]+", r"\1=[REDACTED]", text)
    text = re.sub(r"(?i)\b(?:postgres(?:ql)?|https?)://[^\s@]+@[^\s]+", "[REDACTED_URL]", text)
    text = re.sub(r"(?i)(?:\\\\\?\\)?[a-z]:\\users\\[^\\]+(?:\\[^\s,]*)?", "[REDACTED_USER_PATH]", text)
    text = re.sub(r"(?i)\\device\\harddiskvolume\d+\\users\\[^\\]+(?:\\[^\s,]*)?", "[REDACTED_USER_PATH]", text)
    return text


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {sanitize_text(key, 100): sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    return sanitize_text(value) if isinstance(value, str) else value


def external_path(raw: str | None, required: bool = False) -> Path | None:
    if raw is None:
        if required:
            raise ValidationError("--output-log is required")
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise ValidationError("path must be absolute")
    try:
        path.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError:
        return path
    raise ValidationError("path must be outside repository")


def dates(date_from: str | None, date_to: str | None, lookback: int, today: date | None = None) -> tuple[date, date]:
    if not 1 <= lookback <= 30:
        raise ValidationError("--lookback-days must be 1..30")
    today = today or date.today()
    try:
        end = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today
        start = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else end - timedelta(days=lookback)
    except ValueError as exc:
        raise ValidationError("dates must use YYYY-MM-DD") from exc
    if end > today or start > end or (end - start).days > 30:
        raise ValidationError("date range is invalid or exceeds 30 days")
    return start, end


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}", suffix=".tmp", dir=path.parent)
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect NDVI for one field; dry-run is safe by default.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--write", action="store_true")
    parser.add_argument("--field-id", type=int)
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--lock-file")
    parser.add_argument("--output-log")
    return parser.parse_args(argv)


def mode(args: argparse.Namespace) -> str:
    return "write" if args.write else "apply" if args.apply else "dry-run"


def field_lookup(field_id: int) -> dict[str, Any]:
    from database import SessionLocal
    from sqlalchemy import text

    session = SessionLocal()
    try:
        row = session.execute(text("SELECT id, ST_AsText(geometry) AS geometry_wkt FROM fields WHERE id=:id AND is_active=true AND geometry IS NOT NULL AND NOT ST_IsEmpty(geometry)"), {"id": field_id}).mappings().first()
        if not row:
            raise ValidationError("field is missing, inactive, or has empty geometry")
        return dict(row)
    finally:
        session.close()


def validate_observation(observation: Any, start: date, end: date) -> tuple[dict[str, Any], bool]:
    """Validate the payload before an apply/write outcome; bool means quality blocked."""
    if not isinstance(observation, dict):
        raise ValidationError("NDVI observation must be an object")
    from services.satellite import validate_ndvi_quality
    from services.satellite_safety import require_payload_provenance

    require_payload_provenance(observation)
    try:
        captured = date.fromisoformat(str(observation["captured_date"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("captured_date is invalid") from exc
    if not start <= captured <= end:
        raise ValidationError("captured date outside requested range")
    mean = observation.get("mean_ndvi")
    if isinstance(mean, bool) or not isinstance(mean, (int, float)) or not math.isfinite(mean):
        return observation, True
    valid, _reason = validate_ndvi_quality(mean, observation.get("cloud_cover_pct"), observation.get("min_ndvi"), observation.get("max_ndvi"))
    return observation, not valid


def persist(field_id: int, record: dict[str, Any], start: date, end: date) -> bool:
    """Insert once, returning true only when PostgreSQL returned an inserted id."""
    from database import SessionLocal
    from sqlalchemy import text

    _record, quality_blocked = validate_observation(record, start, end)
    if quality_blocked:
        raise ValidationError("quality-blocked observation cannot be persisted")
    captured = date.fromisoformat(str(record["captured_date"]))
    values = {key: record.get(key) for key in ("mean_ndvi", "min_ndvi", "max_ndvi", "std_ndvi", "p10_ndvi", "p90_ndvi", "cloud_cover_pct", "valid_pixels_pct")}
    values.update(field_id=field_id, captured_date=captured, satellite="Sentinel-2")
    statement = text("INSERT INTO ndvi_records (field_id,captured_date,mean_ndvi,min_ndvi,max_ndvi,std_ndvi,p10_ndvi,p90_ndvi,cloud_cover_pct,valid_pixels_pct,satellite) VALUES (:field_id,:captured_date,:mean_ndvi,:min_ndvi,:max_ndvi,:std_ndvi,:p10_ndvi,:p90_ndvi,:cloud_cover_pct,:valid_pixels_pct,:satellite) ON CONFLICT (field_id,captured_date) DO NOTHING RETURNING id")
    session = SessionLocal()
    try:
        result = session.execute(statement, values)
        inserted = result.first() is not None
        session.commit()
        return inserted
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run(args: argparse.Namespace, *, lookup: Callable[[int], dict[str, Any]] = field_lookup, service_factory: Callable[[], Any] | None = None, writer: Callable[[int, dict[str, Any], date, date], bool] = persist) -> int:
    started = time.monotonic()
    result: dict[str, Any] = {"schema_version": SCHEMA, "field_id": args.field_id, "mode": mode(args), "scenes_received": 0, "candidate_count": 0, "inserted_count": 0, "skipped_existing_count": 0, "quality_blocked_count": 0, "error_count": 0, "started_at": datetime.now(timezone.utc).isoformat(), "diagnostics": []}
    lock = None
    output_log: Path | None = None
    code = 4
    try:
        current_mode = mode(args)
        start, end = dates(args.date_from, args.date_to, args.lookback_days)
        result.update(date_from=start.isoformat(), date_to=end.isoformat())
        output_log = external_path(args.output_log, required=current_mode in ("apply", "write"))
        if current_mode == "dry-run":
            code = 0
        else:
            if not args.field_id or args.field_id <= 0:
                raise ValidationError("--apply and --write require --field-id")
            lockfile = external_path(args.lock_file) or DEFAULT_LOCK
            try:
                lock = acquire_lock(str(lockfile), mutex_name=MUTEX)
            except SystemExit as exc:
                if exc.code == 3:
                    code = 3
                    raise RuntimeError("LOCK_CONTENTION")
                raise
            field = lookup(args.field_id)
            if service_factory is None:
                from services.satellite import get_satellite_service
                service_factory = get_satellite_service
            from services.satellite_safety import require_real_service
            service = service_factory()
            require_real_service(service)
            observation = service.get_ndvi_stats(field["geometry_wkt"], start, end)
            result["scenes_received"] = 0 if observation is None else 1
            if observation is None:
                raise RuntimeError("Sentinel collection returned no observation")
            result["candidate_count"] = 1
            observation, quality_blocked = validate_observation(observation, start, end)
            if quality_blocked:
                result["quality_blocked_count"] = 1
            elif current_mode == "write":
                try:
                    inserted = writer(args.field_id, observation, start, end)
                except Exception as exc:
                    raise ValidationError(f"persistence failed: {exc}") from exc
                if inserted:
                    result["inserted_count"] = 1
                else:
                    result["skipped_existing_count"] = 1
            code = 0
    except ValidationError as exc:
        code = 2
        result["error_count"] += 1
        result["diagnostics"].append(sanitize_text(exc))
    except __import__("services.satellite_safety", fromlist=["SatelliteProvenanceError"]).SatelliteProvenanceError as exc:
        code = 2
        result["error_count"] += 1
        result["diagnostics"].append(sanitize_text(exc))
    except RuntimeError as exc:
        if str(exc) != "LOCK_CONTENTION":
            code = 1
            result["error_count"] += 1
            result["diagnostics"].append(sanitize_text(exc))
    except Exception as exc:
        code = 4
        result["error_count"] += 1
        result["diagnostics"].append(sanitize_text(exc))
    finally:
        if lock is not None:
            try:
                release_lock(lock)
            except Exception as exc:
                code = 4
                result["diagnostics"].append(sanitize_text(f"lock release failed: {exc}"))
        result.update(finished_at=datetime.now(timezone.utc).isoformat(), duration_seconds=round(time.monotonic() - started, 3), exit_code=code)
        if output_log is not None:
            try:
                atomic_json(output_log, result)
            except Exception:
                code = 2
                result["exit_code"] = code
    return code


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(parse_args(argv)))


if __name__ == "__main__":
    main()
