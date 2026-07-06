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

Dry-run mode: no Sentinel Hub calls, no DB writes.
NDVI routes to legacy ndvi_records path only. New indices routed to
satellite_index_records only.

Usage:
  python backend/scripts/collect_satellite_indices.py --help
  python backend/scripts/collect_satellite_indices.py --dry-run --field-id 4 --index savi --date-from 2026-07-01 --date-to 2026-07-01 --max-fields 1
  python backend/scripts/collect_satellite_indices.py --dry-run --field-id 4 --indices savi,ndmi --date-from 2026-07-01 --date-to 2026-07-01 --max-fields 1
  python backend/scripts/collect_satellite_indices.py --dry-run --field-id 4 --index ndvi --date-from 2026-07-01 --date-to 2026-07-01 --max-fields 1
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, "backend")

from config import settings
from database import SessionLocal
from services.collector_idempotency import (
    IdempotencyMode,
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
    normalize_index_code,
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

    # Mode
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Safe mode: no Sentinel Hub calls, no DB writes. Default: True if no --force.",
    )

    # Scope
    parser.add_argument(
        "--field-id", type=int, default=None,
        help="Limit collection to a single field ID.",
    )
    parser.add_argument(
        "--enterprise-id", type=int, default=None,
        help="Limit collection to fields belonging to an enterprise.",
    )
    parser.add_argument(
        "--max-fields", type=int, default=None,
        help="Maximum number of fields to process. Required unless --field-id or --enterprise-id is set.",
    )

    # Index selection
    parser.add_argument(
        "--index", type=str, default=None,
        help="Single index code: ndvi, savi, evi, ndmi, ndre.",
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

    # Safety guards
    parser.add_argument(
        "--force", action="store_true", default=False,
        help="Explicit opt-in for real collection (enables Sentinel Hub calls and DB writes). "
             "Also overrides skip-existing in idempotency checks.",
    )
    parser.add_argument(
        "--skip-existing", action="store_true", default=False,
        help="Skip fields/indexes that already have data for the date range.",
    )
    parser.add_argument(
        "--no-sentinel", action="store_true", default=False,
        help="Prevent any Sentinel Hub API calls even if credentials are available.",
    )
    parser.add_argument(
        "--output-log", type=str, default=None,
        help="Optional path to write JSON run summary outside the repo.",
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


def run_real(args: argparse.Namespace) -> None:
    """
    Execute real collection mode stub.
    For future implementation of actual Sentinel Hub collection.
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

    date_from = _validate_date(args.date_from or (date.today() - timedelta(days=30)).isoformat(), "--date-from")
    date_to = _validate_date(args.date_to or date.today().isoformat(), "--date-to")

    # Acquire lock for real mode
    force_lock = args.force_lock or args.break_stale_lock
    lock_path = acquire_lock(
        lock_file=args.lock_file,
        force=force_lock,
        dry_run=False,
    )

    print()
    print("=" * 60)
    print("  REAL COLLECTION MODE")
    print("=" * 60)
    target_table = "ndvi_records (legacy NDVI)" if is_ndvi else "satellite_index_records"
    print(f"  Target table:     {target_table}")
    print(f"  Indices:          {', '.join(codes)}")
    print(f"  Lock file:        {lock_path or args.lock_file or DEFAULT_LOCK_FILE}")
    print()
    print("  This mode is not fully implemented yet.")
    print("  Use --dry-run for validation.")
    print("=" * 60)

    # Release lock before exit
    release_lock(lock_path)
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


def main() -> None:
    args = parse_args()

    # Guard: dry-run is the default safe mode unless --force is set
    is_dry_run = args.dry_run or not args.force

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
