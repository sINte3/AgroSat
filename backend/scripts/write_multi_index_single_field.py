#!/usr/bin/env python3
"""
Controlled, idempotent, single-field write for parsed multi-index Sentinel-2
results into the satellite_index_records table.

Default mode = preview/no-write.  Actual DB mutation requires explicit --write.
Supports --verify-only (read satellite_index_records, no Sentinel Hub, no write).

Usage:
  python backend\\scripts\\write_multi_index_single_field.py --help
  python backend\\scripts\\write_multi_index_single_field.py --field-id 4 --days 15 --indices savi,evi,ndmi,ndre
  python backend\\scripts\\write_multi_index_single_field.py --field-id 4 --days 15 --indices savi,evi,ndmi,ndre --write
  python backend\\scripts\\write_multi_index_single_field.py --field-id 4 --indices savi,evi,ndmi,ndre --verify-only
  python backend\\scripts\\write_multi_index_single_field.py --self-test
"""

import argparse
import math
import os
import sys
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from services.satellite_safety import validate_batch_provenance, validate_credentials
from database import SessionLocal, engine
from models.monitoring import SatelliteIndexRecord
from services.satellite_indices import (
    SUPPORTED_INDEX_CODES,
    build_multi_index_evalscript,
    normalize_index_code,
    parse_multi_index_stats_response,
    validate_index_quality,
)

STATISTICAL_API_URL = "https://services.sentinel-hub.com/api/v1/statistics"
TOKEN_URL = "https://services.sentinel-hub.com/auth/realms/main/protocol/openid-connect/token"


# ── Helpers (reused from dry_run, kept local for isolation) ────────────────


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


# ── Preflight checks ───────────────────────────────────────────────────────


def _check_table_exists() -> None:
    """Exit if satellite_index_records table does not exist."""
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


def _check_field(field_id: int) -> tuple[int, str, str]:
    """Return (id, name, geometry_wkt) for an active field. Exit if missing."""
    db = SessionLocal()
    try:
        row = db.execute(
            sa_text(
                "SELECT id, name, ST_AsText(geometry) AS geometry_wkt "
                "FROM fields WHERE id = :field_id AND is_active = true"
            ),
            {"field_id": field_id},
        ).mappings().first()
    finally:
        db.close()

    if not row:
        print(f"ERROR: No active field found with id={field_id}", file=sys.stderr)
        sys.exit(1)
    if not row["geometry_wkt"]:
        print(f"ERROR: Field id={field_id} has no geometry", file=sys.stderr)
        sys.exit(1)
    return row["id"], row["name"], row["geometry_wkt"]


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


# ── Record preparation ─────────────────────────────────────────────────────


def _prepare_records(parsed: dict[str, dict]) -> list[dict]:
    """Convert parsed results into row dicts for upsert. Rejects NaN/Inf."""
    records = []
    for code, d in parsed.items():
        for key in ("mean_value", "min_value", "max_value", "std_value", "valid_pixels_pct"):
            val = d.get(key)
            if val is not None:
                try:
                    fv = float(val)
                    if math.isnan(fv) or math.isinf(fv):
                        print(f"ERROR: {code} field '{key}' is NaN/Inf ({val}) — aborting write", file=sys.stderr)
                        sys.exit(1)
                except (TypeError, ValueError):
                    pass  # None is fine
        records.append({
            "field_id": None,  # filled by caller
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
            "satellite": d.get("satellite"),
        })
    return records


# ── CLI ─────────────────────────────────────────────────────────────────────


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Controlled single-field write for multi-index Sentinel-2 results."
    )
    parser.add_argument("--field-id", type=int, default=None, help="Field ID to query")
    parser.add_argument("--days", type=int, default=15, help="Look-back days (default: 15)")
    parser.add_argument(
        "--indices", type=str, default="savi,evi,ndmi,ndre",
        help="Comma-separated index codes (default: savi,evi,ndmi,ndre)",
    )
    parser.add_argument(
        "--write", action="store_true", help="Actually write to DB (default: preview/no-write)"
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="Read satellite_index_records for field/indices (no Sentinel Hub, no write)",
    )
    parser.add_argument("--self-test", action="store_true", help="Run offline self-test")
    return parser.parse_args(argv)


# ── Verification mode ──────────────────────────────────────────────────────


def verify_only(field_id: int, index_codes: list[str]) -> None:
    """Read satellite_index_records for the field and print rows."""
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


# ── Write logic ────────────────────────────────────────────────────────────


