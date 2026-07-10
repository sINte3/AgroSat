"""
Historical NDVI backfill based on earliest-record coverage detection.
Finds fields whose earliest NDVI record is missing or after 2026-02-01,
then backfills from 2026-01-01 up to (but not including) the first record.

Usage:
    python scripts/run_remaining_backfill.py           # real backfill
    python scripts/run_remaining_backfill.py --dry-run  # dry run (no writes)
"""
import sys
import os
import time
import argparse
import logging
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, init_db
from models.field import Field, CropSeason
from models.enterprise import Enterprise
from models.crop import CropType
from models.monitoring import NDVIRecord, Alert, ScoutingNote, User
from services.satellite import fetch_ndvi_for_field_date, get_satellite_service
from services.satellite_safety import require_payload_provenance
from sqlalchemy import text

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

BACKFILL_START_DATE = date(2026, 1, 1)
EARLY_HISTORY_THRESHOLD_DATE = date(2026, 2, 1)
CONFIGURED_HISTORICAL_END = date(2026, 6, 15)
BATCH_SIZE = 10
BATCH_PAUSE_S = 3
RATE_LIMIT_S = 0.5


def generate_sentinel2_dates(start_date, end_date):
    """Generate approximate Sentinel-2 acquisition dates (every ~5 days)."""
    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=5)
    return dates


def recalculate_change_for_field(db, field_id):
    """
    Recalculate ndvi_change and ndvi_change_pct for all records of a field
    in chronological order. First record gets None values.
    """
    records = (
        db.query(NDVIRecord)
        .filter(NDVIRecord.field_id == field_id)
        .order_by(NDVIRecord.captured_date.asc())
        .all()
    )
    previous = None
    for record in records:
        if previous is None:
            record.ndvi_change = None
            record.ndvi_change_pct = None
        else:
            if record.mean_ndvi is not None and previous.mean_ndvi is not None:
                record.ndvi_change = record.mean_ndvi - previous.mean_ndvi
                if previous.mean_ndvi >= 0.15:
                    record.ndvi_change_pct = (record.ndvi_change / previous.mean_ndvi) * 100
                else:
                    record.ndvi_change_pct = None
            else:
                record.ndvi_change = None
                record.ndvi_change_pct = None
        previous = record
    db.flush()


