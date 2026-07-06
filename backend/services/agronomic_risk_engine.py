"""
Read-only agronomic risk engine.

Converts existing satellite index data and data-quality facts into
field-level agronomic risk signals.

All queries use explicit SQL / text() - no ORM lazy loading, no DB writes.
"""
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ─── Thresholds ────────────────────────────────────────────────────────────

DEFAULT_FRESH_DAYS = 10
DEFAULT_FIELD_LIMIT = 100
MAX_FIELD_LIMIT = 1000

# Value bounds consistent with data-quality engine
EXPECTED_VALUE_MIN = -1.0
EXPECTED_VALUE_MAX = 1.0

# NDVI thresholds (conservative)
NDVI_LOW = 0.25        # latest NDVI < 0.25 → high
NDVI_CRITICAL = 0.15   # latest NDVI < 0.15 → critical

# NDMI thresholds (water stress proxy)
NDMI_LOW = 0.0         # latest NDMI < 0.0 → high
NDMI_CRITICAL = -0.1   # latest NDMI < -0.1 → critical

# Decline thresholds
DECLINE_MEDIUM = 0.10  # delta >= 0.10 → medium
DECLINE_HIGH = 0.20    # delta >= 0.20 → high
DECLINE_CRITICAL = 0.30  # delta >= 0.30 → critical

# Cloud / pixel quality thresholds
CLOUD_MEDIUM = 50      # cloud_cover_pct >= 50 → medium
CLOUD_HIGH = 70        # cloud_cover_pct >= 70 → high
VALID_PIXELS_MEDIUM = 70  # valid_pixels_pct < 70 → medium
VALID_PIXELS_HIGH = 50    # valid_pixels_pct < 50 → high


# ─── Severity mapping ──────────────────────────────────────────────────────

SEVERITY_SCORE = {"low": 1, "medium": 2, "high": 3, "critical": 4}

SATELLITE_INDEX_CODES = frozenset({"savi", "evi", "ndmi", "ndre"})


# ─── Data fetchers ─────────────────────────────────────────────────────────

def _fetch_fields(db: Session, enterprise_id: int | None = None) -> list[dict[str, Any]]:
    """Fetch active fields with enterprise info."""
    where = ""
    params: dict[str, Any] = {}
    if enterprise_id is not None:
        where = " AND f.enterprise_id = :eid"
        params["eid"] = enterprise_id

    rows = db.execute(
        text(f"""
            SELECT f.id AS field_id,
                   f.name AS field_name,
                   f.enterprise_id,
                   e.name AS enterprise_name
            FROM fields f
            LEFT JOIN enterprises e ON e.id = f.enterprise_id
            WHERE f.is_active = true OR f.is_active IS NULL
            {where}
            ORDER BY f.id
        """),
        params,
    ).fetchall()
    return [
        {
            "field_id": r.field_id,
            "field_name": r.field_name,
            "enterprise_id": r.enterprise_id,
            "enterprise_name": r.enterprise_name,
        }
        for r in rows
    ]


def _fetch_ndvi_latest_and_previous(
    db: Session,
) -> dict[int, dict[str, Any]]:
    """
    For each field with NDVI data, fetch latest and previous (2nd-latest)
    captured_date, mean_ndvi.
    Uses window function - no per-field loops.
    Returns dict[field_id, {latest_captured_date, latest_mean_ndvi,
                            previous_captured_date, previous_mean_ndvi,
                            cloud_cover_pct, valid_pixels_pct}]
    """
    rows = db.execute(
        text("""
            WITH ranked AS (
                SELECT field_id,
                       captured_date,
                       mean_ndvi,
                       cloud_cover_pct,
                       valid_pixels_pct,
                       ROW_NUMBER() OVER (
                           PARTITION BY field_id
                           ORDER BY captured_date DESC, id DESC
                       ) AS rn
                FROM ndvi_records
                WHERE mean_ndvi IS NOT NULL
            )
            SELECT field_id,
                   MAX(CASE WHEN rn = 1 THEN captured_date END) AS latest_date,
                   MAX(CASE WHEN rn = 1 THEN mean_ndvi END) AS latest_value,
                   MAX(CASE WHEN rn = 2 THEN captured_date END) AS prev_date,
                   MAX(CASE WHEN rn = 2 THEN mean_ndvi END) AS prev_value,
                   MAX(CASE WHEN rn = 1 THEN cloud_cover_pct END) AS cloud_cover_pct,
                   MAX(CASE WHEN rn = 1 THEN valid_pixels_pct END) AS valid_pixels_pct
            FROM ranked
            WHERE rn <= 2
            GROUP BY field_id
        """)
    ).fetchall()
    return {
        r.field_id: {
            "latest_captured_date": r.latest_date,
            "latest_mean_ndvi": r.latest_value,
            "previous_captured_date": r.prev_date,
            "previous_mean_ndvi": r.prev_value,
            "cloud_cover_pct": r.cloud_cover_pct,
            "valid_pixels_pct": r.valid_pixels_pct,
        }
        for r in rows
    }


