"""
API роутер для алертов — оптимизированные SQL запросы (без N+1).
Harden: auth/tenant isolation/cache/pagination per TASK_007.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from models.monitoring import User
from datetime import datetime
from services.cache import cache_get, cache_set, cache_delete_pattern
from api.auth import get_current_active_user
from api.dependencies import (
    require_enterprise_scope,
    get_authorized_field_row,
    normalize_role,
    is_global_role,
    is_tenant_role,
)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("/")
def get_alerts(
    enterprise_id: int = None,
    severity: str = None,
    alert_type: str = None,
    field_id: int = None,
    is_active: bool = True,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    effective_eid: int = Depends(require_enterprise_scope),
):
    """Все алерты — один оптимизированный SQL запрос с JOIN.
    Tenant-isolated: agronomist/viewer see only their own enterprise.
    """
    # Enforce enterprise scope
    resolved_eid = enterprise_id
    if is_tenant_role(normalize_role(current_user)):
        if enterprise_id is not None and enterprise_id != effective_eid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Доступ запрещён для данного предприятия"
            )
        resolved_eid = effective_eid

    # Validate severity
    if severity is not None and severity not in ("info", "warning", "critical"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="severity must be one of: info, warning, critical"
        )

    cache_key = (f"alerts:{resolved_eid or 'all'}:{severity or 'all'}:"
                 f"{alert_type or 'all'}:{field_id or 'all'}:{is_active}:{limit}:{offset}")
    cached = cache_get(cache_key)
    if cached:
        return cached

    conditions = []
    params = {}

    if is_active is not None:
        conditions.append("a.is_active = :is_active")
        params["is_active"] = is_active

    if severity:
        conditions.append("a.severity = :severity")
        params["severity"] = severity

    if alert_type:
        conditions.append("a.alert_type = :alert_type")
        params["alert_type"] = alert_type

    if field_id:
        conditions.append("a.field_id = :field_id")
        params["field_id"] = field_id

    if resolved_eid is not None:
        conditions.append("f.enterprise_id = :eid")
        params["eid"] = resolved_eid

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
            a.acknowledged_by_id,
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
        LIMIT :lim OFFSET :off
    """)

    params["lim"] = limit
    params["off"] = offset
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
            "acknowledged_by_id": r.acknowledged_by_id,
            "is_active": r.is_active,
            "captured_date": str(r.captured_date) if r.captured_date else None,
            "cloud_cover_pct": float(r.cloud_cover_pct) if r.cloud_cover_pct is not None else None,
            "snapshot_ndvi": float(r.snapshot_ndvi) if r.snapshot_ndvi is not None else None,
        }
        for r in rows
    ]
    cache_set(cache_key, result, ttl_seconds=60)
    return result


@router.get("/{field_id}")
def get_field_alerts(
    field_id: int,
    is_active: bool = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Алерты конкретного поля с авторизацией поля."""
    # Object-level field authorization
    field_row = get_authorized_field_row(field_id=field_id, db=db, current_user=current_user)

    cache_key = f"field_alerts:{field_id}:{is_active}:{limit}:{offset}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    conditions = ["a.field_id = :fid"]
    params = {"fid": field_id}

    if is_active is not None:
        conditions.append("a.is_active = :ia")
        params["ia"] = is_active

    where = " AND ".join(conditions)

    sql = text(f"""
        SELECT
            a.id, a.field_id, a.alert_type, a.severity, a.title,
            a.description, a.recommendation, a.triggered_value,
            a.threshold_value, a.triggered_at, a.acknowledged_at,
            a.acknowledged_by_id, a.is_active,
            n.captured_date, n.cloud_cover_pct, n.mean_ndvi AS snapshot_ndvi
        FROM alerts a
        LEFT JOIN ndvi_records n ON n.id = a.ndvi_record_id
        WHERE {where}
        ORDER BY a.triggered_at DESC
        LIMIT :lim OFFSET :off
    """)
    params["lim"] = limit
    params["off"] = offset
    rows = db.execute(sql, params).fetchall()

    result = [
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
            "acknowledged_by_id": r.acknowledged_by_id,
            "is_active": r.is_active,
            "captured_date": str(r.captured_date) if r.captured_date else None,
            "cloud_cover_pct": float(r.cloud_cover_pct) if r.cloud_cover_pct is not None else None,
            "snapshot_ndvi": float(r.snapshot_ndvi) if r.snapshot_ndvi is not None else None,
        }
        for r in rows
    ]
    cache_set(cache_key, result, ttl_seconds=60)
    return result


@router.put("/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Отметить алерт как просмотренный с проверкой прав доступа (RBAC)."""
    role = normalize_role(current_user)

    # Viewer write block
    if role == "viewer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Пользователи с ролью 'viewer' не имеют прав на выполнение этого действия"
        )

    # Single UPDATE ... FROM with tenant check
    if is_global_role(role):
        # Administrators may update any active alert.
        sql = text("""
            UPDATE alerts a
            SET is_active = false,
                acknowledged_at = :now,
                acknowledged_by_id = :uid
            FROM fields f
            WHERE a.id = :aid
              AND a.is_active = true
              AND f.id = a.field_id
            RETURNING a.id, a.field_id
        """)
        params = {"aid": alert_id, "now": datetime.utcnow(), "uid": current_user.id}
    elif is_tenant_role(role):
        # Tenant roles may update alerts only inside their enterprise.
        eid = current_user.enterprise_id
        if eid is None:
            raise HTTPException(status_code=403, detail="User has no enterprise_id")
        sql = text("""
            UPDATE alerts a
            SET is_active = false,
                acknowledged_at = :now,
                acknowledged_by_id = :uid
            FROM fields f
            WHERE a.id = :aid
              AND a.is_active = true
              AND f.id = a.field_id
              AND f.enterprise_id = :eid
            RETURNING a.id, a.field_id
        """)
        params = {"aid": alert_id, "now": datetime.utcnow(), "uid": current_user.id, "eid": eid}
    else:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    row = db.execute(sql, params).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Алерт не найден")
    db.commit()

    # Invalidate caches
    cache_delete_pattern("alerts:*")
    cache_delete_pattern("dashboard:*")
    cache_delete_pattern("map:*")

    return {"status": "ok", "alert_id": alert_id}
