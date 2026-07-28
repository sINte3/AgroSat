"""Generic authenticated raster endpoints with provider-neutral responses."""

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from models.monitoring import User
from schemas.raster import RasterMetadataResponse
from services.raster_observations import (
    local_today,
    scoped_ndvi_observation_row,
    validate_bbox,
)
from services.raster_provider import (
    DEFAULT_SIZE,
    SCHEMA_VERSION,
    RasterServiceUnavailable,
    RasterUpstreamInvalid,
    RasterUpstreamTimeout,
    UnsupportedRasterIndex,
    provider_metadata,
    render_raster,
    validate_provider_size,
)


router = APIRouter(prefix="/api/raster", tags=["raster"])


def _provider_metadata_or_422(index_code: str):
    try:
        normalized = str(index_code or "").strip().lower()
        return normalized, provider_metadata(normalized)
    except UnsupportedRasterIndex:
        raise HTTPException(
            status_code=422,
            detail="Unsupported raster index",
        ) from None


@router.get(
    "/fields/{field_id}/metadata",
    response_model=RasterMetadataResponse,
)
def get_raster_metadata(
    field_id: int,
    index_code: str = Query(default="ndvi", min_length=2, max_length=16),
    date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    normalized, provider = _provider_metadata_or_422(index_code)
    row = scoped_ndvi_observation_row(
        db,
        field_id,
        current_user,
        date_to or local_today(),
        exact=False,
    )
    try:
        bbox = validate_bbox((row.west, row.south, row.east, row.north))
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Field geometry is unavailable",
        ) from None
    return RasterMetadataResponse(
        schema_version=SCHEMA_VERSION,
        field_id=row.field_id,
        enterprise_id=row.enterprise_id,
        index_code=normalized,
        observation_date=row.observation_date,
        bbox=bbox,
        default_size=provider["default_size"],
        allowed_sizes=provider["allowed_sizes"],
        legend=provider["legend"],
        limitations=provider["limitations"],
        quality={
            "accepted_observation": True,
            "mask": provider["quality_mask"],
        },
        provenance={
            "provider": provider["provider"],
            "satellite": row.satellite,
            "observation_id": row.observation_id,
            "processing_version": provider["processing_version"],
            "request_timeout_class": provider["request_timeout_class"],
        },
        image_template=(
            f"/api/raster/fields/{row.field_id}/image"
            f"?index_code={normalized}"
            f"&observation_date={row.observation_date.isoformat()}"
            "&size={size}"
        ),
    )


@router.get(
    "/fields/{field_id}/image",
    responses={200: {"content": {"image/png": {}}}},
)
def get_raster_image(
    field_id: int,
    observation_date: date,
    index_code: str = Query(default="ndvi", min_length=2, max_length=16),
    size: int = Query(default=DEFAULT_SIZE),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    normalized, provider = _provider_metadata_or_422(index_code)
    try:
        validate_provider_size(normalized, size)
    except ValueError:
        raise HTTPException(status_code=422, detail="Unsupported raster size") from None
    row = scoped_ndvi_observation_row(
        db,
        field_id,
        current_user,
        observation_date,
        exact=True,
    )
    try:
        geometry = (
            row.geometry
            if isinstance(row.geometry, dict)
            else json.loads(row.geometry)
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(
            status_code=502,
            detail="Field geometry is unavailable",
        ) from None
    try:
        image, cache_state, _render_metadata = render_raster(
            row.field_id,
            geometry,
            normalized,
            row.observation_date,
            size,
        )
    except RasterServiceUnavailable:
        raise HTTPException(
            status_code=503,
            detail="Satellite service unavailable",
        ) from None
    except RasterUpstreamTimeout:
        raise HTTPException(
            status_code=504,
            detail="Satellite service timeout",
        ) from None
    except RasterUpstreamInvalid:
        raise HTTPException(
            status_code=502,
            detail="Invalid satellite raster response",
        ) from None
    return Response(
        content=image,
        media_type="image/png",
        headers={
            "X-AgroSat-Raster-Provider": provider["provider"],
            "X-AgroSat-Raster-Index": normalized,
            "X-AgroSat-Observation-Date": row.observation_date.isoformat(),
            "X-AgroSat-Raster-Cache": cache_state,
            "Cache-Control": "private, max-age=86400",
            "Vary": "Authorization",
        },
    )
