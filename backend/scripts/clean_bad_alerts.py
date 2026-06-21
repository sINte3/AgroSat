"""
Удаляет (помечает is_active=false) алерты, основанные на ненадёжных данных:
  - снимки с облачностью > 30%
  - NDVI < -0.5 (шум сенсора / вода)
  - проценты падения, рассчитанные от очень низкой базы

Запуск: python scripts/clean_bad_alerts.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

# 1. Деактивировать алерты по облачным снимкам
r1 = db.execute(text("""
    UPDATE alerts a
    SET is_active = false
    FROM ndvi_records n
    WHERE a.ndvi_record_id = n.id
      AND a.is_active = true
      AND n.cloud_cover_pct > 30
"""))

# 2. Деактивировать алерты по шумным снимкам (NDVI < -0.5)
r2 = db.execute(text("""
    UPDATE alerts a
    SET is_active = false
    FROM ndvi_records n
    WHERE a.ndvi_record_id = n.id
      AND a.is_active = true
      AND n.mean_ndvi < -0.5
"""))

# 3. Деактивировать алерты с нереалистичным процентом (< -200% или > 500%)
r3 = db.execute(text("""
    UPDATE alerts
    SET is_active = false
    WHERE is_active = true
      AND triggered_value IS NOT NULL
      AND threshold_value IS NOT NULL
      AND ABS(triggered_value) < 0.05
"""))

db.commit()

# Подсчёт оставшихся
remaining = db.execute(text("SELECT COUNT(*) FROM alerts WHERE is_active = true")).scalar()
print(f"Ok. Active alerts remaining: {remaining}")
db.close()
