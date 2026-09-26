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
    current_crop_type_id: int | None = Field(
        description="Narrowing by the field's CURRENT crop classification, never crop at event time.")


class CropClassification(StrictModel):
    """The crop dimension of this response: a current classification of each field."""

    basis: Literal["current_crop_season"]
    reference_year: int = Field(description="Asia/Tashkent year of generated_at.")
    rule: str
    historical_crop_at_event: Literal[False] = Field(
        description="Always false: crop_seasons cannot establish the crop grown when a past event "
                    "happened, so no metric is attributed to a historical crop.")


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
    unassigned: int = Field(description="needs_inspection inspection cases without an assignee.")


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


class RemediationStatusCounts(StrictModel):
    """Active cases per TASK_225 remediation_status value (the value the command center returns per case)."""

    needs_inspection: NeedsInspection
    inspection_active: int
    awaiting_review: int
    awaiting_decision: int
    plan_active: PlanActive
    work_active: int
    awaiting_satellite_verification: AwaitingVerification = Field(
        description="The TASK_225 state (PENDING_DATA or TOO_EARLY) only; the command center's summary "
                    "figure of the same name is plans_pending_verification.")
    verification_blocked: VerificationBlocked
    improved_awaiting_closure: int
    not_improved: NotImproved
    reopened: int


class WorkItems(StrictModel):
    active: int
    planned: int
    in_progress: int
    unassigned: int
    overdue_work_items: int = Field(
        description="Active current-cycle work items of live plans whose due_at has passed (TASK_220 "
                    "item rule; the TASK_221 per-item overdue notification rule). A unit of work "
                    "items, not cases.")


class OverdueCases(StrictModel):
    """The Operational Center per-case is_overdue flag; equals its summary overdue_work."""

    total: int
    inspection_stage: int = Field(description="No current plan: the inspection deadline has passed.")
    work_stage: int = Field(description="The case's primary active work item (in progress first, then "
                                        "earliest due) is past due.")


class DataUnavailable(StrictModel):
    freshness_cases: int
    external_cases: int


class CurrentState(StrictModel):
    """Open canonical workload (Operational Center case model, TASK_225 states)."""

    as_of: datetime = Field(
        description="Current-state anchor: every count is the lifecycle state at this instant; "
                    "the period does not narrow it.")
    active_problems: ActiveProblems
    by_remediation_status: RemediationStatusCounts
    plans_pending_verification: int = Field(
        description="Cases whose current plan is pending_verification in any verification status: the "
                    "command center summary awaiting_satellite_verification and the TASK_220 plan "
                    "summary pending_verification.")
    work_items: WorkItems
    overdue_cases: OverdueCases
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


class ReopenEvents(StrictModel):
    """Reopen EVENTS in the period; current.by_remediation_status.reopened is the current state."""

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
    reopen_events: ReopenEvents


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


class RemediationStatusTotals(StrictModel):
    """Active cases per TASK_225 remediation_status value."""

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


class BreakdownCurrent(StrictModel):
    active_problems: int
    by_remediation_status: RemediationStatusTotals
    overdue_cases: int = Field(description="Operational Center is_overdue flag.")
    overdue_work_items: int = Field(description="Late active work items (a unit of work items).")


class BreakdownPeriod(StrictModel):
    inspections_opened: int
    resolved_cycles: int
    improved: int
    unchanged: int
    worsened: int
    unverified: int
    reopen_events: int


class BreakdownMetrics(StrictModel):
    monitored_fields: int
    current: BreakdownCurrent
    period: BreakdownPeriod


class EnterpriseBreakdown(BreakdownMetrics):
    enterprise_id: int
    enterprise_name: str


class CurrentCropBreakdown(BreakdownMetrics):
    """Fields grouped by their CURRENT crop classification; period numbers are NOT crop-at-event."""

    current_crop_type_id: int | None = Field(description="Null groups fields without a crop season.")
    current_crop_name: str | None


class FieldBreakdown(BreakdownMetrics):
    field_id: int
    field_name: str
    enterprise_id: int
    enterprise_name: str
    current_crop_type_id: int | None
    current_crop_name: str | None


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
    reopen_events: int


class Breakdowns(StrictModel):
    enterprises: list[EnterpriseBreakdown]
    current_crops: list[CurrentCropBreakdown]
    periods: list[PeriodBreakdown]
    fields: FieldPage


class ManagementAnalyticsResponse(StrictModel):
    definitions_version: Literal["management_analytics_v1"]
    generated_at: datetime
    timezone: Literal["Asia/Tashkent"]
    scope: AnalyticsScope
    crop_classification: CropClassification
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
