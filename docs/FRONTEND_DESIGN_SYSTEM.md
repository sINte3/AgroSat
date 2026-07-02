# AgroSat Frontend Design System

> Single UI/UX reference for AgroSat redesign tasks.
> Inspired by **OneSoil**-like map-first clarity and **True Fields**-like analytical depth.

---

## 1. Purpose

This document is the single frontend UI/UX reference for AgroSat redesign tasks. It defines the visual and interaction foundation before any visual refactoring begins.

**Rules:**

- Do not make random per-page CSS fixes without checking this document.
- Redesign must stay **map-first** and **operational**.
- Visual polish must support **agronomic decision-making**, not decoration.
- All new frontend implementation tasks must reference this document for layout, color, spacing, and component guidance.

---

## 2. Product UI Principles

| # | Principle | Rationale |
|---|-----------|-----------|
| 1 | **Map-first workflow** | The map is the primary work surface. Field geometry, satellite data, and spatial context drive all operational decisions. |
| 2 | **Field-centered operations** | Every action (alert review, data inspection, scouting) starts from or returns to a specific field. |
| 3 | **Management overview first, details second** | Managers see aggregate health; agronomists drill into field detail. |
| 4 | **Every index value must answer "is this field OK or does it need attention?"** | Data is actionable, not ornamental. |
| 5 | **No decorative dashboards without operational use** | Every chart, stat card, and panel serves a decision. |
| 6 | **Compact but readable enterprise UI** | Dense information density; no wasted whitespace. |
| 7 | **Russian-language production UI** | Current deployment uses Russian (`ru-RU`). Labels such as `Главная`, `Предупреждения`, `Поля`, `Предприятия`, `Обзор`, `Алерты` are the current baseline. |
| 8 | **Future i18n readiness** | UI text should eventually be extractable to locale files, but i18n is not implemented now. |

---

## 3. Primary App Layout

The intended long-term layout (target for refactoring tasks):

```
┌─────────┬──────────────────────────────────────┬──────────┐
│         │           Top Header                 │          │
│ Sidebar │──────────────────────────────────────│  Right   │
│  (icon  │                                      │  Panel   │
│   nav)  │        Main Content Area             │ (field   │
│         │        / Map Workspace               │  detail) │
│         │                                      │          │
│         ├──────────────────────────────────────┤          │
│         │   Bottom Timeline / Index History     │          │
└─────────┴──────────────────────────────────────┴──────────┘
```

### Spacing rules

- **Top header**: Must not overlap page content. Content offset below header must be explicit (e.g. `pt-16` pattern).
- **Page titles**: Must appear once. The `Header` component currently shows `Главная` for dashboard; the `AlertsPage` also renders `<h2>Предупреждения</h2>` in page content — the duplicate Главная must be eliminated in implementation tasks (title should come from one source, not both header and page).
- **`Предупреждения` title**: Must not collide with header. Current `pt-16` offset on `AlertsPage` is correct.
- **Fixed/sticky elements**: Must have clear content offsets documented in layout CSS, not ad-hoc inline values.
- **Z-index hierarchy**:
  - `z-10`: Map controls, style toggles, overlay buttons
  - `z-20`: Field list panel, header (when visible)
  - `z-30`: Sidebar
  - `z-40`: Modals, dropdowns
  - `z-50`: Draw mode controls, tooltips
  No value above `z-50` should exist outside modals.

### Current state (pre-refactor)

The `Sidebar` (left icon nav, `z-30`) and `Header` (when visible, `z-20`) are the only persistent chrome. The map workspace currently uses a full-page layout where the `Header` is hidden (`currentView === 'fields'` returns null), and content fills the remaining space. The right-side field detail panel is shown as a sidebar overlay on the map page. The bottom timeline panel does not yet exist.

---

## 4. Navigation and Routes

### Intended route model (future)

| View | Route | Status |
|------|-------|--------|
| Dashboard / Home | `/dashboard` | Exists |
| Fields / Map workspace | `/fields` | Exists |
| Alerts / Warnings | `/alerts` | Exists |
| Enterprises / Farms / Subsidiaries | `/enterprises` | Exists |
| Reports | `/reports` | Future |
| Scouting | `/scouting` | Future |
| Productivity zones | `/zones` | Future |
| Settings / Admin | `/settings` | Future |

### Deep-linking target (not implemented now)

