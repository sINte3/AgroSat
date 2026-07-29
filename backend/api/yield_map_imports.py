"""Tenant-authorized preview and persistence API for measured yield maps."""

import re

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from api.dependencies import (
    get_authorized_field_row,
    get_authorized_field_row_for_write,
)
from database import get_db
from schemas.yield_map_import import (
    YieldImportAcceptRequest,
    YieldImportPreviewRequest,
)
from services import yield_map_imports as service


router = APIRouter(prefix="/api/yield-map-imports", tags=["yield_map_imports"])
KEY = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


@router.post("/preview")
def preview_yield_map_import(
    payload: YieldImportPreviewRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    field = get_authorized_field_row_for_write(
        payload.field_id,
        db,
        current_user,
    )
    return service.preview_import(db, current_user, field, payload)


@router.post("", status_code=status.HTTP_201_CREATED)
def accept_yield_map_import(
    payload: YieldImportAcceptRequest,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if not KEY.fullmatch(idempotency_key):
        raise HTTPException(422, "Invalid Idempotency-Key")
    field = get_authorized_field_row_for_write(
        payload.field_id,
        db,
        current_user,
    )
    created, item = service.accept_import(
        db,
        current_user,
        field,
        payload,
        idempotency_key,
    )
    response.status_code = 201 if created else 200
    return {"created": created, "import": item}


@router.get("")
def list_yield_map_imports(
    field_id: int = Query(..., gt=0),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    field = get_authorized_field_row(field_id, db, current_user)
    return service.list_imports(
        db,
        current_user,
        field,
        limit=limit,
        offset=offset,
    )


@router.get("/{import_id}")
def get_yield_map_import(
    import_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if import_id <= 0:
        raise HTTPException(422, "Invalid import id")
    return service.get_import(db, current_user, import_id)


@router.get("/{import_id}/points")
def list_yield_map_points(
    import_id: int,
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if import_id <= 0:
        raise HTTPException(422, "Invalid import id")
    return service.list_points(
        db,
        current_user,
        import_id,
        limit=limit,
        offset=offset,
    )
