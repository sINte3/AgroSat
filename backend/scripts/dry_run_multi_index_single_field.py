#!/usr/bin/env python3
"""
Dry-run: multi-index satellite calculation for a single field (read-only).

Reads one field from DB, builds Sentinel Hub Statistical API payload, sends
it for each requested index, parses, validates quality, and prints a report.
Never writes to DB. Does not touch the existing NDVI pipeline.
"""

import argparse
import json
import logging
import math
import sys
import time
from datetime import date, timedelta
from typing import Optional

# Safe stdout/stderr for Windows CMD and redirected logs
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
from services.sentinel_provider import resolve_sentinel_provider
from services.satellite_indices import (
    SUPPORTED_INDEX_CODES,
    build_multi_index_evalscript,
    get_index_definition,
    normalize_index_code,
    parse_multi_index_stats_response,
    validate_index_quality,
    _interval_is_valid_for_index,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_SELECTED_ENDPOINTS = resolve_sentinel_provider(settings.sentinel_hub_provider)
STATISTICAL_API_URL = _SELECTED_ENDPOINTS.statistical_url
TOKEN_URL = _SELECTED_ENDPOINTS.token_url


# -- Sentinel Hub OAuth2 (local helper, does not modify satellite.py) --


def _get_access_token(client_id: str, client_secret: str) -> str:
    """Obtain an OAuth2 access token from Sentinel Hub (client credentials)."""
    if not client_id or not client_secret:
        raise ValueError(
            "Sentinel Hub credentials not configured. "
            "Set SENTINEL_HUB_CLIENT_ID and SENTINEL_HUB_CLIENT_SECRET in .env"
        )
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"]


# -- CLI --


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run: multi-index satellite calculation for one field (read-only)."
    )
    parser.add_argument("--field-id", type=int, default=None, help="Field ID to query")
    parser.add_argument("--days", type=int, default=15, help="Look-back days (default: 15)")
    parser.add_argument(
        "--indices",
        type=str,
        default="savi,evi,ndmi,ndre",
        help="Comma-separated index codes (default: savi,evi,ndmi,ndre)",
    )
    parser.add_argument(
        "--aggregation-interval", type=str, default="P5D", help="Aggregation interval (default: P5D)"
    )
    parser.add_argument("--resolution", type=int, default=20, help="Resolution in meters (default: 20)")
    parser.add_argument("--max-cloud-coverage", type=int, default=80, help="Max cloud coverage %% (default: 80)")
    parser.add_argument("--self-test", action="store_true", help="Run offline self-test (no DB, no network)")
    parser.add_argument("--debug-raw", action="store_true", help="Print raw API response diagnostic section")
    parser.add_argument(
        "--dump-raw-json", type=str, default=None,
        help="Write raw API response JSON to PATH (no credentials)",
    )
    parser.add_argument(
        "--print-payload-summary", action="store_true",
        help="Print sanitized request payload summary",
    )
    return parser.parse_args(argv)


# -- Payload builder (testable helper) --


def build_statistical_payload(
    geometry_geojson: dict,
    evalscript: str,
    index_codes: list[str],
    date_from: date,
    date_to: date,
    aggregation_interval: str = "P5D",
    resolution: int = 20,
    max_cloud_coverage: int = 80,
) -> dict:
    """Build a Sentinel Hub Statistical API payload dict (no IO)."""
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


# -- Diagnostic helpers --


def _sanitize_geometry_type(geom: dict) -> str:
    """Return geometry type string without exposing coordinates."""
    return geom.get("type", "unknown")


