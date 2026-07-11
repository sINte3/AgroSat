#!/usr/bin/env python3
"""
Hardened CLI entrypoint for satellite data collection.

Safe, production-grade CLI for collecting Sentinel Hub satellite indices.
Runs as a standalone process -- never imported or started by FastAPI/web workers.

Idempotency and locking:
  - File-based lock prevents concurrent runs.
  - Idempotency helpers check existing records before writing.
  - --skip-existing mode skips fields/indexes with existing data.
  - --force flag overrides skip logic.
  - --write flag is required for actual DB writes.

Dry-run mode: no Sentinel Hub calls, no DB writes (default).
Apply mode (--apply): Sentinel Hub calls + parse, no DB writes.
Write mode (--write): Sentinel Hub calls + parse + idempotent DB writes.

NDVI routes to legacy ndvi_records path only. New indices routed to
satellite_index_records only. ndvi for satellite_index_records is rejected.

Usage:
  python backend/scripts/collect_satellite_indices.py --help
  python backend/scripts/collect_satellite_indices.py --dry-run --field-id 4 --index savi --date-from 2026-07-01 --date-to 2026-07-01 --max-fields 1
  python backend/scripts/collect_satellite_indices.py --dry-run --field-id 4 --indices savi,ndmi --date-from 2026-07-01 --date-to 2026-07-01 --max-fields 1
  python backend/scripts/collect_satellite_indices.py --dry-run --field-id 4 --index ndvi --date-from 2026-07-01 --date-to 2026-07-01 --max-fields 1
  python backend/scripts/collect_satellite_indices.py --apply --field-id 4 --index savi --date-from 2026-07-01 --date-to 2026-07-01 --max-fields 1
  python backend/scripts/collect_satellite_indices.py --write --field-id 4 --index savi --date-from 2026-07-01 --date-to 2026-07-01 --max-fields 1
"""

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from database import SessionLocal
from services.collector_idempotency import (
    IdempotencyMode,
    check_exists_by_key,
    count_existing_ndvi,
    count_existing_satellite_index,
    has_db_level_uniqueness,
    plan_actions,
)
from services.collector_locking import (
    DEFAULT_LOCK_FILE,
    acquire_lock,
    release_lock,
)
from services.satellite_indices import (
    SUPPORTED_INDEX_CODES as MULTI_INDEX_CODES,
    build_multi_index_evalscript,
    extract_intervals_metadata,
    normalize_index_code,
    parse_multi_index_stats_response_flat,
    validate_index_quality,
)
from services.satellite_collection import (
    MultiIndexSentinelHubService,
    MockMultiIndexSatelliteService,
    get_multi_index_satellite_service,
    get_mock_multi_index_satellite_service,
    filter_by_quality,
)
from services.satellite_safety import (
    SatelliteConfigurationError,
    require_real_provenance,
    require_real_service,
    validate_credentials,
)
# -- Constants --

LEGACY_NDVI_CODE = "ndvi"

# Multi-index codes that go into satellite_index_records
# (savi, evi, ndmi, ndre)
SATELLITE_INDEX_CODES = {c for c in MULTI_INDEX_CODES}

# Date format for display
DATE_FMT = "%Y-%m-%d"

# Sentinel Hub endpoints (used only when --no-sentinel is not set and not dry-run)
STATISTICAL_API_URL = "https://services.sentinel-hub.com/api/v1/statistics"
TOKEN_URL = "https://services.sentinel-hub.com/auth/realms/main/protocol/openid-connect/token"

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# -- Index validation helpers --


def _validate_index_code(raw: str) -> str:
    """Validate and normalize a single index code. Exit on invalid."""
    code = normalize_index_code(raw)
    if code == LEGACY_NDVI_CODE:
        return code
    if code in SATELLITE_INDEX_CODES:
        return code
    print(f"ERROR: Unsupported index code '{raw}'", file=sys.stderr)
    print(f"  Supported: ndvi, {', '.join(sorted(SATELLITE_INDEX_CODES))}", file=sys.stderr)
    sys.exit(2)


def _parse_indexes(index_arg: Optional[str], indices_arg: Optional[str]) -> list[str]:
    """Parse --index and --indices into a normalized list. Exit on invalid."""
    raw_codes: list[str] = []
    if index_arg:
        raw_codes.append(index_arg)
    if indices_arg:
        raw_codes.extend(indices_arg.split(","))
    if not raw_codes:
        print("ERROR: At least one of --index or --indices is required", file=sys.stderr)
        sys.exit(2)
    codes = []
    has_ndvi = False
    has_multi = False
    for raw in raw_codes:
        raw = raw.strip()
        if not raw:
            continue
        code = _validate_index_code(raw)
        codes.append(code)
        if code == LEGACY_NDVI_CODE:
            has_ndvi = True
        else:
            has_multi = True
    if has_ndvi and has_multi:
        print(
            "ERROR: ndvi and satellite indices (savi/evi/ndmi/ndre) cannot be mixed "
            "in a single run. NDVI uses the legacy ndvi_records path; satellite indices "
            "use satellite_index_records.",
            file=sys.stderr,
        )
        sys.exit(2)
    if not codes:
        print("ERROR: No valid index codes after parsing", file=sys.stderr)
        sys.exit(2)
    return codes


def _validate_date(date_str: str, label: str) -> date:
    """Parse and validate a date string YYYY-MM-DD. Exit on invalid."""
    try:
        return datetime.strptime(date_str, DATE_FMT).date()
    except ValueError:
        print(f"ERROR: Invalid {label} '{date_str}' -- expected YYYY-MM-DD", file=sys.stderr)
        sys.exit(2)


# -- Field query (read-only SELECT) --


