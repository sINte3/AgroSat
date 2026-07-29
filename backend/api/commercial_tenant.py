"""Authorized commercial tenant boundary APIs."""

import re

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from schemas.commercial_tenant import (
    CommercialProfileUpdate,
    LifecycleDecision,
    LifecycleRequestCreate,
    ProviderCredentialReferenceUpdate,
)
from services import commercial_tenant as service


router = APIRouter(prefix="/api/commercial/tenants", tags=["commercial_tenants"])
KEY = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
CODE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


@router.get("/{enterprise_id}")
def get_commercial_boundary(
    enterprise_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.get_boundary(db, current_user, enterprise_id)


@router.put("/{enterprise_id}")
def update_commercial_boundary(
    enterprise_id: int,
    payload: CommercialProfileUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.update_profile(db, current_user, enterprise_id, payload)


@router.get("/{enterprise_id}/memberships")
def list_commercial_memberships(
    enterprise_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.list_memberships(
        db, current_user, enterprise_id, limit, offset
    )


@router.put("/{enterprise_id}/providers/{provider_code}")
def configure_commercial_provider(
    enterprise_id: int,
    provider_code: str,
    payload: ProviderCredentialReferenceUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if not CODE.fullmatch(provider_code):
        raise HTTPException(422, "Invalid provider code")
    return service.configure_provider(
        db, current_user, enterprise_id, provider_code, payload
    )


@router.get("/{enterprise_id}/lifecycle-requests")
def list_commercial_lifecycle_requests(
    enterprise_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.list_lifecycle_requests(
        db, current_user, enterprise_id, limit, offset
    )


@router.post("/{enterprise_id}/lifecycle-requests", status_code=201)
def create_commercial_lifecycle_request(
    enterprise_id: int,
    payload: LifecycleRequestCreate,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if not KEY.fullmatch(idempotency_key):
        raise HTTPException(422, "Invalid Idempotency-Key")
    created, item = service.create_lifecycle_request(
        db, current_user, enterprise_id, payload, idempotency_key
    )
    response.status_code = 201 if created else 200
    return {"created": created, "request": item}


@router.post("/{enterprise_id}/lifecycle-requests/{request_id}/decision")
def decide_commercial_lifecycle_request(
    enterprise_id: int,
    request_id: int,
    payload: LifecycleDecision,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.decide_lifecycle_request(
        db, current_user, enterprise_id, request_id, payload
    )
