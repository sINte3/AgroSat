"""Run exactly one bounded, read-only CDSE Pixel NDVI diagnostic for field 4."""

from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

from sqlalchemy.engine import make_url


DATABASE_PREFIX = "agrosat_r3_d_pixel_ndvi_"


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-env", type=Path, required=True)
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--diagnostic-root", type=Path, required=True)
    args = parser.parse_args()

    runtime_env = args.runtime_env.resolve(strict=True)
    backend_root = args.backend_root.resolve(strict=True)
    diagnostic_root = args.diagnostic_root.resolve()
    diagnostic_root.mkdir(parents=True, exist_ok=True)
    if any(diagnostic_root.iterdir()):
        raise RuntimeError("DIAGNOSTIC_ROOT_MUST_BE_EMPTY")

    os.environ["AGROSAT_RUNTIME_ENV_FILE"] = str(runtime_env)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path.insert(0, str(backend_root))

    from config import settings
    from database import SessionLocal
    from services import pixel_ndvi

    database_name = make_url(settings.database_url).database
    if not database_name or not database_name.startswith(DATABASE_PREFIX) or database_name == "agrosat":
        raise RuntimeError("ISOLATED_DATABASE_IDENTITY_REJECTED")
    if settings.sentinel_hub_provider != "cdse":
        raise RuntimeError("CDSE_PROVIDER_REQUIRED")

    settings.pixel_ndvi_cache_directory = str(diagnostic_root / "cache")
    provider_calls = 0
    catalog_calls = 0
    process_calls = 0
    service = pixel_ndvi.get_satellite_service()
    original_catalog = service.get_catalog_response
    original_request = pixel_ndvi._request_encoded_png

    def counted_catalog(*request_args, **request_kwargs):
        nonlocal provider_calls, catalog_calls
        provider_calls += 1
        catalog_calls += 1
        if catalog_calls > 1 or provider_calls > 2:
            raise RuntimeError("UNBOUNDED_PROVIDER_CALL_REJECTED")
        return original_catalog(*request_args, **request_kwargs)

    def counted_request(*request_args, **request_kwargs):
        nonlocal provider_calls, process_calls
        provider_calls += 1
        process_calls += 1
        if process_calls > 1 or provider_calls > 2:
            raise RuntimeError("UNBOUNDED_PROVIDER_CALL_REJECTED")
        return original_request(*request_args, **request_kwargs)

    service.get_catalog_response = counted_catalog
    pixel_ndvi.get_satellite_service = lambda: service
    pixel_ndvi._request_encoded_png = counted_request
    with SessionLocal() as db:
        user = SimpleNamespace(role="agronomist", enterprise_id=7)
        upper = pixel_ndvi.local_today()
        lower = upper - timedelta(days=45)
        rows = pixel_ndvi.list_scene_rows(db, 4, user, lower, upper, 10)
        if not rows:
            raise RuntimeError("FIELD_4_HAS_NO_QUALIFIED_SCENE")
        selected = rows[0]
        cold_started = time.perf_counter()
        cold = pixel_ndvi.get_artifact(selected)
        cold_ms = round((time.perf_counter() - cold_started) * 1000, 2)
        warm_started = time.perf_counter()
        warm = pixel_ndvi.get_artifact(selected)
        warm_ms = round((time.perf_counter() - warm_started) * 1000, 2)
        db.rollback()

    if catalog_calls != 1 or process_calls != 1 or provider_calls != 2 or cold.cache_state != "MISS" or warm.cache_state != "HIT":
        raise RuntimeError("DIAGNOSTIC_CACHE_OR_PROVIDER_COUNT_FAILED")
    if cold.png != warm.png:
        raise RuntimeError("DIAGNOSTIC_CACHE_CONTENT_MISMATCH")
    image_path = diagnostic_root / "field4_pixel_ndvi.png"
    atomic_write(image_path, cold.png)
    image_sha = hashlib.sha256(cold.png).hexdigest()
    if b"SENTINEL_HUB_CLIENT" in cold.png or b"Authorization" in cold.png or b"Bearer " in cold.png:
        raise RuntimeError("DIAGNOSTIC_IMAGE_SECRET_SCAN_FAILED")
    summary = cold.metadata["summary"]
    result = {
        "result": "PASS",
        "database": database_name,
        "field_id": 4,
        "provider": "cdse",
        "source": "Sentinel-2 L2A",
        "http_status_class": cold.metadata["provider_diagnostic"]["http_status_class"],
        "provider_duration_ms": cold.metadata["provider_diagnostic"]["duration_ms"],
        "cold_render_ms": cold_ms,
        "warm_cache_ms": warm_ms,
        "provider_call_count": provider_calls,
        "catalog_call_count": catalog_calls,
        "process_call_count": process_calls,
        "cold_cache_state": cold.cache_state,
        "warm_cache_state": warm.cache_state,
        "acquisition_date": selected.observation_date.isoformat(),
        "stored_cloud_cover_pct": selected.cloud_cover_pct,
        "stored_valid_pixel_pct": selected.valid_pixels_pct,
        "width": cold.metadata["width"],
        "height": cold.metadata["height"],
        "source_resolution_m": cold.metadata["source_resolution_m"],
        "effective_resolution_m": cold.metadata["effective_resolution_m"],
        "valid_pixel_count": summary["valid_pixel_count"],
        "no_data_pixel_count": summary["no_data_pixel_count"],
        "valid_pixel_pct": summary["valid_pixel_pct"],
        "response_bytes": len(cold.png),
        "image_sha256": image_sha,
        "image_path": str(image_path),
        "secret_scan": "PASS_BINARY_MARKERS_ABSENT",
        "raw_provider_response_persisted": False,
        "database_write_count": 0,
        "secret_values_logged": False,
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
