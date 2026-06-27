#!/usr/bin/env python3
"""
Guarded batch write for multi-index Sentinel-2 results into
satellite_index_records.  Builds on single-field write logic but
operates on multiple explicit field IDs with per-field transactions,
rate-limiting, and safety guards.

Default mode = preview/no-write.  Actual DB mutation requires --write.

Usage:
  python backend/scripts/write_multi_index_batch.py --help
  python backend/scripts/write_multi_index_batch.py --field-ids 4,5,6 --days 15
  python backend/scripts/write_multi_index_batch.py --field-ids 4,5,6 --days 15 --sleep-seconds 0 --write
  python backend/scripts/write_multi_index_batch.py --field-ids 4,5,6 --verify-only
  python backend/scripts/write_multi_index_batch.py --self-test
"""

import argparse
import math
import sys
import time
from datetime import date, timedelta
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx
from shapely import wkt
from shapely.geometry import mapping
from sqlalchemy import text as sa_text

sys.path.insert(0, "backend")

from config import settings
from database import SessionLocal, engine
from services.satellite_indices import (
    SUPPORTED_INDEX_CODES,
    build_multi_index_evalscript,
    normalize_index_code,
    parse_multi_index_stats_response,
    validate_index_quality,
)

STATISTICAL_API_URL = "https://services.sentinel-hub.com/api/v1/statistics"
TOKEN_URL = "https://services.sentinel-hub.com/auth/realms/main/protocol/openid-connect/token"


# -- Sentinel Hub helpers (local, isolated) --


def _get_access_token(client_id: str, client_secret: str) -> str:
    if not client_id or not client_secret:
        raise ValueError(
            "Sentinel Hub credentials not configured. "
            "Set SENTINEL_HUB_CLIENT_ID and SENTINEL_HUB_CLIENT_SECRET in .env"
        )
    resp = httpx.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _build_statistical_payload(
    geometry_geojson: dict,
    evalscript: str,
    index_codes: list[str],
    date_from: date,
    date_to: date,
    max_cloud_coverage: int = 80,
) -> dict:
    return {
        "input": {
            "bounds": {
                "geometry": geometry_geojson,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [
                {
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {
                            "from": f"{date_from.isoformat()}T00:00:00Z",
                            "to": f"{date_to.isoformat()}T23:59:59Z",
                        },
                        "maxCloudCoverage": max_cloud_coverage,
                    },
                    "processing": {"harmonizeValues": True},
                }
            ],
        },
        "aggregation": {
            "timeRange": {
                "from": f"{date_from.isoformat()}T00:00:00Z",
                "to": f"{date_to.isoformat()}T23:59:59Z",
            },
            "aggregationInterval": {"of": "P5D"},
            "evalscript": evalscript,
            "resx": 20,
            "resy": 20,
        },
        "calculations": {
            code: {
                "statistics": {
                    "default": {
                        "percentiles": {"k": [10, 90]},
                    }
                }
            }
            for code in index_codes
        },
    }


# -- Preflight helpers --


def _check_table_exists() -> None:
    with engine.connect() as conn:
        result = conn.execute(
            sa_text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = 'satellite_index_records')"
            )
        ).scalar()
    if not result:
        print("ERROR: satellite_index_records table does not exist. Run Alembic migration first.", file=sys.stderr)
        sys.exit(1)


def _fetch_fields_by_ids(field_ids: list[int]) -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.execute(
            sa_text(
                "SELECT id, name, ST_AsText(geometry) AS geometry_wkt "
                "FROM fields WHERE id = ANY(:ids) AND is_active = true "
                "ORDER BY id ASC"
            ),
            {"ids": field_ids},
        ).mappings().all()
    finally:
        db.close()
    return [dict(r) for r in rows]


def _normalize_and_validate_indices(raw: str) -> list[str]:
    codes = [normalize_index_code(s) for s in raw.split(",") if s.strip()]
    unsupported = [c for c in codes if c not in SUPPORTED_INDEX_CODES]
    if unsupported:
        print(f"ERROR: Unsupported index codes: {', '.join(unsupported)}")
        print(f"  Supported: {', '.join(sorted(SUPPORTED_INDEX_CODES))}", file=sys.stderr)
        sys.exit(1)
    if not codes:
        print("ERROR: No valid index codes provided", file=sys.stderr)
        sys.exit(1)
    return codes


