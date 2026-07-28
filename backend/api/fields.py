import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from datetime import date
from services.cache import cache_get, cache_set, cache_delete_pattern
from api.dependencies import (
    require_enterprise_scope,
    get_authorized_field_row,
    get_authorized_field_row_for_write,
    normalize_role,
    is_tenant_role,
)
from api.auth import get_current_active_user
from api.query_bounds import (
    FIELD_LIST_ROW_CAP,
    GEOJSON_FIELD_ROW_CAP,
    ensure_within_row_cap,
    fetch_limit,
)
from schemas.field import FieldCreate, FieldUpdate

router = APIRouter(prefix="/api/fields", tags=["fields"])

# Cache key helpers
_cache_keys = {
    "list": "fields:list:{scope}",
    "geojson": "fields:geojson:{scope}",
}


def _invalidate_field_caches():
    cache_delete_pattern("fields:list:*")
    cache_delete_pattern("fields:geojson:*")
    cache_delete_pattern("field-tiles:*")


def _scope_label(enterprise_id):
    return f"enterprise:{enterprise_id}" if enterprise_id else "all"


@router.get("/geojson/all")
def get_all_fields_geojson(
    enterprise_id: int = None,
    db: Session = Depends(get_db),
    _scope: int = Depends(require_enterprise_scope),
):
    """All fields as GeoJSON FeatureCollection — one SQL query, tenant-scoped."""
    # Enforce tenant scope: if user is tenant-scoped, they can only use their own enterprise_id
    effective_eid = enterprise_id or _scope
    if _scope is not None and enterprise_id is not None and enterprise_id != _scope:
        raise HTTPException(status_code=403, detail="Cannot access other enterprise's fields")
    if _scope is not None and enterprise_id is None:
        effective_eid = _scope

    cache_key = f"fields:geojson:v2:{_scope_label(effective_eid)}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    where = "WHERE f.is_active = true"
    params = {
        "year": date.today().year,
        "row_limit": fetch_limit(GEOJSON_FIELD_ROW_CAP),
    }
    if effective_eid:
        where += " AND f.enterprise_id = :eid"
        params["eid"] = effective_eid

    sql = text(f"""
        SELECT
            f.id,
            f.name,
            f.code,
            f.enterprise_id,
            f.area_ha,
            f.centroid_lat,
            f.centroid_lon,
            f.irrigation_type,
            ST_AsGeoJSON(f.geometry)::json AS geometry,
            e.name AS enterprise_name,
            ct.name_ru AS crop_name,
            n.mean_ndvi AS last_ndvi,
            n.captured_date AS last_ndvi_date,
            n.ndvi_change_pct AS ndvi_change_pct,
            COALESCE(al.alert_count, 0) AS active_alerts,
            COALESCE(al.max_severity, 'ok') AS alert_severity
        FROM fields f
        LEFT JOIN enterprises e ON e.id = f.enterprise_id
        LEFT JOIN crop_seasons cs ON cs.field_id = f.id AND cs.season_year = :year
        LEFT JOIN crop_types ct ON ct.id = cs.crop_type_id
        LEFT JOIN LATERAL (
            SELECT mean_ndvi, captured_date, ndvi_change_pct
            FROM ndvi_records
            WHERE field_id = f.id
            ORDER BY captured_date DESC
            LIMIT 1
        ) n ON true
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*) AS alert_count,
                MAX(CASE WHEN severity = 'critical' THEN 3
                         WHEN severity = 'warning' THEN 2
                         WHEN severity = 'info' THEN 1
                         ELSE 0 END) AS sev_num,
                CASE MAX(CASE WHEN severity = 'critical' THEN 3
                              WHEN severity = 'warning' THEN 2
                              WHEN severity = 'info' THEN 1
                              ELSE 0 END)
                    WHEN 3 THEN 'critical'
                    WHEN 2 THEN 'warning'
                    WHEN 1 THEN 'info'
                    ELSE 'ok' END AS max_severity
            FROM alerts
            WHERE field_id = f.id AND is_active = true
        ) al ON true
        {where}
        ORDER BY f.enterprise_id, f.id
        LIMIT :row_limit
    """)

    rows = db.execute(sql, params).fetchall()
    ensure_within_row_cap(
        rows,
        row_cap=GEOJSON_FIELD_ROW_CAP,
        resource="field_geojson",
    )

    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            "geometry": r.geometry,
            "properties": {
                "id": r.id,
                "name": r.name,
                "code": r.code,
                "enterprise_id": r.enterprise_id,
                "enterprise_name": r.enterprise_name,
                "area_ha": r.area_ha,
                "centroid_lat": r.centroid_lat,
                "centroid_lon": r.centroid_lon,
                "irrigation_type": r.irrigation_type,
                "current_crop": r.crop_name,
                "last_ndvi": float(r.last_ndvi) if r.last_ndvi else None,
                "last_ndvi_date": r.last_ndvi_date.isoformat() if r.last_ndvi_date else None,
                "ndvi_change_pct": float(r.ndvi_change_pct) if r.ndvi_change_pct else None,
                "active_alerts": r.active_alerts,
                "alert_severity": r.alert_severity,
            }
        })

    result = {
        "type": "FeatureCollection",
        "features": features,
        "total": len(features),
    }
    cache_set(cache_key, result, ttl_seconds=300)
    return result


