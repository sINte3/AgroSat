"""Typed response contracts for TASK_209 executive accountability."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExecutiveScopeResponse(BaseModel):
    role: Literal["admin", "manager"]
    enterprise_id: int | None


class ExecutiveDateRange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    date_from: date = Field(alias="from")
    date_to: date = Field(alias="to")
    inclusive: bool


class ExecutiveBacklog(BaseModel):
    attention_fields_now: int
    attention_critical: int
    attention_high: int
    attention_medium: int
    open_inspections: int
    unassigned_inspections: int
    overdue_inspections: int
    open_actions: int
    overdue_actions: int
    awaiting_verification: int


class ExecutiveDurationMetric(BaseModel):
    sample_count: int
    median_hours: float | None
    p90_hours: float | None


class ExecutiveCycleTimes(BaseModel):
    attention_signal_to_inspection_hours: ExecutiveDurationMetric
    inspection_to_action_hours: ExecutiveDurationMetric
    action_to_close_hours: ExecutiveDurationMetric


class ExecutiveVerificationOutcomes(BaseModel):
    improved: int
    unchanged: int
    worsened: int
    insufficient_data: int


class ExecutiveDataQuality(BaseModel):
    attention_stale_fields: int
    attention_no_data_fields: int
    attention_low_confidence_fields: int
    latest_observation_by_index: dict[str, date | None]


class ExecutiveEnterpriseRow(BaseModel):
    enterprise_id: int
    enterprise_name: str
    attention_fields_now: int
    attention_critical: int
    attention_high: int
    attention_medium: int
    open_inspections: int
    unassigned_inspections: int
    overdue_inspections: int
    open_actions: int
    overdue_actions: int
    awaiting_verification: int
    verification_outcomes: ExecutiveVerificationOutcomes


class ExecutiveOwnerRow(BaseModel):
    owner_id: int
    owner_name: str | None
    unresolved_actions: int
    overdue_actions: int
    next_due_date: date | None


class ExecutiveOverviewResponse(BaseModel):
    definitions_version: Literal["task209_executive_v1"]
    generated_at: datetime
    timezone: Literal["Asia/Tashkent"]
    scope: ExecutiveScopeResponse
    date_range: ExecutiveDateRange
    backlog: ExecutiveBacklog
    cycle_times: ExecutiveCycleTimes
    verification_outcomes: ExecutiveVerificationOutcomes
    data_quality: ExecutiveDataQuality
    enterprises: list[ExecutiveEnterpriseRow]
    owners: list[ExecutiveOwnerRow]
    limitations: list[str]


class AccountabilityItem(BaseModel):
    id: int
    inspection_id: int
    action_id: int | None
    field_id: int
    field_name: str
    enterprise_id: int
    enterprise_name: str
    owner_id: int | None
    owner_name: str | None
    description: str
    due_date: date | None
    status: str
    version: int
    verification_id: int | None
    verification_status: str | None


class AccountabilityResponse(BaseModel):
    definitions_version: Literal["task209_executive_v1"]
    generated_at: datetime
    timezone: Literal["Asia/Tashkent"]
    scope: ExecutiveScopeResponse
    date_range: ExecutiveDateRange
    kind: Literal[
        "unassigned_inspections",
        "overdue_inspections",
        "overdue_actions",
        "awaiting_verification",
    ]
    owner_id: int | None
    total: int
    limit: int
    offset: int
    items: list[AccountabilityItem]
