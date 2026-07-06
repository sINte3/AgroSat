"""
Satellite alert candidate generation from agronomic risk facts.

Read-only by default. Converts agronomic risk engine output (TASK_136)
into controlled satellite alert candidates.

Idempotency is computed client-side: candidates carry a deterministic
idempotency_key based on (field_id, alert_type, source, index_code,
captured_date, reason_code). Persistent apply is deferred until the
alerts table has a dedicated idempotency column (TASK_137B).

No DB writes. No Sentinel Hub calls. No scheduler.
"""
import logging
from datetime import datetime, date
from hashlib import md5
from typing import Any

from sqlalchemy.orm import Session

from services.agronomic_risk_engine import (
    build_agronomic_risk_summary,
    DEFAULT_FRESH_DAYS,
    DEFAULT_FIELD_LIMIT,
    MAX_FIELD_LIMIT,
)

logger = logging.getLogger(__name__)

# ─── Risk reason → alert type mapping ──────────────────────────────

RISK_REASON_TO_ALERT_TYPE: dict[str, str] = {
    "missing_data": "satellite_missing_data",
    "stale_data": "satellite_stale_data",
    "low_ndvi": "vegetation_low_ndvi",
    "ndvi_decline": "vegetation_ndvi_decline",
    "low_ndmi": "water_stress_low_ndmi",
    "ndmi_decline": "water_stress_ndmi_decline",
    "cloudy_observation": "satellite_cloudy_observation",
    "low_valid_pixels": "satellite_low_valid_pixels",
    "suspicious_value": "satellite_suspicious_value",
}

# Minimum severity per risk reason to generate a candidate
# Rules from task spec:
#   cloudy_observation → high only
#   low_valid_pixels → high only
#   Others: medium+ by default (low excluded)
MIN_SEVERITY_PER_REASON: dict[str, str] = {
    "missing_data": "medium",
    "stale_data": "medium",
    "low_ndvi": "high",
    "ndvi_decline": "medium",
    "low_ndmi": "high",
    "ndmi_decline": "medium",
    "cloudy_observation": "high",
    "low_valid_pixels": "high",
    "suspicious_value": "high",
}

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}

ALERT_SOURCE = "agronomic_risk_engine"
MAX_CANDIDATES_DEFAULT = 100
MAX_CANDIDATES_LIMIT = 1000


def _severity_meets_min(reason_severity: str, min_severity: str) -> bool:
    """Check if reason_severity >= min_severity in severity ordering."""
    return SEVERITY_ORDER.get(reason_severity, 0) >= SEVERITY_ORDER.get(min_severity, 0)


def _build_idempotency_key(
    field_id: int,
    alert_type: str,
    source: str,
    index_code: str | None,
    captured_date: str | None,
    reason_code: str,
) -> str:
    """Deterministic idempotency key for a candidate."""
    raw = f"{field_id}|{alert_type}|{source}|{index_code or ''}|{captured_date or ''}|{reason_code}"
    return md5(raw.encode("utf-8")).hexdigest()


def _build_candidate_message(
    reason_code: str,
    reason_message: str,
    alert_type: str,
) -> str:
    """Build a human-readable message for the candidate."""
    return (
        f"[{alert_type}] {reason_message}"
    )


