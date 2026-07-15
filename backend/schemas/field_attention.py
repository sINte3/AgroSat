"""Typed contracts for the deterministic field attention queue."""
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

Priority = Literal["low", "medium", "high", "critical"]
Confidence = Literal["insufficient", "low", "medium", "high"]


class AttentionScope(BaseModel):
    enterprise_id: Optional[int] = None
    crop_type_id: Optional[int] = None
    max_scope_fields: int


class AttentionSummary(BaseModel):
    fields_evaluated: int
    attention_fields: int
    critical: int
    high: int
    medium: int
    low: int
    returned: int


class AttentionField(BaseModel):
    id: int
    name: str
    enterprise_id: int
    enterprise_name: str
    crop_type_id: int
    crop_name: str
    season_year: int


class AttentionReason(BaseModel):
    code: str
    label: str
    points: int
    indices: list[str]
    count: int


class TopAlert(BaseModel):
    id: int
    type: str
    severity: Literal["info", "warning", "critical"]
    title: str
    triggered_at: datetime


class AlertSummary(BaseModel):
    active_total: int
    critical: int
    warning: int
    info: int
    latest_triggered_at: Optional[datetime] = None
    top_alerts: list[TopAlert]


class SpectralSummary(BaseModel):
    overall_confidence: Confidence
    latest_observation_date: Optional[date] = None
    freshness_days: Optional[int] = None
    data_status: Literal["no_data", "stale", "current"]
    downward_indices: list[str]
    stale_indices: list[str]


class FieldAttentionItem(BaseModel):
    rank: int
    field: AttentionField
    priority: Priority
    attention_score: int = Field(ge=0, le=100)
    reasons: list[AttentionReason]
    alert_summary: AlertSummary
    spectral_summary: SpectralSummary
    recommended_checks: list[str]
    limitations: list[str]


class FieldAttentionQueueResponse(BaseModel):
    generated_at: datetime
    date_to: date
    lookback_days: int
    scope: AttentionScope
    summary: AttentionSummary
    items: list[FieldAttentionItem]
