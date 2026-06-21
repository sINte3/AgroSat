"""
Deactivate alerts where ABS(triggered_value) > 1.0
These are percentage values incorrectly stored as NDVI values.
NDVI is always between -1.0 and 1.0.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import SessionLocal

def cleanup():
    db = SessionLocal()
    try:
        # First, count how many invalid alerts exist
        count_result = db.execute(text("""
            SELECT COUNT(*) FROM alerts
            WHERE is_active = true
            AND ABS(triggered_value) > 1.0
        """)).scalar()
        print(f"Found {count_result} active alerts with |triggered_value| > 1.0")

        if count_result == 0:
            print("Nothing to clean up.")
            return

        # Show some examples before deactivating
        examples = db.execute(text("""
            SELECT id, alert_type, triggered_value, threshold_value
            FROM alerts
            WHERE is_active = true
            AND ABS(triggered_value) > 1.0
            LIMIT 5
        """)).fetchall()

        print("\nExamples of invalid alerts:")
        for row in examples:
            print(f"  ID={row[0]}, type={row[1]}, triggered={row[2]}, threshold={row[3]}")

        # Deactivate them
        result = db.execute(text("""
            UPDATE alerts
            SET is_active = false
            WHERE is_active = true
            AND ABS(triggered_value) > 1.0
        """))
        db.commit()

        print(f"\nDeactivated {result.rowcount} invalid alerts.")

        # Count remaining active alerts
        remaining = db.execute(text("""
            SELECT COUNT(*) FROM alerts WHERE is_active = true
        """)).scalar()
        print(f"Remaining active alerts: {remaining}")

    finally:
        db.close()

if __name__ == "__main__":
    cleanup()
