"""Authenticated APIs for results, evidence, actions, and verification."""

import re

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from schemas.operational_closure import (
    ActionStatus,
    CloseCorrectiveActionRequest,
    CreateCorrectiveActionRequest,
    EvidenceMetadataRequest,
    RecordInspectionResultRequest,
    ReopenCorrectiveActionRequest,
    RequestVerificationRequest,
    ResolveVerificationRequest,
    UpdateCorrectiveActionRequest,
)
from services import operational_closure as service


KEY = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


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


def idempotency_key(
    value: str = Header(..., alias="Idempotency-Key"),
) -> str:
    if not KEY.fullmatch(value):
        raise HTTPException(422, "Invalid Idempotency-Key")
    return value


@inspection_router.post("/{inspection_id}/result")
def record_inspection_result(
    payload: RecordInspectionResultRequest,
    inspection_id: int = Path(..., gt=0),
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.record_result(db, current_user, inspection_id, payload, key)


@inspection_router.post("/{inspection_id}/evidence")
def attach_inspection_evidence(
    payload: EvidenceMetadataRequest,
    inspection_id: int = Path(..., gt=0),
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.attach_evidence(db, current_user, inspection_id, payload, key)


@inspection_router.post("/{inspection_id}/actions")
def create_corrective_action(
    payload: CreateCorrectiveActionRequest,
    inspection_id: int = Path(..., gt=0),
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.create_action(db, current_user, inspection_id, payload, key)


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


@action_router.patch("/{action_id}")
def update_corrective_action(
    payload: UpdateCorrectiveActionRequest,
    action_id: int = Path(..., gt=0),
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.update_action(db, current_user, action_id, payload, key)


@action_router.post("/{action_id}/close")
def close_corrective_action(
    payload: CloseCorrectiveActionRequest,
    action_id: int = Path(..., gt=0),
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.close_action(db, current_user, action_id, payload, key)


@action_router.post("/{action_id}/reopen")
def reopen_corrective_action(
    payload: ReopenCorrectiveActionRequest,
    action_id: int = Path(..., gt=0),
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.reopen_action(db, current_user, action_id, payload, key)


@action_router.post("/{action_id}/verification-requests")
def request_action_verification(
    payload: RequestVerificationRequest,
    action_id: int = Path(..., gt=0),
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.request_verification(
        db,
        current_user,
        action_id,
        payload,
        key,
    )


@verification_router.post("/{verification_id}/resolve")
def resolve_action_verification(
    payload: ResolveVerificationRequest,
    verification_id: int = Path(..., gt=0),
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.resolve_verification(
        db,
        current_user,
        verification_id,
        payload,
        key,
    )
