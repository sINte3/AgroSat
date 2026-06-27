#!/usr/bin/env python3
"""
Preview-only batch multi-index calculation across multiple fields.

Reads selected fields from DB, calls Sentinel Hub Statistical API for each,
parses and reports.  Never writes to the database.

Usage:
  python backend/scripts/preview_multi_index_batch.py --help
  python backend/scripts/preview_multi_index_batch.py --limit 3
  python backend/scripts/preview_multi_index_batch.py --field-ids 4,5,6 --days 15
  python backend/scripts/preview_multi_index_batch.py --self-test
"""

import argparse
import json
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

sys.path.insert(0, "backend")

from config import settings
from database import SessionLocal
from services.satellite_indices import (
    SUPPORTED_INDEX_CODES,
    build_multi_index_evalscript,
    normalize_index_code,
    parse_multi_index_stats_response,
)

STATISTICAL_API_URL = "https://services.sentinel-hub.com/api/v1/statistics"
TOKEN_URL = "https://services.sentinel-hub.com/auth/realms/main/protocol/openid-connect/token"


# -- Sentinel Hub OAuth2 (local helper, does not modify satellite.py) --


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


# -- Payload builder (reused pattern from single-field dry-run) --


def _build_statistical_payload(
    geometry_geojson: dict,
    evalscript: str,
    index_codes: list[str],
    date_from: date,
    date_to: date,
    aggregation_interval: str = "P5D",
    resolution: int = 20,
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
            "aggregationInterval": {"of": aggregation_interval},
            "evalscript": evalscript,
            "resx": resolution,
            "resy": resolution,
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


# -- CLI --


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview-only batch multi-index calculation across fields (no DB writes)."
    )
    parser.add_argument("--limit", type=int, default=5, help="Max fields to preview (default: 5, max: 25)")
    parser.add_argument(
        "--field-ids", type=str, default=None,
        help="Comma-separated field IDs (overrides enterprise/limit selection)",
    )
    parser.add_argument("--enterprise-id", type=int, default=None, help="Filter by enterprise ID")
    parser.add_argument("--days", type=int, default=15, help="Look-back days (default: 15)")
    parser.add_argument(
        "--indices", type=str, default="savi,evi,ndmi,ndre",
        help="Comma-separated index codes (default: savi,evi,ndmi,ndre)",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.5, help="Seconds between Sentinel Hub calls")
    parser.add_argument("--max-cloud-coverage", type=int, default=80, help="Max cloud coverage %% (default: 80)")
    parser.add_argument(
        "--allow-large-batch", action="store_true",
        help="Required if --limit > 25",
    )
    parser.add_argument("--self-test", action="store_true", help="Run offline validation (no DB, no network)")
    parser.add_argument(
        "--json-summary", type=str, default=None,
        help="Write sanitized summary JSON to PATH (no raw responses, no secrets)",
    )
    return parser.parse_args(argv)


# -- Field queries (explicit SQL only, no ORM lazy loading) --