- Selected field must eventually be URL-addressable: `/fields/{fieldId}`
- Selected enterprise/farm must eventually be URL-addressable: `/enterprises/{enterpriseId}`
- Browser back/forward must not break workflow — currently state is managed via local React state (`selectedFieldId`, `selectedEnterpriseId`) not in URL.
- The current `AppLayout` uses a `view` state variable driven by URL path but field/enterprise IDs are not reflected in the URL, making direct linking impossible.

---

## 5. Dashboard Design Rules

### Purpose

Management overview — not decorative. The dashboard must answer "what needs attention right now" at a glance.

### Must eventually show (for implementation tasks)

- Total fields
- Total hectares (**currently missing** per TASK_103A — must be added)
- Healthy / risk / no-data field counts
- Latest satellite data freshness
- Top problematic fields
- Alerts summary (critical + warning)
- Enterprise / farm comparison
- Index trend summary (NDVI, SAVI, EVI, NDMI, NDRE)
- Map / risk preview if useful

### Current dashboard structure

The `DashboardPage` loads `getCachedDashboardSummary()` and `getAlerts()`. `SummaryCards` renders four cards: total fields, active alerts, critical alerts, average NDVI. The alerts section is split into critical and warning sections on a scrollable second panel. The `EnterpriseList` component renders enterprise cards at the top. Total hectares is not present — the `summary` object does not expose `total_hectares`. This must be addressed in a backend + frontend task.

---

## 6. Map Workspace Design Rules

The map workspace is the primary operational surface.

### Layout

- **Map** occupies the full workspace background.
- **Field list / filter panel** sits as an overlay on the left side (currently `FieldListPanel` with enterprise filter).
- **Selected field state** must be visually obvious (border highlight, distinct fill color).
- **Layer legend** must be available (currently NDVI legend is shown only when NDVI color mode is active).
- **Index / date controls** must be clear and accessible.
- **Map controls** must not hide important data — currently navigation control is `top-right`, style switcher is `top-right`.
- **Avoid full MapLibre re-mount** unless necessary — the `key={mapReloadKey}` pattern in `FieldsPage` triggers a complete re-mount after field creation. This should be replaced with a data refresh that does not destroy and recreate the map instance.

### Critical rule

> MapLibre layers, sources, and event listeners must be cleaned up on unmount and style changes.

The current `FieldMap` component does clean up layers, sources, and event listeners in its return function and style switch handler, but the `rehydrateLayers` function after style switch re-attaches handlers using `m.off()` before `m.on()`. The cleanup must be verified as complete in all code paths (unmount, style switch, logout).

### Color modes

- **Crop mode** (current default): fills fields by crop type using `CROP_COLORS` mapping.
- **NDVI mode**: fills fields by last NDVI value using a color interpolation expression.
- Future: SAVI, EVI, NDMI, NDRE color modes.

---

## 7. Field Detail Panel Design Rules

### Future panel contents

The `FieldDetailPanel` (right-side panel on map workspace) and `FieldDetailPage` (full-page detail) should converge on a shared component that supports:

- Field name
- Enterprise / farm
- Crop
- Area (hectares)
- Latest satellite date
- Status / risk badge
- Latest index cards:
  - NDVI
  - SAVI
  - EVI
  - NDMI
  - NDRE
- Trend / delta (change from previous measurement)
- Valid pixels percentage
- History chart (line chart per selected index)
- Recommended next action
- Link to alerts / scouting / report

### States

| State | Behavior |
|-------|----------|
| **Loading** | Skeleton placeholders for each section (text lines + chart area pulse) |
| **Empty** | "Нет данных" for each missing section. Full panel should still render field name and enterprise. |
| **Error** | Red banner with error message and retry button. Do not hide field identity. |
| **No satellite data** | Show "Нет данных" with explanation: "Данные спутника отсутствуют для этого периода". |

---

## 8. Satellite Index Visual Rules

### Index semantics

| Code | Full name | Meaning |
|------|-----------|---------|
| NDVI | Normalized Difference Vegetation Index | Vegetation vigor legacy index |
| SAVI | Soil-Adjusted Vegetation Index | Soil-adjusted vegetation signal |
| EVI | Enhanced Vegetation Index | Enhanced vegetation signal |
| NDMI | Normalized Difference Moisture Index | Vegetation / water / moisture signal |
| NDRE | Normalized Difference Red Edge | Red-edge vegetation / chlorophyll-sensitive signal |

