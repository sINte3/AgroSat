"""
Генерация PDF отчётов по предприятию.
Использует reportlab со шрифтом Arial (Windows) для поддержки кириллицы.

Положить в: backend/services/pdf_report.py
"""

import os
import io
import logging
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logger = logging.getLogger(__name__)

# Windows Arial — поддерживает кириллицу, всегда присутствует на Windows
ARIAL_REGULAR = r"C:\Windows\Fonts\arial.ttf"
ARIAL_BOLD = r"C:\Windows\Fonts\arialbd.ttf"

_FONTS_REGISTERED = False


def _register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    pdfmetrics.registerFont(TTFont("AppFont", ARIAL_REGULAR))
    if os.path.exists(ARIAL_BOLD):
        pdfmetrics.registerFont(TTFont("AppFont-Bold", ARIAL_BOLD))
    else:
        pdfmetrics.registerFont(TTFont("AppFont-Bold", ARIAL_REGULAR))
    _FONTS_REGISTERED = True


def _ndvi_color(v):
    """Цвет фона для значения NDVI."""
    if v is None:
        return colors.HexColor("#cccccc")
    if v < 0.2:
        return colors.HexColor("#c0392b")
    if v < 0.35:
        return colors.HexColor("#e67e22")
    if v < 0.5:
        return colors.HexColor("#f1c40f")
    if v < 0.65:
        return colors.HexColor("#7fb800")
    return colors.HexColor("#27ae60")