def _print_payload_summary(payload: dict, index_codes: list[str]) -> None:
    """Print a sanitized summary of the request payload (no credentials, no full coords)."""
    print()
    print("--- PAYLOAD SUMMARY ---")
    data_list = payload.get("input", {}).get("data", [])
    if data_list:
        print(f"  collection:       {data_list[0].get('type', 'N/A')}")
        df = data_list[0].get("dataFilter", {})
        tr = df.get("timeRange", {})
        print(f"  date_from:        {tr.get('from', 'N/A')}")
        print(f"  date_to:          {tr.get('to', 'N/A')}")
        print(f"  maxCloudCoverage: {df.get('maxCloudCoverage', 'N/A')}")
    agg = payload.get("aggregation", {})
    print(f"  aggregation_int:  {agg.get('aggregationInterval', {}).get('of', 'N/A')}")
    print(f"  resx:             {agg.get('resx', 'N/A')}")
    print(f"  resy:             {agg.get('resy', 'N/A')}")
    bounds = payload.get("input", {}).get("bounds", {})
    print(f"  geometry_type:    {_sanitize_geometry_type(bounds.get('geometry', {}))}")
    calc = payload.get("calculations", {})
    print(f"  requested_indices:{', '.join(sorted(calc.keys()))}")
    for code in index_codes:
        if code in calc:
            stats = calc[code].get("statistics", {})
            default_stats = stats.get("default", {})
            pcts = default_stats.get("percentiles", {})
            print(f"    {code}: percentiles k={pcts.get('k', [])}")
    evalscript = agg.get("evalscript", "")
    print(f"  evalscript_len:   {len(evalscript)} chars")
    print(f"  evalscript_hash:  {hash(evalscript) % (10**8):08x}")
    print("--- END PAYLOAD SUMMARY ---")
    print()


def _print_debug_raw(response_data: dict, index_codes: list[str]) -> None:
    """Print a detailed diagnostic breakdown of the raw API response."""
    print()
    print("--- DEBUG RAW RESPONSE ---")
    print(f"  top-level JSON keys: {list(response_data.keys())}")
    intervals = response_data.get("data", [])
    print(f"  intervals count:     {len(intervals)}")
    for i_idx, interval in enumerate(intervals):
        i_from = interval.get("interval", {}).get("from", "N/A")
        i_to = interval.get("interval", {}).get("to", "N/A")
        print(f"  interval[{i_idx}]: {i_from} -> {i_to}")
        outputs = interval.get("outputs", {})
        print(f"    output keys: {list(outputs.keys())}")
        for code in index_codes:
            if code not in outputs:
                print(f"    [{code}]: MISSING in this interval")
                continue
            bands = outputs[code].get("bands", {})
            print(f"    [{code}] bands keys: {list(bands.keys())}")
            band = bands.get("B0", {})
            stats = band.get("stats", {})
            if not stats:
                print(f"    [{code}] stats: EMPTY or missing")
                continue
            print(f"    [{code}] stats keys: {list(stats.keys())}")
            print(f"      sampleCount:  {stats.get('sampleCount', 'N/A')}")
            print(f"      noDataCount:  {stats.get('noDataCount', 'N/A')}")
            print(f"      mean:         {stats.get('mean', 'N/A')}")
            print(f"      min:          {stats.get('min', 'N/A')}")
            print(f"      max:          {stats.get('max', 'N/A')}")
            print(f"      stDev:        {stats.get('stDev', 'N/A')}")
            pcts = stats.get("percentiles", {})
            if pcts:
                print(f"      percentiles:  {pcts}")
    # Show which interval the parser would select for each index
    print()
    print("  Parser selection per index (latest valid interval):")
    for code in index_codes:
        valid_idxs = []
        for i_idx, interval in enumerate(intervals):
            ok, _ = _interval_is_valid_for_index(interval, code)
            if ok:
                valid_idxs.append(i_idx)
        if valid_idxs:
            latest = valid_idxs[-1]
            print(f"    {code}: valid intervals = {valid_idxs}, selected = interval[{latest}]")
        else:
            print(f"    {code}: NO valid intervals (NaN/noData or missing)")
    print("--- END DEBUG RAW RESPONSE ---")
    print()


# -- Self-test --


