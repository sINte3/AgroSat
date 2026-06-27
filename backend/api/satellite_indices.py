"""
Read-only API router for multi-index satellite records (SAVI, EVI, NDMI, NDRE).

Follows the same raw-SQL / raw-dict style as backend/api/ndvi.py.
No ORM lazy loading — all queries use explicit text() SQL.
No references to ndvi_records or the legacy NDVI pipeline.
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.dependencies import get_authorized_field_row
from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/satellite-indices", tags=["satellite_indices"])

SUPPORTED_INDEX_CODES = frozenset({"savi", "evi", "ndmi", "ndre"})
MAX_DAYS = 3650


# ─── Local helpers ───────────────────────────────────────────────────────────

def normalize_index_code(index_code: str) -> str:
    """Lowercase + strip. Returns empty string if None/whitespace."""
    if not index_code or not index_code.strip():
        return ""
    return index_code.strip().lower()


def safe_float(v):
    """Convert to float or None. Rejects NaN/Inf."""
    if v is None:
        return None
    try:
        val = float(v)
        import math
        return None if math.isnan(val) or math.isinf(val) else val
    except (TypeError, ValueError):
        return None


def _format_record(row) -> dict:
    """Convert a satellite_index_records row tuple into a JSON-safe dict."""
    return {
        "id": row.id,
        "field_id": row.field_id,
        "captured_date": str(row.captured_date),
        "index_code": row.index_code,
        "mean_value": safe_float(row.mean_value),
        "min_value": safe_float(row.min_value),
        "max_value": safe_float(row.max_value),
        "std_value": safe_float(row.std_value),
        "p10_value": safe_float(row.p10_value),
        "p90_value": safe_float(row.p90_value),
        "valid_pixels_pct": safe_float(row.valid_pixels_pct),
        "cloud_cover_pct": safe_float(row.cloud_cover_pct),
        "satellite": row.satellite,
        "created_at": str(row.created_at) if row.created_at else None,
    }


def _validate_and_normalize_index(code: str) -> str:
    """Validate index_code query param, return normalized form or 422."""
    normalized = normalize_index_code(code)
    if not normalized:
        raise HTTPException(status_code=422, detail="index_code is required")
    if normalized not in SUPPORTED_INDEX_CODES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported index_code '{code}'. Supported: {', '.join(sorted(SUPPORTED_INDEX_CODES))}",
        )
    return normalized


def _cloud_filter(include_cloudy: bool) -> tuple[str, str]:
    """Return (where_clause, having_clause) for cloud cover filtering."""
    if include_cloudy:
        return "", ""
    # Exclude records where cloud_cover_pct > 30; keep NULL cloud_cover_pct
    return "AND (cloud_cover_pct IS NULL OR cloud_cover_pct <= 30)", ""


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/{field_id}/latest")
async def get_latest(
    field_id: int,
    index_code: str,
    include_cloudy: bool = False,
    db: Session = Depends(get_db),
    _auth_field=Depends(get_authorized_field_row),
):
    """Latest satellite index record for a field and index code."""
    code = _validate_and_normalize_index(index_code)
    cloud_where, _ = _cloud_filter(include_cloudy)

    row = db.execute(text(f"""
        SELECT id, field_id, captured_date, index_code,
               mean_value, min_value, max_value, std_value,
               p10_value, p90_value,
               valid_pixels_pct, cloud_cover_pct, satellite, created_at
        FROM satellite_index_records
        WHERE field_id = :fid
          AND index_code = :code
          {cloud_where}
        ORDER BY captured_date DESC, id DESC
        LIMIT 1
    """), {"fid": field_id, "code": code}).fetchone()

    result = {
        "field_id": field_id,
        "field_name": _auth_field.name,
        "index_code": code,
        "record": _format_record(row) if row else None,
    }
    return result


@router.get("/{field_id}/history")
async def get_history(
    field_id: int,
    index_code: str,
    days: int = 90,
    include_cloudy: bool = False,
    db: Session = Depends(get_db),
    _auth_field=Depends(get_authorized_field_row),
):
    """Historical satellite index records for a field and index code."""
    code = _validate_and_normalize_index(index_code)

    if days < 1:
        raise HTTPException(status_code=422, detail="days must be >= 1")
    if days > MAX_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"days must be <= {MAX_DAYS}",
        )

    cloud_where, _ = _cloud_filter(include_cloudy)
    since_date = (datetime.now() - timedelta(days=days)).date()

    rows = db.execute(text(f"""
        SELECT id, field_id, captured_date, index_code,
               mean_value, min_value, max_value, std_value,
               p10_value, p90_value,
               valid_pixels_pct, cloud_cover_pct, satellite, created_at
        FROM satellite_index_records
        WHERE field_id = :fid
          AND index_code = :code
          AND captured_date >= :since
          {cloud_where}
        ORDER BY captured_date ASC, id ASC
    """), {"fid": field_id, "code": code, "since": since_date}).fetchall()

    records = [_format_record(r) for r in rows]

    return {
        "field_id": field_id,
        "field_name": _auth_field.name,
        "index_code": code,
        "days": days,
        "include_cloudy": include_cloudy,
        "records": records,
        "count": len(records),
    }