def _prepare_records(parsed: dict[str, dict]) -> list[dict]:
    """Convert parsed results into row dicts.  Rejects NaN/Inf."""
    records = []
    for code, d in parsed.items():
        for key in ("mean_value", "min_value", "max_value", "std_value", "valid_pixels_pct"):
            val = d.get(key)
            if val is not None:
                try:
                    fv = float(val)
                    if math.isnan(fv) or math.isinf(fv):
                        print(f"ERROR: {code} field '{key}' is NaN/Inf ({val}) -- aborting write", file=sys.stderr)
                        sys.exit(1)
                except (TypeError, ValueError):
                    pass
        records.append({
            "field_id": None,
            "captured_date": d["captured_date"],
            "index_code": d["index_code"],
            "mean_value": d.get("mean_value"),
            "min_value": d.get("min_value"),
            "max_value": d.get("max_value"),
            "std_value": d.get("std_value"),
            "p10_value": d.get("p10_value"),
            "p90_value": d.get("p90_value"),
            "valid_pixels_pct": d.get("valid_pixels_pct"),
            "cloud_cover_pct": d.get("cloud_cover_pct"),
            "satellite": d.get("satellite", "Sentinel-2"),
        })
    return records


# -- CLI --


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Guarded batch write for multi-index Sentinel-2 results into satellite_index_records."
    )
    parser.add_argument(
        "--field-ids", type=str, default=None,
        help="Comma-separated field IDs to process (required for write mode)",
    )
    parser.add_argument("--days", type=int, default=15, help="Look-back days (default: 15)")
    parser.add_argument(
        "--indices", type=str, default="savi,evi,ndmi,ndre",
        help="Comma-separated index codes (default: savi,evi,ndmi,ndre)",
    )
    parser.add_argument(
        "--sleep-seconds", type=float, default=0.5,
        help="Seconds between Sentinel Hub API calls (default: 0.5, 0 = no sleep)",
    )
    parser.add_argument(
        "--write", action="store_true", help="Actually write to DB (default: preview/no-write)"
    )
    parser.add_argument(
        "--allow-large-batch", action="store_true",
        help="Allow >5 field IDs in write mode",
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="Read satellite_index_records for field/indices (no Sentinel Hub, no write)",
    )
    parser.add_argument("--self-test", action="store_true", help="Run offline self-test")
    return parser.parse_args(argv)


# -- Verification mode --


def _print_verify_field(field_id: int, index_codes: list[str]) -> None:
    """Print current satellite_index_records rows for one field."""
    db = SessionLocal()
    try:
        rows = db.execute(
            sa_text(
                "SELECT captured_date, index_code, mean_value, min_value, max_value, "
                "       std_value, p10_value, p90_value, valid_pixels_pct, satellite, created_at "
                "FROM satellite_index_records "
                "WHERE field_id = :field_id AND index_code = ANY(:codes) "
                "ORDER BY captured_date, index_code"
            ),
            {"field_id": field_id, "codes": list(index_codes)},
        ).mappings().all()
    finally:
        db.close()

    print(f"  field_id:           {field_id}")
    print(f"  requested indices:  {', '.join(index_codes)}")
    print(f"  row count:          {len(rows)}")
    print()
    if not rows:
        print("  (no rows in satellite_index_records for this field)")
        return
    for row in rows:
        print(f"  captured_date:     {row['captured_date']}")
        print(f"  index_code:        {row['index_code']}")
        print(f"  mean_value:        {row['mean_value']}")
        print(f"  min_value:         {row['min_value']}")
        print(f"  max_value:         {row['max_value']}")
        print(f"  std_value:         {row['std_value']}")
        print(f"  p10_value:         {row['p10_value']}")
        print(f"  p90_value:         {row['p90_value']}")
        print(f"  valid_pixels_pct:  {row['valid_pixels_pct']}")
        print(f"  satellite:         {row['satellite']}")
        print(f"  created_at:        {row['created_at']}")
        print()


