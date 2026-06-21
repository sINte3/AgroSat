# TASK_018 — PDF report generation per enterprise (server-side, Cyrillic)

## Skills to load
- `/mnt/skills/user/full-output-enforcement/SKILL.md`
- Read `/mnt/skills/public/pdf/SKILL.md` for reportlab patterns

## Goal

Replace the current `window.print()` "Скачать отчёт" button with a proper server-generated PDF.
The PDF must support Cyrillic text and contain: cover, KPI summary, fields table, and critical alerts list.

---

## CRITICAL: Cyrillic font support

ReportLab's built-in fonts (Helvetica) DO NOT render Cyrillic — Russian text will show as black boxes.
You MUST register a TrueType font that supports Cyrillic. Use **DejaVu Sans**.

### Step 0: Install reportlab and get the font

```bash
cd C:\AgroSat\backend
pip install reportlab
```

DejaVu Sans ships with matplotlib. If matplotlib is installed, find it. Otherwise download it.
Create `backend/scripts/get_font.py`:

```python
"""Download DejaVu Sans font for PDF Cyrillic support."""
import os
import urllib.request

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts")
os.makedirs(FONT_DIR, exist_ok=True)

fonts = {
    "DejaVuSans.ttf": "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans.ttf",
    "DejaVuSans-Bold.ttf": "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf",
}

for name, url in fonts.items():
    dest = os.path.join(FONT_DIR, name)
    if os.path.exists(dest):
        print(f"{name} already exists")
        continue
    print(f"Downloading {name}...")
    urllib.request.urlretrieve(url, dest)
    print(f"Saved to {dest}")

print("Done. Fonts in:", FONT_DIR)
```

Run it:
```bash
python scripts/get_font.py
```

This creates `C:\AgroSat\backend\fonts\DejaVuSans.ttf` and `DejaVuSans-Bold.ttf`.

---

## Step 1: Create `backend/services/pdf_report.py`

```python
"""
Генерация PDF отчётов по предприятию.
Использует reportlab с шрифтом DejaVu Sans для поддержки кириллицы.
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

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts")

# Register Cyrillic fonts once at import
_FONTS_REGISTERED = False

def _register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    regular = os.path.join(FONT_DIR, "DejaVuSans.ttf")
    bold = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
    pdfmetrics.registerFont(TTFont("DejaVu", regular))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", bold))
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

    # Styles with Cyrillic font
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleRu", parent=styles["Title"],
        fontName="DejaVu-Bold", fontSize=22, textColor=colors.HexColor("#1a5d1a"),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "SubtitleRu", parent=styles["Normal"],
        fontName="DejaVu", fontSize=11, textColor=colors.HexColor("#666666"),
        spaceAfter=4,
    )
    h2_style = ParagraphStyle(
        "H2Ru", parent=styles["Heading2"],
        fontName="DejaVu-Bold", fontSize=15, textColor=colors.HexColor("#1a5d1a"),
        spaceBefore=14, spaceAfter=8,
    )
    body_style = ParagraphStyle(
        "BodyRu", parent=styles["Normal"],
        fontName="DejaVu", fontSize=10, leading=14,
    )
    small_style = ParagraphStyle(
        "SmallRu", parent=styles["Normal"],
        fontName="DejaVu", fontSize=9, leading=12, textColor=colors.HexColor("#444444"),
    )

    story = []

    # ─── Cover / Header ───
    story.append(Paragraph("🛰 AgroSat — Отчёт по предприятию", subtitle_style))
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
        ("FONTNAME", (0, 0), (-1, -1), "DejaVu"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555555")),
        ("FONTNAME", (1, 0), (1, -1), "DejaVu-Bold"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#f5f9f5"), colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(kpi_table)

    # ─── Fields Table ───
    story.append(Paragraph(f"Поля предприятия ({len(fields)})", h2_style))

    # Header row
    table_data = [["Контур", "Культура", "Площадь, га", "NDVI"]]
    # Sort by NDVI ascending (problematic first)
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
        ("FONTNAME", (0, 0), (-1, -1), "DejaVu"),
        ("FONTNAME", (0, 0), (-1, 0), "DejaVu-Bold"),
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
    # Color NDVI cells
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
                f"🔴 <b>{a.get('title', 'Алерт')}</b> — {a.get('field_name', '')}",
                body_style
            ))
            if a.get("description"):
                story.append(Paragraph(a["description"], small_style))
            if a.get("recommendation"):
                story.append(Paragraph(f"<i>Рекомендация: {a['recommendation']}</i>", small_style))
            story.append(Spacer(1, 8))

    # ─── Footer note ───
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "Отчёт сформирован автоматически системой AgroSat на основе данных Sentinel-2.",
        small_style
    ))

    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes
```

