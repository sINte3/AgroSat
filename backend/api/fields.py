from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from models.field import Field, CropSeason
from models.crop import CropType
from models.monitoring import NDVIRecord, Alert
from datetime import date
from services.cache import cache_get, cache_set

router = APIRouter(prefix="/api/fields", tags=["fields"])


@router.get("/geojson/all")
def get_all_fields_geojson(enterprise_id: int = None, db: Session = Depends(get_db)):
    """Все поля как GeoJSON FeatureCollection — один SQL запрос."""
    cache_key = f"fields:geojson:{enterprise_id or 'all'}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    where = "WHERE f.is_active = true"
    params = {"year": date.today().year}
    if enterprise_id:
        where += " AND f.enterprise_id = :eid"
        params["eid"] = enterprise_id

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
    """)

    rows = db.execute(sql, params).fetchall()

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
def get_fields(enterprise_id: int = None, db: Session = Depends(get_db)):
    """Список полей — один SQL запрос."""
    cache_key = f"fields:list:{enterprise_id or 'all'}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    where = "WHERE f.is_active = true"
    params = {"year": date.today().year}
    if enterprise_id:
        where += " AND f.enterprise_id = :eid"
        params["eid"] = enterprise_id

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
    """)

    rows = db.execute(sql, params).fetchall()

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
def get_field(field_id: int, db: Session = Depends(get_db)):
    """Детальная информация о поле."""
    sql = text("""
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
    """)

    r = db.execute(sql, {"fid": field_id, "year": date.today().year}).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Поле не найдено")

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
def get_field_geojson(field_id: int, db: Session = Depends(get_db)):
    return get_field(field_id, db)


@router.post("/{field_id}/season")
def set_field_season(field_id: int, data: dict, db: Session = Depends(get_db)):
    f = db.query(Field).filter(Field.id == field_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Поле не найдено")

    season_year = data.get("season_year", date.today().year)
    existing = db.query(CropSeason).filter(
        CropSeason.field_id == field_id,
        CropSeason.season_year == season_year
    ).first()
    if existing:
        db.delete(existing)

    season = CropSeason(
        field_id=field_id,
        crop_type_id=data.get("crop_type_id"),
        season_year=season_year,
        planting_date=data.get("planting_date"),
        variety=data.get("variety"),
    )
    db.add(season)
    db.commit()
    return {"status": "ok", "field_id": field_id, "season_year": season_year}
