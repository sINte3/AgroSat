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

        bad_count = row[1] + row[2] + row[3]
        if bad_count == 0:
            print("No bad records found. Nothing to clean.")
            return

        # Detach alerts referencing bad NDVI records (FK constraint)
        conn.execute(text("""
            UPDATE alerts SET ndvi_record_id = NULL
            WHERE ndvi_record_id IN (
                SELECT id FROM ndvi_records
                WHERE mean_ndvi < 0 OR mean_ndvi > 1.0 OR cloud_cover_pct > 30
            )
        """))
        print(f"Detached alerts from {bad_count} bad NDVI records")

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
