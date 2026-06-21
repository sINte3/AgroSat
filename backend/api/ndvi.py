from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ndvi", tags=["ndvi"])


@router.get("/{field_id}/history")
async def get_ndvi_history(
    field_id: int,
    days: int = 90,
    include_cloudy: bool = False,
    db: Session = Depends(get_db),
):
    """История NDVI по полю за N дней. Возвращает пустой список если данных нет.
    По умолчанию исключает облачные снимки (cloud_cover_pct > 30)."""
    # Проверяем что поле существует (по id или code)
    field_row = db.execute(
        text("SELECT id, name FROM fields WHERE id = :fid OR code = CAST(:fid AS TEXT) LIMIT 1"),
        {"fid": field_id}
    ).fetchone()
    if not field_row:
        raise HTTPException(status_code=404, detail=f"Поле {field_id} не найдено")

    # Дата начала периода
    since_date = (datetime.now() - timedelta(days=days)).date()

    cloud_filter = "" if include_cloudy else "  AND (cloud_cover_pct IS NULL OR cloud_cover_pct <= 30)"

    records = db.execute(text(f"""
        SELECT
            id,
            captured_date,
            mean_ndvi,
            min_ndvi,
            max_ndvi,
            std_ndvi,
            ndvi_change,
            ndvi_change_pct,
            satellite,
            cloud_cover_pct
        FROM ndvi_records
        WHERE field_id = :fid
          AND captured_date >= :since
          {cloud_filter}
        ORDER BY captured_date ASC
    """), {"fid": field_row.id, "since": since_date}).fetchall()

    def safe_float(v):
        return round(float(v), 4) if v is not None else None

    return {
        "field_id": field_row.id,
        "field_name": field_row.name,
        "days": days,
        "records": [
            {
                "id": r.id,
                "captured_date": str(r.captured_date),
                "mean_ndvi": safe_float(r.mean_ndvi),
                "min_ndvi": safe_float(r.min_ndvi),
                "max_ndvi": safe_float(r.max_ndvi),
                "std_ndvi": safe_float(r.std_ndvi),
                "ndvi_change": safe_float(r.ndvi_change),
                "change_pct": safe_float(r.ndvi_change_pct),
                "satellite": r.satellite,
                "cloud_cover_pct": safe_float(r.cloud_cover_pct),
            }
            for r in records
        ],
        "count": len(records),
    }


@router.get("/{field_id}/latest")
async def get_ndvi_latest(field_id: int, db: Session = Depends(get_db)):
    """Последний NDVI снимок поля."""
    # Сначала находим поле по id или code
    frow = db.execute(
        text("SELECT id FROM fields WHERE id = :fid OR code = CAST(:fid AS TEXT) LIMIT 1"),
        {"fid": field_id}
    ).fetchone()
    if not frow:
        return {"field_id": field_id, "record": None}

    row = db.execute(text("""
        SELECT id, captured_date, mean_ndvi, min_ndvi, max_ndvi,
               ndvi_change_pct, satellite, cloud_cover_pct
        FROM ndvi_records
        WHERE field_id = :fid
        ORDER BY captured_date DESC
        LIMIT 1
    """), {"fid": frow.id}).fetchone()

    if not row:
        return {"field_id": frow.id, "record": None}

    return {
        "field_id": frow.id,
        "record": {
            "id": row.id,
            "captured_date": str(row.captured_date),
            "mean_ndvi": float(row.mean_ndvi) if row.mean_ndvi is not None else None,
            "min_ndvi": float(row.min_ndvi) if row.min_ndvi is not None else None,
            "max_ndvi": float(row.max_ndvi) if row.max_ndvi is not None else None,
            "change_pct": float(row.ndvi_change_pct) if row.ndvi_change_pct is not None else None,
            "satellite": row.satellite,
            "cloud_cover_pct": float(row.cloud_cover_pct) if row.cloud_cover_pct is not None else None,
        }
    }


@router.post("/{field_id}/refresh")
async def refresh_ndvi(field_id: int, db: Session = Depends(get_db)):
    """Принудительно обновить NDVI для поля сейчас."""
    field_row = db.execute(
        text("SELECT id, name FROM fields WHERE id = :fid OR code = CAST(:fid AS TEXT) LIMIT 1"),
        {"fid": field_id}
    ).fetchone()
    if not field_row:
        raise HTTPException(status_code=404, detail=f"Поле {field_id} не найдено")

    try:
        from services.satellite import SatelliteService
        from services.alert_engine import AlertEngine
        satellite = SatelliteService()
        alert_engine = AlertEngine(db)

        record = await satellite.fetch_ndvi_for_field(field_row.id, db)
        if record:
            await alert_engine.check_field(field_id)
            return {"status": "ok", "message": f"NDVI обновлён: {record.mean_ndvi:.4f}", "ndvi": float(record.mean_ndvi)}
        else:
            return {"status": "no_data", "message": "Спутниковые данные недоступны (облачность или mock режим)"}
    except Exception as e:
        logger.error(f"Ошибка обновления NDVI для поля {field_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