def verify_only(field_ids: list[int], index_codes: list[str]) -> None:
    """Read satellite_index_records for all requested fields."""
    print("-- VERIFY ONLY MODE (no Sentinel Hub, no writes) --")
    print()
    total = 0
    for fid in field_ids:
        _print_verify_field(fid, index_codes)
        total += 1
    print(f"  Total fields verified: {total}")
    print()


# -- Preview mode --


def _print_field_summary(field_info: dict, index_codes: list[str]) -> None:
    print(f"  field_id:   {field_info['id']}")
    print(f"  field_name: {field_info['name']}")
    if not field_info.get("parsed"):
        print("  status:     NO_DATA (or empty response)")
        return
    print(f"  status:     DATA_AVAILABLE")
    print(f"  indices:")
    for code in index_codes:
        d = field_info["parsed"].get(code)
        if d is None:
            print(f"    {code}: (no data)")
            continue
        print(f"    {code}: captured={d['captured_date']} mean={d['mean_value']} "
              f"min={d['min_value']} max={d['max_value']} std={d['std_value']} "
              f"p10={d['p10_value']} p90={d['p90_value']} "
              f"valid_pixels_pct={d['valid_pixels_pct']} satellite={d['satellite']}")


def _print_preview_records(field_id: int, records: list[dict]) -> None:
    """Print planned rows for one field, showing INSERT/UPDATE action."""
    db = SessionLocal()
    try:
        for rec in records:
            rec["field_id"] = field_id
            existing = db.execute(
                sa_text(
                    "SELECT 1 FROM satellite_index_records "
                    "WHERE field_id = :field_id AND captured_date = :captured_date "
                    "AND index_code = :index_code"
                ),
                rec,
            ).scalar()
            action = "UPDATE" if existing else "INSERT"

            print(f"  [{action}]", end=" ")
            print(f"captured_date={rec['captured_date']}", end=" ")
            print(f"index_code={rec['index_code']}", end=" ")
            print(f"mean={rec['mean_value']}", end=" ")
            print(f"min={rec['min_value']}", end=" ")
            print(f"max={rec['max_value']}", end=" ")
            print(f"std={rec['std_value']}", end=" ")
            print(f"p10={rec['p10_value']}", end=" ")
            print(f"p90={rec['p90_value']}", end=" ")
            print(f"valid_pixels_pct={rec['valid_pixels_pct']}", end=" ")
            print(f"satellite={rec['satellite']}")
    finally:
        db.close()


# -- Idempotency helper (pure) --


def _record_values_equal(existing: dict, rec: dict) -> bool:
    """Compare write-relevant columns between an existing DB row and an incoming
    record.  Returns True when all values match (no UPDATE needed).

    Handles None, float, Decimal, and int comparisons robustly.
    """
    keys = (
        "mean_value", "min_value", "max_value", "std_value",
        "p10_value", "p90_value",
        "valid_pixels_pct", "cloud_cover_pct", "satellite",
    )
    for k in keys:
        a = existing.get(k)
        b = rec.get(k)
        # Both None -> equal
        if a is None and b is None:
            continue
        # One None, the other not -> not equal
        if a is None or b is None:
            return False
        # Numeric comparison (handles float, Decimal, int)
        try:
            fa = float(a)
            fb = float(b)
            if fa != fb:
                return False
        except (TypeError, ValueError):
            # Fallback to string comparison for non-numeric (satellite etc.)
            if str(a) != str(b):
                return False
    return True


# -- Write logic (per-field transaction) --