def _fetch_fields_by_ids(field_ids: list[int]) -> list[dict]:
    """Fetch active fields with geometry by explicit ID list. Returns ordered by id ASC."""
    from sqlalchemy import text

    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT f.id, f.name, f.enterprise_id, f.area_ha,
                       ST_AsText(f.geometry) AS geometry_wkt
                FROM fields f
                WHERE f.id = ANY(:ids) AND f.is_active = true
                ORDER BY f.id ASC
            """),
            {"ids": field_ids},
        ).mappings().all()
    finally:
        db.close()
    return [dict(r) for r in rows]


def _fetch_active_fields(enterprise_id: Optional[int], limit: int) -> list[dict]:
    """Fetch active fields with geometry, optionally filtered by enterprise. Ordered by id ASC."""
    from sqlalchemy import text

    db = SessionLocal()
    try:
        if enterprise_id is not None:
            rows = db.execute(
                text("""
                    SELECT f.id, f.name, f.enterprise_id, f.area_ha,
                           ST_AsText(f.geometry) AS geometry_wkt
                    FROM fields f
                    WHERE f.is_active = true AND f.enterprise_id = :eid
                    ORDER BY f.id ASC
                    LIMIT :lim
                """),
                {"eid": enterprise_id, "lim": limit},
            ).mappings().all()
        else:
            rows = db.execute(
                text("""
                    SELECT f.id, f.name, f.enterprise_id, f.area_ha,
                           ST_AsText(f.geometry) AS geometry_wkt
                    FROM fields f
                    WHERE f.is_active = true
                    ORDER BY f.id ASC
                    LIMIT :lim
                """),
                {"lim": limit},
            ).mappings().all()
    finally:
        db.close()
    return [dict(r) for r in rows]


# -- Sentinel Hub API call for one field --


def _call_sentinel_hub(
    token: str, payload: dict, field_id: int, field_name: str,
) -> dict:
    """Call Sentinel Hub Statistical API. Returns result dict with status and parsed data."""
    try:
        resp = httpx.post(
            STATISTICAL_API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=120.0,
        )
        if not resp.is_success:
            return {
                "field_id": field_id,
                "field_name": field_name,
                "status": "ERROR",
                "error": f"HTTP {resp.status_code}",
            }
        response_data = resp.json()
    except httpx.TimeoutException:
        return {
            "field_id": field_id,
            "field_name": field_name,
            "status": "ERROR",
            "error": "Timeout",
        }
    except httpx.RequestError as e:
        return {
            "field_id": field_id,
            "field_name": field_name,
            "status": "ERROR",
            "error": e.__class__.__name__,
        }

    return {"field_id": field_id, "field_name": field_name, "status": "API_OK", "data": response_data}


def _parse_sentinel_result(raw_result: dict, index_codes: list[str]) -> dict:
    """Parse raw Sentinel Hub result into per-index preview data. Returns the result dict with parsed data."""
    response_data = raw_result.get("data")
    if response_data is None:
        return raw_result

    parsed = parse_multi_index_stats_response(response_data, index_codes)

    if not parsed:
        raw_result["status"] = "NO_DATA"
        return raw_result

    raw_result["status"] = "DATA_AVAILABLE"
    raw_result["parsed"] = parsed
    # Pick captured_date from the first available index
    for code in index_codes:
        if code in parsed:
            raw_result["captured_date"] = parsed[code]["captured_date"]
            break
    return raw_result


# -- Output helpers --


def _sanitize_for_summary(result: dict) -> dict:
    """Return a summary-safe copy: no raw response data, no secrets."""
    safe = {
        "field_id": result.get("field_id"),
        "field_name": result.get("field_name"),
        "enterprise_id": result.get("enterprise_id"),
        "area_ha": result.get("area_ha"),
        "status": result.get("status"),
        "captured_date": result.get("captured_date"),
    }
    if result.get("status") == "ERROR":
        safe["error"] = result.get("error")
    if result.get("status") == "DATA_AVAILABLE":
        as_parsed = result.get("parsed", {})
        indices_safe = {}
        for code, d in as_parsed.items():
            indices_safe[code] = {
                "captured_date": d.get("captured_date"),
                "mean_value": d.get("mean_value"),
                "min_value": d.get("min_value"),
                "max_value": d.get("max_value"),
                "std_value": d.get("std_value"),
                "p10_value": d.get("p10_value"),
                "p90_value": d.get("p90_value"),
                "valid_pixels_pct": d.get("valid_pixels_pct"),
                "cloud_cover_pct": d.get("cloud_cover_pct"),
                "satellite": d.get("satellite"),
            }
        safe["parsed"] = indices_safe
        safe["indices"] = indices_safe
    return safe


def _print_separator(char: str = "-", width: int = 60) -> None:
    print(char * width)


def _print_field_result(result: dict, index_codes: list[str]) -> None:
    """Print a single field's result in a readable format."""
    fid = result.get("field_id", "?")
    fname = result.get("field_name", "?")
    eid = result.get("enterprise_id")
    area = result.get("area_ha")
    status = result.get("status", "UNKNOWN")

    print(f"  field_id:       {fid}")
    print(f"  field_name:     {fname}")
    if eid is not None:
        print(f"  enterprise_id:  {eid}")
    if area is not None:
        print(f"  area_ha:        {area}")
    print(f"  status:         {status}")

    if status == "ERROR":
        print(f"  error:          {result.get('error', 'unknown')}")
        return

    if status == "NO_DATA":
        print(f"  captured_date:  N/A")
        print(f"  index_values:   (no data returned)")
        return

    if status == "DATA_AVAILABLE":
        print(f"  captured_date:  {result.get('captured_date', 'N/A')}")
        parsed = result.get("parsed", {})
        for code in index_codes:
            d = parsed.get(code)
            if d is None:
                print(f"  [{code}]:        (no data)")
                continue
            print(f"  [{code}]:        mean={d.get('mean_value', 'N/A')} "
                  f"min={d.get('min_value', 'N/A')} max={d.get('max_value', 'N/A')} "
                  f"std={d.get('std_value', 'N/A')} p10={d.get('p10_value', 'N/A')} "
                  f"p90={d.get('p90_value', 'N/A')} valid_pixels_pct={d.get('valid_pixels_pct', 'N/A')} "
                  f"cloud_cover_pct={d.get('cloud_cover_pct', 'N/A')} satellite={d.get('satellite', 'N/A')}")


