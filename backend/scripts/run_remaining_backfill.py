"""
Run remaining backfill for fields that have no NDVI history before June 16, 2026.
Finds fields without historical data and runs backfill from Jan 1 to Jun 15.

Usage:
    python scripts/run_remaining_backfill.py
"""
import sys
import os
import time
import logging
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, init_db
from models.field import Field
from models.monitoring import NDVIRecord
from services.satellite import fetch_ndvi_for_field_date
from sqlalchemy import text

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

START = date(2026, 1, 1)
END = date(2026, 6, 15)
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


def get_existing_dates(db, field_id):
    result = db.execute(
        text("SELECT captured_date FROM ndvi_records WHERE field_id = :fid"),
        {"fid": field_id}
    )
    return {row[0] for row in result.fetchall()}


def backfill_field(db, field, target_dates, existing_dates):
    """Fetch historical NDVI for one field across target dates."""
    new_dates = [d for d in target_dates if d not in existing_dates]
    if not new_dates:
        return 0

    success_count = 0
    for target_date in new_dates:
        try:
            ndvi_data = fetch_ndvi_for_field_date(db, field, target_date)
            if ndvi_data is None:
                continue

            captured_date = date.fromisoformat(ndvi_data["captured_date"])

            existing = db.query(NDVIRecord).filter(
                NDVIRecord.field_id == field.id,
                NDVIRecord.captured_date == captured_date
            ).first()
            if existing:
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
                mean_ndvi=ndvi_data["mean_ndvi"],
                min_ndvi=ndvi_data.get("min_ndvi"),
                max_ndvi=ndvi_data.get("max_ndvi"),
                std_ndvi=ndvi_data.get("std_ndvi"),
                p10_ndvi=ndvi_data.get("p10_ndvi"),
                p90_ndvi=ndvi_data.get("p90_ndvi"),
                cloud_cover_pct=ndvi_data.get("cloud_cover_pct"),
                valid_pixels_pct=ndvi_data.get("valid_pixels_pct"),
                satellite=ndvi_data.get("satellite", "Sentinel-2"),
                ndvi_change=ndvi_change,
                ndvi_change_pct=ndvi_change_pct,
            )
            db.add(record)
            db.flush()
            success_count += 1
            time.sleep(RATE_LIMIT_S)

        except Exception as e:
            logger.error(f"  [{field.name}] Error for {target_date}: {e}")
            db.rollback()
            time.sleep(1)
            continue

    return success_count


def main():
    init_db()
    db = SessionLocal()

    target_dates = generate_sentinel2_dates(START, END)

    try:
        # Find fields with no NDVI before June 16
        rows = db.execute(text("""
            SELECT f.id
            FROM fields f
            WHERE f.id NOT IN (
                SELECT DISTINCT nr.field_id FROM ndvi_records nr
                WHERE nr.captured_date < '2026-06-16'
            )
            ORDER BY f.id
        """)).fetchall()

        field_ids = [r[0] for r in rows]
        logger.info(f"=== Remaining Backfill ===")
        logger.info(f"Date range: {START} to {END}")
        logger.info(f"Target dates: {len(target_dates)} (every ~5 days)")
        logger.info(f"Fields needing history: {len(field_ids)}")

        if not field_ids:
            logger.info("All fields already have history. Nothing to do.")
            return

        total_success = 0
        total_fields = len(field_ids)

        for i, fid in enumerate(field_ids):
            field = db.query(Field).get(fid)
            if not field:
                continue

            logger.info(f"\n[{i+1}/{total_fields}] Processing: {field.name} (id={fid})")

            existing = get_existing_dates(db, fid)
            count = backfill_field(db, field, target_dates, existing)
            total_success += count

            logger.info(f"  → Saved {count} new records")

            if (i + 1) % BATCH_SIZE == 0 and i + 1 < total_fields:
                db.commit()
                logger.info(f"\n--- Batch pause (processed {i+1}/{total_fields}) ---")
                time.sleep(BATCH_PAUSE_S)

        db.commit()

        # Summary
        r = db.execute(text("""
            SELECT COUNT(*) FROM ndvi_records
            WHERE mean_ndvi >= 0 AND mean_ndvi = mean_ndvi
        """)).fetchone()
        r2 = db.execute(text("""
            SELECT COUNT(DISTINCT field_id) FROM ndvi_records
            WHERE captured_date < '2026-06-16'
        """)).fetchone()

        logger.info(f"\n=== COMPLETE ===")
        logger.info(f"New records saved this run: {total_success}")
        logger.info(f"Total valid records: {r[0]}")
        logger.info(f"Fields with history before Jun 16: {r2[0]}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
