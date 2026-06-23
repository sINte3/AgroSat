"""
Dashboard API — aggregate metrics with tenant isolation.
Harden: auth/tenant isolation/explicit SQL CTEs per TASK_007.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from models.monitoring import User
from services.cache import cache_get, cache_set, cache_delete_pattern
from api.auth import get_current_active_user
from api.dependencies import (
    require_enterprise_scope,
    normalize_role,
    is_tenant_role,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def get_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    effective_eid: int = Depends(require_enterprise_scope),
):
    """Сводка по кластеру или предприятию для главного дашборда.
    Admin/manager: cluster-wide. Agronomist/viewer: their own enterprise.
    """
    cache_key = f"dashboard:summary:{effective_eid or 'all'}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    if effective_eid is not None:
        # Tenant view — one CTE query
        sql = text("""
            WITH field_stats AS (
                SELECT
                    f.id,
                    f.area_ha,
                    (SELECT ndvi.mean_ndvi
                     FROM ndvi_records ndvi
                     WHERE ndvi.field_id = f.id
                     ORDER BY ndvi.captured_date DESC
                     LIMIT 1) AS last_ndvi,
                    f.enterprise_id
                FROM fields f
                WHERE f.is_active = true AND f.enterprise_id = :eid
            ),
            alert_counts AS (
                SELECT
                    f.enterprise_id,
                    COUNT(*) FILTER (WHERE a.severity = 'critical') AS critical_count,
                    COUNT(*) FILTER (WHERE a.severity = 'warning') AS warning_count,
                    COUNT(*) AS active_total
                FROM alerts a
                JOIN fields f ON f.id = a.field_id
                WHERE a.is_active = true AND f.enterprise_id = :eid
                GROUP BY f.enterprise_id
            )
            SELECT
                COALESCE((SELECT COUNT(*) FROM field_stats), 0) AS total_fields,
                COALESCE(SUM(fs.area_ha), 0) AS total_area_ha,
                AVG(fs.last_ndvi) AS avg_ndvi,
                COALESCE((SELECT active_total FROM alert_counts), 0) AS active_alerts_count,
                COALESCE((SELECT critical_count FROM alert_counts), 0) AS critical_alerts_count,
                COALESCE((SELECT warning_count FROM alert_counts), 0) AS warning_alerts_count,
                COALESCE((SELECT COUNT(*) FROM field_stats WHERE last_ndvi IS NULL), 0) AS no_data_fields_count
            FROM field_stats fs
        """)
        row = db.execute(sql, {"eid": effective_eid}).fetchone()
    else:
        # Global view
        sql = text("""
            WITH field_stats AS (
                SELECT
                    f.id,
                    f.area_ha,
                    (SELECT ndvi.mean_ndvi
                     FROM ndvi_records ndvi
                     WHERE ndvi.field_id = f.id
                     ORDER BY ndvi.captured_date DESC
                     LIMIT 1) AS last_ndvi
                FROM fields f
                WHERE f.is_active = true
            ),
            alert_counts AS (
                SELECT
                    COUNT(*) FILTER (WHERE severity = 'critical') AS critical_count,
                    COUNT(*) FILTER (WHERE severity = 'warning') AS warning_count,
                    COUNT(*) AS active_total
                FROM alerts
                WHERE is_active = true
            )
            SELECT
                COALESCE((SELECT COUNT(*) FROM field_stats), 0) AS total_fields,
                COALESCE(SUM(fs.area_ha), 0) AS total_area_ha,
                AVG(fs.last_ndvi) AS avg_ndvi,
                COALESCE((SELECT active_total FROM alert_counts), 0) AS active_alerts_count,
                COALESCE((SELECT critical_count FROM alert_counts), 0) AS critical_alerts_count,
                COALESCE((SELECT warning_count FROM alert_counts), 0) AS warning_alerts_count,
                COALESCE((SELECT COUNT(*) FROM field_stats WHERE last_ndvi IS NULL), 0) AS no_data_fields_count
            FROM field_stats fs
        """)
        row = db.execute(sql).fetchone()

    result = {
        "total_fields": row.total_fields,
        "total_area_ha": round(float(row.total_area_ha), 1) if row.total_area_ha else 0,
        "avg_ndvi": round(float(row.avg_ndvi), 4) if row.avg_ndvi is not None else None,
        "active_alerts": row.active_alerts_count,
        "critical_alerts": row.critical_alerts_count,
        "warning_alerts": row.warning_alerts_count,
        "fields_no_data": row.no_data_fields_count,
        "last_updated": __import__("datetime").date.today().isoformat(),
    }
    cache_set(cache_key, result, ttl_seconds=60)
    return result