def _mock_multi_index_response() -> dict:
    """Return a representative mocked Statistical API response for all 4 indices."""
    return {
        "data": [
            {
                "interval": {"from": "2026-06-15T00:00:00Z", "to": "2026-06-20T00:00:00Z"},
                "outputs": {
                    "savi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 1200,
                                    "noDataCount": 80,
                                    "mean": "0.431",
                                    "min": "0.112",
                                    "max": "0.751",
                                    "stDev": "0.048",
                                    "percentiles": {"10.0": "0.201", "90.0": "0.661"},
                                }
                            }
                        }
                    },
                    "evi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 1200,
                                    "noDataCount": 80,
                                    "mean": "0.312",
                                    "min": "0.082",
                                    "max": "0.612",
                                    "stDev": "0.042",
                                    "percentiles": {"10.0": "0.161", "90.0": "0.532"},
                                }
                            }
                        }
                    },
                    "ndmi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 1200,
                                    "noDataCount": 80,
                                    "mean": "-0.183",
                                    "min": "-0.410",
                                    "max": "0.223",
                                    "stDev": "0.055",
                                    "percentiles": {"10.0": "-0.301", "90.0": "0.121"},
                                }
                            }
                        }
                    },
                    "ndre": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 1200,
                                    "noDataCount": 80,
                                    "mean": "0.281",
                                    "min": "0.095",
                                    "max": "0.498",
                                    "stDev": "0.039",
                                    "percentiles": {"10.0": "0.145", "90.0": "0.412"},
                                }
                            }
                        }
                    },
                },
            }
        ]
    }


