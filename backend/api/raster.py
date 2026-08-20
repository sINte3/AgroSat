"""Generic authenticated raster endpoints with provider-neutral responses."""

import hashlib
import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from models.monitoring import User
from schemas.raster import RasterMetadataResponse
from schemas.pixel_ndvi import (
    PixelNDVISample,
    PixelNDVISceneCatalog,
    PixelNDVIWorkspace,
)
from services.pixel_ndvi import (
    DEFAULT_SCENE_DAYS,
    LEGEND as PIXEL_LEGEND,
    MASK_VERSION,
    MAX_SCENE_COUNT,
    MAX_SCENE_DAYS,
    NO_DATA_LEGEND,
    SCHEMA_VERSION as PIXEL_SCHEMA_VERSION,
    get_artifact as get_pixel_artifact,
    list_scene_rows,
    resolve_scene_row,
    sample_artifact,
    scene_response,
    validate_point,
)
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


def _raise_pixel_provider_error(error: Exception) -> None:
    if isinstance(error, RasterServiceUnavailable):
        raise HTTPException(status_code=503, detail="Satellite service unavailable") from None
    if isinstance(error, RasterUpstreamTimeout):
        raise HTTPException(status_code=504, detail="Satellite service timeout") from None
    if isinstance(error, RasterUpstreamInvalid):
        raise HTTPException(status_code=502, detail="Invalid satellite raster response") from None
    raise error


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


@router.get(
    "/fields/{field_id}/scenes",
    response_model=PixelNDVISceneCatalog,
)
def get_pixel_ndvi_scenes(
    field_id: int,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=MAX_SCENE_COUNT),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return a bounded, tenant-scoped catalog from accepted Sentinel history."""
    upper = date_to or local_today()
    lower = date_from or (upper - timedelta(days=DEFAULT_SCENE_DAYS))
    if upper < lower or (upper - lower).days > MAX_SCENE_DAYS:
        raise HTTPException(status_code=422, detail="Unsupported scene date range")
    try:
        rows = list_scene_rows(db, field_id, current_user, lower, upper, limit)
    except (RasterServiceUnavailable, RasterUpstreamTimeout, RasterUpstreamInvalid) as error:
        _raise_pixel_provider_error(error)
    return PixelNDVISceneCatalog(
        schema_version=PIXEL_SCHEMA_VERSION,
        field_id=field_id,
        date_from=lower.isoformat(),
        date_to=upper.isoformat(),
        scenes=[scene_response(row, default=index == 0) for index, row in enumerate(rows)],
    )


@router.get(
    "/fields/{field_id}/workspace",
    response_model=PixelNDVIWorkspace,
)
def get_pixel_ndvi_workspace(
    field_id: int,
    scene_id: str = Query(min_length=80, max_length=768),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return same-raster bounds, quality, statistics, and same-origin image URL."""
    row = resolve_scene_row(db, field_id, current_user, scene_id)
    try:
        artifact = get_pixel_artifact(row)
    except (RasterServiceUnavailable, RasterUpstreamTimeout, RasterUpstreamInvalid) as error:
        _raise_pixel_provider_error(error)
    metadata = artifact.metadata
    return PixelNDVIWorkspace(
        schema_version=PIXEL_SCHEMA_VERSION,
        field_id=field_id,
        scene=scene_response(row),
        bounds=metadata["bounds"],
        corners=metadata["corners"],
        width=metadata["width"],
        height=metadata["height"],
        source_resolution_m=metadata["source_resolution_m"],
        effective_resolution_m=metadata["effective_resolution_m"],
        response_bytes=metadata["response_bytes"],
        mask=MASK_VERSION,
        legend=list(PIXEL_LEGEND),
        no_data_legend=NO_DATA_LEGEND,
        summary=metadata["summary"],
        image_url=f"/api/raster/fields/{field_id}/pixel-image?scene_id={scene_id}",
        disclaimer="NDVI — индикатор состояния растительности, а не агрономический диагноз.",
    )


@router.get(
    "/fields/{field_id}/pixel-image",
    responses={200: {"content": {"image/png": {}}}, 304: {"description": "Not modified"}},
)
def get_pixel_ndvi_image(
    field_id: int,
    scene_id: str = Query(min_length=80, max_length=768),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    row = resolve_scene_row(db, field_id, current_user, scene_id)
    try:
        artifact = get_pixel_artifact(row)
    except (RasterServiceUnavailable, RasterUpstreamTimeout, RasterUpstreamInvalid) as error:
        _raise_pixel_provider_error(error)
    digest = hashlib.sha256(artifact.png).hexdigest()
    etag = f'"{digest}"'
    headers = {
        "ETag": etag,
        "Cache-Control": "private, max-age=86400",
        "Vary": "Authorization",
        "X-AgroSat-Raster-Cache": artifact.cache_state,
        "X-AgroSat-Raster-Provider": "cdse",
        "X-AgroSat-Observation-Date": row.observation_date.isoformat(),
    }
    if if_none_match and any(value.strip() in {etag, f"W/{etag}"} for value in if_none_match.split(",")):
        return Response(status_code=304, headers=headers)
    return Response(content=artifact.png, media_type="image/png", headers=headers)


@router.get(
    "/fields/{field_id}/sample",
    response_model=PixelNDVISample,
)
def get_pixel_ndvi_sample(
    field_id: int,
    scene_id: str = Query(min_length=80, max_length=768),
    longitude: float = Query(ge=-180, le=180),
    latitude: float = Query(ge=-90, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    row = resolve_scene_row(db, field_id, current_user, scene_id)
    validate_point(row, longitude, latitude)
    try:
        artifact = get_pixel_artifact(row)
    except (RasterServiceUnavailable, RasterUpstreamTimeout, RasterUpstreamInvalid) as error:
        _raise_pixel_provider_error(error)
    value, classification = sample_artifact(artifact, longitude, latitude)
    return PixelNDVISample(
        schema_version=PIXEL_SCHEMA_VERSION,
        field_id=field_id,
        scene_id=scene_id,
        acquired_at=scene_response(row)["acquired_at"],
        longitude=round(longitude, 6),
        latitude=round(latitude, 6),
        resolution_m=artifact.metadata["effective_resolution_m"],
        status="value" if value is not None else "no_data",
        ndvi=value,
        classification=classification,
    )