def _query_fields(
    field_id: Optional[int],
    enterprise_id: Optional[int],
    max_fields: int,
) -> list[dict]:
    """Query fields from DB. Read-only SELECT. Returns list of field dicts."""
    db = SessionLocal()
    try:
        from sqlalchemy import text as sa_text

        conditions: list[str] = ["is_active = true"]
        params: dict = {}

        if field_id is not None:
            conditions.append("id = :fid")
            params["fid"] = field_id
        if enterprise_id is not None:
            conditions.append("enterprise_id = :eid")
            params["eid"] = enterprise_id

        where = " AND ".join(conditions) if conditions else "TRUE"
        limit_clause = ""
        if max_fields is not None and max_fields > 0:
            limit_clause = f" LIMIT :lim"
            params["lim"] = max_fields

        rows = db.execute(
            sa_text(
                f"SELECT id, name, enterprise_id, area_ha, "
                f"  ST_AsText(geometry) AS geometry_wkt "
                f"FROM fields WHERE {where} ORDER BY id ASC{limit_clause}"
            ),
            params,
        ).mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


def _query_existing_ndvi(field_id: int, date_from: date, date_to: date) -> list[dict]:
    """Query existing NDVI records for a field and date range. Read-only."""
    db = SessionLocal()
    try:
        from sqlalchemy import text as sa_text

        rows = db.execute(
            sa_text(
                "SELECT id, captured_date, mean_ndvi "
                "FROM ndvi_records "
                "WHERE field_id = :fid AND captured_date >= :dfrom AND captured_date <= :dto "
                "ORDER BY captured_date"
            ),
            {"fid": field_id, "dfrom": date_from, "dto": date_to},
        ).mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


def _query_existing_satellite_index(
    field_id: int, index_code: str, date_from: date, date_to: date,
) -> list[dict]:
    """Query existing satellite_index_records for a field, index, and date range."""
    db = SessionLocal()
    try:
        from sqlalchemy import text as sa_text

        rows = db.execute(
            sa_text(
                "SELECT id, captured_date, index_code, mean_value "
                "FROM satellite_index_records "
                "WHERE field_id = :fid AND index_code = :ic "
                "  AND captured_date >= :dfrom AND captured_date <= :dto "
                "ORDER BY captured_date"
            ),
            {"fid": field_id, "ic": index_code, "dfrom": date_from, "dto": date_to},
        ).mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


# -- CLI parsing --


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Satellite data collection CLI -- hardened entrypoint.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  Dry-run single field:\n"
            "    %(prog)s --dry-run --field-id 4 --index savi --date-from 2026-07-01 --date-to 2026-07-01 --max-fields 1\n\n"
            "  Dry-run with NDVI (legacy path):\n"
            "    %(prog)s --dry-run --field-id 4 --index ndvi --date-from 2026-07-01 --date-to 2026-07-01 --max-fields 1\n\n"
            "  Dry-run multiple indices:\n"
            "    %(prog)s --dry-run --field-id 4 --indices savi,ndmi,ndre --date-from 2026-07-01 --date-to 2026-07-01 --max-fields 1\n\n"
            "  Real-run (requires Sentinel Hub credentials, explicit --force):\n"
            "    %(prog)s --field-id 4 --index savi --date-from 2026-07-01 --date-to 2026-07-01 --max-fields 1 --force\n"
        ),
    )

    # Mode flags
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Safe mode: no Sentinel Hub calls, no DB writes (default).",
    )
    parser.add_argument(
        "--apply", action="store_true", default=False,
        help="Apply mode: enable Sentinel Hub calls for real collection (no DB writes).",
    )
    parser.add_argument(
        "--write", action="store_true", default=False,
        help="Write mode: enable both Sentinel Hub calls AND idempotent DB writes.",
    )
    parser.add_argument(
        "--mock-sentinel", action="store_true", default=False,
        help="Use mock satellite service (no network calls). For validation only.",
    )

    # Scope
    parser.add_argument(
        "--field-id", type=int, default=None,
        help="Limit collection to a single field ID. Required for apply/write mode.",
    )
    parser.add_argument(
        "--enterprise-id", type=int, default=None,
        help="Limit collection to fields belonging to an enterprise.",
    )
    parser.add_argument(
        "--max-fields", type=int, default=None,
        help="Maximum number of fields to process.",
    )

    # Index selection
    parser.add_argument(
        "--index", type=str, default=None,
        help="Single index code: savi, evi, ndmi, ndre (no NDVI for satellite_index_records).",
    )
    parser.add_argument(
        "--indices", type=str, default=None,
        help="Comma-separated index codes. Alternative to --index.",
    )

    # Date range
    parser.add_argument(
        "--date-from", type=str, default=None,
        help="Start date YYYY-MM-DD. Default: 30 days ago.",
    )
    parser.add_argument(
        "--date-to", type=str, default=None,
        help="End date YYYY-MM-DD. Default: today.",
    )
    parser.add_argument(
        "--max-date-range", type=int, default=120,
        help="Maximum days in date range for safety. Default: 120.",
    )

    # Sentinel aggregation interval
    parser.add_argument(
        "--aggregation-interval", type=str, default="P1D",
        help="Sentinel Hub aggregation interval (ISO 8601 duration). "
             "P1D = daily, P5D = 5-day aggregate. Default: P1D.",
    )

    # Safety / idempotency
    parser.add_argument(
        "--force", action="store_true", default=False,
        help="Override skip-existing and force re-collection. Also serves as "
             "legacy alias for --apply --write combined.",
    )
    parser.add_argument(
        "--skip-existing", action="store_true", default=False,
        help="Skip fields/index_dates that already have records in DB.",
    )
    parser.add_argument(
        "--no-sentinel", action="store_true", default=False,
        help="Prevent any Sentinel Hub API calls even in apply/write mode.",
    )
    parser.add_argument(
        "--overwrite", action="store_true", default=False,
        help="Overwrite existing records on conflict (requires --write). "
             "Not enabled by default -- idempotent skip is default.",
    )
    parser.add_argument(
        "--output-log", type=str, default=None,
        help="Optional path to write JSON run summary outside the repo.",
    )

    # Diagnostics
    parser.add_argument(
        "--debug-raw-intervals", action="store_true", default=False,
        help="Print sanitized Sentinel Hub interval metadata for each field. "
             "Shows interval boundaries, derived captured_date, and ambiguity status. "
             "No secrets (tokens, geometry, credentials) are printed.",
    )

    # Locking flags
    parser.add_argument(
        "--lock-file", type=str, default=None,
        help="Path to lock file. Default: %%TEMP%%\\agrosat_satellite_collector.lock",
    )
    parser.add_argument(
        "--force-lock", action="store_true", default=False,
        help="Break stale lock and acquire fresh lock.",
    )
    parser.add_argument(
        "--break-stale-lock", action="store_true", default=False,
        help="Alias for --force-lock.",
    )

    return parser.parse_args(argv)


