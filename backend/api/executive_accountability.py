"""Management-only executive accountability and reconciliation APIs."""

from datetime import date, datetime
from io import BytesIO
import urllib.parse

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from schemas.executive_accountability import (
    AccountabilityResponse,
    ExecutiveOverviewResponse,
)
from services import executive_accountability as service


router = APIRouter(prefix="/api/executive", tags=["executive_accountability"])


@router.get("/overview", response_model=ExecutiveOverviewResponse)
def get_executive_overview(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    enterprise_id: int | None = Query(None, gt=0),
    attention_lookback_days: int = Query(180, ge=30, le=365),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.overview(
        db,
        current_user,
        date_from=date_from,
        date_to=date_to,
        enterprise_id=enterprise_id,
        attention_lookback_days=attention_lookback_days,
    )


@router.get("/accountability", response_model=AccountabilityResponse)
def get_accountability_queue(
    kind: str = Query(...),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    enterprise_id: int | None = Query(None, gt=0),
    owner_id: int | None = Query(None, gt=0),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return service.accountability(
        db,
        current_user,
        kind=kind,
        date_from=date_from,
        date_to=date_to,
        enterprise_id=enterprise_id,
        owner_id=owner_id,
        limit=limit,
        offset=offset,
    )


@router.get("/export.xlsx")
def download_executive_workbook(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    enterprise_id: int | None = Query(None, gt=0),
    attention_lookback_days: int = Query(180, ge=30, le=365),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    payload = service.overview(
        db,
        current_user,
        date_from=date_from,
        date_to=date_to,
        enterprise_id=enterprise_id,
        attention_lookback_days=attention_lookback_days,
    )
    content = service.executive_workbook(payload)
    date_string = datetime.now(service.TASHKENT).date().isoformat()
    filename = urllib.parse.quote(
        f"agrosat-executive-accountability-{date_string}.xlsx"
    )
    return StreamingResponse(
        BytesIO(content),
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{filename}"
            )
        },
    )
