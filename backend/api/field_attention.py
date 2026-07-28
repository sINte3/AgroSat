"""Authenticated, read-only daily field attention queue."""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from schemas.field_attention import FieldAttentionQueueResponse, Priority
from services.field_attention import MAX_SCOPE_FIELDS, build_attention_queue

router = APIRouter(prefix="/api/field-attention", tags=["field_attention"])


@router.get("/queue", response_model=FieldAttentionQueueResponse)
def get_field_attention_queue(
    enterprise_id: int | None = Query(None), crop_type_id: int | None = Query(None),
    date_to: date | None = Query(None), lookback_days: int = Query(180, ge=30, le=365),
    min_priority: Priority = Query("medium"), limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db), current_user=Depends(get_current_active_user),
):
    return build_attention_queue(
        db,
        current_user,
        enterprise_id=enterprise_id,
        crop_type_id=crop_type_id,
        date_to=date_to,
        lookback_days=lookback_days,
        min_priority=min_priority,
        limit=limit,
    )