# -- Dry-run planner --


def _plan_dry_run_for_field(
    field: dict,
    codes: list[str],
    date_from: date,
    date_to: date,
    skip_existing: bool,
    is_ndvi: bool,
) -> dict:
    """Plan work for one field in dry-run mode. No Sentinel Hub calls, no DB writes."""
    fid = field["id"]
    plan: dict = {
        "field_id": fid,
        "field_name": field.get("name", f"field_{fid}"),
        "enterprise_id": field.get("enterprise_id"),
        "area_ha": field.get("area_ha"),
        "has_geometry": bool(field.get("geometry_wkt")),
        "planned_operations": [],
        "skipped_count": 0,
        "planned_write_count": 0,
    }

    if not field.get("geometry_wkt"):
        plan["note"] = "No geometry -- cannot collect"
        return plan

    for code in codes:
        if is_ndvi:
            existing = _query_existing_ndvi(fid, date_from, date_to)
        else:
            existing = _query_existing_satellite_index(fid, code, date_from, date_to)

        existing_dates = {r["captured_date"].isoformat() if hasattr(r["captured_date"], "isoformat") else str(r["captured_date"]) for r in existing}

        if skip_existing:
            # In dry-run mode with --skip-existing, we plan only for missing dates
            # Since we do not call Sentinel Hub, we cannot know exact captured dates.
            # Show what would be attempted.
            plan["planned_operations"].append({
                "index_code": code,
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "existing_records": len(existing),
                "action": "skip_existing_if_present",
            })
            if len(existing) > 0:
                plan["skipped_count"] += 1
            plan["planned_write_count"] += 1
        else:
            plan["planned_operations"].append({
                "index_code": code,
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "existing_records": len(existing),
                "action": "collect_and_write",
            })
            plan["planned_write_count"] += 1

    return plan


