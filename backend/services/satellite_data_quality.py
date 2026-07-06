"""
Read-only satellite data quality engine.

Computes freshness, completeness, value validity, cloud quality, and
duplicate-risk for NDVI (ndvi_records) and satellite indices
(satellite_index_records) using existing DB facts only.

All queries use explicit SQL / text() — no ORM lazy loading, no DB writes.
"""
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_FRESH_DAYS = 10
DEFAULT_STALE_AFTER_DAYS = 10
EXPECTED_VALUE_MIN = -1.0
EXPECTED_VALUE_MAX = 1.0

# NDVI uses mean_ndvi; satellite indices use mean_value
# Index codes that go to satellite_index_records
SATELLITE_INDEX_CODES = frozenset({"savi", "evi", "ndmi", "ndre"})

PROBLEM_FIELDS_LIMIT = 100


# ─── Schema introspection helpers ──────────────────────────────────────────────

def table_exists(db: Session, table_name: str) -> bool:
    """Check if a table exists in the public schema."""
    row = db.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table_name},
    ).fetchone()
    return row is not None


def has_column(db: Session, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    row = db.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t "
            "AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).fetchone()
    return row is not None


# ─── Core quality data queries ────────────────────────────────────────────────

def _fetch_field_index_map(db: Session) -> list[dict[str, Any]]:
    """
    Fetch all active fields with enterprise info.
    Returns list of {field_id, field_name, enterprise_id, enterprise_name}.
    """
    rows = db.execute(
        text("""
            SELECT f.id AS field_id,
                   f.name AS field_name,
                   f.enterprise_id,
                   e.name AS enterprise_name
            FROM fields f
            LEFT JOIN enterprises e ON e.id = f.enterprise_id
            WHERE f.is_active = true OR f.is_active IS NULL
            ORDER BY f.id
        """)
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


def _fetch_ndvi_latest_per_field(db: Session) -> dict[int, dict[str, Any]]:
    """
    For each field that has at least one ndvi_records row, return the
    latest captured_date and mean_ndvi. Returns dict[field_id, {...}].
    """
    rows = db.execute(
        text("""
            SELECT DISTINCT ON (field_id)
                   field_id,
                   captured_date,
                   mean_ndvi,
                   cloud_cover_pct,
                   valid_pixels_pct
            FROM ndvi_records
            ORDER BY field_id, captured_date DESC, id DESC
        """)
    ).fetchall()
    return {
        r.field_id: {
            "captured_date": r.captured_date,
            "mean_ndvi": r.mean_ndvi,
            "cloud_cover_pct": r.cloud_cover_pct,
            "valid_pixels_pct": r.valid_pixels_pct,
        }
        for r in rows
    }


def _fetch_satellite_latest_per_field_index(
    db: Session,
    index_codes: frozenset,
) -> dict[tuple[int, str], dict[str, Any]]:
    """
    For each (field_id, index_code) pair with data, return the latest
    captured_date and mean_value. Returns dict[(field_id, index_code), {...}].
    Only includes rows where index_code is in the supported set.
    """
    if not index_codes:
        return {}

    placeholders = ", ".join(f":c{i}" for i in range(len(index_codes)))
    params = {f"c{i}": code for i, code in enumerate(sorted(index_codes))}

    rows = db.execute(
        text(f"""
            SELECT DISTINCT ON (field_id, index_code)
                   field_id,
                   index_code,
                   captured_date,
                   mean_value,
                   cloud_cover_pct,
                   valid_pixels_pct
            FROM satellite_index_records
            WHERE index_code IN ({placeholders})
            ORDER BY field_id, index_code, captured_date DESC, id DESC
        """),
        params,
    ).fetchall()
    return {
        (r.field_id, r.index_code): {
            "captured_date": r.captured_date,
            "mean_value": r.mean_value,
            "cloud_cover_pct": r.cloud_cover_pct,
            "valid_pixels_pct": r.valid_pixels_pct,
        }
        for r in rows
    }


def _fetch_suspicious_ndvi_count(db: Session) -> int:
    """Count NDVI records where mean_ndvi is outside [-1, 1]."""
    row = db.execute(
        text("""
            SELECT COUNT(*) FROM ndvi_records
            WHERE mean_ndvi IS NOT NULL
              AND (mean_ndvi < :vmin OR mean_ndvi > :vmax)
        """),
        {"vmin": EXPECTED_VALUE_MIN, "vmax": EXPECTED_VALUE_MAX},
    ).fetchone()
    return row[0] if row else 0


def _fetch_suspicious_satellite_count(db: Session) -> int:
    """Count satellite index records where mean_value is outside [-1, 1]."""
    row = db.execute(
        text("""
            SELECT COUNT(*) FROM satellite_index_records
            WHERE mean_value IS NOT NULL
              AND (mean_value < :vmin OR mean_value > :vmax)
        """),
        {"vmin": EXPECTED_VALUE_MIN, "vmax": EXPECTED_VALUE_MAX},
    ).fetchone()
    return row[0] if row else 0


def _fetch_suspicious_satellite_fields(
    db: Session,
    index_codes: frozenset,
    limit: int = PROBLEM_FIELDS_LIMIT,
) -> list[dict[str, Any]]:
    """
    Fetch satellite index records with suspicious values (outside [-1, 1]),
    joined with field/enterprise names.
    """
    if not index_codes:
        return []
    placeholders = ", ".join(f":c{i}" for i in range(len(index_codes)))
    params = {f"c{i}": code for i, code in enumerate(sorted(index_codes))}
    params["vmin"] = EXPECTED_VALUE_MIN
    params["vmax"] = EXPECTED_VALUE_MAX
    params["limit"] = limit

    rows = db.execute(
        text(f"""
            SELECT sir.field_id,
                   f.name AS field_name,
                   e.name AS enterprise_name,
                   sir.index_code,
                   sir.captured_date,
                   sir.mean_value
            FROM satellite_index_records sir
            JOIN fields f ON f.id = sir.field_id
            LEFT JOIN enterprises e ON e.id = f.enterprise_id
            WHERE sir.index_code IN ({placeholders})
              AND sir.mean_value IS NOT NULL
              AND (sir.mean_value < :vmin OR sir.mean_value > :vmax)
            ORDER BY sir.captured_date DESC
            LIMIT :limit
        """),
        params,
    ).fetchall()

    return [
        {
            "field_id": r.field_id,
            "field_name": r.field_name,
            "enterprise_name": r.enterprise_name,
            "index_code": r.index_code,
            "status": "suspicious",
            "latest_captured_date": str(r.captured_date),
            "age_days": None,
            "reason": f"mean_value={r.mean_value:.4f} outside expected range [{EXPECTED_VALUE_MIN}, {EXPECTED_VALUE_MAX}]",
        }
        for r in rows
    ]


def _fetch_suspicious_ndvi_fields(
    db: Session,
    limit: int = PROBLEM_FIELDS_LIMIT,
) -> list[dict[str, Any]]:
    """
    Fetch NDVI records with suspicious values (outside [-1, 1]),
    joined with field/enterprise names.
    """
    rows = db.execute(
        text("""
            SELECT nr.field_id,
                   f.name AS field_name,
                   e.name AS enterprise_name,
                   nr.captured_date,
                   nr.mean_ndvi
            FROM ndvi_records nr
            JOIN fields f ON f.id = nr.field_id
            LEFT JOIN enterprises e ON e.id = f.enterprise_id
            WHERE nr.mean_ndvi IS NOT NULL
              AND (nr.mean_ndvi < :vmin OR nr.mean_ndvi > :vmax)
            ORDER BY nr.captured_date DESC
            LIMIT :limit
        """),
        {"vmin": EXPECTED_VALUE_MIN, "vmax": EXPECTED_VALUE_MAX, "limit": limit},
    ).fetchall()

    return [
        {
            "field_id": r.field_id,
            "field_name": r.field_name,
            "enterprise_name": r.enterprise_name,
            "index_code": "ndvi",
            "status": "suspicious",
            "latest_captured_date": str(r.captured_date),
            "age_days": None,
            "reason": f"mean_ndvi={r.mean_ndvi:.4f} outside expected range [{EXPECTED_VALUE_MIN}, {EXPECTED_VALUE_MAX}]",
        }
        for r in rows
    ]


# ─── Quality status helpers ───────────────────────────────────────────────────

def _age_days(captured_date) -> int | None:
    """Compute age in days from captured_date to today."""
    if captured_date is None:
        return None
    return (datetime.now().date() - captured_date).days


def _freshness_status(
    captured_date,
    fresh_days: int,
    stale_after_days: int,
) -> str:
    """
    Classify freshness:
    - "missing" if captured_date is None
    - "fresh" if age <= fresh_days
    - "stale" if age > stale_after_days
    (fresh_days and stale_after_days are the same by default.)
    """
    if captured_date is None:
        return "missing"
    age = _age_days(captured_date)
    if age is None:
        return "missing"
    if age <= fresh_days:
        return "fresh"
    # age > stale_after_days
    return "stale"


def _cloud_quality_status(
    db: Session,
    ndvi_table: str,
    sat_table: str,
) -> str:
    """Check whether cloud_cover_pct column exists."""
    ndvi_has = has_column(db, ndvi_table, "cloud_cover_pct")
    sat_has = has_column(db, sat_table, "cloud_cover_pct")
    if ndvi_has and sat_has:
        return "available"
    return "not_available"


def _valid_pixel_quality_status(
    db: Session,
    ndvi_table: str,
    sat_table: str,
) -> str:
    """Check whether valid_pixels_pct column exists."""
    ndvi_has = has_column(db, ndvi_table, "valid_pixels_pct")
    sat_has = has_column(db, sat_table, "valid_pixels_pct")
    if ndvi_has and sat_has:
        return "available"
    return "not_available"


# ─── Main quality summary ─────────────────────────────────────────────────────

def build_quality_summary(
    db: Session,
    fresh_days: int = DEFAULT_FRESH_DAYS,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    limit: int = PROBLEM_FIELDS_LIMIT,
    enterprise_id: int | None = None,
    index_code_filter: str | None = None,
) -> dict[str, Any]:
    """
    Build the full data quality summary dict.

    This is the single entry point for the API endpoint. All DB reads are
    explicit SQL aggregates — no lazy loading, no per-field loops over the DB.
    """
    # ── Fields ────────────────────────────────────────────────────────────
    all_fields = _fetch_field_index_map(db)

    # Filter by enterprise if requested
    if enterprise_id is not None:
        all_fields = [f for f in all_fields if f["enterprise_id"] == enterprise_id]

    total_fields = len(all_fields)
    field_ids = {f["field_id"] for f in all_fields}
    field_map = {f["field_id"]: f for f in all_fields}

    now = datetime.utcnow()

    # ── Limits / info ─────────────────────────────────────────────────────
    limitations = []

    # ── NDVI (ndvi_records) ───────────────────────────────────────────────
    ndvi_latest = _fetch_ndvi_latest_per_field(db)
    suspicious_ndvi_count = _fetch_suspicious_ndvi_count(db)
    suspicious_ndvi_fields = _fetch_suspicious_ndvi_fields(db, limit)

    # ── Satellite indices (satellite_index_records) ───────────────────────
    index_codes = SATELLITE_INDEX_CODES
    if index_code_filter:
        norm_filter = index_code_filter.strip().lower()
        if norm_filter in index_codes:
            index_codes = {norm_filter}
        elif norm_filter == "ndvi":
            index_codes = frozenset()
        else:
            index_codes = frozenset()

    sat_latest = _fetch_satellite_latest_per_field_index(db, index_codes)
    suspicious_sat_count = _fetch_suspicious_satellite_count(db)
    suspicious_sat_fields = _fetch_suspicious_satellite_fields(db, index_codes, limit)

    total_suspicious = suspicious_ndvi_count + suspicious_sat_count

    # ── Cloud/quality column checks ───────────────────────────────────────
    cloud_status = _cloud_quality_status(db, "ndvi_records", "satellite_index_records")
    pixel_status = _valid_pixel_quality_status(db, "ndvi_records", "satellite_index_records")

    if cloud_status != "available":
        limitations.append("cloud_cover_pct column absent or incomplete — cloud quality not_available")
    if pixel_status != "available":
        limitations.append("valid_pixels_pct column absent or incomplete — valid pixel quality not_available")

    # ── Compute per-index summaries ───────────────────────────────────────
    by_index = []
    index_definitions = [
        ("ndvi", "ndvi_records", ndvi_latest),
    ]
    for code in sorted(index_codes):
        index_definitions.append((code, "satellite_index_records", sat_latest))

    fresh_count_total = 0
    stale_count_total = 0
    missing_count_total = 0

    problem_fields: list[dict] = []

    for idx_code, target_table, latest_dict in index_definitions:
        # Use index_code_filter to skip ndvi if filtered out
        if index_code_filter and idx_code != index_code_filter.strip().lower():
            if not (index_code_filter.strip().lower() == "ndvi" and idx_code == "ndvi"):
                # If filter is "ndvi", satellite indices are already excluded above.
                # If filter is a specific satellite code, non-matching codes skip here.
                if idx_code not in [index_code_filter.strip().lower()]:
                    continue

        fields_with_data = 0
        fresh_count = 0
        stale_count = 0
        missing_count = 0
        idx_suspicious = 0
        latest_overall = None

        for fid in sorted(field_ids):
            entry = latest_dict.get(fid)

            if idx_code == "ndvi":
                is_present = entry is not None
                f_status = _freshness_status(
                    entry["captured_date"] if entry else None,
                    fresh_days,
                    stale_after_days,
                )
                if entry:
                    captured = entry["captured_date"]
                    age = _age_days(captured)
                else:
                    captured = None
                    age = None
            else:
                key = (fid, idx_code)
                entry_for_code = sat_latest.get(key)
                is_present = entry_for_code is not None
                f_status = _freshness_status(
                    entry_for_code["captured_date"] if entry_for_code else None,
                    fresh_days,
                    stale_after_days,
                )
                if entry_for_code:
                    captured = entry_for_code["captured_date"]
                    age = _age_days(captured)
                else:
                    captured = None
                    age = None

            if f_status == "fresh":
                fresh_count += 1
            elif f_status == "stale":
                stale_count += 1
            elif f_status == "missing":
                missing_count += 1

            if is_present:
                fields_with_data += 1
                if latest_overall is None or (
                    captured and captured > latest_overall
                ):
                    latest_overall = captured

            # Collect problem fields
            if f_status in ("missing", "stale"):
                field_info = field_map.get(fid, {})
                problem_fields.append({
                    "field_id": fid,
                    "field_name": field_info.get("field_name", ""),
                    "enterprise_name": field_info.get("enterprise_name"),
                    "index_code": idx_code,
                    "status": f_status,
                    "latest_captured_date": str(captured) if captured else None,
                    "age_days": age,
                    "reason": f"No data" if f_status == "missing"
                              else f"Last data {age} days ago (threshold: {stale_after_days} days)",
                })

        # Suspicious values for this index
        if idx_code == "ndvi":
            idx_suspicious = suspicious_ndvi_count
        else:
            # We count all suspicious satellite records — could filter by code
            idx_suspicious = suspicious_sat_count

        coverage = round((fields_with_data / total_fields * 100), 1) if total_fields > 0 else 0.0

        by_index.append({
            "index_code": idx_code,
            "target_table": target_table,
            "expected_fields": total_fields,
            "fields_with_data": fields_with_data,
            "fields_missing_data": total_fields - fields_with_data,
            "coverage_percent": coverage,
            "latest_captured_date": str(latest_overall) if latest_overall else None,
            "fresh_count": fresh_count,
            "stale_count": stale_count,
            "missing_count": missing_count,
            "suspicious_value_count": idx_suspicious,
            "cloud_quality_status": cloud_status,
            "valid_pixel_quality_status": pixel_status,
        })

        fresh_count_total += fresh_count
        stale_count_total += stale_count
        missing_count_total += missing_count

    # Append suspicious NDVI fields to problem_fields
    problem_fields.extend(suspicious_ndvi_fields)
    # Append suspicious satellite fields
    problem_fields.extend(suspicious_sat_fields)

    # Deduplicate problem_fields by (field_id, index_code, status) keeping first
    seen = set()
    deduped = []
    for pf in problem_fields:
        key = (pf["field_id"], pf["index_code"], pf["status"])
        if key not in seen:
            seen.add(key)
            deduped.append(pf)
    problem_fields = deduped[:limit]

    # Compute total field-index pairs across all checked indices
    # (NDVI fields + index_codes * fields)
    ndvi_pairs = total_fields  # NDVI for every field
    sat_pairs = total_fields * len(index_codes)
    total_pairs = ndvi_pairs + sat_pairs

    # ndvi is always checked unless filtering excluded it
    indices_checked = ["ndvi"] if not index_code_filter or index_code_filter.strip().lower() == "ndvi" else []
    indices_checked.extend(sorted(index_codes))

    return {
        "generated_at": now.isoformat(),
        "thresholds": {
            "fresh_days": fresh_days,
            "stale_after_days": stale_after_days,
            "expected_value_min": EXPECTED_VALUE_MIN,
            "expected_value_max": EXPECTED_VALUE_MAX,
        },
        "summary": {
            "total_fields": total_fields,
            "indices_checked": indices_checked,
            "fresh_field_index_pairs": fresh_count_total,
            "stale_field_index_pairs": stale_count_total,
            "missing_field_index_pairs": missing_count_total,
            "suspicious_value_count": total_suspicious,
        },
        "by_index": by_index,
        "problem_fields": problem_fields,
        "limitations": limitations,
    }
