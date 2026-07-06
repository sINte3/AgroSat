"""
Read-only API router for satellite data quality summary.

Exposes a single endpoint:
  GET /api/satellite-data-quality/summary

Returns freshness, completeness, value validity, cloud/valid-pixel quality,
and problem fields across all fields and indices (NDVI, SAVI, EVI, NDMI, NDRE).

Uses explicit SQL aggregates — no lazy loading, no DB writes, no Sentinel Hub.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from api.dependencies import require_enterprise_scope
from database import get_db
from services.satellite_data_quality import (
    build_quality_summary,
    DEFAULT_FRESH_DAYS,
    PROBLEM_FIELDS_LIMIT,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/satellite-data-quality",
    tags=["satellite_data_quality"],
)


@router.get(
    "/summary",
    summary="Satellite data quality summary across all fields and indices",
    description=(
        "Read-only quality engine: returns freshness, completeness, "
        "value validity, cloud/valid-pixel quality, and problem fields "
        "for NDVI, SAVI, EVI, NDMI, NDRE."
    ),
)
async def get_data_quality_summary(
    fresh_days: int = Query(
        DEFAULT_FRESH_DAYS,
        ge=1,
        le=365,
        description="Max age in days for 'fresh' status",
    ),
    limit: int = Query(
        PROBLEM_FIELDS_LIMIT,
        ge=1,
        le=1000,
        description="Max problem fields to return",
    ),
    enterprise_id: Optional[int] = Query(
        None,
        description="Filter to a specific enterprise",
    ),
    index_code: Optional[str] = Query(
        None,
        description="Filter to a specific index code (ndvi, savi, evi, ndmi, ndre)",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
    enterprise_scope=Depends(require_enterprise_scope),
):
    """
    Read-only satellite data quality summary.

    Auth:
    - All authenticated users can access (admin, manager, agronomist, viewer).
    - Tenant-scoped users (agronomist, viewer) implicitly scoped to their
      enterprise via require_enterprise_scope.
    - Admins/managers can pass `enterprise_id` to filter.

    Behavior:
    - No DB writes.
    - No Sentinel Hub calls.
    - No lazy loading — all queries use explicit SQL aggregates.
    - NDVI is always read from ndvi_records, never from satellite_index_records.
    - Negative values are valid unless outside [-1, 1].
    """
    # Apply enterprise scope for tenant users
    effective_enterprise_id = enterprise_id
    if enterprise_scope is not None:
        # Tenant user: force to their enterprise, ignore query param
        effective_enterprise_id = enterprise_scope

    if index_code:
        norm = index_code.strip().lower()
        valid_ndvi = {"ndvi"}
        from services.satellite_data_quality import SATELLITE_INDEX_CODES
        if norm not in valid_ndvi and norm not in SATELLITE_INDEX_CODES:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported index_code '{index_code}'. "
                       f"Supported: ndvi, {', '.join(sorted(SATELLITE_INDEX_CODES))}",
            )

    stale_after_days = fresh_days  # Same threshold for staleness

    return build_quality_summary(
        db=db,
        fresh_days=fresh_days,
        stale_after_days=stale_after_days,
        limit=limit,
        enterprise_id=effective_enterprise_id,
        index_code_filter=index_code,
    )