### Visual rules

- **Negative values are allowed** and must not be treated as invalid.
- Use documented data quality rules from `docs/SATELLITE_INDEX_DATA_QUALITY_RULES.md`.
- **Do not duplicate NDVI color functions** — the `getNdviColor` function in `FieldDetailPanel.jsx` currently duplicates the NDVI color mapping used in `FieldMap.jsx`. Future implementation must consolidate index color scales into a shared module.
- **Future implementation should centralize index color scales**: a single `indexColors.js` or `useIndexColor()` hook that maps any index code + value to a color.
- **No unsupported `ndvi` insertion into `satellite_index_records`** — NDVI remains in `ndvi_records` table; other indices go in `satellite_index_records`.

### Critical rule

> `mean_value < 0` is not an invalid-value predicate

`mean_value < 0 is not an invalid-value predicate`

All five indices can legitimately produce negative values under certain agronomic conditions. Negative values must not be filtered, deleted, or marked as invalid in the UI.

---

## 9. Color System

### Color categories (not final brand tokens — to be centralized)

| Category | Usage | Suggested direction |
|----------|-------|-------------------|
| **Primary brand green** | Accent, active states, positive metrics | Current `agro-accent` / green-600 range |
| **Secondary dark / navy / charcoal** | Enterprise UI backgrounds, text | Current `agro-text` / `agro-dark` |
| **Neutral grayscale** | Cards, borders, muted text | Current `agro-border`, `agro-muted`, `agro-card` |
| **Success / healthy** | NDVI ≥ 0.6, fields in good condition | Green (`#16a34a` range) |
| **Warning / risk** | NDVI 0.3–0.6, moderate alerts | Amber / yellow |
| **Critical** | NDVI < 0.3, critical alerts, failures | Red |
| **No-data / cloud / disabled** | Missing values, cloudy pixels, disabled controls | Gray (`#9ca3af` / `#4b5563`) |
| **Moisture / water blue** | NDMI context, water-related indicators | Blue |

### Rules

- Hardcoded scattered hex colors (e.g. `#1a2e23`, `#e0e7e3`, `#bbf7d0` in enterprise pages) must be eliminated in future implementation tasks.
- Colors must be centralized as **design tokens** or shared Tailwind classes / CSS custom properties.
- The current `tailwind.config.js` defines a custom `agro-*` color palette — new tokens should extend this, not bypass it.

---

## 10. Typography and Spacing

### Heading levels

| Level | Size | Weight | Usage |
|-------|------|--------|-------|
| H1 | `text-xl` / 20px | Bold (700) | Page titles |
| H2 | `text-lg` / 18px | Semibold (600) | Section headers |
| H3 | `text-sm` / 14px | Semibold (600) | Card titles, panel headers |
| H4 | `text-xs` / 12px | Semibold (600) | Group labels, uppercase |

### Page title behavior

- **Page titles must not be duplicated between global header and page content.**
- The duplicate Главная in the `Header` component (rendered as `<h1>`) and `DashboardPage` content must be eliminated.
- `AlertsPage` correctly uses `<h2>` for "Предупреждения" in content — the Header also shows "Предупреждения" which is the intended behavior (breadcrumb vs page title).

### Text sizes

- Card titles: `text-sm` (14px)
- Table text: `text-sm` (14px)
- Small metadata text: `text-xs` (12px)
- Minimum readable size: `text-[11px]` only for secondary metadata in compact layouts; never below 11px
- **Enterprise users need readability, not tiny decorative text** — default body text must be at least 14px for Russian text legibility.

### Spacing scale

- Page padding: `p-4` (16px) / `p-6` (24px)
- Card gap: `gap-4` (16px)
- Section spacing: `space-y-4` / `space-y-6`
- Compact enterprise cards: `p-3` (12px)
- Dense list items: `px-3 py-2`

### Dense enterprise layout rules

- Use compact spacing (`p-3`, `gap-3`) for high-density enterprise views.
- Avoid excessive margin/padding on enterprise pages — current `EnterpriseDetailPage` uses multiple inline `marginTop: 14, marginBottom: 8` that should be standardized.

---

## 11. Components

The following reusable components must be standardized in future implementation tasks. They are **not implemented now**.

