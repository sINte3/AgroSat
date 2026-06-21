"""
Pydantic схемы для погоды.
"""

from typing import Optional
from pydantic import BaseModel, Field


class WeatherCurrent(BaseModel):
    """Текущая погода."""
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    wind_ms: Optional[float] = None
    precipitation_mm: Optional[float] = None
    weather_code: Optional[int] = None
    description: str = "Нет данных"


class WeatherForecastDay(BaseModel):
    """Прогноз на день."""
    date: str
    temp_max: Optional[float] = None
    temp_min: Optional[float] = None
    precipitation_mm: Optional[float] = None
    et0_mm: Optional[float] = None
    wind_max_ms: Optional[float] = None
    weather_code: Optional[int] = None
    weather_description: str = "Нет данных"


class WeatherRisk(BaseModel):
    """Погодный риск."""
    type: str
    severity: str
    date: str
    description: str
    value: Optional[float] = None


class WeatherResponse(BaseModel):
    """Полный ответ с погодой."""
    current: WeatherCurrent
    forecast: list[WeatherForecastDay] = []
    risks: list[WeatherRisk] = []
    location: Optional[dict] = None
    field_name: Optional[str] = None
