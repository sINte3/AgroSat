"""
Скрипт обновления центроидов полей.
Запуск: cd C:\AgroSat\backend && python scripts/fix_centroids.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

try:
    # Обновить центроиды для полей где они не заданы
    result = db.execute(text("""
        UPDATE fields
        SET centroid_lat = ST_Y(ST_Centroid(geometry)),
            centroid_lon = ST_X(ST_Centroid(geometry))
        WHERE centroid_lat IS NULL
          AND geometry IS NOT NULL
    """))
    updated = result.rowcount
    db.commit()
    print(f"Обновлено полей без центроидов: {updated}")

    # Проверить сколько полей все еще без центроидов
    count = db.execute(text(
        "SELECT COUNT(*) FROM fields WHERE centroid_lat IS NULL"
    )).scalar()
    print(f"Полей без центроидов осталось: {count}")

    # Показать общее количество
    total = db.execute(text("SELECT COUNT(*) FROM fields")).scalar()
    print(f"Всего полей в базе: {total}")

except Exception as e:
    print(f"Ошибка: {e}")
    db.rollback()
finally:
    db.close()
