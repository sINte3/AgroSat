"""Authenticated Operational Command Center HTTP API."""

from datetime import datetime
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from schemas.operational_center import (
    CaseDetailResponse,
    FilterOptionsResponse,
    NotificationListResponse,
    NotificationTransition,
    NotificationTransitionResponse,
    QueueResponse,
    SummaryResponse,
    TimelineResponse,
)
from services import operational_center as service


router = APIRouter(prefix="/api/operational-center", tags=["operational_center"])
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


def idempotency_key(value: str = Header(..., alias="Idempotency-Key")) -> str:
    if not IDEMPOTENCY_KEY.fullmatch(value):
        raise HTTPException(422, "Invalid Idempotency-Key")
    return value


def queue_parameters(
    enterprise_id: int | None = Query(None, gt=0),
    field_id: int | None = Query(None, gt=0),
    crop_type_id: int | None = Query(None, gt=0),
    assignee_id: int | None = Query(None, gt=0),
    operational_status: str | None = Query(
        None,
        pattern="^(needs_review|awaiting_inspection|awaiting_review|awaiting_work|awaiting_evidence|awaiting_verification|stale|external_unavailable|improved_closed)$",
    ),
    source: str | None = Query(
        None, pattern="^(inspection|candidate|alert|freshness|external|notification)$"
    ),
    due_from: datetime | None = Query(None),
    due_to: datetime | None = Query(None),
    overdue: bool | None = Query(None),
    blocked: bool | None = Query(None),
    awaiting_verification: bool | None = Query(None),
    external_state: str | None = Query(None, pattern="^(stale|unavailable)$"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    for value in (due_from, due_to):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise HTTPException(422, "Due date filters require a timezone")
    if due_from is not None and due_to is not None and due_to < due_from:
        raise HTTPException(422, "due_to precedes due_from")
    return locals()


@router.get("/queue", response_model=QueueResponse)
def queue(
    filters: dict = Depends(queue_parameters),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.list_queue(db, current_user, filters)


@router.get("/summary", response_model=SummaryResponse)
def summary(
    filters: dict = Depends(queue_parameters),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.summary(db, current_user, filters)


@router.get("/filter-options", response_model=FilterOptionsResponse)
def filter_options(
    enterprise_id: int | None = Query(None, gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.filter_options(db, current_user, enterprise_id)


@router.get("/cases/{case_key}", response_model=CaseDetailResponse)
def case_detail(
    case_key: str = Path(..., min_length=3, max_length=180),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.detail(db, current_user, case_key)


@router.get("/fields/{field_id}/timeline", response_model=TimelineResponse)
def field_timeline(
    field_id: int = Path(..., gt=0),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.field_timeline(
        db, current_user, field_id, limit=limit, offset=offset
    )


@router.get("/notifications", response_model=NotificationListResponse)
def notifications(
    notification_status: str | None = Query(
        None, alias="status", pattern="^(unread|read|dismissed|resolved)$"
    ),
    notification_type: str | None = Query(
        None,
        pattern="^(new_critical|assignment|due_soon|overdue|missing_execution_evidence|awaiting_satellite_verification|external_source_unavailable)$",
    ),
    case_key: str | None = Query(None, min_length=3, max_length=180),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.list_notifications(
        db,
        current_user,
        status=notification_status,
        notification_type=notification_type,
        case_key=case_key,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/notifications/{notification_id}/transition",
    response_model=NotificationTransitionResponse,
)
def transition_notification(
    payload: NotificationTransition,
    notification_id: int = Path(..., gt=0),
    command_key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.transition_notification(
        db, current_user, notification_id, payload, command_key
    )