@router.get("/")
def get_fields(
    enterprise_id: int = None,
    db: Session = Depends(get_db),
    _scope: int = Depends(require_enterprise_scope),
):
    """Field list — one SQL query, tenant-scoped."""
    effective_eid = enterprise_id or _scope
    if _scope is not None and enterprise_id is not None and enterprise_id != _scope:
        raise HTTPException(status_code=403, detail="Cannot access other enterprise's fields")
    if _scope is not None and enterprise_id is None:
        effective_eid = _scope

    cache_key = f"fields:list:v2:{_scope_label(effective_eid)}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    where = "WHERE f.is_active = true"
    params = {
        "year": date.today().year,
        "row_limit": fetch_limit(FIELD_LIST_ROW_CAP),
    }
    if effective_eid:
        where += " AND f.enterprise_id = :eid"
        params["eid"] = effective_eid

    sql = text(f"""
        SELECT
            f.id, f.name, f.code, f.enterprise_id, f.area_ha,
            f.centroid_lat, f.centroid_lon, f.irrigation_type,
            ct.name_ru AS crop_name,
            n.mean_ndvi AS last_ndvi,
            n.captured_date AS last_ndvi_date,
            n.ndvi_change_pct,
            COALESCE(al.alert_count, 0) AS active_alerts,
            COALESCE(al.max_severity, 'ok') AS alert_severity
        FROM fields f
        LEFT JOIN crop_seasons cs ON cs.field_id = f.id AND cs.season_year = :year
        LEFT JOIN crop_types ct ON ct.id = cs.crop_type_id
        LEFT JOIN LATERAL (
            SELECT mean_ndvi, captured_date, ndvi_change_pct
            FROM ndvi_records WHERE field_id = f.id
            ORDER BY captured_date DESC LIMIT 1
        ) n ON true
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS alert_count,
                CASE MAX(CASE WHEN severity='critical' THEN 3
                              WHEN severity='warning' THEN 2
                              WHEN severity='info' THEN 1 ELSE 0 END)
                    WHEN 3 THEN 'critical' WHEN 2 THEN 'warning'
                    WHEN 1 THEN 'info' ELSE 'ok' END AS max_severity
            FROM alerts WHERE field_id = f.id AND is_active = true
        ) al ON true
        {where}
        ORDER BY f.enterprise_id, f.id
        LIMIT :row_limit
    """)

    rows = db.execute(sql, params).fetchall()
    ensure_within_row_cap(
        rows,
        row_cap=FIELD_LIST_ROW_CAP,
        resource="fields",
    )

    result = [
        {
            "id": r.id,
            "name": r.name,
            "code": r.code,
            "enterprise_id": r.enterprise_id,
            "area_ha": r.area_ha,
            "current_crop": r.crop_name,
            "last_ndvi": float(r.last_ndvi) if r.last_ndvi else None,
            "last_ndvi_date": r.last_ndvi_date.isoformat() if r.last_ndvi_date else None,
            "ndvi_change_pct": float(r.ndvi_change_pct) if r.ndvi_change_pct else None,
            "active_alerts": r.active_alerts,
            "alert_severity": r.alert_severity,
        }
        for r in rows
    ]
    cache_set(cache_key, result, ttl_seconds=300)
    return result


