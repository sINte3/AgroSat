"""Canonical current-remediation status projection (TASK_225).

One definition of "where is this case now", derived only from the canonical
lifecycles: the TASK_217 inspection (``field_inspections``) and its current
TASK_220 plan (``agronomy_plans``). The retired TASK_209/TASK_217
``corrective_actions`` rows never contribute to current status.

Readers embed :func:`inspection_status_sql` in their own bounded, tenant-scoped
SQL, so the projection costs no extra query and cannot drift between readers.
:func:`inspection_status` is the same decision table in Python, used by tests
to pin the SQL to it.
"""

from __future__ import annotations


NEEDS_INSPECTION = "needs_inspection"
INSPECTION_ACTIVE = "inspection_active"
AWAITING_REVIEW = "awaiting_review"
AWAITING_DECISION = "awaiting_decision"
PLAN_ACTIVE = "plan_active"
WORK_ACTIVE = "work_active"
AWAITING_SATELLITE_VERIFICATION = "awaiting_satellite_verification"
VERIFICATION_BLOCKED = "verification_blocked"
IMPROVED_AWAITING_CLOSURE = "improved_awaiting_closure"
NOT_IMPROVED = "not_improved"
REOPENED = "reopened"
IMPROVED_CLOSED = "improved_closed"
CLOSED_WITHOUT_IMPROVEMENT = "closed_without_improvement"
REJECTED = "rejected"
CANCELLED = "cancelled"
INSPECTION_CLOSED = "inspection_closed"
DATA_UNAVAILABLE = "data_unavailable"

REMEDIATION_STATES = (
    NEEDS_INSPECTION, INSPECTION_ACTIVE, AWAITING_REVIEW, AWAITING_DECISION,
    PLAN_ACTIVE, WORK_ACTIVE, AWAITING_SATELLITE_VERIFICATION, VERIFICATION_BLOCKED,
    IMPROVED_AWAITING_CLOSURE, NOT_IMPROVED, REOPENED, IMPROVED_CLOSED,
    CLOSED_WITHOUT_IMPROVEMENT, REJECTED, CANCELLED, INSPECTION_CLOSED, DATA_UNAVAILABLE,
)
TERMINAL_STATES = frozenset({
    IMPROVED_CLOSED, CLOSED_WITHOUT_IMPROVEMENT, REJECTED, CANCELLED, INSPECTION_CLOSED,
})

BLOCKED_VERIFICATION = ("CLOUD_BLOCKED", "QUALITY_BLOCKED", "PROVIDER_DEGRADED", "INCONCLUSIVE")
NOT_IMPROVED_VERIFICATION = ("NO_MATERIAL_CHANGE", "WORSENED")

# The Operational Center's published ``operational_status`` vocabulary, kept for
# the current frontend; ``closed_without_improvement`` is new (TASK_225): a plan
# closed without an IMPROVED verification is no longer reported as improved.
OPERATIONAL_STATUS = {
    NEEDS_INSPECTION: "awaiting_inspection",
    INSPECTION_ACTIVE: "awaiting_inspection",
    AWAITING_REVIEW: "awaiting_review",
    AWAITING_DECISION: "awaiting_work",
    PLAN_ACTIVE: "awaiting_work",
    WORK_ACTIVE: "awaiting_evidence",
    AWAITING_SATELLITE_VERIFICATION: "awaiting_verification",
    VERIFICATION_BLOCKED: "awaiting_verification",
    IMPROVED_AWAITING_CLOSURE: "awaiting_verification",
    NOT_IMPROVED: "awaiting_verification",
    REOPENED: "awaiting_work",
    IMPROVED_CLOSED: "improved_closed",
    CLOSED_WITHOUT_IMPROVEMENT: "closed_without_improvement",
    REJECTED: "closed_without_improvement",
    CANCELLED: "closed_without_improvement",
    INSPECTION_CLOSED: "closed_without_improvement",
}


def _quoted(values) -> str:
    return ",".join(f"'{value}'" for value in values)


