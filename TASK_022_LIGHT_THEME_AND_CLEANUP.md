# TASK_022: Delete Unused Enterprises + Light Theme

## Overview
Two changes:
1. Delete 6 unused enterprises from the database (keep only id=9 Бухара Сервис and id=7 Гарден Бухоро)
2. Switch the entire UI from dark theme to a clean light theme

---

## Step 0: Read files first (MANDATORY)

```
frontend/src/App.jsx
frontend/src/components/Layout/Sidebar.jsx
frontend/src/components/Layout/Header.jsx
frontend/src/components/Map/FieldMap.jsx
frontend/src/pages/DashboardPage.jsx
frontend/src/pages/AlertsPage.jsx
frontend/src/pages/FieldsPage.jsx
frontend/src/index.css
frontend/tailwind.config.js
```

Also find all enterprise-related pages:
```bash
grep -rn "enterprise\|Enterprise" frontend/src/ --include="*.jsx" -l
```

---

## Step 1: Delete unused enterprises from database

Create and run `backend/scripts/cleanup_enterprises.py`:

```python
"""
Delete unused enterprises and their related data.
Keep ONLY:
  - id=9  Бухара Сервис Агрокластер (BAK-08)
  - id=7  Гарден Бухоро Агрокластер (BAK-06)
Delete ALL others.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import SessionLocal

def cleanup():
    db = SessionLocal()
    keep_ids = [7, 9]
    
    try:
        # Show what will be deleted
        enterprises = db.execute(text("""
            SELECT e.id, e.name, e.code, 
                   (SELECT COUNT(*) FROM fields f WHERE f.enterprise_id = e.id) as field_count
            FROM enterprises e
            WHERE e.id NOT IN :keep_ids
            ORDER BY e.id
        """), {"keep_ids": tuple(keep_ids)}).fetchall()
        
        print("Enterprises to DELETE:")
        for e in enterprises:
            print(f"  id={e[0]}, {e[1]} ({e[2]}), {e[3]} fields")
        
        if not enterprises:
            print("Nothing to delete.")
            return
        
        delete_ids = [e[0] for e in enterprises]
        
        # Get field IDs for these enterprises (needed for cascading deletes)
        field_ids_result = db.execute(text("""
            SELECT id FROM fields WHERE enterprise_id = ANY(:ids)
        """), {"ids": delete_ids}).fetchall()
        field_ids = [r[0] for r in field_ids_result]
        
        print(f"\nFields to delete: {len(field_ids)}")
        
        if field_ids:
            # Delete alerts for these fields
            r = db.execute(text("DELETE FROM alerts WHERE field_id = ANY(:ids)"), {"ids": field_ids})
            print(f"Deleted {r.rowcount} alerts")
            
            # Delete NDVI records for these fields
            r = db.execute(text("DELETE FROM ndvi_records WHERE field_id = ANY(:ids)"), {"ids": field_ids})
            print(f"Deleted {r.rowcount} NDVI records")
            
            # Delete crop seasons for these fields
            r = db.execute(text("DELETE FROM crop_seasons WHERE field_id = ANY(:ids)"), {"ids": field_ids})
            print(f"Deleted {r.rowcount} crop seasons")
            
            # Delete scouting notes if table exists
            try:
                r = db.execute(text("DELETE FROM scouting_notes WHERE field_id = ANY(:ids)"), {"ids": field_ids})
                print(f"Deleted {r.rowcount} scouting notes")
            except Exception:
                pass
            
            # Delete the fields
            r = db.execute(text("DELETE FROM fields WHERE enterprise_id = ANY(:ids)"), {"ids": delete_ids})
            print(f"Deleted {r.rowcount} fields")
        
        # Delete the enterprises
        r = db.execute(text("DELETE FROM enterprises WHERE id = ANY(:ids)"), {"ids": delete_ids})
        print(f"Deleted {r.rowcount} enterprises")
        
        db.commit()
        
        # Verify
        remaining = db.execute(text("SELECT id, name, code FROM enterprises ORDER BY id")).fetchall()
        print(f"\nRemaining enterprises ({len(remaining)}):")
        for e in remaining:
            print(f"  id={e[0]}, {e[1]} ({e[2]})")
            
    except Exception as ex:
        db.rollback()
        print(f"ERROR: {ex}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    cleanup()
```

