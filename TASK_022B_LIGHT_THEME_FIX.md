# TASK_022B: Complete Light Theme Sweep

## Problem
The light theme from TASK_022 was only partially applied. Many components still have hardcoded dark backgrounds, light-on-dark text, and unreadable sections. This task does a comprehensive sweep of EVERY frontend file to enforce a clean, professional light theme.

---

## Step 1: Find ALL files with dark colors

Run these searches and fix EVERY match:

```bash
# Find all hardcoded dark backgrounds
grep -rn "bg-\[#0\|bg-\[#1\|bg-\[#2\|#0a0f\|#111\|#162\|#1e2\|#1a2b\|bg-gray-900\|bg-gray-800\|bg-slate-900\|bg-slate-800\|bg-zinc-900\|bg-zinc-800\|bg-neutral-900\|bg-neutral-800\|bg-green-900\|bg-green-950\|bg-emerald-900\|bg-emerald-950" frontend/src/ -r --include="*.jsx" --include="*.js" -l

# Find all light text that should now be dark
grep -rn "text-white\|text-gray-100\|text-gray-200\|text-gray-300\|text-slate-100\|text-slate-200\|text-green-100\|text-emerald-100" frontend/src/ -r --include="*.jsx" -l

# Find inline styles with dark colors
grep -rn "backgroundColor.*['\"]#[012]" frontend/src/ -r --include="*.jsx" -l
grep -rn "color.*['\"]#[cdef]" frontend/src/ -r --include="*.jsx" -l
```

---

## Step 2: Apply these rules to EVERY file found

### Background colors — replace:
| Old (dark) | New (light) |
|---|---|
| `bg-agro-dark` | Keep — token now maps to `#f8faf9` |
| `bg-agro-panel` | Keep — token now maps to `#ffffff` |
| `bg-agro-card` | Keep — token now maps to `#f1f5f3` |
| `bg-[#0a0f0d]` or similar hardcoded dark hex | `bg-white` or `bg-gray-50` |
| `bg-gray-900`, `bg-gray-800`, `bg-slate-900` | `bg-white` or `bg-gray-50` |
| `bg-green-900`, `bg-green-950`, `bg-emerald-950` | `bg-green-50` or `bg-white` |
| Any `backgroundColor: '#0...'` or `'#1...'` inline style | `backgroundColor: '#ffffff'` or `'#f8faf9'` |

### Text colors — replace:
| Old (light text for dark bg) | New (dark text for light bg) |
|---|---|
| `text-white` (on non-button, non-badge elements) | `text-gray-900` or `text-agro-text` |
| `text-gray-100`, `text-gray-200`, `text-gray-300` | `text-gray-700` or `text-gray-900` |
| `text-slate-100`, `text-slate-200` | `text-slate-700` or `text-slate-900` |
| `text-green-100`, `text-emerald-100` | `text-green-700` or `text-green-900` |
| Any `color: '#e...'` or `'#f...'` or `'#c...'` inline style | `color: '#1a2e23'` or `'#374151'` |

**EXCEPTIONS — keep `text-white` in these cases:**
- Buttons with colored backgrounds (`bg-agro-accent`, `bg-red-500`, `bg-green-600`, etc.)
- Badges with colored backgrounds
- The logo circle in the sidebar
- Map control active buttons
- Alert severity badges

### Border colors — replace:
| Old | New |
|---|---|
| `border-agro-border` | Keep — token now maps to `#e0e7e3` |
| `border-gray-700`, `border-gray-600` | `border-gray-200` or `border-agro-border` |
| `border-[#1e2d25]` or similar | `border-gray-200` |

### Hover states — replace:
| Old | New |
|---|---|
| `hover:bg-agro-hover` | Keep — token maps to `#e8eeea` |
| `hover:bg-gray-800`, `hover:bg-gray-700` | `hover:bg-gray-100` |
| `hover:bg-[#1a2b22]` | `hover:bg-gray-100` |

---

## Step 3: Fix specific components

### Sidebar.jsx
- Background: `bg-white border-r border-gray-200`
- Icons default: `text-gray-400`
- Icons active: `text-green-600`
- Active indicator bar: `bg-green-600`
- Hover: `hover:bg-gray-100`
- Logo circle: `bg-green-600 text-white` (this is correct to keep white text)
- Tooltips: `bg-gray-800 text-white` (dark tooltip on light UI — correct)
- Bottom user circle: `bg-gray-100 text-gray-500`

### Header.jsx
- Remove any dark green bar/banner background
- Background: `bg-white/90 backdrop-blur-sm border-b border-gray-200`
- Title text: `text-gray-900`
- Subtitle text: `text-gray-500`
- If there's a colored banner/bar at the top, remove it or make it white

