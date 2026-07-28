"""Authenticated, tenant-safe field MVT endpoints."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from models.monitoring import User
from schemas.field_tiles import FieldTileMetadataResponse
from services.field_tiles import (
    CACHE_TTL_SECONDS,
    get_metadata,
    get_tile,
    resolve_scope,
)


router = APIRouter(prefix="/api/field-tiles", tags=["field-tiles"])


@router.get("/metadata", response_model=FieldTileMetadataResponse)
def field_tile_metadata(
    enterprise_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    scope = resolve_scope(current_user, enterprise_id)
    return get_metadata(db, scope)


@router.get(
    "/{z}/{x}/{y}.mvt",
    responses={
        200: {
            "content": {
                "application/vnd.mapbox-vector-tile": {},
            }
        },
        304: {"description": "Authorized tile is unchanged"},
    },
)
def field_tile(
    request: Request,
    z: int,
    x: int,
    y: int,
    enterprise_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    scope = resolve_scope(current_user, enterprise_id)
    tile, cache_state, etag = get_tile(db, scope, z, x, y)
    headers = {
        "Cache-Control": f"private, max-age={CACHE_TTL_SECONDS}",
        "ETag": etag,
        "Vary": "Authorization",
        "X-AgroSat-Tile-Cache": cache_state,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(
        content=tile,
        media_type="application/vnd.mapbox-vector-tile",
        headers=headers,
    )
