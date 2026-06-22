"""
Pydantic схемы для полей (Field).
"""

import math
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError


class SeasonCreate(BaseModel):
    """Добавление/обновление сезона посева."""
    crop_type_id: int
    season_year: int = Field(..., ge=2020, le=2100)
    variety: Optional[str] = None
    planting_date: Optional[str] = None
    expected_harvest_date: Optional[str] = None
    actual_harvest_date: Optional[str] = None
    planned_yield_tha: Optional[float] = None
    actual_yield_tha: Optional[float] = None
    notes: Optional[str] = None


class SeasonResponse(BaseModel):
    """Ответ с данными сезона."""
    id: int
    season_year: int
    crop_type_id: int
    crop_name: Optional[str] = None
    variety: Optional[str] = None
    planting_date: Optional[str] = None
    expected_harvest_date: Optional[str] = None
    actual_harvest_date: Optional[str] = None
    planned_yield_tha: Optional[float] = None
    actual_yield_tha: Optional[float] = None

    class Config:
        from_attributes = True


# Bukhara region coordinate bounds
BUKHARA_LON_MIN = 63.0
BUKHARA_LON_MAX = 65.5
BUKHARA_LAT_MIN = 38.5
BUKHARA_LAT_MAX = 40.5
MAX_VERTICES = 2000


def _validate_geojson_polygon(geometry: dict) -> dict:
    """Validate a GeoJSON geometry dict is a Polygon within Bukhara bounds."""
    if not isinstance(geometry, dict):
        raise PydanticCustomError("invalid_type", "Geometry must be a dict")

    geom_type = geometry.get("type")
    if geom_type != "Polygon":
        raise PydanticCustomError(
            "invalid_geometry_type",
            f"Only Polygon geometry is accepted, got {geom_type}",
        )

    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) == 0:
        raise PydanticCustomError("invalid_coordinates", "Polygon must have a non-empty coordinates array")

    total_vertices = 0
    for ring_idx, ring in enumerate(coordinates):
        if not isinstance(ring, list) or len(ring) < 4:
            raise PydanticCustomError(
                "invalid_ring",
                f"Ring {ring_idx} must have at least 4 points, got {len(ring) if isinstance(ring, list) else 'invalid'}",
            )

        # Check ring is closed (first point == last point)
        first = ring[0]
        last = ring[-1]
        if not isinstance(first, list) or len(first) < 2 or not isinstance(last, list) or len(last) < 2:
            raise PydanticCustomError("invalid_point", "Each point must be [lon, lat]")
        if first[0] != last[0] or first[1] != last[1]:
            raise PydanticCustomError("open_ring", f"Ring {ring_idx} is not closed")

        for pt_idx, pt in enumerate(ring):
            if not isinstance(pt, list) or len(pt) < 2:
                raise PydanticCustomError("invalid_point", f"Point {pt_idx} in ring {ring_idx} must be [lon, lat]")
            lon, lat = pt[0], pt[1]
            if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
                raise PydanticCustomError("non_numeric", f"Coordinate values must be numeric at point {pt_idx} in ring {ring_idx}")
            if math.isnan(lon) or math.isinf(lon) or math.isnan(lat) or math.isinf(lat):
                raise PydanticCustomError("nan_inf", f"NaN/Infinity coordinates are not allowed at point {pt_idx} in ring {ring_idx}")
            total_vertices += 1

    # Validate Bukhara bounds (check at least one ring's exterior points)
    exterior = coordinates[0]
    for pt_idx, pt in enumerate(exterior):
        lon, lat = pt[0], pt[1]
        if not (BUKHARA_LON_MIN <= lon <= BUKHARA_LON_MAX):
            raise PydanticCustomError(
                "out_of_bounds",
                f"Longitude {lon} at point {pt_idx} outside Bukhara bounds ({BUKHARA_LON_MIN}-{BUKHARA_LON_MAX})",
            )
        if not (BUKHARA_LAT_MIN <= lat <= BUKHARA_LAT_MAX):
            raise PydanticCustomError(
                "out_of_bounds",
                f"Latitude {lat} at point {pt_idx} outside Bukhara bounds ({BUKHARA_LAT_MIN}-{BUKHARA_LAT_MAX})",
            )

    if total_vertices > MAX_VERTICES:
        raise PydanticCustomError(
            "too_many_vertices",
            f"Polygon has {total_vertices} vertices, maximum allowed is {MAX_VERTICES}",
        )

    return geometry


class FieldCreate(BaseModel):
    """Create a new field."""
    enterprise_id: int
    name: str = Field(..., max_length=255)
    code: Optional[str] = Field(None, max_length=50)
    geometry: dict = Field(..., description="GeoJSON Polygon")
    area_ha: Optional[float] = None
    irrigation_type: Optional[str] = Field(None, max_length=50)
    soil_type: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None

    _validate_geometry = field_validator("geometry")(_validate_geojson_polygon)


def _validate_geojson_polygon_opt(geometry: Any) -> Any:
    """Validate GeoJSON Polygon, allowing None (for partial updates)."""
    if geometry is None:
        return geometry
    return _validate_geojson_polygon(geometry)


class FieldUpdate(BaseModel):
    """Field update. All fields optional."""
    name: Optional[str] = Field(None, max_length=255)
    code: Optional[str] = Field(None, max_length=50)
    geometry: Optional[dict] = None
    area_ha: Optional[float] = None
    irrigation_type: Optional[str] = Field(None, max_length=50)
    soil_type: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None
    is_active: Optional[bool] = None

    _validate_geometry = field_validator("geometry")(_validate_geojson_polygon_opt)


class AlertBrief(BaseModel):
    """Краткая информация об алерте."""
    id: int
    alert_type: str
    severity: str
    title: str
    triggered_at: datetime
    is_active: bool

    class Config:
        from_attributes = True


class FieldResponse(BaseModel):
    """Полный ответ с данными поля."""
    id: int
    enterprise_id: int
    name: str
    code: Optional[str] = None
    area_ha: Optional[float] = None
    centroid_lat: Optional[float] = None
    centroid_lon: Optional[float] = None
    irrigation_type: Optional[str] = None
    soil_type: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    enterprise_name: str = ""
    current_ndvi: Optional[float] = None
    last_ndvi_date: Optional[str] = None
    active_alerts_count: int = 0
    current_crop: Optional[str] = None
    current_season: Optional[SeasonResponse] = None
    geometry: Optional[dict] = None

    class Config:
        from_attributes = True


class FieldListItem(BaseModel):
    """Поле для списка (без геометрии)."""
    id: int
    enterprise_id: int
    name: str
    code: Optional[str] = None
    area_ha: Optional[float] = None
    is_active: bool
    enterprise_name: str = ""
    current_ndvi: Optional[float] = None
    last_ndvi_date: Optional[str] = None
    active_alerts_count: int = 0
    current_crop: Optional[str] = None

    class Config:
        from_attributes = True


class GeoJSONFeature(BaseModel):
    """GeoJSON Feature."""
    type: str = "Feature"
    geometry: dict
    properties: dict


class GeoJSONFeatureCollection(BaseModel):
    """GeoJSON FeatureCollection."""
    type: str = "FeatureCollection"
    features: list[GeoJSONFeature]
