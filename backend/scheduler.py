"""
Планировщик фоновых задач: автоматический запрос NDVI.
Использует APScheduler — запускать только через CLI runner.
"""

import logging
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import settings

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def fetch_all_fields_ndvi():
    """
    Обход всех активных полей и обновление NDVI.
    Запускается автоматически каждые N часов.
    """
    from database import SessionLocal
    from models.field import Field, CropSeason
    from models.monitoring import NDVIRecord
    from services.satellite import satellite_service, validate_ndvi_quality
    from services.alert_engine import analyze_field_ndvi, save_alerts
    from sqlalchemy.orm import selectinload

    db = SessionLocal()
    try:
        fields = (
            db.query(Field)
            .options(selectinload(Field.seasons).selectinload(CropSeason.crop_type))
            .filter(Field.is_active == True)
            .all()
        )
        logger.info(f"📡 Начинаем обновление NDVI для {len(fields)} полей...")

        updated = 0
        errors = 0

        for field in fields:
            try:
                # Получаем геометрию поля в WKT
                from sqlalchemy import func, text as sql_text
                result = db.execute(
                    sql_text("SELECT ST_AsText(geometry) FROM fields WHERE id = :id"),
                    {"id": field.id}
                ).fetchone()

                if not result:
                    continue

                geometry_wkt = result[0]

                # Запрашиваем NDVI за последние 10 дней
                today = date.today()
                ndvi_data = satellite_service.get_ndvi_stats(
                    geometry_wkt=geometry_wkt,
                    date_from=today - timedelta(days=10),
                    date_to=today,
                )

                if not ndvi_data:
                    logger.warning(f"Поле {field.id} ({field.name}): нет данных NDVI")
                    continue

                # Проверяем, нет ли уже записи за эту дату
                captured_date = date.fromisoformat(ndvi_data["captured_date"])
                existing = db.query(NDVIRecord).filter(
                    NDVIRecord.field_id == field.id,
                    NDVIRecord.captured_date == captured_date
                ).first()

                if existing:
                    logger.debug(f"Поле {field.name}: NDVI за {captured_date} уже есть")
                    continue

                # Вычисляем изменение относительно предыдущего снимка
                prev_record = db.query(NDVIRecord).filter(
                    NDVIRecord.field_id == field.id,
                ).order_by(NDVIRecord.captured_date.desc()).first()

                ndvi_change = None
                ndvi_change_pct = None

                if prev_record and prev_record.mean_ndvi:
                    ndvi_change = ndvi_data["mean_ndvi"] - prev_record.mean_ndvi
                    if prev_record.mean_ndvi != 0:
                        ndvi_change_pct = (ndvi_change / prev_record.mean_ndvi) * 100

                # Quality gate — reject bad data before saving
                is_valid, reason = validate_ndvi_quality(
                    mean_ndvi=ndvi_data["mean_ndvi"],
                    cloud_cover_pct=ndvi_data.get("cloud_cover_pct"),
                    min_ndvi=ndvi_data.get("min_ndvi"),
                    max_ndvi=ndvi_data.get("max_ndvi"),
                    field_name=field.name,
                )
                if not is_valid:
                    logger.info(f"Поле {field.id} ({field.name}): quality gate rejected — {reason}")
                    continue

                # Сохраняем новую запись
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

                # Анализируем и сохраняем алерты
                alerts_data = analyze_field_ndvi(field, record, db)
                if alerts_data:
                    saved = save_alerts(field, record, alerts_data, db)
                    if saved > 0:
                        logger.info(
                            f"🚨 Поле '{field.name}': создано {saved} алертов "
                            f"(NDVI={record.mean_ndvi:.3f})"
                        )

                updated += 1
                logger.info(
                    f"✅ Поле '{field.name}': NDVI={record.mean_ndvi:.3f} "
                    f"(изм. {ndvi_change_pct:+.1f}%)" if ndvi_change_pct is not None
                    else f"✅ Поле '{field.name}': NDVI={record.mean_ndvi:.3f}"
                )

            except Exception as e:
                logger.error(f"❌ Ошибка обработки поля {field.id} ({field.name}): {e}")
                errors += 1
                db.rollback()

        logger.info(f"📊 NDVI обновление завершено: {updated} обновлено, {errors} ошибок")

    finally:
        db.close()


def start_scheduler(immediate_run=True):
    """Запустить планировщик. Idempotent — безопасен для повторных вызовов."""
    if scheduler.running:
        logger.info("⏱️ Планировщик уже запущен, повторная регистрация пропущена")
        return

    interval_hours = settings.ndvi_fetch_interval_hours

    scheduler.add_job(
        fetch_all_fields_ndvi,
        trigger=IntervalTrigger(hours=interval_hours),
        id="fetch_ndvi",
        name="Обновление NDVI всех полей",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.start()
    logger.info(f"⏱️ Планировщик запущен. NDVI обновляется каждые {interval_hours} ч.")

    if immediate_run:
        from apscheduler.triggers.date import DateTrigger
        from datetime import datetime, timedelta
        scheduler.add_job(
            fetch_all_fields_ndvi,
            trigger=DateTrigger(run_date=datetime.now(scheduler.timezone) + timedelta(seconds=30)),
            id="fetch_ndvi_startup",
            name="Первоначальное обновление NDVI",
            max_instances=1,
        )


def stop_scheduler():
    """Остановить планировщик и дождаться завершения текущих задач."""
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("⏹️ Планировщик остановлен")
    else:
        logger.info("⏹️ Планировщик не был запущен")
