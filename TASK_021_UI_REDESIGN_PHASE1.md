# TASK_021: UI Redesign Phase 1 — Layout + Satellite Basemap + Minimalist Styling

## Goal
Transform the AgroSat interface from a traditional dashboard layout into a modern, map-centric design inspired by OneSoil. The map becomes the primary canvas, taking up the full screen. Navigation becomes a slim sidebar. The header becomes minimal. Satellite imagery becomes the default basemap.

---

## Step 0: Read files first (MANDATORY)

Read ALL of these files before making any changes:
```
frontend/src/App.jsx
frontend/src/components/Layout/Sidebar.jsx
frontend/src/components/Layout/Header.jsx
frontend/src/pages/FieldsPage.jsx
frontend/src/components/Map/FieldMap.jsx
frontend/src/index.css
frontend/tailwind.config.js
```

Also check the current navigation system:
```bash
grep -rn "onNavigate\|setCurrentPage\|currentPage" frontend/src/ --include="*.jsx" --include="*.js" -l
```

---

## Step 1: New Color Palette and Global Styles

### Update `tailwind.config.js`

Add these custom colors to the theme.extend.colors section:
```javascript
colors: {
  // Keep existing colors, ADD these:
  'agro': {
    'dark': '#0a0f0d',      // Main background (almost black-green)
    'panel': '#111916',      // Panel backgrounds
    'card': '#162019',       // Card/item backgrounds
    'border': '#1e2d25',     // Subtle borders
    'hover': '#1a2b22',      // Hover state
    'text': '#e2e8e4',       // Primary text (light)
    'muted': '#7a8f82',      // Secondary/muted text
    'accent': '#34d399',     // Primary accent (emerald green)
    'accent-dim': '#166534', // Dimmed accent
  }
}
```

### Update `index.css`

Find the existing global styles. Add/replace with:
```css
/* Scrollbar styling for the dark theme */
::-webkit-scrollbar {
  width: 6px;
}
::-webkit-scrollbar-track {
  background: #111916;
}
::-webkit-scrollbar-thumb {
  background: #1e2d25;
  border-radius: 3px;
}
::-webkit-scrollbar-thumb:hover {
  background: #2a3f32;
}

/* Smooth transitions globally */
* {
  transition-property: background-color, border-color, color, opacity;
  transition-duration: 150ms;
}

/* Override for map — no transitions on map elements */
.maplibregl-map * {
  transition: none !important;
}
```

---

## Step 2: Redesign Sidebar — Slim Icon Bar

The current Sidebar should become a slim vertical icon bar (56-64px wide) on the LEFT edge of the screen. No text labels visible by default — only icons. Tooltip on hover shows the page name.

### Rewrite `Sidebar.jsx`

The sidebar should have:
- Width: 56px (w-14)
- Full height of viewport
- Background: `bg-agro-dark` with right border `border-r border-agro-border`
- Logo at top (small, just icon — use a leaf/plant emoji or "🌾" as placeholder, or the first letter "A" in a circle)
- Navigation icons stacked vertically, centered
- Active page indicator: left accent bar (3px wide, bg-agro-accent) + icon color change
- Icons use simple Unicode or inline SVG (DO NOT install any icon library)

Navigation items (in order):
1. **Карта** (Map) — grid/map icon → navigates to 'fields'
2. **Обзор** (Dashboard) — bar-chart icon → navigates to 'dashboard'  
3. **Алерты** (Alerts) — bell icon → navigates to 'alerts'
4. **Предприятия** (Enterprises) — building icon → navigates to 'enterprises'

Use simple inline SVGs for icons. Here are minimal SVG paths to use:

```jsx
// Map icon (grid of 4 squares)
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-5 h-5">
  <path d="M9 2L15 2M9 22L15 22M2 9L2 15M22 9L22 15M6 2H4a2 2 0 00-2 2v2M18 2h2a2 2 0 012 2v2M6 22H4a2 2 0 01-2-2v-2M18 22h2a2 2 0 002-2v-2"/>
</svg>

// Dashboard icon (bar chart)  
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-5 h-5">
  <path d="M3 3v18h18M7 16v-4M12 16V8M17 16v-6"/>
</svg>

// Bell icon (alerts)
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-5 h-5">
  <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 01-3.46 0"/>
</svg>

// Building icon (enterprises)
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-5 h-5">
  <path d="M3 21h18M5 21V7l8-4v18M19 21V11l-6-4M9 9h1M9 13h1M9 17h1"/>
</svg>
```

Bottom of sidebar: small user avatar circle or settings gear icon.

Each nav item should show a tooltip on hover. Implement with CSS `group` + `group-hover` pattern:
```jsx
<div className="relative group">
  <button ...>{icon}</button>
  <div className="absolute left-full ml-2 px-2 py-1 bg-agro-card text-agro-text text-xs rounded 
                  opacity-0 group-hover:opacity-100 pointer-events-none whitespace-nowrap z-50
                  border border-agro-border">
    Карта
  </div>
</div>
```

The sidebar must receive `currentPage` and `onNavigate` props and work with the existing navigation system.

---

## Step 3: Minimize Header

The Header should become a minimal floating bar at the top of the content area (to the right of the sidebar). It should be semi-transparent and NOT take up significant vertical space.

### Rewrite `Header.jsx`

New header design:
- Position: absolute, top of the map area, z-index above map
- Background: `bg-agro-dark/80 backdrop-blur-sm`
- Height: 40-44px
- Contains:
  - Page title (left side) — small text, e.g. "Мониторинг полей"
  - Right side: notification badge (alert count if any), user name/avatar placeholder
- Rounded bottom corners: `rounded-b-lg`
- Margin from left: leave space for sidebar

