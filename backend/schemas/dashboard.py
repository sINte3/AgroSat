"""
Pydantic схемы для дашборда.
Harden: Pydantic v2 ConfigDict, additional fields per TASK_007.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


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

    model_config = ConfigDict(from_attributes=True)


class DashboardSummary(BaseModel):
    """Сводка по всему кластеру."""
    total_fields: int = 0
    total_enterprises: int = 0
    total_area_ha: Optional[float] = None
    active_alerts: int = 0
    critical_alerts: int = 0
    warning_alerts: int = 0
    avg_ndvi: Optional[float] = None
    fields_with_problems: int = 0
    fields_no_data: int = 0
    last_updated: Optional[str] = None
    critical_alert_fields: int = 0
    enterprises: list[EnterpriseSummary] = []

    model_config = ConfigDict(from_attributes=True)


class EnterpriseDashboard(BaseModel):
    """Сводка по одному предприятию."""
    enterprise_id: int
    enterprise_name: str
    region: Optional[str] = None
    total_fields: int = 0
    total_area_ha: Optional[float] = None
    active_alerts: int = 0
    critical_alerts: int = 0
    warning_alerts: int = 0
    avg_ndvi: Optional[float] = None
    fields_with_problems: int = 0
    fields_no_data: int = 0
    last_updated: Optional[str] = None
    top_alerts: list = []

    model_config = ConfigDict(from_attributes=True)
