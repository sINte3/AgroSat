"""Authenticated field-inspection assignment and lifecycle API."""
from datetime import date, datetime
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from schemas.field_inspection import (CancelInspectionRequest, CompleteInspectionRequest,
    CreateInspectionRequest, InspectionCreateResponse, InspectionItem, InspectionListResponse,
    InspectionStatus, TransitionRequest, UpdateInspectionRequest)
from services import field_inspections as service

router = APIRouter(prefix="/api/field-inspections", tags=["field_inspections"])
KEY = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


@router.post("", response_model=InspectionCreateResponse, status_code=status.HTTP_201_CREATED)
def create_inspection(payload: CreateInspectionRequest, response: Response, idempotency_key: str = Header(..., alias="Idempotency-Key"), db: Session = Depends(get_db), current_user=Depends(get_current_active_user)):
    if not KEY.fullmatch(idempotency_key): raise HTTPException(422, "Invalid Idempotency-Key")
    created, item = service.create(db, current_user, payload, idempotency_key)
    response.status_code = 201 if created else 200
    return {"created": created, "inspection": item}


@router.get("", response_model=InspectionListResponse)
def list_inspections(enterprise_id: int | None=Query(None,gt=0), field_id: int | None=Query(None,gt=0), assigned_to_id: int | None=Query(None,gt=0), status_filter: InspectionStatus | None=Query(None,alias="status"), overdue_only: bool=False, due_before: date | None=None, created_after: datetime | None=None, limit: int=Query(50,ge=1,le=200), offset: int=Query(0,ge=0,le=10000), db: Session=Depends(get_db), current_user=Depends(get_current_active_user)):
    return service.list_items(db,current_user,{"enterprise_id":enterprise_id,"field_id":field_id,"assigned_to_id":assigned_to_id,"status":status_filter,"overdue_only":overdue_only,"due_before":due_before,"created_after":created_after,"limit":limit,"offset":offset})


@router.get("/{inspection_id}", response_model=InspectionItem)
def get_inspection(inspection_id: int=Path(...,gt=0), db: Session=Depends(get_db), current_user=Depends(get_current_active_user)): return service.get(db,current_user,inspection_id)


@router.patch("/{inspection_id}", response_model=InspectionItem)
def update_inspection(inspection_id: int, payload: UpdateInspectionRequest, db: Session=Depends(get_db), current_user=Depends(get_current_active_user)): return service.update(db,current_user,inspection_id,payload)


@router.post("/{inspection_id}/start", response_model=InspectionItem)
def start_inspection(inspection_id: int, payload: TransitionRequest, db: Session=Depends(get_db), current_user=Depends(get_current_active_user)): return service.transition(db,current_user,inspection_id,payload,"start")


@router.post("/{inspection_id}/complete", response_model=InspectionItem)
def complete_inspection(inspection_id: int, payload: CompleteInspectionRequest, db: Session=Depends(get_db), current_user=Depends(get_current_active_user)): return service.transition(db,current_user,inspection_id,payload,"complete")


@router.post("/{inspection_id}/cancel", response_model=InspectionItem)
def cancel_inspection(inspection_id: int, payload: CancelInspectionRequest, db: Session=Depends(get_db), current_user=Depends(get_current_active_user)): return service.transition(db,current_user,inspection_id,payload,"cancel")
