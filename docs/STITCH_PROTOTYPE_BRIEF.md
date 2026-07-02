# AgroSat Google Stitch Prototype Brief

> Product/design handoff document for generating visual prototypes in Google Stitch.
> Guides future frontend implementation tasks. Not production code.

---

## 1. Purpose

This document is a brief for generating visual prototypes in **Google Stitch**.

### Critical disclaimers

- **Stitch is for visual exploration only.** Prototypes generated from these prompts are design mocks to validate layout, information hierarchy, and visual direction before development begins.
- **Stitch-generated code must not be copied directly into production.** Generated HTML, CSS, or JavaScript is not production-quality, does not respect existing API contracts, React component architecture, MapLibre integration, or database constraints.
- **Production implementation must be done manually in AgroSat frontend tasks**, using the existing React / Tailwind stack, documented design system (`docs/FRONTEND_DESIGN_SYSTEM.md`), and satellite data quality rules (`docs/SATELLITE_INDEX_DATA_QUALITY_RULES.md`).
- This brief guides future tasks, not runtime behavior.

---

## 2. Product summary

**AgroSat** is an industrial satellite field monitoring platform for Bukhoro Agrocluster, an agricultural holding in Uzbekistan.

### Target users

| User type | Role |
|-----------|------|
| **Agronomists** | Daily field monitoring, vegetation index inspection, alert triage |
| **Enterprise managers** | Cross-farm health overview, resource allocation decisions |
| **Holding management** | Strategic oversight, enterprise comparison, reporting |
| **GIS / operator users** | Map workspace navigation, field geometry, satellite data layers |

### Core use case

1. Find fields needing attention — browse the map workspace or dashboard.
2. Inspect vegetation / moisture / index signals — NDVI, SAVI, EVI, NDMI, NDRE.
3. Review alerts — operational work queue with severity and status.
4. Compare enterprises / farms — side-by-side health metrics.
5. Prepare management decisions — reports and data exports.

---

## 3. Visual direction

### Desired look

| Attribute | Target |
|-----------|--------|
| Primary paradigm | **map-first** — the map is the primary work surface |
| Grade | Enterprise-grade, professional GIS/agronomy interface |
| Dashboard style | Clean agronomic dashboard, not generic SaaS template |
| Information density | High density without visual chaos |
| Inspiration | **OneSoil** clarity, **True Fields** analytical depth |
| Palette | Green / dark neutral professional palette |
| Typography | Readable Russian text, minimum 14px body |
| Icons | Simple, functional line icons — no decoration |

### What this is not

- Not a consumer toy UI
- Not a generic SaaS template
- Not a cartoon / farm illustration style
- Not a decorative dashboard with no operational value

---

## 4. Global layout prompt for Stitch

> Copy-paste this prompt into Google Stitch to generate the AgroSat application shell.

```
Generate a Russian-language enterprise agricultural monitoring application shell.

Layout:
- Left sidebar with icon-only navigation (48px wide, dark green/charcoal background)
- Top header bar with page title, enterprise selector, and user avatar (48px height, matching dark palette)
- Main content area filling the remaining space, with a map/workspace surface as the background
- Optional collapsible right panel for field detail

Color palette:
- Primary: dark green (#1a3a2a range)
- Accent: medium green (#16a34a range)
- Backgrounds: dark neutral (#1e1e2e, #111827)
- Cards: slightly lighter surface (#1f2937)
- Text: white/light gray (#e2e8f0, #94a3b8)
- Danger: red (#dc2626)
- Warning: amber (#f59e0b)
- Info: blue (#3b82f6)

Navigation items in the left sidebar (Russian labels):
- Главная
- Карта полей
- Предупреждения
- Предприятия
- Отчёты
- Настройки

Active navigation item should have an accent green left border or highlight.

Styling requirements:
- Responsive desktop-first (minimum 1280px width)
- All cards and tables must be readable — no text smaller than 11px
- Font: system UI font stack (Segoe UI, system-ui, sans-serif)
- No decorative gimmicks, no farm illustrations, no cartoons
- Professional enterprise GIS appearance
- High information density without clutter
```

---

## 5. Dashboard prototype prompt

> Copy-paste this prompt into Google Stitch to generate the management dashboard.

