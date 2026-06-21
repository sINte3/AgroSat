"""
Pydantic схемы для дашборда.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class EnterpriseSummary(BaseModel):
    """Краткая сводка по одному предприятию."""
    id: int
    name: str
    region: Optional[str] = None
    fields_count: int = 0
    active_alerts: int = 0
    critical_alerts: int = 0
    avg_ndvi: Optional[float] = None
    fields_with_problems: int = 0


class DashboardSummary(BaseModel):
    """Сводка по всему кластеру."""
    total_fields: int = 0
    total_enterprises: int = 0
    active_alerts: int = 0
    critical_alerts: int = 0
    avg_ndvi: Optional[float] = None
    fields_with_problems: int = 0
    last_updated: Optional[str] = None
    critical_alert_fields: int = 0
    enterprises: list[EnterpriseSummary] = []


class EnterpriseDashboard(BaseModel):
    """Сводка по одному предприятию."""
    enterprise_id: int
    enterprise_name: str
    region: Optional[str] = None
    total_fields: int = 0
    total_area_ha: Optional[float] = None
    active_alerts: int = 0
    critical_alerts: int = 0
    avg_ndvi: Optional[float] = None
    fields_with_problems: int = 0
    last_updated: Optional[str] = None
    top_alerts: list = []  # последние 5 алертов