def run_dry_run(args: argparse.Namespace) -> None:
    """Execute dry-run mode: plan, read DB, report. No Sentinel Hub, no writes."""
    started_at = datetime.now(timezone.utc).replace(microsecond=0)

    codes = _parse_indexes(args.index, args.indices)
    is_ndvi = codes == [LEGACY_NDVI_CODE] or (len(codes) == 1 and codes[0] == LEGACY_NDVI_CODE)

    date_from = args.date_from
    if date_from is not None:
        date_from = _validate_date(date_from, "--date-from")
    else:
        date_from = date.today() - timedelta(days=30)

    date_to = args.date_to
    if date_to is not None:
        date_to = _validate_date(date_to, "--date-to")
    else:
        date_to = date.today()

    if date_from > date_to:
        print(f"ERROR: --date-from ({date_from}) is after --date-to ({date_to})", file=sys.stderr)
        sys.exit(2)

    # Determine scope guard
    if args.field_id is None and args.enterprise_id is None and (args.max_fields is None or args.max_fields <= 0):
        max_fields = 5
        print("NOTE: No --field-id or --enterprise-id set. Defaulting --max-fields=5 to prevent all-fields run.")
    else:
        max_fields = args.max_fields

    # Lock check (dry-run mode: just check lock status)
    lock_path = None
    if not args.dry_run:
        # Lock check only attempted in non-dry-run; but since we're in dry-run,
        # we report lock status
        pass

    # Acquire/dry-run lock
    force_lock = args.force_lock or args.break_stale_lock
    lock_path = acquire_lock(
        lock_file=args.lock_file,
        force=force_lock,
        dry_run=True,
    )

    # Fetch fields
    fields = _query_fields(args.field_id, args.enterprise_id, max_fields)
    total_fields = len(fields)

    if not fields:
        print("ERROR: No active fields found matching criteria", file=sys.stderr)
        sys.exit(1)

    # Idempotency mode determination
    idempotency_mode = IdempotencyMode.PLANNED_ONLY
    if args.skip_existing:
        idempotency_mode = IdempotencyMode.SKIP_EXISTING
    elif args.force:
        idempotency_mode = IdempotencyMode.FORCE

    # Existing records count per index across all fields
    existing_count_by_index: dict[str, int] = {}
    for code in codes:
        total_existing = 0
        for field in fields:
            fid = field["id"]
            if is_ndvi:
                total_existing += count_existing_ndvi(fid, date_from, date_to)
            else:
                total_existing += count_existing_satellite_index(fid, code, date_from, date_to)
        existing_count_by_index[code] = total_existing

    # Plan per field
    plans = []
    total_planned_writes = 0
    total_skipped = 0
    total_no_geom = 0

    for field in fields:
        plan = _plan_dry_run_for_field(field, codes, date_from, date_to, args.skip_existing, is_ndvi)
        plans.append(plan)
        total_planned_writes += plan["planned_write_count"]
        total_skipped += plan["skipped_count"]
        if not plan["has_geometry"]:
            total_no_geom += 1

    finished_at = datetime.now(timezone.utc).replace(microsecond=0)
    duration = (finished_at - started_at).total_seconds()

    # -- Print summary --
    target_table = "ndvi_records (legacy NDVI)" if is_ndvi else "satellite_index_records"
    mode_str = "DRY-RUN (no Sentinel Hub calls, no DB writes)"

    print()
    print("=" * 60)
    print(f"  SATELLITE COLLECTION REPORT")
    print("=" * 60)
    print(f"  Run mode:             {mode_str}")
    print(f"  Target table:         {target_table}")
    print(f"  Date range:           {date_from.isoformat()} to {date_to.isoformat()}")
    print(f"  Indices:              {', '.join(codes)}")
    print(f"  Fields selected:      {total_fields}")
    print(f"  Fields with geometry: {total_fields - total_no_geom}")
    print(f"  Fields no geometry:   {total_no_geom}")
    print(f"  Skip existing:        {args.skip_existing}")
    print(f"  Force:                {args.force}")
    print(f"  Planned writes:       {total_planned_writes}")
    print(f"  Skipped (existing):   {total_skipped}")
    print()
    # Idempotency section
    print(f"  Idempotency mode:     {idempotency_mode}")
    for code in codes:
        ec = existing_count_by_index.get(code, 0)
        uni = has_db_level_uniqueness(code)
        uni_str = "DB UNIQUE" if uni else "SCRIPT-LEVEL (no DB unique constraint — migration deferred)"
        print(f"    {code}: {ec} existing records — {uni_str}")
    print()
    # Lock status
    if args.lock_file:
        print(f"  Lock file:            {args.lock_file}")
    else:
        print(f"  Lock file:            {DEFAULT_LOCK_FILE} (default)")
    print(f"  Lock acquired:        dry-run check only")
    print()

    print("  Per-field plan:")
    print()

    for p in plans:
        print(f"  Field {p['field_id']} ({p['field_name']}):")
        if not p["has_geometry"]:
            print(f"    SKIP: no geometry")
            continue
        for op in p["planned_operations"]:
            print(f"    [{op['index_code']}] {op['date_from']} -> {op['date_to']} "
                  f"action={op['action']} existing={op['existing_records']}")
        print()

    # Check for credentials
    has_creds = bool(settings.sentinel_hub_client_id and settings.sentinel_hub_client_secret)

    print(f"  Sentinel Hub credentials: {'present' if has_creds else 'NOT CONFIGURED'}")
    print(f"  Sentinel Hub calls:       NONE (dry-run)")
    print(f"  DB writes:                NONE (dry-run)")
    print()
    print(f"  Started at:  {started_at.isoformat()}")
    print(f"  Finished at: {finished_at.isoformat()}")
    print(f"  Duration:    {duration:.2f}s")
    print()
    print(f"  DRY-RUN COMPLETE (no data was written)")
    print("=" * 60)

    # JSON output if requested
    if args.output_log:
        json_output(args.output_log, {
            "run_mode": "dry-run",
            "target_table": target_table,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "indices": codes,
            "fields_selected": total_fields,
            "fields_with_geometry": total_fields - total_no_geom,
            "planned_writes": total_planned_writes,
            "skipped_existing": total_skipped,
            "idempotency_mode": idempotency_mode,
            "existing_count_by_index": existing_count_by_index,
            "lock_file": args.lock_file or DEFAULT_LOCK_FILE,
            "lock_acquired": False,
            "sentinel_hub_calls": 0,
            "db_writes": 0,
            "success_count": 0,
            "failure_count": 0,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": duration,
            "exit_code": 0,
        })

    sys.exit(0)


# -- Real-run planner --


def _get_satellite_service(
    mock_sentinel: bool,
    no_sentinel: bool,
) -> Any:
    """Get satellite service instance based on flags.

    Args:
        mock_sentinel: Use mock service (no network calls).
        no_sentinel: Prevent real Sentinel Hub calls.

    Returns:
        A service instance with collect_indices() interface.
    """
    if no_sentinel:
        raise SatelliteConfigurationError("--no-sentinel cannot run collection")
    if mock_sentinel:
        logger.info("Using mock satellite service (no network calls)")
        return get_mock_multi_index_satellite_service()

    logger.info("Using real Sentinel Hub satellite service")
    return get_multi_index_satellite_service()


def _insert_satellite_index_record(
    field_id: int,
    captured_date: date,
    index_code: str,
    mean_value: float | None,
    satellite: str,
    min_value: float | None = None,
    max_value: float | None = None,
    std_value: float | None = None,
    p10_value: float | None = None,
    p90_value: float | None = None,
    valid_pixels_pct: float | None = None,
    cloud_cover_pct: float | None = None,
) -> bool:
    """
    Insert a single satellite_index_record. Returns True on success, False on skip/error.

    Uses idempotent insert: checks (field_id, captured_date, index_code) uniqueness
    before inserting. Does NOT overwrite existing records.
    """
    require_real_provenance(satellite)
    if check_exists_by_key(field_id, captured_date, index_code):
        logger.info("  SKIP (exists): field=%d date=%s code=%s", field_id, captured_date, index_code)
        return False

    db = SessionLocal()
    try:
        from sqlalchemy import text as sa_text

        db.execute(
            sa_text(
                "INSERT INTO satellite_index_records "
                "(field_id, captured_date, index_code, mean_value, min_value, max_value, "
                " std_value, p10_value, p90_value, valid_pixels_pct, cloud_cover_pct, satellite) "
                "VALUES (:fid, :cd, :ic, :mv, :minv, :maxv, :stdv, :p10, :p90, :vpp, :ccp, :sat)"
            ),
            {
                "fid": field_id,
                "cd": captured_date,
                "ic": index_code,
                "mv": mean_value,
                "minv": min_value,
                "maxv": max_value,
                "stdv": std_value,
                "p10": p10_value,
                "p90": p90_value,
                "vpp": valid_pixels_pct,
                "ccp": cloud_cover_pct,
                "sat": satellite,
            },
        )
        db.commit()
        logger.info("  INSERTED: field=%d date=%s code=%s mean=%.4f", field_id, captured_date, index_code, mean_value or 0)
        return True
    except Exception:
        db.rollback()
        logger.exception("  INSERT FAILED: field=%d date=%s code=%s", field_id, captured_date, index_code)
        return False
    finally:
        db.close()


