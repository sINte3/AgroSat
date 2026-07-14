"""Authenticated aggregate, read-only agronomic interpretation endpoint."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from api.dependencies import ALLOWED_ROLES, is_tenant_role, normalize_role
from database import get_db
from schemas.agronomic_interpretation import AgronomicInterpretationResponse
from services.agronomic_interpretation import (INDEX_CODES, TOP_LIMITATIONS, add_cross_index_hypotheses, build_index_interpretation, overall_confidence)

router = APIRouter(prefix="/api/agronomic-interpretation", tags=["agronomic_interpretation"])
TASHKENT = ZoneInfo("Asia/Tashkent")


def _field_context(db: Session, field_id: int, current_user, season_year: int):
    role = normalize_role(current_user)
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Unknown role")
    params = {"field_id": field_id, "season_year": season_year}
    tenant_clause = ""
    if is_tenant_role(role):
        if current_user.enterprise_id is None:
            raise HTTPException(status_code=403, detail="User has no enterprise_id")
        tenant_clause = " AND f.enterprise_id = :enterprise_id"
        params["enterprise_id"] = current_user.enterprise_id
    row = db.execute(text(f"""
        SELECT f.id, f.name, f.enterprise_id, cs.season_year, ct.name_ru AS crop_name
        FROM fields f
        LEFT JOIN crop_seasons cs ON cs.field_id = f.id AND cs.season_year = :season_year
        LEFT JOIN crop_types ct ON ct.id = cs.crop_type_id
        WHERE f.id = :field_id{tenant_clause}
    """), params).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Field not found")
    return row


@router.get("/fields/{field_id}", response_model=AgronomicInterpretationResponse)
def get_agronomic_interpretation(
    field_id: int,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    local_today = datetime.now(TASHKENT).date()
    resolved_to = date_to or local_today
    resolved_from = date_from or resolved_to - timedelta(days=180)
    if resolved_from > resolved_to:
        raise HTTPException(status_code=422, detail="date_from must be <= date_to")
    if (resolved_to - resolved_from).days > 730:
        raise HTTPException(status_code=422, detail="requested range must be <= 730 days")
    field = _field_context(db, field_id, current_user, resolved_to.year)
    ndvi_rows = db.execute(text("""
        SELECT id, captured_date, mean_ndvi AS value, satellite, cloud_cover_pct, valid_pixels_pct
        FROM ndvi_records
        WHERE field_id = :field_id AND captured_date >= :date_from AND captured_date <= :date_to
          AND mean_ndvi IS NOT NULL
        ORDER BY captured_date ASC, id ASC
    """), {"field_id": field_id, "date_from": resolved_from, "date_to": resolved_to}).fetchall()
    index_rows = db.execute(text("""
        SELECT id, captured_date, index_code, mean_value AS value, satellite, cloud_cover_pct, valid_pixels_pct
        FROM satellite_index_records
        WHERE field_id = :field_id AND captured_date >= :date_from AND captured_date <= :date_to
          AND index_code = ANY(:index_codes) AND mean_value IS NOT NULL
        ORDER BY captured_date ASC, id ASC
    """), {"field_id": field_id, "date_from": resolved_from, "date_to": resolved_to, "index_codes": list(INDEX_CODES[1:])}).fetchall()
    grouped = {code: [] for code in INDEX_CODES}
    grouped["ndvi"] = ndvi_rows
    for row in index_rows:
        if row.index_code in grouped:
            grouped[row.index_code].append(row)
    items = [build_index_interpretation(code, grouped[code], local_today) for code in INDEX_CODES]
    add_cross_index_hypotheses(items)
    return {"field": {"id": field.id, "name": field.name, "enterprise_id": field.enterprise_id, "crop_name": field.crop_name, "season_year": field.season_year}, "range": {"date_from": resolved_from, "date_to": resolved_to}, "generated_at": datetime.now(TASHKENT), "overall_confidence": overall_confidence(items), "indices": items, "limitations": TOP_LIMITATIONS}
