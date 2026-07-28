"""Authenticated endpoints for accepted, cached Sentinel-2 NDVI rasters."""

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from models.monitoring import User
from schemas.ndvi_raster import NDVIRasterMetadataResponse
from services.ndvi_raster import (
    ALLOWED_SIZES,
    DEFAULT_SIZE,
    LEGEND,
    LIMITATIONS,
    validate_size,
)
from services.raster_observations import (
    local_today as _local_today,
    scoped_ndvi_observation_row as _scoped_observation_row,
    validate_bbox,
)
from services.raster_provider import (
    RasterServiceUnavailable,
    RasterUpstreamInvalid,
    RasterUpstreamTimeout,
    get_raster_png,
)

router = APIRouter(prefix="/api/ndvi-raster", tags=["ndvi-raster"])


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
            "X-AgroSat-Satellite": row.satellite,
            "X-AgroSat-Raster-Cache": cache_state,
            "Cache-Control": "private, max-age=86400",
        },
    )
