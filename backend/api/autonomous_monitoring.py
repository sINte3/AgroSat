"""Tenant-safe APIs for the autonomous monitoring operator workspace."""

from typing import Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from services import autonomous_monitoring as service


router = APIRouter(prefix="/api/monitoring", tags=["autonomous_monitoring"])


class TransitionRequest(BaseModel):
    action: Literal["confirm", "dismiss", "resolve"]
    reason: str = Field(min_length=3, max_length=2000)
    expected_version: int = Field(ge=1)


class InspectionRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)
    expected_version: int = Field(ge=1)


@router.get("/status")
def status(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.status_summary(db, current_user)


@router.get("/freshness")
def freshness(
    enterprise_id: int | None = Query(None, gt=0),
    field_id: int | None = Query(None, gt=0),
    status_filter: Literal[
        "FRESH", "AGING", "STALE", "NEVER_COLLECTED", "CLOUD_BLOCKED",
        "PROVIDER_DEGRADED", "QUALITY_BLOCKED",
    ] | None = Query(None, alias="status"),
    index_code: Literal["ndvi", "savi", "evi", "ndmi", "ndre"] | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.list_freshness(
        db, current_user, enterprise_filter=enterprise_id, field_id=field_id,
        status=status_filter, index_code=index_code, limit=limit, offset=offset,
    )


@router.get("/candidates")
def candidates(
    enterprise_id: int | None = Query(None, gt=0),
    field_id: int | None = Query(None, gt=0),
    state: Literal["NEW", "CONFIRMED", "DISMISSED", "INSPECTION_CREATED", "RESOLVED", "SUPERSEDED"] | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.list_candidates(
        db, current_user, enterprise_filter=enterprise_id, field_id=field_id,
        state=state, limit=limit, offset=offset,
    )


@router.post("/candidates/{candidate_id}/transition")
def transition(
    payload: TransitionRequest,
    candidate_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.transition_candidate(
        db, current_user, candidate_id, action=payload.action,
        reason=payload.reason, expected_version=payload.expected_version,
    )


@router.post("/candidates/{candidate_id}/inspection")
def inspection(
    payload: InspectionRequest,
    candidate_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.create_inspection(
        db, current_user, candidate_id, reason=payload.reason,
        expected_version=payload.expected_version,
    )
