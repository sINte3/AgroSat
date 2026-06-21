# TASK_018B — PDF report generation (local Windows Arial font, no internet)

## Skills to load
- `/mnt/skills/user/full-output-enforcement/SKILL.md`
- Read `/mnt/skills/public/pdf/SKILL.md` for reportlab patterns

## Context

Previous TASK_018 failed — the backend PDF files were never created (likely the font download
from the internet failed and aborted the whole task). This version uses the LOCAL Windows
Arial font which is already present at `C:\Windows\Fonts\arial.ttf` and supports Cyrillic.
NO internet download needed.

DO NOT create `scripts/get_font.py` and DO NOT download any fonts. Use the Windows Arial directly.

---

## Step 1: Install reportlab

```bash
cd C:\AgroSat\backend
venv\Scripts\activate
pip install reportlab
```

---

## Step 2: Create `backend/services/pdf_report.py`

Create this EXACT file:

```python
"""
Генерация PDF отчётов по предприятию.
Использует reportlab со шрифтом Arial (Windows) для поддержки кириллицы.
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

# Windows Arial — supports Cyrillic, always present on Windows
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
        # fallback: use regular as bold
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
```

---

## Step 3: Create `backend/api/reports.py`

Create this EXACT file:

```python
"""
API для генерации PDF отчётов.

Эндпоинт:
    GET /api/reports/enterprise/{enterprise_id}/pdf — скачать PDF отчёт
"""

import logging
import io
import urllib.parse
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from services.pdf_report import generate_enterprise_pdf

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/enterprise/{enterprise_id}/pdf")
def download_enterprise_pdf(enterprise_id: int, db: Session = Depends(get_db)):
    """Сгенерировать и вернуть PDF отчёт по предприятию."""

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
```

---

## Step 4: Register router in `backend/main.py`

Find where other routers are included (look for lines like `app.include_router(...)`) and add:

```python
from api.reports import router as reports_router
app.include_router(reports_router)
```

IMPORTANT: verify this line is actually added — the previous task failed to do this.

---

## Step 5: Frontend — change the "Скачать отчёт" button

In `frontend/src/pages/EnterpriseDetailPage.jsx`:

Find the button with text "📄 Скачать отчёт" — it currently has `onClick={handlePrintReport}`.
Change its onClick to open the PDF endpoint directly:

```jsx
<button onClick={() => {
  window.open(`http://localhost:8000/api/reports/enterprise/${enterpriseId}/pdf`, '_blank');
}} style={{
  background: '#1a2818',
  border: '1px solid #2d4a2d',
  borderRadius: 8,
  padding: '8px 14px',
  color: '#4ade80',
  cursor: 'pointer',
  fontSize: 12,
  whiteSpace: 'nowrap',
  transition: 'all 0.15s',
}}
  onMouseEnter={e => { e.currentTarget.style.background = '#2d4a2d'; }}
  onMouseLeave={e => { e.currentTarget.style.background = '#1a2818'; }}
>
  📄 Скачать отчёт PDF
</button>
```

You can leave the old `handlePrintReport` function and `EnterpriseReport` import in place (unused is fine),
or remove them. The key change is the button's onClick now opens the backend PDF URL.

---

## Step 6: VERIFY the backend files exist after creation

Run these commands and confirm all three exist:
```bash
dir api\reports.py
dir services\pdf_report.py
findstr /n "reports" main.py
```

All three must return results. If any is missing, the task is NOT complete.

---

## Step 7: Test PDF generation directly (before frontend)

Restart backend, then test the endpoint with a quick Python script.
Create `backend/test_pdf.py`:

```python
import requests
r = requests.get("http://localhost:8000/api/reports/enterprise/2/pdf")
print("Status:", r.status_code)
print("Content-Type:", r.headers.get("content-type"))
if r.status_code == 200:
    with open("test_output.pdf", "wb") as f:
        f.write(r.content)
    print("Saved test_output.pdf, size:", len(r.content), "bytes")
else:
    print("Error:", r.text)
```

Run: `python test_pdf.py` (try enterprise id 2 or whichever has fields).
If it saves a PDF with size > 5000 bytes → success. Open test_output.pdf and verify Cyrillic is readable.

---

## Checklist
- [ ] `pip install reportlab` done
- [ ] `backend/services/pdf_report.py` created (uses C:\Windows\Fonts\arial.ttf — NO download)
- [ ] `backend/api/reports.py` created
- [ ] `main.py` — `from api.reports import router as reports_router` + `app.include_router(reports_router)` added
- [ ] Verified all 3 files exist (Step 6)
- [ ] Frontend button onClick changed to open PDF URL
- [ ] test_pdf.py confirms PDF downloads with readable Cyrillic

## Important
- Use LOCAL Windows Arial font — DO NOT download anything from the internet
- DO NOT create get_font.py, DO NOT create a fonts/ folder
- Verify main.py actually got the router line (previous attempt missed this)
- All text in Russian
