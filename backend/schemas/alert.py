"""
Pydantic схемы для алертов.
Harden: acknowledged_by_id, Pydantic v2 ConfigDict per TASK_007.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class AlertResponse(BaseModel):
    """Алерт."""
    id: int
    field_id: int
    field_name: str = ""
    enterprise_name: str = ""
    alert_type: str
    severity: str
    title: str
    description: str
    recommendation: Optional[str] = None
    triggered_value: Optional[float] = None
    threshold_value: Optional[float] = None
    triggered_at: datetime
    acknowledged_at: Optional[datetime] = None
    acknowledged_by_id: Optional[int] = None
    is_active: bool
    captured_date: Optional[str] = None
    cloud_cover_pct: Optional[float] = None
    snapshot_ndvi: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class AcknowledgeResponse(BaseModel):
    """Результат отметки алерта."""
    success: bool
    message: str