def generate_enterprise_pdf(enterprise: dict, fields: list, alerts: list, kpi: dict) -> bytes:
    """
    Создать PDF отчёт по предприятию.

    enterprise: {name, code, region}
    fields: list of {name, code, current_crop, area_ha, current_ndvi}
    alerts: list of {title, severity, field_name, description, recommendation}
    kpi: {total_fields, avg_ndvi, critical_count, total_area, normal_count}

    Returns: PDF bytes
    """
    _register_fonts()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        title=f"Отчёт — {enterprise.get('name', '')}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleRu", parent=styles["Title"],
        fontName="AppFont-Bold", fontSize=22, textColor=colors.HexColor("#1a5d1a"),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "SubtitleRu", parent=styles["Normal"],
        fontName="AppFont", fontSize=11, textColor=colors.HexColor("#666666"),
        spaceAfter=4,
    )
    h2_style = ParagraphStyle(
        "H2Ru", parent=styles["Heading2"],
        fontName="AppFont-Bold", fontSize=15, textColor=colors.HexColor("#1a5d1a"),
        spaceBefore=14, spaceAfter=8,
    )
    body_style = ParagraphStyle(
        "BodyRu", parent=styles["Normal"],
        fontName="AppFont", fontSize=10, leading=14,
    )
    small_style = ParagraphStyle(
        "SmallRu", parent=styles["Normal"],
        fontName="AppFont", fontSize=9, leading=12, textColor=colors.HexColor("#444444"),
    )

    story = []

    # ─── Header ───
    story.append(Paragraph("AgroSat — Отчёт по предприятию", subtitle_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(enterprise.get("name", "—"), title_style))
    meta = f"Код: {enterprise.get('code', '—')}  •  Регион: {enterprise.get('region', '—')}"
    story.append(Paragraph(meta, subtitle_style))
    story.append(Paragraph(
        f"Дата формирования: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        subtitle_style
    ))
    story.append(Spacer(1, 10))

    # ─── KPI Summary ───
    story.append(Paragraph("Сводка", h2_style))
    kpi_data = [
        ["Всего полей", str(kpi.get("total_fields", "—"))],
        ["Средний NDVI", f"{kpi.get('avg_ndvi'):.3f}" if kpi.get("avg_ndvi") is not None else "—"],
        ["Критических алертов", str(kpi.get("critical_count", "—"))],
        ["Полей в норме", str(kpi.get("normal_count", "—"))],
        ["Общая площадь, га", f"{kpi.get('total_area'):.1f}" if kpi.get("total_area") is not None else "—"],
    ]
    kpi_table = Table(kpi_data, colWidths=[80 * mm, 40 * mm])
    kpi_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "AppFont"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555555")),
        ("FONTNAME", (1, 0), (1, -1), "AppFont-Bold"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#f5f9f5"), colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(kpi_table)

    # ─── Fields Table ───
    story.append(Paragraph(f"Поля предприятия ({len(fields)})", h2_style))

    table_data = [["Контур", "Культура", "Площадь, га", "NDVI"]]
    sorted_fields = sorted(
        fields,
        key=lambda f: f.get("current_ndvi") if f.get("current_ndvi") is not None else 999
    )
    for f in sorted_fields:
        ndvi_val = f.get("current_ndvi")
        table_data.append([
            Paragraph(str(f.get("name", "—")), small_style),
            Paragraph(str(f.get("current_crop", "—") or "—"), small_style),
            f"{f.get('area_ha'):.1f}" if f.get("area_ha") is not None else "—",
            f"{ndvi_val:.3f}" if ndvi_val is not None else "—",
        ])

    fields_table = Table(
        table_data,
        colWidths=[70 * mm, 50 * mm, 30 * mm, 30 * mm],
        repeatRows=1,
    )
    table_style_cmds = [
        ("FONTNAME", (0, 0), (-1, -1), "AppFont"),
        ("FONTNAME", (0, 0), (-1, 0), "AppFont-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a5d1a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (2, 0), (3, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i, f in enumerate(sorted_fields, start=1):
        table_style_cmds.append(
            ("BACKGROUND", (3, i), (3, i), _ndvi_color(f.get("current_ndvi")))
        )
        table_style_cmds.append(
            ("TEXTCOLOR", (3, i), (3, i), colors.white)
        )
    fields_table.setStyle(TableStyle(table_style_cmds))
    story.append(fields_table)

    # ─── Critical Alerts ───
    critical = [a for a in alerts if a.get("severity") == "critical"]
    if critical:
        story.append(PageBreak())
        story.append(Paragraph(f"Критические алерты ({len(critical)})", h2_style))
        for a in critical:
            story.append(Paragraph(
                f"<b>{a.get('title', 'Алерт')}</b> — {a.get('field_name', '')}",
                body_style
            ))
            if a.get("description"):
                story.append(Paragraph(a["description"], small_style))
            if a.get("recommendation"):
                story.append(Paragraph(f"<i>Рекомендация: {a['recommendation']}</i>", small_style))
            story.append(Spacer(1, 8))

    # ─── Footer ───
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "Отчёт сформирован автоматически системой AgroSat на основе данных Sentinel-2.",
        small_style
    ))

    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes


# ─── Management report PDF ───────────────────────────────────────────────────


def _mgmt_val(v, fmt="str"):
    """Return value or em-dash. fmt: 'str', 'float1', 'float3', 'int'."""
    if v is None:
        return "—"
    if fmt == "float1":
        return f"{float(v):.1f}"
    if fmt == "float3":
        return f"{float(v):.3f}"
    if fmt == "int":
        return str(int(v))
    return str(v)


def _mgmt_header_style(base_size=20):
    return ParagraphStyle(
        "MgmtTitle", fontName="AppFont-Bold", fontSize=base_size,
        textColor=colors.HexColor("#1a5d1a"), spaceAfter=4,
    )


def _mgmt_sub_style(base_size=11):
    return ParagraphStyle(
        "MgmtSub", fontName="AppFont", fontSize=base_size,
        textColor=colors.HexColor("#555555"), spaceAfter=2,
    )


def _mgmt_h2_style(base_size=14):
    return ParagraphStyle(
        "MgmtH2", fontName="AppFont-Bold", fontSize=base_size,
        textColor=colors.HexColor("#1a5d1a"), spaceBefore=14, spaceAfter=6,
    )


def _mgmt_body_style(base_size=10):
    return ParagraphStyle(
        "MgmtBody", fontName="AppFont", fontSize=base_size, leading=base_size + 4,
    )


def _mgmt_small_style(base_size=9):
    return ParagraphStyle(
        "MgmtSmall", fontName="AppFont", fontSize=base_size, leading=base_size + 3,
        textColor=colors.HexColor("#444444"),
    )


def _mgmt_table_style(header_bg=colors.HexColor("#1a5d1a")):
    return TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "AppFont"),
        ("FONTNAME", (0, 0), (-1, 0), "AppFont-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.HexColor("#f9f9f9"), colors.white]),
    ])


def _make_simple_table(data, col_widths, align_cols=None):
    """Build a styled Table from list-of-lists."""
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(_mgmt_table_style())
    if align_cols:
        for c in align_cols:
            tbl.setStyle(TableStyle([("ALIGN", (c, 0), (c, -1), "CENTER")]))
    return tbl


