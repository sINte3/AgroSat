# TASK_013C — Table columns redesign: EnterpriseFieldsTable

## Skills to load
- `/mnt/skills/user/full-output-enforcement/SKILL.md`

## Only touch this file
`frontend/src/components/Enterprise/EnterpriseFieldsTable.jsx`

---

## Change 1: Remove "Код поля" column

Remove the "Код поля" `<th>` header and the corresponding `<td>` cell from every row.
The table will now have 5 columns only: Контур, Культура, Площадь (га), NDVI, Предупреждения.

---

## Change 2: Rename "Название" → "Контур"

```jsx
// BEFORE:
<th>Название</th>

// AFTER:
<th>Контур</th>
```

---

## Change 3: Rename "Алерты" → "Предупреждения"

```jsx
// BEFORE:
<th>Алерты</th>

// AFTER:
<th>Предупреждения</th>
```

---

## Change 4: Crop name display mapping

When rendering the crop name in the "Культура" cell, apply this mapping:

```javascript
function formatCropName(cropName) {
  if (!cropName) return '—';
  const name = cropName.toLowerCase();
  if (name.includes('томчи') || name.includes('tomchi') || name.includes('капельн') || name.includes('drip')) {
    return 'Хлопок капля';
  }
  if (name.includes('очик') || name.includes('ochiq') || name.includes('полив') || name.includes('open')) {
    return 'Хлопок полив';
  }
  if (name.includes('галла') || name.includes('galla') || name.includes('пшениц') || name.includes('wheat')) {
    return 'Пшеница';
  }
  if (name.includes('люцерн') || name.includes('alfalfa')) {
    return 'Люцерна';
  }
  if (name.includes('кукуруз') || name.includes('maize') || name.includes('corn')) {
    return 'Кукуруза';
  }
  if (name.includes('рис') || name.includes('rice')) {
    return 'Рис';
  }
  // fallback: return as-is but capitalize
  return cropName.charAt(0).toUpperCase() + cropName.slice(1);
}

// Usage in cell:
<td>{formatCropName(field.current_crop)}</td>
```

---

## Change 5: Fixed column widths (5 columns, balanced)

Use `table-layout: fixed` with these exact widths:

```jsx
<table style={{
  width: '100%',
  borderCollapse: 'collapse',
  tableLayout: 'fixed',
  fontSize: 12,
  fontFamily: "'Inter', 'Segoe UI', system-ui, sans-serif",
}}>
  <colgroup>
    <col style={{ width: '30%' }} />   {/* Контур */}
    <col style={{ width: '22%' }} />   {/* Культура */}
    <col style={{ width: '16%' }} />   {/* Площадь (га) */}
    <col style={{ width: '16%' }} />   {/* NDVI */}
    <col style={{ width: '16%' }} />   {/* Предупреждения */}
  </colgroup>
```

---

## Change 6: Typography — no text shifting, clean alignment

### Header row:
```jsx
const thStyle = {
  padding: '10px 8px',
  fontSize: 11,
  fontWeight: 600,
  letterSpacing: '0.04em',
  textTransform: 'uppercase',
  color: '#64748b',
  background: '#0a1509',
  textAlign: 'left',
  borderBottom: '1px solid #1e3520',
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  userSelect: 'none',
};
```

### Data cells — all cells must have consistent padding and no wrapping:
```jsx
const tdStyle = {
  padding: '9px 8px',
  fontSize: 12,
  lineHeight: '1.3',
  color: '#e2e8f0',
  borderBottom: '1px solid #0f1b0d',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  verticalAlign: 'middle',
};
```

### Numeric columns (Площадь, NDVI) — right-align numbers, monospace font:
```jsx
// Площадь cell:
<td style={{ ...tdStyle, textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontFamily: 'monospace' }}>
  {field.area_ha != null ? field.area_ha.toFixed(1) : '—'}
</td>

// NDVI cell — colored badge:
<td style={{ ...tdStyle, textAlign: 'center' }}>
  <span style={{
    display: 'inline-block',
    padding: '2px 7px',
    borderRadius: 4,
    fontSize: 11,
    fontWeight: 700,
    fontFamily: "'Roboto Mono', 'Courier New', monospace",
    fontVariantNumeric: 'tabular-nums',
    minWidth: 52,
    textAlign: 'center',
    background: getNDVIBackground(field.current_ndvi),
    color: '#fff',
  }}>
    {field.current_ndvi != null ? field.current_ndvi.toFixed(3) : '—'}
  </span>
</td>
```

### Предупреждения cell — center-aligned badge:
```jsx
<td style={{ ...tdStyle, textAlign: 'center' }}>
  {alertCount > 0 ? (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 3,
      background: alertCount > 0 && hasCritical ? '#7f1d1d' : '#78350f',
      color: '#fff',
      borderRadius: 10,
      padding: '2px 8px',
      fontSize: 11,
      fontWeight: 600,
      minWidth: 28,
    }}>
      {hasCritical ? '🔴' : '🟡'} {alertCount}
    </span>
  ) : (
    <span style={{ color: '#374151', fontSize: 12 }}>—</span>
  )}
</td>
```

---

## Change 7: Center the table container

The outer wrapper `<div>` wrapping the whole table section should be centered:

```jsx
<div style={{
  width: '100%',
  overflowX: 'auto',
  overflowY: 'auto',
  maxHeight: 'calc(100vh - 400px)',
  borderRadius: 8,
  border: '1px solid #1e3520',
  // Table is 100% width inside — it will naturally fill the panel width
}}>
```

---

## Expected final result

```
┌──────────────────────┬──────────────┬────────────┬────────┬────────────────┐
│ КОНТУР               │ КУЛЬТУРА     │ ПЛОЩАДЬ ГА │  NDVI  │ ПРЕДУПРЕЖДЕНИЯ │
│ 30%                  │ 22%          │ 16%        │  16%   │     16%        │
├──────────────────────┼──────────────┼────────────┼────────┼────────────────┤
│ 8691 пахта очик 2026 │ Хлопок полив │        3.4 │ -0.234 │    🔴 1        │
│ 8567 пахта очик 2026 │ Хлопок полив │        7.9 │ -0.066 │    🔴 1        │
│ 8738-3 галла 2026    │ Пшеница      │        8.7 │ -0.032 │      —         │
└──────────────────────┴──────────────┴────────────┴────────┴────────────────┘
```

All text fits, no clipping, no column shifting.