def run_real(args: argparse.Namespace) -> None:
    """
    Execute real or apply collection mode for satellite indices.

    Modes:
      --apply:          Enable Sentinel Hub calls (or mock), parse results, no DB writes.
      --write:          Enable both Sentinel Hub calls AND idempotent DB writes.
      --mock-sentinel:  Use mock satellite service instead of real Sentinel Hub.
      --no-sentinel:    Reject collection; no external call means no synthetic substitute.
    """
    # Multi-field write-mode guard
    if args.field_id is None and args.enterprise_id is None and (args.max_fields is None or args.max_fields <= 0):
        print(
            "ERROR: Real collection requires --field-id, --enterprise-id, or "
            "--max-fields to prevent accidental all-fields run.",
            file=sys.stderr,
        )
        sys.exit(2)

    started_at = datetime.now(timezone.utc).replace(microsecond=0)
    codes = _parse_indexes(args.index, args.indices)
    is_ndvi = codes == [LEGACY_NDVI_CODE] or (len(codes) == 1 and codes[0] == LEGACY_NDVI_CODE)
    is_apply_mode = bool(args.apply)
    is_write_mode = bool(args.write) or bool(args.force)
    do_db_writes = is_write_mode
    use_mock = bool(args.mock_sentinel)

    # Determine execution mode for display
    if use_mock and not do_db_writes:
        mode_label = "MOCK APPLY (no DB writes)"
    elif do_db_writes:
        mode_label = "REAL + WRITE"
    else:
        mode_label = "APPLY (Sentinel Hub calls, no DB writes)"

    # Reject NDVI for satellite_index_records path
    if not is_ndvi and LEGACY_NDVI_CODE in codes:
        print("ERROR: ndvi is not allowed in satellite_index_records path", file=sys.stderr)
        sys.exit(2)

    # Guard: require --field-id for apply/write modes (prevent broad scope)
    if args.field_id is None and not is_ndvi:
        print(
            "ERROR: --field-id is required for satellite index collection. "
            "Broad enterprise/all-fields collection for satellite indices is not yet enabled.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Date range safety guard
    date_from = _validate_date(args.date_from or (date.today() - timedelta(days=30)).isoformat(), "--date-from")
    date_to = _validate_date(args.date_to or date.today().isoformat(), "--date-to")
    date_range_days = (date_to - date_from).days
    max_range = args.max_date_range or 120
    if date_range_days > max_range:
        print(
            f"ERROR: Date range {date_range_days} days exceeds max {max_range} days. "
            f"Use --max-date-range to override (not recommended for production).",
            file=sys.stderr,
        )
        sys.exit(2)

    if date_from > date_to:
        print(f"ERROR: --date-from ({date_from}) is after --date-to ({date_to})", file=sys.stderr)
        sys.exit(2)

    # Defense in depth if the pure validator was bypassed.
    if do_db_writes and use_mock:
        raise SatelliteConfigurationError("Synthetic satellite service cannot write")

    # Acquire lock for real mode
    force_lock = args.force_lock or args.break_stale_lock
    lock_path = acquire_lock(
        lock_file=args.lock_file,
        force=force_lock,
        dry_run=False,
    )

    # Get satellite service
    service = _get_satellite_service(use_mock, args.no_sentinel)
    if do_db_writes:
        require_real_service(service)

    # Fetch fields
    max_fields = args.max_fields
    if args.field_id is not None:
        max_fields = 1  # single field mode
    fields = _query_fields(args.field_id, args.enterprise_id, max_fields)
    total_fields = len(fields)

    if not fields:
        print("ERROR: No active fields found matching criteria", file=sys.stderr)
        release_lock(lock_path)
        sys.exit(1)

    # ── Idempotency setup ──
    if args.force:
        idempotency_mode = IdempotencyMode.FORCE
    elif args.skip_existing or do_db_writes:
        idempotency_mode = IdempotencyMode.SKIP_EXISTING
    else:
        idempotency_mode = IdempotencyMode.PLANNED_ONLY
    do_skip_existing = idempotency_mode == IdempotencyMode.SKIP_EXISTING and not args.force
    do_overwrite = bool(args.overwrite) and do_db_writes

    # ── NDVI legacy path ──
    if is_ndvi:
        print()
        print("=" * 60)
        print("  LEGACY NDVI PATH (routed through existing dry-run code)")
        print("=" * 60)
        print(f"  Mode:                 {mode_label}")
        print(f"  Indices:              {', '.join(codes)}")
        print(f"  Fields selected:      {total_fields}")
        print(f"  Lock:                 {lock_path or args.lock_file or DEFAULT_LOCK_FILE}")
        print()
        print("  NOTE: write mode for NDVI is not implemented in this script path.")
        print("  NDVI collection uses the legacy ndvi_collector pipeline.")
        print()
        # Dry-run-style report for NDVI in real mode
        for field in fields:
            fid = field["id"]
            fname = field.get("name", f"field_{fid}")
            geom_wkt = field.get("geometry_wkt")
            print(f"  Field {fid} ({fname}):")
            if not geom_wkt:
                print(f"    SKIP: no geometry")
                continue
            for code in codes:
                existing = _query_existing_ndvi(fid, date_from, date_to)
                print(f"    [{code}] existing NDVI records: {len(existing)}")
        release_lock(lock_path)
        sys.exit(0)

    # ── Collection loop ──
    target_table = "satellite_index_records"
    per_field_results: list[dict] = []
    total_sentinel_calls = 0
    total_candidates = 0
    total_would_insert = 0
    total_would_skip_existing = 0
    total_quality_passed = 0
    total_quality_blocked = 0
    total_db_inserted = 0
    total_db_skipped = 0
    total_errors = 0

    print()
    print("=" * 60)
    print("  SATELLITE INDEX COLLECTION")
    print("=" * 60)
    print(f"  Mode:                 {mode_label}")
    print(f"  Target table:         {target_table}")
    print(f"  Indices:              {', '.join(codes)}")
    print(f"  Fields selected:      {total_fields}")
    print(f"  Date range:           {date_from} to {date_to} ({date_range_days} days)")
    print(f"  Aggregation interval: {args.aggregation_interval}")
    print(f"  Idempotency mode:     {idempotency_mode}")
    print(f"  Overwrite:            {do_overwrite}")
    print(f"  Lock:                 {lock_path or args.lock_file or DEFAULT_LOCK_FILE}")
    print()

    for field in fields:
        fid = field["id"]
        fname = field.get("name", f"field_{fid}")
        geom_wkt = field.get("geometry_wkt")

        print(f"  Field {fid} ({fname}):")

        if not geom_wkt:
            print(f"    SKIP: no geometry")
            per_field_results.append({
                "field_id": fid,
                "field_name": fname,
                "status": "skipped_no_geometry",
                "error": None,
            })
            continue

        field_errors: list[str] = []
        field_candidates = 0
        field_would_insert = 0
        field_would_skip = 0
        field_would_skip_multi_day = 0
        field_quality_passed = 0
        field_quality_blocked = 0
        field_db_inserted = 0
        field_db_skipped = 0

        # Plan actions per index code
        planned = plan_actions(fid, codes, date_from, date_to, idempotency_mode)
        for p in planned:
            ic = p["index_code"]
            action = p["action"]
            existing_count = p["existing_count"]
            print(f"    [{ic}] planned action: {action} (existing={existing_count})")

        # Determine which codes to actually collect
        codes_to_collect: list[str] = []
        for p in planned:
            ic = p["index_code"]
            if p["action"] == "collect_then_check":
                # Range-wide existing records found, but we collect all candidates
                # and check exact-date (field_id, captured_date, index_code) per candidate.
                # The existing_count is informational only, not a pre-skip decision.
                codes_to_collect.append(ic)
                print(f"      [{ic}] {p['existing_count']} existing range-wide (will check exact dates per candidate)")
            elif p["action"] == "skip_existing" and do_skip_existing and not do_overwrite:
                total_would_skip_existing += 1
                field_would_skip += 1
                print(f"      SKIP: {ic} already has {p['existing_count']} records")
            elif is_ndvi:
                print(f"      NDVI path: skipping satellite service call (legacy path)")
                total_would_skip_existing += 1
                field_would_skip += 1
            else:
                codes_to_collect.append(ic)

        if not codes_to_collect:
            print(f"      No codes to collect (all codes excluded by legacy NDVI path or full-pre-skip)")
            per_field_results.append({
                "field_id": fid,
                "field_name": fname,
                "status": "all_skipped",
                "errors": field_errors,
                "candidates": field_candidates,
                "would_insert": field_would_insert,
                "would_skip_existing": field_would_skip,
                "quality_passed": field_quality_passed,
                "quality_blocked": field_quality_blocked,
                "db_inserted": field_db_inserted,
                "db_skipped": field_db_skipped,
                "candidate_records": [],
            })
            continue

        # ── Collect indices from satellite service ──
        try:
            print(f"      Collecting indices: {', '.join(codes_to_collect)} ...")
            flat_records = service.collect_indices_flat(
                geometry_wkt=geom_wkt,
                index_codes=codes_to_collect,
                date_from=date_from,
                date_to=date_to,
                aggregation_interval=args.aggregation_interval,
            )
            total_sentinel_calls += 1
            print(f"      Sentinel Hub returned {len(flat_records)} flat records (per-interval, per-index)")
        except Exception as e:
            err_msg = f"Satellite collection failed: {e}"
            print(f"      ERROR: {err_msg}")
            field_errors.append(str(e))
            total_errors += 1
            per_field_results.append({
                "field_id": fid,
                "field_name": fname,
                "status": "collection_error",
                "errors": field_errors,
                "candidates": field_candidates,
                "would_insert": field_would_insert,
                "would_skip_existing": field_would_skip,
                "quality_passed": field_quality_passed,
                "quality_blocked": field_quality_blocked,
                "db_inserted": field_db_inserted,
                "db_skipped": field_db_skipped,
                "collection_error": err_msg,
            })
            continue

        # ── Debug: raw interval diagnostics ──
        field_intervals_meta: list[dict] = []
        try:
            field_intervals_meta = service.get_last_intervals_metadata(codes_to_collect)
        except Exception:
            pass

        if args.debug_raw_intervals and field_intervals_meta:
            print(f"    Interval metadata: aggregation_interval={args.aggregation_interval}")
            print(f"    [DEBUG] Raw Sentinel intervals ({len(field_intervals_meta)}):")
            for im in field_intervals_meta:
                amb = "AMBIGUOUS" if im.get("interval_ambiguous") else "OK"
                reason = im.get("interval_ambiguous_reason", "")
                amb_suffix = f" — {reason}" if reason else ""
                print(f"        {im['interval_from']} -> {im['interval_to']} "
                      f"derived_date={im['derived_captured_date']} "
                      f"[{amb}]{amb_suffix}")
                for ic, ix in im.get("per_index", {}).items():
                    print(f"          {ic}: mean={ix.get('mean')} "
                          f"min={ix.get('min')} max={ix.get('max')} "
                          f"samples={ix.get('sample_count')}")
        elif args.debug_raw_intervals:
            print(f"      [DEBUG] No raw intervals available from Sentinel Hub response")

        # ── Process flat records: quality filter, then emit candidates ──
        field_candidate_records: list[dict] = []
        for rec in flat_records:
            code = rec["index_code"]
            captured_date_str = rec["captured_date"]

            try:
                cd = datetime.strptime(captured_date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                cd = date_to

            # Quality gate
            valid, reason = validate_index_quality(
                code,
                rec.get("mean_value"),
                cloud_cover_pct=rec.get("cloud_cover_pct"),
                min_value=rec.get("min_value"),
                max_value=rec.get("max_value"),
                valid_pixels_pct=rec.get("valid_pixels_pct"),
            )
            if not valid:
                print(f"      QUALITY BLOCKED: [{code}] date={captured_date_str} — {reason}")
                field_quality_blocked += 1
                total_quality_blocked += 1
                continue

            field_quality_passed += 1
            total_quality_passed += 1

            # Ambiguous interval check (flat parser already skips ambiguous,
            # but double-check for safety)
            if rec.get("interval_ambiguous"):
                reason = rec.get("interval_ambiguous_reason", "multi-day aggregate")
                print(f"      SKIP (multi-day): [{code}] date={captured_date_str} — {reason}")
                field_would_skip += 1
                total_would_skip_existing += 1
                continue

            field_candidates += 1
            total_candidates += 1

            field_candidate_records.append({
                "field_id": fid,
                "index_code": code,
                "captured_date": captured_date_str,
                "mean_value": rec.get("mean_value"),
                "min_value": rec.get("min_value"),
                "max_value": rec.get("max_value"),
                "std_value": rec.get("std_value"),
                "p10_value": rec.get("p10_value"),
                "p90_value": rec.get("p90_value"),
                "valid_pixels_pct": rec.get("valid_pixels_pct"),
                "cloud_cover_pct": rec.get("cloud_cover_pct"),
                "interval_from": rec.get("interval_from"),
                "interval_to": rec.get("interval_to"),
                "interval_ambiguous": rec.get("interval_ambiguous"),
                "satellite": getattr(service, "source", None),
            })

            mean_val = rec.get("mean_value")
            min_val = rec.get("min_value")
            max_val = rec.get("max_value")
            std_val = rec.get("std_value")
            p10_val = rec.get("p10_value")
            p90_val = rec.get("p90_value")
            vpp = rec.get("valid_pixels_pct")
            ccp = rec.get("cloud_cover_pct")

            print(f"      CANDIDATE: [{code}] date={captured_date_str} mean={mean_val} "
                  f"min={min_val} max={max_val} valid_pct={vpp} cloud_pct={ccp}")

            if do_db_writes:
                if not do_overwrite and check_exists_by_key(fid, cd, code):
                    print(f"        SKIP (exists in DB)")
                    field_db_skipped += 1
                    total_db_skipped += 1
                    continue

                if do_overwrite:
                    db = SessionLocal()
                    try:
                        from sqlalchemy import text as sa_text
                        db.execute(
                            sa_text(
                                "DELETE FROM satellite_index_records "
                                "WHERE field_id = :fid AND captured_date = :cd AND index_code = :ic"
                            ),
                            {"fid": fid, "cd": cd, "ic": code},
                        )
                        db.commit()
                        print(f"        OVERWRITE: deleted existing record for {code}")
                    except Exception:
                        db.rollback()
                    finally:
                        db.close()

                inserted = _insert_satellite_index_record(
                    field_id=fid,
                    captured_date=cd,
                    index_code=code,
                    mean_value=mean_val,
                    min_value=min_val,
                    max_value=max_val,
                    std_value=std_val,
                    p10_value=p10_val,
                    p90_value=p90_val,
                    valid_pixels_pct=vpp,
                    cloud_cover_pct=ccp,
                    satellite=getattr(service, "source", None),
                )
                if inserted:
                    field_db_inserted += 1
                    total_db_inserted += 1
                else:
                    field_db_skipped += 1
                    total_db_skipped += 1
            else:
                field_would_insert += 1
                total_would_insert += 1

        per_field_results.append({
            "field_id": fid,
            "field_name": fname,
            "status": "ok",
            "errors": field_errors,
            "codes_to_collect": codes_to_collect,
            "candidates": field_candidates,
            "quality_passed": field_quality_passed,
            "quality_blocked": field_quality_blocked,
            "would_insert": field_would_insert,
            "would_skip_existing": field_would_skip,
            "would_skip_multi_day": field_would_skip_multi_day,
            "db_inserted": field_db_inserted,
            "db_skipped": field_db_skipped,
            "sentinel_intervals": field_intervals_meta,
            "candidate_records": field_candidate_records,
        })

        print()

    # ── Summary ──
    finished_at = datetime.now(timezone.utc).replace(microsecond=0)
    duration = (finished_at - started_at).total_seconds()

    print("=" * 60)
    print("  COLLECTION SUMMARY")
    print("=" * 60)
    print(f"  Mode:                 {mode_label}")
    print(f"  Target table:         {target_table}")
    print(f"  Indices:              {', '.join(codes)}")
    print(f"  Fields processed:     {total_fields}")
    print(f"  Sentinel Hub calls:   {total_sentinel_calls}")
    print(f"  Total candidates:     {total_candidates}")
    print(f"  Quality passed:       {total_quality_passed}")
    print(f"  Quality blocked:      {total_quality_blocked}")
    print(f"  Would insert:         {total_would_insert}")
    print(f"  Would skip existing:  {total_would_skip_existing}")
    print(f"  DB inserted:          {total_db_inserted}")
    print(f"  DB skipped (exists):  {total_db_skipped}")
    print(f"  Errors:               {total_errors}")
    print(f"  Overwrite:            {do_overwrite}")
    print(f"  Idempotency mode:     {idempotency_mode}")

    # Per-code existing counts (aggregated across fields)
    print()
    print(f"  Existing records per index (informational -- does not pre-skip indices):")
    for code in codes:
        total_ec = 0
        for field in fields:
            total_ec += count_existing_satellite_index(field["id"], code, date_from, date_to)
        print(f"    {code}: {total_ec} records in date range")
    print()

    print(f"  Lock:                 {lock_path or args.lock_file or DEFAULT_LOCK_FILE}")
    if lock_path:
        release_lock(lock_path)
    print(f"  Started at:           {started_at.isoformat()}")
    print(f"  Finished at:          {finished_at.isoformat()}")
    print(f"  Duration:             {duration:.2f}s")
    print()

    if total_errors > 0:
        print(f"  WARNING: {total_errors} error(s) occurred during collection")
        print()

    if do_db_writes:
        print(f"  DB writes:            {total_db_inserted} inserted, {total_db_skipped} skipped")
    else:
        print(f"  DB writes:            NONE ({mode_label})")
    print("=" * 60)

    # JSON output if requested
    if args.output_log:
        json_output(args.output_log, {
            "run_mode": mode_label,
            "target_table": target_table,
            "is_ndvi": is_ndvi,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "date_range_days": date_range_days,
            "aggregation_interval": args.aggregation_interval,
            "indices": codes,
            "fields_selected": total_fields,
            "sentinel_hub_calls": total_sentinel_calls,
            "candidates_generated": total_candidates,
            "quality_passed": total_quality_passed,
            "quality_blocked": total_quality_blocked,
            "would_insert": total_would_insert,
            "would_skip_existing": total_would_skip_existing,
            "db_inserted": total_db_inserted,
            "db_skipped": total_db_skipped,
            "errors": total_errors,
            "idempotency_mode": idempotency_mode,
            "overwrite": do_overwrite,
            "lock_file": str(lock_path or args.lock_file or DEFAULT_LOCK_FILE),
            "sentinel_hub_calls_made": total_sentinel_calls,
            "db_writes_performed": total_db_inserted,
            "db_skipped_existing": total_db_skipped,
            "success_count": total_fields - total_errors,
            "failure_count": total_errors,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": duration,
            "exit_code": 0,
            # Sentinel interval metadata for date-mapping audit
            "per_field_intervals": [
                {
                    "field_id": r["field_id"],
                    "sentinel_intervals": r.get("sentinel_intervals", []),
                }
                for r in per_field_results
                if r.get("sentinel_intervals")
            ],
        })

    if total_errors > 0:
        sys.exit(1)
    sys.exit(0)


# -- JSON output helper --


def json_output(path: str, data: dict) -> None:
    """Write a JSON summary to the given path. No secrets included."""
    safe = {k: v for k, v in data.items() if k not in ("token", "access_token", "credentials")}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(safe, f, indent=2, default=str, ensure_ascii=False)
        print(f"  JSON log written to: {path}")
    except Exception as e:
        print(f"  WARNING: Failed to write JSON log: {e}", file=sys.stderr)


# -- Main entry point --


def validate_collection_mode(args: argparse.Namespace) -> None:
    """Reject unsafe modes before locks, DB access, services, or network calls."""
    write_enabled = bool(args.write) or bool(args.force)
    collection_enabled = bool(args.apply) or write_enabled
    if write_enabled and bool(args.mock_sentinel):
        raise SystemExit(2)
    if collection_enabled and bool(args.no_sentinel):
        raise SystemExit(2)
    if bool(args.mock_sentinel) and not bool(args.apply):
        raise SystemExit(2)
    if collection_enabled and not bool(args.mock_sentinel):
        try:
            validate_credentials(
                settings.sentinel_hub_client_id,
                settings.sentinel_hub_client_secret,
            )
        except SatelliteConfigurationError:
            raise SystemExit(2)


def main() -> None:
    args = parse_args()

    validate_collection_mode(args)

    # TASK_142_NDVI_APPLY_WRITE_GUARD
    # NDVI remains legacy-only here. Dry-run inspection is allowed.
    # Apply/write/force must not route NDVI through satellite_index_records collection.
    requested_index_codes_for_guard = set()
    if getattr(args, "index", None):
        requested_index_codes_for_guard.update(
            part.strip().lower()
            for part in str(args.index).split(",")
            if part.strip()
        )
    if getattr(args, "indices", None):
        requested_index_codes_for_guard.update(
            part.strip().lower()
            for part in str(args.indices).split(",")
            if part.strip()
        )
    if (
        "ndvi" in requested_index_codes_for_guard
        and (
            bool(getattr(args, "apply", False))
            or bool(getattr(args, "write", False))
            or bool(getattr(args, "force", False))
        )
    ):
        print("ERROR: ndvi is legacy-only and is not supported in apply/write/force mode for satellite_index_records. Use the legacy NDVI collector.")
        raise SystemExit(2)


    # Determine run mode:
    #   --apply or --write or --force  -> real mode (Sentinel Hub calls permitted)
    #   --dry-run or no mode flag      -> dry-run mode (safe default)
    has_real_flag = bool(args.apply) or bool(args.write) or bool(args.force)
    is_dry_run = bool(args.dry_run) or not has_real_flag

    # Validate scope: must have at least one scope limiter
    if args.field_id is None and args.enterprise_id is None and (args.max_fields is None or args.max_fields <= 0):
        if not is_dry_run:
            print(
                "ERROR: Real collection requires --field-id, --enterprise-id, or --max-fields "
                "to prevent accidental all-fields run.",
                file=sys.stderr,
            )
            sys.exit(2)

    # Parse and validate index codes early
    _parse_indexes(args.index, args.indices)

    if is_dry_run:
        run_dry_run(args)
    else:
        run_real(args)


if __name__ == "__main__":
    main()

