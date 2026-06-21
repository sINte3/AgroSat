# TASK_025: NDVI Quality Gate + Historical Backfill

## Goal
Two-part task:
1. **Quality gate** — reject bad NDVI snapshots (negative values, high cloud, artifacts) BEFORE saving to the database
2. **Historical backfill** — load NDVI history for all 275 fields from January 1, 2026 to June 15, 2026 so charts show meaningful seasonal dynamics

---

## Step 0: Read files first (MANDATORY)

```
backend/services/satellite.py
backend/services/alert_engine.py
backend/scripts/fetch_all_ndvi.py
backend/models/monitoring.py
backend/config.py
backend/.env
```

Check current NDVI data range:
```bash
cd backend
python -c "
from database import engine
from sqlalchemy import text
with engine.connect() as conn:
    result = conn.execute(text('''
        SELECT 
            MIN(captured_date) as earliest,
            MAX(captured_date) as latest,
            COUNT(*) as total_records,
            COUNT(CASE WHEN mean_ndvi < 0 THEN 1 END) as negative_count,
            COUNT(CASE WHEN cloud_cover_pct > 30 THEN 1 END) as cloudy_count
        FROM ndvi_records
    '''))
    row = result.fetchone()
    print(f'Earliest: {row[0]}')
    print(f'Latest: {row[1]}')
    print(f'Total records: {row[2]}')
    print(f'Negative NDVI records: {row[3]}')
    print(f'Cloudy records (>30%): {row[4]}')
"
```

Also check how many processing units we might use. Each field request ≈ 1 PU. Sentinel-2 revisit is ~5 days, so Jan 1 – Jun 15 ≈ 33 passes × 275 fields = ~9,075 requests. This fits within the 30,000 PU/month free tier but will take time.

---

## PART 1: NDVI Quality Gate

### Step 1: Add quality validation to satellite.py

Edit `backend/services/satellite.py`