@router.get("/{field_id}")
def get_field(
    field_id: int,
    db: Session = Depends(get_db),
    _auth_field=Depends(get_authorized_field_row),
):
    """Detailed field info."""
    # _auth_field is unused here, we still do the SQL since get_authorized_field_row
    # only returns a subset of columns. Re-fetch with full detail.
    r = db.execute(text("""
        SELECT
            f.id, f.name, f.code, f.enterprise_id, f.area_ha,
            f.centroid_lat, f.centroid_lon, f.irrigation_type, f.notes,
            ST_AsGeoJSON(f.geometry)::json AS geometry,
            e.name AS enterprise_name,
            ct.name_ru AS crop_name, ct.code AS crop_code,
            cs.season_year, cs.planting_date, cs.variety,
            n.mean_ndvi, n.min_ndvi, n.max_ndvi, n.std_ndvi,
            n.captured_date AS ndvi_date, n.ndvi_change_pct,
            COALESCE(al.alert_count, 0) AS active_alerts,
            COALESCE(al.max_severity, 'ok') AS alert_severity
        FROM fields f
        LEFT JOIN enterprises e ON e.id = f.enterprise_id
        LEFT JOIN crop_seasons cs ON cs.field_id = f.id
            AND cs.season_year = :year
        LEFT JOIN crop_types ct ON ct.id = cs.crop_type_id
        LEFT JOIN LATERAL (
            SELECT mean_ndvi, min_ndvi, max_ndvi, std_ndvi,
                   captured_date, ndvi_change_pct
            FROM ndvi_records WHERE field_id = f.id
            ORDER BY captured_date DESC LIMIT 1
        ) n ON true
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS alert_count,
                CASE MAX(CASE WHEN severity='critical' THEN 3
                              WHEN severity='warning' THEN 2
                              WHEN severity='info' THEN 1 ELSE 0 END)
                    WHEN 3 THEN 'critical' WHEN 2 THEN 'warning'
                    WHEN 1 THEN 'info' ELSE 'ok' END AS max_severity
            FROM alerts WHERE field_id = f.id AND is_active = true
        ) al ON true
        WHERE f.id = :fid
    """), {"fid": field_id, "year": date.today().year}).fetchone()

    if not r:
        raise HTTPException(status_code=404, detail="Field not found")

    return {
        "type": "Feature",
        "geometry": r.geometry,
        "properties": {
            "id": r.id,
            "name": r.name,
            "code": r.code,
            "enterprise_id": r.enterprise_id,
            "enterprise_name": r.enterprise_name,
            "area_ha": r.area_ha,
            "centroid_lat": r.centroid_lat,
            "centroid_lon": r.centroid_lon,
            "irrigation_type": r.irrigation_type,
            "notes": r.notes,
            "current_crop": r.crop_name,
            "crop_code": r.crop_code,
            "season_year": r.season_year,
            "planting_date": r.planting_date.isoformat() if r.planting_date else None,
            "variety": r.variety,
            "last_ndvi": float(r.mean_ndvi) if r.mean_ndvi else None,
            "last_ndvi_date": r.ndvi_date.isoformat() if r.ndvi_date else None,
            "ndvi_change_pct": float(r.ndvi_change_pct) if r.ndvi_change_pct else None,
            "active_alerts": r.active_alerts,
            "alert_severity": r.alert_severity,
        }
    }


@router.get("/{field_id}/geojson")
def get_field_geojson(
    field_id: int,
    db: Session = Depends(get_db),
    _auth_field=Depends(get_authorized_field_row),
):
    return get_field(field_id, db, _auth_field)


