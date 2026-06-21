"""
Pydantic схемы для полей (Field).
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


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


class FieldCreate(BaseModel):
    """Создание нового поля."""
    enterprise_id: int
    name: str = Field(..., max_length=255)
    code: Optional[str] = Field(None, max_length=50)
    geometry: dict = Field(..., description="GeoJSON Polygon или MultiPolygon")
    area_ha: Optional[float] = None
    irrigation_type: Optional[str] = Field(None, max_length=50)
    soil_type: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class FieldUpdate(BaseModel):
    """Редактирование поля. Все поля опциональны."""
    name: Optional[str] = Field(None, max_length=255)
    code: Optional[str] = Field(None, max_length=50)
    geometry: Optional[dict] = None
    area_ha: Optional[float] = None
    irrigation_type: Optional[str] = Field(None, max_length=50)
    soil_type: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None
    is_active: Optional[bool] = None


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