def generate_alert_candidates(
    db: Session,
    fresh_days: int = DEFAULT_FRESH_DAYS,
    limit: int = MAX_CANDIDATES_DEFAULT,
    enterprise_id: int | None = None,
    field_id: int | None = None,
    min_severity: str | None = None,
    include_low: bool = False,
) -> dict[str, Any]:
    """
    Generate satellite alert candidates from agronomic risk facts.

    Read-only. Uses build_agronomic_risk_summary() to get risk data,
    then converts reasons into alert candidates.

    Returns dict with:
    - generated_at: ISO datetime
    - mode: always "dry_run" (read-only)
    - summary: counts of candidates
    - candidates: list of alert candidate dicts
    - limitations: list of strings
    """
    now = datetime.utcnow()
    limitations: list[str] = []

    # Use risk engine to get field-level risk data with reasons
    risk_result = build_agronomic_risk_summary(
        db=db,
        fresh_days=fresh_days,
        limit=limit,      # Apply to field count
        enterprise_id=enterprise_id,
        include_low_risk=include_low,
        min_severity=min_severity,
        field_id=field_id,
    )

    field_risks = risk_result.get("field_risks", [])

    limitations.append(
        "Alert candidates are read-only. Persistent writes require "
        "dedicated idempotency column (source_key) in alerts table."
    )
    limitations.append(
        "Idempotency based on (field_id, alert_type, source, index_code, "
        "captured_date, reason_code) — computed inline, not DB-enforced."
    )
    limitations.append("Low-severity reasons are excluded by default.")

    # Convert field risks to alert candidates
    candidates: list[dict[str, Any]] = []
    risk_fields_evaluated = len(field_risks)

    for field_risk in field_risks:
        fid = field_risk["field_id"]
        field_name = field_risk["field_name"]
        eid = field_risk.get("enterprise_id")
        ename = field_risk.get("enterprise_name")

        reasons = field_risk.get("reasons", [])

        for reason in reasons:
            reason_code = reason.get("code", "")
            reason_severity = reason.get("severity", "low")

            # Determine alert type
            alert_type = RISK_REASON_TO_ALERT_TYPE.get(reason_code)
            if alert_type is None:
                limitations.append(f"Unknown reason code '{reason_code}' — no matching alert type")
                continue

            # Severity filter per reason
            reason_min = MIN_SEVERITY_PER_REASON.get(reason_code, "medium")
            if not _severity_meets_min(reason_severity, reason_min):
                continue

            # Build candidate fields
            index_code = reason.get("index_code")
            captured_date = reason.get("captured_date")
            current_value = reason.get("current_value")
            previous_value = reason.get("previous_value")
            delta = reason.get("delta")
            reason_message = reason.get("message", "")

            idempotency_key = _build_idempotency_key(
                field_id=fid,
                alert_type=alert_type,
                source=ALERT_SOURCE,
                index_code=index_code,
                captured_date=captured_date,
                reason_code=reason_code,
            )

            message = _build_candidate_message(reason_code, reason_message, alert_type)

            candidates.append({
                "field_id": fid,
                "field_name": field_name,
                "enterprise_id": eid,
                "enterprise_name": ename,
                "alert_type": alert_type,
                "severity": reason_severity,
                "source": ALERT_SOURCE,
                "index_code": index_code,
                "reason_code": reason_code,
                "captured_date": captured_date,
                "current_value": current_value,
                "previous_value": previous_value,
                "delta": delta,
                "message": message,
                "idempotency_key": idempotency_key,
                "action": "would_insert",
            })

    # Apply candidate limit (separate from field limit)
    total_candidates = len(candidates)
    candidates = candidates[:MAX_CANDIDATES_LIMIT]

    # Apply fallback default limit
    effective_limit = min(limit, MAX_CANDIDATES_LIMIT)
    candidates = candidates[:effective_limit]

    # Summarize
    by_severity: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for c in candidates:
        sev = c["severity"]
        by_severity[sev] = by_severity.get(sev, 0) + 1
        typ = c["alert_type"]
        by_type[typ] = by_type.get(typ, 0) + 1

    return {
        "generated_at": now.isoformat(),
        "mode": "dry_run",
        "summary": {
            "risk_fields_evaluated": risk_fields_evaluated,
            "candidates_total": total_candidates,
            "would_insert": len(candidates),
            "would_skip_existing": 0,
            "blocked": 0,
        },
        "by_severity": by_severity,
        "by_alert_type": by_type,
        "candidates": candidates,
        "limitations": list(set(limitations)),
    }