@router.post("/")
def create_field(
    data: FieldCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
    _scope: int = Depends(require_enterprise_scope),
):
    """Create a new field with PostGIS geometry validation."""
    role = normalize_role(current_user)

    # Block viewers from creating
    if role == "viewer":
        raise HTTPException(status_code=403, detail="Viewers cannot create fields")

    # Tenant scoping: agronomist must create within their own enterprise
    if is_tenant_role(role):
        if _scope is None:
            raise HTTPException(status_code=403, detail="User has no enterprise_id")
        if data.enterprise_id != _scope:
            raise HTTPException(status_code=403, detail="Cannot create field for another enterprise")

    # Validate geometry via PostGIS CTE: fix, extract polygons, validate
    geojson = data.geometry
    result = db.execute(text("""
        WITH
        geom_in AS (
            SELECT ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326) AS g
        ),
        fixed AS (
            SELECT ST_MakeValid(g) AS g FROM geom_in
        ),
        extracted AS (
            SELECT ST_CollectionExtract(g, 3) AS g FROM fixed
        )
        SELECT
            GeometryType(g) AS geom_type,
            ST_IsValid(g) AS is_valid,
            ST_IsEmpty(g) AS is_empty,
            ST_Area(ST_Transform(g, 32639)) / 10000.0 AS area_ha,
            ST_AsGeoJSON(g) AS sanitized_geojson,
            ST_X(ST_Centroid(g)) AS centroid_lon,
            ST_Y(ST_Centroid(g)) AS centroid_lat
        FROM extracted
    """), {"geojson": json.dumps(geojson)}).fetchone()

    if not result or result.is_empty:
        raise HTTPException(status_code=400, detail="Geometry resulted in empty shape after processing")
    if not result.is_valid:
        raise HTTPException(status_code=400, detail="Invalid geometry after processing")
    if result.geom_type != "POLYGON":
        raise HTTPException(status_code=400, detail=f"Geometry must be Polygon, got {result.geom_type}")
    area_ha = float(result.area_ha)
    if area_ha < 0.1 or area_ha > 2000.0:
        raise HTTPException(status_code=400, detail=f"Unrealistic area: {area_ha:.2f} ha (allowed 0.1-2000)")

    # Insert the field
    insert_sql = text("""
        INSERT INTO fields (enterprise_id, name, code, geometry, area_ha,
                            centroid_lat, centroid_lon, irrigation_type,
                            soil_type, notes, is_active, created_at, updated_at)
        VALUES (:eid, :name, :code,
                ST_SetSRID(ST_GeomFromGeoJSON(:sanitized_geojson), 4326),
                :area_ha, :centroid_lat, :centroid_lon,
                :irrigation_type, :soil_type, :notes, true, NOW(), NOW())
        RETURNING id, name, code, enterprise_id, area_ha,
                  centroid_lat, centroid_lon, irrigation_type,
                  soil_type, notes, is_active, created_at, updated_at
    """)
    row = db.execute(insert_sql, {
        "eid": data.enterprise_id,
        "name": data.name,
        "code": data.code,
        "sanitized_geojson": result.sanitized_geojson,
        "area_ha": data.area_ha or area_ha,
        "centroid_lat": result.centroid_lat,
        "centroid_lon": result.centroid_lon,
        "irrigation_type": data.irrigation_type,
        "soil_type": data.soil_type,
        "notes": data.notes,
    }).fetchone()
    db.commit()

    _invalidate_field_caches()

    return {
        "id": row.id,
        "name": row.name,
        "code": row.code,
        "enterprise_id": row.enterprise_id,
        "area_ha": row.area_ha,
    }