### DashboardPage.jsx
- Page background: inherits light from body
- KPI cards: `bg-white rounded-xl border border-gray-200 shadow-sm`
- Card titles/labels: `text-gray-500 text-sm`
- Card numbers: keep their colors (green, red, orange)
- Alert list section: `bg-white rounded-xl border border-gray-200`
- Alert items: `hover:bg-gray-50 border-b border-gray-100`
- Alert field names: `text-green-700 font-medium` (links)
- Alert descriptions: `text-gray-600`
- "Показать все предупреждения" button: `bg-green-50 text-green-700 hover:bg-green-100`

### AlertsPage.jsx
- Filter pills default: `bg-white border border-gray-200 text-gray-700`
- Filter pills active: `bg-green-600 text-white border-green-600`
- Alert cards: `bg-white rounded-lg border border-gray-200 shadow-sm`
- Critical alerts: cards can have `border-l-4 border-l-red-500` or light red left accent
- Warning alerts: `border-l-4 border-l-amber-500`
- Info alerts: `border-l-4 border-l-blue-500`
- Alert type text: `text-gray-500 text-sm`
- Field name: `text-green-700 font-semibold`
- Description text: `text-gray-700` — MUST be readable
- Recommendation text: `text-gray-600 bg-green-50 p-3 rounded-lg`
- NDVI badges: keep colored (`bg-red-100 text-red-700` for low, `bg-green-100 text-green-700` for good)
- Search input: `bg-white border border-gray-200`

### EnterprisesPage.jsx
- Remove any dark header/banner
- Enterprise cards: `bg-white rounded-xl border border-gray-200 shadow-sm hover:shadow-md`
- Card title: `text-gray-900 font-semibold`
- Card subtitle (BAK code): `text-gray-500`
- Stats numbers: keep colored
- Stats labels: `text-gray-500 text-sm`
- Links: `text-green-600 hover:text-green-700`
- Critical badge: `bg-red-500 text-white` (keep)

### EnterpriseDetailPage.jsx
- Full sweep — same pattern as above
- Tab buttons: `text-gray-500` default, `text-green-600 border-b-2 border-green-600` active
- Tables: `bg-white` header `bg-gray-50`
- Table rows: `hover:bg-gray-50 border-b border-gray-100`
- Recommendation/AI sections: `bg-green-50 text-gray-700 p-4 rounded-lg`
- Modal backgrounds: `bg-white rounded-xl`
- Modal overlay: `bg-black/50`

### EnterpriseReport.jsx, EnterpriseDashboard.jsx, EnterpriseFieldsTable.jsx
- Same pattern — all backgrounds white, all text dark, keep colored badges/indicators

### NDVIChart.jsx
- Chart background: white
- Grid lines: `#e5e7eb` (gray-200)
- Axis text: `#6b7280` (gray-500)
- Tooltip: can stay dark (`bg-gray-800 text-white`) for contrast — this is a chart tooltip, not content

### WeatherWidget.jsx
- Cards: `bg-white border border-gray-200`
- Text: `text-gray-700`
- Temperature numbers: `text-gray-900 font-bold`

### NDVIHistoryModal.jsx
- Modal body: `bg-white`
- Table: header `bg-gray-50`, rows alternating `bg-white` and `bg-gray-50`
- Text: `text-gray-700`

---

## Step 4: Fix FieldMap.jsx controls

Map floating controls should be light:
- Style switcher container: `bg-white rounded-lg shadow-md border border-gray-200`
- Style buttons: `text-gray-600 px-3 py-1.5`
- Active style button: `bg-green-600 text-white`
- NDVI/Culture toggle: same pattern
- NDVI legend: `bg-white/95 rounded-lg shadow-md border border-gray-200 p-2`
- Legend text: `text-gray-700`

---

## Step 5: Final verification scan

After all changes, run:
```bash
# Should return NO results (except intentional dark elements like tooltips, modal overlays, map labels)
grep -rn "bg-gray-900\|bg-gray-800\|bg-slate-900\|bg-slate-800\|bg-\[#0\|bg-\[#1" frontend/src/ --include="*.jsx" | grep -v "tooltip\|Tooltip\|overlay\|modal-overlay\|chart\|halo"
```

Build and verify:
```bash
cd C:\AgroSat\frontend && npm run build
```

---

## Verification Checklist

- [ ] ALL page backgrounds are white or very light gray (#f8faf9)
- [ ] ALL text is dark and readable on light backgrounds
- [ ] Sidebar is white with green accents
- [ ] No dark green/black header bars
- [ ] Dashboard KPI cards are white with subtle borders and shadows
- [ ] Alert descriptions and recommendations are fully readable
- [ ] Enterprise cards are white with clean borders
- [ ] Map controls are light-themed
- [ ] Buttons with colored backgrounds keep white text
- [ ] Badges keep their colored backgrounds
- [ ] No console errors
- [ ] Build passes

## CRITICAL
- DO NOT remove `glyphs` from map styles
- DO NOT change `onNavigate` navigation system
- DO NOT break field-centroids map source
- Preserve ALL functionality
