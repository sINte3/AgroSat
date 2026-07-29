"""
Weather API — field-authorized weather with tenant isolation.
Harden: auth/tenant isolation/bbox checks/sanitized errors per TASK_007.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from models.monitoring import User
from services.weather import get_field_weather
from services.cache import cache_get, cache_set
from api.auth import get_current_active_user
from api.dependencies import (
    get_authorized_field_row,
    normalize_role,
    require_enterprise_scope,
)

router = APIRouter(prefix="/api/weather", tags=["weather"])


@router.get("/field/{field_id}")
def get_weather_for_field(
    field_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Погода для конкретного поля — требует авторизации поля."""
    # Object-level field authorization (404 for cross-tenant access)
    field_row = get_authorized_field_row(field_id=field_id, db=db, current_user=current_user)

    if not field_row.centroid_lat or not field_row.centroid_lon:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Для данного поля не заданы координаты центра"
        )

    # Cache after auth, keyed by field_id
    cache_key = (
        f"weather:v2:enterprise:{int(field_row.enterprise_id)}:"
        f"field:{int(field_row.id)}"
    )
    cached = cache_get(cache_key)
    if cached:
        return cached

    lat = float(field_row.centroid_lat)
    lon = float(field_row.centroid_lon)

    try:
        weather = get_field_weather(lat, lon)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось получить данные погоды"
        )

    if not weather:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось получить данные погоды"
        )

    weather["field_id"] = field_row.id
    weather["field_name"] = field_row.name
    cache_set(cache_key, weather, ttl_seconds=300)
    return weather


@router.get("/location")
def get_weather_by_location(
    lat: float,
    lon: float,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Погода по координатам. Только admin/manager, ограничено Бухарой."""
    role = normalize_role(current_user)
    if role not in ("admin", "manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только администраторы и менеджеры могут запрашивать погоду по координатам"
        )

    # Bukhara bounding box
    if not (63.0 <= lon <= 65.5 and 38.5 <= lat <= 40.5):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Координаты вне разрешённого региона (Бухарская область)"
        )

    scope = require_enterprise_scope(current_user)
    scope_key = "global" if scope is None else f"enterprise:{int(scope)}"
    cache_key = f"weather:v2:{scope_key}:location:{lat}:{lon}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        weather = get_field_weather(lat, lon)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось получить данные погоды"
        )

    if not weather:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось получить данные погоды"
        )

    cache_set(cache_key, weather, ttl_seconds=300)
    return weather
