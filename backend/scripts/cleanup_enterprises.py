"""
Delete unused enterprises and their related data.
Keep ONLY:
  - id=9  Бухара Сервис Агрокластер (BAK-08)
  - id=7  Гарден Бухоро Агрокластер (BAK-06)
Delete ALL others.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import SessionLocal

def cleanup():
    db = SessionLocal()
    keep_ids = [7, 9]

    try:
        # Show what will be deleted
        enterprises = db.execute(text("""
            SELECT e.id, e.name, e.code,
                   (SELECT COUNT(*) FROM fields f WHERE f.enterprise_id = e.id) as field_count
            FROM enterprises e
            WHERE e.id NOT IN :keep_ids
            ORDER BY e.id
        """), {"keep_ids": tuple(keep_ids)}).fetchall()

        print("Enterprises to DELETE:")
        for e in enterprises:
            print(f"  id={e[0]}, {e[1]} ({e[2]}), {e[3]} fields")

        if not enterprises:
            print("Nothing to delete.")
            return

        delete_ids = [e[0] for e in enterprises]

        # Get field IDs for these enterprises (needed for cascading deletes)
        field_ids_result = db.execute(text("""
            SELECT id FROM fields WHERE enterprise_id = ANY(:ids)
        """), {"ids": delete_ids}).fetchall()
        field_ids = [r[0] for r in field_ids_result]

        print(f"\nFields to delete: {len(field_ids)}")

        if field_ids:
            # Delete alerts for these fields
            r = db.execute(text("DELETE FROM alerts WHERE field_id = ANY(:ids)"), {"ids": field_ids})
            print(f"Deleted {r.rowcount} alerts")

            # Delete NDVI records for these fields
            r = db.execute(text("DELETE FROM ndvi_records WHERE field_id = ANY(:ids)"), {"ids": field_ids})
            print(f"Deleted {r.rowcount} NDVI records")

            # Delete crop seasons for these fields
            r = db.execute(text("DELETE FROM crop_seasons WHERE field_id = ANY(:ids)"), {"ids": field_ids})
            print(f"Deleted {r.rowcount} crop seasons")

            # Delete scouting notes if table exists
            try:
                r = db.execute(text("DELETE FROM scouting_notes WHERE field_id = ANY(:ids)"), {"ids": field_ids})
                print(f"Deleted {r.rowcount} scouting notes")
            except Exception:
                pass

            # Delete the fields
            r = db.execute(text("DELETE FROM fields WHERE enterprise_id = ANY(:ids)"), {"ids": delete_ids})
            print(f"Deleted {r.rowcount} fields")

        # Delete the enterprises
        r = db.execute(text("DELETE FROM enterprises WHERE id = ANY(:ids)"), {"ids": delete_ids})
        print(f"Deleted {r.rowcount} enterprises")

        db.commit()

        # Verify
        remaining = db.execute(text("SELECT id, name, code FROM enterprises ORDER BY id")).fetchall()
        print(f"\nRemaining enterprises ({len(remaining)}):")
        for e in remaining:
            print(f"  id={e[0]}, {e[1]} ({e[2]})")

    except Exception as ex:
        db.rollback()
        print(f"ERROR: {ex}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    cleanup()
