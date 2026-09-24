"""Read-only NDVI endpoints.

Satellite collection is owned by the standalone collector
(scripts/collect_satellite.py), which writes observations inside a
satellite_collection_runs record under its advisory lock. The web process
never calls the provider and never writes an observation: the former
synchronous refresh endpoint is retired with 410 Gone (TASK_225).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from datetime import datetime, timedelta

from api.auth import get_current_active_user
from api.dependencies import get_authorized_field_row
from api.lifecycle_retirement import retired
from api.query_bounds import (
    SATELLITE_HISTORY_ROW_CAP,
    ensure_within_row_cap,
    fetch_limit,
)

router = APIRouter(prefix="/api/ndvi", tags=["ndvi"])


@router.get("/{field_id}/history")
def get_ndvi_history(
    field_id: int,
    days: int = 90,
    include_cloudy: bool = False,
    db: Session = Depends(get_db),
    _auth_field=Depends(get_authorized_field_row),
):
    """NDVI history for a field over N days. Returns empty list if no data.
    By default excludes cloudy captures (cloud_cover_pct > 30)."""
    if days < 1 or days > SATELLITE_HISTORY_ROW_CAP:
        raise HTTPException(
            status_code=422,
            detail=f"days must be between 1 and {SATELLITE_HISTORY_ROW_CAP}",
        )

    # get_authorized_field_row already verified access; _auth_field contains the row
    since_date = (datetime.now() - timedelta(days=days)).date()
    cloud_filter = "" if include_cloudy else "  AND (cloud_cover_pct IS NULL OR cloud_cover_pct <= 30)"

    records = db.execute(text(f"""
        SELECT
            id,
            captured_date,
            mean_ndvi,
            min_ndvi,
            max_ndvi,
            std_ndvi,
            ndvi_change,
            ndvi_change_pct,
            satellite,
            cloud_cover_pct
        FROM ndvi_records
        WHERE field_id = :fid
          AND captured_date >= :since
          {cloud_filter}
        ORDER BY captured_date ASC
        LIMIT :row_limit
    """), {
        "fid": field_id,
        "since": since_date,
        "row_limit": fetch_limit(SATELLITE_HISTORY_ROW_CAP),
    }).fetchall()
    ensure_within_row_cap(
        records,
        row_cap=SATELLITE_HISTORY_ROW_CAP,
        resource="ndvi_history",
    )

    def safe_float(v):
        return round(float(v), 4) if v is not None else None

    return {
        "field_id": field_id,
        "field_name": _auth_field.name,
        "days": days,
        "records": [
            {
                "id": r.id,
                "captured_date": str(r.captured_date),
                "mean_ndvi": safe_float(r.mean_ndvi),
                "min_ndvi": safe_float(r.min_ndvi),
                "max_ndvi": safe_float(r.max_ndvi),
                "std_ndvi": safe_float(r.std_ndvi),
                "ndvi_change": safe_float(r.ndvi_change),
                "change_pct": safe_float(r.ndvi_change_pct),
                "satellite": r.satellite,
                "cloud_cover_pct": safe_float(r.cloud_cover_pct),
            }
            for r in records
        ],
        "count": len(records),
    }


@router.get("/{field_id}/latest")
def get_ndvi_latest(
    field_id: int,
    db: Session = Depends(get_db),
    _auth_field=Depends(get_authorized_field_row),
):
    """Latest NDVI capture for a field."""
    row = db.execute(text("""
        SELECT id, captured_date, mean_ndvi, min_ndvi, max_ndvi,
               ndvi_change_pct, satellite, cloud_cover_pct
        FROM ndvi_records
        WHERE field_id = :fid
        ORDER BY captured_date DESC
        LIMIT 1
    """), {"fid": field_id}).fetchone()

    if not row:
        return {"field_id": field_id, "record": None}

    return {
        "field_id": field_id,
        "record": {
            "id": row.id,
            "captured_date": str(row.captured_date),
            "mean_ndvi": float(row.mean_ndvi) if row.mean_ndvi is not None else None,
            "min_ndvi": float(row.min_ndvi) if row.min_ndvi is not None else None,
            "max_ndvi": float(row.max_ndvi) if row.max_ndvi is not None else None,
            "change_pct": float(row.ndvi_change_pct) if row.ndvi_change_pct is not None else None,
            "satellite": row.satellite,
            "cloud_cover_pct": float(row.cloud_cover_pct) if row.cloud_cover_pct is not None else None,
        }
    }


@router.post("/{field_id}/refresh", status_code=410)
def refresh_ndvi(
    field_id: int,
    current_user=Depends(get_current_active_user),
):
    """Retired: the web process no longer collects satellite data."""
    raise retired(
        "POST /api/ndvi/{field_id}/refresh",
        "Collection runs in the standalone collector (scripts/collect_satellite.py); "
        "read GET /api/ndvi/{field_id}/latest",
        "Synchronous web-process satellite collection is retired; no observation is written.",
    )