```
Generate a Russian-language agricultural management dashboard page.

Title: "Главная" (Dashboard)

Top stat bar with 6 cards in a row:
1. "Всего полей" — 1 247 (large number, white text)
2. "Общая площадь" — 84 320 га
3. "Здоровые поля" — 892 (green accent, with small up-arrow icon)
4. "Поля под риском" — 218 (amber accent, with small warning icon)
5. "Полей без данных" — 137 (gray/dim)
6. "Свежесть данных" — "12.06.2026" (latest satellite pass date with calendar icon)

Below the stat bar, two-column layout:

Left column (60%):
- "Проблемные поля" — table with columns: Поле, Предприятие, NDVI (color-coded value), Статус (risk badge), Последние данные. Show 5 rows max. Badge colors: green for healthy, amber for risk, red for critical, gray for no-data.

Right column (40%):
- "Сводка предупреждений" — card showing Критично: 14 (red), Высокий риск: 31 (amber), Инфо: 7 (blue). With a "Перейти к предупреждениям" link button.

Below the two-column section:
- "Сравнение предприятий" — horizontal bar chart or small-multiple cards, showing 4-5 enterprises with healthy/risk/no-data breakdown per enterprise.

Small map preview / risk heatmap thumbnail in the bottom-right corner.

Styling: dark neutral background (#1e1e2e), cards (#1f2937) with subtle border, readable Russian text, compact layout, no decorative elements.
```

---

## 6. Map workspace prototype prompt

> Copy-paste this prompt into Google Stitch to generate the map workspace.
> Aligns with TASK_106 layout (left field list + central map + right detail panel).

```
Generate a Russian-language agricultural map workspace page.

Title: "Карта полей" (Fields Map)

Three-panel layout:

Left panel (320px, collapsible, dark background with scroll):
- Header: "Список полей" with search input and enterprise filter dropdown
- Field list items, each showing:
  - Field name (e.g., "Поле №124")
  - Enterprise / farm name (small, muted)
  - Status badge: "Здоровое" (green), "Риск" (amber), "Критично" (red), "Нет данных" (gray)
  - Latest NDVI value (small, with color indicator dot)
  - Selected field gets a highlighted background

Central map area (fills remaining space):
- Styled interactive map with field polygon outlines (green borders, semi-transparent fills)
- Selected field highlighted with bright green border (3px) and distinct fill
- Index type selector: row of pill buttons showing NDVI, SAVI, EVI, NDMI, NDRE
- Date selector: date input or dropdown showing latest available date
- Map legend: color scale gradient bar with min/max values for the active index
- No actual satellite raster imagery — visual placeholder showing field polygons with index-based coloring

Right panel (380px, collapsible, dark background with scroll):
- Field detail content (see separate prompt below)
- Slides in when a field is selected, shows empty state with "Выберите поле на карте" when nothing selected

Map controls (top-right, semi-transparent):
- Zoom in/out buttons
- Style toggle (Satellite / Streets / Dark)

Styling: dark professional theme, map occupies full workspace background, panels overlay with slight transparency/backdrop blur.
```

---

## 7. Field detail panel prototype prompt

> Copy-paste this prompt into Google Stitch to generate the selected field detail panel.
> This panel appears on the right side of the map workspace.

```
Generate a Russian-language field detail panel for an agricultural monitoring platform.

Panel title: field name (e.g., "Поле №124 — Северное")
Subtitle line: enterprise/farm name (muted)

Information grid (two-column compact layout):
- Культура: Пшеница (Crop: Wheat)
- Площадь: 86.4 га (Area)
- Последние данные спутника: 12.06.2026 (Latest satellite date)
- Статус: badge — "Здоровое" (green) or "Риск" (amber) or "Критично" (red) or "Нет данных" (gray)

Index cards section (5 cards, displayed in a 2+3 grid layout):

Each card shows:
- Index name (large): NDVI, SAVI, EVI, NDMI, NDRE
- Mean value (large number, color-coded per index scale)
- Valid pixels percentage (e.g., "87%")
- Small trend arrow (up/down/flat from previous measurement)
- Trend/history: small inline sparkline chart showing the last 6-8 measurements

CRITICAL: Negative index values can be valid agronomic signals. Do NOT visually
mark negative values as broken or invalid data. Values below zero are shown
normally with appropriate color positioning on the index scale.

> negative index values can be valid

Below index cards:

"Рекомендуемое действие" section:
- Placeholder text: "Последние измерения показывают снижение NDVI. Рекомендуется провести полевое обследование."
- This is a static placeholder, not a live recommendation engine.

Alerts block:
- Small section showing "Предупреждения" with 1-2 alert items for this field
- Each alert shows severity badge (Критично / Высокий риск / Инфо), message, and date

Loading state: show skeleton pulse placeholders for all sections
Empty state: show "Нет данных" with field name and enterprise still visible
Error state: red banner with error message and "Повторить" (retry) button, field identity remains visible

Styling: dark panel background (#1f2937), compact cards with subtle borders (#374151), readable Russian text.
```