Run it:
```bash
cd C:\AgroSat\backend
python scripts/cleanup_enterprises.py
```

---

## Step 2: Light Theme — New Color Palette

### Update `tailwind.config.js`

Replace the `agro` color block with a light palette:

```javascript
'agro': {
  'dark': '#f8faf9',         // Main background — very light gray-green
  'panel': '#ffffff',         // Panel backgrounds — white
  'card': '#f1f5f3',          // Card/item backgrounds — light green-gray
  'border': '#e0e7e3',        // Borders — subtle green-gray
  'hover': '#e8eeea',         // Hover state
  'text': '#1a2e23',          // Primary text — dark green-black
  'muted': '#6b8578',         // Secondary/muted text — medium green-gray
  'accent': '#16a34a',        // Primary accent — green-600
  'accent-dim': '#bbf7d0',    // Dimmed accent — green-200
}
```

---

## Step 3: Light Theme — Update Sidebar.jsx

The sidebar should become light:
- Background: `bg-white` with a right border `border-r border-agro-border`
- Logo circle: `bg-agro-accent text-white` (green circle with white letter)
- Icon buttons: `text-agro-muted` default, `text-agro-accent` when active
- Active indicator: left bar stays `bg-agro-accent`
- Hover: `bg-agro-card`
- Tooltips: `bg-agro-text text-white` (dark tooltip on light UI)
- Bottom user avatar: `bg-agro-card text-agro-muted`

Find and replace all dark-theme classes in Sidebar.jsx:
- `bg-agro-dark` → `bg-white`
- `text-agro-text` → keep (it now maps to dark text)
- `text-agro-muted` → keep (it now maps to medium gray-green)
- `bg-agro-card` for tooltips → `bg-agro-text text-white` (invert for tooltips)
- `border-agro-border` → keep

---

## Step 4: Light Theme — Update Header.jsx

- Background: `bg-white/80 backdrop-blur-sm` (was `bg-agro-dark/80`)
- Text: `text-agro-text` (now maps to dark)
- Border bottom: `border-b border-agro-border`
- Alert badge: keep red styling

Find and replace:
- `bg-agro-dark/80` → `bg-white/80`
- Any `bg-agro-dark` → `bg-white`

---

## Step 5: Light Theme — Update App.jsx

- Root div: `bg-agro-dark` → this now maps to `#f8faf9` (light), so it should work automatically
- If there's any explicit dark color (`bg-[#0a0f0d]` or similar hardcoded dark values), replace with `bg-agro-dark`

---

## Step 6: Light Theme — Update FieldMap.jsx

### Map controls (style switcher, NDVI toggle)

Find the floating control buttons on the map. Update their styling:
- Container background: `bg-white/90 backdrop-blur-sm` (was dark)
- Button text: `text-agro-muted` default, `text-agro-text` or `bg-agro-accent text-white` when active
- Border: `border border-agro-border`
- Shadow: add `shadow-sm` for depth on the light background

### NDVI legend
- Background: `bg-white/90` (was dark)
- Text: `text-agro-text`

### Field label styling on map
Field labels need a text halo for readability on satellite imagery. Find the text layer style for field labels and ensure:
```javascript
'text-halo-color': '#ffffff',
'text-halo-width': 1.5,
'text-color': '#1a2e23'  // dark text
```

This keeps labels readable on both satellite and light map backgrounds.

---

## Step 7: Light Theme — Update DashboardPage.jsx

Find all dark-theme classes and update:
- Page background: should inherit from App (`bg-agro-dark` = light now)
- Cards: `bg-white border border-agro-border rounded-xl shadow-sm`
- Card text: `text-agro-text` for primary, `text-agro-muted` for secondary
- KPI numbers: keep colored (green for good, red for critical, orange for warnings)
- Alert list items: `bg-agro-card hover:bg-agro-hover border-b border-agro-border`
- Section headers: `text-agro-text font-semibold`