def _fetch_satellite_latest_and_previous(
    db: Session,
    index_code: str,
) -> dict[int, dict[str, Any]]:
    """
    For each field with data for the given index_code, fetch latest and
    previous (2nd-latest) captured_date and mean_value.
    Uses window function - no per-field loops.
    """
    rows = db.execute(
        text("""
            WITH ranked AS (
                SELECT field_id,
                       captured_date,
                       mean_value,
                       cloud_cover_pct,
                       valid_pixels_pct,
                       ROW_NUMBER() OVER (
                           PARTITION BY field_id
                           ORDER BY captured_date DESC, id DESC
                       ) AS rn
                FROM satellite_index_records
                WHERE index_code = :code AND mean_value IS NOT NULL
            )
            SELECT field_id,
                   MAX(CASE WHEN rn = 1 THEN captured_date END) AS latest_date,
                   MAX(CASE WHEN rn = 1 THEN mean_value END) AS latest_value,
                   MAX(CASE WHEN rn = 2 THEN captured_date END) AS prev_date,
                   MAX(CASE WHEN rn = 2 THEN mean_value END) AS prev_value,
                   MAX(CASE WHEN rn = 1 THEN cloud_cover_pct END) AS cloud_cover_pct,
                   MAX(CASE WHEN rn = 1 THEN valid_pixels_pct END) AS valid_pixels_pct
            FROM ranked
            WHERE rn <= 2
            GROUP BY field_id
        """),
        {"code": index_code},
    ).fetchall()
    return {
        r.field_id: {
            "latest_captured_date": r.latest_date,
            "latest_mean_value": r.latest_value,
            "previous_captured_date": r.prev_date,
            "previous_mean_value": r.prev_value,
            "cloud_cover_pct": r.cloud_cover_pct,
            "valid_pixels_pct": r.valid_pixels_pct,
        }
        for r in rows
    }


def _age_days(captured_date) -> int | None:
    """Compute age in days from captured_date to today."""
    if captured_date is None:
        return None
    return (datetime.now().date() - captured_date).days


# ─── Risk evaluation (pure logic, no DB) ────────────────────────────────────

