"""Read-only H1 Management Analytics v1 (TASK_232).

One management snapshot of the canonical closed loop, over the fields the
caller is authorized to see:

  satellite signal (autonomous_anomaly_candidates) and active alerts
  -> TASK_217 inspection (field_inspections)
  -> TASK_220 plan -> work -> execution evidence
  -> later accepted observation -> verification (agronomy_verifications)
  -> close, rework or reopen (agronomy_events)

Current state is the TASK_221 Operational Center case model
(``services.operational_center.CASES_CTE``) classified by the TASK_225
projection (``services/remediation_status.py``), so the command center and this
snapshot cannot disagree on what is open or where it stands. Windowed facts
come from the append-only TASK_220 history (``agronomy_events``,
``agronomy_verifications``) and the canonical inspection timestamps.

The retired TASK_209 ``corrective_actions`` and ``action_verification_requests``
are never read here; ``/api/executive`` keeps that history readable.

Everything is computed by one bounded, tenant-scoped SQL statement (plus one
existence check when the caller narrows by enterprise, field or crop), so the
sections of one response reconcile with each other and the statement count
does not grow with the data. No ORM object is loaded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

from api.dependencies import ALLOWED_ROLES, normalize_role
from api.query_bounds import ensure_within_row_cap, fetch_limit
from services import remediation_status as rs
from services.agronomy_policy import POLICY as VERIFICATION_POLICY
from services.executive_accountability import DateWindow, resolve_window
from services.operational_center import CASES_CTE


DEFINITIONS_VERSION = "management_analytics_v1"
TIMEZONE = "Asia/Tashkent"
TASHKENT = ZoneInfo(TIMEZONE)
MANAGEMENT_ROLES = frozenset({"admin", "manager"})
GRANULARITIES = ("day", "week", "month")
MIN_SAMPLES_FOR_P90 = 10
ENTERPRISE_ROW_CAP = 500
CROP_ROW_CAP = 200

# The canonical remediation states (services/remediation_status.py) that are
# still open. Terminal states and the data-availability state are excluded
# from the problem load; data availability is reported on its own.
ACTIVE_STATES = (
    rs.NEEDS_INSPECTION, rs.INSPECTION_ACTIVE, rs.AWAITING_REVIEW, rs.AWAITING_DECISION,
    rs.PLAN_ACTIVE, rs.WORK_ACTIVE, rs.AWAITING_SATELLITE_VERIFICATION,
    rs.VERIFICATION_BLOCKED, rs.IMPROVED_AWAITING_CLOSURE, rs.NOT_IMPROVED, rs.REOPENED,
)
# Verification statuses of policy r3-f-v1 (services/agronomy_policy.py). Only
# the three conclusive ones are outcomes; everything else is "not verified".
CONCLUSIVE = ("IMPROVED", "NO_MATERIAL_CHANGE", "WORSENED")
NOT_CONCLUSIVE = (
    "PENDING_DATA", "TOO_EARLY", "CLOUD_BLOCKED", "QUALITY_BLOCKED",
    "PROVIDER_DEGRADED", "INCONCLUSIVE",
)
NDVI_FRESHNESS = (
    "FRESH", "AGING", "STALE", "NEVER_COLLECTED", "CLOUD_BLOCKED",
    "PROVIDER_DEGRADED", "QUALITY_BLOCKED", "NOT_EVALUATED",
)

LIFECYCLE_SOURCES = (
    "fields", "crop_seasons", "field_inspections", "autonomous_anomaly_candidates",
    "alerts", "agronomy_plans", "agronomy_work_items", "agronomy_verifications",
    "agronomy_events", "satellite_field_freshness", "satellite_collection_runs",
)
EXCLUDED_LEGACY_SOURCES = ("corrective_actions", "action_verification_requests")

LIMITATIONS = [
    (
        "Current-state sections describe the lifecycle at generated_at and are "
        "not narrowed by the period; windowed sections count facts whose "
        "documented anchor timestamp falls inside the period."
    ),
    (
        "Crop attribution uses the field's current crop season (latest "
        "crop_seasons.season_year not after the generation year, Asia/Tashkent), "
        "the Operational Center rule; historical crop rotation is not reconstructed."
    ),
    (
        "IMPROVED, UNCHANGED and WORSENED are the persisted r3-f-v1 satellite "
        "verification statuses: an observed NDVI change over the verification "
        "scope, not proof of agronomic cause, yield or financial effect."
    ),
    (
        "Missing, cloud-, quality- or provider-blocked, too-early and inconclusive "
        "verifications are never counted as outcomes."
    ),
    (
        "TASK_220 work items have no blocked state; blocked means a verification "
        "that cannot conclude (cloud, quality, provider, inconclusive)."
    ),
    (
        "Alert-origin cases have no timezone-safe persisted signal time "
        "(alerts.triggered_at has no time zone), so signal-to-inspection time "
        "covers autonomous-candidate cases only."
    ),
    (
        "The retired TASK_209 corrective-action history is not part of these "
        "metrics; /api/executive keeps it readable."
    ),
]


def _quoted(values) -> str:
    return ",".join(f"'{value}'" for value in values)


def _count_columns(columns: dict[str, str], indent: str = "    ") -> str:
    return (",\n" + indent).join(
        f"count(*) FILTER (WHERE {condition})::integer AS {name}"
        for name, condition in columns.items()
    )


def _sum_columns(names, indent: str = "    ") -> str:
    return (",\n" + indent).join(f"COALESCE(sum({name}),0)::integer AS {name}" for name in names)


_RESOLVED = "fact IN ('cycle_closed','cycle_returned_for_rework')"

CURRENT_COLUMNS = {
    "active_problems": "true",
    "source_inspection": "root_source='inspection'",
    "source_candidate": "root_source='candidate'",
    "source_alert": "root_source='alert'",
    "priority_critical": "priority_rank=0",
    "priority_high": "priority_rank=1",
    "priority_normal": "priority_rank=2",
    "priority_low": "priority_rank=3",
    "legacy_open_inspections": "source_kind='legacy'",
    "needs_inspection": f"remediation_status='{rs.NEEDS_INSPECTION}'",
    "needs_inspection_inspections": f"remediation_status='{rs.NEEDS_INSPECTION}' AND root_source='inspection'",
    "needs_inspection_candidates": f"remediation_status='{rs.NEEDS_INSPECTION}' AND root_source='candidate'",
    "needs_inspection_alerts": f"remediation_status='{rs.NEEDS_INSPECTION}' AND root_source='alert'",
    "needs_inspection_unassigned": (
        f"remediation_status='{rs.NEEDS_INSPECTION}' AND root_source='inspection' "
        "AND inspection_assignee_id IS NULL"
    ),
    "inspection_active": f"remediation_status='{rs.INSPECTION_ACTIVE}'",
    "awaiting_review": f"remediation_status='{rs.AWAITING_REVIEW}'",
    "awaiting_decision": f"remediation_status='{rs.AWAITING_DECISION}'",
    "plan_active": f"remediation_status='{rs.PLAN_ACTIVE}'",
    "plan_active_draft": f"remediation_status='{rs.PLAN_ACTIVE}' AND plan_status='draft'",
    "plan_active_approved": f"remediation_status='{rs.PLAN_ACTIVE}' AND plan_status='approved'",
    "work_active": f"remediation_status='{rs.WORK_ACTIVE}'",
    "awaiting_satellite_verification": f"remediation_status='{rs.AWAITING_SATELLITE_VERIFICATION}'",
    "awaiting_pending_data": (
        f"remediation_status='{rs.AWAITING_SATELLITE_VERIFICATION}' AND verification_status='PENDING_DATA'"
    ),
    "awaiting_too_early": (
        f"remediation_status='{rs.AWAITING_SATELLITE_VERIFICATION}' AND verification_status='TOO_EARLY'"
    ),
    "verification_blocked": f"remediation_status='{rs.VERIFICATION_BLOCKED}'",
    "blocked_cloud": f"remediation_status='{rs.VERIFICATION_BLOCKED}' AND verification_status='CLOUD_BLOCKED'",
    "blocked_quality": f"remediation_status='{rs.VERIFICATION_BLOCKED}' AND verification_status='QUALITY_BLOCKED'",
    "blocked_provider": (
        f"remediation_status='{rs.VERIFICATION_BLOCKED}' AND verification_status='PROVIDER_DEGRADED'"
    ),
    "blocked_inconclusive": (
        f"remediation_status='{rs.VERIFICATION_BLOCKED}' AND verification_status='INCONCLUSIVE'"
    ),
    "improved_awaiting_closure": f"remediation_status='{rs.IMPROVED_AWAITING_CLOSURE}'",
    "not_improved": f"remediation_status='{rs.NOT_IMPROVED}'",
    "not_improved_unchanged": f"remediation_status='{rs.NOT_IMPROVED}' AND verification_status='NO_MATERIAL_CHANGE'",
    "not_improved_worsened": f"remediation_status='{rs.NOT_IMPROVED}' AND verification_status='WORSENED'",
    "reopened": f"remediation_status='{rs.REOPENED}'",
    "overdue_inspections": "inspection_overdue",
    "overdue_plans": "plan_overdue",
    "overdue_cases": "inspection_overdue OR plan_overdue",
}

# The per-row breakdown keeps the canonical state names as columns.
BREAKDOWN_CURRENT_COLUMNS = {
    "active_problems": "true",
    **{state: f"remediation_status='{state}'" for state in ACTIVE_STATES},
    "overdue_cases": "inspection_overdue OR plan_overdue",
}

FLOW_COLUMNS = {
    "anomaly_candidates_detected": "fact='candidate_detected'",
    "inspections_opened": "fact='inspection_opened'",
    "inspections_opened_manual": "fact='inspection_opened' AND detail='manual'",
    "inspections_opened_alert": "fact='inspection_opened' AND detail='alert'",
    "inspections_opened_pixel_ndvi": "fact='inspection_opened' AND detail='pixel_ndvi'",
    "inspections_confirmed": "fact='inspection_confirmed'",
    "inspections_rejected": "fact='inspection_rejected'",
    "inspections_cancelled": "fact='inspection_cancelled'",
    "plans_drafted": "fact='plan_drafted'",
    "plan_cycles_approved": "fact='cycle_approved'",
    "plan_cycles_work_completed": "fact='cycle_work_completed'",
    "plan_cycles_verified": "fact='cycle_verified'",
    "plans_cancelled": "fact='plan_cancelled'",
    "resolved_cycles": _RESOLVED,
    "outcome_improved": f"{_RESOLVED} AND detail='IMPROVED'",
    "outcome_unchanged": f"{_RESOLVED} AND detail='NO_MATERIAL_CHANGE'",
    "outcome_worsened": f"{_RESOLVED} AND detail='WORSENED'",
    **{
        f"unverified_{status.lower()}": f"{_RESOLVED} AND detail='{status}'"
        for status in NOT_CONCLUSIVE
    },
    "closed": "fact='cycle_closed'",
    "closed_improved": "fact='cycle_closed' AND detail='IMPROVED'",
    "returned_for_rework": "fact='cycle_returned_for_rework'",
    "reopened_after_closure": "fact='reopened_after_closure'",
    "reopened_after_verification": "fact='reopened_after_verification'",
}

BUCKET_COLUMNS = {
    "anomaly_candidates_detected": "fact='candidate_detected'",
    "inspections_opened": "fact='inspection_opened'",
    "plans_drafted": "fact='plan_drafted'",
    "plan_cycles_approved": "fact='cycle_approved'",
    "plan_cycles_work_completed": "fact='cycle_work_completed'",
    "plan_cycles_verified": "fact='cycle_verified'",
    "resolved_cycles": _RESOLVED,
    "improved": f"{_RESOLVED} AND detail='IMPROVED'",
    "unchanged": f"{_RESOLVED} AND detail='NO_MATERIAL_CHANGE'",
    "worsened": f"{_RESOLVED} AND detail='WORSENED'",
    "unverified": f"{_RESOLVED} AND detail NOT IN ({_quoted(CONCLUSIVE)})",
    "closed": "fact='cycle_closed'",
    "returned_for_rework": "fact='cycle_returned_for_rework'",
    "reopened": "fact IN ('reopened_after_closure','reopened_after_verification')",
}

BREAKDOWN_PERIOD_COLUMNS = {
    "inspections_opened": "fact='inspection_opened'",
    "resolved_cycles": _RESOLVED,
    "improved": f"{_RESOLVED} AND detail='IMPROVED'",
    "unchanged": f"{_RESOLVED} AND detail='NO_MATERIAL_CHANGE'",
    "worsened": f"{_RESOLVED} AND detail='WORSENED'",
    "unverified": f"{_RESOLVED} AND detail NOT IN ({_quoted(CONCLUSIVE)})",
    "reopened": "fact IN ('reopened_after_closure','reopened_after_verification')",
}

BREAKDOWN_NUMBERS = (
    ("monitored_fields",)
    + tuple(BREAKDOWN_CURRENT_COLUMNS)
    + ("overdue_work_items",)
    + tuple(f"period_{name}" for name in BREAKDOWN_PERIOD_COLUMNS)
)

# Duration metrics: start event, end event, the timestamp that places a sample
# in the period, and the population. Only completed, correctly ordered pairs
# are sampled; an open cycle has no end event and contributes nothing.
DURATIONS = {
    "signal_to_inspection_opened": {
        "start_event": "autonomous_anomaly_candidates.created_at (satellite signal detected)",
        "end_event": "field_inspections.created_at (canonical inspection opened from that candidate)",
        "period_anchor": "field_inspections.created_at",
        "population": "canonical inspections opened from an autonomous anomaly candidate",
    },
    "inspection_opened_to_reviewed": {
        "start_event": "field_inspections.created_at (inspection opened)",
        "end_event": "field_inspections.reviewed_at (finding confirmed or rejected)",
        "period_anchor": "field_inspections.reviewed_at",
        "population": "canonical inspections reviewed (confirmed or rejected)",
    },
    "inspection_submitted_to_plan_drafted": {
        "start_event": "field_inspections.submitted_at (finding submitted)",
        "end_event": "agronomy_plans.created_at of the inspection's first plan",
        "period_anchor": "agronomy_plans.created_at of the first plan",
        "population": "inspections whose first TASK_220 plan was drafted",
    },
    "plan_approved_to_work_completed": {
        "start_event": "agronomy_events 'approve' of the plan cycle",
        "end_event": "agronomy_events 'work_complete' that moved the cycle to pending_verification",
        "period_anchor": "work completion event time",
        "population": "plan cycles whose required work was completed",
    },
    "work_completed_to_verified": {
        "start_event": "agronomy_verifications.completed_at (cycle work completion)",
        "end_event": "agronomy_verifications.created_at of the cycle's first conclusive verification",
        "period_anchor": "first conclusive verification time",
        "population": "plan cycles with a conclusive IMPROVED, NO_MATERIAL_CHANGE or WORSENED verification",
    },
    "case_opened_to_verified_closure": {
        "start_event": "field_inspections.created_at (canonical case root opened)",
        "end_event": "agronomy_plans.closed_at (plan closed with a conclusive verification)",
        "period_anchor": "agronomy_plans.closed_at",
        "population": "plans currently closed whose closing verification is conclusive",
    },
}
UNSUPPORTED_DURATIONS = [
    {
        "metric": "alert_signal_to_inspection_opened",
        "reason": (
            "alerts.triggered_at is stored without a time zone, so no timezone-safe "
            "persisted signal time exists for alert-origin cases"
        ),
    },
]


# ── SQL ──────────────────────────────────────────────────────────────────────
#
# Appended to CASES_CTE (one WITH list). Tokens:
#   @field_scope@      server-side scope and narrowing filters on fields f
#   @case_scope@       the same narrowing, repeated on cases c for pushdown
#   @external_allowed@ enterprise-level external cases only without a field or
#                      crop filter (the Operational Center behaviour)
#
# Parameters: as_of, as_of_date, actor_user_id (CASES_CTE), period_start,
# period_end, granularity, field_limit, field_offset, enterprise_fetch,
# crop_fetch, and the optional scope_enterprise_id, field_id, crop_type_id.

_PERIOD = "{column} >= :period_start AND {column} < :period_end"


def _in_period(column: str) -> str:
    return _PERIOD.format(column=column)


_TEMPLATE = """,
scope_fields AS (
  SELECT f.id AS field_id, f.enterprise_id, e.name AS enterprise_name, f.name AS field_name,
    COALESCE(f.is_active, false) AS monitored, crop.crop_type_id, crop.crop_name
  FROM fields f
  JOIN enterprises e ON e.id=f.enterprise_id
  LEFT JOIN LATERAL (
    SELECT cs.crop_type_id, ct.name_ru AS crop_name
    FROM crop_seasons cs JOIN crop_types ct ON ct.id=cs.crop_type_id
    WHERE cs.field_id=f.id
      AND cs.season_year <= extract(year from timezone('Asia/Tashkent', :as_of))::integer
    ORDER BY cs.season_year DESC, cs.id DESC LIMIT 1
  ) crop ON true
  WHERE @field_scope@
),
scoped_cases AS (
  SELECT c.root_source, c.field_id, c.enterprise_id, c.priority_rank,
    c.remediation_status, c.inspection_id, c.plan_id
  FROM cases c
  WHERE @case_scope@
    AND (
      EXISTS (SELECT 1 FROM scope_fields sf
              WHERE sf.field_id=c.field_id AND sf.enterprise_id=c.enterprise_id)
      OR (c.root_source='external' AND @external_allowed@ AND EXISTS (
            SELECT 1 FROM scope_fields sf WHERE sf.enterprise_id=c.enterprise_id))
    )
),
problem_cases AS (
  SELECT sc.root_source, sc.field_id, sc.priority_rank, sc.remediation_status,
    i.source_kind, i.assigned_to_id AS inspection_assignee_id,
    p.status AS plan_status, p.verification_status,
    COALESCE(sc.plan_id IS NULL
      AND i.status IN ('pending','new','assigned','in_progress','submitted')
      AND ((i.due_at IS NOT NULL AND i.due_at < :as_of)
           OR (i.due_at IS NULL AND i.due_date < :as_of_date)), false) AS inspection_overdue,
    (p.id IS NOT NULL AND EXISTS (
      SELECT 1 FROM agronomy_work_items w
      WHERE w.plan_id=p.id AND w.enterprise_id=p.enterprise_id AND w.cycle=p.cycle
        AND w.status IN ('planned','in_progress') AND w.due_at < :as_of)) AS plan_overdue
  FROM scoped_cases sc
  LEFT JOIN field_inspections i ON i.id=sc.inspection_id AND i.enterprise_id=sc.enterprise_id
  LEFT JOIN agronomy_plans p ON p.id=sc.plan_id AND p.enterprise_id=sc.enterprise_id
  WHERE sc.root_source IN ('inspection','candidate','alert')
    AND sc.remediation_status IN (@active_states@)
),
live_work AS (
  SELECT w.field_id, w.status, w.due_at, w.assigned_to_id
  FROM agronomy_work_items w
  JOIN agronomy_plans p ON p.id=w.plan_id AND p.enterprise_id=w.enterprise_id AND p.cycle=w.cycle
  WHERE p.status NOT IN ('closed','cancelled','superseded')
    AND w.status IN ('planned','in_progress')
    AND EXISTS (SELECT 1 FROM scope_fields sf
                WHERE sf.field_id=w.field_id AND sf.enterprise_id=w.enterprise_id)
),
field_freshness AS (
  SELECT sf.field_id, COALESCE(s.status, 'NOT_EVALUATED') AS ndvi_status
  FROM scope_fields sf
  LEFT JOIN satellite_field_freshness s
    ON s.field_id=sf.field_id AND s.enterprise_id=sf.enterprise_id AND s.index_code='ndvi'
  WHERE sf.monitored
),
period_inspections AS (
  SELECT i.field_id, i.source_kind, i.status, i.created_at, i.reviewed_at, i.cancelled_at
  FROM field_inspections i
  WHERE i.source_kind <> 'legacy'
    AND EXISTS (SELECT 1 FROM scope_fields sf
                WHERE sf.field_id=i.field_id AND sf.enterprise_id=i.enterprise_id)
    AND ((@created_in_period@) OR (@reviewed_in_period@) OR (@cancelled_in_period@))
),
touched_plans AS (
  SELECT DISTINCT ev.plan_id
  FROM agronomy_events ev
  WHERE @event_in_period@
    AND EXISTS (SELECT 1 FROM scope_fields sf
                WHERE sf.field_id=ev.field_id AND sf.enterprise_id=ev.enterprise_id)
),
cycle_events AS (
  -- The cycle of an event is 1 + the number of earlier cycle-opening events:
  -- rework/reinspection after verification, or reopen after closure. The
  -- opening event itself belongs to the cycle it ends.
  SELECT ev.plan_id, ev.field_id, ev.event_type, ev.occurred_at,
    ev.detail->>'from_status' AS from_status,
    ev.response->>'status' AS resulting_status,
    1 + COALESCE(sum(CASE WHEN ev.event_type IN ('rework','reinspection','reopen')
                           AND ev.detail->>'from_status' IN ('pending_verification','closed')
                      THEN 1 ELSE 0 END)
          OVER (PARTITION BY ev.plan_id ORDER BY ev.id
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS cycle
  FROM agronomy_events ev
  JOIN touched_plans tp ON tp.plan_id=ev.plan_id
  WHERE ev.event_type IN ('approve','work_complete','close','override_close',
                          'rework','reinspection','reopen','cancel','supersede')
),
cycle_milestones AS (
  SELECT plan_id, cycle,
    min(occurred_at) FILTER (WHERE event_type='approve') AS approved_at,
    min(occurred_at) FILTER (WHERE event_type='work_complete'
                               AND resulting_status='pending_verification') AS completed_at,
    min(occurred_at) FILTER (WHERE event_type IN ('cancel','supersede')) AS abandoned_at
  FROM cycle_events
  GROUP BY plan_id, cycle
),
cycle_verifications AS (
  -- The latest verification row of a cycle is the plan's verification status
  -- for that cycle: rows are written only while pending_verification, and every
  -- new row also sets agronomy_plans.verification_status.
  SELECT v.plan_id, v.cycle,
    (array_agg(v.status ORDER BY v.id DESC))[1] AS final_status,
    min(v.id) FILTER (WHERE v.status IN (@conclusive@)) AS first_conclusive_id
  FROM agronomy_verifications v
  JOIN touched_plans tp ON tp.plan_id=v.plan_id
  GROUP BY v.plan_id, v.cycle
),
first_conclusive AS (
  SELECT v.field_id, v.status, v.created_at, v.completed_at
  FROM cycle_verifications cv
  JOIN agronomy_verifications v ON v.id=cv.first_conclusive_id
),
cycle_resolutions AS (
  -- A verified cycle ends exactly once: closed (close/override_close) or
  -- returned for another cycle (rework/reinspection after verification).
  SELECT ce.field_id, ce.occurred_at,
    CASE WHEN ce.event_type IN ('close','override_close')
      THEN 'cycle_closed' ELSE 'cycle_returned_for_rework' END AS fact,
    COALESCE(cv.final_status, 'PENDING_DATA') AS outcome
  FROM cycle_events ce
  LEFT JOIN cycle_verifications cv ON cv.plan_id=ce.plan_id AND cv.cycle=ce.cycle
  WHERE (ce.event_type IN ('close','override_close')
         OR (ce.event_type IN ('rework','reinspection') AND ce.from_status='pending_verification'))
    AND @resolution_in_period@
),
flow_facts AS (
  SELECT a.field_id, 'candidate_detected'::text AS fact, NULL::text AS detail, a.created_at AS anchor
  FROM autonomous_anomaly_candidates a
  WHERE @candidate_in_period@
    AND EXISTS (SELECT 1 FROM scope_fields sf
                WHERE sf.field_id=a.field_id AND sf.enterprise_id=a.enterprise_id)
  UNION ALL
  SELECT field_id, 'inspection_opened', source_kind, created_at
  FROM period_inspections WHERE @created_in_period@
  UNION ALL
  SELECT field_id, 'inspection_' || status, NULL, reviewed_at
  FROM period_inspections
  WHERE status IN ('confirmed','rejected') AND @reviewed_in_period@
  UNION ALL
  SELECT field_id, 'inspection_cancelled', NULL, cancelled_at
  FROM period_inspections WHERE status='cancelled' AND @cancelled_in_period@
  UNION ALL
  SELECT p.field_id, 'plan_drafted', NULL, p.created_at
  FROM agronomy_plans p
  WHERE @plan_created_in_period@
    AND EXISTS (SELECT 1 FROM scope_fields sf
                WHERE sf.field_id=p.field_id AND sf.enterprise_id=p.enterprise_id)
  UNION ALL
  SELECT field_id, 'cycle_approved', NULL, occurred_at
  FROM cycle_events WHERE event_type='approve' AND @event_occurred_in_period@
  UNION ALL
  SELECT field_id, 'cycle_work_completed', NULL, occurred_at
  FROM cycle_events
  WHERE event_type='work_complete' AND resulting_status='pending_verification'
    AND @event_occurred_in_period@
  UNION ALL
  SELECT field_id, 'cycle_verified', status, created_at
  FROM first_conclusive WHERE @verified_in_period@
  UNION ALL
  SELECT field_id, fact, outcome, occurred_at FROM cycle_resolutions
  UNION ALL
  SELECT field_id,
    CASE WHEN event_type='reopen' THEN 'reopened_after_closure'
      ELSE 'reopened_after_verification' END,
    NULL, occurred_at
  FROM cycle_events
  WHERE (event_type='reopen'
         OR (event_type IN ('rework','reinspection') AND from_status='pending_verification'))
    AND @event_occurred_in_period@
  UNION ALL
  SELECT field_id, 'plan_cancelled', NULL, occurred_at
  FROM cycle_events WHERE event_type='cancel' AND @event_occurred_in_period@
),
first_plans AS (
  SELECT p.inspection_id, min(p.created_at) AS first_plan_at
  FROM agronomy_plans p
  WHERE p.inspection_id IN (
      SELECT recent.inspection_id FROM agronomy_plans recent
      WHERE @recent_plan_in_period@)
    AND EXISTS (SELECT 1 FROM scope_fields sf
                WHERE sf.field_id=p.field_id AND sf.enterprise_id=p.enterprise_id)
  GROUP BY p.inspection_id
),
duration_samples AS (
  SELECT 'signal_to_inspection_opened'::text AS metric,
    extract(epoch FROM (i.created_at - a.created_at))/3600.0 AS hours
  FROM field_inspections i
  JOIN autonomous_anomaly_candidates a
    ON a.inspection_id=i.id AND a.field_id=i.field_id AND a.enterprise_id=i.enterprise_id
  WHERE i.source_kind <> 'legacy' AND @inspection_created_in_period@
    AND i.created_at >= a.created_at
    AND EXISTS (SELECT 1 FROM scope_fields sf
                WHERE sf.field_id=i.field_id AND sf.enterprise_id=i.enterprise_id)
  UNION ALL
  SELECT 'inspection_opened_to_reviewed', extract(epoch FROM (reviewed_at - created_at))/3600.0
  FROM period_inspections
  WHERE status IN ('confirmed','rejected') AND @reviewed_in_period@ AND reviewed_at >= created_at
  UNION ALL
  SELECT 'inspection_submitted_to_plan_drafted',
    extract(epoch FROM (fp.first_plan_at - i.submitted_at))/3600.0
  FROM first_plans fp
  JOIN field_inspections i ON i.id=fp.inspection_id
  WHERE @first_plan_in_period@ AND i.submitted_at IS NOT NULL AND fp.first_plan_at >= i.submitted_at
  UNION ALL
  SELECT 'plan_approved_to_work_completed', extract(epoch FROM (completed_at - approved_at))/3600.0
  FROM cycle_milestones
  WHERE @completed_in_period@ AND approved_at IS NOT NULL AND completed_at >= approved_at
  UNION ALL
  SELECT 'work_completed_to_verified', extract(epoch FROM (created_at - completed_at))/3600.0
  FROM first_conclusive
  WHERE @verified_in_period@ AND completed_at IS NOT NULL AND created_at >= completed_at
  UNION ALL
  SELECT 'case_opened_to_verified_closure', extract(epoch FROM (p.closed_at - i.created_at))/3600.0
  FROM agronomy_plans p
  JOIN field_inspections i ON i.id=p.inspection_id AND i.enterprise_id=p.enterprise_id
  WHERE p.status='closed' AND p.verification_status IN (@conclusive@)
    AND @plan_closed_in_period@ AND p.closed_at >= i.created_at
    AND EXISTS (SELECT 1 FROM scope_fields sf
                WHERE sf.field_id=p.field_id AND sf.enterprise_id=p.enterprise_id)
),
duration_stats AS (
  SELECT metric, count(*)::integer AS sample_count,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY hours) AS median_hours,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY hours) AS p90_hours
  FROM duration_samples
  GROUP BY metric
),
current_totals AS (
  SELECT count(DISTINCT field_id)::integer AS fields_affected,
    @current_columns@
  FROM problem_cases
),
work_totals AS (
  SELECT count(*)::integer AS active,
    count(*) FILTER (WHERE status='planned')::integer AS planned,
    count(*) FILTER (WHERE status='in_progress')::integer AS in_progress,
    count(*) FILTER (WHERE assigned_to_id IS NULL)::integer AS unassigned,
    count(*) FILTER (WHERE due_at < :as_of)::integer AS overdue
  FROM live_work
),
coverage_totals AS (
  SELECT count(*)::integer AS fields_in_scope,
    count(*) FILTER (WHERE monitored)::integer AS monitored_fields,
    (SELECT count(DISTINCT pc.field_id)::integer FROM problem_cases pc
     JOIN scope_fields affected ON affected.field_id=pc.field_id
     WHERE affected.monitored) AS monitored_fields_with_problems,
    @freshness_columns@
  FROM scope_fields
),
data_totals AS (
  SELECT count(*) FILTER (WHERE root_source='freshness')::integer AS freshness_cases,
    count(*) FILTER (WHERE root_source='external')::integer AS external_cases
  FROM scoped_cases
  WHERE remediation_status='@data_unavailable@'
),
flow_totals AS (
  SELECT @flow_columns@
  FROM flow_facts
),
completion_totals AS (
  SELECT
    (SELECT count(*)::integer FROM cycle_milestones
     WHERE @approved_in_period@) AS work_denominator,
    (SELECT count(*)::integer FROM cycle_milestones
     WHERE @approved_in_period@ AND completed_at IS NOT NULL) AS work_completed,
    (SELECT count(*)::integer FROM cycle_milestones
     WHERE @approved_in_period@ AND completed_at IS NULL AND abandoned_at IS NOT NULL)
      AS work_ended_without_completion,
    (SELECT count(*)::integer FROM cycle_milestones
     WHERE @completed_in_period@) AS verification_denominator,
    (SELECT count(*)::integer FROM cycle_milestones cm
     JOIN cycle_verifications cv ON cv.plan_id=cm.plan_id AND cv.cycle=cm.cycle
     WHERE @cm_completed_in_period@ AND cv.final_status='IMPROVED') AS verification_improved,
    (SELECT count(*)::integer FROM cycle_milestones cm
     JOIN cycle_verifications cv ON cv.plan_id=cm.plan_id AND cv.cycle=cm.cycle
     WHERE @cm_completed_in_period@ AND cv.final_status='NO_MATERIAL_CHANGE') AS verification_unchanged,
    (SELECT count(*)::integer FROM cycle_milestones cm
     JOIN cycle_verifications cv ON cv.plan_id=cm.plan_id AND cv.cycle=cm.cycle
     WHERE @cm_completed_in_period@ AND cv.final_status='WORSENED') AS verification_worsened,
    (SELECT count(*)::integer FROM agronomy_plans p
     WHERE @plan_created_in_period@ AND p.status<>'superseded'
       AND EXISTS (SELECT 1 FROM scope_fields sf
                   WHERE sf.field_id=p.field_id AND sf.enterprise_id=p.enterprise_id))
      AS plan_denominator,
    (SELECT count(*)::integer FROM agronomy_plans p
     WHERE @plan_created_in_period@ AND p.status='closed'
       AND EXISTS (SELECT 1 FROM scope_fields sf
                   WHERE sf.field_id=p.field_id AND sf.enterprise_id=p.enterprise_id))
      AS plan_closed,
    (SELECT count(*)::integer FROM agronomy_plans p
     WHERE @plan_created_in_period@ AND p.status='closed' AND p.verification_status='IMPROVED'
       AND EXISTS (SELECT 1 FROM scope_fields sf
                   WHERE sf.field_id=p.field_id AND sf.enterprise_id=p.enterprise_id))
      AS plan_closed_improved,
    (SELECT count(*)::integer FROM agronomy_plans p
     WHERE @plan_created_in_period@ AND p.status='cancelled'
       AND EXISTS (SELECT 1 FROM scope_fields sf
                   WHERE sf.field_id=p.field_id AND sf.enterprise_id=p.enterprise_id))
      AS plan_cancelled
),
field_current AS (
  SELECT field_id,
    @breakdown_current_columns@
  FROM problem_cases
  GROUP BY field_id
),
field_work AS (
  SELECT field_id, count(*) FILTER (WHERE due_at < :as_of)::integer AS overdue_work_items
  FROM live_work
  GROUP BY field_id
),
field_flow AS (
  SELECT field_id,
    @breakdown_period_columns@
  FROM flow_facts
  GROUP BY field_id
),
field_rows AS (
  SELECT sf.field_id, sf.field_name, sf.enterprise_id, sf.enterprise_name,
    sf.crop_type_id, sf.crop_name,
    CASE WHEN sf.monitored THEN 1 ELSE 0 END AS monitored_fields,
    @field_row_columns@
  FROM scope_fields sf
  LEFT JOIN field_current fc ON fc.field_id=sf.field_id
  LEFT JOIN field_work fw ON fw.field_id=sf.field_id
  LEFT JOIN field_flow ff ON ff.field_id=sf.field_id
),
bucket_rows AS (
  SELECT date_trunc(:granularity, anchor AT TIME ZONE 'Asia/Tashkent')::date AS bucket_start,
    @bucket_columns@
  FROM flow_facts
  GROUP BY 1
)
SELECT
  (SELECT row_to_json(t) FROM current_totals t) AS current_totals,
  (SELECT row_to_json(t) FROM work_totals t) AS work_totals,
  (SELECT row_to_json(t) FROM coverage_totals t) AS coverage_totals,
  (SELECT row_to_json(t) FROM data_totals t) AS data_totals,
  (SELECT row_to_json(t) FROM flow_totals t) AS flow_totals,
  (SELECT row_to_json(t) FROM completion_totals t) AS completion_totals,
  COALESCE((SELECT json_agg(row_to_json(t) ORDER BY t.metric) FROM duration_stats t),
    '[]'::json) AS durations,
  COALESCE((SELECT json_agg(row_to_json(t) ORDER BY t.position) FROM (
      SELECT row_number() OVER (ORDER BY min(enterprise_name), enterprise_id) AS position,
        enterprise_id, min(enterprise_name) AS enterprise_name,
        @breakdown_sums@
      FROM field_rows GROUP BY enterprise_id
      ORDER BY min(enterprise_name), enterprise_id LIMIT :enterprise_fetch) t),
    '[]'::json) AS enterprises,
  COALESCE((SELECT json_agg(row_to_json(t) ORDER BY t.position) FROM (
      SELECT row_number() OVER (ORDER BY min(crop_name) NULLS LAST, crop_type_id NULLS LAST) AS position,
        crop_type_id, min(crop_name) AS crop_name,
        @breakdown_sums@
      FROM field_rows GROUP BY crop_type_id
      ORDER BY min(crop_name) NULLS LAST, crop_type_id NULLS LAST LIMIT :crop_fetch) t),
    '[]'::json) AS crops,
  COALESCE((SELECT json_agg(row_to_json(t) ORDER BY t.bucket_start) FROM bucket_rows t),
    '[]'::json) AS buckets,
  COALESCE((SELECT json_agg(row_to_json(t) ORDER BY t.position) FROM (
      SELECT row_number() OVER (
          ORDER BY active_problems DESC, overdue_cases DESC, period_resolved_cycles DESC,
            field_name, field_id) AS position,
        field_rows.*
      FROM field_rows
      ORDER BY active_problems DESC, overdue_cases DESC, period_resolved_cycles DESC,
        field_name, field_id
      LIMIT :field_limit OFFSET :field_offset) t),
    '[]'::json) AS fields,
  (SELECT count(*)::integer FROM scope_fields) AS field_total
