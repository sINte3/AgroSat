"""Authenticated read-only field telematics context."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from api.dependencies import get_authorized_field_row
from config import settings
from database import get_db
from models.monitoring import User
from services.cache import cache_get, cache_set
from services.telematics import (
    UnsupportedTelematicsProvider,
    read_field_telematics,
    telematics_cache_key,
)


router = APIRouter(prefix="/api/telematics", tags=["telematics"])
PROVIDER = UnsupportedTelematicsProvider()


def resolve_field_mapping(_enterprise_id: int, _field_id: int):
    """Future tenant mapping repository; fail closed while B-006 is unresolved."""
    return None


@router.get("/fields/{field_id}")
def get_field_telematics(
    field_id: int,
    hours: int = Query(default=24, ge=1, le=24 * 31),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # Keep the deferred first-pilot surface non-enumerable. This check runs
    # before field lookup so disabled deployments expose neither field nor
    # telematics availability through response differences.
    if not settings.wialon_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    field = get_authorized_field_row(field_id, db, current_user)
    ended_at = datetime.now(timezone.utc)
    started_at = ended_at - timedelta(hours=hours)
    enterprise_id = int(field.enterprise_id)
    cache_key = telematics_cache_key(enterprise_id, int(field.id), started_at, ended_at)
    cached = cache_get(cache_key)
    if isinstance(cached, dict):
        return cached
    result = read_field_telematics(
        PROVIDER,
        resolve_field_mapping(enterprise_id, int(field.id)),
        started_at=started_at,
        ended_at=ended_at,
        limit=limit,
    )
    if result["status"] in {"available", "stale"}:
        cache_set(cache_key, result, ttl_seconds=60)
    return result
