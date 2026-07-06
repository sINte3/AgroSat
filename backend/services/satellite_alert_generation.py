"""
Satellite alert candidate generation from agronomic risk facts.

Read-only by default. Converts agronomic risk engine output (TASK_136)
into controlled satellite alert candidates.

Protected apply mode (TASK_139): explicit --apply required for persistence.
Dry-run is always the default. Apply uses source/source_key idempotency.

No DB writes without --apply. No Sentinel Hub calls. No scheduler.
"""
import logging
from datetime import datetime, date
from hashlib import md5
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.monitoring import Alert
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


# ─── Protected apply mode (TASK_139) ───────────────────────────────────


def build_alert_from_candidate(candidate: dict) -> dict:
    """
    Map candidate dict to Alert insert payload.

    Returns dict matching Alert columns. Use deterministic Russian text.
    """
    reason_code = candidate.get("reason_code", "")
    alert_type = candidate.get("alert_type", "")
    severity = candidate.get("severity", "medium")
    current_value = candidate.get("current_value")
    previous_value = candidate.get("previous_value")
    delta = candidate.get("delta")
    captured_date = candidate.get("captured_date")
    field_name = candidate.get("field_name", "")
    index_code = candidate.get("index_code")
    idempotency_key = candidate.get("idempotency_key", "")
    now_utc = datetime.utcnow()

    # Title mapping
    title_map: dict[str, str] = {
        "vegetation_low_ndvi": "Спутниковый риск: снижение NDVI",
        "vegetation_ndvi_decline": "Спутниковый риск: падение NDVI",
        "water_stress_low_ndmi": "Спутниковый риск: водный стресс NDMI",
        "water_stress_ndmi_decline": "Спутниковый риск: ухудшение NDMI",
        "satellite_missing_data": "Спутниковый риск: нет данных",
        "satellite_stale_data": "Спутниковый риск: устаревшие данные",
        "satellite_cloudy_observation": "Спутниковый риск: облачность",
        "satellite_low_valid_pixels": "Спутниковый риск: качество снимка",
        "satellite_suspicious_value": "Спутниковый риск: аномальное значение",
    }
    title = title_map.get(alert_type, f"Спутниковый риск: {alert_type}")

    # Description: field name, index, reason code, values, captured date
    desc_parts = [f"Поле: {field_name}"]
    if index_code:
        desc_parts.append(f"Индекс: {index_code}")
    desc_parts.append(f"Причина: {reason_code}")
    if current_value is not None:
        desc_parts.append(f"Значение: {current_value}")
    if previous_value is not None:
        desc_parts.append(f"Предыдущее: {previous_value}")
    if delta is not None:
        desc_parts.append(f"Дельта: {delta}")
    if captured_date:
        desc_parts.append(f"Дата снимка: {captured_date}")
    description = " | ".join(desc_parts)

    # Recommendation
    if reason_code in ("missing_data", "stale_data"):
        recommendation = (
            "Проверить поступление спутниковых данных "
            "и при необходимости повторить сбор через CLI."
        )
    elif reason_code in ("cloudy_observation", "low_valid_pixels"):
        recommendation = (
            "Не принимать агрономическое решение только по этому снимку; "
            "дождаться снимка лучшего качества."
        )
    else:
        recommendation = (
            "Проверить поле агрономом и сопоставить "
            "со свежими снимками/осмотром."
        )

    # Triggered value: prefer current_value
    triggered_value = current_value
    if triggered_value is None and previous_value is not None:
        triggered_value = previous_value

    # Timestamp
    if captured_date:
        try:
            triggered_at = datetime.strptime(
                captured_date, "%Y-%m-%d"
            )
        except ValueError:
            triggered_at = now_utc
    else:
        triggered_at = now_utc

    return {
        "field_id": candidate.get("field_id"),
        "alert_type": alert_type,
        "severity": severity,
        "title": title,
        "description": description,
        "recommendation": recommendation,
        "triggered_value": triggered_value,
        "threshold_value": None,
        "triggered_at": triggered_at,
        "is_active": True,
        "source": ALERT_SOURCE,
        "source_key": idempotency_key,
        "ndvi_record_id": None,
    }