"""


def _field_row_columns() -> str:
    current = [f"COALESCE(fc.{name},0) AS {name}" for name in BREAKDOWN_CURRENT_COLUMNS]
    work = ["COALESCE(fw.overdue_work_items,0) AS overdue_work_items"]
    period = [f"COALESCE(ff.{name},0) AS period_{name}" for name in BREAKDOWN_PERIOD_COLUMNS]
    return ",\n    ".join(current + work + period)


def _freshness_columns() -> str:
    return ",\n    ".join(
        f"(SELECT count(*)::integer FROM field_freshness WHERE ndvi_status='{status}') "
        f"AS ndvi_{status.lower()}"
        for status in NDVI_FRESHNESS
    )


_SUBSTITUTIONS = {
    "@active_states@": _quoted(ACTIVE_STATES),
    "@conclusive@": _quoted(CONCLUSIVE),
    "@data_unavailable@": rs.DATA_UNAVAILABLE,
    "@current_columns@": _count_columns(CURRENT_COLUMNS),
    "@flow_columns@": _count_columns(FLOW_COLUMNS, "  "),
    "@bucket_columns@": _count_columns(BUCKET_COLUMNS),
    "@breakdown_current_columns@": _count_columns(BREAKDOWN_CURRENT_COLUMNS),
    "@breakdown_period_columns@": _count_columns(BREAKDOWN_PERIOD_COLUMNS),
    "@field_row_columns@": _field_row_columns(),
    "@freshness_columns@": _freshness_columns(),
    "@breakdown_sums@": _sum_columns(BREAKDOWN_NUMBERS, "        "),
    "@created_in_period@": _in_period("created_at"),
    "@reviewed_in_period@": _in_period("reviewed_at"),
    "@cancelled_in_period@": _in_period("cancelled_at"),
    "@event_in_period@": _in_period("ev.occurred_at"),
    "@event_occurred_in_period@": _in_period("occurred_at"),
    "@resolution_in_period@": _in_period("ce.occurred_at"),
    "@candidate_in_period@": _in_period("a.created_at"),
    "@plan_created_in_period@": _in_period("p.created_at"),
    "@recent_plan_in_period@": _in_period("recent.created_at"),
    "@inspection_created_in_period@": _in_period("i.created_at"),
    "@first_plan_in_period@": _in_period("fp.first_plan_at"),
    "@completed_in_period@": _in_period("completed_at"),
    "@cm_completed_in_period@": _in_period("cm.completed_at"),
    "@approved_in_period@": _in_period("approved_at"),
    "@verified_in_period@": _in_period("created_at"),
    "@plan_closed_in_period@": _in_period("p.closed_at"),
}


def _expand(template: str) -> str:
    for token, value in _SUBSTITUTIONS.items():
        template = template.replace(token, value)
    return template


# The scope tokens are the only per-request variation and are chosen from
# fixed fragments; every value travels as a bound parameter.
STATEMENT_TEMPLATE = CASES_CTE + _expand(_TEMPLATE)
if "@" in STATEMENT_TEMPLATE.replace("@field_scope@", "").replace(
    "@case_scope@", ""
).replace("@external_allowed@", ""):
    raise RuntimeError("management analytics SQL has an unresolved token")

# One fingerprint of every definition the response depends on: the case model,
# the projection vocabulary and this module's statement and metric catalogue.
# tests/test_task232_management_analytics_contract.py pins it, so a change to
# any of them fails until definitions_version is reviewed.
DEFINITIONS_FINGERPRINT = hashlib.sha256(
    "\n".join((
        DEFINITIONS_VERSION,
        STATEMENT_TEMPLATE,
        rs.inspection_status_sql("i", "p"),
        repr(sorted(DURATIONS.items())),
        VERIFICATION_POLICY,
        str(MIN_SAMPLES_FOR_P90),
    )).encode("utf-8")
).hexdigest()


# ── scope ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AnalyticsScope:
    role: str
    user_id: int
    authorization: str
    enterprise_id: int | None
    field_id: int | None
    crop_type_id: int | None


def resolve_scope(user, *, enterprise_id=None, field_id=None, crop_type_id=None) -> AnalyticsScope:
    """Server-side authority first; request filters can only narrow it.

    A manager is bound to the user's own enterprise. Asking for any other
    enterprise answers the same 404 as a non-existent one, so the response
    reveals nothing about other tenants.
    """
    role = normalize_role(user)
    if role not in ALLOWED_ROLES:
        raise HTTPException(403, "Unknown role")
    if role not in MANAGEMENT_ROLES:
        raise HTTPException(403, "Management analytics requires a management role")
    if role == "manager":
        if user.enterprise_id is None:
            raise HTTPException(403, "Manager has no enterprise_id")
        if enterprise_id is not None and enterprise_id != user.enterprise_id:
            raise HTTPException(404, "Enterprise not found")
        return AnalyticsScope(role, int(user.id), "tenant", int(user.enterprise_id), field_id, crop_type_id)
    return AnalyticsScope(role, int(user.id), "global", enterprise_id, field_id, crop_type_id)


def _validate_targets(db, scope: AnalyticsScope, requested_enterprise_id) -> None:
    """One existence check for the narrowing targets; non-enumerating 404s."""
    checks, params = [], {}
    if requested_enterprise_id is not None:
        checks.append("EXISTS (SELECT 1 FROM enterprises WHERE id=:enterprise_id) AS enterprise_found")
        params["enterprise_id"] = requested_enterprise_id
    if scope.field_id is not None:
        tenant = " AND f.enterprise_id=:scope_enterprise_id" if scope.enterprise_id is not None else ""
        checks.append(f"EXISTS (SELECT 1 FROM fields f WHERE f.id=:field_id{tenant}) AS field_found")
        params["field_id"] = scope.field_id
        if scope.enterprise_id is not None:
            params["scope_enterprise_id"] = scope.enterprise_id
    if scope.crop_type_id is not None:
        checks.append("EXISTS (SELECT 1 FROM crop_types WHERE id=:crop_type_id) AS crop_found")
        params["crop_type_id"] = scope.crop_type_id
    if not checks:
        return
    found = db.execute(text("SELECT " + ", ".join(checks)), params).mappings().one()
    if requested_enterprise_id is not None and not found["enterprise_found"]:
        raise HTTPException(404, "Enterprise not found")
    if scope.field_id is not None and not found["field_found"]:
        raise HTTPException(404, "Field not found")
    if scope.crop_type_id is not None and not found["crop_found"]:
        raise HTTPException(404, "Crop type not found")


def statement_for(scope: AnalyticsScope) -> str:
    field_scope, case_scope = ["true"], ["true"]
    if scope.enterprise_id is not None:
        field_scope.append("f.enterprise_id=:scope_enterprise_id")
        case_scope.append("c.enterprise_id=:scope_enterprise_id")
    if scope.field_id is not None:
        field_scope.append("f.id=:field_id")
        case_scope.append("c.field_id=:field_id")
    if scope.crop_type_id is not None:
        field_scope.append("crop.crop_type_id=:crop_type_id")
    external = "true" if scope.field_id is None and scope.crop_type_id is None else "false"
    return (
        STATEMENT_TEMPLATE
        .replace("@field_scope@", " AND ".join(field_scope))
        .replace("@case_scope@", " AND ".join(case_scope))
        .replace("@external_allowed@", external)
    )


# ── assembly ─────────────────────────────────────────────────────────────────


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def _hours(value) -> float | None:
    return None if value is None else round(float(value), 2)


def duration_metric(name: str, stats: dict | None) -> dict:
    """Median from one sample; p90 only from MIN_SAMPLES_FOR_P90 samples."""
    count = int((stats or {}).get("sample_count") or 0)
    return {
        **DURATIONS[name],
        "sample_count": count,
        "median_hours": _hours(stats["median_hours"]) if count else None,
        "p90_hours": _hours(stats["p90_hours"]) if count >= MIN_SAMPLES_FOR_P90 else None,
        "status": "measured" if count else "no_samples",
    }


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def bucket_starts(window: DateWindow, granularity: str) -> list[date]:
    """Local calendar buckets covering the window: days, ISO weeks or months."""
    if granularity == "day":
        first, step = window.date_from, lambda value: value + timedelta(days=1)
    elif granularity == "week":
        first = window.date_from - timedelta(days=window.date_from.weekday())
        step = lambda value: value + timedelta(days=7)  # noqa: E731
    else:
        first, step = _month_start(window.date_from), _next_month
    starts, current = [], first
    while current <= window.date_to:
        starts.append(current)
        current = step(current)
    return starts


def _bucket_end(start: date, granularity: str) -> date:
    if granularity == "day":
        return start
    if granularity == "week":
        return start + timedelta(days=6)
    return _next_month(start) - timedelta(days=1)


def _periods(window: DateWindow, granularity: str, rows: list[dict]) -> list[dict]:
    by_start = {date.fromisoformat(str(row["bucket_start"])): row for row in rows}
    periods = []
    for start in bucket_starts(window, granularity):
        row = by_start.get(start, {})
        periods.append({
            "bucket_start": max(start, window.date_from),
            "bucket_end": min(_bucket_end(start, granularity), window.date_to),
            **{name: int(row.get(name) or 0) for name in BUCKET_COLUMNS},
        })
    return periods


def _breakdown_row(row: dict) -> dict:
    return {
        "monitored_fields": int(row["monitored_fields"]),
        "current": {
            **{name: int(row[name]) for name in BREAKDOWN_CURRENT_COLUMNS},
            "overdue_work_items": int(row["overdue_work_items"]),
        },
        "period": {name: int(row[f"period_{name}"]) for name in BREAKDOWN_PERIOD_COLUMNS},
    }


def _current(as_of: datetime, totals: dict, work: dict, data: dict) -> dict:
    n = {key: int(value or 0) for key, value in totals.items()}
    return {
        "as_of": as_of,
        "active_problems": {
            "total": n["active_problems"],
            "fields_affected": n["fields_affected"],
            "by_source": {
                "inspection": n["source_inspection"],
                "candidate": n["source_candidate"],
                "alert": n["source_alert"],
            },
            "by_priority": {
                "critical": n["priority_critical"],
                "high": n["priority_high"],
                "normal": n["priority_normal"],
                "low": n["priority_low"],
            },
            "legacy_open_inspections": n["legacy_open_inspections"],
        },
        "inspection_funnel": {
            "needs_inspection": {
                "total": n["needs_inspection"],
                "inspections": n["needs_inspection_inspections"],
                "candidates": n["needs_inspection_candidates"],
                "alerts": n["needs_inspection_alerts"],
                "unassigned_inspections": n["needs_inspection_unassigned"],
            },
            "inspection_active": n["inspection_active"],
            "awaiting_review": n["awaiting_review"],
            "awaiting_decision": n["awaiting_decision"],
        },
        "remediation_funnel": {
            "plan_active": {
                "total": n["plan_active"],
                "draft": n["plan_active_draft"],
                "approved": n["plan_active_approved"],
            },
            "work_active": n["work_active"],
            "awaiting_satellite_verification": {
                "total": n["awaiting_satellite_verification"],
                "pending_data": n["awaiting_pending_data"],
                "too_early": n["awaiting_too_early"],
            },
            "verification_blocked": {
                "total": n["verification_blocked"],
                "cloud_blocked": n["blocked_cloud"],
                "quality_blocked": n["blocked_quality"],
                "provider_degraded": n["blocked_provider"],
                "inconclusive": n["blocked_inconclusive"],
            },
            "improved_awaiting_closure": n["improved_awaiting_closure"],
            "not_improved": {
                "total": n["not_improved"],
                "unchanged": n["not_improved_unchanged"],
                "worsened": n["not_improved_worsened"],
            },
            "reopened": n["reopened"],
        },
        "work_items": {key: int(work[key] or 0) for key in
                       ("active", "planned", "in_progress", "unassigned", "overdue")},
        "overdue": {
            "cases": n["overdue_cases"],
            "inspections": n["overdue_inspections"],
            "plans_with_overdue_work": n["overdue_plans"],
            "work_items": int(work["overdue"] or 0),
        },
        "data_unavailable": {
            "freshness_cases": int(data["freshness_cases"] or 0),
            "external_cases": int(data["external_cases"] or 0),
        },
    }


def _outcomes(flow: dict) -> dict:
    verified = {
        "improved": flow["outcome_improved"],
        "unchanged": flow["outcome_unchanged"],
        "worsened": flow["outcome_worsened"],
    }
    unverified = {status.lower(): flow[f"unverified_{status.lower()}"] for status in NOT_CONCLUSIVE}
    return {
        "resolved_cycles": flow["resolved_cycles"],
        "verified": {**verified, "total": sum(verified.values())},
        "unverified": {"total": sum(unverified.values()), **unverified},
        "closed": {
            "total": flow["closed"],
            "improved": flow["closed_improved"],
            "without_improvement": flow["closed"] - flow["closed_improved"],
        },
        "returned_for_rework": flow["returned_for_rework"],
        "reopened": {
            "total": flow["reopened_after_closure"] + flow["reopened_after_verification"],
            "after_closure": flow["reopened_after_closure"],
            "after_verification": flow["reopened_after_verification"],
        },
    }


def _period_activity(flow: dict) -> dict:
    return {
        "anomaly_candidates_detected": flow["anomaly_candidates_detected"],
        "inspections_opened": {
            "total": flow["inspections_opened"],
            "manual": flow["inspections_opened_manual"],
            "alert": flow["inspections_opened_alert"],
            "pixel_ndvi": flow["inspections_opened_pixel_ndvi"],
        },
        "inspections_confirmed": flow["inspections_confirmed"],
        "inspections_rejected": flow["inspections_rejected"],
        "inspections_cancelled": flow["inspections_cancelled"],
        "plans_drafted": flow["plans_drafted"],
        "plan_cycles_approved": flow["plan_cycles_approved"],
        "plan_cycles_work_completed": flow["plan_cycles_work_completed"],
        "plan_cycles_verified": flow["plan_cycles_verified"],
        "plans_cancelled": flow["plans_cancelled"],
    }


def _completion(totals: dict) -> dict:
    n = {key: int(value or 0) for key, value in totals.items()}
    work_open = n["work_denominator"] - n["work_completed"] - n["work_ended_without_completion"]
    conclusive = n["verification_improved"] + n["verification_unchanged"] + n["verification_worsened"]
    plan_open = n["plan_denominator"] - n["plan_closed"] - n["plan_cancelled"]
    return {
        "work_completion": {
            "population": "plan cycles approved in the period (agronomy_events 'approve')",
            "numerator": n["work_completed"],
            "denominator": n["work_denominator"],
            "rate": _rate(n["work_completed"], n["work_denominator"]),
            "completed": n["work_completed"],
            "ended_without_completion": n["work_ended_without_completion"],
            "open": work_open,
        },
        "verification_completion": {
            "population": "plan cycles whose required work was completed in the period",
            "numerator": conclusive,
            "denominator": n["verification_denominator"],
            "rate": _rate(conclusive, n["verification_denominator"]),
            "improved": n["verification_improved"],
            "unchanged": n["verification_unchanged"],
            "worsened": n["verification_worsened"],
            "not_conclusive": n["verification_denominator"] - conclusive,
            "improved_rate": _rate(n["verification_improved"], n["verification_denominator"]),
        },
        "plan_closure": {
            "population": "agronomy plans drafted in the period and not superseded",
            "numerator": n["plan_closed"],
            "denominator": n["plan_denominator"],
            "rate": _rate(n["plan_closed"], n["plan_denominator"]),
            "closed_improved": n["plan_closed_improved"],
            "closed_without_improvement": n["plan_closed"] - n["plan_closed_improved"],
            "cancelled": n["plan_cancelled"],
            "open": plan_open,
        },
    }


def _coverage(totals: dict) -> dict:
    n = {key: int(value or 0) for key, value in totals.items()}
    return {
        "fields_in_scope": n["fields_in_scope"],
        "monitored_fields": n["monitored_fields"],
        "inactive_fields": n["fields_in_scope"] - n["monitored_fields"],
        "monitored_fields_with_active_problems": n["monitored_fields_with_problems"],
        "ndvi_freshness": {status.lower(): n[f"ndvi_{status.lower()}"] for status in NDVI_FRESHNESS},
    }


def _provenance() -> dict:
    return {
        "lifecycle": "task220_canonical_remediation",
        "inspection_workflow": "task217_canonical_inspection",
        "status_projection": "task225_remediation_status",
        "case_model": "task221_operational_center_cases",
        "verification_policy_version": VERIFICATION_POLICY,
        "sources": list(LIFECYCLE_SOURCES),
        "excluded_legacy_sources": list(EXCLUDED_LEGACY_SOURCES),
        "definitions_fingerprint": DEFINITIONS_FINGERPRINT,
    }


def snapshot(
    db,
    user,
    *,
    date_from=None,
    date_to=None,
    enterprise_id=None,
    field_id=None,
    crop_type_id=None,
    granularity="week",
    field_limit=50,
    field_offset=0,
    now: datetime | None = None,
) -> dict:
    """Build the versioned management snapshot for ``user``."""
    as_of = now or datetime.now(timezone.utc)
    generated_at = as_of.astimezone(TASHKENT)
    scope = resolve_scope(user, enterprise_id=enterprise_id, field_id=field_id,
                          crop_type_id=crop_type_id)
    window = resolve_window(date_from, date_to, today=generated_at.date())
    if granularity not in GRANULARITIES:
        raise HTTPException(422, "granularity must be day, week or month")
    params = {
        "as_of": as_of,
        "as_of_date": generated_at.date(),
        "actor_user_id": scope.user_id,
        "period_start": window.from_timestamp,
        "period_end": window.to_exclusive,
        "granularity": granularity,
        "field_limit": field_limit,
        "field_offset": field_offset,
        "enterprise_fetch": fetch_limit(ENTERPRISE_ROW_CAP),
        "crop_fetch": fetch_limit(CROP_ROW_CAP),
    }
    if scope.enterprise_id is not None:
        params["scope_enterprise_id"] = scope.enterprise_id
    if scope.field_id is not None:
        params["field_id"] = scope.field_id
    if scope.crop_type_id is not None:
        params["crop_type_id"] = scope.crop_type_id
    try:
        _validate_targets(db, scope, enterprise_id)
        row = db.execute(text(statement_for(scope)), params).mappings().one()
        enterprises = list(row["enterprises"] or [])
        crops = list(row["crops"] or [])
        ensure_within_row_cap(enterprises, row_cap=ENTERPRISE_ROW_CAP, resource="management_analytics.enterprises")
        ensure_within_row_cap(crops, row_cap=CROP_ROW_CAP, resource="management_analytics.crops")
        flow = {key: int(value or 0) for key, value in row["flow_totals"].items()}
        durations = {item["metric"]: item for item in (row["durations"] or [])}
        return {
            "definitions_version": DEFINITIONS_VERSION,
            "generated_at": generated_at,
            "timezone": TIMEZONE,
            "scope": {
                "role": scope.role,
                "authorization": scope.authorization,
                "enterprise_id": scope.enterprise_id,
                "field_id": scope.field_id,
                "crop_type_id": scope.crop_type_id,
            },
            "period": {
                "requested": {"date_from": date_from, "date_to": date_to},
                "effective": {
                    "date_from": window.date_from,
                    "date_to": window.date_to,
                    "inclusive": True,
                    "days": (window.date_to - window.date_from).days + 1,
                    "starts_at": window.from_timestamp,
                    "ends_before": window.to_exclusive,
                    "granularity": granularity,
                },
            },
            "provenance": _provenance(),
            "coverage": _coverage(row["coverage_totals"]),
            "current": _current(generated_at, row["current_totals"], row["work_totals"], row["data_totals"]),
            "period_activity": _period_activity(flow),
            "outcomes": _outcomes(flow),
            "cycle_times": {
                "unit": "hours",
                "p90_minimum_samples": MIN_SAMPLES_FOR_P90,
                "metrics": {name: duration_metric(name, durations.get(name)) for name in DURATIONS},
                "unsupported": [dict(item) for item in UNSUPPORTED_DURATIONS],
            },
            "completion": _completion(row["completion_totals"]),
            "breakdowns": {
                "enterprises": [
                    {"enterprise_id": item["enterprise_id"], "enterprise_name": item["enterprise_name"],
                     **_breakdown_row(item)}
                    for item in enterprises
                ],
                "crops": [
                    {"crop_type_id": item["crop_type_id"], "crop_name": item["crop_name"],
                     **_breakdown_row(item)}
                    for item in crops
                ],
                "periods": _periods(window, granularity, list(row["buckets"] or [])),
                "fields": {
                    "items": [
                        {
                            "field_id": item["field_id"], "field_name": item["field_name"],
                            "enterprise_id": item["enterprise_id"],
                            "enterprise_name": item["enterprise_name"],
                            "crop_type_id": item["crop_type_id"], "crop_name": item["crop_name"],
                            **_breakdown_row(item),
                        }
                        for item in (row["fields"] or [])
                    ],
                    "total": int(row["field_total"] or 0),
                    "limit": field_limit,
                    "offset": field_offset,
                },
            },
            "limitations": list(LIMITATIONS),
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