def _limitations_block(limitations, title, body_style, small_style):
    """Return story elements for a limitations subsection."""
    els = []
    if limitations:
        els.append(Paragraph(title, body_style))
        for lim in limitations:
            els.append(Paragraph(f"• {lim}", small_style))
        els.append(Spacer(1, 6))
    return els


def generate_management_report_pdf(
    *,
    generated_at: str,
    date_range: dict,
    summary: dict,
    enterprises: list,
    alerts: dict,
    data_freshness: dict,
    limitations_summary: list,
    satellite_cluster: dict,
    satellite_enterprises: list,
    satellite_limitations: list,
) -> bytes:
    """Generate a management-level PDF report covering all sections."""
    _register_fonts()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        title="AgroSat — Управленческий отчёт",
    )

    ts = _mgmt_header_style()
    ss = _mgmt_sub_style()
    h2 = _mgmt_h2_style()
    body = _mgmt_body_style()
    small = _mgmt_small_style()

    story = []

    # ── 1. Title / header ───────────────────────────────────────────────────
    story.append(Paragraph("AgroSat", ts))
    story.append(Paragraph("Управленческий отчёт по состоянию полей", ss))
    story.append(Paragraph(
        f"Дата формирования: {generated_at[:19].replace('T', ' ')}", ss
    ))
    dr_from = date_range.get("from", "")
    dr_to = date_range.get("to", "")
    if dr_from and dr_to:
        story.append(Paragraph(
            f"Диапазон: {dr_from[:10]} — {dr_to[:10]}", ss
        ))
    story.append(Spacer(1, 10))

    # ── 2. Summary section ──────────────────────────────────────────────────
    story.append(Paragraph("Сводка", h2))
    sum_fields = [
        ("Всего полей", _mgmt_val(summary.get("total_fields"), "int")),
        ("Общая площадь, га", _mgmt_val(summary.get("total_hectares"), "float1")),
        ("Полей с данными", _mgmt_val(summary.get("fields_with_data"), "int")),
        ("Полей без данных", _mgmt_val(summary.get("fields_without_data"), "int")),
        ("Средний NDVI", _mgmt_val(summary.get("avg_ndvi"), "float3")),
        ("Активных алертов", _mgmt_val(summary.get("active_alerts"), "int")),
    ]
    story.append(_make_simple_table(
        sum_fields,
        col_widths=[70 * mm, 40 * mm],
        align_cols=[1],
    ))

    # ── 3. Enterprise comparison ────────────────────────────────────────────
    story.append(Paragraph("Сравнение предприятий", h2))
    if enterprises:
        ent_header = [
            "Предприятие", "Полей", "Площадь, га",
            "Алерты", "Проблемные поля", "Без данных", "Ср. NDVI", "Дата"
        ]
        ent_data = [ent_header]
        for e in enterprises:
            ent_data.append([
                Paragraph(str(e.get("name", "—")), small),
                _mgmt_val(e.get("field_count"), "int"),
                _mgmt_val(e.get("total_hectares"), "float1"),
                _mgmt_val(e.get("active_alerts"), "int"),
                _mgmt_val(e.get("problem_fields"), "int"),
                _mgmt_val(e.get("fields_without_data"), "int"),
                _mgmt_val(e.get("avg_ndvi"), "float3"),
                str(e.get("latest_data_date", "—") or "—"),
            ])
        cols = [55 * mm, 14 * mm, 20 * mm, 14 * mm, 22 * mm, 18 * mm, 16 * mm, 28 * mm]
        story.append(_make_simple_table(ent_data, col_widths=cols, align_cols=[1, 2, 3, 4, 5, 6]))
    else:
        story.append(Paragraph("Нет данных по предприятиям.", small))

    # ── 4. Alerts section ────────────────────────────────────────────────────
    story.append(Paragraph("Алерты", h2))
    total_active = alerts.get("total_active", 0)
    story.append(Paragraph(
        f"Всего активных алертов: {total_active}", body
    ))
    severity_breakdown = []
    for sev in ("critical", "high", "info"):
        cnt = alerts.get(sev, 0)
        if cnt:
            severity_breakdown.append(f"{sev}: {cnt}")
    if severity_breakdown:
        story.append(Paragraph(
            "По уровням: " + ", ".join(severity_breakdown), small
        ))

    latest_items = alerts.get("latest_items", [])
    if latest_items:
        story.append(Paragraph("Последние алерты:", body))
        alert_header = ["Дата", "Название", "Уровень", "Поле", "Предприятие"]
        alert_data = [alert_header]
        for item in latest_items[:10]:
            alert_data.append([
                (str(item.get("triggered_at", ""))[:16].replace("T", " ") or "—"),
                Paragraph(str(item.get("title", "—") or "—"), small),
                str(item.get("severity", "—") or "—"),
                Paragraph(str(item.get("field_name", "—") or "—"), small),
                Paragraph(str(item.get("enterprise_name", "—") or "—"), small),
            ])
        cols = [32 * mm, 40 * mm, 18 * mm, 35 * mm, 40 * mm]
        story.append(_make_simple_table(alert_data, col_widths=cols, align_cols=[2]))

    # ── 5. Data freshness ────────────────────────────────────────────────────
    story.append(Paragraph("Свежесть данных", h2))
    fresh_items = [
        ("Последний NDVI", _mgmt_val(data_freshness.get("latest_ndvi_date"))),
        ("Последний спутниковый индекс",
         _mgmt_val(data_freshness.get("latest_satellite_index_date"))),
        ("Полей без данных", _mgmt_val(data_freshness.get("fields_without_data"), "int")),
    ]
    if data_freshness.get("note"):
        fresh_items.append(("Примечание", str(data_freshness["note"])))
    story.append(_make_simple_table(
        fresh_items, col_widths=[55 * mm, 55 * mm],
    ))

    # ── 6. Satellite indices ────────────────────────────────────────────────
    story.append(Paragraph("Спутниковые индексы", h2))

    si_header = [
        "Индекс", "Среднее", "Мин.", "Макс.",
        "Записей", "Полей", "Последняя дата"
    ]
    si_data = [si_header]
    for code in ("savi", "evi", "ndmi", "ndre"):
        ci = satellite_cluster.get(code, {})
        si_data.append([
            code.upper(),
            _mgmt_val(ci.get("avg_mean_value"), "float3"),
            _mgmt_val(ci.get("min_mean_value"), "float3"),
            _mgmt_val(ci.get("max_mean_value"), "float3"),
            _mgmt_val(ci.get("record_count"), "int"),
            _mgmt_val(ci.get("field_count"), "int"),
            str(ci.get("latest_captured_date", "—") or "—"),
        ])
    cols = [20 * mm, 20 * mm, 20 * mm, 20 * mm, 18 * mm, 16 * mm, 32 * mm]
    story.append(_make_simple_table(si_data, col_widths=cols, align_cols=[1, 2, 3, 4, 5]))

    # Enterprise-level satellite indices
    if satellite_enterprises:
        story.append(Spacer(1, 6))
        story.append(Paragraph("По предприятиям:", body))
        ent_si_header = [
            "Предприятие", "SAVI", "EVI", "NDMI", "NDRE", "Дата"
        ]
        ent_si_data = [ent_si_header]
        for se in satellite_enterprises:
            indices = se.get("indices", {})
            ent_si_data.append([
                Paragraph(str(se.get("enterprise_name", "—")), small),
                _mgmt_val(indices.get("savi", {}).get("avg_mean_value"), "float3"),
                _mgmt_val(indices.get("evi", {}).get("avg_mean_value"), "float3"),
                _mgmt_val(indices.get("ndmi", {}).get("avg_mean_value"), "float3"),
                _mgmt_val(indices.get("ndre", {}).get("avg_mean_value"), "float3"),
                str(se.get("latest_snapshot_date", "—") or "—"),
            ])
        cols = [40 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 28 * mm]
        story.append(_make_simple_table(ent_si_data, col_widths=cols, align_cols=[1, 2, 3, 4]))

    # ── 7. Limitations ──────────────────────────────────────────────────────
    story.append(Paragraph("Ограничения", h2))
    story.extend(_limitations_block(
        limitations_summary, "Ограничения основной сводки:", body, small
    ))
    story.extend(_limitations_block(
        satellite_limitations, "Ограничения агрегации спутниковых индексов:", body, small
    ))
    if not limitations_summary and not satellite_limitations:
        story.append(Paragraph("Нет ограничений.", small))

    # ── 8. Footer ───────────────────────────────────────────────────────────
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "Сформировано автоматически системой AgroSat.",
        ParagraphStyle(
            "MgmtFooter", parent=small,
            textColor=colors.HexColor("#888888"),
            fontSize=8, alignment=1,  # center
        )
    ))

    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes
