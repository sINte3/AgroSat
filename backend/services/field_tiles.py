"""Tenant-safe, bounded PostGIS MVT delivery for AgroSat fields."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.dependencies import ALLOWED_ROLES, is_tenant_role, normalize_role
from models.monitoring import User
from services.cache import cache_get, cache_get_binary, cache_set, cache_set_binary


SCHEMA_VERSION = "task209_field_mvt_v1"
SOURCE_LAYER = "fields"
MIN_ZOOM = 3
MAX_ZOOM = 18
EXTENT = 4096
BUFFER = 64
CACHE_TTL_SECONDS = 300
PROPERTY_NAMES = (
    "id",
    "name",
    "code",
    "enterprise_id",
    "enterprise_name",
    "area_ha",
    "centroid_lat",
    "centroid_lon",
    "irrigation_type",
    "current_crop",
    "last_ndvi",
    "last_ndvi_date",
    "ndvi_change_pct",
    "active_alerts",
    "alert_severity",
)


@dataclass(frozen=True)
class TileScope:
    role: str
    enterprise_id: int | None


def resolve_scope(user: User, requested_enterprise_id: int | None = None) -> TileScope:
    role = normalize_role(user)
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Unknown role")
    if role == "admin":
        return TileScope(role=role, enterprise_id=requested_enterprise_id)
    if not is_tenant_role(role) or user.enterprise_id is None:
        raise HTTPException(status_code=403, detail="User has no enterprise_id")
    if (
        requested_enterprise_id is not None
        and requested_enterprise_id != user.enterprise_id
    ):
        raise HTTPException(
            status_code=403,
            detail="Cannot access another enterprise",
        )
    return TileScope(role=role, enterprise_id=user.enterprise_id)


def validate_coordinates(z: int, x: int, y: int) -> None:
    if not MIN_ZOOM <= z <= MAX_ZOOM:
        raise HTTPException(status_code=422, detail="Unsupported tile zoom")
    limit = 1 << z
    if x < 0 or y < 0 or x >= limit or y >= limit:
        raise HTTPException(status_code=422, detail="Invalid tile coordinates")


def simplification_tolerance_m(z: int) -> float:
    if z <= 7:
        return 150.0
    if z <= 10:
        return 40.0
    if z <= 13:
        return 10.0
    return 0.5


def _scope_label(enterprise_id: int | None) -> str:
    return f"enterprise:{enterprise_id}" if enterprise_id is not None else "all"


def metadata_cache_key(scope: TileScope) -> str:
    return f"field-tiles:{SCHEMA_VERSION}:metadata:{_scope_label(scope.enterprise_id)}"


def tile_cache_key(scope: TileScope, z: int, x: int, y: int) -> str:
    return (
        f"field-tiles:{SCHEMA_VERSION}:tile:"
        f"{_scope_label(scope.enterprise_id)}:{z}:{x}:{y}"
    )


def _tenant_clause(scope: TileScope) -> tuple[str, dict[str, Any]]:
    if scope.enterprise_id is None:
        return "", {}
    return " AND f.enterprise_id = :enterprise_id", {
        "enterprise_id": scope.enterprise_id,
    }


def _metadata_query(scope: TileScope):
    tenant_clause, params = _tenant_clause(scope)
    statement = text(
        f"""
        WITH scoped_fields AS (
            SELECT f.geometry
            FROM fields f
            WHERE f.is_active = true{tenant_clause}
        )
        SELECT
            COUNT(*)::integer AS field_count,
            CASE WHEN COUNT(*) = 0 THEN NULL
                 ELSE ARRAY[
                     ST_XMin(ST_Extent(geometry)),
                     ST_YMin(ST_Extent(geometry)),
                     ST_XMax(ST_Extent(geometry)),
                     ST_YMax(ST_Extent(geometry))
                 ] END AS bounds
        FROM scoped_fields
        """
    )
    return statement, params


def _tile_query(scope: TileScope, z: int, x: int, y: int):
    tenant_clause, tenant_params = _tenant_clause(scope)
    params = {
        "z": z,
        "x": x,
        "y": y,
        "season_year": datetime.now(ZoneInfo("Asia/Tashkent")).year,
        "tolerance_m": simplification_tolerance_m(z),
        **tenant_params,
    }
    statement = text(
        f"""
        WITH bounds AS (
            SELECT
                ST_TileEnvelope(:z, :x, :y) AS geom_3857,
                ST_Transform(ST_TileEnvelope(:z, :x, :y), 4326) AS geom_4326
        ),
        candidates AS (
            SELECT
                f.id,
                f.name,
                f.code,
                f.enterprise_id,
                e.name AS enterprise_name,
                f.area_ha,
                f.centroid_lat,
                f.centroid_lon,
                f.irrigation_type,
                ct.name_ru AS current_crop,
                n.mean_ndvi::double precision AS last_ndvi,
                n.captured_date::text AS last_ndvi_date,
                n.ndvi_change_pct::double precision AS ndvi_change_pct,
                COALESCE(al.active_alerts, 0)::integer AS active_alerts,
                COALESCE(al.alert_severity, 'ok') AS alert_severity,
                b.geom_3857,
                ST_Transform(f.geometry, 3857) AS geometry_3857
            FROM fields f
            CROSS JOIN bounds b
            LEFT JOIN enterprises e ON e.id = f.enterprise_id
            LEFT JOIN crop_seasons cs
              ON cs.field_id = f.id AND cs.season_year = :season_year
            LEFT JOIN crop_types ct ON ct.id = cs.crop_type_id
            LEFT JOIN LATERAL (
                SELECT nr.mean_ndvi, nr.captured_date, nr.ndvi_change_pct
                FROM ndvi_records nr
                WHERE nr.field_id = f.id
                ORDER BY nr.captured_date DESC
                LIMIT 1
            ) n ON true
            LEFT JOIN LATERAL (
                SELECT
                    COUNT(*) AS active_alerts,
                    CASE MAX(
                        CASE a.severity
                            WHEN 'critical' THEN 3
                            WHEN 'warning' THEN 2
                            WHEN 'info' THEN 1
                            ELSE 0
                        END
                    )
                        WHEN 3 THEN 'critical'
                        WHEN 2 THEN 'warning'
                        WHEN 1 THEN 'info'
                        ELSE 'ok'
                    END AS alert_severity
                FROM alerts a
                WHERE a.field_id = f.id AND a.is_active = true
            ) al ON true
            WHERE f.is_active = true{tenant_clause}
              AND f.geometry && b.geom_4326
              AND ST_Intersects(f.geometry, b.geom_4326)
        ),
        tile_rows AS (
            SELECT
                id,
                name,
                code,
                enterprise_id,
                enterprise_name,
                area_ha,
                centroid_lat,
                centroid_lon,
                irrigation_type,
                current_crop,
                last_ndvi,
                last_ndvi_date,
                ndvi_change_pct,
                active_alerts,
                alert_severity,
                ST_AsMVTGeom(
                    ST_SimplifyPreserveTopology(
                        geometry_3857,
                        :tolerance_m
                    ),
                    geom_3857,
                    {EXTENT},
                    {BUFFER},
                    true
                ) AS geom
            FROM candidates
        )
        SELECT ST_AsMVT(
            tile_rows,
            '{SOURCE_LAYER}',
            {EXTENT},
            'geom',
            'id'
        ) AS tile
        FROM tile_rows
        WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom)
        """
    )
    return statement, params


def get_metadata(db: Session, scope: TileScope) -> dict[str, Any]:
    key = metadata_cache_key(scope)
    cached = cache_get(key)
    if isinstance(cached, dict) and cached.get("schema_version") == SCHEMA_VERSION:
        return cached
    statement, params = _metadata_query(scope)
    row = db.execute(statement, params).mappings().first()
    field_count = int(row["field_count"]) if row else 0
    raw_bounds = row["bounds"] if row else None
    bounds = [float(value) for value in raw_bounds] if raw_bounds else None
    enterprise_query = (
        f"?enterprise_id={scope.enterprise_id}"
        if scope.enterprise_id is not None
        else ""
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_layer": SOURCE_LAYER,
        "min_zoom": MIN_ZOOM,
        "max_zoom": MAX_ZOOM,
        "extent": EXTENT,
        "buffer": BUFFER,
        "scope": {
            "role": scope.role,
            "enterprise_id": scope.enterprise_id,
        },
        "field_count": field_count,
        "bounds": bounds,
        "properties": list(PROPERTY_NAMES),
        "tile_template": (
            "/api/field-tiles/{z}/{x}/{y}.mvt"
            f"{enterprise_query}"
        ),
    }
    cache_set(key, payload, ttl_seconds=CACHE_TTL_SECONDS)
    return payload


def get_tile(
    db: Session,
    scope: TileScope,
    z: int,
    x: int,
    y: int,
) -> tuple[bytes, str, str]:
    validate_coordinates(z, x, y)
    key = tile_cache_key(scope, z, x, y)
    cached = cache_get_binary(key)
    if cached is not None:
        tile = cached
        cache_state = "HIT"
    else:
        statement, params = _tile_query(scope, z, x, y)
        value = db.execute(statement, params).scalar_one_or_none()
        tile = bytes(value) if value else b""
        cache_state = (
            "MISS"
            if tile and cache_set_binary(key, tile, CACHE_TTL_SECONDS)
            else "BYPASS"
        )
    etag = f'"{hashlib.sha256(tile).hexdigest()}"'
    return tile, cache_state, etag
