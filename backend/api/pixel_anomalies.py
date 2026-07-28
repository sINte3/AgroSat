"""Tenant-scoped pixel anomaly API."""

from datetime import date
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from schemas.pixel_anomaly import (
    CreateInspectionFromAnomalyRequest,
    PixelAnomalyClassification,
    PixelAnomalyDetail,
    PixelAnomalyGeometryResponse,
    PixelAnomalyIndex,
    PixelAnomalyInspectionResponse,
    PixelAnomalyListResponse,
    PixelAnomalyStatus,
    PixelAnomalySummaryResponse,
)
from services import pixel_anomalies as service


router = APIRouter(prefix="/api/pixel-anomalies", tags=["pixel_anomalies"])
KEY = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


@router.get("/fields/{field_id}/summary", response_model=PixelAnomalySummaryResponse)
def field_summary(
    field_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.field_summary(db, current_user, field_id)


@router.get("", response_model=PixelAnomalyListResponse)
def list_anomalies(
    enterprise_id: int | None = Query(None, gt=0),
    field_id: int | None = Query(None, gt=0),
    index_code: PixelAnomalyIndex | None = None,
    status_filter: PixelAnomalyStatus | None = Query(None, alias="status"),
    classification: PixelAnomalyClassification | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.list_items(
        db,
        current_user,
        {
            "enterprise_id": enterprise_id,
            "field_id": field_id,
            "index_code": index_code,
            "status": status_filter,
            "classification": classification,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
            "offset": offset,
        },
    )


@router.get("/{anomaly_id}", response_model=PixelAnomalyDetail)
def anomaly_detail(
    anomaly_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.detail(db, current_user, anomaly_id)


@router.get(
    "/{anomaly_id}/geometry",
    response_model=PixelAnomalyGeometryResponse,
)
def anomaly_geometry(
    anomaly_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.geometry(db, current_user, anomaly_id)


@router.post(
    "/{anomaly_id}/inspection",
    response_model=PixelAnomalyInspectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_inspection(
    payload: CreateInspectionFromAnomalyRequest,
    response: Response,
    anomaly_id: int = Path(..., gt=0),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if not KEY.fullmatch(idempotency_key):
        raise HTTPException(422, "Invalid Idempotency-Key")
    result = service.create_inspection(
        db,
        current_user,
        anomaly_id,
        payload,
        idempotency_key,
    )
    response.status_code = 201 if result["created"] else 200
    return result