| Component | Purpose | Suggested props |
|-----------|---------|----------------|
| **AppShell** | Top-level layout wrapper (sidebar + header + content + optional right panel) | `sidebar`, `header`, `children`, `rightPanel` |
| **Header** | Top header bar with page title and optional actions | `title`, `subtitle`, `actions`, `alertCount` |
| **Sidebar** | Left icon navigation | `activeView`, `onNavigate`, `enterprises` |
| **PageHeader** | Per-page title with breadcrumb and actions | `title`, `subtitle`, `backTo`, `actions` |
| **StatCard** | Metric display card (icon + label + value) | `icon`, `label`, `value`, `color`, `trend` |
| **RiskBadge** | Field risk level badge | `level: 'healthy' \| 'warning' \| 'critical' \| 'no_data'` |
| **IndexCard** | Satellite index value card for the field detail panel | `indexCode`, `value`, `trend`, `date`, `validPixels` |
| **FieldStatusBadge** | Field data freshness or status | `status: 'fresh' \| 'stale' \| 'no_data'` |
| **DataFreshnessBadge** | Satellite data age indicator | `lastDate`, `maxAgeDays` |
| **AlertSeverityBadge** | Alert severity indicator | `severity: 'critical' \| 'warning' \| 'info'` |
| **EmptyState** | Placeholder for empty data | `icon`, `title`, `description`, `action` |
| **LoadingState** | Loading skeleton wrapper | `lines`, `variant: 'card' \| 'table' \| 'chart'` |
| **ErrorState** | Error display with retry | `message`, `onRetry` |
| **MapLegend** | Legend for the active index/color mode | `indexCode`, `colorScale`, `min`, `max` |
| **FilterBar** | Reusable filter/toolbar row | `filters`, `search`, `onChange` |
| **TableToolbar** | Table action bar (search, filter, export) | `search`, `filters`, `actions` |
| **RightPanel** | Collapsible right-side panel for field detail | `width`, `children`, `onClose` |

---

## 12. Alerts / Warnings UX Rules

### Core principle

Alerts are **operational work items**, not just messages. Each alert demands or has demanded an action.

### Required visibility

- Severity must be visible at a glance (critical red, warning amber, info blue).
- Status must be visible: new / acknowledged / in progress / closed.
- Field link must be available (clickable field name or ID).
- Date / freshness must be visible.

### Status distinctions

The UI must distinguish:

| Status | Visual cue |
|--------|------------|
| New | Unread indicator (bold, dot, or highlight) |
| Acknowledged | Normal appearance |
| In progress | Working indicator |
| Closed | Dimmed or hidden by default |

### Future alert table / card must support

- Filters by severity, status, enterprise, field, date range
- Severity badges (red / amber / blue)
- Status badges (new / ack / progress / closed)
- Field navigation (click → highlight on map or open detail)
- Responsible person later
- Comment / history later

### Current patterns

The `AlertsPage` provides severity filters, a search bar (by field name), and renders `AlertsList` which supports `onAcknowledged`. The dashboard `AlertsSection` renders critical and warning groups. The `EnterpriseDetailPage` renders alerts in a Recommendations tab with AI analysis and Telegram send.

---

## 13. Scouting Future-Readiness

### Future workflow (not implemented now)

1. Satellite anomaly creates / checks a **point of interest** (POI) on the map.
2. Agronomist visits the field.
3. Agronomist adds photo, comment, diagnosis.
4. Scouting record status is updated.
5. Record is linked back to the satellite signal that triggered it.

### Design implications

- The map workspace must support point markers for POIs.
- The field detail panel must show recent scouting entries.
- A new `/scouting` route will be needed.
- Scouting records must be linkable from alerts.

---

## 14. Reports Future-Readiness

### Future reporting direction (not implemented now)

- Management weekly report: field health summary, top issues, trends.
- Problematic fields report: fields with critical conditions.
- No fresh satellite data report: fields where data is stale.
- Enterprise / farm comparison: side-by-side metrics.
- Export to PDF / Excel later.

### Design implications

- A new `/reports` route will be needed.
- Reports should reuse the `StatCard`, `RiskBadge`, and `IndexCard` components.
- PDF download should use server-side generation (current pattern via `GET /api/reports/enterprise/{id}/pdf`).

---

## 15. CSS and Maintainability Rules

### Rules

