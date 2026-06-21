"""
Pydantic схемы для NDVI.
"""

from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel


class NDVIRecordResponse(BaseModel):
    """Одна запись NDVI."""
    id: int
    field_id: int
    captured_date: date
    mean_ndvi: Optional[float] = None
    min_ndvi: Optional[float] = None
    max_ndvi: Optional[float] = None
    std_ndvi: Optional[float] = None
    p10_ndvi: Optional[float] = None
    p90_ndvi: Optional[float] = None
    ndvi_change: Optional[float] = None
    ndvi_change_pct: Optional[float] = None
    cloud_cover_pct: Optional[float] = None
    valid_pixels_pct: Optional[float] = None
    satellite: Optional[str] = None

    class Config:
        from_attributes = True


class NDVIHistoryResponse(BaseModel):
    """История NDVI для поля."""
    field_id: int
    field_name: str
    days: int
    records: list[NDVIRecordResponse]


class NDVILatestResponse(BaseModel):
    """Последний снимок NDVI."""
    field_id: int
    field_name: str
    record: Optional[NDVIRecordResponse] = None
    age_days: Optional[int] = None  # сколько дней прошло со снимка
    has_alert: bool = False


class NDVIRefreshResponse(BaseModel):
    """Результат принудительного обновления NDVI."""
    field_id: int
    field_name: str
    success: bool
    message: str
    record: Optional[NDVIRecordResponse] = None