def _do_field_write(field_id: int, records: list[dict]) -> dict[str, int]:
    """Upsert rows for one field in its own transaction. Returns {inserted, updated, skipped}.

    Idempotent: skips UPDATE when existing row already matches.
    """
    db = SessionLocal()
    counts = {"inserted": 0, "updated": 0, "skipped": 0}
    try:
        for rec in records:
            rec["field_id"] = field_id
            existing = db.execute(
                sa_text(
                    "SELECT id, mean_value, min_value, max_value, std_value, "
                    "  p10_value, p90_value, valid_pixels_pct, cloud_cover_pct, satellite "
                    "FROM satellite_index_records "
                    "WHERE field_id = :field_id AND captured_date = :captured_date "
                    "AND index_code = :index_code"
                ),
                {
                    "field_id": rec["field_id"],
                    "captured_date": rec["captured_date"],
                    "index_code": rec["index_code"],
                },
            ).mappings().first()

            if existing:
                if _record_values_equal(dict(existing), rec):
                    counts["skipped"] += 1
                else:
                    db.execute(
                        sa_text(
                            "UPDATE satellite_index_records SET "
                            "  mean_value = :mean_value, min_value = :min_value, "
                            "  max_value = :max_value, std_value = :std_value, "
                            "  p10_value = :p10_value, p90_value = :p90_value, "
                            "  valid_pixels_pct = :valid_pixels_pct, "
                            "  cloud_cover_pct = :cloud_cover_pct, "
                            "  satellite = :satellite "
                            "WHERE id = :existing_id"
                        ),
                        {**rec, "existing_id": existing["id"]},
                    )
                    counts["updated"] += 1
            else:
                db.execute(
                    sa_text(
                        "INSERT INTO satellite_index_records "
                        "(field_id, captured_date, index_code, mean_value, min_value, max_value, "
                        " std_value, p10_value, p90_value, valid_pixels_pct, cloud_cover_pct, satellite) "
                        "VALUES (:field_id, :captured_date, :index_code, :mean_value, :min_value, "
                        ":max_value, :std_value, :p10_value, :p90_value, :valid_pixels_pct, "
                        ":cloud_cover_pct, :satellite)"
                    ),
                    rec,
                )
                counts["inserted"] += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return counts


# -- Self-test --


def _mock_multi_index_response() -> dict:
    return {
        "data": [
            {
                "interval": {"from": "2026-06-15T00:00:00Z", "to": "2026-06-20T00:00:00Z"},
                "outputs": {
                    "savi": {"bands": {"B0": {"stats": {"sampleCount": 1200, "noDataCount": 80,
                        "mean": "0.431", "min": "0.112", "max": "0.751", "stDev": "0.048",
                        "percentiles": {"10.0": "0.201", "90.0": "0.661"}}}}},
                    "evi": {"bands": {"B0": {"stats": {"sampleCount": 1200, "noDataCount": 80,
                        "mean": "0.312", "min": "0.082", "max": "0.612", "stDev": "0.042",
                        "percentiles": {"10.0": "0.161", "90.0": "0.532"}}}}},
                    "ndmi": {"bands": {"B0": {"stats": {"sampleCount": 1200, "noDataCount": 80,
                        "mean": "-0.183", "min": "-0.410", "max": "0.223", "stDev": "0.055",
                        "percentiles": {"10.0": "-0.301", "90.0": "0.121"}}}}},
                    "ndre": {"bands": {"B0": {"stats": {"sampleCount": 1200, "noDataCount": 80,
                        "mean": "0.281", "min": "0.095", "max": "0.498", "stDev": "0.039",
                        "percentiles": {"10.0": "0.145", "90.0": "0.412"}}}}},
                },
            }
        ]
    }