Search for all hardcoded dark colors:
```bash
grep -n "bg-\[#0\|bg-\[#1\|bg-gray-9\|bg-gray-8\|bg-slate-9\|bg-slate-8\|bg-zinc-9\|bg-zinc-8\|#0a0f\|#111\|#162\|#1e2" frontend/src/pages/DashboardPage.jsx
```

Replace any found with the appropriate `agro-*` token.

---

## Step 8: Light Theme — Update AlertsPage.jsx

- Page background: inherited (light)
- Filter pills: `bg-white border border-agro-border` default, `bg-agro-accent text-white` when active
- Alert cards: `bg-white border border-agro-border rounded-lg shadow-sm`
- Severity badges: keep existing colors (red for critical, orange for warning, blue for info)
- Search input: `bg-white border border-agro-border` with `focus:ring-agro-accent`
- NDVI value badges: keep their colored backgrounds (green/red)

---

## Step 9: Light Theme — Update Enterprise pages

Find all enterprise-related page files:
```bash
grep -rn "enterprise\|Enterprise" frontend/src/pages/ --include="*.jsx" -l
grep -rn "enterprise\|Enterprise" frontend/src/components/ --include="*.jsx" -l
```

Apply the same light theme pattern:
- `bg-agro-dark` backgrounds are now light (automatic via token change)
- Any hardcoded dark hex values → replace with tokens
- Cards: `bg-white border border-agro-border shadow-sm`
- Text: `text-agro-text` / `text-agro-muted`

Pay special attention to `EnterpriseDetailPage.jsx` — it has KPI cards, tables, tabs, modals. Make sure all use tokens, not hardcoded colors.

---

## Step 10: Light Theme — Update index.css

Update the scrollbar styling:
```css
::-webkit-scrollbar {
  width: 6px;
}
::-webkit-scrollbar-track {
  background: #f1f5f3;
}
::-webkit-scrollbar-thumb {
  background: #c8d5cc;
  border-radius: 3px;
}
::-webkit-scrollbar-thumb:hover {
  background: #a3b5a9;
}
```

Check if there are any other hardcoded dark colors in index.css and update them.

---

## Step 11: Light Theme — Check for any remaining dark colors

Run these searches to find stragglers:

```bash
# Hardcoded dark backgrounds
grep -rn "#0a0f0d\|#111916\|#162019\|#1e2d25\|#1a2b22" frontend/src/ --include="*.jsx" --include="*.js" --include="*.css"

# Dark tailwind utilities that shouldn't be in a light theme
grep -rn "bg-gray-900\|bg-gray-800\|bg-slate-900\|bg-slate-800\|bg-zinc-900\|bg-zinc-800\|bg-neutral-900\|bg-neutral-800" frontend/src/ --include="*.jsx"

# Old dark green backgrounds
grep -rn "bg-\[#0\|bg-\[#1" frontend/src/ --include="*.jsx"
```

Replace any found with appropriate `agro-*` tokens or standard light colors.

---

## Verification Checklist

- [ ] Only 2 enterprises remain in database (Бухара Сервис id=9, Гарден Бухоро id=7)
- [ ] App loads without errors on light background
- [ ] Sidebar is white/light with green accent for active item
- [ ] Dashboard page: white cards on light gray background, readable text
- [ ] Alerts page: white cards, colored severity badges still visible
- [ ] Enterprises page: shows only 2 enterprises
- [ ] Map page: satellite imagery default, fields visible with labels
- [ ] Map controls (style switcher, NDVI toggle) are light-themed and readable
- [ ] Enterprise detail page: light theme applied, KPI cards readable
- [ ] No hardcoded dark hex colors remaining in JSX files
- [ ] No console errors
- [ ] All navigation works

---

## CRITICAL WARNINGS

1. **DO NOT remove the `glyphs` property** from ANY map style object.
2. **DO NOT change the `onNavigate` callback system.**
3. **DO NOT break the field-centroids source** for map labels.
4. **Preserve ALL existing functionality** — this is a theme change + DB cleanup, not a feature change.
5. **The token names stay the same** (`agro-dark`, `agro-panel`, etc.) — only the VALUES change from dark to light. This means most components will update automatically just from the tailwind.config.js change. Only components with hardcoded colors or inverted logic (like dark tooltips) need manual fixes.
