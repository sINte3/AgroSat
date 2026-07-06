"""
Read-only API router for agronomic risk summary.

Exposes a single endpoint:
  GET /api/agronomic-risk/summary

Returns field-level agronomic risk signals derived from existing satellite
index data and data-quality facts.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.auth import get_current_active_user
from api.dependencies import require_enterprise_scope
from database import get_db
from services.agronomic_risk_engine import (
    build_agronomic_risk_summary,
    DEFAULT_FRESH_DAYS,
    DEFAULT_FIELD_LIMIT,
    MAX_FIELD_LIMIT,
)
from services.satellite_alert_generation import (
    generate_alert_candidates,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/agronomic-risk",
    tags=["agronomic_risk"],
)

VALID_SEVERITIES = frozenset({"low", "medium", "high", "critical"})


@router.get(
    "/summary",
    summary="Agronomic risk summary across all fields",
    description=(
        "Read-only agronomic risk engine: returns field-level risk signals "
        "derived from NDVI, NDMI, and data quality facts. Conservative thresholds only."
    ),
)
async def get_agronomic_risk_summary(
    fresh_days: int = Query(
        DEFAULT_FRESH_DAYS,
        ge=1,
        le=365,
        description="Max age in days for 'fresh' status",
    ),
    limit: int = Query(
        DEFAULT_FIELD_LIMIT,
        ge=1,
        le=MAX_FIELD_LIMIT,
        description="Max field risks to return",
    ),
    enterprise_id: Optional[int] = Query(
        None,
        description="Filter to a specific enterprise",
    ),
    include_low_risk: bool = Query(
        False,
        description="Include fields with low (no) risk",
    ),
    min_severity: Optional[str] = Query(
        None,
        description="Minimum severity to include (low, medium, high, critical)",
    ),
    field_id: Optional[int] = Query(
        None,
        description="Filter to a specific field",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
    enterprise_scope=Depends(require_enterprise_scope),
):
    """
    Read-only agronomic risk summary.

    Auth:
    - All authenticated users can access.
    - Tenant-scoped users (agronomist, viewer) scoped to their enterprise.
    - Admins/managers can pass enterprise_id to filter.

    Behavior:
    - No DB writes.
    - No Sentinel Hub calls.
    - No lazy loading — all queries use explicit SQL.
    - NDVI from ndvi_records, NDMI from satellite_index_records.
    - Negative values preserved as valid — only outside [-1, 1] flagged.
    """
    # Validate min_severity
    if min_severity is not None and min_severity.lower() not in VALID_SEVERITIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid min_severity '{min_severity}'. "
                   f"Valid: {', '.join(sorted(VALID_SEVERITIES))}",
        )

    # Apply enterprise scope for tenant users
    effective_enterprise_id = enterprise_id
    if enterprise_scope is not None:
        effective_enterprise_id = enterprise_scope

    return build_agronomic_risk_summary(
        db=db,
        fresh_days=fresh_days,
        limit=limit,
        enterprise_id=effective_enterprise_id,
        include_low_risk=include_low_risk,
        min_severity=min_severity,
        field_id=field_id,
    )


@router.get(
    "/alert-candidates",
    summary="Satellite alert candidates from agronomic risk facts",
    description=(
        "Read-only alert candidate generation. Converts agronomic risk "
        "engine output into controlled satellite alert candidates. "
        "No DB writes — dry-run mode always."
    ),
)
async def get_alert_candidates(
    fresh_days: int = Query(
        DEFAULT_FRESH_DAYS,
        ge=1,
        le=365,
        description="Max age in days for 'fresh' status",
    ),
    limit: int = Query(
        100,
        ge=1,
        le=1000,
        description="Max alert candidates to return",
    ),
    enterprise_id: Optional[int] = Query(
        None,
        description="Filter to a specific enterprise",
    ),
    field_id: Optional[int] = Query(
        None,
        description="Filter to a specific field",
    ),
    min_severity: Optional[str] = Query(
        None,
        description="Minimum severity to include (low, medium, high, critical)",
    ),
    include_low: bool = Query(
        False,
        description="Include low-severity reasons (noisy)",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
    enterprise_scope=Depends(require_enterprise_scope),
):
    """
    Read-only satellite alert candidates.

    Auth:
    - All authenticated users.
    - Tenant-scoped users scoped to their enterprise.
    - Admins/managers can pass enterprise_id to filter.

    Behavior:
    - No DB writes.
    - No Sentinel Hub calls.
    - Uses agronomic risk engine data (reuses queries).
    - Candidates have deterministic idempotency keys.
    - Persistent alert creation deferred pending migration.
    """
    # Validate min_severity
    if min_severity is not None and min_severity.lower() not in VALID_SEVERITIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid min_severity '{min_severity}'. "
                   f"Valid: {', '.join(sorted(VALID_SEVERITIES))}",
        )

    # Apply enterprise scope for tenant users
    effective_enterprise_id = enterprise_id
    if enterprise_scope is not None:
        effective_enterprise_id = enterprise_scope

    return generate_alert_candidates(
        db=db,
        fresh_days=fresh_days,
        limit=limit,
        enterprise_id=effective_enterprise_id,
        field_id=field_id,
        min_severity=min_severity,
        include_low=include_low,
    )
