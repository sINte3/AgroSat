"""
API для генерации PDF отчётов.

Эндпоинт:
    GET /api/reports/enterprise/{enterprise_id}/pdf — скачать PDF отчёт

Положить в: backend/api/reports.py
"""

import logging
import io
import urllib.parse
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from services.pdf_report import generate_enterprise_pdf
from models.monitoring import User
from api.auth import get_current_active_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/enterprise/{enterprise_id}/pdf")
def download_enterprise_pdf(
    enterprise_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Сгенерировать и вернуть PDF отчёт по предприятию с проверкой прав доступа."""

    if enterprise_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="enterprise_id must be positive",
        )

    role = str(current_user.role or "").lower()

    if role not in {"admin", "manager", "agronomist", "viewer"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для скачивания PDF-отчёта",
        )

    if role in {"agronomist", "viewer"}:
        if current_user.enterprise_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="У пользователя не указано предприятие",
            )

        if current_user.enterprise_id != enterprise_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Вы можете скачивать отчёты только для своего предприятия",
            )

    ent_row = db.execute(text("""
        SELECT id, name, code, region FROM enterprises WHERE id = :eid
    """), {"eid": enterprise_id}).fetchone()
    if not ent_row:
        raise HTTPException(status_code=404, detail="Enterprise not found")

    enterprise = {"name": ent_row.name, "code": ent_row.code, "region": ent_row.region}

    field_rows = db.execute(text("""
        SELECT
            f.id, f.name, f.code, f.area_ha,
            ct.name_ru as current_crop,
            latest.mean_ndvi as current_ndvi
        FROM fields f
        LEFT JOIN crop_seasons cs ON cs.field_id = f.id AND cs.season_year = 2026
        LEFT JOIN crop_types ct ON ct.id = cs.crop_type_id
        LEFT JOIN LATERAL (
            SELECT mean_ndvi
            FROM ndvi_records nr
            WHERE nr.field_id = f.id
            ORDER BY nr.captured_date DESC
            LIMIT 1
        ) latest ON true
        WHERE f.enterprise_id = :eid AND f.is_active = true
        ORDER BY f.name
    """), {"eid": enterprise_id}).fetchall()

    fields = [
        {
            "name": r.name, "code": r.code, "area_ha": r.area_ha,
            "current_crop": r.current_crop,
            "current_ndvi": float(r.current_ndvi) if r.current_ndvi is not None else None,
        }
        for r in field_rows
    ]

    alert_rows = db.execute(text("""
        SELECT a.title, a.severity, a.description, a.recommendation,
               f.name as field_name
        FROM alerts a
        JOIN fields f ON f.id = a.field_id
        WHERE f.enterprise_id = :eid AND a.is_active = true AND a.severity = 'critical'
        ORDER BY a.triggered_at DESC
    """), {"eid": enterprise_id}).fetchall()

    alerts = [
        {
            "title": r.title, "severity": r.severity,
            "description": r.description, "recommendation": r.recommendation,
            "field_name": r.field_name,
        }
        for r in alert_rows
    ]

    ndvis = [f["current_ndvi"] for f in fields if f["current_ndvi"] is not None]
    areas = [f["area_ha"] for f in fields if f["area_ha"] is not None]
    kpi = {
        "total_fields": len(fields),
        "avg_ndvi": (sum(ndvis) / len(ndvis)) if ndvis else None,
        "critical_count": len(alerts),
        "normal_count": sum(1 for v in ndvis if v >= 0.35),
        "total_area": sum(areas) if areas else None,
    }

    try:
        pdf_bytes = generate_enterprise_pdf(enterprise, fields, alerts, kpi)
    except Exception as e:
        logger.error(f"PDF generation failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка генерации PDF: {str(e)[:200]}")

    safe_name = enterprise["name"].replace(" ", "_")
    filename = f"AgroSat_{safe_name}.pdf"
    encoded = urllib.parse.quote(filename)

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"}
    )