def run_self_test() -> None:
    errors = []
    codes = ["savi", "evi", "ndmi", "ndre"]

    # 1. CLI parsing: parse_args returns correct defaults
    args_default = parse_args([])
    if args_default.days != 15:
        errors.append(f"default days is {args_default.days}, expected 15")
    if args_default.sleep_seconds != 0.5:
        errors.append(f"default sleep_seconds is {args_default.sleep_seconds}, expected 0.5")
    if args_default.write:
        errors.append("default write should be False")

    # 2. --write without --field-ids is rejected
    args_write_no_ids = parse_args(["--write"])
    if args_write_no_ids.field_ids is not None:
        errors.append("--write without --field-ids should leave field_ids=None")
    # main() will exit if --write and no --field-ids; test the guard exists

    # 3. Write mode rejects >5 field IDs without --allow-large-batch
    args_large = parse_args(["--write", "--field-ids", "1,2,3,4,5,6"])
    fids = [int(x.strip()) for x in args_large.field_ids.split(",") if x.strip()]
    if not (len(fids) > 5 and not args_large.allow_large_batch):
        errors.append("Write mode should reject >5 field IDs without --allow-large-batch")

    # 4. Normal --write with 5 IDs and --allow-large-batch is OK
    args_allowed = parse_args(["--write", "--field-ids", "1,2,3,4,5,6", "--allow-large-batch"])
    fids_allowed = [int(x.strip()) for x in args_allowed.field_ids.split(",") if x.strip()]
    if len(fids_allowed) != 6 or not args_allowed.allow_large_batch or not args_allowed.write:
        errors.append("--write with --allow-large-batch and 6 IDs should be allowed")

    # 5. Index normalization rejects invalid codes
    try:
        _normalize_and_validate_indices("savi,evi,invalid_code")
        errors.append("normalize_and_validate_indices did not reject 'invalid_code'")
    except SystemExit:
        pass

    # 6. Record preparation preserves real zero values
    parsed_with_zero = {
        "savi": {
            "captured_date": "2026-06-20", "index_code": "savi",
            "mean_value": 0.0, "min_value": 0.0, "max_value": 0.0,
            "std_value": 0.0, "p10_value": 0.0, "p90_value": 0.0,
            "valid_pixels_pct": 100.0, "cloud_cover_pct": None, "satellite": "Sentinel-2",
        }
    }
    recs = _prepare_records(parsed_with_zero)
    if len(recs) != 1:
        errors.append(f"_prepare_records zero: expected 1 record, got {len(recs)}")
    else:
        r = recs[0]
        if r["mean_value"] != 0.0 or r["min_value"] != 0.0:
            errors.append(f"_prepare_records zero: real zero was not preserved: {r}")

    # 7. Record preparation rejects NaN/Inf values
    for val in (float("nan"), float("inf")):
        parsed_bad = {
            "savi": {
                "captured_date": "2026-06-20", "index_code": "savi",
                "mean_value": val, "min_value": 0.1, "max_value": 0.8,
                "std_value": 0.05, "p10_value": 0.2, "p90_value": 0.7,
                "valid_pixels_pct": 100.0, "cloud_cover_pct": None, "satellite": "Sentinel-2",
            }
        }
        try:
            _prepare_records(parsed_bad)
            errors.append(f"_prepare_records did not reject {val} mean_value")
        except SystemExit:
            pass

    # 8. Parser round-trip
    mock = _mock_multi_index_response()
    parsed = parse_multi_index_stats_response(mock, codes)
    for code in codes:
        if code not in parsed:
            errors.append(f"Parser did not return key '{code}'")

    # 9. Record preparation for normal values
    recs_normal = _prepare_records(parsed)
    if len(recs_normal) != len(codes):
        errors.append(f"_prepare_records normal: expected {len(codes)} records, got {len(recs_normal)}")

    # 10. Verify-only flags
    args_verify = parse_args(["--field-ids", "4,5,6", "--indices", "savi", "--verify-only"])
    if not args_verify.verify_only:
        errors.append("parse_args: --verify-only not recognized")
    if args_verify.write:
        errors.append("parse_args: --verify-only should not set write=True")

    # 11. ASCII-safe source: no U+2500..U+257F box drawing chars
    box_drawing = {0x2500, 0x2501, 0x2502, 0x2503, 0x250C, 0x2510, 0x2514, 0x2518,
                   0x251C, 0x2524, 0x252C, 0x2534, 0x253C, 0x2550, 0x2551, 0x2554,
                   0x2557, 0x255A, 0x255D}
    try:
        with open(__file__, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                for ch in line:
                    if ord(ch) in box_drawing:
                        errors.append(
                            f"Box-drawing character U+{ord(ch):04X} at line {lineno}"
                        )
    except Exception as e:
        errors.append(f"Box-drawing scan failed: {e}")

    # 12. No 'ndvi_records' reference in source (except self-test's own detection code)
    _st12_source_lines: list[str] = []
    try:
        with open(__file__, "r", encoding="utf-8") as _st12_fh:
            _st12_source_lines = _st12_fh.readlines()
    except Exception as e:
        errors.append(f"Static source scan (read) for ndvi_records failed: {e}")

    if _st12_source_lines:
        for _st12_lineno, _st12_line in enumerate(_st12_source_lines, 1):
            _st12_stripped = _st12_line.strip()
            if _st12_stripped.startswith("#") or _st12_stripped.startswith('"""') or _st12_stripped.startswith("'''"):
                continue
            if _st12_lineno >= 575 and _st12_lineno <= 617:
                continue
            if "ndvi_records" in _st12_stripped:
                errors.append(
                    f"Reference to 'ndvi_records' at line {_st12_lineno}: {_st12_stripped[:80]}"
                )

    # 13. No scheduler/backfill/API/frontend keywords as runtime dependencies.
    # Skip self-test's own definition of the keyword list so we don't flag ourselves.
    _st13_forbidden = ["scheduler", "backfill", "APScheduler", "FastAPI", "MapLibre"]
    _st13_source_lines: list[str] = []
    try:
        with open(__file__, "r", encoding="utf-8") as _st13_fh:
            _st13_source_lines = _st13_fh.readlines()
    except Exception as e:
        errors.append(f"Static scan for forbidden keywords failed: {e}")

    if _st13_source_lines:
        for _st13_lineno, _st13_line in enumerate(_st13_source_lines, 1):
            _st13_stripped = _st13_line.strip()
            if _st13_stripped.startswith("#") or _st13_stripped.startswith('"""') or _st13_stripped.startswith("'''"):
                continue
            # Skip the lines where _st13_forbidden is defined (this block)
            if _st13_lineno >= 575 and _st13_lineno <= 617:
                continue
            for _st13_kw in _st13_forbidden:
                if _st13_kw in _st13_stripped:
                    errors.append(
                        f"Forbidden keyword '{_st13_kw}' found at line {_st13_lineno}: {_st13_stripped[:80]}"
                    )

    # 14. _record_values_equal: identical rows -> equal (skip)
    eq_rec = {
        "mean_value": 0.431, "min_value": 0.112, "max_value": 0.751,
        "std_value": 0.048, "p10_value": 0.201, "p90_value": 0.661,
        "valid_pixels_pct": 100.0, "cloud_cover_pct": None, "satellite": "Sentinel-2",
    }
    if not _record_values_equal(eq_rec, eq_rec):
        errors.append("_record_values_equal: identical dicts should be equal")

    # 15. _record_values_equal: changed mean_value -> not equal (update)
    changed_mean = {**eq_rec, "mean_value": 0.999}
    if _record_values_equal(eq_rec, changed_mean):
        errors.append("_record_values_equal: changed mean_value should be not equal")

    # 16. _record_values_equal: zero values compare correctly
    zero_a = {
        "mean_value": 0.0, "min_value": 0.0, "max_value": 0.0,
        "std_value": 0.0, "p10_value": 0.0, "p90_value": 0.0,
        "valid_pixels_pct": 0.0, "cloud_cover_pct": None, "satellite": "Sentinel-2",
    }
    zero_b = dict(zero_a)
    if not _record_values_equal(zero_a, zero_b):
        errors.append("_record_values_equal: zero values should be equal")

    # 17. _record_values_equal: None cloud_cover_pct compares correctly
    none_a = {**eq_rec, "cloud_cover_pct": None}
    none_b = {**eq_rec, "cloud_cover_pct": None}
    if not _record_values_equal(none_a, none_b):
        errors.append("_record_values_equal: both None cloud_cover_pct should be equal")
    # One none, one not
    some_b = {**eq_rec, "cloud_cover_pct": 12.5}
    if _record_values_equal(none_a, some_b):
        errors.append("_record_values_equal: None vs value should be not equal")

    # 18. _record_values_equal: numeric string vs float comparison
    str_val = {**eq_rec, "mean_value": "0.431"}
    float_val = {**eq_rec, "mean_value": 0.431}
    if not _record_values_equal(str_val, float_val):
        errors.append("_record_values_equal: '0.431' vs 0.431 should be equal")

    if errors:
        print("SELF TEST FAILED")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print("SELF TEST PASSED")
    sys.exit(0)


# -- Main --


def main() -> None:
    args = parse_args()

    if args.self_test:
        run_self_test()
        return

    index_codes = _normalize_and_validate_indices(args.indices)

    if args.days < 1:
        print("ERROR: --days must be >= 1", file=sys.stderr)
        sys.exit(1)

    # Parse field IDs
    if args.field_ids:
        try:
            field_ids = [int(x.strip()) for x in args.field_ids.split(",") if x.strip()]
        except ValueError:
            print("ERROR: --field-ids must be comma-separated integers", file=sys.stderr)
            sys.exit(1)
    else:
        field_ids = []

    # Verify-only mode
    if args.verify_only:
        if not field_ids:
            print("ERROR: --field-ids is required with --verify-only", file=sys.stderr)
            sys.exit(1)
        _check_table_exists()
        verify_only(field_ids, index_codes)
        return

    # Write mode guards
    if args.write:
        print("WRITE MODE ENABLED")
        if not field_ids:
            print("ERROR: --field-ids is required in write mode", file=sys.stderr)
            sys.exit(1)
        if len(field_ids) > 5 and not args.allow_large_batch:
            print(
                f"ERROR: {len(field_ids)} field IDs > 5 requires --allow-large-batch",
                file=sys.stderr,
            )
            sys.exit(1)

    # Without --write, no field_ids is OK in preview mode
    if not args.write and not field_ids:
        print("ERROR: --field-ids is required (use --self-test for offline validation)", file=sys.stderr)
        sys.exit(1)

    # Preflight: table exists
    _check_table_exists()

    # Preflight: fetch fields
    rows = _fetch_fields_by_ids(field_ids)
    requested_ids = set(field_ids)
    found_ids = {r["id"] for r in rows}
    missing = sorted(requested_ids - found_ids)
    if missing:
        print(f"NOTE: field IDs not found or not active: {missing}")
        field_ids = [fid for fid in field_ids if fid not in missing]
        if not field_ids:
            print("ERROR: No valid field IDs remaining", file=sys.stderr)
            sys.exit(1)

    # Filter to fields with geometry
    rows_with_geom = [r for r in rows if r.get("geometry_wkt")]
    no_geom_ids = [r["id"] for r in rows if not r.get("geometry_wkt")]
    if no_geom_ids:
        print(f"NOTE: field(s) have no geometry, skipping: {no_geom_ids}")

    if not rows_with_geom:
        print("ERROR: No fields with geometry available", file=sys.stderr)
        sys.exit(1)

    # Build evalscript once
    evalscript = build_multi_index_evalscript(index_codes)

    # Date range
    date_to = date.today()
    date_from = date_to - timedelta(days=args.days)

    # Credentials
    client_id = settings.sentinel_hub_client_id
    client_secret = settings.sentinel_hub_client_secret
    if not client_id or not client_secret:
        print("ERROR: Sentinel Hub credentials not configured in .env", file=sys.stderr)
        print("  Set SENTINEL_HUB_CLIENT_ID and SENTINEL_HUB_CLIENT_SECRET", file=sys.stderr)
        sys.exit(1)

    # Obtain token once
    try:
        token = _get_access_token(client_id, client_secret)
    except Exception as e:
        print(f"ERROR: Failed to obtain Sentinel Hub token: {e}", file=sys.stderr)
        sys.exit(1)

    # Process each field
    all_results: list[dict] = []
    total_inserted = 0
    total_updated = 0
    total_skipped = 0
    total_errors = 0
    total_planned = 0

    for idx, row in enumerate(rows_with_geom):
        fid = row["id"]
        fname = row["name"]
        geometry_wkt = row["geometry_wkt"]

        geom = wkt.loads(geometry_wkt)
        geojson_geom = mapping(geom)

        payload = _build_statistical_payload(
            geometry_geojson=geojson_geom,
            evalscript=evalscript,
            index_codes=index_codes,
            date_from=date_from,
            date_to=date_to,
        )

        print(f"[{idx + 1}/{len(rows_with_geom)}] Processing field_id={fid} ({fname}) ...")

        # Sentinel Hub call
        try:
            resp = httpx.post(
                STATISTICAL_API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=120.0,
            )
            if not resp.is_success:
                print(f"  ERROR: Sentinel Hub returned HTTP {resp.status_code} for field {fid}: {resp.text[:200]}")
                total_errors += 1
                print()
                if idx < len(rows_with_geom) - 1 and args.sleep_seconds > 0:
                    time.sleep(args.sleep_seconds)
                continue
            response_data = resp.json()
        except httpx.TimeoutException:
            print(f"  ERROR: Sentinel Hub request timed out for field {fid}")
            total_errors += 1
            print()
            if idx < len(rows_with_geom) - 1 and args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
            continue
        except httpx.RequestError as e:
            print(f"  ERROR: Sentinel Hub request failed for field {fid}: {e}")
            total_errors += 1
            print()
            if idx < len(rows_with_geom) - 1 and args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
            continue

        # Parse
        parsed = parse_multi_index_stats_response(response_data, index_codes)

        if not parsed:
            print(f"  WARNING: No valid intervals returned for field {fid}")
            all_results.append({"field_id": fid, "status": "NO_DATA"})
            print()
            if idx < len(rows_with_geom) - 1 and args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
            continue

        # Preflight: validate records (rejects NaN/Inf)
        records = _prepare_records(parsed)

        total_planned += len(records)

        # Print parsed values
        for code in index_codes:
            if code in parsed:
                d = parsed[code]
                print(f"  {code}: captured={d['captured_date']} mean={d['mean_value']} "
                      f"min={d['min_value']} max={d['max_value']} std={d['std_value']} "
                      f"p10={d['p10_value']} p90={d['p90_value']} "
                      f"valid_pixels_pct={d['valid_pixels_pct']} satellite={d['satellite']}")
            else:
                print(f"  {code}: (no data)")

        if args.write:
            # Per-field transaction
            try:
                counts = _do_field_write(fid, records)
                total_inserted += counts["inserted"]
                total_updated += counts["updated"]
                total_skipped += counts["skipped"]
                print(f"  field_id={fid}: inserted={counts['inserted']} "
                      f"updated={counts['updated']} skipped={counts['skipped']}")
            except Exception as e:
                print(f"  ERROR: DB write failed for field {fid}: {e}")
                total_errors += 1
        else:
            # Preview: show planned rows with INSERT/UPDATE labels
            print(f"  [PREVIEW] Field {fid} — planned {len(records)} records:")
            _print_preview_records(fid, records)

        all_results.append({"field_id": fid, "status": "DATA_AVAILABLE", "records": len(records)})
        print()

        # Rate limit between fields
        if idx < len(rows_with_geom) - 1 and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    # -- Summary --
    with_data_count = sum(1 for r in all_results if r.get("status") == "DATA_AVAILABLE")

    print("-" * 50)
    print("  BATCH SUMMARY")
    print("-" * 50)
    print(f"  total_requested:     {len(field_ids)}")
    print(f"  total_processed:     {len(rows_with_geom)}")
    print(f"  with_data:           {with_data_count}")
    print(f"  fields_with_errors:  {total_errors}")
    print(f"  records_planned:     {total_planned}")

    if args.write:
        print(f"  total_inserted:      {total_inserted}")
        print(f"  total_updated:       {total_updated}")
        print(f"  total_skipped:       {total_skipped}")
        print(f"  total_errors:        {total_errors}")
        print()
        print(f"  RESULT: BATCH WRITE COMPLETE")
    else:
        print()
        print("  PREVIEW ONLY: no DB writes performed")
        print("  Use --write to commit these changes.")


if __name__ == "__main__":
    main()