def inspection_status(inspection_status_value, plan_status=None, verification_status=None) -> str:
    """The decision table, in Python. ``plan_status`` is the current plan's, or None."""
    if plan_status == "closed":
        return IMPROVED_CLOSED if verification_status == "IMPROVED" else CLOSED_WITHOUT_IMPROVEMENT
    if plan_status == "pending_verification":
        if verification_status == "IMPROVED":
            return IMPROVED_AWAITING_CLOSURE
        if verification_status in NOT_IMPROVED_VERIFICATION:
            return NOT_IMPROVED
        if verification_status in BLOCKED_VERIFICATION:
            return VERIFICATION_BLOCKED
        return AWAITING_SATELLITE_VERIFICATION
    if plan_status == "rework":
        return REOPENED
    if plan_status == "in_progress":
        return WORK_ACTIVE
    if plan_status in {"draft", "approved"}:
        return PLAN_ACTIVE
    if inspection_status_value == "confirmed":
        return AWAITING_DECISION
    if inspection_status_value == "submitted":
        return AWAITING_REVIEW
    if inspection_status_value == "in_progress":
        return INSPECTION_ACTIVE
    if inspection_status_value in {"pending", "new", "assigned"}:
        return NEEDS_INSPECTION
    if inspection_status_value == "rejected":
        return REJECTED
    if inspection_status_value == "cancelled":
        return CANCELLED
    return INSPECTION_CLOSED


def inspection_status_sql(inspection: str = "i", plan: str = "p") -> str:
    """SQL CASE over an inspection row and its current plan row (NULL when none).

    The plan row must be the inspection's current plan: its one non-terminal
    plan or, failing that, its most recent closed plan. Cancelled and
    superseded plans are not current.
    """
    i, p = inspection, plan
    return (
        "CASE"
        f" WHEN {p}.status='closed' AND {p}.verification_status='IMPROVED' THEN '{IMPROVED_CLOSED}'"
        f" WHEN {p}.status='closed' THEN '{CLOSED_WITHOUT_IMPROVEMENT}'"
        f" WHEN {p}.status='pending_verification' AND {p}.verification_status='IMPROVED' THEN '{IMPROVED_AWAITING_CLOSURE}'"
        f" WHEN {p}.status='pending_verification' AND {p}.verification_status IN ({_quoted(NOT_IMPROVED_VERIFICATION)}) THEN '{NOT_IMPROVED}'"
        f" WHEN {p}.status='pending_verification' AND {p}.verification_status IN ({_quoted(BLOCKED_VERIFICATION)}) THEN '{VERIFICATION_BLOCKED}'"
        f" WHEN {p}.status='pending_verification' THEN '{AWAITING_SATELLITE_VERIFICATION}'"
        f" WHEN {p}.status='rework' THEN '{REOPENED}'"
        f" WHEN {p}.status='in_progress' THEN '{WORK_ACTIVE}'"
        f" WHEN {p}.status IN ('draft','approved') THEN '{PLAN_ACTIVE}'"
        f" WHEN {i}.status='confirmed' THEN '{AWAITING_DECISION}'"
        f" WHEN {i}.status='submitted' THEN '{AWAITING_REVIEW}'"
        f" WHEN {i}.status='in_progress' THEN '{INSPECTION_ACTIVE}'"
        f" WHEN {i}.status IN ('pending','new','assigned') THEN '{NEEDS_INSPECTION}'"
        f" WHEN {i}.status='rejected' THEN '{REJECTED}'"
        f" WHEN {i}.status='cancelled' THEN '{CANCELLED}'"
        f" ELSE '{INSPECTION_CLOSED}' END"
    )


def operational_status_sql(state_sql: str) -> str:
    """Map a canonical state expression onto the published operational vocabulary."""
    branches = " ".join(
        f"WHEN '{state}' THEN '{legacy}'" for state, legacy in OPERATIONAL_STATUS.items()
    )
    return f"CASE ({state_sql}) {branches} ELSE 'awaiting_inspection' END"


def blocked_sql(plan: str = "p") -> str:
    """Verification that cannot conclude: external data blocked or inconclusive."""
    return (
        f"({plan}.status='pending_verification' AND "
        f"{plan}.verification_status IN ({_quoted(BLOCKED_VERIFICATION)}))"
    )
