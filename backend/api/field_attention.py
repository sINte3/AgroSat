"""Authenticated, read-only daily field attention queue."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from api.dependencies import ALLOWED_ROLES, is_tenant_role, normalize_role
from database import get_db
from schemas.field_attention import FieldAttentionQueueResponse, Priority
from services.agronomic_interpretation import INDEX_CODES, add_cross_index_hypotheses, build_index_interpretation
from services.field_attention import PRIORITY_WEIGHT, queue_sort_key, score_field

router = APIRouter(prefix="/api/field-attention", tags=["field_attention"])
TASHKENT = ZoneInfo("Asia/Tashkent")
MAX_SCOPE_FIELDS = 2000


@router.get("/queue", response_model=FieldAttentionQueueResponse)
def get_field_attention_queue(
    enterprise_id: int | None = Query(None), crop_type_id: int | None = Query(None),
    date_to: date | None = Query(None), lookback_days: int = Query(180, ge=30, le=365),
    min_priority: Priority = Query("medium"), limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db), current_user=Depends(get_current_active_user),
):
    generated_at = datetime.now(TASHKENT)
    resolved_to = date_to or generated_at.date()
    role = normalize_role(current_user)
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Unknown role")
    resolved_enterprise = enterprise_id
    if is_tenant_role(role):
        if current_user.enterprise_id is None:
            raise HTTPException(status_code=403, detail="User has no enterprise_id")
        if enterprise_id is not None and enterprise_id != current_user.enterprise_id:
            raise HTTPException(status_code=403, detail="Доступ запрещён для данного предприятия")
        resolved_enterprise = current_user.enterprise_id

    conditions = ["f.is_active = true", "cs.season_year = :season_year"]
    params = {"season_year": resolved_to.year, "scope_limit": MAX_SCOPE_FIELDS + 1}
    if resolved_enterprise is not None:
        conditions.append("f.enterprise_id = :enterprise_id"); params["enterprise_id"] = resolved_enterprise
    if crop_type_id is not None:
        conditions.append("cs.crop_type_id = :crop_type_id"); params["crop_type_id"] = crop_type_id
    fields = db.execute(text(f"""
        SELECT f.id, f.name, f.enterprise_id, e.name AS enterprise_name,
               cs.crop_type_id, ct.name_ru AS crop_name, cs.season_year
        FROM fields f
        JOIN enterprises e ON e.id = f.enterprise_id
        JOIN crop_seasons cs ON cs.field_id = f.id
        JOIN crop_types ct ON ct.id = cs.crop_type_id
        WHERE {' AND '.join(conditions)}
        ORDER BY f.id ASC
        LIMIT :scope_limit
    """), params).fetchall()
    if len(fields) > MAX_SCOPE_FIELDS:
        raise HTTPException(status_code=422, detail="Select an enterprise or a narrower crop filter; authorized scope exceeds 2000 fields")
    base = {"generated_at": generated_at, "date_to": resolved_to, "lookback_days": lookback_days,
            "scope": {"enterprise_id": enterprise_id, "crop_type_id": crop_type_id, "max_scope_fields": MAX_SCOPE_FIELDS}}
    if not fields:
        return {**base, "summary": {"fields_evaluated": 0, "attention_fields": 0, "critical": 0, "high": 0, "medium": 0, "low": 0, "returned": 0}, "items": []}

    field_ids = [f.id for f in fields]
    common = {"field_ids": field_ids, "date_from": resolved_to - timedelta(days=lookback_days), "date_to": resolved_to}
    alerts = db.execute(text("""
        SELECT id, field_id, alert_type, severity, title, triggered_at FROM alerts
        WHERE field_id = ANY(:field_ids) AND is_active = true
        ORDER BY field_id ASC, triggered_at DESC, id ASC
    """), {"field_ids": field_ids}).fetchall()
    ndvi = db.execute(text("""
        SELECT id, field_id, captured_date, mean_ndvi AS value, satellite, cloud_cover_pct, valid_pixels_pct
        FROM ndvi_records WHERE field_id = ANY(:field_ids) AND captured_date >= :date_from
          AND captured_date <= :date_to AND mean_ndvi IS NOT NULL
        ORDER BY field_id ASC, captured_date ASC, id ASC
    """), common).fetchall()
    multi = db.execute(text("""
        SELECT id, field_id, captured_date, index_code, mean_value AS value, satellite, cloud_cover_pct, valid_pixels_pct
        FROM satellite_index_records WHERE field_id = ANY(:field_ids) AND captured_date >= :date_from
          AND captured_date <= :date_to AND mean_value IS NOT NULL
          AND index_code = ANY(:index_codes)
        ORDER BY field_id ASC, captured_date ASC, id ASC
    """), {**common, "index_codes": list(INDEX_CODES[1:])}).fetchall()
    by_alert, by_index = defaultdict(list), defaultdict(lambda: {code: [] for code in INDEX_CODES})
    for row in alerts: by_alert[row.field_id].append(row)
    for row in ndvi: by_index[row.field_id]["ndvi"].append(row)
    for row in multi:
        if row.index_code in INDEX_CODES[1:]: by_index[row.field_id][row.index_code].append(row)
    items = []
    for field in fields:
        interpretations = [build_index_interpretation(code, by_index[field.id][code], resolved_to) for code in INDEX_CODES]
        add_cross_index_hypotheses(interpretations)
        item = {"rank": 0, "field": {key: getattr(field, key) for key in ("id", "name", "enterprise_id", "enterprise_name", "crop_type_id", "crop_name", "season_year")}}
        item.update(score_field(by_alert[field.id], interpretations, resolved_to)); items.append(item)
    counts = {priority: sum(i["priority"] == priority for i in items) for priority in PRIORITY_WEIGHT}
    attention = sum(i["priority"] != "low" for i in items)
    selected = [i for i in items if PRIORITY_WEIGHT[i["priority"]] >= PRIORITY_WEIGHT[min_priority]]
    selected.sort(key=queue_sort_key); selected = selected[:limit]
    for rank, item in enumerate(selected, 1): item["rank"] = rank
    return {**base, "summary": {"fields_evaluated": len(items), "attention_fields": attention,
            **counts, "returned": len(selected)}, "items": selected}