---

## 8. Alerts prototype prompt

> Copy-paste this prompt into Google Stitch to generate the alerts/warnings UI.
> Aligns with TASK_108 alerts UX redesign (operational work queue).

```
Generate a Russian-language alerts page for an agricultural monitoring platform.

Title: "Предупреждения" (Alerts / Warnings)

Filter bar (horizontal row, below title):
- Filter by severity: "Вся важность" dropdown with options: Все, Критично, Высокий риск, Инфо
- Filter by status: "Все статусы" dropdown with options: Все, Новое, Принято, Закрыто
- Search by field name: text input with search icon
- Date range filter: from / to date inputs

Alerts list (scrollable, card-based, not a raw table):

Each alert card shows in a compact row layout:
- Severity badge (left edge, colored bar):
  - "Критично" — red background (#dc2626)
  - "Высокий риск" — amber background (#f59e0b)
  - "Инфо" — blue background (#3b82f6)
- Status badge:
  - "Новое" — blue filled badge (unread indicator)
  - "Принято" — gray outlined badge
  - "Закрыто" — dimmed/low-opacity badge
- Field name (clickable link style)
- Alert type/message (e.g., "Критическое снижение NDVI", "Отсутствие данных за 14+ дней")
- Date/time (e.g., "12.06.2026 14:30")
- "Принять" (Acknowledge) button — primary action for new alerts, disabled for already-acknowledged alerts

Empty state:
- When no alerts match filters, show a centered card with:
  - Checkmark icon (green)
  - "Нет предупреждений" title
  - "На данный момент все поля в норме" subtitle
  - Optional: "Сбросить фильтры" button if filters are active

Styling: dark professional theme, cards on dark background, severity visible at a glance, status indicators clear, alerts look like operational work items not decorative messages.
```

---

## 9. Enterprise comparison prototype prompt

> Copy-paste this prompt into Google Stitch to generate an enterprise/farm comparison screen.

```
Generate a Russian-language enterprise comparison page for an agricultural holding management platform.

Title: "Сравнение предприятий" (Enterprise Comparison)

Filter/sort bar:
- Search by enterprise name
- Sort by: Название, Площадь, Всего полей, Средний NDVI
- Toggle between table view and card view

Table columns:
1. Предприятие (Enterprise name, clickable)
2. Всего полей (Total fields)
3. Общая площадь, га (Total hectares)
4. Здоровые (Healthy field count, green)
5. Под риском (Risk field count, amber)
6. Без данных (No-data field count, gray)
7. Средний NDVI (Average NDVI, color-coded)
8. Средний SAVI
9. Средний EVI
10. Средний NDMI
11. Средний NDRE
12. Предупреждения (Alert count, red if critical > 0)

Each row should provide at-a-glance health status — green/yellow/gray color coding on the healthy/risk/no-data columns.

Sort/filter controls: all columns sortable, filter by enterprise name.

Management-friendly layout: dense but readable, clear visual hierarchy, totals row at bottom.

Design note: This is a visual prototype. Do not imply that backend API support for
all these metrics already exists. Average indices per enterprise and alert counts
per enterprise may require future backend implementation.
```

---

## 10. Reports prototype prompt

> Copy-paste this prompt into Google Stitch to generate the future reports UI concept.

