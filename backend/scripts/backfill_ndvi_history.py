"""
Backfill NDVI history for all fields from Jan 1, 2026 to Jun 15, 2026.
Uses Sentinel Hub Statistical API to get NDVI for each Sentinel-2 pass.

Usage:
    python scripts/backfill_ndvi_history.py [--start 2026-01-01] [--end 2026-06-15] [--batch 10] [--enterprise_id 9]

Options:
    --start         Start date (default: 2026-01-01)
    --end           End date (default: 2026-06-15)
    --batch         Fields per batch before pause (default: 10)
    --enterprise_id Only process fields from this enterprise (optional)
    --field_id      Process a single field (optional, for testing)
    --dry-run       Show what would be done without actually fetching
"""
import sys
import os
import time
import argparse
import logging
from datetime import datetime, timedelta, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, init_db
from models.field import Field
from models.monitoring import NDVIRecord
from services.satellite import fetch_ndvi_for_field_date, get_satellite_service, validate_ndvi_quality
from services.satellite_safety import require_payload_provenance
from sqlalchemy import text

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def get_existing_dates(db, field_id):
    """Get set of dates that already have NDVI records for this field."""
    result = db.execute(
        text("SELECT captured_date FROM ndvi_records WHERE field_id = :fid"),
        {"fid": field_id}
    )
    return {row[0] for row in result.fetchall()}


def generate_sentinel2_dates(start_date, end_date):
    """
    Generate approximate Sentinel-2 acquisition dates.
    Sentinel-2A + 2B combined revisit is ~5 days.
    """
    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=5)
    return dates


def backfill_field(db, field, target_dates, existing_dates, *, service):
    """Fetch historical NDVI for one field across target dates."""
    field_name = field.name or f"field_{field.id}"

    new_dates = [d for d in target_dates if d not in existing_dates]

    if not new_dates:
        logger.info(f"  [{field_name}] All {len(target_dates)} dates already loaded, skipping")
        return 0

    logger.info(
        f"  [{field_name}] {len(new_dates)} dates to fetch "
        f"({len(existing_dates)} already exist)"
    )

    success_count = 0

    for target_date in new_dates:
        try:
            ndvi_data = fetch_ndvi_for_field_date(db, field, target_date, service=service)

            if ndvi_data is None:
                continue

            captured_date = date.fromisoformat(ndvi_data["captured_date"])

            # Check if this exact date already exists (race condition guard)
            existing = db.query(NDVIRecord).filter(
                NDVIRecord.field_id == field.id,
                NDVIRecord.captured_date == captured_date
            ).first()
            if existing:
                continue

            # Calculate NDVI change from previous record
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

            # Save the record
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
                satellite=require_payload_provenance(ndvi_data),
                ndvi_change=ndvi_change,
                ndvi_change_pct=ndvi_change_pct,
            )
            db.add(record)
            db.flush()
            success_count += 1

            # Rate limiting
            time.sleep(0.5)

        except Exception as e:
            logger.error(f"  [{field_name}] Error for {target_date}: {e}")
            db.rollback()
            time.sleep(1)
            continue

    return success_count


def main():
    parser = argparse.ArgumentParser(description='Backfill NDVI history')
    parser.add_argument('--start', default='2026-01-01', help='Start date YYYY-MM-DD')
    parser.add_argument('--end', default='2026-06-15', help='End date YYYY-MM-DD')
    parser.add_argument('--batch', type=int, default=10, help='Fields per batch')
    parser.add_argument('--enterprise_id', type=int, help='Filter by enterprise')
    parser.add_argument('--field_id', type=int, help='Process single field')
    parser.add_argument('--dry-run', action='store_true', help='Show plan only')
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)
    target_dates = generate_sentinel2_dates(start_date, end_date)

    logger.info(f"=== NDVI Historical Backfill ===")
    logger.info(f"Date range: {start_date} to {end_date}")
    logger.info(f"Target dates: {len(target_dates)} (every ~5 days)")

    service = None if args.dry_run else get_satellite_service()
    init_db()
    db = SessionLocal()

    try:
        query = db.query(Field)
        if args.field_id:
            query = query.filter(Field.id == args.field_id)
        elif args.enterprise_id:
            query = query.filter(Field.enterprise_id == args.enterprise_id)

        fields = query.all()
        logger.info(f"Fields to process: {len(fields)}")

        if args.dry_run:
            # Show first 5 fields as sample
            for f in fields[:5]:
                existing = get_existing_dates(db, f.id)
                new = len([d for d in target_dates if d not in existing])
                logger.info(f"  {f.name}: {new} new dates ({len(existing)} existing)")
            if len(fields) > 5:
                logger.info(f"  ... and {len(fields) - 5} more fields")

            # Quick total estimate: count existing records, back out estimate
            count_result = db.execute(text("SELECT COUNT(*) FROM ndvi_records"))
            existing_total = count_result.fetchone()[0]
            # ~6 records per field already exist from recent fetches
            already_covered = existing_total
            total_needed = len(fields) * len(target_dates)
            total_requests = max(0, total_needed - already_covered)

            logger.info(f"\nTotal API requests needed: ~{total_requests}")
            logger.info(f"Estimated PU cost: ~{total_requests} PU")
            logger.info(f"Estimated time: ~{total_requests * 0.7 / 60:.0f} minutes")
            return

        total_success = 0
        total_fields = len(fields)

        for i, field in enumerate(fields):
            field_name = field.name or f"field_{field.id}"
            logger.info(f"\n[{i+1}/{total_fields}] Processing: {field_name}")

            existing = get_existing_dates(db, field.id)

            count = backfill_field(db, field, target_dates, existing, service=service)
            total_success += count

            logger.info(f"  → Saved {count} new records")

            # Batch commit and pause
            if (i + 1) % args.batch == 0 and i + 1 < total_fields:
                db.commit()
                logger.info(f"\n--- Batch pause (processed {i+1}/{total_fields}) ---")
                time.sleep(5)

        db.commit()
        logger.info(f"\n=== COMPLETE ===")
        logger.info(f"Total new NDVI records saved: {total_success}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