def _eval_ndvi_risks(
    latest_captured_date,
    latest_mean_ndvi: float | None,
    previous_mean_ndvi: float | None,
    fresh_days: int,
) -> list[dict[str, Any]]:
    """Evaluate NDVI-based risk reasons for one field."""
    reasons: list[dict[str, Any]] = []

    # Stale/missing
    if latest_mean_ndvi is None:
        reasons.append({
            "code": "missing_data",
            "severity": "high",
            "index_code": "ndvi",
            "current_value": None,
            "previous_value": None,
            "delta": None,
            "captured_date": None,
            "message": "No NDVI data available for this field",
        })
        return reasons

    age = _age_days(latest_captured_date)
    if age is not None and age > fresh_days:
        reasons.append({
            "code": "stale_data",
            "severity": "medium" if age <= fresh_days * 2 else "high",
            "index_code": "ndvi",
            "current_value": latest_mean_ndvi,
            "previous_value": None,
            "delta": None,
            "captured_date": str(latest_captured_date),
            "message": f"NDVI data {age} days old (threshold: {fresh_days} days)",
        })

    # Suspicious value
    if latest_mean_ndvi < EXPECTED_VALUE_MIN or latest_mean_ndvi > EXPECTED_VALUE_MAX:
        reasons.append({
            "code": "suspicious_value",
            "severity": "high",
            "index_code": "ndvi",
            "current_value": latest_mean_ndvi,
            "previous_value": None,
            "delta": None,
            "captured_date": str(latest_captured_date),
            "message": f"NDVI={latest_mean_ndvi:.4f} outside expected range [{EXPECTED_VALUE_MIN}, {EXPECTED_VALUE_MAX}]",
        })
        return reasons

    # Low NDVI
    if latest_mean_ndvi < NDVI_CRITICAL:
        reasons.append({
            "code": "low_ndvi",
            "severity": "critical",
            "index_code": "ndvi",
            "current_value": latest_mean_ndvi,
            "previous_value": None,
            "delta": None,
            "captured_date": str(latest_captured_date),
            "message": f"NDVI={latest_mean_ndvi:.4f} critically low (threshold: {NDVI_CRITICAL})",
        })
    elif latest_mean_ndvi < NDVI_LOW:
        reasons.append({
            "code": "low_ndvi",
            "severity": "high",
            "index_code": "ndvi",
            "current_value": latest_mean_ndvi,
            "previous_value": None,
            "delta": None,
            "captured_date": str(latest_captured_date),
            "message": f"NDVI={latest_mean_ndvi:.4f} below normal (threshold: {NDVI_LOW})",
        })

    # NDVI decline
    if previous_mean_ndvi is not None and latest_mean_ndvi is not None:
        delta = previous_mean_ndvi - latest_mean_ndvi
        if delta >= DECLINE_CRITICAL:
            reasons.append({
                "code": "ndvi_decline",
                "severity": "critical",
                "index_code": "ndvi",
                "current_value": latest_mean_ndvi,
                "previous_value": previous_mean_ndvi,
                "delta": -delta,
                "captured_date": str(latest_captured_date),
                "message": f"NDVI declined by {delta:.2f} from {previous_mean_ndvi:.4f} to {latest_mean_ndvi:.4f}",
            })
        elif delta >= DECLINE_HIGH:
            reasons.append({
                "code": "ndvi_decline",
                "severity": "high",
                "index_code": "ndvi",
                "current_value": latest_mean_ndvi,
                "previous_value": previous_mean_ndvi,
                "delta": -delta,
                "captured_date": str(latest_captured_date),
                "message": f"NDVI declined by {delta:.2f} from {previous_mean_ndvi:.4f} to {latest_mean_ndvi:.4f}",
            })
        elif delta >= DECLINE_MEDIUM:
            reasons.append({
                "code": "ndvi_decline",
                "severity": "medium",
                "index_code": "ndvi",
                "current_value": latest_mean_ndvi,
                "previous_value": previous_mean_ndvi,
                "delta": -delta,
                "captured_date": str(latest_captured_date),
                "message": f"NDVI declined by {delta:.2f} from {previous_mean_ndvi:.4f} to {latest_mean_ndvi:.4f}",
            })

    return reasons


