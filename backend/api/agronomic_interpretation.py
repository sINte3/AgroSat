"""Authenticated, read-only contextual agronomic interpretation endpoint."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from api.dependencies import ALLOWED_ROLES, is_tenant_role, normalize_role
from database import get_db
from schemas.agronomic_interpretation import AgronomicInterpretationResponse
from services.agronomic_interpretation import (HISTORY_RECORD_LIMIT, INDEX_CODES, TOP_LIMITATIONS, add_cross_index_hypotheses, build_index_interpretation, build_summary, overall_confidence)

router = APIRouter(prefix="/api/agronomic-interpretation", tags=["agronomic_interpretation"])
TASHKENT = ZoneInfo("Asia/Tashkent")


def _field_context(db: Session, field_id: int, current_user, season_year: int):
    """Authorized field and persisted crop/season context in one explicit join."""
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
        SELECT f.id, f.name, f.enterprise_id, f.soil_type,
               cs.season_year, ct.name_ru AS crop_name
        FROM fields f
        LEFT JOIN crop_seasons cs ON cs.id = (
            SELECT cs2.id FROM crop_seasons cs2
            WHERE cs2.field_id = f.id AND cs2.season_year <= :season_year
            ORDER BY cs2.season_year DESC, cs2.id DESC LIMIT 1
        )
        LEFT JOIN crop_types ct ON ct.id = cs.crop_type_id
        WHERE f.id = :field_id{tenant_clause}
    """), params).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Field not found")
    return row


def _context(field) -> dict:
    flags = {
        "crop_available": field.crop_name is not None,
        "season_available": field.season_year is not None,
        "growth_stage_available": False,
        "weather_available": False,
        "soil_available": bool(getattr(field, "soil_type", None)),
        "inspection_evidence_available": False,
    }
    mapping = (("crop_available", "crop"), ("season_available", "season"), ("growth_stage_available", "growth_stage"), ("weather_available", "weather"), ("soil_available", "soil"), ("inspection_evidence_available", "inspection_evidence"))
    flags["missing_context"] = [name for flag, name in mapping if not flags[flag]]
    return flags


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
    context = _context(field)
    ndvi_rows = db.execute(text("""
        SELECT * FROM (
            SELECT id, captured_date, mean_ndvi AS value, min_ndvi AS min_value,
                   max_ndvi AS max_value, std_ndvi AS std_value,
                   p10_ndvi AS p10_value, p90_ndvi AS p90_value,
                   satellite, cloud_cover_pct, valid_pixels_pct
            FROM ndvi_records
            WHERE field_id = :field_id AND captured_date BETWEEN :date_from AND :date_to
              AND mean_ndvi IS NOT NULL
            ORDER BY captured_date DESC, id DESC LIMIT :history_limit
        ) bounded ORDER BY captured_date ASC, id ASC
    """), {"field_id": field_id, "date_from": resolved_from, "date_to": resolved_to, "history_limit": HISTORY_RECORD_LIMIT}).fetchall()
    index_rows = db.execute(text("""
        SELECT * FROM (
            SELECT id, captured_date, index_code, mean_value AS value,
                   min_value, max_value, std_value, p10_value, p90_value,
                   satellite, cloud_cover_pct, valid_pixels_pct
            FROM satellite_index_records
            WHERE field_id = :field_id AND captured_date BETWEEN :date_from AND :date_to
              AND index_code = ANY(:index_codes) AND mean_value IS NOT NULL
            ORDER BY captured_date DESC, id DESC LIMIT :history_limit
        ) bounded ORDER BY captured_date ASC, id ASC
    """), {"field_id": field_id, "date_from": resolved_from, "date_to": resolved_to, "index_codes": list(INDEX_CODES[1:]), "history_limit": HISTORY_RECORD_LIMIT * 4}).fetchall()
    grouped = {code: [] for code in INDEX_CODES}
    grouped["ndvi"] = ndvi_rows
    for row in index_rows:
        if row.index_code in grouped and row.index_code != "ndvi":
            grouped[row.index_code].append(row)
    items = [build_index_interpretation(code, grouped[code], local_today, context) for code in INDEX_CODES]
    add_cross_index_hypotheses(items)
    summary = build_summary(items)
    field_payload = {"id": field.id, "name": field.name, "field_id": field.id, "field_name": field.name, "enterprise_id": field.enterprise_id, "crop_name": field.crop_name, "season_year": field.season_year, "growth_stage": None}
    return {"field": field_payload, "range": {"date_from": resolved_from, "date_to": resolved_to}, "generated_at": datetime.now(TASHKENT), "context": context, "summary": summary, "overall_confidence": overall_confidence(items), "indices": items, "limitations": TOP_LIMITATIONS}
