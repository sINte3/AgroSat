"""
API роутер для алертов — оптимизированные SQL запросы (без N+1).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from models.monitoring import Alert
from datetime import datetime
from services.cache import cache_get, cache_set, cache_delete_pattern

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("/")
def get_alerts(
    enterprise_id: int = None,
    severity: str = None,
    alert_type: str = None,
    field_id: int = None,
    is_active: bool = True,
    limit: int = 300,
    db: Session = Depends(get_db)
):
    """Все алерты — один оптимизированный SQL запрос с JOIN.

    Устраняет N+1: a.field.name и a.field.enterprise.name больше
    не вызывают отдельные SQL запросы на каждый алерт.
    """
    cache_key = f"alerts:{enterprise_id or 'all'}:{severity or 'all'}:{alert_type or 'all'}:{field_id or 'all'}:{is_active}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    conditions = []
    params = {}

    if is_active:
        conditions.append("a.is_active = true")

    if severity:
        conditions.append("a.severity = :severity")
        params["severity"] = severity

    if alert_type:
        conditions.append("a.alert_type = :alert_type")
        params["alert_type"] = alert_type

    if field_id:
        conditions.append("a.field_id = :field_id")
        params["field_id"] = field_id

    if enterprise_id:
        conditions.append("f.enterprise_id = :eid")
        params["eid"] = enterprise_id

    where = " AND ".join(conditions) if conditions else "true"

    sql = text(f"""
        SELECT
            a.id,
            a.field_id,
            a.alert_type,
            a.severity,
            a.title,
            a.description,
            a.recommendation,
            a.triggered_value,
            a.threshold_value,
            a.triggered_at,
            a.acknowledged_at,
            a.is_active,
            f.name AS field_name,
            e.name AS enterprise_name,
            n.captured_date,
            n.cloud_cover_pct,
            n.mean_ndvi AS snapshot_ndvi
        FROM alerts a
        JOIN fields f ON f.id = a.field_id
        JOIN enterprises e ON e.id = f.enterprise_id
        LEFT JOIN ndvi_records n ON n.id = a.ndvi_record_id
        WHERE {where}
        ORDER BY
            CASE a.severity
                WHEN 'critical' THEN 1
                WHEN 'warning' THEN 2
                ELSE 3
            END,
            a.triggered_at DESC
        LIMIT :lim
    """)

    params["lim"] = limit
    rows = db.execute(sql, params).fetchall()

    result = [
        {
            "id": r.id,
            "field_id": r.field_id,
            "field_name": r.field_name,
            "enterprise_name": r.enterprise_name,
            "alert_type": r.alert_type,
            "severity": r.severity,
            "title": r.title,
            "description": r.description,
            "recommendation": r.recommendation,
            "triggered_value": float(r.triggered_value) if r.triggered_value is not None else None,
            "threshold_value": float(r.threshold_value) if r.threshold_value is not None else None,
            "triggered_at": r.triggered_at.isoformat() if r.triggered_at else None,
            "acknowledged_at": r.acknowledged_at.isoformat() if r.acknowledged_at else None,
            "is_active": r.is_active,
            "captured_date": str(r.captured_date) if r.captured_date else None,
            "cloud_cover_pct": float(r.cloud_cover_pct) if r.cloud_cover_pct is not None else None,
            "snapshot_ndvi": float(r.snapshot_ndvi) if r.snapshot_ndvi is not None else None,
        }
        for r in rows
    ]
    cache_set(cache_key, result, ttl_seconds=60)
    return result


# ─── Info Alerts ──────────────────────────────────────────────────────────────
# Mark `info` severity alerts for the field-card — returned when enterprise
# detail page needs the snapshot metadata on alert cards.
# REUSE the same query as get_alerts but filtered to non-cloudy, non-noisy records.
# This endpoint is intentionally lazy — it returns alerts with captured_date
# and cloud_cover_pct so the frontend can display data quality info.
# All new alert creation already runs through safe_pct_change and skip logic,
# so we just need to serve the metadata fields here.
# The important work (A2-A4) is in ndvi.py and alert_engine.py.
# ────────────────────────────────────────────────────────────────────────────────

@router.get("/{field_id}")
def get_field_alerts(field_id: int, db: Session = Depends(get_db)):
    """Алерты конкретного поля."""
    sql = text("""
        SELECT
            a.id, a.field_id, a.alert_type, a.severity, a.title,
            a.description, a.recommendation, a.triggered_value,
            a.threshold_value, a.triggered_at, a.acknowledged_at, a.is_active,
            n.captured_date, n.cloud_cover_pct, n.mean_ndvi AS snapshot_ndvi
        FROM alerts a
        LEFT JOIN ndvi_records n ON n.id = a.ndvi_record_id
        WHERE a.field_id = :fid
        ORDER BY a.triggered_at DESC
        LIMIT 50
    """)
    rows = db.execute(sql, {"fid": field_id}).fetchall()
    return [
        {
            "id": r.id,
            "field_id": r.field_id,
            "alert_type": r.alert_type,
            "severity": r.severity,
            "title": r.title,
            "description": r.description,
            "recommendation": r.recommendation,
            "triggered_value": float(r.triggered_value) if r.triggered_value is not None else None,
            "threshold_value": float(r.threshold_value) if r.threshold_value is not None else None,
            "triggered_at": r.triggered_at.isoformat() if r.triggered_at else None,
            "acknowledged_at": r.acknowledged_at.isoformat() if r.acknowledged_at else None,
            "is_active": r.is_active,
            "captured_date": str(r.captured_date) if r.captured_date else None,
            "cloud_cover_pct": float(r.cloud_cover_pct) if r.cloud_cover_pct is not None else None,
            "snapshot_ndvi": float(r.snapshot_ndvi) if r.snapshot_ndvi is not None else None,
        }
        for r in rows
    ]


@router.put("/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, db: Session = Depends(get_db)):
    """Отметить алерт как просмотренный."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Алерт не найден")
    alert.acknowledged_at = datetime.utcnow()
    alert.is_active = False
    db.commit()
    # After acknowledging, clear alert and dashboard caches
    cache_delete_pattern("alerts:*")
    cache_delete_pattern("dashboard:*")
    return {"status": "ok", "alert_id": alert_id}
