"""
Read-only API router for multi-index satellite records (SAVI, EVI, NDMI, NDRE).

Follows the same raw-SQL / raw-dict style as backend/api/ndvi.py.
No ORM lazy loading — all queries use explicit text() SQL.
No references to ndvi_records or the legacy NDVI pipeline.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.dependencies import get_authorized_field_row, normalize_role, is_tenant_role
from api.auth import get_current_active_user
from api.query_bounds import (
    FIELD_LIST_ROW_CAP,
    SATELLITE_HISTORY_ROW_CAP,
    ensure_within_row_cap,
    fetch_limit,
)
from database import get_db

from schemas.satellite import (
    CoverageResponse, CoverageFilters, CoverageSummary,
    IndexSummaryDetail, FieldCoverageItem, IndexCoverageDetail,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/satellite-indices", tags=["satellite_indices"])

SUPPORTED_INDEX_CODES = frozenset({"savi", "evi", "ndmi", "ndre"})
MAX_DAYS = SATELLITE_HISTORY_ROW_CAP
MAX_REQUESTED_FIELD_IDS = FIELD_LIST_ROW_CAP


# ─── Coverage endpoint ───────────────────────────────────────────────────────────
# NOTE: /coverage registered BEFORE /{field_id}/... routes to prevent FastAPI
# from matching "coverage" as field_id path param.


@router.get(
    "/coverage",
    summary="Satellite index coverage across fields",
    description=(
        "Returns satellite index coverage metadata for fields matching the given "
        "criteria. Supports filtering by enterprise, field IDs, index codes, "
        "date range, and active status. "
        "Supported index codes: savi, evi, ndmi, ndre. "
        "NDVI is not supported via this endpoint (use legacy /api/ndvi). "
        "coverage_status: none (no data), partial (some indices), complete (all indices). "
        "freshness_status: no_data, fresh (within stale_after_days), stale, future_date. "
        "include_empty=true: fields with no satellite records still appear."
    ),
    responses={
        422: {"description": "Validation error"},
    },
)
async def get_coverage(
    enterprise_id: Optional[int] = None,
    field_ids: Optional[str] = None,
    index_codes: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    active_only: bool = True,
    include_empty: bool = True,
    stale_after_days: int = 10,
    as_of: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    """Satellite index coverage endpoint."""
    # ── Resolve as_of ──────────────────────────────────────────────────────
    if as_of:
        try:
            as_of_date = datetime.strptime(as_of, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid as_of date format, use YYYY-MM-DD")
    else:
        as_of_date = datetime.now().date()

    if stale_after_days < 0:
        raise HTTPException(status_code=422, detail="stale_after_days must be >= 0")

    # ── Resolve index_codes ────────────────────────────────────────────────
    requested_codes: list[str] = []
    if index_codes:
        for raw_code in index_codes.split(","):
            code = normalize_index_code(raw_code)
            if not code:
                raise HTTPException(status_code=422, detail="Empty index code in list")
            if code == "ndvi":
                raise HTTPException(
                    status_code=422,
                    detail="NDVI is not supported via satellite-indices. Use legacy /api/ndvi.",
                )
            if code not in SUPPORTED_INDEX_CODES:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unsupported index_code '{code}'. Supported: {', '.join(sorted(SUPPORTED_INDEX_CODES))}",
                )
            requested_codes.append(code)
    else:
        requested_codes = sorted(SUPPORTED_INDEX_CODES)
    requested_code_set = set(requested_codes)

    # ── Resolve field_ids ──────────────────────────────────────────────────
    parsed_field_ids: Optional[list[int]] = None
    if field_ids:
        parsed_field_ids = []
        for part in field_ids.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                fid = int(part)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"Invalid field_id: '{part}'")
            parsed_field_ids.append(fid)
        if not parsed_field_ids:
            raise HTTPException(status_code=422, detail="field_ids list is empty after parsing")
        if len(parsed_field_ids) > MAX_REQUESTED_FIELD_IDS:
            raise HTTPException(
                status_code=422,
                detail=f"field_ids supports at most {MAX_REQUESTED_FIELD_IDS} values",
            )

    # ── Validate date filters ──────────────────────────────────────────────
    parsed_date_from: Optional[date] = None
    parsed_date_to: Optional[date] = None
    if date_from:
        try:
            parsed_date_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date_from format, use YYYY-MM-DD")
    if date_to:
        try:
            parsed_date_to = datetime.strptime(date_to, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date_to format, use YYYY-MM-DD")
    if parsed_date_from and parsed_date_to and parsed_date_from > parsed_date_to:
        raise HTTPException(status_code=422, detail="date_from must be <= date_to")

    # ── Tenant scope ───────────────────────────────────────────────────────
    role = normalize_role(current_user)
    tenant_enterprise_id: Optional[int] = None
    if is_tenant_role(role):
        tenant_enterprise_id = current_user.enterprise_id
        if tenant_enterprise_id is None:
            raise HTTPException(status_code=403, detail="User has no enterprise_id")

    resolved_enterprise_id = enterprise_id
    if tenant_enterprise_id is not None:
        if enterprise_id is not None and enterprise_id != tenant_enterprise_id:
            raise HTTPException(status_code=403, detail="Cannot access other enterprise's data")
        resolved_enterprise_id = tenant_enterprise_id

    # ── Query 1: Fetch fields ──────────────────────────────────────────────
    field_params: dict = {"row_limit": fetch_limit(FIELD_LIST_ROW_CAP)}
    field_where_parts: list[str] = ["1=1"]
    if resolved_enterprise_id is not None:
        field_where_parts.append("f.enterprise_id = :eid")
        field_params["eid"] = resolved_enterprise_id
    if parsed_field_ids:
        field_where_parts.append("f.id = ANY(:fids)")
        field_params["fids"] = parsed_field_ids
    if active_only:
        field_where_parts.append("f.is_active = TRUE")

    field_where = " AND ".join(field_where_parts)

    fields_rows = db.execute(text(f"""
        SELECT f.id, f.name, f.code, f.enterprise_id, f.area_ha, f.is_active,
               e.name AS enterprise_name
        FROM fields f
        LEFT JOIN enterprises e ON e.id = f.enterprise_id
        WHERE {field_where}
        ORDER BY f.id
        LIMIT :row_limit
    """), field_params).fetchall()
    ensure_within_row_cap(
        fields_rows,
        row_cap=FIELD_LIST_ROW_CAP,
        resource="satellite_coverage_fields",
    )

    if not fields_rows:
        empty_summary = CoverageSummary(
            fields_total=0,
            fields_with_any_data=0,
            fields_without_data=0,
            fields_with_all_requested_indices=0,
            fields_with_partial_indices=0,
            fields_stale=0,
            latest_captured_date=None,
            record_count_total=0,
            index_summary={
                code: IndexSummaryDetail(
                    fields_with_data=0, record_count=0, latest_captured_date=None
                ) for code in sorted(requested_codes)
            },
        )
        return CoverageResponse(
            filters=CoverageFilters(
                enterprise_id=resolved_enterprise_id,
                field_ids=parsed_field_ids or [],
                index_codes=sorted(requested_codes),
                date_from=date_from,
                date_to=date_to,
                active_only=active_only,
                include_empty=include_empty,
                stale_after_days=stale_after_days,
                as_of=str(as_of_date),
            ),
            summary=empty_summary,
            fields=[],
        )

    field_id_list = [r.id for r in fields_rows]

    # ── Query 2: Aggregate satellite_index_records ──────────────────────────
    record_params: dict = {"fids": field_id_list}
    record_where_parts: list[str] = ["sr.field_id = ANY(:fids)", "sr.index_code = ANY(:codes)"]
    record_params["codes"] = list(requested_code_set)
    if parsed_date_from:
        record_where_parts.append("sr.captured_date >= :dfrom")
        record_params["dfrom"] = parsed_date_from
    if parsed_date_to:
        record_where_parts.append("sr.captured_date <= :dto")
        record_params["dto"] = parsed_date_to

    record_where = " AND ".join(record_where_parts)

    agg_rows = db.execute(text(f"""
        SELECT
            sr.field_id,
            sr.index_code,
            COUNT(*)                          AS record_count,
            COUNT(DISTINCT sr.captured_date)  AS date_count,
            MAX(sr.captured_date)             AS latest_captured_date
        FROM satellite_index_records sr
        WHERE {record_where}
        GROUP BY sr.field_id, sr.index_code
        ORDER BY sr.field_id, sr.index_code
    """), record_params).fetchall()

    # ── Query 3: Latest record per (field_id, index_code) for mean_value ───
    latest_rows = db.execute(text(f"""
        SELECT DISTINCT ON (sr.field_id, sr.index_code)
            sr.field_id,
            sr.index_code,
            sr.captured_date,
            sr.mean_value
        FROM satellite_index_records sr
        WHERE sr.field_id = ANY(:fids)
          AND sr.index_code = ANY(:codes)
        ORDER BY sr.field_id, sr.index_code, sr.captured_date DESC, sr.id DESC
    """), record_params).fetchall()

    latest_map: dict[tuple[int, str], Optional[float]] = {}
    for r in latest_rows:
        latest_map[(r.field_id, r.index_code)] = safe_float(r.mean_value)

    # Build per-field aggregation
    agg_map: dict[int, dict[str, dict]] = {}
    total_record_count = 0
    overall_latest: Optional[date] = None
    index_field_set: dict[str, set[int]] = {code: set() for code in requested_code_set}
    index_record_count: dict[str, int] = {code: 0 for code in requested_code_set}
    index_latest: dict[str, Optional[date]] = {code: None for code in requested_code_set}

    for row in agg_rows:
        fid = row.field_id
        code = row.index_code
        if code not in requested_code_set:
            continue
        if fid not in agg_map:
            agg_map[fid] = {}
        rc = row.record_count or 0
        dc = row.date_count or 0
        latest_date = row.latest_captured_date
        lv = latest_map.get((fid, code))

        entry = {
            "record_count": rc,
            "date_count": dc,
            "latest_captured_date": latest_date,
            "latest_mean_value": lv,
        }
        agg_map[fid][code] = entry

        total_record_count += rc
        index_record_count[code] += rc
        index_field_set[code].add(fid)
        if latest_date:
            if index_latest[code] is None or latest_date > index_latest[code]:
                index_latest[code] = latest_date
            if overall_latest is None or latest_date > overall_latest:
                overall_latest = latest_date

    # ── Assemble field coverage items ──────────────────────────────────────
    field_items: list[FieldCoverageItem] = []
    fields_with_any_data = 0
    fields_all_indices = 0
    fields_partial = 0
    fields_no_data = 0
    fields_stale_count = 0

    for f in fields_rows:
        fid = f.id
        field_agg = agg_map.get(fid, {})
        has_any = len(field_agg) > 0
        has_all = len(field_agg) >= len(requested_code_set)

        if not has_any:
            coverage_status = "none"
            fields_no_data += 1
        elif has_all:
            coverage_status = "complete"
            fields_with_any_data += 1
            fields_all_indices += 1
        else:
            coverage_status = "partial"
            fields_with_any_data += 1
            fields_partial += 1

        # freshness_status
        field_latest: Optional[date] = None
        for code_agg in field_agg.values():
            ld = code_agg.get("latest_captured_date")
            if ld and (field_latest is None or ld > field_latest):
                field_latest = ld

        if field_latest is None:
            freshness_status = "no_data"
        elif field_latest > as_of_date:
            freshness_status = "future_date"
        elif (as_of_date - field_latest).days <= stale_after_days:
            freshness_status = "fresh"
        else:
            freshness_status = "stale"
            fields_stale_count += 1

        # Per-index details
        indices_dict: dict[str, IndexCoverageDetail] = {}
        total_fc = 0
        total_dc = 0
        for code in sorted(requested_code_set):
            code_agg = field_agg.get(code)
            if code_agg:
                rc = code_agg["record_count"]
                dc = code_agg["date_count"]
                ld = code_agg["latest_captured_date"]
                lv = code_agg["latest_mean_value"]
                total_fc += rc
                total_dc += dc
            else:
                rc = 0
                dc = 0
                ld = None
                lv = None
            indices_dict[code] = IndexCoverageDetail(
                has_data=rc > 0,
                record_count=rc,
                date_count=dc,
                latest_captured_date=str(ld) if ld else None,
                latest_mean_value=lv,
            )

        days_since = None
        if field_latest:
            days_since = (as_of_date - field_latest).days

        field_items.append(FieldCoverageItem(
            field_id=fid,
            field_name=f.name,
            field_code=f.code,
            enterprise_id=f.enterprise_id,
            enterprise_name=f.enterprise_name or "",
            area_ha=safe_float(f.area_ha),
            is_active=f.is_active,
            has_any_data=has_any,
            has_all_requested_indices=has_all,
            coverage_status=coverage_status,
            freshness_status=freshness_status,
            latest_captured_date=str(field_latest) if field_latest else None,
            days_since_latest=days_since,
            record_count_total=total_fc,
            date_count_total=total_dc,
            indices=indices_dict,
        ))

    if not include_empty:
        field_items = [fi for fi in field_items if fi.has_any_data]
        fields_with_any_data = sum(1 for fi in field_items if fi.has_any_data)
        fields_no_data = 0
        fields_all_indices = sum(1 for fi in field_items if fi.has_all_requested_indices)
        fields_partial = sum(1 for fi in field_items if fi.coverage_status == "partial")
        fields_stale_count = sum(1 for fi in field_items if fi.freshness_status == "stale")

    # ── Build index_summary ────────────────────────────────────────────────
    index_summary_dict: dict[str, IndexSummaryDetail] = {}
    for code in sorted(requested_code_set):
        index_summary_dict[code] = IndexSummaryDetail(
            fields_with_data=len(index_field_set[code]),
            record_count=index_record_count[code],
            latest_captured_date=str(index_latest[code]) if index_latest[code] else None,
        )

    summary = CoverageSummary(
        fields_total=len(field_items),
        fields_with_any_data=fields_with_any_data,
        fields_without_data=fields_no_data,
        fields_with_all_requested_indices=fields_all_indices,
        fields_with_partial_indices=fields_partial,
        fields_stale=fields_stale_count,
        latest_captured_date=str(overall_latest) if overall_latest else None,
        record_count_total=total_record_count,
        index_summary=index_summary_dict,
    )

    return CoverageResponse(
        filters=CoverageFilters(
            enterprise_id=resolved_enterprise_id,
            field_ids=parsed_field_ids or [],
            index_codes=sorted(requested_codes),
            date_from=date_from,
            date_to=date_to,
            active_only=active_only,
            include_empty=include_empty,
            stale_after_days=stale_after_days,
            as_of=str(as_of_date),
        ),
        summary=summary,
        fields=field_items,
    )


# ─── Local helpers ───────────────────────────────────────────────────────────

def normalize_index_code(index_code: str) -> str:
    """Lowercase + strip. Returns empty string if None/whitespace."""
    if not index_code or not index_code.strip():
        return ""
    return index_code.strip().lower()


def safe_float(v):
    """Convert to float or None. Rejects NaN/Inf."""
    if v is None:
        return None
    try:
        val = float(v)
        import math
        return None if math.isnan(val) or math.isinf(val) else val
    except (TypeError, ValueError):
        return None


def _format_record(row) -> dict:
    """Convert a satellite_index_records row tuple into a JSON-safe dict."""
    return {
        "id": row.id,
        "field_id": row.field_id,
        "captured_date": str(row.captured_date),
        "index_code": row.index_code,
        "mean_value": safe_float(row.mean_value),
        "min_value": safe_float(row.min_value),
        "max_value": safe_float(row.max_value),
        "std_value": safe_float(row.std_value),
        "p10_value": safe_float(row.p10_value),
        "p90_value": safe_float(row.p90_value),
        "valid_pixels_pct": safe_float(row.valid_pixels_pct),
        "cloud_cover_pct": safe_float(row.cloud_cover_pct),
        "satellite": row.satellite,
        "created_at": str(row.created_at) if row.created_at else None,
    }


def _validate_and_normalize_index(code: str) -> str:
    """Validate index_code query param, return normalized form or 422."""
    normalized = normalize_index_code(code)
    if not normalized:
        raise HTTPException(status_code=422, detail="index_code is required")
    if normalized not in SUPPORTED_INDEX_CODES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported index_code '{code}'. Supported: {', '.join(sorted(SUPPORTED_INDEX_CODES))}",
        )
    return normalized


def _cloud_filter(include_cloudy: bool) -> tuple[str, str]:
    """Return (where_clause, having_clause) for cloud cover filtering."""
    if include_cloudy:
        return "", ""
    # Exclude records where cloud_cover_pct > 30; keep NULL cloud_cover_pct
    return "AND (cloud_cover_pct IS NULL OR cloud_cover_pct <= 30)", ""


# ─── Endpoints ───────────────────────────────────────────────────────────────

_LATEST_OK_EXAMPLE = {
    "field_id": 4,
    "field_name": "1361 Гарден галла 2026",
    "index_code": "savi",
    "record": {
        "id": 1,
        "field_id": 4,
        "captured_date": "2026-06-22",
        "index_code": "savi",
        "mean_value": 0.3229,
        "min_value": 0.3229,
        "max_value": 0.3229,
        "std_value": 0.0,
        "p10_value": 0.3229,
        "p90_value": 0.3229,
        "valid_pixels_pct": 100.0,
        "cloud_cover_pct": None,
        "satellite": "Sentinel-2",
        "created_at": "2026-06-27T04:50:27.439756",
    },
}

_LATEST_NODATA_EXAMPLE = {
    "field_id": 4,
    "field_name": "1361 Гарден галла 2026",
    "index_code": "savi",
    "record": None,
}

_HISTORY_OK_EXAMPLE = {
    "field_id": 4,
    "field_name": "1361 Гарден галла 2026",
    "index_code": "savi",
    "days": 30,
    "include_cloudy": False,
    "records": [
        {
            "id": 1,
            "field_id": 4,
            "captured_date": "2026-06-22",
            "index_code": "savi",
            "mean_value": 0.3229,
            "min_value": 0.3229,
            "max_value": 0.3229,
            "std_value": 0.0,
            "p10_value": 0.3229,
            "p90_value": 0.3229,
            "valid_pixels_pct": 100.0,
            "cloud_cover_pct": None,
            "satellite": "Sentinel-2",
            "created_at": "2026-06-27T04:50:27.439756",
        },
    ],
    "count": 1,
}

_HISTORY_EMPTY_EXAMPLE = {
    "field_id": 4,
    "field_name": "1361 Гарден галла 2026",
    "index_code": "savi",
    "days": 30,
    "include_cloudy": False,
    "records": [],
    "count": 0,
}

_NOT_FOUND_EXAMPLE = {"detail": "Field not found"}

_UNSUPPORTED_CODE_EXAMPLE = {
    "detail": "Unsupported index_code 'ndvi'. Supported: evi, ndmi, ndre, savi",
}


@router.get(
    "/{field_id}/latest",
    summary="Latest satellite index record for a field",
    description=(
        "Returns the most recent satellite index record for a given field and "
        "index code. The record is nested under a `record` key. "
        "If no matching record exists, `record` is `null`. "
        "Supported index codes: savi, evi, ndmi, ndre."
    ),
    responses={
        200: {
            "description": "Latest record (record may be null if no data)",
            "content": {
                "application/json": {
                    "examples": {
                        "Has data": {"value": _LATEST_OK_EXAMPLE},
                        "No data": {"value": _LATEST_NODATA_EXAMPLE},
                    },
                },
            },
        },
        404: {
            "description": "Field not found",
            "content": {
                "application/json": {
                    "example": _NOT_FOUND_EXAMPLE,
                },
            },
        },
        422: {
            "description": "Unsupported index_code",
            "content": {
                "application/json": {
                    "example": _UNSUPPORTED_CODE_EXAMPLE,
                },
            },
        },
    },
)
async def get_latest(
    field_id: int,
    index_code: str,
    include_cloudy: bool = False,
    db: Session = Depends(get_db),
    _auth_field=Depends(get_authorized_field_row),
):
    """Latest satellite index record for a field and index code."""
    code = _validate_and_normalize_index(index_code)
    cloud_where, _ = _cloud_filter(include_cloudy)

    row = db.execute(text(f"""
        SELECT id, field_id, captured_date, index_code,
               mean_value, min_value, max_value, std_value,
               p10_value, p90_value,
               valid_pixels_pct, cloud_cover_pct, satellite, created_at
        FROM satellite_index_records
        WHERE field_id = :fid
          AND index_code = :code
          {cloud_where}
        ORDER BY captured_date DESC, id DESC
        LIMIT 1
    """), {"fid": field_id, "code": code}).fetchone()

    result = {
        "field_id": field_id,
        "field_name": _auth_field.name,
        "index_code": code,
        "record": _format_record(row) if row else None,
    }
    return result


@router.get(
    "/{field_id}/history",
    summary="Historical satellite index records for a field",
    description=(
        "Returns satellite index records over a lookback window for a given "
        "field and index code. Records are in the `records` list; `count` is "
        "the number of records returned. "
        "Supported index codes: savi, evi, ndmi, ndre."
    ),
    responses={
        200: {
            "description": "Historical records (records may be empty)",
            "content": {
                "application/json": {
                    "examples": {
                        "Has data": {"value": _HISTORY_OK_EXAMPLE},
                        "Empty history": {"value": _HISTORY_EMPTY_EXAMPLE},
                    },
                },
            },
        },
        404: {
            "description": "Field not found",
            "content": {
                "application/json": {
                    "example": _NOT_FOUND_EXAMPLE,
                },
            },
        },
        422: {
            "description": "Unsupported index_code",
            "content": {
                "application/json": {
                    "example": _UNSUPPORTED_CODE_EXAMPLE,
                },
            },
        },
    },
)
async def get_history(
    field_id: int,
    index_code: str,
    days: int = 90,
    include_cloudy: bool = False,
    db: Session = Depends(get_db),
    _auth_field=Depends(get_authorized_field_row),
):
    """Historical satellite index records for a field and index code."""
    code = _validate_and_normalize_index(index_code)

    if days < 1:
        raise HTTPException(status_code=422, detail="days must be >= 1")
    if days > MAX_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"days must be <= {MAX_DAYS}",
        )

    cloud_where, _ = _cloud_filter(include_cloudy)
    since_date = (datetime.now() - timedelta(days=days)).date()

    rows = db.execute(text(f"""
        SELECT id, field_id, captured_date, index_code,
               mean_value, min_value, max_value, std_value,
               p10_value, p90_value,
               valid_pixels_pct, cloud_cover_pct, satellite, created_at
        FROM satellite_index_records
        WHERE field_id = :fid
          AND index_code = :code
          AND captured_date >= :since
          {cloud_where}
        ORDER BY captured_date ASC, id ASC
        LIMIT :row_limit
    """), {
        "fid": field_id,
        "code": code,
        "since": since_date,
        "row_limit": fetch_limit(SATELLITE_HISTORY_ROW_CAP),
    }).fetchall()
    ensure_within_row_cap(
        rows,
        row_cap=SATELLITE_HISTORY_ROW_CAP,
        resource="satellite_index_history",
    )

    records = [_format_record(r) for r in rows]

    return {
        "field_id": field_id,
        "field_name": _auth_field.name,
        "index_code": code,
        "days": days,
        "include_cloudy": include_cloudy,
        "records": records,
        "count": len(records),
    }
