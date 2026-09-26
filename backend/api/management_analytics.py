"""Read-only H1 Management Analytics v1 API (TASK_232)."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from database import get_db
from schemas.management_analytics import Granularity, ManagementAnalyticsResponse
from services import management_analytics as service


router = APIRouter(prefix="/api/management-analytics", tags=["management_analytics"])

# The crop dimension is the field's current classification only. A bare
# crop_type_id would read as "crop grown at the time"; unknown query parameters
# are otherwise ignored, so refuse it instead of silently widening the result.
CROP_PARAMETER_REFUSAL = (
    "crop_type_id is not supported: crops are classified by each field's current "
    "crop season, never by the crop at event time; use current_crop_type_id"
)


@router.get("", response_model=ManagementAnalyticsResponse)
def get_management_analytics(
    date_from: date | None = Query(
        None, description="Inclusive Asia/Tashkent start date; defaults to 29 days before date_to."),
    date_to: date | None = Query(
        None, description="Inclusive Asia/Tashkent end date; defaults to today. At most 366 days."),
    enterprise_id: int | None = Query(
        None, gt=0, description="Narrows the server-side scope; never widens it."),
    field_id: int | None = Query(None, gt=0, description="Narrows to one authorized field."),
    current_crop_type_id: int | None = Query(
        None, gt=0,
        description="Narrows to fields whose CURRENT crop season has this crop type "
                    "(not the crop grown when a past event happened)."),
    crop_type_id: int | None = Query(None, include_in_schema=False),
    granularity: Granularity = Query("week", description="Local calendar bucket of the period breakdown."),
    field_limit: int = Query(50, ge=1, le=200, description="Page size of the field breakdown."),
    field_offset: int = Query(0, ge=0, le=10000, description="Offset of the field breakdown page."),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    # Plain def: the synchronous Session runs in the threadpool.
    if crop_type_id is not None:
        raise HTTPException(422, CROP_PARAMETER_REFUSAL)
    return service.snapshot(
        db,
        current_user,
        date_from=date_from,
        date_to=date_to,
        enterprise_id=enterprise_id,
        field_id=field_id,
        current_crop_type_id=current_crop_type_id,
        granularity=granularity,
        field_limit=field_limit,
        field_offset=field_offset,
    )
