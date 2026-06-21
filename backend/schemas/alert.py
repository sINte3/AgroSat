"""
Pydantic схемы для алертов.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


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
    is_active: bool

    class Config:
        from_attributes = True


class AcknowledgeResponse(BaseModel):
    """Результат отметки алерта."""
    success: bool
    message: str
