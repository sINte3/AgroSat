"""Tenant-safe HTTP API for closed-loop agronomy plans and work."""
from datetime import datetime
import csv
import io
import re

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Path, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from schemas.closed_loop_agronomy import (
    DraftRequest, MutationResponse, PlanCommand, PlanEdit, QueueResponse,
    Reevaluate, VersionRequest, WorkCommand, WorkCreate,
)
from services import closed_loop_agronomy as service


router = APIRouter(prefix="/api/agronomy-plans", tags=["closed_loop_agronomy"])
KEY = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


def idempotency_key(value: str = Header(..., alias="Idempotency-Key")) -> str:
    if not KEY.fullmatch(value):
        raise HTTPException(422, "Invalid Idempotency-Key")
    return value


def queue_parameters(
    enterprise_id: int | None = Query(None, gt=0), field_id: int | None = Query(None, gt=0),
    inspection_id: int | None = Query(None, gt=0), assigned_to_id: int | None = Query(None, gt=0),
    priority: str | None = Query(None, pattern="^(low|normal|high|urgent)$"),
    status: str | None = Query(None, pattern="^(draft|approved|in_progress|pending_verification|rework|closed|cancelled|superseded)$"),
    verification_status: str | None = Query(None, pattern="^(PENDING_DATA|TOO_EARLY|CLOUD_BLOCKED|QUALITY_BLOCKED|PROVIDER_DEGRADED|INCONCLUSIVE|IMPROVED|NO_MATERIAL_CHANGE|WORSENED)$"),
    source_kind: str | None = Query(None, pattern="^(manual|alert|pixel_ndvi|autonomous)$"),
    due_state: str | None = Query(None, pattern="^(overdue|due)$"),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=10000),
):
    return locals()


@router.post("", response_model=MutationResponse, status_code=201)
def create_draft(payload: DraftRequest, key: str = Depends(idempotency_key), db: Session = Depends(get_db), current_user=Depends(get_current_active_user)):
    return service.draft(db, current_user, payload, key)


@router.get("/queue", response_model=QueueResponse)
def queue(filters: dict = Depends(queue_parameters), db: Session = Depends(get_db), current_user=Depends(get_current_active_user)):
    return service.list_queue(db, current_user, filters)


@router.get("/summary")
def manager_summary(filters: dict = Depends(queue_parameters), db: Session = Depends(get_db), current_user=Depends(get_current_active_user)):
    return service.summary(db, current_user, filters)


@router.get("/export.csv")
def export_queue(filters: dict = Depends(queue_parameters), db: Session = Depends(get_db), current_user=Depends(get_current_active_user)):
    filters = {**filters, "limit": 200, "offset": 0}
    result = service.list_queue(db, current_user, filters)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("plan_id", "enterprise", "field", "inspection_id", "priority", "status", "verification", "due_at"))
    for item in result["items"]:
        writer.writerow((item["id"], item["enterprise_name"], item["field_name"], item["inspection_id"], item["priority"], item["status"], item["verification_status"], item.get("due_at") or ""))
    return Response(output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=agronomy-plans.csv", "Cache-Control": "private, no-store"})


@router.get("/{plan_id}")
def detail(plan_id: int = Path(..., gt=0), db: Session = Depends(get_db), current_user=Depends(get_current_active_user)):
    return service.detail(db, current_user, plan_id)


@router.put("/{plan_id}", response_model=MutationResponse)
def edit(payload: PlanEdit, plan_id: int = Path(..., gt=0), key: str = Depends(idempotency_key), db: Session = Depends(get_db), current_user=Depends(get_current_active_user)):
    return service.edit(db, current_user, plan_id, payload, key)


@router.post("/{plan_id}/transition", response_model=MutationResponse)
def transition(payload: PlanCommand, plan_id: int = Path(..., gt=0), key: str = Depends(idempotency_key), db: Session = Depends(get_db), current_user=Depends(get_current_active_user)):
    return service.transition(db, current_user, plan_id, payload, key)


@router.post("/{plan_id}/work", response_model=MutationResponse, status_code=201)
def add_work(payload: WorkCreate, plan_id: int = Path(..., gt=0), key: str = Depends(idempotency_key), db: Session = Depends(get_db), current_user=Depends(get_current_active_user)):
    return service.add_work(db, current_user, plan_id, payload, key)


@router.post("/{plan_id}/work/{item_id}/transition", response_model=MutationResponse)
def work_transition(payload: WorkCommand, plan_id: int = Path(..., gt=0), item_id: int = Path(..., gt=0), key: str = Depends(idempotency_key), db: Session = Depends(get_db), current_user=Depends(get_current_active_user)):
    return service.work_transition(db, current_user, plan_id, item_id, payload, key)


@router.post("/{plan_id}/work/{item_id}/evidence", response_model=MutationResponse, status_code=201)
async def upload_evidence(plan_id: int = Path(..., gt=0), item_id: int = Path(..., gt=0), expected_plan_version: int = Form(..., gt=0), expected_version: int = Form(..., gt=0), key: str = Form(..., min_length=8, max_length=64), photo: UploadFile = File(...), db: Session = Depends(get_db), current_user=Depends(get_current_active_user)):
    if not KEY.fullmatch(key): raise HTTPException(422, "Invalid idempotency key")
    content = await photo.read(8 * 1024 * 1024 + 1); await photo.close()
    return service.upload_evidence(db, current_user, plan_id, item_id, expected_plan_version, expected_version, key, photo.filename or "photo", photo.content_type or "application/octet-stream", content)


@router.get("/{plan_id}/evidence/{photo_id}")
def download_evidence(plan_id: int = Path(..., gt=0), photo_id: int = Path(..., gt=0), db: Session = Depends(get_db), current_user=Depends(get_current_active_user)):
    metadata, path = service.photo_file(db, current_user, plan_id, photo_id)
    return FileResponse(path, media_type=metadata["media_type"], filename=metadata["original_filename"], headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/{plan_id}/evidence/{photo_id}", response_model=MutationResponse)
def delete_evidence(payload: VersionRequest, plan_id: int = Path(..., gt=0), photo_id: int = Path(..., gt=0), key: str = Depends(idempotency_key), db: Session = Depends(get_db), current_user=Depends(get_current_active_user)):
    return service.delete_evidence(db, current_user, plan_id, photo_id, payload, key)


@router.post("/{plan_id}/reevaluate", response_model=MutationResponse)
def reevaluate(payload: Reevaluate, plan_id: int = Path(..., gt=0), key: str = Depends(idempotency_key), db: Session = Depends(get_db), current_user=Depends(get_current_active_user)):
    return service.reevaluate(db, current_user, plan_id, payload, key)
