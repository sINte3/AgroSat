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
