"""Legacy TASK_209 field-inspection API: read-only since TASK_225.

Rows created here before migration 0013 carry ``source_kind='legacy'``. They
stay readable; every write is retired with 410 Gone and points at the
canonical inspection lifecycle (/api/anomaly-inspections).
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from api.lifecycle_retirement import retired
from database import get_db
from schemas.field_inspection import InspectionItem, InspectionListResponse, InspectionStatus
from services import field_inspections as service

router = APIRouter(prefix="/api/field-inspections", tags=["field_inspections"])

CANONICAL_CREATE = "POST /api/anomaly-inspections"
LEGACY_CLOSEOUT = (
    "POST /api/anomaly-inspections/{inspection_id}/cancel closes out an active legacy "
    "inspection; open new work with POST /api/anomaly-inspections"
)


@router.post("", status_code=410)
def create_inspection(current_user=Depends(get_current_active_user)):
    raise retired(
        "POST /api/field-inspections", CANONICAL_CREATE,
        "Legacy inspections can no longer be created; use the canonical inspection workflow.",
    )


@router.get("", response_model=InspectionListResponse)
def list_inspections(enterprise_id: int | None=Query(None,gt=0), field_id: int | None=Query(None,gt=0), assigned_to_id: int | None=Query(None,gt=0), status_filter: InspectionStatus | None=Query(None,alias="status"), overdue_only: bool=False, due_before: date | None=None, created_after: datetime | None=None, limit: int=Query(50,ge=1,le=200), offset: int=Query(0,ge=0,le=10000), db: Session=Depends(get_db), current_user=Depends(get_current_active_user)):
    return service.list_items(db,current_user,{"enterprise_id":enterprise_id,"field_id":field_id,"assigned_to_id":assigned_to_id,"status":status_filter,"overdue_only":overdue_only,"due_before":due_before,"created_after":created_after,"limit":limit,"offset":offset})


@router.get("/{inspection_id}", response_model=InspectionItem)
def get_inspection(inspection_id: int=Path(...,gt=0), db: Session=Depends(get_db), current_user=Depends(get_current_active_user)): return service.get(db,current_user,inspection_id)


@router.patch("/{inspection_id}", status_code=410)
def update_inspection(inspection_id: int=Path(...,gt=0), current_user=Depends(get_current_active_user)):
    raise retired("PATCH /api/field-inspections/{inspection_id}", LEGACY_CLOSEOUT,
                  "Legacy inspections are read-only.")


@router.post("/{inspection_id}/start", status_code=410)
def start_inspection(inspection_id: int=Path(...,gt=0), current_user=Depends(get_current_active_user)):
    raise retired("POST /api/field-inspections/{inspection_id}/start", LEGACY_CLOSEOUT,
                  "The legacy inspection lifecycle is retired.")


@router.post("/{inspection_id}/complete", status_code=410)
def complete_inspection(inspection_id: int=Path(...,gt=0), current_user=Depends(get_current_active_user)):
    raise retired("POST /api/field-inspections/{inspection_id}/complete", LEGACY_CLOSEOUT,
                  "The legacy inspection lifecycle is retired.")


@router.post("/{inspection_id}/cancel", status_code=410)
def cancel_inspection(inspection_id: int=Path(...,gt=0), current_user=Depends(get_current_active_user)):
    raise retired("POST /api/field-inspections/{inspection_id}/cancel",
                  "POST /api/anomaly-inspections/{inspection_id}/cancel",
                  "Legacy close-out moved to the canonical inspection API.")