def _eval_ndmi_risks(
    latest_captured_date,
    latest_mean_value: float | None,
    previous_mean_value: float | None,
    fresh_days: int,
) -> list[dict[str, Any]]:
    """Evaluate NDMI-based (water stress proxy) risk reasons for one field."""
    reasons: list[dict[str, Any]] = []

    if latest_mean_value is None:
        reasons.append({
            "code": "missing_data",
            "severity": "medium",
            "index_code": "ndmi",
            "current_value": None,
            "previous_value": None,
            "delta": None,
            "captured_date": None,
            "message": "No NDMI data available for this field",
        })
        return reasons

    age = _age_days(latest_captured_date)
    if age is not None and age > fresh_days:
        reasons.append({
            "code": "stale_data",
            "severity": "medium",
            "index_code": "ndmi",
            "current_value": latest_mean_value,
            "previous_value": None,
            "delta": None,
            "captured_date": str(latest_captured_date),
            "message": f"NDMI data {age} days old (threshold: {fresh_days} days)",
        })

    if latest_mean_value < EXPECTED_VALUE_MIN or latest_mean_value > EXPECTED_VALUE_MAX:
        reasons.append({
            "code": "suspicious_value",
            "severity": "high",
            "index_code": "ndmi",
            "current_value": latest_mean_value,
            "previous_value": None,
            "delta": None,
            "captured_date": str(latest_captured_date),
            "message": f"NDMI={latest_mean_value:.4f} outside expected range",
        })
        return reasons

    # Low NDMI (water stress proxy)
    if latest_mean_value < NDMI_CRITICAL:
        reasons.append({
            "code": "low_ndmi",
            "severity": "critical",
            "index_code": "ndmi",
            "current_value": latest_mean_value,
            "previous_value": None,
            "delta": None,
            "captured_date": str(latest_captured_date),
            "message": f"NDMI={latest_mean_value:.4f} critically low — possible water stress (threshold: {NDMI_CRITICAL})",
        })
    elif latest_mean_value < NDMI_LOW:
        reasons.append({
            "code": "low_ndmi",
            "severity": "high",
            "index_code": "ndmi",
            "current_value": latest_mean_value,
            "previous_value": None,
            "delta": None,
            "captured_date": str(latest_captured_date),
            "message": f"NDMI={latest_mean_value:.4f} below normal — possible water stress (threshold: {NDMI_LOW})",
        })

    # NDMI decline
    if previous_mean_value is not None and latest_mean_value is not None:
        delta = previous_mean_value - latest_mean_value
        if delta >= DECLINE_CRITICAL:
            reasons.append({
                "code": "ndmi_decline",
                "severity": "critical",
                "index_code": "ndmi",
                "current_value": latest_mean_value,
                "previous_value": previous_mean_value,
                "delta": -delta,
                "captured_date": str(latest_captured_date),
                "message": f"NDMI declined by {delta:.2f} from {previous_mean_value:.4f} to {latest_mean_value:.4f}",
            })
        elif delta >= DECLINE_HIGH:
            reasons.append({
                "code": "ndmi_decline",
                "severity": "high",
                "index_code": "ndmi",
                "current_value": latest_mean_value,
                "previous_value": previous_mean_value,
                "delta": -delta,
                "captured_date": str(latest_captured_date),
                "message": f"NDMI declined by {delta:.2f} from {previous_mean_value:.4f} to {latest_mean_value:.4f}",
            })
        elif delta >= DECLINE_MEDIUM:
            reasons.append({
                "code": "ndmi_decline",
                "severity": "medium",
                "index_code": "ndmi",
                "current_value": latest_mean_value,
                "previous_value": previous_mean_value,
                "delta": -delta,
                "captured_date": str(latest_captured_date),
                "message": f"NDMI declined by {delta:.2f} from {previous_mean_value:.4f} to {latest_mean_value:.4f}",
            })

    return reasons


def _eval_quality_risks(
    cloud_cover_pct: float | None,
    valid_pixels_pct: float | None,
) -> list[dict[str, Any]]:
    """Evaluate data quality risk reasons (cloudy, low valid pixels)."""
    reasons: list[dict[str, Any]] = []

    if cloud_cover_pct is not None:
        if cloud_cover_pct >= CLOUD_HIGH:
            reasons.append({
                "code": "cloudy_observation",
                "severity": "high",
                "index_code": None,
                "current_value": cloud_cover_pct,
                "previous_value": None,
                "delta": None,
                "captured_date": None,
                "message": f"Cloud cover {cloud_cover_pct:.1f}% (threshold: {CLOUD_HIGH}%)",
            })
        elif cloud_cover_pct >= CLOUD_MEDIUM:
            reasons.append({
                "code": "cloudy_observation",
                "severity": "medium",
                "index_code": None,
                "current_value": cloud_cover_pct,
                "previous_value": None,
                "delta": None,
                "captured_date": None,
                "message": f"Cloud cover {cloud_cover_pct:.1f}% (threshold: {CLOUD_MEDIUM}%)",
            })

    if valid_pixels_pct is not None:
        if valid_pixels_pct < VALID_PIXELS_HIGH:
            reasons.append({
                "code": "low_valid_pixels",
                "severity": "high",
                "index_code": None,
                "current_value": valid_pixels_pct,
                "previous_value": None,
                "delta": None,
                "captured_date": None,
                "message": f"Valid pixels {valid_pixels_pct:.1f}% (threshold: {VALID_PIXELS_HIGH}%)",
            })
        elif valid_pixels_pct < VALID_PIXELS_MEDIUM:
            reasons.append({
                "code": "low_valid_pixels",
                "severity": "medium",
                "index_code": None,
                "current_value": valid_pixels_pct,
                "previous_value": None,
                "delta": None,
                "captured_date": None,
                "message": f"Valid pixels {valid_pixels_pct:.1f}% (threshold: {VALID_PIXELS_MEDIUM}%)",
            })

    return reasons


