"""
Idempotency helpers for satellite collection CLI.

Provides reusable functions to:
  - Check whether records already exist
  - Plan write/skip/update actions per field+index+date
  - Report existing record counts per index

NDVI uses ndvi_records table (legacy).
Satellite indices use satellite_index_records table with DB-level
UniqueConstraint on (field_id, captured_date, index_code).

Uses raw SQL (via sqlalchemy.text) to avoid ORM mapper dependency issues
with string-based relationships across model files.
"""

import logging
from datetime import date
from typing import Any

from database import SessionLocal
from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

LEGACY_NDVI_CODE = "ndvi"

# Satellite index codes that go into satellite_index_records
SATELLITE_INDEX_CODES = {"savi", "evi", "ndmi", "ndre"}


class IdempotencyMode:
    """Idempotency mode for a collection run."""

    SKIP_EXISTING = "skip-existing"
    FORCE = "force"
    PLANNED_ONLY = "planned-only"


def count_existing_ndvi(field_id: int, date_from: date, date_to: date) -> int:
    """Count existing NDVI records for a field and date range. Read-only SELECT."""
    db = SessionLocal()
    try:
        row = db.execute(
            sa_text(
                "SELECT COUNT(id) FROM ndvi_records "
                "WHERE field_id = :fid AND captured_date >= :dfrom AND captured_date <= :dto"
            ),
            {"fid": field_id, "dfrom": date_from, "dto": date_to},
        ).scalar()
        return row or 0
    finally:
        db.close()


def count_existing_satellite_index(
    field_id: int, index_code: str, date_from: date, date_to: date,
) -> int:
    """Count existing satellite_index_records for a field, index, and date range."""
    db = SessionLocal()
    try:
        row = db.execute(
            sa_text(
                "SELECT COUNT(id) FROM satellite_index_records "
                "WHERE field_id = :fid AND index_code = :ic "
                "  AND captured_date >= :dfrom AND captured_date <= :dto"
            ),
            {"fid": field_id, "ic": index_code, "dfrom": date_from, "dto": date_to},
        ).scalar()
        return row or 0
    finally:
        db.close()


def check_exists_by_key(
    field_id: int,
    captured_date: date,
    index_code: str,
) -> bool:
    """
    Check whether a specific record exists by business key.

    For satellite indices: (field_id, captured_date, index_code).
    For NDVI: (field_id, captured_date).
    """
    db = SessionLocal()
    try:
        if index_code == LEGACY_NDVI_CODE:
            row = db.execute(
                sa_text(
                    "SELECT COUNT(id) FROM ndvi_records "
                    "WHERE field_id = :fid AND captured_date = :cd"
                ),
                {"fid": field_id, "cd": captured_date},
            ).scalar()
        else:
            row = db.execute(
                sa_text(
                    "SELECT COUNT(id) FROM satellite_index_records "
                    "WHERE field_id = :fid AND captured_date = :cd AND index_code = :ic"
                ),
                {"fid": field_id, "cd": captured_date, "ic": index_code},
            ).scalar()
        return (row or 0) > 0
    finally:
        db.close()


def plan_actions(
    field_id: int,
    codes: list[str],
    date_from: date,
    date_to: date,
    mode: str,
) -> list[dict[str, Any]]:
    """
    Determine planned actions for each (field, index) combination.

    Returns list of dicts with:
      - field_id, index_code, date_from, date_to
      - action: "insert", "skip_existing", "update_existing", "collect_then_check"
      - reason, existing_count

    In SKIP_EXISTING mode, actions with range-wide existing records return
    "collect_then_check" rather than "skip_existing".  The caller must still
    collect candidates and apply exact-date ``(field_id, captured_date, index_code)``
    idempotency per candidate.  Pre-skipping an entire index from range-wide
    counts is incorrect for historical backfill with partial existing data.

    Since exact captured_dates from Sentinel Hub are unknown until API call,
    plans at field+index level and reports existing counts.
    """
    legacy_ndvi = codes == [LEGACY_NDVI_CODE]

    plans = []
    for code in codes:
        if legacy_ndvi or code == LEGACY_NDVI_CODE:
            existing_count = count_existing_ndvi(field_id, date_from, date_to)
        else:
            existing_count = count_existing_satellite_index(field_id, code, date_from, date_to)

        if existing_count > 0:
            if mode == IdempotencyMode.SKIP_EXISTING:
                action = "collect_then_check"
                reason = "Existing range-wide records found; will check per-candidate exact-date idempotency"
            elif mode == IdempotencyMode.FORCE:
                action = "update_existing"
                reason = "Records exist but --force overrides — will update"
            else:
                action = "skip_existing"
                reason = "Records already exist (use --force to override)"
        else:
            action = "insert"
            reason = "No existing records — will insert"

        plans.append({
            "field_id": field_id,
            "index_code": code,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "existing_count": existing_count,
            "action": action,
            "reason": reason,
        })

    return plans


def has_db_level_uniqueness(index_code: str) -> bool:
    """
    Report whether the target table has DB-level uniqueness enforcement.

    satellite_index_records: True (UniqueConstraint on field_id, captured_date, index_code).
    ndvi_records: False (no unique constraint).
    """
    return index_code != LEGACY_NDVI_CODE
