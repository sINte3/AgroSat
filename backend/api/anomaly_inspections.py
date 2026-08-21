"""API for anomaly source, inspection, action, photo, and verification workflow."""

from datetime import datetime
import re

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Path, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from schemas.anomaly_inspection import (
    ActionTransitionRequest,
    AssignInspectionRequest,
    CancelInspectionRequest,
    CreateActionRequest,
    CreateInspectionRequest,
    FindingRequest,
    InspectionPriority,
    InspectionStatus,
    QueueResponse,
    ReviewInspectionRequest,
    SourceKind,
    VerifyActionRequest,
    VersionRequest,
    WorkflowMutationResponse,
)
from services import anomaly_inspections as service


router = APIRouter(prefix="/api/anomaly-inspections", tags=["anomaly_inspection_workflow"])
KEY = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


def idempotency_key(value: str = Header(..., alias="Idempotency-Key")) -> str:
    if not KEY.fullmatch(value):
        raise HTTPException(422, "Invalid Idempotency-Key")
    return value


@router.post("", status_code=201)
def create_inspection(
    payload: CreateInspectionRequest,
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.create(db, current_user, payload, key)


@router.get("/queue", response_model=QueueResponse)
def inspection_queue(
    enterprise_id: int | None = Query(None, gt=0),
    field_id: int | None = Query(None, gt=0),
    assigned_to_id: int | None = Query(None, gt=0),
    priority: InspectionPriority | None = None,
    status: InspectionStatus | None = None,
    source_kind: SourceKind | None = None,
    due_state: str | None = Query(None, pattern="^(overdue|due)$"),
    search: str | None = Query(None, min_length=1, max_length=100),
    sort: str = Query("priority", pattern="^(priority|due_at|created_at)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.list_queue(db, current_user, locals())


@router.get("/assignees")
def inspection_assignees(
    enterprise_id: int | None = Query(None, gt=0),
    field_id: int | None = Query(None, gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.list_assignees(db, current_user, enterprise_id, field_id)


@router.get("/fields/{field_id}/timeline")
def field_timeline(
    field_id: int = Path(..., gt=0),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.field_timeline(db, current_user, field_id, limit, offset)


@router.get("/{inspection_id}")
def inspection_detail(
    inspection_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.detail(db, current_user, inspection_id)


@router.post("/{inspection_id}/assignment", response_model=WorkflowMutationResponse)
def assign_inspection(
    payload: AssignInspectionRequest,
    inspection_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.assign(db, current_user, inspection_id, payload)


@router.post("/{inspection_id}/start", response_model=WorkflowMutationResponse)
def start_inspection(
    payload: VersionRequest,
    inspection_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.start(db, current_user, inspection_id, payload)


@router.put("/{inspection_id}/finding", response_model=WorkflowMutationResponse)
def save_finding(
    payload: FindingRequest,
    inspection_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.save_finding(db, current_user, inspection_id, payload)


@router.post("/{inspection_id}/submit", response_model=WorkflowMutationResponse)
def submit_inspection(
    payload: VersionRequest,
    inspection_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.submit(db, current_user, inspection_id, payload)


@router.post("/{inspection_id}/review", response_model=WorkflowMutationResponse)
def review_inspection(
    payload: ReviewInspectionRequest,
    inspection_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.review(db, current_user, inspection_id, payload)


@router.post("/{inspection_id}/cancel", response_model=WorkflowMutationResponse)
def cancel_inspection(
    payload: CancelInspectionRequest,
    inspection_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.cancel(db, current_user, inspection_id, payload)


@router.post("/{inspection_id}/photos", response_model=WorkflowMutationResponse, status_code=201)
async def upload_photo(
    inspection_id: int = Path(..., gt=0),
    expected_version: int = Form(..., gt=0),
    captured_at: datetime | None = Form(None),
    photo: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    content = await photo.read(service.MAX_PHOTO_BYTES + 1)
    await photo.close()
    return service.upload_photo(
        db, current_user, inspection_id, expected_version,
        photo.filename or "photo", photo.content_type or "application/octet-stream", content, captured_at,
    )


@router.get("/{inspection_id}/photos/{photo_id}")
def download_photo(
    inspection_id: int = Path(..., gt=0),
    photo_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    metadata, path = service.photo_download(db, current_user, inspection_id, photo_id)
    return FileResponse(
        path, media_type=metadata["media_type"], filename=metadata["original_filename"],
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{inspection_id}/photos/{photo_id}", response_model=WorkflowMutationResponse)
def delete_photo(
    payload: VersionRequest,
    inspection_id: int = Path(..., gt=0),
    photo_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.delete_photo(db, current_user, inspection_id, photo_id, payload.expected_version)


@router.post("/{inspection_id}/actions", response_model=WorkflowMutationResponse, status_code=201)
def create_action(
    payload: CreateActionRequest,
    inspection_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.create_action(db, current_user, inspection_id, payload)


@router.post("/actions/{action_id}/transition", response_model=WorkflowMutationResponse)
def transition_action(
    payload: ActionTransitionRequest,
    action_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.action_transition(db, current_user, action_id, payload)


@router.post("/actions/{action_id}/verify", response_model=WorkflowMutationResponse)
def verify_action(
    payload: VerifyActionRequest,
    action_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.verify_action(db, current_user, action_id, payload)