def main():
    parser = argparse.ArgumentParser(description="Backfill historical NDVI")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without writing any data")
    args = parser.parse_args()

    service = None if args.dry_run else get_satellite_service()
    init_db()
    db = SessionLocal()

    try:
        # ── Candidate selection: earliest-record based ──────────────────────
        rows = db.execute(text("""
            SELECT
                f.id,
                f.name,
                MIN(n.captured_date) AS first_ndvi_date,
                MAX(n.captured_date) AS last_ndvi_date,
                COUNT(n.id) AS ndvi_count
            FROM fields f
            LEFT JOIN ndvi_records n ON n.field_id = f.id
            WHERE f.is_active = true
            GROUP BY f.id, f.name
            HAVING MIN(n.captured_date) IS NULL
               OR MIN(n.captured_date) > DATE '2026-02-01'
            ORDER BY f.id
        """)).fetchall()

        candidate_fields = []
        for r in rows:
            candidate_fields.append({
                "id": r.id,
                "name": r.name,
                "first_ndvi_date": r.first_ndvi_date,
                "last_ndvi_date": r.last_ndvi_date,
                "ndvi_count": r.ndvi_count,
            })

        logger.info("=" * 60)
        logger.info("  Historical NDVI Backfill")
        logger.info(f"  Backfill window start : {BACKFILL_START_DATE}")
        logger.info(f"  Early-history threshold: {EARLY_HISTORY_THRESHOLD_DATE}")
        logger.info(f"  Candidate fields      : {len(candidate_fields)}")
        logger.info("=" * 60)

        if not candidate_fields:
            logger.info("All active fields have early history (Jan 2026). Nothing to do.")
            return

        if args.dry_run:
            logger.info("\n  DRY RUN — no data will be written\n")
            logger.info(f"  {'Field ID':<10} {'Field Name':<30} {'First NDVI':<14} {'Last NDVI':<14} {'Count':<6} Backfill window")
            logger.info(f"  {'-'*9:<10} {'-'*29:<30} {'-'*13:<14} {'-'*13:<14} {'-'*5:<6} {'-'*20}")
            for cf in candidate_fields:
                first_date = str(cf["first_ndvi_date"]) if cf["first_ndvi_date"] else "NONE"
                last_date = str(cf["last_ndvi_date"]) if cf["last_ndvi_date"] else "NONE"
                if cf["first_ndvi_date"] is None:
                    backfill_end = CONFIGURED_HISTORICAL_END
                else:
                    backfill_end = cf["first_ndvi_date"]
                target_dates_global = generate_sentinel2_dates(BACKFILL_START_DATE, backfill_end)
                if backfill_end <= BACKFILL_START_DATE:
                    window_str = "skip (already has early history)"
                else:
                    window_str = f"{BACKFILL_START_DATE} → {backfill_end - timedelta(days=1)} ({len(target_dates_global)} dates)"
                logger.info(f"  {cf['id']:<10} {cf['name']:<30} {first_date:<14} {last_date:<14} {cf['ndvi_count']:<6} {window_str}")
            logger.info("\n  Dry-run complete. Rerun without --dry-run to execute.")
            return

        # ── Real backfill ──────────────────────────────────────────────────
        logger.info(f"\n  Starting backfill for {len(candidate_fields)} fields...")

        total_attempted = 0
        total_inserted = 0
        total_skipped_existing = 0
        total_skipped_no_data = 0
        total_skipped_quality = 0
        total_errors = 0
        total_fields = len(candidate_fields)

        for i, cf in enumerate(candidate_fields):
            field = db.get(Field, cf["id"])
            if not field:
                continue

            # Compute backfill window per field
            if cf["first_ndvi_date"] is None:
                backfill_end_exclusive = CONFIGURED_HISTORICAL_END
            else:
                backfill_end_exclusive = cf["first_ndvi_date"]

            if backfill_end_exclusive <= BACKFILL_START_DATE:
                logger.info(f"[{i+1}/{total_fields}] Field {field.name} (id={field.id}): already has early history; skip")
                continue

            target_dates = generate_sentinel2_dates(BACKFILL_START_DATE, backfill_end_exclusive)
            # Exclude dates on or after first record date
            target_dates = [d for d in target_dates if d < backfill_end_exclusive]

            if not target_dates:
                continue

            field_attempted = 0
            field_inserted = 0
            field_skipped_existing = 0
            field_skipped_no_data = 0
            field_skipped_quality = 0
            field_errors = 0

            logger.info(
                f"\n[{i+1}/{total_fields}] {field.name} (id={field.id}): "
                f"backfilling {len(target_dates)} dates "
                f"[{BACKFILL_START_DATE} → {backfill_end_exclusive - timedelta(days=1)}]"
            )

            for target_date in target_dates:
                field_attempted += 1
                try:
                    ndvi_data = fetch_ndvi_for_field_date(db, field, target_date, service=service)
                    if ndvi_data is None:
                        field_skipped_no_data += 1
                        logger.debug(f"  [{field.name}] {target_date}: no data (cloud/mock)")
                        continue

                    captured_date = date.fromisoformat(ndvi_data["captured_date"])

                    # Idempotency check against actual captured date
                    existing = db.query(NDVIRecord).filter(
                        NDVIRecord.field_id == field.id,
                        NDVIRecord.captured_date == captured_date,
                    ).first()

                    if existing:
                        field_skipped_existing += 1
                        logger.info(f"  [{field.name}] {captured_date}: already exists, skipping")
                        continue

                    prev_record = db.query(NDVIRecord).filter(
                        NDVIRecord.field_id == field.id,
                        NDVIRecord.captured_date < captured_date
                    ).order_by(NDVIRecord.captured_date.desc()).first()

                    ndvi_change = None
                    ndvi_change_pct = None
                    if prev_record and prev_record.mean_ndvi:
                        ndvi_change = ndvi_data["mean_ndvi"] - prev_record.mean_ndvi
                        if prev_record.mean_ndvi >= 0.15:
                            ndvi_change_pct = (ndvi_change / prev_record.mean_ndvi) * 100

                    record = NDVIRecord(
                        field_id=field.id,
                        captured_date=captured_date,
                        mean_ndvi=ndvi_data.get("mean_ndvi"),
                        min_ndvi=ndvi_data.get("min_ndvi"),
                        max_ndvi=ndvi_data.get("max_ndvi"),
                        std_ndvi=ndvi_data.get("std_ndvi"),
                        p10_ndvi=ndvi_data.get("p10_ndvi"),
                        p90_ndvi=ndvi_data.get("p90_ndvi"),
                        cloud_cover_pct=ndvi_data.get("cloud_cover_pct"),
                        valid_pixels_pct=ndvi_data.get("valid_pixels_pct"),
                        satellite=require_payload_provenance(ndvi_data),
                        ndvi_change=ndvi_change,
                        ndvi_change_pct=ndvi_change_pct,
                    )
                    db.add(record)
                    db.flush()
                    field_inserted += 1
                    time.sleep(RATE_LIMIT_S)

                except Exception as e:
                    logger.error(f"  [{field.name}] Error for {target_date}: {e}")
                    db.rollback()
                    field_errors += 1
                    time.sleep(1)
                    continue

            # Recalculate ndvi_change/ndvi_change_pct chronologically
            if field_inserted > 0:
                recalculate_change_for_field(db, field.id)

            total_attempted += field_attempted
            total_inserted += field_inserted
            total_skipped_existing += field_skipped_existing
            total_skipped_no_data += field_skipped_no_data
            total_skipped_quality += field_skipped_quality
            total_errors += field_errors

            logger.info(
                f"  → inserted={field_inserted} skipped_existing={field_skipped_existing} "
                f"no_data={field_skipped_no_data} errors={field_errors}"
            )

            if (i + 1) % BATCH_SIZE == 0 and (i + 1) < total_fields:
                db.commit()
                logger.info(f"\n--- Batch commit (processed {i+1}/{total_fields}) ---")
                time.sleep(BATCH_PAUSE_S)

        # Final commit
        db.commit()

        # ── Summary ────────────────────────────────────────────────────────
        logger.info("\n" + "=" * 60)
        logger.info("  BACKFILL COMPLETE")
        logger.info(f"  candidate_fields  = {len(candidate_fields)}")
        logger.info(f"  attempted_dates   = {total_attempted}")
        logger.info(f"  inserted_records  = {total_inserted}")
        logger.info(f"  skipped_existing  = {total_skipped_existing}")
        logger.info(f"  skipped_no_data   = {total_skipped_no_data}")
        logger.info(f"  skipped_quality   = {total_skipped_quality}")
        logger.info(f"  errors            = {total_errors}")
        logger.info("=" * 60)

    finally:
        db.close()


if __name__ == "__main__":
    main()
