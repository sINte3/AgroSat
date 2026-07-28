#!/usr/bin/env python3
"""Bounded standalone pixel-anomaly processor.

Dry-run is the default. Numeric fixtures are accepted only in dry-run mode.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
from threading import Event
import time
from typing import Any
import uuid


BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.collector_locking import acquire_lock, release_lock
from services.pixel_anomaly_algorithm import (
    AnomalyContractError,
    AnomalyThresholds,
    analyze_pixel_scenes,
)
from services.pixel_anomaly_processing import persist_anomaly_result
from services.pixel_scene_provider import (
    FixturePixelSceneProvider,
    PixelSceneProvider,
    PixelSceneRequest,
    PixelSceneUnavailable,
    provider_registry,
    scene_from_fixture,
)


SCHEMA_VERSION = 1
SUPPORTED_INDICES = ("ndvi", "savi", "evi", "ndmi", "ndre")
MAX_FIELDS = 100
MAX_ATTEMPTS = 5
MAX_TIMEOUT_SECONDS = 300
MUTEX_NAME = "Global\\AgroSatPixelAnomalyProcessor_v1"


class CliContractError(ValueError):
    """Invalid bounded invocation."""


@dataclass(frozen=True)
class WorkItem:
    current: PixelSceneRequest
    comparison: PixelSceneRequest

    def key(self) -> str:
        payload = {
            "comparison": asdict(self.comparison),
            "current": asdict(self.current),
        }
        encoded = json.dumps(
            payload,
            default=str,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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


def external_path(raw: str | None, label: str, *, required: bool = False) -> Path | None:
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


def atomic_json(path: Path, value: dict[str, Any]) -> None:
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


def _request(payload: dict[str, Any]) -> PixelSceneRequest:
    try:
        request = PixelSceneRequest(
            enterprise_id=int(payload["enterprise_id"]),
            field_id=int(payload["field_id"]),
            index_code=str(payload["index_code"]).lower(),
            observed_at=datetime.strptime(
                str(payload["observed_at"]),
                "%Y-%m-%d",
            ).date(),
            record_type=str(payload["record_type"]),
            record_id=int(payload["record_id"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CliContractError("invalid scene request") from exc
    if request.enterprise_id <= 0 or request.field_id <= 0 or request.record_id <= 0:
        raise CliContractError("scene request identifiers must be positive")
    if request.index_code not in SUPPORTED_INDICES:
        raise CliContractError("scene request index is unsupported")
    if request.record_type not in {"ndvi_record", "satellite_index_record"}:
        raise CliContractError("scene request record type is unsupported")
    if request.record_type == "ndvi_record" and request.index_code != "ndvi":
        raise CliContractError("ndvi_record can only provide ndvi")
    return request


def _validate_pair(item: WorkItem, expected_index: str) -> None:
    current = item.current
    comparison = item.comparison
    if current.index_code != expected_index or comparison.index_code != expected_index:
        raise CliContractError("work-item index does not match --index")
    if (
        current.enterprise_id,
        current.field_id,
        current.index_code,
    ) != (
        comparison.enterprise_id,
        comparison.field_id,
        comparison.index_code,
    ):
        raise CliContractError("current and comparison requests do not match")
    separation = (current.observed_at - comparison.observed_at).days
    if not 1 <= separation <= 365:
        raise CliContractError("work-item date window must be between 1 and 365 days")


def load_request_items(path: Path, expected_index: str, maximum: int) -> list[WorkItem]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pairs = payload["pairs"]
    except (OSError, KeyError, json.JSONDecodeError, TypeError) as exc:
        raise CliContractError("invalid request file") from exc
    if payload.get("schema_version") != SCHEMA_VERSION or not isinstance(pairs, list):
        raise CliContractError("unsupported request-file schema")
    if not 1 <= len(pairs) <= maximum:
        raise CliContractError("request file exceeds bounded field batch")
    try:
        items = [
            WorkItem(_request(pair["current"]), _request(pair["comparison"]))
            for pair in pairs
        ]
    except (KeyError, TypeError) as exc:
        raise CliContractError("request file contains an invalid pair") from exc
    for item in items:
        _validate_pair(item, expected_index)
    keys = [item.key() for item in items]
    if len(set(keys)) != len(keys):
        raise CliContractError("request file contains duplicate work items")
    return items


def load_fixture_items(
    path: Path,
    expected_index: str,
    maximum: int,
) -> tuple[list[WorkItem], FixturePixelSceneProvider]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pairs = payload["pairs"]
    except (OSError, KeyError, json.JSONDecodeError, TypeError) as exc:
        raise CliContractError("invalid fixture file") from exc
    if payload.get("schema_version") != SCHEMA_VERSION or not isinstance(pairs, list):
        raise CliContractError("unsupported fixture-file schema")
    if not 1 <= len(pairs) <= maximum:
        raise CliContractError("fixture file exceeds bounded field batch")
    items: list[WorkItem] = []
    scenes = {}
    for pair in pairs:
        try:
            current = scene_from_fixture(pair["current"])
            comparison = scene_from_fixture(pair["comparison"])
        except (KeyError, TypeError, PixelSceneUnavailable) as exc:
            raise CliContractError("fixture file contains an invalid pair") from exc
        item = WorkItem(
            PixelSceneRequest(
                current.enterprise_id,
                current.field_id,
                current.index_code,
                current.observed_at,
                current.record_type,
                current.record_id,
            ),
            PixelSceneRequest(
                comparison.enterprise_id,
                comparison.field_id,
                comparison.index_code,
                comparison.observed_at,
                comparison.record_type,
                comparison.record_id,
            ),
        )
        _validate_pair(item, expected_index)
        key = (current.field_id, current.observed_at)
        comparison_key = (comparison.field_id, comparison.observed_at)
        if key in scenes or comparison_key in scenes:
            raise CliContractError("fixture scenes must be unique by field and date")
        scenes[key] = current
        scenes[comparison_key] = comparison
        items.append(item)
    return items, FixturePixelSceneProvider(scenes)


def fetch_with_retry(
    provider: PixelSceneProvider,
    request: PixelSceneRequest,
    *,
    timeout_seconds: int,
    attempts: int,
    cancel_event: Event,
    sleep=time.sleep,
):
    for attempt in range(1, attempts + 1):
        try:
            return provider.fetch(
                request,
                timeout_seconds=timeout_seconds,
                cancel_event=cancel_event,
            )
        except PixelSceneUnavailable as exc:
            if (
                exc.category not in {"network", "quota"}
                or attempt == attempts
                or cancel_event.is_set()
            ):
                raise
            sleep(min(2 ** (attempt - 1), 8))
    raise PixelSceneUnavailable("operational", "provider retry contract failed")


def _checkpoint(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"schema_version": SCHEMA_VERSION, "completed": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliContractError("checkpoint is unreadable") from exc
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise CliContractError("checkpoint schema is unsupported")
    if not isinstance(payload.get("completed"), dict):
        raise CliContractError("checkpoint completed map is invalid")
    return payload


def execute(
    args,
    *,
    cancel_event: Event,
    session_factory=None,
) -> tuple[int, dict[str, Any]]:
    if not 1 <= args.max_fields <= MAX_FIELDS:
        raise CliContractError("--max-fields must be between 1 and 100")
    if not 1 <= args.timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise CliContractError("--timeout-seconds must be between 1 and 300")
    if not 1 <= args.attempts <= MAX_ATTEMPTS:
        raise CliContractError("--attempts must be between 1 and 5")
    fixture_path = external_path(args.fixture, "--fixture")
    request_path = external_path(args.request_file, "--request-file")
    if bool(fixture_path) == bool(request_path):
        raise CliContractError("provide exactly one of --fixture or --request-file")
    if args.write and fixture_path:
        raise CliContractError("fixture input is rejected with --write")
    checkpoint_path = external_path(
        args.checkpoint,
        "--checkpoint",
        required=args.write or args.resume,
    )
    log_path = external_path(args.log_file, "--log-file", required=args.write)
    if args.resume and checkpoint_path is None:
        raise CliContractError("--resume requires --checkpoint")

    if fixture_path:
        items, provider = load_fixture_items(
            fixture_path,
            args.index,
            args.max_fields,
        )
    else:
        items = load_request_items(request_path, args.index, args.max_fields)
        providers = provider_registry()
        if args.provider not in providers:
            raise CliContractError("unknown pixel provider")
        provider = providers[args.provider]

    checkpoint = _checkpoint(checkpoint_path if args.resume else None)
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(uuid.uuid4()),
        "mode": "write" if args.write else "dry_run",
        "provider": provider.name,
        "index_code": args.index,
        "started_at": utc_now(),
        "finished_at": None,
        "processed": 0,
        "skipped_checkpoint": 0,
        "status_counts": {},
        "failure_categories": {},
        "inserted_runs": 0,
        "replayed_runs": 0,
        "zones": 0,
        "exit_code": None,
    }
    counts: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    db = None
    if args.write:
        if session_factory is None:
            from database import SessionLocal

            session_factory = SessionLocal
        db = session_factory()
    try:
        for item in items:
            if cancel_event.is_set():
                failures["cancelled"] += 1
                break
            work_key = item.key()
            if args.resume and work_key in checkpoint["completed"]:
                summary["skipped_checkpoint"] += 1
                continue
            started_at = datetime.now(timezone.utc)
            try:
                current = fetch_with_retry(
                    provider,
                    item.current,
                    timeout_seconds=args.timeout_seconds,
                    attempts=args.attempts,
                    cancel_event=cancel_event,
                )
                comparison = fetch_with_retry(
                    provider,
                    item.comparison,
                    timeout_seconds=args.timeout_seconds,
                    attempts=args.attempts,
                    cancel_event=cancel_event,
                )
                result = analyze_pixel_scenes(current, comparison)
                summary["processed"] += 1
                counts[result.status] += 1
                summary["zones"] += len(result.zones)
                if args.write:
                    outcome = persist_anomaly_result(
                        db,
                        result,
                        AnomalyThresholds(),
                        started_at=started_at,
                        processing_run_id=summary["run_id"],
                    )
                    summary[
                        "inserted_runs" if outcome.inserted else "replayed_runs"
                    ] += 1
                if checkpoint_path:
                    checkpoint["completed"][work_key] = {
                        "run_key": result.run_key,
                        "status": result.status,
                        "completed_at": utc_now(),
                    }
                    atomic_json(checkpoint_path, checkpoint)
            except PixelSceneUnavailable as exc:
                failures[exc.category] += 1
            except AnomalyContractError:
                failures["contract"] += 1
            except KeyboardInterrupt:
                cancel_event.set()
                failures["cancelled"] += 1
                break
            except Exception:
                failures["database" if args.write else "operational"] += 1
        summary["status_counts"] = dict(sorted(counts.items()))
        summary["failure_categories"] = dict(sorted(failures.items()))
        summary["finished_at"] = utc_now()
        if cancel_event.is_set():
            exit_code = 130
        elif failures and not counts:
            exit_code = 4 if set(failures) == {"unsupported_data"} else 5
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


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--index", choices=SUPPORTED_INDICES, required=True)
    value.add_argument("--provider", default="sentinel_numeric_pixels")
    source = value.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture")
    source.add_argument("--request-file")
    value.add_argument("--write", action="store_true")
    value.add_argument("--max-fields", type=int, default=MAX_FIELDS)
    value.add_argument("--timeout-seconds", type=int, default=60)
    value.add_argument("--attempts", type=int, default=3)
    value.add_argument("--checkpoint")
    value.add_argument("--resume", action="store_true")
    value.add_argument("--log-file")
    value.add_argument("--lock-file")
    return value


def main(argv: list[str] | None = None) -> int:
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
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "exit_code": 2,
                    "failure_category": "contract",
                    "detail": sanitize_text(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        if lock_path:
            release_lock(lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