def run_self_test() -> None:
    """Offline validation: no DB, no network, no credentials."""
    errors = []

    # --- Static check: no box-drawing Unicode characters in this file ---
    # Represented as code points so the source stays ASCII and doesn't trip on itself.
    _box_drawing = {0x2500, 0x2501, 0x2502, 0x2503, 0x250C, 0x2510, 0x2514, 0x2518,
                    0x251C, 0x2524, 0x252C, 0x2534, 0x253C, 0x2550, 0x2551, 0x2554,
                    0x2557, 0x255A, 0x255D}
    try:
        with open(__file__, "r", encoding="utf-8") as _fh:
            for _lineno, _line in enumerate(_fh, 1):
                for _ch in _line:
                    if ord(_ch) in _box_drawing:
                        errors.append(
                            f"Box-drawing character U+{ord(_ch):04X} found at line {_lineno} "
                            f"-- use ASCII only for Windows portability"
                        )
    except Exception as e:
        errors.append(f"Static scan failed: {e}")
    if errors:
        _fail_self_test(errors)
    codes = ["savi", "evi", "ndmi", "ndre"]

    # --- Evalscript ---
    try:
        script = build_multi_index_evalscript(codes)
    except Exception as e:
        errors.append(f"build_multi_index_evalscript failed: {e}")
        _fail_self_test(errors)

    for band in ["B02", "B04", "B08", "B05", "B8A", "B11", "SCL"]:
        if band not in script:
            errors.append(f"Evalscript missing required band {band}")

    for code in codes:
        if code not in script:
            errors.append(f"Evalscript missing output for index '{code}'")

    if "let s = sample;" not in script:
        errors.append("Evalscript missing 'let s = sample;'")

    # --- Build payload with mock geometry ---
    mock_geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    d_from = date(2026, 1, 1)
    d_to = date(2026, 1, 31)

    payload = build_statistical_payload(
        geometry_geojson=mock_geom,
        evalscript=script,
        index_codes=codes,
        date_from=d_from,
        date_to=d_to,
        aggregation_interval="P5D",
        resolution=20,
        max_cloud_coverage=80,
    )

    # 1. Payload calculations keys exactly match requested codes
    if "calculations" not in payload:
        errors.append("Payload missing 'calculations' key")
    else:
        calc_keys = sorted(payload["calculations"].keys())
        expected_keys = sorted(codes)
        if calc_keys != expected_keys:
            errors.append(
                f"Calculations keys {calc_keys} do not match requested codes {expected_keys}"
            )

        # 2. Each calculation requests percentiles 10 and 90
        for code in codes:
            calc = payload["calculations"][code]
            stats = calc.get("statistics", {})
            default_stats = stats.get("default", {})
            percentiles = default_stats.get("percentiles", {})
            k_vals = percentiles.get("k", [])
            if 10 not in k_vals or 90 not in k_vals:
                errors.append(f"Calculation '{code}' missing percentiles 10/90, got {k_vals}")

    # 3. Evalscript contains 'let s = sample;'
    agg_script = payload.get("aggregation", {}).get("evalscript", "")
    if "let s = sample;" not in agg_script:
        errors.append("Aggregation evalscript missing 'let s = sample;'")

    # 4. Default resolution is 20
    resx = payload.get("aggregation", {}).get("resx")
    resy = payload.get("aggregation", {}).get("resy")
    if resx != 20:
        errors.append(f"resx is {resx}, expected 20")
    if resy != 20:
        errors.append(f"resy is {resy}, expected 20")

    # 5. harmonizeValues is True
    data_list = payload.get("input", {}).get("data", [])
    if not data_list:
        errors.append("Payload input.data is empty")
    else:
        processing = data_list[0].get("processing", {})
        if processing.get("harmonizeValues") is not True:
            errors.append(f"harmonizeValues is not True: {processing.get('harmonizeValues')}")

    # 6. maxCloudCoverage is present
    data_filter = data_list[0].get("dataFilter", {}) if data_list else {}
    if "maxCloudCoverage" not in data_filter:
        errors.append("maxCloudCoverage is missing from dataFilter")

    # 7. Sentinel-2 L2A type
    data_type = data_list[0].get("type", "") if data_list else ""
    if data_type != "sentinel-2-l2a":
        errors.append(f"Data type is '{data_type}', expected 'sentinel-2-l2a'")

    # --- Parse mocked multi-index response ---
    mock = _mock_multi_index_response()
    parsed = parse_multi_index_stats_response(mock, codes)
    for code in codes:
        if code not in parsed:
            errors.append(f"Parser did not return key '{code}'")
        else:
            d = parsed[code]
            if d.get("index_code") != code:
                errors.append(f"Parser index_code mismatch for '{code}'")

    # --- Validate quality: SAVI accepted, negative NDMI accepted ---
    if "savi" in parsed:
        ok, reason = validate_index_quality(
            "savi", parsed["savi"].get("mean_value"),
            cloud_cover_pct=15, valid_pixels_pct=parsed["savi"].get("valid_pixels_pct"),
            min_value=parsed["savi"].get("min_value"), max_value=parsed["savi"].get("max_value"),
        )
        if not ok:
            errors.append(f"SAVI quality rejected unexpectedly: {reason}")

    if "ndmi" in parsed:
        ok, reason = validate_index_quality(
            "ndmi", parsed["ndmi"].get("mean_value"),
            cloud_cover_pct=15, valid_pixels_pct=parsed["ndmi"].get("valid_pixels_pct"),
            min_value=parsed["ndmi"].get("min_value"), max_value=parsed["ndmi"].get("max_value"),
        )
        if not ok:
            errors.append(f"NDMI (-0.183) quality rejected unexpectedly: {reason}")

    if errors:
        _fail_self_test(errors)

    # --- Diagnostic helpers smoke-test ---
    try:
        _print_payload_summary(payload, codes)
    except Exception as e:
        errors.append(f"_print_payload_summary crashed: {e}")

    try:
        _print_debug_raw(mock, codes)
    except Exception as e:
        errors.append(f"_print_debug_raw crashed: {e}")

    # --- Mock a zero-value response for debug-raw coverage ---
    zero_mock = {
        "data": [
            {
                "interval": {"from": "2026-06-10T00:00:00Z", "to": "2026-06-15T00:00:00Z"},
                "outputs": {
                    "savi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 500,
                                    "noDataCount": 0,
                                    "mean": "0.0",
                                    "min": "0.0",
                                    "max": "0.0",
                                    "stDev": "0.0",
                                    "percentiles": {"10.0": "0.0", "90.0": "0.0"},
                                }
                            }
                        }
                    },
                },
            },
            {
                "interval": {"from": "2026-06-05T00:00:00Z", "to": "2026-06-10T00:00:00Z"},
                "outputs": {
                    "savi": {
                        "bands": {
                            "B0": {
                                "stats": {
                                    "sampleCount": 800,
                                    "noDataCount": 0,
                                    "mean": "0.45",
                                    "min": "0.10",
                                    "max": "0.80",
                                    "stDev": "0.05",
                                    "percentiles": {"10.0": "0.20", "90.0": "0.70"},
                                }
                            }
                        }
                    },
                },
            },
        ]
    }
    try:
        _print_debug_raw(zero_mock, ["savi", "evi"])
    except Exception as e:
        errors.append(f"_print_debug_raw(zero_mock) crashed: {e}")

    if errors:
        _fail_self_test(errors)

    print("SELF TEST PASSED")
    sys.exit(0)


