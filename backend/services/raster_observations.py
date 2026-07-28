"""Explicit tenant-scoped observation lookup shared by raster adapters."""

import math
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.dependencies import ALLOWED_ROLES, is_tenant_role, normalize_role
from models.monitoring import User


def local_today() -> date:
    return datetime.now(ZoneInfo("Asia/Tashkent")).date()


def validate_bbox(values: Any) -> list[float]:
    try:
        bbox = [float(value) for value in values]
    except (TypeError, ValueError):
        raise ValueError("Field geometry is unavailable") from None
    if len(bbox) != 4 or not all(math.isfinite(value) for value in bbox):
        raise ValueError("Field geometry is unavailable")
    west, south, east, north = bbox
    if west >= east or south >= north:
        raise ValueError("Field geometry is unavailable")
    return bbox


def scoped_ndvi_observation_row(
    db: Session,
    field_id: int,
    current_user: User,
    date_to: date,
    *,
    exact: bool,
):
    """Return one accepted Sentinel-2 NDVI row with scope inside the SQL."""
    role = normalize_role(current_user)
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Unknown role")
    date_param = "observation_date" if exact else "date_to"
    params = {"field_id": field_id, date_param: date_to}
    tenant_clause = ""
    if is_tenant_role(role):
        if current_user.enterprise_id is None:
            raise HTTPException(status_code=403, detail="User has no enterprise_id")
        tenant_clause = " AND f.enterprise_id = :enterprise_id"
        params["enterprise_id"] = current_user.enterprise_id
    date_clause = f"n.captured_date {'=' if exact else '<='} :{date_param}"
    geometry_select = "ST_AsGeoJSON(f.geometry)::json AS geometry," if exact else ""
    row = db.execute(
        text(
            f"""
            SELECT f.id AS field_id,
                   f.enterprise_id AS enterprise_id,
                   n.id AS observation_id,
                   n.captured_date AS observation_date,
                   n.satellite AS satellite,
                   {geometry_select}
                   ST_XMin(Box2D(f.geometry)) AS west,
                   ST_YMin(Box2D(f.geometry)) AS south,
                   ST_XMax(Box2D(f.geometry)) AS east,
                   ST_YMax(Box2D(f.geometry)) AS north
            FROM fields f
            JOIN ndvi_records n ON n.field_id = f.id
            WHERE f.id = :field_id{tenant_clause}
              AND {date_clause}
              AND n.mean_ndvi IS NOT NULL
              AND n.satellite = 'Sentinel-2'
            ORDER BY n.captured_date DESC
            LIMIT 1
            """
        ),
        params,
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Field not found")
    return row
