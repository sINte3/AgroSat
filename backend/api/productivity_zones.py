"""Tenant-authorized read API for measured-yield productivity zones."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from api.dependencies import get_authorized_field_row
from database import get_db
from services import productivity_zones as service


router = APIRouter(
    prefix="/api/productivity-zones",
    tags=["productivity_zones"],
)


@router.get("/fields/{field_id}")
def latest_productivity_zones(
    field_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if field_id <= 0:
        raise HTTPException(422, "Invalid field id")
    field = get_authorized_field_row(field_id, db, current_user)
    return service.latest_for_field(db, current_user, field)


@router.get("/runs/{run_id}")
def get_productivity_zone_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if run_id <= 0:
        raise HTTPException(422, "Invalid run id")
    return service.get_run(db, current_user, run_id)


@router.get("/runs/{run_id}/zones")
def list_productivity_zones(
    run_id: int,
    limit: int = Query(10, ge=1, le=10),
    offset: int = Query(0, ge=0, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if run_id <= 0:
        raise HTTPException(422, "Invalid run id")
    return service.list_zones(
        db,
        current_user,
        run_id,
        limit=limit,
        offset=offset,
    )