```
Generate a Russian-language reports page for an agricultural monitoring platform.

Title: "Отчёты" (Reports)

Available reports section (card grid, 2-3 cards):

Card 1: "Еженедельный мониторинг полей"
- Description: "Сводка состояния полей за неделю: здоровые, проблемные, без данных"
- Placeholder preview showing mini table with field count, healthy %, risk %, no-data %
- Export button placeholders:
  - "PDF" (outlined button, not implemented — shows "Скоро" tooltip)
  - "Excel" (outlined button, not implemented — shows "Скоро" tooltip)

Card 2: "Проблемные поля"
- Description: "Поля с критическими показателями, требующие внимания"
- Mini preview: list of 3-4 field names with red/amber status badges
- Same export button placeholders

Card 3: "Сравнение предприятий"
- Description: "Сравнительный анализ показателей по предприятиям"
- Mini preview: small bar chart or comparison table snippet
- Same export button placeholders

Card 4: "Нет свежих данных"
- Description: "Поля, по которым отсутствуют актуальные спутниковые данные"
- Mini preview showing a few fields with "Нет данных" labels
- Same export button placeholders

Design note: Export buttons (PDF, Excel) are visual placeholders only. Do NOT
implement export functionality in Stitch. Actual exports require backend implementation.

Styling: report cards on dark background, professional financial-report aesthetic, suitable for holding management review.
```

---

## 11. Stitch output acceptance criteria

### Accept if

| Criterion | Details |
|-----------|---------|
| Map is primary work surface | The map occupies the main content area, not a small widget |
| Russian labels are clear | All navigation, buttons, and labels use correct Russian agricultural terminology |
| Cards are readable | Text is legible, contrast meets enterprise standards |
| Severity/status visible | Alert severity and status badges are distinguishable at a glance |
| Dashboard is not decorative | Every stat card and chart answers "what needs attention" |
| Information density is practical | Dense layout without wasted whitespace, but not cluttered |
| Design is implementable | Uses standard CSS layout (flexbox/grid), no custom canvas rendering, compatible with React + Tailwind + MapLibre |

### Reject if

| Criterion | Details |
|-----------|---------|
| Generic SaaS look | Looks like a bootstrap admin template without agricultural character |
| Farming cartoons dominate | Illustrations of tractors, crops, or farmers as primary visual elements |
| Map is secondary | Map shown as a small widget rather than the central workspace |
| Too much empty whitespace | Low density, excessive card padding, layout optimized for presentation decks |
| Tiny unreadable text | Body text below 11px, cramped table cells, illegible Russian characters |
| Color-only semantics | Risk levels conveyed only by color without text labels or icons |
| Impossible layout | Requires WebGL canvas, Three.js, non-standard mapping libraries — must work with React + Tailwind + MapLibre |
| Production code assumed ready | Generated code that assumes API endpoints, database tables, or backend services already exist |

---

## 12. Production implementation warning

> Read this section carefully before acting on Stitch output.

### Rules

1. **Stitch output is not production code.** The generated HTML, CSS, or JavaScript is a visual mock, not a deliverable artifact.
2. **Future implementation must be broken into atomic tasks.** Each frontend task should modify one screen, one component, or one interaction pattern.
3. **Do not copy-paste generated code directly into the AgroSat codebase.** Stitch-generated code does not respect:
   - Existing React component architecture and hooks
   - API contracts and data fetching patterns
   - MapLibre lifecycle and cleanup rules
   - Tailwind configuration and custom `agro-*` palette
   - Database constraints and migration policies
4. **Frontend tasks must preserve API contracts.** Do not change expected request/response shapes in frontend code. Backend changes require separate tasks.
5. **Backend/DB changes require separate tasks.** Do not add backend dependencies, change API behavior, or require DB schema changes in a frontend-focused task.
6. **MapLibre cleanup rules still apply.** All layers, sources, and event listeners must be cleaned up on unmount and style changes.
7. **Lazy SQLAlchemy, APScheduler-in-FastAPI, and DB schema changes without migrations** remain forbidden project rules.

---

## 13. Recommended next implementation tasks after Stitch

After the Stitch prototype validates the visual direction, implement production code in the following order:

### TASK_110_MANAGEMENT_DASHBOARD_CONCEPT
Implement the enhanced management dashboard with the stat bar, problematic fields table, alerts summary, and enterprise comparison section.

### TASK_111_RUNTIME_VISUAL_VALIDATION_FRONTEND_UI
Validate the Stitch prototype layout against real frontend runtime — compare component rendering, spacing, and color against the design system.

### TASK_112_MAP_WORKSPACE_FILTERS_AND_LEGEND
Implement the field list search/filter panel, index type selector, date selector, and map legend in the map workspace.

### TASK_113_ENTERPRISE_COMPARISON_UI
Build the enterprise comparison screen with table view, sort/filter controls, and average index display.

### TASK_114_REPORTS_UI_CONCEPT
Create the reports page layout with report card grid and export button placeholders, without implementing actual exports or backend integration.
