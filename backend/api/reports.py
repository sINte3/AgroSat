"""
API для отчётов AgroSat.

Эндпоинты:
    GET /api/reports/enterprise/{enterprise_id}/pdf — скачать PDF отчёт
    GET /api/reports/management/summary — JSON management report read model
    GET /api/reports/management/satellite-indices/summary — SAVI/EVI/NDMI/NDRE aggregation
    GET /api/reports/management/pdf — скачать PDF управленческого отчёта
    GET /api/reports/management/excel — скачать Excel управленческого отчёта
"""

import logging
import io
import math
import urllib.parse
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from services.pdf_report import (
    generate_enterprise_pdf,
    generate_management_report_pdf,
)
from models.monitoring import User
from api.auth import get_current_active_user
from api.dependencies import (
    require_enterprise_scope,
    normalize_role,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reports", tags=["reports"])


# ─── Shared helpers ───────────────────────────────────────────────────────────


def _safe_float(v):
    """Convert to float or None. Handles None, NaN, Inf."""
    if v is None:
        return None
    try:
        val = float(v)
        return None if math.isnan(val) or math.isinf(val) else round(val, 4)
    except (TypeError, ValueError):
        return None


def _coalesce_int(v):
    """Return int or 0 for values from SQL aggregates."""
    if v is None:
        return 0
    return int(v)


# ─── Shared data-fetching helpers (for JSON + PDF) ─────────────────────────


def _fetch_management_summary_data(
    db: Session, date_from: str, date_to: str,
    current_user: User, effective_eid: int,
):
    """Returns the same nested dict as get_management_summary, for reuse by PDF endpoint."""
    now = datetime.utcnow()
    to_date = now
    if date_to:
        try:
            to_date = datetime.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date_to format (use ISO 8601)")

    from_date = to_date - timedelta(days=7)
    if date_from:
        try:
            from_date = datetime.fromisoformat(date_from)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date_from format (use ISO 8601)")

    generated_at = now.isoformat()
    date_range = {"from": from_date.isoformat(), "to": to_date.isoformat()}

    role = normalize_role(current_user)
    tenant_clause = ""
    params = {"from_date": from_date.date(), "to_date": to_date.date()}
    if effective_eid is not None:
        tenant_clause = "AND f.enterprise_id = :eid"
        params["eid"] = effective_eid

    sql = text(f"""
        WITH
        active_fields AS (
            SELECT f.id, f.area_ha, f.enterprise_id
            FROM fields f
            WHERE f.is_active = true {tenant_clause}
        ),
        latest_ndvi AS (
            SELECT DISTINCT ON (nr.field_id)
                nr.field_id, nr.mean_ndvi, nr.captured_date
            FROM ndvi_records nr
            WHERE nr.captured_date <= :to_date
            ORDER BY nr.field_id, nr.captured_date DESC
        ),
        active_alert_counts AS (
            SELECT
                f.enterprise_id,
                COUNT(*) FILTER (WHERE a.severity = 'critical') AS critical_count,
                COUNT(*) FILTER (WHERE a.severity IN ('warning', 'high')) AS high_count,
                COUNT(*) FILTER (WHERE a.severity = 'info') AS info_count,
                COUNT(*) AS total_active
            FROM alerts a
            JOIN fields f ON f.id = a.field_id
            WHERE a.is_active = true
              {tenant_clause.replace('f.', 'f.')}
            GROUP BY f.enterprise_id
        ),
        cluster AS (
            SELECT
                COUNT(af.id) AS total_fields,
                COALESCE(SUM(af.area_ha), 0) AS total_hectares,
                COUNT(lndvi.field_id) AS fields_with_data,
                AVG(lndvi.mean_ndvi) AS avg_ndvi,
                MAX(lndvi.captured_date) AS latest_ndvi_date,
                COALESCE((SELECT SUM(aac.total_active) FROM active_alert_counts aac), 0) AS active_alerts,
                COALESCE((SELECT SUM(aac.critical_count) FROM active_alert_counts aac), 0) AS critical_alerts
            FROM active_fields af
            LEFT JOIN latest_ndvi lndvi ON lndvi.field_id = af.id
        ),
        enterprises_agg AS (
            SELECT
                e.id, e.name,
                COUNT(DISTINCT af.id) AS field_count,
                COALESCE(SUM(af.area_ha), 0) AS total_hectares,
                COUNT(DISTINCT lndvi.field_id) AS fields_with_data,
                AVG(lndvi.mean_ndvi) AS avg_ndvi,
                MAX(lndvi.captured_date) AS latest_data_date,
                COALESCE(aac.total_active, 0) AS active_alerts,
                COALESCE(aac.critical_count, 0) AS critical_alerts
            FROM enterprises e
            LEFT JOIN active_fields af ON af.enterprise_id = e.id
            LEFT JOIN latest_ndvi lndvi ON lndvi.field_id = af.id
            LEFT JOIN active_alert_counts aac ON aac.enterprise_id = e.id
            WHERE EXISTS (SELECT 1 FROM active_fields af2 WHERE af2.enterprise_id = e.id)
               OR (SELECT COUNT(*) FROM active_fields) = 0
            GROUP BY e.id, e.name, aac.total_active, aac.critical_count
            ORDER BY e.name
        )
        SELECT
            c.total_fields, c.total_hectares, c.fields_with_data,
            c.avg_ndvi, c.active_alerts, c.critical_alerts, c.latest_ndvi_date,
            (SELECT COALESCE(json_agg(
                json_build_object(
                    'id', ea.id, 'name', ea.name,
                    'field_count', ea.field_count,
                    'total_hectares', ROUND(CAST(ea.total_hectares AS numeric), 1),
                    'active_alerts', ea.active_alerts,
                    'critical_alerts', ea.critical_alerts,
                    'fields_with_data', ea.fields_with_data,
                    'fields_without_data', ea.field_count - ea.fields_with_data,
                    'avg_ndvi', ea.avg_ndvi,
                    'latest_data_date', CASE WHEN ea.latest_data_date IS NOT NULL THEN ea.latest_data_date::text ELSE NULL END
                )
                ORDER BY ea.name
            ) FILTER (WHERE ea.id IS NOT NULL), '[]'::json) FROM enterprises_agg ea) AS enterprises_json
        FROM cluster c
    """)

    row = db.execute(sql, params).fetchone()
    if not row:
        return _empty_response(generated_at, date_range)

    total_fields = _coalesce_int(row.total_fields)
    fields_with_data = _coalesce_int(row.fields_with_data)

    summary = {
        "total_fields": total_fields,
        "total_hectares": round(float(row.total_hectares), 1) if row.total_hectares else 0,
        "active_alerts": _coalesce_int(row.active_alerts),
        "problem_fields": None,
        "fields_with_data": fields_with_data,
        "fields_without_data": max(0, total_fields - fields_with_data),
        "avg_ndvi": _safe_float(row.avg_ndvi),
    }

    enterprises = row.enterprises_json if row.enterprises_json else []

    # Alert severity breakdown
    alert_conditions = ["a.is_active = true"]
    alert_params = {}
    if effective_eid is not None:
        alert_conditions.append("f.enterprise_id = :eid")
        alert_params["eid"] = effective_eid
    alert_where = " AND ".join(alert_conditions)
    alert_row = db.execute(
        text(f"""
            SELECT
                COUNT(*) AS total_active,
                COUNT(*) FILTER (WHERE a.severity = 'critical') AS critical,
                COUNT(*) FILTER (WHERE a.severity IN ('warning', 'high')) AS high,
                COUNT(*) FILTER (WHERE a.severity = 'info') AS info
            FROM alerts a
            JOIN fields f ON f.id = a.field_id
            WHERE {alert_where}
        """),
        alert_params,
    ).fetchone()

    latest_alerts_sql = text(f"""
        SELECT a.id, a.title, a.severity, a.triggered_at,
               f.name AS field_name, e.name AS enterprise_name
        FROM alerts a
        JOIN fields f ON f.id = a.field_id
        JOIN enterprises e ON e.id = f.enterprise_id
        WHERE a.is_active = true {tenant_clause}
        ORDER BY a.triggered_at DESC NULLS LAST
        LIMIT 10
    """)
    latest_alerts = db.execute(latest_alerts_sql, params).fetchall()

    alerts_summary = {
        "total_active": _coalesce_int(alert_row.total_active) if alert_row else 0,
        "critical": _coalesce_int(alert_row.critical) if alert_row else 0,
        "high": _coalesce_int(alert_row.high) if alert_row else 0,
        "info": _coalesce_int(alert_row.info) if alert_row else 0,
        "latest_items": [
            {
                "id": r.id,
                "title": r.title,
                "severity": r.severity,
                "field_name": r.field_name,
                "enterprise_name": r.enterprise_name,
                "triggered_at": r.triggered_at.isoformat() if r.triggered_at else None,
            }
            for r in latest_alerts
        ],
    }

    # Data freshness
    data_freshness = {}
    if row.latest_ndvi_date:
        data_freshness["latest_ndvi_date"] = str(row.latest_ndvi_date)
    else:
        data_freshness["latest_ndvi_date"] = None
        data_freshness["note"] = "No NDVI data found in the database"

    si_row = db.execute(text("""
        SELECT MAX(captured_date) AS latest_date FROM satellite_index_records
    """)).fetchone()
    if si_row and si_row.latest_date:
        data_freshness["latest_satellite_index_date"] = str(si_row.latest_date)

    data_freshness["fields_without_data"] = max(0, total_fields - fields_with_data)

    # Limitations
    limitations = [
        "SAVI/EVI/NDMI/NDRE enterprise aggregation is not included in this endpoint; planned for TASK_118.",
        "Field-level risk classification is not included because thresholds are not yet defined.",
        "Export to PDF/Excel is not included in this endpoint.",
        "Per-enterprise NDVI averages are at the enterprise aggregate level; field-level thresholds are not applied.",
    ]

    return {
        "generated_at": generated_at,
        "date_range": date_range,
        "summary": summary,
        "enterprises": enterprises,
        "alerts": alerts_summary,
        "data_freshness": data_freshness,
        "limitations": limitations,
    }


def _fetch_satellite_indices_data(
    db: Session, date_from: str, date_to: str,
    current_user: User, effective_eid: int,
):
    """Returns the same nested dict as get_satellite_indices_summary, for reuse by PDF endpoint."""
    now = datetime.utcnow()
    to_date = now
    if date_to:
        try:
            to_date = datetime.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date_to format (use ISO 8601)")

    from_date = to_date - timedelta(days=7)
    if date_from:
        try:
            from_date = datetime.fromisoformat(date_from)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date_from format (use ISO 8601)")

    generated_at = now.isoformat()
    date_range = {"from": from_date.isoformat(), "to": to_date.isoformat()}

    params = {"from_date": from_date.date(), "to_date": to_date.date()}
    tenant_clause = ""
    enterprise_join_clause = ""
    if effective_eid is not None:
        tenant_clause = "AND f.enterprise_id = :eid"
        enterprise_join_clause = "AND e.id = :eid"
        params["eid"] = effective_eid

    sql = text(f"""
        WITH
        active_fields AS (
            SELECT f.id, f.enterprise_id
            FROM fields f
            WHERE f.is_active = true {tenant_clause}
        ),
        index_agg AS (
            SELECT
                sir.index_code,
                COUNT(sir.id) AS record_count,
                COUNT(DISTINCT sir.field_id) AS field_count,
                AVG(sir.mean_value) AS avg_mean_value,
                MIN(sir.mean_value) AS min_mean_value,
                MAX(sir.mean_value) AS max_mean_value,
                AVG(sir.valid_pixels_pct) AS avg_valid_pixels_pct,
                AVG(sir.cloud_cover_pct) AS avg_cloud_cover_pct,
                AVG(sir.std_value) AS avg_std_value,
                MAX(sir.captured_date) AS latest_captured_date
            FROM satellite_index_records sir
            JOIN active_fields af ON af.id = sir.field_id
            WHERE sir.index_code IN ('savi', 'evi', 'ndmi', 'ndre')
              AND sir.captured_date BETWEEN :from_date AND :to_date
            GROUP BY sir.index_code
        ),
        enterprise_agg AS (
            SELECT
                e.id AS enterprise_id, e.name AS enterprise_name,
                sir.index_code,
                COUNT(sir.id) AS record_count,
                COUNT(DISTINCT sir.field_id) AS field_count,
                AVG(sir.mean_value) AS avg_mean_value,
                MIN(sir.mean_value) AS min_mean_value,
                MAX(sir.mean_value) AS max_mean_value,
                MAX(sir.captured_date) AS latest_captured_date
            FROM enterprises e
            JOIN fields f ON f.enterprise_id = e.id AND f.is_active = true {enterprise_join_clause.replace('f.', 'f.')}
            JOIN satellite_index_records sir ON sir.field_id = f.id
            WHERE sir.index_code IN ('savi', 'evi', 'ndmi', 'ndre')
              AND sir.captured_date BETWEEN :from_date AND :to_date
            GROUP BY e.id, e.name, sir.index_code
            ORDER BY e.name, sir.index_code
        )
        SELECT
            COALESCE(json_agg(
                json_build_object(
                    'index_code', ia.index_code,
                    'avg_mean_value', ia.avg_mean_value,
                    'min_mean_value', ia.min_mean_value,
                    'max_mean_value', ia.max_mean_value,
                    'record_count', ia.record_count,
                    'field_count', ia.field_count,
                    'latest_captured_date', CASE WHEN ia.latest_captured_date IS NOT NULL THEN ia.latest_captured_date::text ELSE NULL END,
                    'avg_valid_pixels_pct', ia.avg_valid_pixels_pct,
                    'avg_cloud_cover_pct', ia.avg_cloud_cover_pct,
                    'avg_std_value', ia.avg_std_value
                )
                ORDER BY ia.index_code
            ) FILTER (WHERE ia.index_code IS NOT NULL), '[]'::json) AS cluster_json,
            COALESCE(json_agg(
                json_build_object(
                    'enterprise_id', ea.enterprise_id,
                    'enterprise_name', ea.enterprise_name,
                    'index_code', ea.index_code,
                    'avg_mean_value', ea.avg_mean_value,
                    'min_mean_value', ea.min_mean_value,
                    'max_mean_value', ea.max_mean_value,
                    'record_count', ea.record_count,
                    'field_count', ea.field_count,
                    'latest_captured_date', CASE WHEN ea.latest_captured_date IS NOT NULL THEN ea.latest_captured_date::text ELSE NULL END
                )
                ORDER BY ea.enterprise_name, ea.index_code
            ) FILTER (WHERE ea.enterprise_id IS NOT NULL), '[]'::json) AS enterprises_json
        FROM index_agg ia
        FULL JOIN enterprise_agg ea ON false
    """)

    row = db.execute(sql, params).fetchone()

    cluster_indices = {}
    for item in row.cluster_json if row and row.cluster_json else []:
        code = item["index_code"]
        cluster_indices[code] = {
            "avg_mean_value": _safe_float(item["avg_mean_value"]),
            "min_mean_value": _safe_float(item["min_mean_value"]),
            "max_mean_value": _safe_float(item["max_mean_value"]),
            "record_count": _coalesce_int(item["record_count"]),
            "field_count": _coalesce_int(item["field_count"]),
            "latest_captured_date": item["latest_captured_date"],
        }

    SUPPORTED = frozenset({"savi", "evi", "ndmi", "ndre"})
    cluster = {}
    for code in sorted(SUPPORTED):
        if code in cluster_indices:
            cluster[code] = cluster_indices[code]
        else:
            cluster[code] = {
                "avg_mean_value": None, "min_mean_value": None, "max_mean_value": None,
                "record_count": 0, "field_count": 0, "latest_captured_date": None,
            }

    enterprise_map = {}
    for item in row.enterprises_json if row and row.enterprises_json else []:
        eid = item["enterprise_id"]
        if eid not in enterprise_map:
            enterprise_map[eid] = {
                "enterprise_id": eid,
                "enterprise_name": item["enterprise_name"],
                "indices": {},
            }
        code = item["index_code"]
        enterprise_map[eid]["indices"][code] = {
            "avg_mean_value": _safe_float(item["avg_mean_value"]),
            "min_mean_value": _safe_float(item["min_mean_value"]),
            "max_mean_value": _safe_float(item["max_mean_value"]),
            "record_count": _coalesce_int(item["record_count"]),
            "field_count": _coalesce_int(item["field_count"]),
            "latest_captured_date": item["latest_captured_date"],
        }

    # Compute latest_snapshot_date per enterprise
    enterprises = []
    for ent in sorted(enterprise_map.values(), key=lambda x: x["enterprise_name"]):
        snap_dates = [
            idx["latest_captured_date"]
            for idx in ent["indices"].values()
            if idx["latest_captured_date"]
        ]
        ent["latest_snapshot_date"] = max(snap_dates) if snap_dates else None
        enterprises.append(ent)

    data_freshness = {}
    latest_rec = db.execute(
        text("""
            SELECT MAX(captured_date) AS latest_date
            FROM satellite_index_records
            WHERE index_code IN ('savi', 'evi', 'ndmi', 'ndre')
        """)
    ).fetchone()
    if latest_rec and latest_rec.latest_date:
        data_freshness["latest_satellite_index_date"] = str(latest_rec.latest_date)
    else:
        data_freshness["latest_satellite_index_date"] = None
        data_freshness["note"] = "No satellite index data found"

    ndvi_count = db.execute(
        text("""
            SELECT COUNT(*) AS cnt
            FROM satellite_index_records
            WHERE LOWER(index_code) = 'ndvi'
              AND captured_date BETWEEN :from_date AND :to_date
        """),
        {"from_date": from_date.date(), "to_date": to_date.date()},
    ).scalar() or 0

    limitations = []
    if ndvi_count > 0:
        limitations.append(
            f"Unsupported NDVI records found in satellite_index_records: {ndvi_count}; "
            "these are excluded from aggregation."
        )
    has_enterprise_data = any(e["indices"] for e in enterprises)
    if not has_enterprise_data and effective_eid is None:
        if not any(v["record_count"] > 0 for v in cluster.values()):
            limitations.append("No satellite index data found for the current date range.")
        else:
            limitations.append(
                "Enterprise-level breakdown is empty; enterprises may lack active fields "
                "or satellite index records in the requested date range."
            )

    return {
        "generated_at": generated_at,
        "date_range": date_range,
        "index_codes": sorted(SUPPORTED),
        "cluster": cluster,
        "enterprises": enterprises,
        "data_freshness": data_freshness,
        "limitations": limitations,
    }


# ─── Management summary endpoint ──────────────────────────────────────────────


@router.get(
    "/management/summary",
    summary="Management report JSON read model",
    description=(
        "Returns a JSON management report read model with cluster-wide and "
        "per-enterprise aggregated metrics. Uses explicit CTE queries — no N+1. "
        "Tenant-scoped: admin/manager see all enterprises, "
        "agronomist/viewer see only their own enterprise."
    ),
)
def get_management_summary(
    date_from: str = Query(None, description="Start date (ISO format, optional)"),
    date_to: str = Query(None, description="End date (ISO format, optional)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    effective_eid: int = Depends(require_enterprise_scope),
):
    """JSON management report read model — delegates to shared helper."""
    return _fetch_management_summary_data(db, date_from, date_to, current_user, effective_eid)


# ─── Satellite index aggregation endpoint ────────────────────────────────────

SUPPORTED_SATELLITE_INDEX_CODES = frozenset({"savi", "evi", "ndmi", "ndre"})


@router.get(
    "/management/satellite-indices/summary",
    summary="SAVI/EVI/NDMI/NDRE aggregation for management reports",
    description=(
        "Returns cluster-wide and per-enterprise aggregated SAVI/EVI/NDMI/NDRE "
        "values from satellite_index_records. Uses a single CTE query with "
        "json_agg for enterprise breakdown — no N+1. "
        "Tenant-scoped: admin/manager see all enterprises, "
        "agronomist/viewer see only their own enterprise. "
        "NDVI is excluded from this endpoint (use /api/ndvi/*)."
    ),
)
def get_satellite_indices_summary(
    date_from: str = Query(None, description="Start date (ISO format, optional)"),
    date_to: str = Query(None, description="End date (ISO format, optional)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    effective_eid: int = Depends(require_enterprise_scope),
):
    """SAVI/EVI/NDMI/NDRE aggregation for management reports — delegates to shared helper."""
    return _fetch_satellite_indices_data(db, date_from, date_to, current_user, effective_eid)


def _empty_response(generated_at: str, date_range: dict) -> dict:
    """Return an empty/fallback response when no data is available."""
    return {
        "generated_at": generated_at,
        "date_range": date_range,
        "summary": {
            "total_fields": 0,
            "total_hectares": 0,
            "fields_with_data": 0,
            "fields_without_data": 0,
            "active_alerts": 0,
            "problem_fields": None,
            "avg_ndvi": None,
        },
        "enterprises": [],
        "alerts": {"total_active": 0, "critical": 0, "high": 0, "info": 0, "latest_items": []},
        "data_freshness": {},
        "limitations": [],
    }


# ─── Management PDF endpoint ────────────────────────────────────────────────


@router.get(
    "/management/pdf",
    summary="Download management report PDF",
    description=(
        "Generates and returns a PDF management report containing cluster-wide and "
        "per-enterprise aggregated metrics, alert summary, data freshness, and "
        "satellite index aggregation (SAVI/EVI/NDMI/NDRE). "
        "Tenant-scoped: admin/manager see all enterprises, "
        "agronomist/viewer see only their own enterprise."
    ),
)
def download_management_pdf(
    date_from: str = Query(None, description="Start date (ISO format, optional)"),
    date_to: str = Query(None, description="End date (ISO format, optional)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    effective_eid: int = Depends(require_enterprise_scope),
):
    """Generate and return a management-level PDF report."""

    mgmt_data = _fetch_management_summary_data(db, date_from, date_to, current_user, effective_eid)
    si_data = _fetch_satellite_indices_data(db, date_from, date_to, current_user, effective_eid)

    try:
        pdf_bytes = generate_management_report_pdf(
            generated_at=mgmt_data["generated_at"],
            date_range=mgmt_data["date_range"],
            summary=mgmt_data["summary"],
            enterprises=mgmt_data["enterprises"],
            alerts=mgmt_data["alerts"],
            data_freshness=mgmt_data["data_freshness"],
            limitations_summary=mgmt_data["limitations"],
            satellite_cluster=si_data["cluster"],
            satellite_enterprises=si_data["enterprises"],
            satellite_limitations=si_data["limitations"],
        )
    except Exception as e:
        logger.error(f"Management PDF generation failed: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка генерации PDF управленческого отчёта: {str(e)[:200]}",
        )

    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"agrosat-management-report-{date_str}.pdf"
    encoded = urllib.parse.quote(filename)

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )


# ─── Excel helper ──────────────────────────────────────────────────────────────


def _safe_str(val):
    """Render value as string; missing/null as em-dash."""
    if val is None:
        return "—"
    return str(val)


def _bold_header(ws, row, max_col):
    """Style a header row bold with fill."""
    fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    font = Font(bold=True)
    for c in range(1, max_col + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = font
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", wrap_text=True)


def _auto_width(ws, min_width=10, max_width=40):
    """Set column widths based on header content."""
    for col_cells in ws.columns:
        length = min_width
        for i, cell in enumerate(col_cells):
            if cell.value:
                length = max(length, min(len(str(cell.value)) + 2, max_width))
        ws.column_dimensions[get_column_letter(col_cells[0].column)].width = length


# ─── Management Excel endpoint ───────────────────────────────────────────────


@router.get(
    "/management/excel",
    summary="Download management report Excel",
    description=(
        "Generates and returns an XLSX management report with multiple worksheets: "
        "Summary, Enterprises, Satellite Indices (cluster & enterprise), Alerts, and Limitations. "
        "Tenant-scoped: admin/manager see all enterprises, "
        "agronomist/viewer see only their own enterprise."
    ),
)
def download_management_excel(
    date_from: str = Query(None, description="Start date (ISO format, optional)"),
    date_to: str = Query(None, description="End date (ISO format, optional)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    effective_eid: int = Depends(require_enterprise_scope),
):
    """Generate and return a management-level XLSX report."""

    mgmt_data = _fetch_management_summary_data(db, date_from, date_to, current_user, effective_eid)
    si_data = _fetch_satellite_indices_data(db, date_from, date_to, current_user, effective_eid)

    wb = Workbook()

    # ── Summary ──────────────────────────────────────────────────────────────
    ws_summary = wb.active
    ws_summary.title = "Summary"
    s = mgmt_data["summary"]
    dr = mgmt_data["date_range"]
    df = mgmt_data["data_freshness"]

    summary_rows = [
        ("Generated At", mgmt_data["generated_at"]),
        ("Date Range From", dr.get("from", "")),
        ("Date Range To", dr.get("to", "")),
        ("Total Fields", s["total_fields"]),
        ("Total Hectares", s["total_hectares"]),
        ("Fields With Data", s["fields_with_data"]),
        ("Fields Without Data", s["fields_without_data"]),
        ("Average NDVI", _safe_str(s.get("avg_ndvi"))),
        ("Active Alerts", s["active_alerts"]),
        ("Latest NDVI Date", _safe_str(df.get("latest_ndvi_date"))),
        ("Latest Satellite Index Date", _safe_str(df.get("latest_satellite_index_date"))),
    ]
    for i, (label, value) in enumerate(summary_rows, 1):
        ws_summary.cell(row=i, column=1, value=label)
        ws_summary.cell(row=i, column=2, value=value)
    _bold_header(ws_summary, 1, 2)
    _auto_width(ws_summary)

    # ── Enterprises ──────────────────────────────────────────────────────────
    ws_ent = wb.create_sheet("Enterprises")
    headers_ent = [
        "Enterprise", "Field Count", "Total Hectares", "Active Alerts",
        "Critical Alerts", "Fields With Data", "Fields Without Data",
        "Average NDVI", "Latest Data Date",
    ]
    for c, h in enumerate(headers_ent, 1):
        ws_ent.cell(row=1, column=c, value=h)
    _bold_header(ws_ent, 1, len(headers_ent))

    enterprises = mgmt_data.get("enterprises", [])
    if enterprises:
        for r, ent in enumerate(enterprises, 2):
            ws_ent.cell(row=r, column=1, value=ent.get("name", ""))
            ws_ent.cell(row=r, column=2, value=ent.get("field_count", 0))
            ws_ent.cell(row=r, column=3, value=ent.get("total_hectares", 0))
            ws_ent.cell(row=r, column=4, value=ent.get("active_alerts", 0))
            ws_ent.cell(row=r, column=5, value=ent.get("critical_alerts", 0))
            ws_ent.cell(row=r, column=6, value=ent.get("fields_with_data", 0))
            ws_ent.cell(row=r, column=7, value=ent.get("fields_without_data", 0))
            ws_ent.cell(row=r, column=8, value=_safe_str(ent.get("avg_ndvi")))
            ws_ent.cell(row=r, column=9, value=_safe_str(ent.get("latest_data_date")))
    else:
        ws_ent.cell(row=2, column=1, value="No enterprise data available.")
    _auto_width(ws_ent)

    # ── Satellite Cluster ────────────────────────────────────────────────────
    ws_si = wb.create_sheet("Satellite Cluster")
    headers_si = [
        "Index Code", "Average", "Minimum", "Maximum",
        "Record Count", "Field Count", "Latest Captured Date",
    ]
    for c, h in enumerate(headers_si, 1):
        ws_si.cell(row=1, column=c, value=h)
    _bold_header(ws_si, 1, len(headers_si))

    cluster = si_data.get("cluster", {})
    for r, code in enumerate(sorted(cluster.keys()), 2):
        idx = cluster[code]
        ws_si.cell(row=r, column=1, value=code.upper())
        ws_si.cell(row=r, column=2, value=_safe_str(idx.get("avg_mean_value")))
        ws_si.cell(row=r, column=3, value=_safe_str(idx.get("min_mean_value")))
        ws_si.cell(row=r, column=4, value=_safe_str(idx.get("max_mean_value")))
        ws_si.cell(row=r, column=5, value=idx.get("record_count", 0))
        ws_si.cell(row=r, column=6, value=idx.get("field_count", 0))
        ws_si.cell(row=r, column=7, value=_safe_str(idx.get("latest_captured_date")))
    _auto_width(ws_si)

    # ── Satellite Enterprises ─────────────────────────────────────────────────
    ws_se = wb.create_sheet("Satellite Enterprises")
    headers_se = [
        "Enterprise", "SAVI Avg", "EVI Avg", "NDMI Avg", "NDRE Avg",
        "Latest Snapshot Date",
    ]
    for c, h in enumerate(headers_se, 1):
        ws_se.cell(row=1, column=c, value=h)
    _bold_header(ws_se, 1, len(headers_se))

    si_enterprises = si_data.get("enterprises", [])
    if si_enterprises:
        for r, ent in enumerate(si_enterprises, 2):
            ws_se.cell(row=r, column=1, value=ent.get("enterprise_name", ""))
            indices = ent.get("indices", {})
            ws_se.cell(row=r, column=2, value=_safe_str(indices.get("savi", {}).get("avg_mean_value")))
            ws_se.cell(row=r, column=3, value=_safe_str(indices.get("evi", {}).get("avg_mean_value")))
            ws_se.cell(row=r, column=4, value=_safe_str(indices.get("ndmi", {}).get("avg_mean_value")))
            ws_se.cell(row=r, column=5, value=_safe_str(indices.get("ndre", {}).get("avg_mean_value")))
            ws_se.cell(row=r, column=6, value=_safe_str(ent.get("latest_snapshot_date")))
    else:
        ws_se.cell(row=2, column=1, value="No enterprise satellite index data available.")
    _auto_width(ws_se)

    # ── Alerts ───────────────────────────────────────────────────────────────
    ws_alerts = wb.create_sheet("Alerts")
    alerts = mgmt_data.get("alerts", {})
    alert_summary_rows = [
        ("Total Active Alerts", alerts.get("total_active", 0)),
        ("Critical", alerts.get("critical", 0)),
        ("High / Warning", alerts.get("high", 0)),
        ("Info", alerts.get("info", 0)),
    ]
    for i, (label, value) in enumerate(alert_summary_rows, 1):
        ws_alerts.cell(row=i, column=1, value=label)
        ws_alerts.cell(row=i, column=2, value=value)
    _bold_header(ws_alerts, 1, 2)

    latest_items = alerts.get("latest_items", [])
    if latest_items:
        # blank row
        blank_row = len(alert_summary_rows) + 2
        ws_alerts.cell(row=blank_row, column=1, value="Latest Alert Items")
        ws_alerts.cell(row=blank_row, column=1).font = Font(bold=True)
        headers_alert_items = ["ID", "Title", "Severity", "Field", "Enterprise", "Triggered At"]
        hr = blank_row + 1
        for c, h in enumerate(headers_alert_items, 1):
            ws_alerts.cell(row=hr, column=c, value=h)
        _bold_header(ws_alerts, hr, len(headers_alert_items))
        for r, item in enumerate(latest_items, hr + 1):
            ws_alerts.cell(row=r, column=1, value=item.get("id", ""))
            ws_alerts.cell(row=r, column=2, value=_safe_str(item.get("title")))
            ws_alerts.cell(row=r, column=3, value=_safe_str(item.get("severity")))
            ws_alerts.cell(row=r, column=4, value=_safe_str(item.get("field_name")))
            ws_alerts.cell(row=r, column=5, value=_safe_str(item.get("enterprise_name")))
            ws_alerts.cell(row=r, column=6, value=_safe_str(item.get("triggered_at")))
    _auto_width(ws_alerts)

    # ── Limitations ──────────────────────────────────────────────────────────
    ws_lim = wb.create_sheet("Limitations")
    headers_lim = ["Limitation"]
    ws_lim.cell(row=1, column=1, value="Limitation")
    _bold_header(ws_lim, 1, 1)

    all_limitations = mgmt_data.get("limitations", []) + si_data.get("limitations", [])
    if all_limitations:
        for r, lim in enumerate(all_limitations, 2):
            ws_lim.cell(row=r, column=1, value=lim)
    else:
        ws_lim.cell(row=2, column=1, value="No limitations.")
    _auto_width(ws_lim, max_width=80)

    # ── Generate XLSX bytes ──────────────────────────────────────────────────
    try:
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        xlsx_bytes = buf.getvalue()
    except Exception as e:
        logger.error(f"Excel generation failed: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка генерации Excel управленческого отчёта: {str(e)[:200]}",
        )

    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"agrosat-management-report-{date_str}.xlsx"
    encoded = urllib.parse.quote(filename)

    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )


@router.get("/enterprise/{enterprise_id}/pdf")
def download_enterprise_pdf(
    enterprise_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Сгенерировать и вернуть PDF отчёт по предприятию с проверкой прав доступа."""

    if enterprise_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="enterprise_id must be positive",
        )

    role = str(current_user.role or "").lower()

    if role not in {"admin", "manager", "agronomist", "viewer"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для скачивания PDF-отчёта",
        )

    if role in {"agronomist", "viewer"}:
        if current_user.enterprise_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="У пользователя не указано предприятие",
            )

        if current_user.enterprise_id != enterprise_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Вы можете скачивать отчёты только для своего предприятия",
            )

    ent_row = db.execute(text("""
        SELECT id, name, code, region FROM enterprises WHERE id = :eid
    """), {"eid": enterprise_id}).fetchone()
    if not ent_row:
        raise HTTPException(status_code=404, detail="Enterprise not found")

    enterprise = {"name": ent_row.name, "code": ent_row.code, "region": ent_row.region}

    field_rows = db.execute(text("""
        SELECT
            f.id, f.name, f.code, f.area_ha,
            ct.name_ru as current_crop,
            latest.mean_ndvi as current_ndvi
        FROM fields f
        LEFT JOIN crop_seasons cs ON cs.field_id = f.id AND cs.season_year = 2026
        LEFT JOIN crop_types ct ON ct.id = cs.crop_type_id
        LEFT JOIN LATERAL (
            SELECT mean_ndvi
            FROM ndvi_records nr
            WHERE nr.field_id = f.id
            ORDER BY nr.captured_date DESC
            LIMIT 1
        ) latest ON true
        WHERE f.enterprise_id = :eid AND f.is_active = true
        ORDER BY f.name
    """), {"eid": enterprise_id}).fetchall()

    fields = [
        {
            "name": r.name, "code": r.code, "area_ha": r.area_ha,
            "current_crop": r.current_crop,
            "current_ndvi": float(r.current_ndvi) if r.current_ndvi is not None else None,
        }
        for r in field_rows
    ]

    alert_rows = db.execute(text("""
        SELECT a.title, a.severity, a.description, a.recommendation,
               f.name as field_name
        FROM alerts a
        JOIN fields f ON f.id = a.field_id
        WHERE f.enterprise_id = :eid AND a.is_active = true AND a.severity = 'critical'
        ORDER BY a.triggered_at DESC
    """), {"eid": enterprise_id}).fetchall()

    alerts = [
        {
            "title": r.title, "severity": r.severity,
            "description": r.description, "recommendation": r.recommendation,
            "field_name": r.field_name,
        }
        for r in alert_rows
    ]

    ndvis = [f["current_ndvi"] for f in fields if f["current_ndvi"] is not None]
    areas = [f["area_ha"] for f in fields if f["area_ha"] is not None]
    kpi = {
        "total_fields": len(fields),
        "avg_ndvi": (sum(ndvis) / len(ndvis)) if ndvis else None,
        "critical_count": len(alerts),
        "normal_count": sum(1 for v in ndvis if v >= 0.35),
        "total_area": sum(areas) if areas else None,
    }

    try:
        pdf_bytes = generate_enterprise_pdf(enterprise, fields, alerts, kpi)
    except Exception as e:
        logger.error(f"PDF generation failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка генерации PDF: {str(e)[:200]}")

    safe_name = enterprise["name"].replace(" ", "_")
    filename = f"AgroSat_{safe_name}.pdf"
    encoded = urllib.parse.quote(filename)

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"}
    )
