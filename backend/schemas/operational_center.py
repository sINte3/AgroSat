"""Stable HTTP contracts for the Operational Command Center."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


OperationalStatus = Literal[
    "needs_review",
    "awaiting_inspection",
    "awaiting_review",
    "awaiting_work",
    "awaiting_evidence",
    "awaiting_verification",
    "stale",
    "external_unavailable",
    "improved_closed",
    # TASK_225: a plan closed without an IMPROVED verification is not "improved".
    "closed_without_improvement",
]
# Canonical remediation state (services/remediation_status.py), one vocabulary
# for every case; operational_status above is the published compatibility view.
RemediationStatus = Literal[
    "needs_inspection", "inspection_active", "awaiting_review", "awaiting_decision",
    "plan_active", "work_active", "awaiting_satellite_verification", "verification_blocked",
    "improved_awaiting_closure", "not_improved", "reopened", "improved_closed",
    "closed_without_improvement", "rejected", "cancelled", "inspection_closed",
    "data_unavailable",
]
RootSource = Literal["inspection", "candidate", "alert", "freshness", "external"]
NotificationStatus = Literal["unread", "read", "dismissed", "resolved"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueueItem(StrictModel):
    case_key: str
    source: RootSource
    source_id: str
    enterprise_id: int
    enterprise_name: str
    field_id: int | None
    field_name: str | None
    crop_type_id: int | None = None
    crop_name: str | None = None
    title: str
    priority: str
    operational_status: OperationalStatus
    remediation_status: RemediationStatus
    assignee_id: int | None = None
    assignee_name: str | None = None
    due_at: datetime | None = None
    is_overdue: bool
    blocked: bool
    awaiting_verification: bool
    external_state: str | None = None
    source_time: datetime
    priority_reasons: list[str]
    unread_notifications: int = 0
    active_notifications: int = 0
    inspection_id: int | None = None
    plan_id: int | None = None
    candidate_id: int | None = None
    alert_id: int | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class QueueResponse(StrictModel):
    as_of: datetime
    items: list[QueueItem]
    total: int
    limit: int
    offset: int


class SummaryResponse(StrictModel):
    as_of: datetime
    active_situations: int
    overdue_work: int
    blocked_or_external_unavailable: int
    awaiting_field_inspection: int
    awaiting_work: int
    awaiting_evidence: int
    awaiting_satellite_verification: int
    improved_or_closed_recent: int
    closed_without_improvement_recent: int
    not_improved: int
    reopened: int
    verification_blocked: int


class FilterOption(StrictModel):
    id: int
    label: str
    enterprise_id: int | None = None


class FilterOptionsResponse(StrictModel):
    enterprises: list[FilterOption]
    fields: list[FilterOption]
    crops: list[FilterOption]
    assignees: list[FilterOption]


class TimelineItem(StrictModel):
    source_kind: str
    source_id: str
    event_type: str
    occurred_at: datetime
    actor_id: int | None = None
    actor_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TimelineResponse(StrictModel):
    field_id: int
    items: list[TimelineItem]
    limit: int
    offset: int


class CaseDetailResponse(StrictModel):
    as_of: datetime
    case: QueueItem
    field: dict[str, Any] | None
    source_snapshot: dict[str, Any]
    inspection: dict[str, Any] | None
    agronomy_plan: dict[str, Any] | None
    work_items: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    verifications: list[dict[str, Any]]
    weather: dict[str, Any]
    telematics: dict[str, Any]
    notifications: list[dict[str, Any]]
    timeline: list[TimelineItem]
    links: dict[str, str]
    causality_limitation: str


class NotificationItem(StrictModel):
    id: int
    enterprise_id: int
    field_id: int | None = None
    case_key: str
    source_kind: str
    source_id: str
    notification_type: str
    severity: str
    title: str
    message: str
    provenance: dict[str, Any]
    status: NotificationStatus
    version: int
    available_at: datetime
    due_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class NotificationListResponse(StrictModel):
    items: list[NotificationItem]
    total: int
    limit: int
    offset: int


class NotificationTransition(StrictModel):
    action: Literal["read", "dismiss"]
    expected_version: int = Field(gt=0, strict=True)
    reason: str | None = Field(None, min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class NotificationTransitionResponse(StrictModel):
    notification_id: int
    status: NotificationStatus
    version: int
    replayed: bool = False
