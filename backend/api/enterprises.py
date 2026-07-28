from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from datetime import date
import logging

from api.auth import get_current_active_user
from api.dependencies import normalize_role, is_global_role, is_tenant_role
from api.query_bounds import (
    ENTERPRISE_LIST_ROW_CAP,
    FIELD_LIST_ROW_CAP,
    ensure_within_row_cap,
    fetch_limit,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/enterprises", tags=["enterprises"])


@router.get("/")
async def list_enterprises(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Enterprise list with aggregated KPIs — one query, tenant-scoped."""
    role = normalize_role(current_user)

    if is_global_role(role):
        # admin/manager: see all enterprises
        where = ""
        params = {}
    elif is_tenant_role(role):
        # agronomist/viewer: see only their assigned enterprise
        if current_user.enterprise_id is None:
            raise HTTPException(status_code=403, detail="User has no enterprise_id")
        where = "WHERE e.id = :eid"
        params = {"eid": current_user.enterprise_id}
    else:
        raise HTTPException(status_code=403, detail="Unknown role")
    params["row_limit"] = fetch_limit(ENTERPRISE_LIST_ROW_CAP)

    rows = db.execute(text(f"""
        SELECT
            e.id,
            e.name,
            e.code,
            e.region,
            e.total_area_ha,
            COUNT(DISTINCT f.id) AS total_fields,
            ROUND(AVG(n.mean_ndvi)::numeric, 4) AS avg_ndvi,
            COUNT(DISTINCT a_crit.id) AS critical_alerts,
            COUNT(DISTINCT a_all.id) AS active_alerts,
            COUNT(DISTINCT CASE WHEN n.mean_ndvi < 0.3 THEN f.id END) AS fields_with_problems
        FROM enterprises e
        LEFT JOIN fields f ON f.enterprise_id = e.id AND f.is_active = true
        LEFT JOIN LATERAL (
            SELECT mean_ndvi, captured_date
            FROM ndvi_records
            WHERE field_id = f.id
            ORDER BY captured_date DESC
            LIMIT 1
        ) n ON true
        LEFT JOIN alerts a_crit ON a_crit.field_id = f.id
            AND a_crit.is_active = true AND a_crit.severity = 'critical'
        LEFT JOIN alerts a_all ON a_all.field_id = f.id
            AND a_all.is_active = true
        {where}
        GROUP BY e.id, e.name, e.code, e.region, e.total_area_ha
        ORDER BY e.name
        LIMIT :row_limit
    """), params).fetchall()
    ensure_within_row_cap(
        rows,
        row_cap=ENTERPRISE_LIST_ROW_CAP,
        resource="enterprises",
    )

    return [
        {
            "id": r.id,
            "name": r.name,
            "code": r.code,
            "region": r.region,
            "total_fields": r.total_fields,
            "total_area_ha": float(r.total_area_ha) if r.total_area_ha else None,
            "avg_ndvi": float(r.avg_ndvi) if r.avg_ndvi else None,
            "active_alerts": r.active_alerts,
            "critical_alerts": r.critical_alerts,
            "fields_with_problems": r.fields_with_problems,
        }
        for r in rows
    ]


@router.get("/{enterprise_id}")
async def get_enterprise(
    enterprise_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Enterprise detail + field list with last NDVI, crop, and alerts. Tenant-scoped."""
    role = normalize_role(current_user)
    params = {"id": enterprise_id}
    tenant_clause = ""
    if is_tenant_role(role):
        if current_user.enterprise_id is None:
            raise HTTPException(status_code=403, detail="User has no enterprise_id")
        tenant_clause = " AND id = :eid"
        params["eid"] = current_user.enterprise_id
    elif not is_global_role(role):
        raise HTTPException(status_code=403, detail="Unknown role")

    row = db.execute(
        text(
            "SELECT id, name, code, region, total_area_ha "
            f"FROM enterprises WHERE id = :id{tenant_clause}"
        ),
        params,
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Enterprise not found")

    fields = db.execute(text("""
        SELECT
            f.id,
            f.name,
            f.code,
            f.area_ha,
            f.irrigation_type,
            ct.name_ru AS crop_name,
            n.mean_ndvi  AS current_ndvi,
            n.captured_date AS last_ndvi_date,
            COALESCE(al.alert_count, 0) AS active_alerts,
            COALESCE(al.max_severity, 'ok') AS alert_severity
        FROM fields f
        LEFT JOIN crop_seasons cs ON cs.field_id = f.id
            AND cs.season_year = :year
        LEFT JOIN crop_types ct ON ct.id = cs.crop_type_id
        LEFT JOIN LATERAL (
            SELECT mean_ndvi, captured_date
            FROM ndvi_records
            WHERE field_id = f.id
            ORDER BY captured_date DESC
            LIMIT 1
        ) n ON true
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS alert_count,
                CASE MAX(CASE WHEN severity='critical' THEN 3
                              WHEN severity='warning' THEN 2
                              WHEN severity='info' THEN 1 ELSE 0 END)
                    WHEN 3 THEN 'critical' WHEN 2 THEN 'warning'
                    WHEN 1 THEN 'info' ELSE 'ok' END AS max_severity
            FROM alerts WHERE field_id = f.id AND is_active = true
        ) al ON true
        WHERE f.enterprise_id = :eid
        ORDER BY COALESCE(n.mean_ndvi, 999) ASC, f.name
        LIMIT :row_limit
    """), {
        "eid": enterprise_id,
        "year": date.today().year,
        "row_limit": fetch_limit(FIELD_LIST_ROW_CAP),
    }).fetchall()
    ensure_within_row_cap(
        fields,
        row_cap=FIELD_LIST_ROW_CAP,
        resource="enterprise_fields",
    )

    return {
        "id": row.id,
        "name": row.name,
        "code": row.code,
        "region": row.region,
        "total_area_ha": float(row.total_area_ha) if row.total_area_ha else None,
        "field_count": len(fields),
        "fields": [
            {
                "id": f.id,
                "name": f.name,
                "code": f.code,
                "area_ha": float(f.area_ha) if f.area_ha else None,
                "current_crop": f.crop_name,
                "current_ndvi": float(f.current_ndvi) if f.current_ndvi is not None else None,
                "last_ndvi_date": str(f.last_ndvi_date) if f.last_ndvi_date else None,
                "irrigation_type": f.irrigation_type,
                "active_alerts": f.active_alerts,
                "alert_severity": f.alert_severity,
            }
            for f in fields
        ],
    }