@router.put("/{field_id}")
def update_field(
    field_id: int,
    data: FieldUpdate,
    db: Session = Depends(get_db),
    _auth_field=Depends(get_authorized_field_row_for_write),
    current_user=Depends(get_current_active_user),
):
    """Update an existing field."""
    role = normalize_role(current_user)

    # Build dynamic UPDATE
    set_clauses = []
    params = {"fid": field_id}

    if data.name is not None:
        set_clauses.append("name = :name")
        params["name"] = data.name
    if data.code is not None:
        set_clauses.append("code = :code")
        params["code"] = data.code
    if data.irrigation_type is not None:
        set_clauses.append("irrigation_type = :irrigation_type")
        params["irrigation_type"] = data.irrigation_type
    if data.soil_type is not None:
        set_clauses.append("soil_type = :soil_type")
        params["soil_type"] = data.soil_type
    if data.notes is not None:
        set_clauses.append("notes = :notes")
        params["notes"] = data.notes
    if data.is_active is not None:
        set_clauses.append("is_active = :is_active")
        params["is_active"] = data.is_active
    if data.area_ha is not None and data.geometry is None:
        set_clauses.append("area_ha = :area_ha")
        params["area_ha"] = data.area_ha
    if data.geometry is not None:
        # Validate geometry via PostGIS CTE
        geojson = data.geometry
        result = db.execute(text("""
            WITH
            geom_in AS (
                SELECT ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326) AS g
            ),
            fixed AS (
                SELECT ST_MakeValid(g) AS g FROM geom_in
            ),
            extracted AS (
                SELECT ST_CollectionExtract(g, 3) AS g FROM fixed
            )
            SELECT
                GeometryType(g) AS geom_type,
                ST_IsValid(g) AS is_valid,
                ST_IsEmpty(g) AS is_empty,
                ST_Area(ST_Transform(g, 32639)) / 10000.0 AS area_ha,
                ST_X(ST_Centroid(g)) AS centroid_lon,
                ST_Y(ST_Centroid(g)) AS centroid_lat,
                ST_AsGeoJSON(g) AS sanitized_geojson
            FROM extracted
        """), {"geojson": json.dumps(geojson)}).fetchone()

        if not result or result.is_empty:
            raise HTTPException(status_code=400, detail="Geometry resulted in empty shape after processing")
        if not result.is_valid:
            raise HTTPException(status_code=400, detail="Invalid geometry after processing")
        if result.geom_type != "POLYGON":
            raise HTTPException(status_code=400, detail=f"Geometry must be Polygon, got {result.geom_type}")
        area_ha = float(result.area_ha)
        if area_ha < 0.1 or area_ha > 2000.0:
            raise HTTPException(status_code=400, detail=f"Unrealistic area: {area_ha:.2f} ha (allowed 0.1-2000)")

        set_clauses.append("geometry = ST_SetSRID(ST_GeomFromGeoJSON(:sanitized_geojson), 4326)")
        set_clauses.append("area_ha = :area_ha")
        set_clauses.append("centroid_lat = :centroid_lat")
        set_clauses.append("centroid_lon = :centroid_lon")
        params["sanitized_geojson"] = result.sanitized_geojson
        params["area_ha"] = area_ha
        params["centroid_lat"] = result.centroid_lat
        params["centroid_lon"] = result.centroid_lon

    if not set_clauses:
        raise HTTPException(status_code=400, detail="No fields to update")

    set_clauses.append("updated_at = NOW()")
    sql = text(f"UPDATE fields SET {', '.join(set_clauses)} WHERE id = :fid")

    db.execute(sql, params)
    db.commit()

    _invalidate_field_caches()

    return {"status": "ok", "field_id": field_id}


@router.post("/{field_id}/season")
def set_field_season(
    field_id: int,
    data: dict,
    db: Session = Depends(get_db),
    _auth_field=Depends(get_authorized_field_row_for_write),
    current_user=Depends(get_current_active_user),
):
    """Set or replace the crop season for a field."""
    role = normalize_role(current_user)

    season_year = data.get("season_year", date.today().year)

    # Use explicit SQL instead of ORM lazy traversal
    existing = db.execute(
        text("SELECT id FROM crop_seasons WHERE field_id = :fid AND season_year = :year"),
        {"fid": field_id, "year": season_year},
    ).fetchone()
    if existing:
        db.execute(
            text("DELETE FROM crop_seasons WHERE id = :sid"),
            {"sid": existing.id},
        )

    db.execute(
        text("""
            INSERT INTO crop_seasons (field_id, crop_type_id, season_year, planting_date, variety, created_at)
            VALUES (:fid, :crop_type_id, :year, :planting_date, :variety, NOW())
        """),
        {
            "fid": field_id,
            "crop_type_id": data.get("crop_type_id"),
            "year": season_year,
            "planting_date": data.get("planting_date"),
            "variety": data.get("variety"),
        },
    )
    db.commit()

    _invalidate_field_caches()

    return {"status": "ok", "field_id": field_id, "season_year": season_year}