def _compute_final_risk(reasons: list[dict]) -> tuple[str, int]:
    """Compute final risk level and score from reasons.
    Max severity wins - no averaging."""
    if not reasons:
        return "low", 0
    max_score = max(SEVERITY_SCORE.get(r["severity"], 0) for r in reasons)
    severity_map = {1: "low", 2: "medium", 3: "high", 4: "critical"}
    return severity_map.get(max_score, "low"), max_score


# ─── Main engine entry point ───────────────────────────────────────────────

def build_agronomic_risk_summary(
    db: Session,
    fresh_days: int = DEFAULT_FRESH_DAYS,
    limit: int = DEFAULT_FIELD_LIMIT,
    enterprise_id: int | None = None,
    include_low_risk: bool = False,
    min_severity: str | None = None,
    field_id: int | None = None,
) -> dict[str, Any]:
    """
    Build the full agronomic risk summary.

    Read-only. Uses explicit SQL window functions for latest/previous values.
    No per-field DB loops. No lazy loading. No DB writes.
    """
    now = datetime.utcnow()
    limitations: list[str] = []

    # Fetch fields
    all_fields = _fetch_fields(db, enterprise_id)
    enterprise_map: dict[int, str] = {}
    for f in all_fields:
        eid = f["enterprise_id"]
        ename = f["enterprise_name"]
        if eid and ename and eid not in enterprise_map:
            enterprise_map[eid] = ename

    # Filter by specific field_id if requested
    if field_id is not None:
        all_fields = [f for f in all_fields if f["field_id"] == field_id]
        if not all_fields:
            return {
                "generated_at": now.isoformat(),
                "thresholds": _build_thresholds(),
                "summary": {"total_fields": 0, "fields_with_any_risk": 0,
                            "by_severity": {}},
                "by_enterprise": [],
                "field_risks": [],
                "limitations": [],
            }

    total_fields = len(all_fields)
    field_ids_set = {f["field_id"] for f in all_fields}

    # Fetch latest/previous data in bulk
    ndvi_data = _fetch_ndvi_latest_and_previous(db)
    ndmi_data = _fetch_satellite_latest_and_previous(db, "ndmi")

    limitations.append("Crop-specific thresholds unavailable — using conservative NDVI/NDMI defaults")
    limitations.append("Optional index reasons (EVI, SAVI, NDRE) not included — conservative mode")

    # Evaluate risk per field
    field_risks: list[dict] = []
    by_enterprise_agg: dict[int, dict] = {}

    for field in all_fields:
        fid = field["field_id"]
        ndvi = ndvi_data.get(fid, {})
        ndmi = ndmi_data.get(fid, {})

        reasons: list[dict] = []

        # NDVI risks
        ndvi_reasons = _eval_ndvi_risks(
            ndvi.get("latest_captured_date"),
            ndvi.get("latest_mean_ndvi"),
            ndvi.get("previous_mean_ndvi"),
            fresh_days,
        )
        reasons.extend(ndvi_reasons)

        # NDMI risks (water stress proxy)
        ndmi_reasons = _eval_ndmi_risks(
            ndmi.get("latest_captured_date"),
            ndmi.get("latest_mean_value"),
            ndmi.get("previous_mean_value"),
            fresh_days,
        )
        reasons.extend(ndmi_reasons)

        # Quality risks from NDVI observation (prefer NDVI for quality)
        quality_reasons = _eval_quality_risks(
            ndvi.get("cloud_cover_pct") or ndmi.get("cloud_cover_pct"),
            ndvi.get("valid_pixels_pct") or ndmi.get("valid_pixels_pct"),
        )
        # Deduplicate quality reasons by code
        seen_codes: set[tuple] = set()
        for qr in quality_reasons:
            key = (qr["code"], qr.get("current_value"))
            if key not in seen_codes:
                seen_codes.add(key)
                reasons.append(qr)

        # Compute final risk
        risk_level, risk_score = _compute_final_risk(reasons)

        # Filter by include_low_risk / min_severity
        if not include_low_risk and risk_level == "low":
            continue
        if min_severity:
            min_score = SEVERITY_SCORE.get(min_severity, 0)
            if risk_score < min_score:
                continue

        # Build latest_values
        latest_values = {}
        latest_dates = {}
        if ndvi.get("latest_mean_ndvi") is not None:
            latest_values["ndvi"] = ndvi["latest_mean_ndvi"]
            latest_dates["ndvi"] = str(ndvi["latest_captured_date"]) if ndvi.get("latest_captured_date") else None
        if ndmi.get("latest_mean_value") is not None:
            latest_values["ndmi"] = ndmi["latest_mean_value"]
            latest_dates["ndmi"] = str(ndmi["latest_captured_date"]) if ndmi.get("latest_captured_date") else None

        field_risks.append({
            "field_id": fid,
            "field_name": field["field_name"],
            "enterprise_id": field["enterprise_id"],
            "enterprise_name": field["enterprise_name"],
            "risk_level": risk_level,
            "risk_score": risk_score,
            "latest_dates": latest_dates,
            "latest_values": latest_values,
            "reasons": reasons,
        })

        # Enterprise aggregation
        eid = field["enterprise_id"]
        if eid not in by_enterprise_agg:
            by_enterprise_agg[eid] = {
                "enterprise_id": eid,
                "enterprise_name": enterprise_map.get(eid),
                "total_fields": 0,
                "fields_with_any_risk": 0,
                "low": 0,
                "medium": 0,
                "high": 0,
                "critical": 0,
            }
        by_enterprise_agg[eid]["total_fields"] += 1
        if risk_level != "low":
            by_enterprise_agg[eid]["fields_with_any_risk"] += 1
        if risk_level in by_enterprise_agg[eid]:
            by_enterprise_agg[eid][risk_level] += 1

    # Apply limit to field_risks
    field_risks_sorted = sorted(field_risks, key=lambda x: x["risk_score"], reverse=True)
    limited_risks = field_risks_sorted[:limit]
    # Re-accumulate summary for limited set
    limited_count = len(limited_risks)
    limited_with_risk = sum(1 for r in limited_risks if r["risk_level"] != "low")
    limited_by_sev = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for r in limited_risks:
        sev = r["risk_level"]
        if sev in limited_by_sev:
            limited_by_sev[sev] += 1

    by_enterprise_list = sorted(by_enterprise_agg.values(), key=lambda x: x["enterprise_id"])

    return {
        "generated_at": now.isoformat(),
        "thresholds": _build_thresholds(fresh_days=fresh_days),
        "summary": {
            "total_fields": total_fields,
            "fields_with_any_risk": limited_with_risk,
            "low": limited_by_sev["low"],
            "medium": limited_by_sev["medium"],
            "high": limited_by_sev["high"],
            "critical": limited_by_sev["critical"],
        },
        "by_enterprise": by_enterprise_list,
        "field_risks": limited_risks,
        "limitations": limitations,
    }


def _build_thresholds(fresh_days: int = DEFAULT_FRESH_DAYS) -> dict[str, Any]:
    """Return the thresholds dict for the response."""
    return {
        "fresh_days": fresh_days,
        "value_min": EXPECTED_VALUE_MIN,
        "value_max": EXPECTED_VALUE_MAX,
        "ndvi_low": NDVI_LOW,
        "ndvi_critical": NDVI_CRITICAL,
        "ndmi_low": NDMI_LOW,
        "ndmi_critical": NDMI_CRITICAL,
        "decline_medium": DECLINE_MEDIUM,
        "decline_high": DECLINE_HIGH,
        "decline_critical": DECLINE_CRITICAL,
        "cloud_medium": CLOUD_MEDIUM,
        "cloud_high": CLOUD_HIGH,
        "valid_pixels_medium": VALID_PIXELS_MEDIUM,
        "valid_pixels_high": VALID_PIXELS_HIGH,
    }