**Important:** The header should NOT be a full-width bar. It should be a floating element that doesn't interfere with the map. For the map/fields page, the header can be transparent/hidden entirely — the map controls are sufficient.

If the current page is 'fields' (map page), the header should be HIDDEN or extremely minimal (just a small floating pill showing alert count).

For other pages (dashboard, alerts, enterprises), show the header with the page title.

---

## Step 4: Update App.jsx Layout

Restructure the main layout:

```
┌──────────────────────────────────────────┐
│ [Sidebar 56px] │ [Content Area - full]   │
│                │                          │
│  🌾 logo      │  Map / Dashboard / etc   │
│                │                          │
│  📍 Map       │                          │
│  📊 Dashboard │                          │
│  🔔 Alerts    │                          │
│  🏢 Enterprises│                         │
│                │                          │
│  ⚙️ Settings  │                          │
└──────────────────────────────────────────┘
```

Update App.jsx:
- Remove any padding/margin around the content area
- The content area should take `calc(100vw - 56px)` width and `100vh` height
- For the fields/map page: content should be edge-to-edge (no padding)
- For other pages: add padding inside the page component itself
- Make sure the root div has `h-screen overflow-hidden`

The overall structure should be:
```jsx
<div className="flex h-screen bg-agro-dark overflow-hidden">
  <Sidebar currentPage={currentPage} onNavigate={handleNavigate} />
  <main className="flex-1 overflow-hidden">
    {/* Page content renders here */}
    {renderPage()}
  </main>
</div>
```

Remove the Header from the App.jsx layout wrapper. Each page will handle its own header/title if needed.

---

## Step 5: FieldsPage — Full-Screen Map

Update FieldsPage.jsx:
- The map should take 100% of the available space (the entire content area)
- Remove any wrappers, cards, or panels around the map
- The FieldMap component should receive `className="w-full h-full"` or equivalent
- Map controls (zoom, style switcher, NDVI toggle) should float on top of the map

---

## Step 6: FieldMap — Satellite Default + Visual Improvements

### Change default basemap to Satellite

In FieldMap.jsx, find the map style definitions. Change the DEFAULT style to Satellite (Esri World Imagery).

The existing satellite style URL should be something like:
```javascript
// Esri World Imagery
{
  version: 8,
  sources: {
    'esri-satellite': {
      type: 'raster',
      tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
      tileSize: 256,
      attribution: '© Esri'
    }
  },
  layers: [{
    id: 'esri-satellite-layer',
    type: 'raster',
    source: 'esri-satellite'
  }],
  glyphs: 'https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf'
}
```

Make this the DEFAULT style when the map loads. Keep the style switcher so users can toggle to OSM if needed.

### Improve field polygon styling on satellite

On satellite imagery, field boundaries should be:
- **Default mode (crop colors):** Semi-transparent fill (opacity 0.25) + solid 2px border in the crop color
- **NDVI mode:** Semi-transparent NDVI color fill (opacity 0.3) + white 1.5px border
- **Hover:** Increase fill opacity to 0.5, add glow effect (thicker border)
- **Selected field:** White border 3px + brighter fill

### Style switcher redesign

The map style toggle buttons should be a small floating control in the bottom-right or top-right corner of the map:
- Small pill-shaped buttons: "Спутник" | "Карта" | "Гибрид"
- Dark semi-transparent background
- Active button highlighted with accent color
- Compact — should not dominate the map

### NDVI toggle redesign

The NDVI mode toggle should also be a floating control near the style switcher:
- Small pill: "Культуры" | "NDVI"  
- Same visual style as the style switcher

---

## Step 7: Update Other Pages Background

For DashboardPage, AlertsPage, and any enterprise pages:
- Set background to `bg-agro-dark`
- Text to `text-agro-text`
- Cards/panels to `bg-agro-panel border border-agro-border rounded-xl`
- Keep their existing layout but apply the new color scheme
- Add padding: `p-6` inside each page component
- Add a small page title at the top of each page (replaces the removed Header)

Do NOT do a full redesign of these pages — just apply colors so they match the new theme. The focus of this task is the map experience.

---

## Step 8: Clean Up

After all changes:
1. Remove any unused imports
2. Remove any unused CSS classes from the old design
3. Make sure there are no console errors
4. Make sure all navigation still works (clicking sidebar items navigates correctly)

---

## Verification Checklist

After completing all steps, verify:

- [ ] App loads without errors
- [ ] Sidebar is slim (56px), shows icons only, tooltips on hover
- [ ] Clicking sidebar items navigates between pages
- [ ] Active page has visual indicator in sidebar
- [ ] Map page: map takes full available space (no padding/margins)
- [ ] Map default style is Satellite (Esri World Imagery)
- [ ] Field polygons visible on satellite with semi-transparent fills
- [ ] Style switcher works (Satellite/OSM/Hybrid)
- [ ] NDVI toggle works
- [ ] Field labels still render on zoom 11+
- [ ] Dashboard page has dark theme applied
- [ ] Alerts page has dark theme applied
- [ ] No console errors
- [ ] All existing functionality preserved (navigation, alerts, enterprise detail, etc.)

---

## CRITICAL WARNINGS

1. **DO NOT remove the `glyphs` property** from ANY map style object. Without it, field labels won't render. Every style object MUST have:
   ```
   glyphs: 'https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf'
   ```

2. **DO NOT change the `onNavigate` callback system.** The app uses a custom callback pattern for navigation, NOT React Router. Preserve this.

3. **DO NOT break the field-centroids source** for map labels. Labels use centroid lat/lon from a POINT source, not from polygon sources.

4. **Preserve ALL existing functionality.** This is a visual redesign, not a feature change. Every button, every filter, every modal must still work.

5. **Map must resize properly** when switching pages. If the map container size changes, call `map.resize()`.