- **Avoid inline styles** except for genuinely unavoidable dynamic positioning (e.g. MapLibre pixel offsets). The `EnterpriseDetailPage` currently has extensive inline style debt found by TASK_103A — this must be migrated to shared classes or Tailwind as part of layout cleanup.
- **Avoid duplicated color functions.** The `getNdviColor` function in `FieldDetailPanel.jsx` duplicates the NDVI color mapping embedded in the MapLibre paint expression in `FieldMap.jsx`. NDVI color mapping duplication must be consolidated in a later frontend task.
- **Avoid one-off page-specific spacing hacks.** Each page should use the shared spacing scale (§10).
- **Prefer shared layout / components / classes** over per-page custom CSS.
- **Generated build output** (`dist/`) must not be committed unless already part of repo policy.
- **No backend/DB coupling from UI tasks** — frontend tasks must not add backend dependencies, change API behavior, or require DB schema changes.

### Current debt to address

| Issue | Location | Task |
|-------|----------|------|
| Inline styles | `EnterpriseDetailPage.jsx` — entire RecommendationsTab, renderMarkdown, skeleton, error | TASK_105 / TASK_106 |
| NDVI function duplicated | `FieldMap.jsx` (paint expr) and `FieldDetailPanel.jsx` (`getNdviColor`) | TASK_107 |
| `key={mapReloadKey}` full re-mount | `FieldsPage.jsx` after field creation | TASK_106 |
| Hardcoded hex colors | `EnterpriseDetailPage.jsx`, `FieldDetailPanel.jsx` | TASK_105 |
| No i18n / Russian hardcoded | All UI files | Future |

---

## 16. Accessibility and Usability Rules

- **Sufficient contrast**: Text on background must meet WCAG AA (4.5:1 for normal text, 3:1 for large text). Current red-on-green and green-on-green patterns need verification.
- **Readable fonts**: Minimum 14px body text for Russian. Avoid font sizes below 11px.
- **Keyboard / focus awareness**: All interactive elements (buttons, links, filters) must be keyboard-accessible. Map interactions are pointer-only by nature, but controls must be keyboard-operable.
- **Clear hover / selected states**: Field borders must change on hover (currently 2px → 3px). Selected fields must have distinct visual treatment.
- **No information by color alone**: Risk indicators must use shapes, icons, or labels in addition to color. The `severityColors` pattern in `FieldDetailPanel` uses background + text color + dot — this is correct.
- **Russian labels must be clear and consistent**: Use standard Russian agricultural terms. Avoid mixing Russian and English in visible labels.
- **Avoid unexplained acronyms for managers**: When showing NDMI, NDRE, SAVI, EVI in UI context, provide tooltip or help text explaining what the index measures. Managers may not be familiar with spectral index abbreviations.

---

## 17. Implementation Sequencing

### Recommended next frontend task sequence

1. **TASK_105_DASHBOARD_HEADER_LAYOUT_CLEANUP** — Eliminate duplicate Главная, add total hectares to dashboard summary, standardize spacing, migrate enterprise page inline styles to shared classes.
2. **TASK_106_MAP_WORKSPACE_LAYOUT_REDESIGN** — Redesign map workspace with persistent right panel, eliminate `key={mapReloadKey}`, add satellite raster overlay, verify MapLibre cleanup, add map legend.
3. **TASK_107_FIELD_DETAIL_PANEL_INDEX_CARDS** — Consolidate NDVI color mapping, add standardized IndexCard component for all five indices, centralize color scales, add valid pixels display.
4. **TASK_108_ALERTS_UX_REDESIGN** — Redesign alerts with status badges, field navigation, severity badges using shared components, add alert status distinction.
5. **TASK_109_STITCH_PROTOTYPE_BRIEF** — Create a stitched prototype connecting the redesigned header, map workspace, field panel, and alerts to validate the full workflow.
6. **TASK_110_MANAGEMENT_DASHBOARD_CONCEPT** — Design and implement the enhanced management dashboard with enterprise comparison, trend summary, and data freshness indicators.

---

## 18. Non-Goals

This design system task does not:

- Implement code changes in frontend runtime files.
- Change API behavior or add new API endpoints.
- Change database schema or add migrations.
- Change satellite index calculations or data pipeline logic.
- Start FastAPI, frontend dev server, or APScheduler.
- Solve Supabase credential rotation.
- Implement scouting, productivity zones, or reports.
- Define final brand color tokens (only categories and direction).
- Implement i18n.
- Change routing or URL structure.
- Modify `.env` files.
- Perform database reads or writes.
