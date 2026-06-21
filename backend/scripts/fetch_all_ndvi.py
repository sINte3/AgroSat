"""
AgroSat — Массовый запуск NDVI для всех полей.
Запуск: python fetch_all_ndvi.py

Скрипт последовательно запрашивает NDVI с Sentinel-2
для каждого активного поля в базе данных.
"""

import sys
import os
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, init_db
from models.field import Field
from models.monitoring import NDVIRecord, Alert
from sqlalchemy import text

# Задержка между запросами (секунды) — не перегружаем Sentinel Hub
DELAY_BETWEEN_REQUESTS = 3


def fetch_ndvi_for_all():
    from services.satellite import satellite_service, validate_ndvi_quality
    from services.alert_engine import analyze_field_ndvi, save_alerts

    init_db()
    db = SessionLocal()

    try:
        fields = db.query(Field).filter(Field.is_active == True).all()
        total = len(fields)
        print(f"\n🛰️  AgroSat — Запуск NDVI для {total} полей")
        print(f"    Sentinel Hub: {'реальные данные' if hasattr(satellite_service, 'client_id') else 'Mock режим'}")
        print(f"    Задержка: {DELAY_BETWEEN_REQUESTS} сек между запросами")
        print("=" * 60)

        updated = 0
        skipped = 0
        errors = 0
        today = date.today()

        for i, field in enumerate(fields, 1):
            prefix = f"[{i:3d}/{total}]"

            # Получаем геометрию
            geom = db.execute(
                text("SELECT ST_AsText(geometry) FROM fields WHERE id = :id"),
                {"id": field.id}
            ).fetchone()

            if not geom:
                print(f"{prefix} ❌ {field.name[:40]} — нет геометрии")
                errors += 1
                continue

            # Уже есть снимок сегодня?
            existing = db.query(NDVIRecord).filter(
                NDVIRecord.field_id == field.id,
                NDVIRecord.captured_date == today
            ).first()

            if existing:
                print(f"{prefix} ⏭️  {field.name[:40]} — NDVI сегодня уже есть ({existing.mean_ndvi:.4f})")
                skipped += 1
                continue

            # Запрашиваем NDVI
            try:
                ndvi_data = satellite_service.get_ndvi_stats(
                    geometry_wkt=geom[0],
                    date_from=today - timedelta(days=10),
                    date_to=today,
                )

                if not ndvi_data:
                    print(f"{prefix} ☁️  {field.name[:40]} — нет данных (облака?)")
                    errors += 1
                    continue

                # Считаем изменение
                prev = db.query(NDVIRecord).filter(
                    NDVIRecord.field_id == field.id
                ).order_by(NDVIRecord.captured_date.desc()).first()

                ndvi_change = None
                ndvi_change_pct = None
                if prev and prev.mean_ndvi:
                    ndvi_change = ndvi_data["mean_ndvi"] - prev.mean_ndvi
                    # ponytail: safe pct — skip when old is too close to zero (NDVI < 0.15)
                    if prev.mean_ndvi >= 0.15:
                        ndvi_change_pct = (ndvi_change / prev.mean_ndvi) * 100

                captured_date = date.fromisoformat(ndvi_data["captured_date"])

                # Quality gate — reject bad data before saving
                is_valid, reason = validate_ndvi_quality(
                    mean_ndvi=ndvi_data["mean_ndvi"],
                    cloud_cover_pct=ndvi_data.get("cloud_cover_pct"),
                    min_ndvi=ndvi_data.get("min_ndvi"),
                    max_ndvi=ndvi_data.get("max_ndvi"),
                    field_name=field.name,
                )
                if not is_valid:
                    print(f"{prefix} 🚫 {field.name[:40]} — quality gate: {reason}")
                    errors += 1
                    continue

                # Сохраняем
                record = NDVIRecord(
                    field_id=field.id,
                    captured_date=captured_date,
                    mean_ndvi=ndvi_data["mean_ndvi"],
                    min_ndvi=ndvi_data["min_ndvi"],
                    max_ndvi=ndvi_data["max_ndvi"],
                    std_ndvi=ndvi_data["std_ndvi"],
                    p10_ndvi=ndvi_data["p10_ndvi"],
                    p90_ndvi=ndvi_data["p90_ndvi"],
                    valid_pixels_pct=ndvi_data["valid_pixels_pct"],
                    satellite=ndvi_data["satellite"],
                    ndvi_change=ndvi_change,
                    ndvi_change_pct=ndvi_change_pct,
                )
                db.add(record)
                db.commit()
                db.refresh(record)

                # Анализируем алерты
                alerts_data = analyze_field_ndvi(field, record, db)
                alert_count = 0
                if alerts_data:
                    alert_count = save_alerts(field, record, alerts_data, db)

                change_str = ""
                if ndvi_change_pct is not None:
                    change_str = f" ({ndvi_change_pct:+.1f}%)"

                alert_str = f" 🚨x{alert_count}" if alert_count > 0 else ""

                print(
                    f"{prefix} ✅ {field.name[:35]:35s} "
                    f"NDVI={record.mean_ndvi:.4f}{change_str}"
                    f"{alert_str}"
                )
                updated += 1

            except Exception as e:
                db.rollback()
                print(f"{prefix} ❌ {field.name[:40]} — ошибка: {e}")
                errors += 1

            # Пауза между запросами
            if i < total:
                time.sleep(DELAY_BETWEEN_REQUESTS)

        print("\n" + "=" * 60)
        print(f"✅ Обновлено:  {updated} полей")
        print(f"⏭️  Пропущено:  {skipped} (данные уже есть)")
        print(f"❌ Ошибок:     {errors}")
        print(f"\nОбновите браузер и включите режим 📡 NDVI на карте!")

    finally:
        db.close()


if __name__ == "__main__":
    fetch_ndvi_for_all()
