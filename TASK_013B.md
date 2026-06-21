# TASK_013B — Fix table column widths in EnterpriseFieldsTable

## Skills to load
- `/mnt/skills/user/full-output-enforcement/SKILL.md`

## Problem

In `frontend/src/components/Enterprise/EnterpriseFieldsTable.jsx` the table columns are uneven:
- "Площадь (га)" column is too wide (stretches to fill space)
- "NDVI" column is cut off on the right edge and not fully visible
- "Название" column wraps text unnecessarily

## Fix: Set explicit fixed widths on every column

Find the `<table>` element and add `table-layout: fixed` with `width: 100%`.
Then set explicit widths on each `<th>` in the `<thead>`:

```jsx
<table style={{
  width: '100%',
  borderCollapse: 'collapse',
  fontSize: 13,
  tableLayout: 'fixed',   // ADD THIS — prevents columns from auto-stretching
}}>
  <thead>
    <tr>
      <th style={{ width: 80, ...headerStyle }}>Код поля</th>
      <th style={{ width: 'auto', ...headerStyle }}>Название</th>
      <th style={{ width: 110, ...headerStyle }}>Культура</th>
      <th style={{ width: 90, textAlign: 'right', ...headerStyle }}>Площадь (га)</th>
      <th style={{ width: 80, textAlign: 'center', ...headerStyle }}>NDVI</th>
      <th style={{ width: 60, textAlign: 'center', ...headerStyle }}>Алерты</th>
    </tr>
  </thead>
```

Where `headerStyle` is whatever existing header styles are used (background, padding, color, etc.).

## Fix: Prevent text overflow in cells

For the "Название" cell, prevent wrapping that makes rows too tall:

```jsx
<td style={{
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  maxWidth: 0,   // required for ellipsis to work in table-layout: fixed
  ...existingCellStyle
}}>
  {field.name}
</td>
```

Apply `overflow: hidden; textOverflow: ellipsis; whiteSpace: nowrap` to ALL cells so no cell wraps or overflows.

## Fix: Ensure the table wrapper doesn't clip NDVI column

Find the outer `<div>` wrapping the table. Make sure it has:
```jsx
<div style={{
  overflowX: 'auto',     // horizontal scroll if needed, not clip
  overflowY: 'auto',
  width: '100%',
  // DO NOT set a fixed width that is smaller than the table's minimum content width
}}>
```

## Fix: NDVI cell — full value visible

The NDVI badge/cell must show the full value (e.g. "-0.234") without being cut off:

```jsx
<td style={{
  textAlign: 'center',
  padding: '8px 6px',
  width: 80,
}}>
  <span style={{
    display: 'inline-block',
    padding: '3px 8px',
    borderRadius: 4,
    fontSize: 12,
    fontWeight: 600,
    minWidth: 56,
    textAlign: 'center',
    background: ndviBackground,
    color: '#fff',
  }}>
    {field.current_ndvi != null ? field.current_ndvi.toFixed(3) : '—'}
  </span>
</td>
```

## Expected result

After the fix the table should look like this (proportional columns, nothing cut off):

```
┌──────────┬──────────────────────┬────────────┬──────────────┬────────┬────────┐
│ Код поля │ Название             │ Культура   │ Площадь (га) │  NDVI  │ Алерты │
│  80px    │       auto           │  110px     │    90px      │  80px  │  60px  │
├──────────┼──────────────────────┼────────────┼──────────────┼────────┼────────┤
│ 8691     │ 8691 пахта очик 2026 │ Хлопок     │          3.4 │ -0.234 │   🔴   │
│ 8567     │ 8567 пахта очик 2026 │ Хлопок     │          7.9 │ -0.066 │   🔴   │
└──────────┴──────────────────────┴────────────┴──────────────┴────────┴────────┘
```

## Only touch this file
`frontend/src/components/Enterprise/EnterpriseFieldsTable.jsx`

Do not modify any other files.
