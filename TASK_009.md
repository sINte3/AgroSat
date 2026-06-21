# TASK_009: Fix Backend API — NDVI History & Enterprises

## Problem

Two API endpoints are returning errors:
1. `GET /api/ndvi/{field_id}/history` — returns error instead of empty list when field has no NDVI data
2. `GET /api/enterprises/` — returns error (router probably not included in main.py)

---

## STEP 1: Read and print current main.py

First, read `backend/main.py` and show its contents in full so we can see which routers are currently included.

---

## STEP 2: Fix /api/ndvi/{field_id}/history

### File: `backend/api/ndvi.py`

Read the current file. Then replace the entire file with this corrected version:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ndvi", tags=["ndvi"])


@router.get("/{field_id}/history")
async def get_ndvi_history(field_id: int, days: int = 90, db: Session = Depends(get_db)):
    """История NDVI по полю за N дней. Возвращает пустой список если данных нет."""
    # Проверяем что поле существует
    field_row = db.execute(
        text("SELECT id, name FROM fields WHERE id = :id"),
        {"id": field_id}
    ).fetchone()
    if not field_row:
        raise HTTPException(status_code=404, detail=f"Поле {field_id} не найдено")

    # Дата начала периода
    since_date = (datetime.now() - timedelta(days=days)).date()

    records = db.execute(text("""
        SELECT
            id,
            captured_date,
            mean_ndvi,
            min_ndvi,
            max_ndvi,
            std_ndvi,
            change_pct,
            satellite,
            cloud_cover_pct
        FROM ndvi_records
        WHERE field_id = :field_id
          AND captured_date >= :since
        ORDER BY captured_date ASC
    """), {"field_id": field_id, "since": since_date}).fetchall()

    def safe_float(v):
        return round(float(v), 4) if v is not None else None

    return {
        "field_id": field_id,
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
                "change_pct": safe_float(r.change_pct),
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
    row = db.execute(text("""
        SELECT id, captured_date, mean_ndvi, min_ndvi, max_ndvi,
               change_pct, satellite, cloud_cover_pct
        FROM ndvi_records
        WHERE field_id = :field_id
        ORDER BY captured_date DESC
        LIMIT 1
    """), {"field_id": field_id}).fetchone()

    if not row:
        return {"field_id": field_id, "record": None}

    return {
        "field_id": field_id,
        "record": {
            "id": row.id,
            "captured_date": str(row.captured_date),
            "mean_ndvi": float(row.mean_ndvi) if row.mean_ndvi is not None else None,
            "min_ndvi": float(row.min_ndvi) if row.min_ndvi is not None else None,
            "max_ndvi": float(row.max_ndvi) if row.max_ndvi is not None else None,
            "change_pct": float(row.change_pct) if row.change_pct is not None else None,
            "satellite": row.satellite,
            "cloud_cover_pct": float(row.cloud_cover_pct) if row.cloud_cover_pct is not None else None,
        }
    }


@router.post("/{field_id}/refresh")
async def refresh_ndvi(field_id: int, db: Session = Depends(get_db)):
    """Принудительно обновить NDVI для поля сейчас."""
    field_row = db.execute(
        text("SELECT id, name FROM fields WHERE id = :id"),
        {"id": field_id}
    ).fetchone()
    if not field_row:
        raise HTTPException(status_code=404, detail=f"Поле {field_id} не найдено")

    try:
        from services.satellite import SatelliteService
        from services.alert_engine import AlertEngine
        satellite = SatelliteService()
        alert_engine = AlertEngine(db)

        record = await satellite.fetch_ndvi_for_field(field_id, db)
        if record:
            await alert_engine.check_field(field_id)
            return {"status": "ok", "message": f"NDVI обновлён: {record.mean_ndvi:.4f}", "ndvi": float(record.mean_ndvi)}
        else:
            return {"status": "no_data", "message": "Спутниковые данные недоступны (облачность или mock режим)"}
    except Exception as e:
        logger.error(f"Ошибка обновления NDVI для поля {field_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

---

## STEP 3: Fix /api/enterprises/

### File: `backend/api/enterprises.py`

Read the current file (if it exists). Then replace it entirely with:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/enterprises", tags=["enterprises"])


@router.get("/")
async def list_enterprises(db: Session = Depends(get_db)):
    """Список всех предприятий с количеством полей."""
    rows = db.execute(text("""
        SELECT
            e.id,
            e.name,
            e.code,
            e.region,
            COUNT(f.id) AS total_fields
        FROM enterprises e
        LEFT JOIN fields f ON f.enterprise_id = e.id
        GROUP BY e.id, e.name, e.code, e.region
        ORDER BY e.name
    """)).fetchall()

    return [
        {
            "id": r.id,
            "name": r.name,
            "code": r.code,
            "region": r.region,
            "total_fields": r.total_fields,
        }
        for r in rows
    ]


@router.get("/{enterprise_id}")
async def get_enterprise(enterprise_id: int, db: Session = Depends(get_db)):
    """Предприятие + список его полей с последним NDVI."""
    row = db.execute(
        text("SELECT id, name, code, region FROM enterprises WHERE id = :id"),
        {"id": enterprise_id}
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Предприятие {enterprise_id} не найдено")

    fields = db.execute(text("""
        SELECT
            f.id,
            f.name,
            f.code,
            f.area_ha,
            n.mean_ndvi  AS current_ndvi,
            n.captured_date AS last_ndvi_date
        FROM fields f
        LEFT JOIN LATERAL (
            SELECT mean_ndvi, captured_date
            FROM ndvi_records
            WHERE field_id = f.id
            ORDER BY captured_date DESC
            LIMIT 1
        ) n ON true
        WHERE f.enterprise_id = :eid
        ORDER BY f.name
    """), {"eid": enterprise_id}).fetchall()

    return {
        "id": row.id,
        "name": row.name,
        "code": row.code,
        "region": row.region,
        "total_fields": len(fields),
        "fields": [
            {
                "id": f.id,
                "name": f.name,
                "code": f.code,
                "area_ha": float(f.area_ha) if f.area_ha else None,
                "current_ndvi": float(f.current_ndvi) if f.current_ndvi is not None else None,
                "last_ndvi_date": str(f.last_ndvi_date) if f.last_ndvi_date else None,
            }
            for f in fields
        ],
    }
```

---

## STEP 4: Fix main.py — include missing routers

Read `backend/main.py`. 

Find the section where routers are included with `app.include_router(...)`.

Add any missing routers. The final list of included routers must contain ALL of these:

```python
from api.enterprises import router as enterprises_router
from api.fields import router as fields_router
from api.ndvi import router as ndvi_router
from api.alerts import router as alerts_router
from api.dashboard import router as dashboard_router
from api.weather import router as weather_router
```

And all must be included:
```python
app.include_router(enterprises_router)
app.include_router(fields_router)
app.include_router(ndvi_router)
app.include_router(alerts_router)
app.include_router(dashboard_router)
app.include_router(weather_router)
```

Only add what is MISSING — do not duplicate existing lines.

---

## STEP 5: Fix frontend NDVIChart error handling

### File: `frontend/src/components/Field/NDVIChart.jsx`

The current error handler treats ALL errors (including 404 = "no data") as failures.
Fix it so 404 shows the empty state instead of an error:

Find this code block:
```javascript
.catch(err => {
    console.error('Ошибка загрузки NDVI:', err);
    setError('Не удалось загрузить историю NDVI');
})
```

Replace with:
```javascript
.catch(err => {
    const status = err.response?.status;
    if (status === 404 || status === 422) {
        // Поле не найдено или нет данных — показываем пустое состояние
        setData([]);
    } else {
        console.error('Ошибка загрузки NDVI:', err);
        setError('Не удалось загрузить историю NDVI');
    }
})
```

---

## STEP 6: Fix frontend WeatherWidget error handling

### File: `frontend/src/components/Field/WeatherWidget.jsx`

Same fix for weather:

Find:
```javascript
.catch(err => {
    console.error('Ошибка загрузки погоды:', err);
    setError('Не удалось загрузить данные погоды');
})
```

Replace with:
```javascript
.catch(err => {
    const status = err.response?.status;
    if (status === 404 || status === 422) {
        setError('Координаты поля не заданы — погода недоступна');
    } else {
        console.error('Ошибка загрузки погоды:', err);
        setError('Не удалось загрузить данные погоды');
    }
})
```

---

## STEP 7: Quick test after restart

After saving all files, the uvicorn server will hot-reload automatically.

Test these URLs in the browser (replace 8677 with any actual field_id you see in the app):

1. http://localhost:8000/api/enterprises/
   → Must return a JSON array of enterprises (not an error)

2. http://localhost:8000/api/ndvi/8677/history?days=90
   → Must return `{"records": [], "count": 0, ...}` — NOT a 404 or 500

3. http://localhost:8000/api/docs
   → The Swagger UI must show all 6 routers

Print the result of curl or fetch for URLs 1 and 2 so we can verify.

---

## Notes

- Do NOT change any model files, scheduler, or seed scripts
- The `LATERAL` JOIN in enterprises.py works in PostgreSQL 12+ (Supabase uses PG 15) ✓
- If `enterprises` table has a different column name for region (e.g., `district` or `oblast`), adjust the query to use the correct column name — read `backend/models/enterprise.py` first to confirm column names
- All error log messages can be in Russian or English
