"""Tenant-authorized weather and irrigation operational context."""

import re

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from api.dependencies import (
    get_authorized_field_row,
    get_authorized_field_row_for_write,
)
from database import get_db
from schemas.irrigation_context import (
    CreateIrrigationEventRequest,
    IrrigationEventCreateResponse,
)
from services import irrigation_context as service
from services.cache import cache_delete_pattern, cache_get, cache_set
from services.weather import get_field_weather


router = APIRouter(
    prefix="/api/irrigation-context",
    tags=["irrigation_context"],
)
KEY = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


@router.get("/fields/{field_id}")
def get_irrigation_context(
    field_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    field = get_authorized_field_row(field_id, db, current_user)
    key = service.irrigation_context_cache_key(
        int(field.enterprise_id),
        int(field.id),
        limit,
    )
    cached = cache_get(key)
    if isinstance(cached, dict):
        return cached
    result = service.field_context(
        db,
        current_user,
        field,
        limit=limit,
        weather_loader=get_field_weather,
    )
    cache_set(key, result, ttl_seconds=300)
    return result


@router.post(
    "/fields/{field_id}/events",
    response_model=IrrigationEventCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_irrigation_event(
    payload: CreateIrrigationEventRequest,
    response: Response,
    field_id: int,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if not KEY.fullmatch(idempotency_key):
        raise HTTPException(422, "Invalid Idempotency-Key")
    field = get_authorized_field_row_for_write(
        field_id,
        db,
        current_user,
    )
    created, event = service.create_event(
        db,
        current_user,
        field,
        payload,
        idempotency_key,
    )
    response.status_code = 201 if created else 200
    if created:
        cache_delete_pattern(
            service.irrigation_context_cache_pattern(
                int(field.enterprise_id),
                int(field.id),
            )
        )
    return {"created": created, "event": event}