Find the function that processes/saves NDVI results (likely called `fetch_ndvi_for_field`, `process_ndvi`, or similar — it's the function that creates NDVIRecord objects and adds them to the database).

**Add a quality gate function** near the top of the file (after imports):

```python
import logging

logger = logging.getLogger(__name__)

def validate_ndvi_quality(mean_ndvi, cloud_cover_pct=None, min_ndvi=None, max_ndvi=None, field_name=""):
    """
    Validate NDVI data quality before saving to database.
    Returns (is_valid, rejection_reason).
    
    Rejection criteria for agricultural fields:
    - mean_ndvi < 0: water/cloud/shadow artifact (impossible for crops)
    - mean_ndvi > 1.0: sensor error
    - cloud_cover_pct > 30: too cloudy for reliable reading
    - min_ndvi < -0.5 AND max_ndvi > 0.5: mixed pixel (cloud edge + vegetation)
    - mean_ndvi very close to 0 (< 0.02) with low std: bare sensor noise
    """
    if mean_ndvi is None:
        return False, "mean_ndvi is None"
    
    # Convert to float safely
    try:
        mean_ndvi = float(mean_ndvi)
    except (TypeError, ValueError):
        return False, f"mean_ndvi not numeric: {mean_ndvi}"
    
    # Negative NDVI — cloud/shadow/water artifact
    if mean_ndvi < 0:
        logger.warning(
            f"NDVI quality gate REJECT [{field_name}]: negative NDVI {mean_ndvi:.4f} "
            f"(cloud/shadow artifact)"
        )
        return False, f"negative NDVI ({mean_ndvi:.4f})"
    
    # NDVI > 1.0 — sensor error
    if mean_ndvi > 1.0:
        logger.warning(
            f"NDVI quality gate REJECT [{field_name}]: NDVI > 1.0 ({mean_ndvi:.4f})"
        )
        return False, f"NDVI exceeds 1.0 ({mean_ndvi:.4f})"
    
    # High cloud cover
    if cloud_cover_pct is not None:
        try:
            cloud_cover_pct = float(cloud_cover_pct)
        except (TypeError, ValueError):
            cloud_cover_pct = None
    
    if cloud_cover_pct is not None and cloud_cover_pct > 30:
        logger.info(
            f"NDVI quality gate REJECT [{field_name}]: cloud cover {cloud_cover_pct:.1f}% > 30%"
        )
        return False, f"high cloud cover ({cloud_cover_pct:.1f}%)"
    
    # Mixed pixel detection: huge range suggests cloud edge
    if min_ndvi is not None and max_ndvi is not None:
        try:
            min_val = float(min_ndvi)
            max_val = float(max_ndvi)
            if min_val < -0.3 and max_val > 0.4:
                logger.warning(
                    f"NDVI quality gate REJECT [{field_name}]: mixed pixel "
                    f"(min={min_val:.4f}, max={max_val:.4f})"
                )
                return False, f"mixed pixel (min={min_val:.4f}, max={max_val:.4f})"
        except (TypeError, ValueError):
            pass
    
    # Passed all checks
    return True, "ok"
```

### Step 2: Apply quality gate where NDVI records are saved

Find the place in satellite.py where `NDVIRecord` is created and added to the DB session. It will look something like:

```python
ndvi_record = NDVIRecord(
    field_id=field_id,
    captured_date=...,
    mean_ndvi=mean_ndvi,
    ...
)
db.add(ndvi_record)
```

**BEFORE** that line, add the quality check:

```python
# Quality gate — reject bad data before saving
is_valid, reason = validate_ndvi_quality(
    mean_ndvi=mean_ndvi,
    cloud_cover_pct=cloud_cover_pct,
    min_ndvi=min_ndvi,
    max_ndvi=max_ndvi,
    field_name=field.name if hasattr(field, 'name') else f"field_{field_id}"
)

if not is_valid:
    logger.info(f"Skipping NDVI save for field {field_id}: {reason}")
    return None  # or continue, depending on the flow
```

**IMPORTANT:** Look at the actual function signature and return pattern. If the function returns the NDVIRecord, return None on rejection. If it's in a loop, use `continue`. Adapt to the existing code structure.

### Step 3: Clean existing bad records from the database

Create `backend/scripts/cleanup_bad_ndvi.py`:

```python
"""
Remove existing NDVI records with negative values or high cloud cover.
Also deactivate any alerts that were triggered by these bad records.
Run once to clean historical data.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import engine
from sqlalchemy import text

def cleanup():
    with engine.connect() as conn:
        # Count bad records first
        result = conn.execute(text("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN mean_ndvi < 0 THEN 1 END) as negative,
                COUNT(CASE WHEN cloud_cover_pct > 30 THEN 1 END) as cloudy,
                COUNT(CASE WHEN mean_ndvi > 1.0 THEN 1 END) as over_one
            FROM ndvi_records
        """))
        row = result.fetchone()
        print(f"Current records: {row[0]}")
        print(f"  Negative NDVI: {row[1]}")
        print(f"  Cloud > 30%: {row[2]}")
        print(f"  NDVI > 1.0: {row[3]}")
        
        if row[1] + row[2] + row[3] == 0:
            print("No bad records found. Nothing to clean.")
            return
        
        # Delete bad NDVI records
        result = conn.execute(text("""
            DELETE FROM ndvi_records 
            WHERE mean_ndvi < 0 
               OR mean_ndvi > 1.0 
               OR cloud_cover_pct > 30
        """))
        deleted = result.rowcount
        print(f"\nDeleted {deleted} bad NDVI records")
        
        # Deactivate alerts with impossible triggered_value
        result = conn.execute(text("""
            UPDATE alerts 
            SET is_active = false 
            WHERE is_active = true 
              AND (ABS(triggered_value) > 1.0 OR triggered_value < 0)
        """))
        deactivated = result.rowcount
        print(f"Deactivated {deactivated} alerts with bad triggered values")
        
        conn.commit()
        
        # Show remaining count
        result = conn.execute(text("SELECT COUNT(*) FROM ndvi_records"))
        remaining = result.fetchone()[0]
        print(f"\nRemaining valid NDVI records: {remaining}")

if __name__ == "__main__":
    cleanup()
```

Run it:
```bash
cd backend
python scripts/cleanup_bad_ndvi.py
```

---

## PART 2: Historical NDVI Backfill

### Step 4: Create historical backfill script

Create `backend/scripts/backfill_ndvi_history.py`:

This script fetches NDVI for all fields from January 1, 2026 to June 15, 2026.
It uses the same Sentinel Hub API as the existing satellite.py service but iterates over historical dates.

```python
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

from database import SessionLocal
from models.enterprise import Enterprise
from models.field import Field
from models.monitoring import NDVIRecord
from services.satellite import SatelliteService
from sqlalchemy import text, and_

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
    We'll try every 5 days and let Sentinel Hub find the nearest actual pass.
    """
    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=5)
    return dates


def backfill_field(db, satellite_service, field, target_dates, existing_dates):
    """Fetch historical NDVI for one field across target dates."""
    field_name = field.name or f"field_{field.id}"
    
    # Skip dates we already have
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
            # Use the satellite service to fetch NDVI for a specific date
            # The service should have a method like fetch_ndvi_for_field(field, date)
            # or we need to call the Sentinel Hub API directly
            
            result = satellite_service.fetch_ndvi_for_field_date(
                db=db,
                field=field,
                target_date=target_date
            )
            
            if result:
                success_count += 1
            
            # Rate limiting: small pause between requests
            time.sleep(0.5)
            
        except Exception as e:
            logger.error(f"  [{field_name}] Error for {target_date}: {e}")
            time.sleep(1)  # Longer pause on error
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
    
    db = SessionLocal()
    
    try:
        # Get fields
        query = db.query(Field)
        if args.field_id:
            query = query.filter(Field.id == args.field_id)
        elif args.enterprise_id:
            query = query.filter(Field.enterprise_id == args.enterprise_id)
        
        fields = query.all()
        logger.info(f"Fields to process: {len(fields)}")
        
        if args.dry_run:
            for f in fields[:5]:
                existing = get_existing_dates(db, f.id)
                new = len([d for d in target_dates if d not in existing])
                logger.info(f"  {f.name}: {new} new dates ({len(existing)} existing)")
            if len(fields) > 5:
                logger.info(f"  ... and {len(fields) - 5} more fields")
            total_requests = sum(
                len([d for d in target_dates if d not in get_existing_dates(db, f.id)])
                for f in fields
            )
            logger.info(f"\nTotal API requests needed: ~{total_requests}")
            logger.info(f"Estimated PU cost: ~{total_requests} PU")
            logger.info(f"Estimated time: ~{total_requests * 0.7 / 60:.0f} minutes")
            return
        
        # Initialize satellite service
        satellite_service = SatelliteService()
        
        total_success = 0
        total_fields = len(fields)
        
        for i, field in enumerate(fields):
            field_name = field.name or f"field_{field.id}"
            logger.info(f"\n[{i+1}/{total_fields}] Processing: {field_name}")
            
            existing = get_existing_dates(db, field.id)
            
            count = backfill_field(db, satellite_service, field, target_dates, existing)
            total_success += count
            
            logger.info(f"  → Saved {count} new records")
            
            # Batch pause to avoid rate limiting
            if (i + 1) % args.batch == 0 and i + 1 < total_fields:
                logger.info(f"\n--- Batch pause (processed {i+1}/{total_fields}) ---")
                db.commit()
                time.sleep(5)
        
        db.commit()
        logger.info(f"\n=== COMPLETE ===")
        logger.info(f"Total new NDVI records saved: {total_success}")
        
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

### Step 5: Add `fetch_ndvi_for_field_date` method to satellite service

Edit `backend/services/satellite.py`

The existing service likely has a method like `fetch_ndvi_for_field()` that fetches the LATEST NDVI. 
We need a variant that fetches NDVI for a SPECIFIC DATE.

**Read the existing `fetch_ndvi_for_field` method carefully.** Understand how it:
1. Builds the Sentinel Hub API request
2. Specifies the date range (time_from, time_to)
3. Passes the field geometry
4. Parses the response
5. Creates the NDVIRecord

Then create a new method `fetch_ndvi_for_field_date` that does the same thing but for a specific target date. The key difference is the date range:

```python
def fetch_ndvi_for_field_date(self, db, field, target_date):
    """
    Fetch NDVI for a specific date (±2 days window to find nearest pass).
    Returns NDVIRecord if valid data found, None otherwise.
    """
    # Search window: target_date ± 2 days
    time_from = target_date - timedelta(days=2)
    time_to = target_date + timedelta(days=2)
    
    # ... (use same API call pattern as existing fetch_ndvi_for_field,
    #      but with time_from/time_to set to the target window)
    
    # IMPORTANT: After getting the result, apply quality gate:
    is_valid, reason = validate_ndvi_quality(
        mean_ndvi=mean_ndvi,
        cloud_cover_pct=cloud_cover_pct,
        min_ndvi=min_ndvi,
        max_ndvi=max_ndvi,
        field_name=field.name
    )
    
    if not is_valid:
        logger.debug(f"Quality gate rejected {field.name} @ {target_date}: {reason}")
        return None
    
    # Check if this exact date already exists
    existing = db.query(NDVIRecord).filter(
        NDVIRecord.field_id == field.id,
        NDVIRecord.captured_date == actual_captured_date  # date from the API response
    ).first()
    
    if existing:
        return None  # already have this date
    
    # Calculate NDVI change from previous record
    prev_record = db.query(NDVIRecord).filter(
        NDVIRecord.field_id == field.id,
        NDVIRecord.captured_date < actual_captured_date
    ).order_by(NDVIRecord.captured_date.desc()).first()
    
    change_pct = None
    if prev_record and prev_record.mean_ndvi and prev_record.mean_ndvi > 0.05:
        change_pct = ((mean_ndvi - prev_record.mean_ndvi) / prev_record.mean_ndvi) * 100
    
    # Save the record
    ndvi_record = NDVIRecord(
        field_id=field.id,
        captured_date=actual_captured_date,
        mean_ndvi=mean_ndvi,
        min_ndvi=min_ndvi,
        max_ndvi=max_ndvi,
        std_ndvi=std_ndvi,
        cloud_cover_pct=cloud_cover_pct,
        ndvi_change_pct=change_pct,
        satellite="sentinel-2",
        # ... other fields as in the existing method
    )
    db.add(ndvi_record)
    
    return ndvi_record
```

**CRITICAL: Base this on the EXISTING code in satellite.py.** Do NOT rewrite the Sentinel Hub API call from scratch. Copy the existing request pattern and adapt it for the date parameter. The Sentinel Hub authentication, URL construction, evalscript, and response parsing should all be taken from the working code.

### Step 6: Handle Mock mode for historical data

If the system is in Mock mode (no Sentinel Hub credentials), the existing service generates fake data. Make `fetch_ndvi_for_field_date` also work in Mock mode by generating plausible historical values:

```python
def _mock_historical_ndvi(self, field, target_date):
    """Generate realistic mock NDVI for historical dates."""
    import random
    import math
    
    # Simulate seasonal crop growth pattern
    day_of_year = target_date.timetuple().tm_yday
    
    # Determine crop type from field name
    name_lower = (field.name or "").lower()
    
    if "пшеница" in name_lower or "галла" in name_lower:
        # Winter wheat: planted Oct, grows Nov-May, harvest Jun
        # Peak NDVI in Apr-May (~0.7), low after harvest Jun+ (~0.15)
        if day_of_year < 60:      # Jan-Feb: dormant
            base = 0.25 + random.uniform(-0.05, 0.05)
        elif day_of_year < 120:   # Mar-Apr: active growth
            base = 0.25 + (day_of_year - 60) * 0.007 + random.uniform(-0.05, 0.05)
        elif day_of_year < 152:   # May: peak
            base = 0.65 + random.uniform(-0.08, 0.08)
        else:                      # Jun+: harvest/senescence
            base = 0.65 - (day_of_year - 152) * 0.02 + random.uniform(-0.05, 0.05)
    
    elif "пахта" in name_lower or "хлопок" in name_lower:
        # Cotton: planted Apr, grows May-Sep, harvest Oct
        if day_of_year < 100:     # Jan-Apr: bare soil
            base = 0.10 + random.uniform(-0.03, 0.05)
        elif day_of_year < 140:   # May: emergence
            base = 0.10 + (day_of_year - 100) * 0.005 + random.uniform(-0.03, 0.03)
        elif day_of_year < 200:   # Jun-Jul: vegetative growth
            base = 0.30 + (day_of_year - 140) * 0.005 + random.uniform(-0.05, 0.05)
        else:                      # Aug+: peak/boll development
            base = 0.55 + random.uniform(-0.08, 0.08)
    
    else:
        # Generic crop
        base = 0.30 + 0.15 * math.sin((day_of_year - 90) * math.pi / 180) + random.uniform(-0.05, 0.05)
    
    # Clamp to valid range
    mean_ndvi = max(0.05, min(0.85, base))
    
    return {
        "mean_ndvi": round(mean_ndvi, 4),
        "min_ndvi": round(max(0.01, mean_ndvi - random.uniform(0.05, 0.15)), 4),
        "max_ndvi": round(min(0.95, mean_ndvi + random.uniform(0.05, 0.15)), 4),
        "std_ndvi": round(random.uniform(0.02, 0.08), 4),
        "cloud_cover_pct": round(random.uniform(0, 20), 1),
    }
```

---

## Step 7: Run the backfill

### First: dry run to see the plan
```bash
cd backend
python scripts/backfill_ndvi_history.py --dry-run
```

### Then: test with a single field
```bash
python scripts/backfill_ndvi_history.py --field_id 1
```

### Then: run by enterprise (one at a time to monitor)
```bash
python scripts/backfill_ndvi_history.py --enterprise_id 7 --batch 5
```

Wait for it to finish, then:
```bash
python scripts/backfill_ndvi_history.py --enterprise_id 9 --batch 5
```

### Verify results
```bash
python -c "
from database import engine
from sqlalchemy import text
with engine.connect() as conn:
    result = conn.execute(text('''
        SELECT 
            MIN(captured_date) as earliest,
            MAX(captured_date) as latest,
            COUNT(*) as total,
            COUNT(DISTINCT field_id) as fields_with_data,
            ROUND(AVG(mean_ndvi)::numeric, 4) as avg_ndvi
        FROM ndvi_records
        WHERE mean_ndvi >= 0
    '''))
    row = result.fetchone()
    print(f'Date range: {row[0]} to {row[1]}')
    print(f'Total valid records: {row[2]}')
    print(f'Fields with data: {row[3]}')
    print(f'Average NDVI: {row[4]}')
"
```

---

## Verification Checklist

### Quality Gate
- [ ] `validate_ndvi_quality()` function exists in satellite.py
- [ ] Quality gate is called BEFORE saving any NDVIRecord to the database
- [ ] Negative NDVI records are rejected with a log message
- [ ] NDVI > 1.0 records are rejected
- [ ] Cloud cover > 30% records are rejected
- [ ] Mixed pixel detection works (min < -0.3 AND max > 0.4)
- [ ] cleanup_bad_ndvi.py runs successfully and removes existing bad records

### Historical Backfill
- [ ] `fetch_ndvi_for_field_date()` method exists in satellite.py
- [ ] Method works in both real (Sentinel Hub) and Mock mode
- [ ] Duplicate dates are skipped (no double records)
- [ ] `ndvi_change_pct` is calculated relative to previous record
- [ ] backfill_ndvi_history.py runs with --dry-run showing the plan
- [ ] backfill_ndvi_history.py runs successfully for at least one field
- [ ] After backfill, NDVI chart in the panel shows data going back to January

### Data Integrity
- [ ] No negative NDVI values remain in ndvi_records table
- [ ] No records with cloud_cover_pct > 30 remain
- [ ] Total record count increased significantly (should be 5000+ after backfill)
- [ ] Build still passes: `cd frontend && npm run build`

---

## CRITICAL WARNINGS

1. **DO NOT rewrite satellite.py from scratch.** Build on the existing working code. Copy the Sentinel Hub API call pattern from the existing `fetch_ndvi_for_field` method.
2. **Check the actual column names** in ndvi_records table: it uses `ndvi_change_pct` (not `change_pct`), `captured_date` (not `date`). Verify with `inspect(NDVIRecord)` or check the model file.
3. **Rate limiting:** Sentinel Hub free tier allows ~30,000 PU/month. The backfill will use ~9,000 PU. Add 0.5s delay between requests and 5s between batches.
4. **Commit in batches:** Don't accumulate thousands of records in memory. Commit every 10 fields.
5. **Mock mode:** If SENTINEL_HUB_CLIENT_ID is not set in .env, the system runs in Mock mode. The backfill script must handle both modes.
6. **Don't create alerts during backfill.** The quality gate should prevent bad alerts, but make sure the backfill script doesn't trigger the alert engine. Save records directly, don't call the full pipeline that includes alert checking.
