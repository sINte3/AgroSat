"""Retired TASK_209 closure APIs: historical reads stay, every write is 410.

Findings and photos belong to the canonical inspection workflow
(/api/anomaly-inspections); corrective work, execution evidence and satellite
verification belong to the TASK_220 agronomy lifecycle (/api/agronomy-plans).
"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from api.lifecycle_retirement import retired
from database import get_db
from schemas.operational_closure import ActionStatus
from services import operational_closure as service


inspection_router = APIRouter(
    prefix="/api/field-inspections",
    tags=["operational_closure"],
)
action_router = APIRouter(
    prefix="/api/operational-actions",
    tags=["operational_closure"],
)
verification_router = APIRouter(
    prefix="/api/verification-requests",
    tags=["operational_closure"],
)

FINDING = "PUT /api/anomaly-inspections/{inspection_id}/finding, then POST /api/anomaly-inspections/{inspection_id}/submit"
PHOTOS = "POST /api/anomaly-inspections/{inspection_id}/photos"
PLAN = "POST /api/agronomy-plans (from a submitted or confirmed canonical inspection)"
WORK = "POST /api/agronomy-plans/{plan_id}/work/{item_id}/transition"
RESOLUTION = "POST /api/agronomy-plans/{plan_id}/transition (close, rework, reopen)"
VERIFICATION = (
    "Satellite verification runs in the standalone collector; "
    "POST /api/agronomy-plans/{plan_id}/reevaluate re-reads an eligible observation"
)
RETIRED = "The TASK_209 corrective-action lifecycle is retired; history stays readable."


@inspection_router.post("/{inspection_id}/result", status_code=410)
def record_inspection_result(
    inspection_id: int = Path(..., gt=0),
    current_user=Depends(get_current_active_user),
):
    raise retired("POST /api/field-inspections/{inspection_id}/result", FINDING, RETIRED)


@inspection_router.post("/{inspection_id}/evidence", status_code=410)
def attach_inspection_evidence(
    inspection_id: int = Path(..., gt=0),
    current_user=Depends(get_current_active_user),
):
    raise retired("POST /api/field-inspections/{inspection_id}/evidence", PHOTOS, RETIRED)


@inspection_router.post("/{inspection_id}/actions", status_code=410)
def create_corrective_action(
    inspection_id: int = Path(..., gt=0),
    current_user=Depends(get_current_active_user),
):
    raise retired("POST /api/field-inspections/{inspection_id}/actions", PLAN, RETIRED)


@inspection_router.get("/{inspection_id}/timeline")
def inspection_timeline(
    inspection_id: int = Path(..., gt=0),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.timeline(
        db,
        current_user,
        inspection_id,
        limit=limit,
        offset=offset,
    )


@inspection_router.get("/{inspection_id}/closure")
def inspection_closure_detail(
    inspection_id: int = Path(..., gt=0),
    evidence_limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.closure_detail(
        db,
        current_user,
        inspection_id,
        evidence_limit=evidence_limit,
    )


@action_router.get("")
def list_corrective_actions(
    enterprise_id: int | None = Query(None, gt=0),
    inspection_id: int | None = Query(None, gt=0),
    owner_id: int | None = Query(None, gt=0),
    status_filter: ActionStatus | None = Query(None, alias="status"),
    overdue_only: bool = False,
    awaiting_verification: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.list_actions(
        db,
        current_user,
        {
            "enterprise_id": enterprise_id,
            "inspection_id": inspection_id,
            "owner_id": owner_id,
            "status": status_filter,
            "overdue_only": overdue_only,
            "awaiting_verification": awaiting_verification,
            "limit": limit,
            "offset": offset,
        },
    )


@action_router.patch("/{action_id}", status_code=410)
def update_corrective_action(
    action_id: int = Path(..., gt=0),
    current_user=Depends(get_current_active_user),
):
    raise retired("PATCH /api/operational-actions/{action_id}", WORK, RETIRED)


@action_router.post("/{action_id}/close", status_code=410)
def close_corrective_action(
    action_id: int = Path(..., gt=0),
    current_user=Depends(get_current_active_user),
):
    raise retired("POST /api/operational-actions/{action_id}/close", RESOLUTION, RETIRED)


@action_router.post("/{action_id}/reopen", status_code=410)
def reopen_corrective_action(
    action_id: int = Path(..., gt=0),
    current_user=Depends(get_current_active_user),
):
    raise retired("POST /api/operational-actions/{action_id}/reopen", RESOLUTION, RETIRED)


@action_router.post("/{action_id}/verification-requests", status_code=410)
def request_action_verification(
    action_id: int = Path(..., gt=0),
    current_user=Depends(get_current_active_user),
):
    raise retired(
        "POST /api/operational-actions/{action_id}/verification-requests", VERIFICATION, RETIRED,
    )


@verification_router.post("/{verification_id}/resolve", status_code=410)
def resolve_action_verification(
    verification_id: int = Path(..., gt=0),
    current_user=Depends(get_current_active_user),
):
    raise retired(
        "POST /api/verification-requests/{verification_id}/resolve", VERIFICATION, RETIRED,
    )