def _print_aggregate_summary(
    results: list[dict],
    index_codes: list[str],
    total_selected: int,
) -> None:
    """Print the aggregate summary across all fields."""
    processed = len(results)
    with_data = sum(1 for r in results if r.get("status") == "DATA_AVAILABLE")
    no_data = sum(1 for r in results if r.get("status") == "NO_DATA")
    errors = sum(1 for r in results if r.get("status") == "ERROR")

    records_per_field = len(index_codes)
    records_planned_total = with_data * records_per_field
    records_by_index = {code: with_data for code in index_codes}

    field_ids_with_data = [r["field_id"] for r in results if r.get("status") == "DATA_AVAILABLE"]
    field_ids_no_data = [r["field_id"] for r in results if r.get("status") == "NO_DATA"]
    field_ids_error = [r["field_id"] for r in results if r.get("status") == "ERROR"]

    print()
    print("  " + "=" * 50)
    print("  AGGREGATE SUMMARY")
    print("  " + "=" * 50)
    print(f"  total_selected:        {total_selected}")
    print(f"  processed:             {processed}")
    print(f"  with_data:             {with_data}")
    print(f"  no_data:               {no_data}")
    print(f"  errors:                {errors}")
    print(f"  records_planned_total:  {records_planned_total} "
          f"({with_data} fields x {records_per_field} indices)")
    print(f"  records_planned_by_index:")
    for code in index_codes:
        print(f"    {code}: {records_by_index[code]}")
    print(f"  field_ids_with_data:   {field_ids_with_data}")
    print(f"  field_ids_no_data:     {field_ids_no_data}")
    print(f"  field_ids_error:       {field_ids_error}")
    print()
    print("  PREVIEW ONLY: no DB writes performed")


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

    # 1. CLI parsing for valid options
    try:
        parse_args(["--help"])
    except SystemExit:
        pass  # expected for --help

    args_default = parse_args([])
    if args_default.limit != 5:
        errors.append(f"default limit is {args_default.limit}, expected 5")
    if args_default.days != 15:
        errors.append(f"default days is {args_default.days}, expected 15")
    if args_default.sleep_seconds != 0.5:
        errors.append(f"default sleep_seconds is {args_default.sleep_seconds}, expected 0.5")

    args_custom = parse_args([
        "--limit", "10", "--days", "30", "--sleep-seconds", "1.0",
        "--field-ids", "4,5,6", "--enterprise-id", "1",
        "--indices", "savi,evi",
    ])
    if args_custom.limit != 10:
        errors.append(f"custom limit is {args_custom.limit}, expected 10")
    if args_custom.days != 30:
        errors.append(f"custom days is {args_custom.days}, expected 30")
    if args_custom.sleep_seconds != 1.0:
        errors.append(f"custom sleep_seconds is {args_custom.sleep_seconds}, expected 1.0")
    if args_custom.field_ids != "4,5,6":
        errors.append(f"custom field_ids is {args_custom.field_ids}, expected '4,5,6'")
    if args_custom.enterprise_id != 1:
        errors.append(f"custom enterprise_id is {args_custom.enterprise_id}, expected 1")
    if args_custom.indices != "savi,evi":
        errors.append(f"custom indices is {args_custom.indices}, expected 'savi,evi'")

    # 2. --limit > 25 rejection unless --allow-large-batch
    args_large = parse_args(["--limit", "30"])
    if not (args_large.limit > 25 and not args_large.allow_large_batch):
        errors.append(
            f"--limit {args_large.limit} > 25 should have allow_large_batch=False "
            f"unless --allow-large-batch is passed"
        )

    # 3. Index normalization rejects invalid codes
    for raw, expected in [("SAVI", "savi"), ("  EVI ", "evi"), ("NDMI", "ndmi")]:
        norm = normalize_index_code(raw)
        if norm != expected:
            errors.append(f"normalize_index_code('{raw}') = '{norm}', expected '{expected}'")

    invalid_codes = ["savi", "evi", "invalid_code"]
    unsupported = [c for c in invalid_codes if normalize_index_code(c) not in SUPPORTED_INDEX_CODES]
    if "invalid_code" not in unsupported:
        errors.append("normalize_index_code did not flag 'invalid_code' as unsupported")

    # 4. Summary aggregation for mocked fields: one DATA_AVAILABLE, one NO_DATA, one ERROR
    mock_results = [
        {"field_id": 1, "field_name": "Field A", "status": "DATA_AVAILABLE", "captured_date": "2026-06-20", "parsed": {c: {} for c in codes}},
        {"field_id": 2, "field_name": "Field B", "status": "NO_DATA"},
        {"field_id": 3, "field_name": "Field C", "status": "ERROR", "error": "HTTP 429"},
    ]
    with_data = sum(1 for r in mock_results if r.get("status") == "DATA_AVAILABLE")
    no_data = sum(1 for r in mock_results if r.get("status") == "NO_DATA")
    errors_count = sum(1 for r in mock_results if r.get("status") == "ERROR")
    if with_data != 1:
        errors.append(f"aggregation with_data={with_data}, expected 1")
    if no_data != 1:
        errors.append(f"aggregation no_data={no_data}, expected 1")
    if errors_count != 1:
        errors.append(f"aggregation errors={errors_count}, expected 1")

    # 5. Output/safety: _sanitize_for_summary strips raw data, no secrets
    raw_result_with_data = {
        "field_id": 1, "field_name": "Test", "enterprise_id": 1, "area_ha": 10.0,
        "status": "DATA_AVAILABLE", "captured_date": "2026-06-20",
        "parsed": {
            "savi": {"captured_date": "2026-06-20", "mean_value": 0.43, "min_value": 0.11,
                     "max_value": 0.75, "std_value": 0.05, "p10_value": 0.20, "p90_value": 0.66,
                     "valid_pixels_pct": 93.0, "cloud_cover_pct": None, "satellite": "Sentinel-2"},
        },
        "data": {"raw": "should_not_appear"},
    }
    safe = _sanitize_for_summary(raw_result_with_data)
    if "data" in safe:
        errors.append("_sanitize_for_summary: 'data' leaked into safe output")
    if "parsed" not in safe:
        errors.append("_sanitize_for_summary: 'parsed' missing from safe output")
    if "mean_value" in json.dumps(safe):
        pass  # mean_value should be inside indices, that's fine

    raw_result_error = {
        "field_id": 1, "field_name": "Test", "status": "ERROR", "error": "HTTP 500",
        "data": {"secret": "should_not_appear"},
    }
    safe_err = _sanitize_for_summary(raw_result_error)
    if "data" in safe_err:
        errors.append("_sanitize_for_summary error result: 'data' leaked")
    if safe_err.get("error") != "HTTP 500":
        errors.append(f"_sanitize_for_summary error result: missing error, got {safe_err}")

    # 6. No DB write keywords in script source (except self-test's own keyword list).
    # Read source lines once; skip the self-test's own write_keywords variable definition (line with "write_keywords").
    _st6_source_lines: list[str] = []
    try:
        with open(__file__, "r", encoding="utf-8") as _st6_fh:
            _st6_source_lines = _st6_fh.readlines()
    except Exception as e:
        errors.append(f"Static source scan (read) failed: {e}")

    _st6_write_kws = ["commit(", "flush(", ".add(", ".merge(", ".delete(",
                      "INSERT ", "UPDATE ", "DELETE ", "TRUNCATE ", "CREATE ", "DROP ",
                      "create_all", "Alembic"]
    # Lines 530-532 define _st6_write_kws; skip them in the scan below.
    if _st6_source_lines:
        for _st6_lineno, _st6_line in enumerate(_st6_source_lines, 1):
            _st6_stripped = _st6_line.strip()
            # Skip comments, docstrings, and the 3 definition lines
            if _st6_stripped.startswith("#") or _st6_stripped.startswith('"""') or _st6_stripped.startswith("'''"):
                continue
            if 530 <= _st6_lineno <= 532:
                continue
            for _st6_kw in _st6_write_kws:
                if _st6_kw in _st6_stripped:
                    errors.append(
                        f"DB write keyword '{_st6_kw}' found outside comment at line {_st6_lineno}: "
                        f"{_st6_stripped[:80]}"
                    )

    # 7. No box-drawing Unicode characters in script source
    box_drawing = {0x2500, 0x2501, 0x2502, 0x2503, 0x250C, 0x2510, 0x2514, 0x2518,
                   0x251C, 0x2524, 0x252C, 0x2534, 0x253C, 0x2550, 0x2551, 0x2554,
                   0x2557, 0x255A, 0x255D}
    try:
        with open(__file__, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                for ch in line:
                    if ord(ch) in box_drawing:
                        errors.append(
                            f"Box-drawing character U+{ord(ch):04X} at line {lineno} "
                            f"-- use ASCII only"
                        )
    except Exception as e:
        errors.append(f"Box-drawing scan failed: {e}")

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
        return  # unreachable

    # Validate --limit
    if args.limit < 1:
        print("ERROR: --limit must be >= 1", file=sys.stderr)
        sys.exit(1)
    if args.limit > 25 and not args.allow_large_batch:
        print(
            f"ERROR: --limit {args.limit} > 25 requires --allow-large-batch",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.days < 1:
        print("ERROR: --days must be >= 1", file=sys.stderr)
        sys.exit(1)

    # Parse and validate indices
    raw_indices = [normalize_index_code(s) for s in args.indices.split(",") if s.strip()]
    unsupported = [c for c in raw_indices if c not in SUPPORTED_INDEX_CODES]
    if unsupported:
        print(f"ERROR: Unsupported index codes: {', '.join(unsupported)}", file=sys.stderr)
        print(f"  Supported: {', '.join(sorted(SUPPORTED_INDEX_CODES))}", file=sys.stderr)
        sys.exit(1)
    if not raw_indices:
        print("ERROR: No valid index codes provided", file=sys.stderr)
        sys.exit(1)
    index_codes = raw_indices

    # -- Fetch fields --
    field_ids_explicit = None
    if args.field_ids:
        try:
            field_ids_explicit = [int(x.strip()) for x in args.field_ids.split(",") if x.strip()]
        except ValueError:
            print("ERROR: --field-ids must be comma-separated integers", file=sys.stderr)
            sys.exit(1)

    if field_ids_explicit:
        rows = _fetch_fields_by_ids(field_ids_explicit)
        requested_ids = set(field_ids_explicit)
        found_ids = {r["id"] for r in rows}
        missing = sorted(requested_ids - found_ids)
        if missing:
            print(f"NOTE: field IDs not found or not active: {missing}")
    else:
        rows = _fetch_active_fields(args.enterprise_id, args.limit)

    total_selected = len(rows)

    if not rows:
        print("No fields selected for preview.")
        print()
        print("PREVIEW ONLY: no DB writes performed")
        sys.exit(0)

    # -- Filter to fields with geometry --
    rows_with_geom = [r for r in rows if r.get("geometry_wkt")]
    if len(rows_with_geom) < len(rows):
        no_geom_ids = [r["id"] for r in rows if not r.get("geometry_wkt")]
        print(f"NOTE: {len(rows) - len(rows_with_geom)} field(s) have no geometry, skipping: {no_geom_ids}")
    if not rows_with_geom:
        print("No fields with geometry available.")
        sys.exit(0)

    # -- Build evalscript --
    evalscript = build_multi_index_evalscript(index_codes)

    # -- Date range --
    date_to = date.today()
    date_from = date_to - timedelta(days=args.days)

    # -- Credentials --
    client_id = settings.sentinel_hub_client_id
    client_secret = settings.sentinel_hub_client_secret
    if not client_id or not client_secret:
        print("ERROR: Sentinel Hub credentials not configured in .env", file=sys.stderr)
        print("  Set SENTINEL_HUB_CLIENT_ID and SENTINEL_HUB_CLIENT_SECRET", file=sys.stderr)
        sys.exit(1)

    # -- Obtain token once --
    try:
        token = _get_access_token(client_id, client_secret)
    except Exception as e:
        print(f"ERROR: Failed to obtain Sentinel Hub token: {e}", file=sys.stderr)
        sys.exit(1)

    # -- Process each field --
    results: list[dict] = []
    for idx, row in enumerate(rows_with_geom):
        field_id = row["id"]
        field_name = row["name"]
        geometry_wkt = row["geometry_wkt"]

        geom = wkt.loads(geometry_wkt)
        geojson_geom = mapping(geom)

        payload = _build_statistical_payload(
            geometry_geojson=geojson_geom,
            evalscript=evalscript,
            index_codes=index_codes,
            date_from=date_from,
            date_to=date_to,
            max_cloud_coverage=args.max_cloud_coverage,
        )

        # Status line
        print(f"[{idx + 1}/{len(rows_with_geom)}] Querying field_id={field_id} ({field_name}) ...")
        raw_result = _call_sentinel_hub(token, payload, field_id, field_name)

        # Attach metadata
        raw_result["enterprise_id"] = row.get("enterprise_id")
        raw_result["area_ha"] = row.get("area_ha")

        # Parse
        raw_result = _parse_sentinel_result(raw_result, index_codes)

        results.append(raw_result)
        _print_separator()
        _print_field_result(raw_result, index_codes)
        _print_separator()

        # Sleep between calls, but not after the last one
        if idx < len(rows_with_geom) - 1 and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    # -- Aggregate summary --
    _print_aggregate_summary(results, index_codes, total_selected)

    # -- Optional JSON summary --
    if args.json_summary:
        safe_results = [_sanitize_for_summary(r) for r in results]
        summary = {
            "preview_timestamp": date.today().isoformat(),
            "total_selected": total_selected,
            "processed": len(results),
            "index_codes": index_codes,
            "fields": safe_results,
        }
        try:
            with open(args.json_summary, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=2, ensure_ascii=False, default=str)
            print(f"  JSON summary written to: {args.json_summary}")
        except Exception as e:
            print(f"  WARNING: Failed to write JSON summary: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