def do_write(field_id: int, records: list[dict]) -> dict[str, int]:
    """Upsert rows into satellite_index_records. Returns {inserted, updated, skipped}."""
    records = validate_batch_provenance(records)
    db = SessionLocal()
    counts = {"inserted": 0, "updated": 0, "skipped": 0}

    try:
        for rec in records:
            rec["field_id"] = field_id
            # Check if row exists
            existing = db.execute(
                sa_text(
                    "SELECT id FROM satellite_index_records "
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
                # Update
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
                # Check if values actually changed
                same = db.execute(
                    sa_text(
                        "SELECT 1 FROM satellite_index_records "
                        "WHERE id = :eid AND "
                        "  mean_value IS NOT DISTINCT FROM :mean_value AND "
                        "  min_value IS NOT DISTINCT FROM :min_value AND "
                        "  max_value IS NOT DISTINCT FROM :max_value AND "
                        "  std_value IS NOT DISTINCT FROM :std_value AND "
                        "  p10_value IS NOT DISTINCT FROM :p10_value AND "
                        "  p90_value IS NOT DISTINCT FROM :p90_value AND "
                        "  valid_pixels_pct IS NOT DISTINCT FROM :valid_pixels_pct AND "
                        "  cloud_cover_pct IS NOT DISTINCT FROM :cloud_cover_pct AND "
                        "  satellite IS NOT DISTINCT FROM :satellite"
                    ),
                    {**rec, "eid": existing["id"]},
                ).scalar()
                if same:
                    counts["skipped"] += 1
                else:
                    counts["updated"] += 1
            else:
                # Insert
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


# ── Preview mode ───────────────────────────────────────────────────────────


def print_preview(records: list[dict], field_id: int, index_codes: list[str]) -> None:
    """Print planned rows and whether each would be inserted or updated."""
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


# ── Self-test ──────────────────────────────────────────────────────────────


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

    # 1. CLI/index normalization rejects unsupported codes
    try:
        _normalize_and_validate_indices("savi,evi,invalid_code")
        errors.append("normalize_and_validate_indices did not reject 'invalid_code'")
    except SystemExit:
        pass  # expected

    # 2. Record preparation preserves real zero values
    parsed_with_zero = {
        "savi": {
            "captured_date": "2026-06-20",
            "index_code": "savi",
            "mean_value": 0.0,
            "min_value": 0.0,
            "max_value": 0.0,
            "std_value": 0.0,
            "p10_value": 0.0,
            "p90_value": 0.0,
            "valid_pixels_pct": 100.0,
            "cloud_cover_pct": None,
            "satellite": "Sentinel-2",
        }
    }
    recs = _prepare_records(parsed_with_zero)
    if len(recs) != 1:
        errors.append(f"_prepare_records zero: expected 1 record, got {len(recs)}")
    else:
        r = recs[0]
        if r["mean_value"] != 0.0 or r["min_value"] != 0.0:
            errors.append(f"_prepare_records zero: real zero was not preserved: {r}")

    # 3. Record preparation rejects NaN/Inf values
    parsed_with_nan = {
        "savi": {
            "captured_date": "2026-06-20",
            "index_code": "savi",
            "mean_value": float("nan"),
            "min_value": 0.1,
            "max_value": 0.8,
            "std_value": 0.05,
            "p10_value": 0.2,
            "p90_value": 0.7,
            "valid_pixels_pct": 100.0,
            "cloud_cover_pct": None,
            "satellite": "Sentinel-2",
        }
    }
    try:
        _prepare_records(parsed_with_nan)
        errors.append("_prepare_records did not reject NaN mean_value")
    except SystemExit:
        pass  # expected

    parsed_with_inf = {
        "savi": {
            "captured_date": "2026-06-20",
            "index_code": "savi",
            "mean_value": 0.3,
            "min_value": 0.1,
            "max_value": float("inf"),
            "std_value": 0.05,
            "p10_value": 0.2,
            "p90_value": 0.7,
            "valid_pixels_pct": 100.0,
            "cloud_cover_pct": None,
            "satellite": "Sentinel-2",
        }
    }
    try:
        _prepare_records(parsed_with_inf)
        errors.append("_prepare_records did not reject Inf max_value")
    except SystemExit:
        pass  # expected

    # 4. Write mode requires explicit --write (via parse_args)
    args_no_write = parse_args(["--field-id", "4", "--self-test"])
    if args_no_write.write:
        errors.append("parse_args: --self-test should not set write=True")
    args_write = parse_args(["--field-id", "4", "--write"])
    if not args_write.write:
        errors.append("parse_args: --write flag not recognized")

    # 5. Upsert key is exactly (field_id, captured_date, index_code)
    # Verified: SatelliteIndexRecord has UniqueConstraint on these three columns.
    unique_constraint = SatelliteIndexRecord.__table_args__[0]
    cols = tuple(c.name if hasattr(c, 'name') else str(c) for c in unique_constraint.columns)
    if cols != ("field_id", "captured_date", "index_code"):
        errors.append(
            f"Upsert key mismatch: expected (field_id, captured_date, index_code), "
            f"got {cols}"
        )

    # 6. Parser round-trip
    mock = _mock_multi_index_response()
    parsed = parse_multi_index_stats_response(mock, codes)
    for code in codes:
        if code not in parsed:
            errors.append(f"Parser did not return key '{code}'")

    # 7. Record preparation for normal values
    recs_normal = _prepare_records(parsed)
    if len(recs_normal) != len(codes):
        errors.append(f"_prepare_records normal: expected {len(codes)} records, got {len(recs_normal)}")

    # 8. Verify that --verify-only is set correctly
    args_verify = parse_args(["--field-id", "4", "--indices", "savi", "--verify-only"])
    if not args_verify.verify_only:
        errors.append("parse_args: --verify-only not recognized")
    if args_verify.write:
        errors.append("parse_args: --verify-only should not set write=True")

    if errors:
        print("SELF TEST FAILED")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print("SELF TEST PASSED")
    sys.exit(0)


# ── Main ───────────────────────────────────────────────────────────────────


def main() -> None:
    args = parse_args()

    if args.self_test:
        run_self_test()
        return  # unreachable

    if args.verify_only:
        if args.field_id is None:
            print("ERROR: --field-id is required with --verify-only", file=sys.stderr)
            sys.exit(1)
        index_codes = _normalize_and_validate_indices(args.indices)
        _check_table_exists()
        verify_only(args.field_id, index_codes)
        return

    if args.field_id is None:
        print("ERROR: --field-id is required (use --self-test for offline validation)", file=sys.stderr)
        sys.exit(1)

    if args.days < 1:
        print("ERROR: --days must be >= 1", file=sys.stderr)
        sys.exit(1)

    index_codes = _normalize_and_validate_indices(args.indices)

    client_id, client_secret = validate_credentials(
        settings.sentinel_hub_client_id, settings.sentinel_hub_client_secret
    )

    # Preflight: table exists
    _check_table_exists()

    # Preflight: field exists with geometry
    field_id, field_name, geometry_wkt = _check_field(args.field_id)

    # Build evalscript and geometry
    evalscript = build_multi_index_evalscript(index_codes)
    geom = wkt.loads(geometry_wkt)
    geojson_geom = mapping(geom)

    # Dates
    date_to = date.today()
    date_from = date_to - timedelta(days=args.days)

    # Credentials
    # Build payload
    payload = _build_statistical_payload(
        geometry_geojson=geojson_geom,
        evalscript=evalscript,
        index_codes=index_codes,
        date_from=date_from,
        date_to=date_to,
    )

    # Fetch from Sentinel Hub
    try:
        token = _get_access_token(client_id, client_secret)
    except Exception as e:
        print(f"ERROR: Failed to obtain Sentinel Hub token: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        resp = httpx.post(
            STATISTICAL_API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=120.0,
        )
        if not resp.is_success:
            print(f"ERROR: Sentinel Hub returned HTTP {resp.status_code}: {resp.text[:500]}", file=sys.stderr)
            sys.exit(1)
        response_data = resp.json()
    except httpx.TimeoutException:
        print("ERROR: Sentinel Hub request timed out", file=sys.stderr)
        sys.exit(1)
    except httpx.RequestError as e:
        print(f"ERROR: Sentinel Hub request failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Parse
    parsed = parse_multi_index_stats_response(response_data, index_codes)

    if not parsed:
        print("WARNING: No valid intervals returned for any requested index.")
        print("RESULT: NO DATA — nothing to write.")
        sys.exit(0)

    # Preflight: parsed results are non-empty and finite
    records = _prepare_records(parsed)

    # Quality validation
    for code in index_codes:
        if code in parsed:
            d = parsed[code]
            valid, reason = validate_index_quality(
                code, d.get("mean_value"),
                cloud_cover_pct=d.get("cloud_cover_pct"),
                min_value=d.get("min_value"),
                max_value=d.get("max_value"),
                valid_pixels_pct=d.get("valid_pixels_pct"),
            )
            if not valid:
                print(f"WARNING: {code} quality rejected ({reason}) — row will still be written with these values")

    # Print field info
    print(f"  field_id:            {field_id}")
    print(f"  field_name:          {field_name}")
    print(f"  date_from:           {date_from.isoformat()}")
    print(f"  date_to:             {date_to.isoformat()}")
    print(f"  indices:             {', '.join(index_codes)}")
    print()

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
    print()

    if args.write:
        print("  WRITE MODE: Writing to satellite_index_records ...")
        counts = do_write(field_id, records)
        print(f"  inserted={counts['inserted']} updated={counts['updated']} "
              f"skipped={counts['skipped']} written_total={counts['inserted'] + counts['updated']}")
    else:
        print("  PREVIEW MODE (no write, use --write to commit):")
        print_preview(records, field_id, index_codes)

    print()
    print(f"  RESULT: {'WRITTEN' if args.write else 'PREVIEW'} — {len(records)} rows "
          f"{'written' if args.write else 'planned'} for field {field_id}")


if __name__ == "__main__":
    main()