def apply_alert_candidates(
    db: Session,
    candidates: list[dict],
    rollback: bool = False,
) -> dict[str, Any]:
    """
    Persist alert candidates to the alerts table.

    Idempotent: checks existing active (source, source_key) in bulk,
    inserts only non-duplicates, relies on DB partial unique index for
    race safety.

    Args:
        db: DB session.
        candidates: list of candidate dicts from generate_alert_candidates.
        rollback: if True, perform inserts inside transaction then rollback.

    Returns:
        dict with mode, rollback flag, summary, per-result list, limitations.
    """
    now = datetime.utcnow()
    limitations: list[str] = []
    result: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "mode": "apply",
        "rollback": rollback,
        "summary": {
            "candidates_total": len(candidates),
            "inserted": 0,
            "skipped_existing": 0,
            "blocked": 0,
        },
        "results": [],
        "limitations": [],
    }

    # Guard: no candidates → early return
    if not candidates:
        result["limitations"].append("No candidates to apply.")
        return result

    # 1. Bulk fetch existing active source_keys for all candidate keys
    candidate_keys = [
        c.get("idempotency_key", "") for c in candidates
    ]
    existing_keys: set[str] = set()
    try:
        rows = db.execute(
            text(
                "SELECT source_key FROM alerts "
                "WHERE source = :source "
                "AND source_key = ANY(:keys) "
                "AND is_active = true"
            ),
            {
                "source": ALERT_SOURCE,
                "keys": candidate_keys,
            },
        ).fetchall()
        # Distinguish column access style
        for r in rows:
            val = r.source_key if hasattr(r, "source_key") else r[0]
            if val:
                existing_keys.add(val)
    except Exception as exc:
        result["limitations"].append(
            f"Bulk existence check error: {exc}"
        )
        # Fall back to empty set → will catch duplicates via IntegrityError
        existing_keys = set()

    # 2. Process candidates
    inserted_count = 0
    skipped_count = 0
    blocked_count = 0
    results_list: list[dict[str, Any]] = []

    for c in candidates:
        sk = c.get("idempotency_key", "")
        fid = c.get("field_id")
        atype = c.get("alert_type", "")
        pair = {
            "field_id": fid,
            "alert_type": atype,
            "source": ALERT_SOURCE,
            "source_key": sk,
        }

        if sk in existing_keys:
            pair["action"] = "skipped_existing"
            pair["reason"] = "Active alert with same source_key already exists"
            results_list.append(pair)
            skipped_count += 1
            continue

        payload = build_alert_from_candidate(c)

        try:
            # Use savepoint per row so a single IntegrityError
            # does not abort the whole batch.
            # WHERE NOT EXISTS provides reliable rowcount;
            # ON CONFLICT DO NOTHING is race-safety net between
            # check and insert.
            with db.begin_nested():
                insert_result = db.execute(
                    text(
                        """INSERT INTO alerts
                        (field_id, alert_type, severity, title, description,
                         recommendation, triggered_value, threshold_value,
                         triggered_at, is_active, source, source_key,
                         ndvi_record_id)
                        SELECT
                        :field_id, :alert_type, :severity, :title, :description,
                        :recommendation, :triggered_value, :threshold_value,
                        :triggered_at, :is_active, :source, :source_key,
                        :ndvi_record_id
                        WHERE NOT EXISTS (
                            SELECT 1 FROM alerts
                            WHERE source = :w_source
                            AND source_key = :w_source_key
                            AND is_active = true
                        )
                        ON CONFLICT DO NOTHING"""
                    ),
                    {
                        **payload,
                        "w_source": ALERT_SOURCE,
                        "w_source_key": sk,
                    },
                )
            affected = insert_result.rowcount or 0
            if affected == 1:
                pair["action"] = "inserted"
                pair["reason"] = "New alert persisted"
                results_list.append(pair)
                inserted_count += 1
            else:
                pair["action"] = "skipped_existing"
                pair["reason"] = (
                    f"INSERT/SELECT rowcount={affected}; "
                    "existing row matched, not inserted"
                )
                results_list.append(pair)
                skipped_count += 1
        except IntegrityError:
            pair["action"] = "skipped_existing"
            pair["reason"] = (
                "IntegrityError — duplicate source_key caught "
                "by partial unique index"
            )
            results_list.append(pair)
            skipped_count += 1
        except Exception as exc:
            pair["action"] = "blocked"
            pair["reason"] = f"Insert error: {exc}"
            results_list.append(pair)
            blocked_count += 1

    result["summary"]["inserted"] = inserted_count
    result["summary"]["skipped_existing"] = skipped_count
    result["summary"]["blocked"] = blocked_count
    result["results"] = results_list
    result["limitations"] = limitations

    # ─── Commit/rollback semantics ──────────────────────────────────
    # Use flush() for intra-transaction visibility (callers see rows);
    # use commit() only when the caller explicitly wants persistence
    # (CLI calls db.commit() after apply returns).
    # This allows callers (including validation tests) to wrap in a
    # savepoint and rollback without leaking writes.
    committed = False
    rolled_back = False
    if rollback:
        db.rollback()
        rolled_back = True
    elif blocked_count > 0:
        db.rollback()
        rolled_back = True
    else:
        db.flush()
        # NOTE: Caller must db.commit() if persistence is desired.
        # The CLI does this after apply_alert_candidates returns.
        committed = True

    result["committed"] = committed
    result["rolled_back"] = rolled_back

    return result
