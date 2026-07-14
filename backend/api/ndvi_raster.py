"""Authenticated endpoints for accepted, cached Sentinel-2 NDVI rasters."""

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from api.dependencies import is_tenant_role, normalize_role
from database import get_db
from models.monitoring import User
from schemas.ndvi_raster import NDVIRasterMetadataResponse
from services.ndvi_raster import (
    ALLOWED_SIZES,
    DEFAULT_SIZE,
    LEGEND,
    LIMITATIONS,
    RasterServiceUnavailable,
    RasterUpstreamInvalid,
    RasterUpstreamTimeout,
    get_raster_png,
    validate_bbox,
    validate_size,
)

router = APIRouter(prefix="/api/ndvi-raster", tags=["ndvi-raster"])


def _local_today() -> date:
    return datetime.now(ZoneInfo("Asia/Tashkent")).date()


def _scoped_observation_row(db: Session, field_id: int, current_user: User, date_to: date, *, exact: bool):
    role = normalize_role(current_user)
    if role not in {"admin", "manager", "agronomist", "viewer"}:
        raise HTTPException(status_code=403, detail="Unknown role")
    date_param = "observation_date" if exact else "date_to"
    params = {"field_id": field_id, date_param: date_to}
    tenant_clause = ""
    if is_tenant_role(role):
        if current_user.enterprise_id is None:
            raise HTTPException(status_code=403, detail="User has no enterprise_id")
        tenant_clause = " AND f.enterprise_id = :enterprise_id"
        params["enterprise_id"] = current_user.enterprise_id
    date_clause = f"n.captured_date {'=' if exact else '<='} :{date_param}"
    geometry_select = "ST_AsGeoJSON(f.geometry)::json AS geometry," if exact else ""
    row = db.execute(text(f"""
        SELECT f.id AS field_id,
               n.captured_date AS observation_date,
               COALESCE(n.satellite, 'Sentinel-2') AS satellite,
               {geometry_select}
               ST_XMin(Box2D(f.geometry)) AS west,
               ST_YMin(Box2D(f.geometry)) AS south,
               ST_XMax(Box2D(f.geometry)) AS east,
               ST_YMax(Box2D(f.geometry)) AS north
        FROM fields f
        JOIN ndvi_records n ON n.field_id = f.id
        WHERE f.id = :field_id{tenant_clause}
          AND {date_clause}
          AND COALESCE(n.satellite, 'Sentinel-2') = 'Sentinel-2'
          AND n.mean_ndvi > 0 AND n.mean_ndvi <= 1
          AND (n.cloud_cover_pct IS NULL OR n.cloud_cover_pct <= 30)
        ORDER BY n.captured_date DESC
        LIMIT 1
    """), params).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Field not found")
    return row


@router.get("/fields/{field_id}/metadata", response_model=NDVIRasterMetadataResponse)
async def get_raster_metadata(
    field_id: int,
    date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return display metadata for the newest accepted observation, without upstream I/O."""
    row = _scoped_observation_row(db, field_id, current_user, date_to or _local_today(), exact=False)
    try:
        bbox = validate_bbox((row.west, row.south, row.east, row.north))
    except ValueError:
        raise HTTPException(status_code=502, detail="Field geometry is unavailable") from None
    return NDVIRasterMetadataResponse(
        field_id=row.field_id,
        observation_date=row.observation_date,
        satellite=row.satellite,
        bbox=bbox,
        default_size=DEFAULT_SIZE,
        allowed_sizes=list(ALLOWED_SIZES),
        legend=list(LEGEND),
        limitations=list(LIMITATIONS),
    )


@router.get("/fields/{field_id}/image", responses={200: {"content": {"image/png": {}}}})
async def get_raster_image(
    field_id: int,
    observation_date: date,
    size: int = Query(default=DEFAULT_SIZE),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return one cached Process API PNG for an exact accepted observation date."""
    try:
        validate_size(size)
    except ValueError:
        raise HTTPException(status_code=422, detail="Unsupported raster size") from None
    row = _scoped_observation_row(db, field_id, current_user, observation_date, exact=True)
    try:
        geometry = row.geometry if isinstance(row.geometry, dict) else json.loads(row.geometry)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=502, detail="Field geometry is unavailable") from None
    try:
        image, cache_state = get_raster_png(row.field_id, geometry, row.observation_date, size)
    except RasterServiceUnavailable:
        raise HTTPException(status_code=503, detail="Satellite service unavailable") from None
    except RasterUpstreamTimeout:
        raise HTTPException(status_code=504, detail="Satellite service timeout") from None
    except RasterUpstreamInvalid:
        raise HTTPException(status_code=502, detail="Invalid satellite raster response") from None
    return Response(
        content=image,
        media_type="image/png",
        headers={
            "X-AgroSat-Observation-Date": row.observation_date.isoformat(),
            "X-AgroSat-Satellite": "Sentinel-2",
            "X-AgroSat-Raster-Cache": cache_state,
            "Cache-Control": "private, max-age=86400",
        },
    )