---

## Step 2: Add backend endpoint in `backend/api/dashboard.py` (or create `backend/api/reports.py`)

Create `backend/api/reports.py`:

```python
"""
API для генерации PDF отчётов.

Эндпоинт:
    GET /api/reports/enterprise/{enterprise_id}/pdf — скачать PDF отчёт
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from services.pdf_report import generate_enterprise_pdf
import io
import urllib.parse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/enterprise/{enterprise_id}/pdf")
def download_enterprise_pdf(enterprise_id: int, db: Session = Depends(get_db)):
    """Сгенерировать и вернуть PDF отчёт по предприятию."""

    # Enterprise
    ent_row = db.execute(text("""
        SELECT id, name, code, region FROM enterprises WHERE id = :eid
    """), {"eid": enterprise_id}).fetchone()
    if not ent_row:
        raise HTTPException(status_code=404, detail="Enterprise not found")

    enterprise = {"name": ent_row.name, "code": ent_row.code, "region": ent_row.region}

    # Fields with latest NDVI + current crop (one optimized query)
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

    # Alerts (critical only, with field name)
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

    # KPI
    ndvis = [f["current_ndvi"] for f in fields if f["current_ndvi"] is not None]
    areas = [f["area_ha"] for f in fields if f["area_ha"] is not None]
    kpi = {
        "total_fields": len(fields),
        "avg_ndvi": (sum(ndvis) / len(ndvis)) if ndvis else None,
        "critical_count": len(alerts),
        "normal_count": sum(1 for v in ndvis if v >= 0.35),
        "total_area": sum(areas) if areas else None,
    }

    # Generate PDF
    try:
        pdf_bytes = generate_enterprise_pdf(enterprise, fields, alerts, kpi)
    except Exception as e:
        logger.error(f"PDF generation failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка генерации PDF: {str(e)[:200]}")

    # Filename (URL-encoded for Cyrillic)
    safe_name = enterprise["name"].replace(" ", "_")
    filename = f"AgroSat_{safe_name}.pdf"
    encoded = urllib.parse.quote(filename)

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"
        }
    )
```

---

## Step 3: Register router in `backend/main.py`

```python
from api.reports import router as reports_router
app.include_router(reports_router)
```

---

## Step 4: Frontend — wire up the "Скачать отчёт" button

In `frontend/src/pages/EnterpriseDetailPage.jsx`, change the "Скачать отчёт" button.
Currently it likely calls `window.print()`. Replace its onClick with a PDF download:

```jsx
<button
  onClick={() => {
    // Open the PDF endpoint in a new tab — browser downloads it
    const url = `/api/reports/enterprise/${enterpriseId}/pdf`;
    window.open(url, '_blank');
  }}
  style={{
    background: '#1a2818',
    border: '1px solid #2d4a2d',
    borderRadius: 8,
    padding: '8px 14px',
    color: '#4ade80',
    cursor: 'pointer',
    fontSize: 12,
    whiteSpace: 'nowrap',
  }}
>
  📄 Скачать отчёт PDF
</button>
```

NOTE: the PDF endpoint is on the backend (port 8000) but Vite proxies `/api/` so `window.open('/api/reports/...')` works through the proxy. If it doesn't download through proxy, use the full backend URL: `window.open('http://localhost:8000/api/reports/enterprise/' + enterpriseId + '/pdf', '_blank')`.

---

## Checklist
- [ ] `pip install reportlab` done
- [ ] `backend/scripts/get_font.py` created and run → fonts/ folder has DejaVuSans.ttf + Bold
- [ ] `backend/services/pdf_report.py` created
- [ ] `backend/api/reports.py` created
- [ ] Router registered in `main.py`
- [ ] Frontend "Скачать отчёт" button opens PDF endpoint
- [ ] Test: click button → PDF downloads with readable Cyrillic text

## Testing
After restart, open an enterprise → click "📄 Скачать отчёт PDF".
A PDF should download containing:
- Cover with enterprise name (Cyrillic readable, no black boxes!)
- KPI summary table
- Fields table sorted by NDVI with colored NDVI cells
- Critical alerts list on page 2

If Cyrillic shows as black boxes → the font wasn't registered; verify fonts/ folder exists and get_font.py ran successfully.

## Important
- Cyrillic font (DejaVu Sans) is MANDATORY — without it Russian text breaks
- Use LATERAL join for latest NDVI (efficient, one query)
- All text in Russian
- No Docker
