"""Authorized API for human-entered variable-rate drafts."""

import re

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from api.dependencies import get_authorized_field_row, get_authorized_field_row_for_write
from database import get_db
from schemas.variable_rate import VariableRateCreateRequest, VariableRateDecisionRequest
from services import variable_rate_recommendations as service


router = APIRouter(
    prefix="/api/variable-rate-recommendations",
    tags=["variable_rate_recommendations"],
)
KEY = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


@router.post("", status_code=status.HTTP_201_CREATED)
def create_variable_rate_recommendation(
    payload: VariableRateCreateRequest,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if not KEY.fullmatch(idempotency_key):
        raise HTTPException(422, "Invalid Idempotency-Key")
    field = get_authorized_field_row_for_write(payload.field_id, db, current_user)
    created, item = service.create_recommendation(
        db, current_user, field, payload, idempotency_key
    )
    response.status_code = 201 if created else 200
    return {"created": created, "recommendation": item}


@router.get("")
def list_variable_rate_recommendations(
    field_id: int = Query(..., gt=0),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    field = get_authorized_field_row(field_id, db, current_user)
    return service.list_recommendations(
        db, current_user, field, limit=limit, offset=offset
    )


@router.get("/{recommendation_id}")
def get_variable_rate_recommendation(
    recommendation_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if recommendation_id <= 0:
        raise HTTPException(422, "Invalid recommendation id")
    return service.get_recommendation(db, current_user, recommendation_id)


@router.post("/{recommendation_id}/approve")
def approve_variable_rate_recommendation(
    recommendation_id: int,
    payload: VariableRateDecisionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.decide(
        db, current_user, recommendation_id, payload, "approved"
    )


@router.post("/{recommendation_id}/reject")
def reject_variable_rate_recommendation(
    recommendation_id: int,
    payload: VariableRateDecisionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.decide(
        db, current_user, recommendation_id, payload, "rejected"
    )


@router.get("/{recommendation_id}/export.geojson")
def export_variable_rate_recommendation(
    recommendation_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.export_geojson(db, current_user, recommendation_id)
