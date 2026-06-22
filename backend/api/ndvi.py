from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from datetime import datetime, timedelta
import logging

from api.dependencies import (
    get_authorized_field_row,
    get_authorized_field_row_for_write,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ndvi", tags=["ndvi"])


@router.get("/{field_id}/history")
async def get_ndvi_history(
    field_id: int,
    days: int = 90,
    include_cloudy: bool = False,
    db: Session = Depends(get_db),
    _auth_field=Depends(get_authorized_field_row),
):
    """NDVI history for a field over N days. Returns empty list if no data.
    By default excludes cloudy captures (cloud_cover_pct > 30)."""
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
    """), {"fid": field_id, "since": since_date}).fetchall()

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
async def get_ndvi_latest(
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


@router.post("/{field_id}/refresh")
async def refresh_ndvi(
    field_id: int,
    db: Session = Depends(get_db),
    _auth_field=Depends(get_authorized_field_row_for_write),
):
    """Force refresh NDVI for a field now. Admin/manager/agronomist only."""
    try:
        from services.satellite import satellite_service, validate_ndvi_quality

        geom_row = db.execute(
            text("SELECT ST_AsText(geometry) AS geometry_wkt FROM fields WHERE id = :fid"),
            {"fid": field_id},
        ).fetchone()
        if not geom_row or not geom_row.geometry_wkt:
            raise HTTPException(status_code=404, detail="Field geometry not found")

        today = datetime.now().date()
        ndvi_data = satellite_service.get_ndvi_stats(
            geometry_wkt=geom_row.geometry_wkt,
            date_from=today - timedelta(days=10),
            date_to=today,
        )

        if not ndvi_data:
            return {
                "status": "no_data",
                "message": "Satellite data unavailable or cloud cover too high",
            }

        is_valid, reason = validate_ndvi_quality(
            mean_ndvi=ndvi_data["mean_ndvi"],
            cloud_cover_pct=ndvi_data.get("cloud_cover_pct"),
            min_ndvi=ndvi_data.get("min_ndvi"),
            max_ndvi=ndvi_data.get("max_ndvi"),
            field_name=_auth_field.name,
        )
        if not is_valid:
            return {
                "status": "rejected",
                "message": "NDVI data rejected by quality gate",
                "reason": reason,
            }

        captured_date = datetime.fromisoformat(str(ndvi_data["captured_date"])).date()

        existing = db.execute(
            text("""
                SELECT id, mean_ndvi
                FROM ndvi_records
                WHERE field_id = :fid AND captured_date = :captured_date
                LIMIT 1
            """),
            {"fid": field_id, "captured_date": captured_date},
        ).fetchone()

        if existing:
            return {
                "status": "exists",
                "message": "NDVI record for this date already exists",
                "record_id": existing.id,
                "ndvi": float(existing.mean_ndvi) if existing.mean_ndvi is not None else None,
            }

        prev = db.execute(
            text("""
                SELECT mean_ndvi
                FROM ndvi_records
                WHERE field_id = :fid
                ORDER BY captured_date DESC
                LIMIT 1
            """),
            {"fid": field_id},
        ).fetchone()

        ndvi_change = None
        ndvi_change_pct = None
        if prev and prev.mean_ndvi is not None and prev.mean_ndvi != 0:
            ndvi_change = float(ndvi_data["mean_ndvi"]) - float(prev.mean_ndvi)
            ndvi_change_pct = (ndvi_change / float(prev.mean_ndvi)) * 100

        row = db.execute(
            text("""
                INSERT INTO ndvi_records (
                    field_id,
                    captured_date,
                    mean_ndvi,
                    min_ndvi,
                    max_ndvi,
                    std_ndvi,
                    p10_ndvi,
                    p90_ndvi,
                    cloud_cover_pct,
                    valid_pixels_pct,
                    satellite,
                    ndvi_change,
                    ndvi_change_pct,
                    processed_at
                )
                VALUES (
                    :field_id,
                    :captured_date,
                    :mean_ndvi,
                    :min_ndvi,
                    :max_ndvi,
                    :std_ndvi,
                    :p10_ndvi,
                    :p90_ndvi,
                    :cloud_cover_pct,
                    :valid_pixels_pct,
                    :satellite,
                    :ndvi_change,
                    :ndvi_change_pct,
                    NOW()
                )
                RETURNING id, mean_ndvi
            """),
            {
                "field_id": field_id,
                "captured_date": captured_date,
                "mean_ndvi": ndvi_data.get("mean_ndvi"),
                "min_ndvi": ndvi_data.get("min_ndvi"),
                "max_ndvi": ndvi_data.get("max_ndvi"),
                "std_ndvi": ndvi_data.get("std_ndvi"),
                "p10_ndvi": ndvi_data.get("p10_ndvi"),
                "p90_ndvi": ndvi_data.get("p90_ndvi"),
                "cloud_cover_pct": ndvi_data.get("cloud_cover_pct"),
                "valid_pixels_pct": ndvi_data.get("valid_pixels_pct"),
                "satellite": ndvi_data.get("satellite", "Sentinel-2"),
                "ndvi_change": ndvi_change,
                "ndvi_change_pct": ndvi_change_pct,
            },
        ).fetchone()
        db.commit()

        return {
            "status": "ok",
            "message": "NDVI updated",
            "record_id": row.id,
            "ndvi": float(row.mean_ndvi) if row.mean_ndvi is not None else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"NDVI refresh error for field {field_id}: {e}")
        raise HTTPException(status_code=500, detail="NDVI refresh failed")