def _fail_self_test(errors: list[str]) -> None:
    print("SELF TEST FAILED")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)


# -- Main dry-run --


def dry_run(args: argparse.Namespace) -> None:
    """Execute the dry-run: read field, call API, parse, validate, report."""

    # --- Parse and validate indices ---
    raw_indices = [normalize_index_code(s) for s in args.indices.split(",") if s.strip()]
    unsupported = [c for c in raw_indices if c not in SUPPORTED_INDEX_CODES]
    if unsupported:
        print(f"ERROR: Unsupported index codes: {', '.join(unsupported)}")
        print(f"  Supported: {', '.join(sorted(SUPPORTED_INDEX_CODES))}")
        sys.exit(1)
    index_codes = raw_indices
    if not index_codes:
        print("ERROR: No valid index codes provided")
        sys.exit(1)

    # --- Read field from DB ---
    from sqlalchemy import text

    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                SELECT id, name, area_ha, ST_AsText(geometry) AS geometry_wkt
                FROM fields
                WHERE id = :field_id AND is_active = true
            """),
            {"field_id": args.field_id},
        ).mappings().first()
    finally:
        db.close()

    if not row:
        print(f"ERROR: No active field found with id={args.field_id}")
        sys.exit(1)

    field_id = row["id"]
    field_name = row["name"]
    area_ha = row["area_ha"]
    geometry_wkt = row["geometry_wkt"]

    # --- Dates ---
    date_to = date.today()
    date_from = date_to - timedelta(days=args.days)
    date_fmt = "%Y-%m-%d"

    # --- Credentials check ---
    client_id = settings.sentinel_hub_client_id
    client_secret = settings.sentinel_hub_client_secret
    credentials_present = bool(client_id and client_secret)

    if not credentials_present:
        print("ERROR: Sentinel Hub credentials not configured in .env")
        print("  Set SENTINEL_HUB_CLIENT_ID and SENTINEL_HUB_CLIENT_SECRET")
        sys.exit(1)

    # --- Build evalscript and geometry ---
    evalscript = build_multi_index_evalscript(index_codes)
    geom = wkt.loads(geometry_wkt)
    geojson_geom = mapping(geom)

    # --- Build payload ---
    payload = build_statistical_payload(
        geometry_geojson=geojson_geom,
        evalscript=evalscript,
        index_codes=index_codes,
        date_from=date_from,
        date_to=date_to,
        aggregation_interval=args.aggregation_interval,
        resolution=args.resolution,
        max_cloud_coverage=args.max_cloud_coverage,
    )

    # --- Send request ---
    try:
        token = _get_access_token(client_id, client_secret)
    except Exception as e:
        print(f"ERROR: Failed to obtain Sentinel Hub token: {e}")
        sys.exit(1)

    api_sent = True
    try:
        resp = httpx.post(
            STATISTICAL_API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=120.0,
        )
        print(f"  HTTP {resp.status_code} {'OK' if resp.is_success else 'FAILED'}")
        if not resp.is_success:
            body = resp.text[:500]
            print(f"  Response: {body}")
            sys.exit(1)
        response_data = resp.json()
    except httpx.TimeoutException:
        print("ERROR: Sentinel Hub request timed out")
        sys.exit(1)
    except httpx.RequestError as e:
        print(f"ERROR: Sentinel Hub request failed: {e}")
        sys.exit(1)

    # --- Diagnostic mode: payload summary ---
    if args.print_payload_summary:
        _print_payload_summary(payload, index_codes)

    # --- Diagnostic mode: raw JSON dump ---
    if args.dump_raw_json:
        try:
            dump_path = args.dump_raw_json
            with open(dump_path, "w", encoding="utf-8") as fh:
                json.dump(response_data, fh, indent=2, ensure_ascii=False)
            print(f"  Raw JSON dumped to: {dump_path}")
        except Exception as e:
            print(f"  WARNING: Failed to dump raw JSON: {e}")

    # --- Diagnostic mode: debug-raw ---
    if args.debug_raw:
        _print_debug_raw(response_data, index_codes)

    # --- Parse ---
    parsed = parse_multi_index_stats_response(response_data, index_codes)

    # --- Report ---
    print()
    print("=" * 60)
    print("  MULTI-INDEX DRY-RUN REPORT")
    print("=" * 60)
    print(f"  field_id:            {field_id}")
    print(f"  field_name:          {field_name}")
    print(f"  area_ha:             {area_ha}")
    print(f"  date_from:           {date_from.strftime(date_fmt)}")
    print(f"  date_to:             {date_to.strftime(date_fmt)}")
    print(f"  indices requested:   {', '.join(index_codes)}")
    print(f"  aggregation_interval:{args.aggregation_interval}")
    print(f"  resolution (m):      {args.resolution}")
    print(f"  max_cloud_coverage:  {args.max_cloud_coverage}%")
    print(f"  credentials present: {credentials_present}")
    print(f"  API request sent:    {api_sent}")
    raw_count = len(response_data.get("data", []))
    print(f"  raw intervals:       {raw_count}")
    print()

    if not parsed:
        print("  WARNING: No valid intervals returned for any requested index.")
        print("  DRY-RUN RESULT: NO DATA (possible cloud cover or coverage gap)")
        sys.exit(0)

    for code in index_codes:
        print(f"  -- {code.upper()} --")
        if code not in parsed:
            print(f"    No data returned")
            continue
        d = parsed[code]
        print(f"    captured_date:      {d.get('captured_date', 'N/A')}")
        print(f"    mean:               {d.get('mean_value', 'N/A')}")
        print(f"    min:                {d.get('min_value', 'N/A')}")
        print(f"    max:                {d.get('max_value', 'N/A')}")
        print(f"    std:                {d.get('std_value', 'N/A')}")
        print(f"    p10:                {d.get('p10_value', 'N/A')}")
        print(f"    p90:                {d.get('p90_value', 'N/A')}")
        print(f"    valid_pixels_pct:   {d.get('valid_pixels_pct', 'N/A')}%")
        print(f"    satellite:          {d.get('satellite', 'N/A')}")

        valid, reason = validate_index_quality(
            code,
            d.get("mean_value"),
            cloud_cover_pct=d.get("cloud_cover_pct"),
            min_value=d.get("min_value"),
            max_value=d.get("max_value"),
            valid_pixels_pct=d.get("valid_pixels_pct"),
        )
        status = "ACCEPTED" if valid else "REJECTED"
        print(f"    quality:            {status} ({reason})")
        print()

    print(f"  DRY-RUN RESULT: {'DATA AVAILABLE' if any(code in parsed for code in index_codes) else 'NO DATA'}")


# -- Entry point --


def main() -> None:
    args = parse_args()

    if args.self_test:
        run_self_test()
        return  # unreachable

    if args.field_id is None:
        print("ERROR: --field-id is required (use --self-test for offline validation)")
        sys.exit(1)

    if args.days < 1:
        print("ERROR: --days must be >= 1")
        sys.exit(1)

    if args.resolution < 1:
        print("ERROR: --resolution must be >= 1")
        sys.exit(1)

    dry_run(args)


if __name__ == "__main__":
    main()
