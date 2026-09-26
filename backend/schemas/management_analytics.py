"""Typed response contract for H1 Management Analytics v1 (TASK_232).

Every count is a count of canonical lifecycle instances or events; the
definitions are in docs/TASK_232_RESULT.md and in
services/management_analytics.py. ``definitions_version`` changes whenever a
definition changes.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Granularity = Literal["day", "week", "month"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalyticsScope(StrictModel):
    role: Literal["admin", "manager"]
    authorization: Literal["global", "tenant"] = Field(
        description="Server-side authority: a manager is bound to the user's enterprise.")
    enterprise_id: int | None = Field(
        description="Effective enterprise narrowing (the manager's own enterprise or an admin filter).")
    field_id: int | None
    crop_type_id: int | None


class RequestedPeriod(StrictModel):
    date_from: date | None
    date_to: date | None


class EffectivePeriod(StrictModel):
    date_from: date
    date_to: date
    inclusive: bool
    days: int
    starts_at: datetime = Field(description="date_from 00:00 Asia/Tashkent.")
    ends_before: datetime = Field(description="Exclusive end: the day after date_to, 00:00 Asia/Tashkent.")
    granularity: Granularity


class AnalyticsPeriod(StrictModel):
    requested: RequestedPeriod
    effective: EffectivePeriod


class Provenance(StrictModel):
    lifecycle: str
    inspection_workflow: str
    status_projection: str
    case_model: str
    verification_policy_version: str
    sources: list[str]
    excluded_legacy_sources: list[str]
    definitions_fingerprint: str


class NdviFreshness(StrictModel):
    fresh: int
    aging: int
    stale: int
    never_collected: int
    cloud_blocked: int
    provider_degraded: int
    quality_blocked: int
    not_evaluated: int


class Coverage(StrictModel):
    """Monitored field = ``fields.is_active`` true, the TASK_219 collection population."""

    fields_in_scope: int
    monitored_fields: int
    inactive_fields: int
    monitored_fields_with_active_problems: int
    ndvi_freshness: NdviFreshness


class SourceCounts(StrictModel):
    inspection: int
    candidate: int
    alert: int


class PriorityCounts(StrictModel):
    critical: int
    high: int
    normal: int
    low: int


class ActiveProblems(StrictModel):
    total: int
    fields_affected: int
    by_source: SourceCounts
    by_priority: PriorityCounts
    legacy_open_inspections: int


class NeedsInspection(StrictModel):
    total: int
    inspections: int
    candidates: int
    alerts: int
    unassigned_inspections: int


class InspectionFunnel(StrictModel):
    needs_inspection: NeedsInspection
    inspection_active: int
    awaiting_review: int
    awaiting_decision: int


class PlanActive(StrictModel):
    total: int
    draft: int
    approved: int


class AwaitingVerification(StrictModel):
    total: int
    pending_data: int
    too_early: int


class VerificationBlocked(StrictModel):
    total: int
    cloud_blocked: int
    quality_blocked: int
    provider_degraded: int
    inconclusive: int


class NotImproved(StrictModel):
    total: int
    unchanged: int
    worsened: int


class RemediationFunnel(StrictModel):
    plan_active: PlanActive
    work_active: int
    awaiting_satellite_verification: AwaitingVerification
    verification_blocked: VerificationBlocked
    improved_awaiting_closure: int
    not_improved: NotImproved
    reopened: int


class WorkItems(StrictModel):
    active: int
    planned: int
    in_progress: int
    unassigned: int
    overdue: int


class Overdue(StrictModel):
    cases: int
    inspections: int
    plans_with_overdue_work: int
    work_items: int


class DataUnavailable(StrictModel):
    freshness_cases: int
    external_cases: int


class CurrentState(StrictModel):
    """Open canonical workload (Operational Center case model, TASK_225 states)."""

    as_of: datetime = Field(
        description="Current-state anchor: every count is the lifecycle state at this instant; "
                    "the period does not narrow it.")
    active_problems: ActiveProblems
    inspection_funnel: InspectionFunnel
    remediation_funnel: RemediationFunnel
    work_items: WorkItems
    overdue: Overdue
    data_unavailable: DataUnavailable


class InspectionsOpened(StrictModel):
    total: int
    manual: int
    alert: int
    pixel_ndvi: int


class PeriodActivity(StrictModel):
    anomaly_candidates_detected: int
    inspections_opened: InspectionsOpened
    inspections_confirmed: int
    inspections_rejected: int
    inspections_cancelled: int
    plans_drafted: int
    plan_cycles_approved: int
    plan_cycles_work_completed: int
    plan_cycles_verified: int
    plans_cancelled: int


class VerifiedOutcomes(StrictModel):
    improved: int
    unchanged: int = Field(description="Canonical NO_MATERIAL_CHANGE.")
    worsened: int
    total: int


class UnverifiedResolutions(StrictModel):
    total: int
    pending_data: int
    too_early: int
    cloud_blocked: int
    quality_blocked: int
    provider_degraded: int
    inconclusive: int


class ClosedCycles(StrictModel):
    total: int
    improved: int
    without_improvement: int


class Reopened(StrictModel):
    total: int
    after_closure: int
    after_verification: int


class Outcomes(StrictModel):
    """One row per plan cycle resolved in the period (anchor: resolution event)."""

    resolved_cycles: int
    verified: VerifiedOutcomes
    unverified: UnverifiedResolutions
    closed: ClosedCycles
    returned_for_rework: int
    reopened: Reopened


class DurationMetric(StrictModel):
    start_event: str
    end_event: str
    period_anchor: str
    population: str
    sample_count: int
    median_hours: float | None
    p90_hours: float | None
    status: Literal["measured", "no_samples"]


class UnsupportedMetric(StrictModel):
    metric: str
    reason: str


class CycleTimeMetrics(StrictModel):
    signal_to_inspection_opened: DurationMetric
    inspection_opened_to_reviewed: DurationMetric
    inspection_submitted_to_plan_drafted: DurationMetric
    plan_approved_to_work_completed: DurationMetric
    work_completed_to_verified: DurationMetric
    case_opened_to_verified_closure: DurationMetric


class CycleTimes(StrictModel):
    unit: Literal["hours"]
    p90_minimum_samples: int
    metrics: CycleTimeMetrics
    unsupported: list[UnsupportedMetric]


class WorkCompletion(StrictModel):
    population: str
    numerator: int
    denominator: int
    rate: float | None
    completed: int
    ended_without_completion: int
    open: int


class VerificationCompletion(StrictModel):
    population: str
    numerator: int
    denominator: int
    rate: float | None
    improved: int
    unchanged: int
    worsened: int
    not_conclusive: int
    improved_rate: float | None


class PlanClosure(StrictModel):
    population: str
    numerator: int
    denominator: int
    rate: float | None
    closed_improved: int
    closed_without_improvement: int
    cancelled: int
    open: int


class Completion(StrictModel):
    work_completion: WorkCompletion
    verification_completion: VerificationCompletion
    plan_closure: PlanClosure


class BreakdownCurrent(StrictModel):
    active_problems: int
    needs_inspection: int
    inspection_active: int
    awaiting_review: int
    awaiting_decision: int
    plan_active: int
    work_active: int
    awaiting_satellite_verification: int
    verification_blocked: int
    improved_awaiting_closure: int
    not_improved: int
    reopened: int
    overdue_cases: int
    overdue_work_items: int


class BreakdownPeriod(StrictModel):
    inspections_opened: int
    resolved_cycles: int
    improved: int
    unchanged: int
    worsened: int
    unverified: int
    reopened: int


class BreakdownMetrics(StrictModel):
    monitored_fields: int
    current: BreakdownCurrent
    period: BreakdownPeriod


class EnterpriseBreakdown(BreakdownMetrics):
    enterprise_id: int
    enterprise_name: str


class CropBreakdown(BreakdownMetrics):
    crop_type_id: int | None = Field(description="Null groups fields without a crop season.")
    crop_name: str | None


class FieldBreakdown(BreakdownMetrics):
    field_id: int
    field_name: str
    enterprise_id: int
    enterprise_name: str
    crop_type_id: int | None
    crop_name: str | None


class FieldPage(StrictModel):
    items: list[FieldBreakdown]
    total: int
    limit: int
    offset: int


class PeriodBreakdown(StrictModel):
    bucket_start: date
    bucket_end: date
    anomaly_candidates_detected: int
    inspections_opened: int
    plans_drafted: int
    plan_cycles_approved: int
    plan_cycles_work_completed: int
    plan_cycles_verified: int
    resolved_cycles: int
    improved: int
    unchanged: int
    worsened: int
    unverified: int
    closed: int
    returned_for_rework: int
    reopened: int


class Breakdowns(StrictModel):
    enterprises: list[EnterpriseBreakdown]
    crops: list[CropBreakdown]
    periods: list[PeriodBreakdown]
    fields: FieldPage


class ManagementAnalyticsResponse(StrictModel):
    definitions_version: Literal["management_analytics_v1"]
    generated_at: datetime
    timezone: Literal["Asia/Tashkent"]
    scope: AnalyticsScope
    period: AnalyticsPeriod
    provenance: Provenance
    coverage: Coverage
    current: CurrentState
    period_activity: PeriodActivity
    outcomes: Outcomes
    cycle_times: CycleTimes
    completion: Completion
    breakdowns: Breakdowns
    limitations: list[str]
