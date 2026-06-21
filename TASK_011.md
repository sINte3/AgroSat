# TASK_011: Fix Field Lookup (code vs ID) + Enterprises Frontend

## Root Cause Analysis

The frontend passes `field.code` (e.g. "8567") to API endpoints, but the backend
does `WHERE id = 8567` which fails because the database primary key `id` is an
auto-increment integer (1, 2, 3...), NOT the field code.

Fix: make NDVI and weather endpoints look up by EITHER `id` OR `code`.

---

## STEP 1: Read current files (MANDATORY — do this first)

Read and print these files completely before making any changes:

```
backend/api/ndvi.py
backend/api/weather.py
frontend/src/components/Field/NDVIChart.jsx
frontend/src/components/Field/WeatherWidget.jsx
frontend/src/pages/EnterprisesPage.jsx
frontend/src/components/Field/FieldDetail.jsx
```

---

## STEP 2: Fix backend/api/ndvi.py — field lookup

In `backend/api/ndvi.py`, find the `get_ndvi_history` function.

Find the SQL query that looks up the field (it will look something like):
```python
field_row = db.execute(
    text("SELECT id, name FROM fields WHERE id = :id"),
    {"id": field_id}
).fetchone()
```

Replace ONLY that query with:
```python
field_row = db.execute(
    text("SELECT id, name FROM fields WHERE id = :fid OR code = CAST(:fid AS TEXT) LIMIT 1"),
    {"fid": field_id}
).fetchone()
```

IMPORTANT: After this query, use `field_row.id` (the real DB primary key) in ALL subsequent queries. Find the ndvi_records query and make sure it uses `field_row.id`:

```python
records = db.execute(text("""
    SELECT ...
    FROM ndvi_records
    WHERE field_id = :field_id
    ...
"""), {"field_id": field_row.id, ...}).fetchall()
```

Also update the return to use `field_row.id`:
```python
return {
    "field_id": field_row.id,
    ...
}
```

Do the same fix for `get_ndvi_latest` function — find its field lookup query and replace with the same pattern.

Do the same fix for `refresh_ndvi` function if it exists.

---

## STEP 3: Fix backend/api/weather.py — field lookup

In `backend/api/weather.py`, find the `get_weather_for_field` function.

Find the SQL query that looks up the field. Replace ONLY that query with:
```python
result = db.execute(
    text("SELECT id, name, centroid_lat, centroid_lon FROM fields WHERE id = :fid OR code = CAST(:fid AS TEXT) LIMIT 1"),
    {"fid": field_id}
).fetchone()
```

Keep everything else the same.

---

## STEP 4: Fix Enterprises frontend

Read `frontend/src/pages/EnterprisesPage.jsx`.

The API at `/api/enterprises/` returns data successfully (confirmed by browser test).
The frontend shows "Не удалось загрузить" — so the component has a bug.

Common issues to check and fix:

### Issue A: Wrong API URL
Find the API call. It must be exactly:
```javascript
apiClient.get('/api/enterprises/')
```
NOT `/enterprises/` or `/api/enterprises` (without trailing slash).

### Issue B: Response parsing
The API returns a plain JSON array: `[{...}, {...}]`
With axios, the data is in `response.data`. Make sure:
```javascript
.then(response => {
  setEnterprises(response.data);  // response.data is the array
})
```
NOT `response.data.enterprises` or `response.data.data`.

### Issue C: Missing error details
Replace the catch block to log the actual error:
```javascript
.catch(err => {
  console.error('Enterprises error:', err.response?.status, err.response?.data, err.message);
  setError('Не удалось загрузить список предприятий');
})
```

### Issue D: Component might not exist or have syntax error
If EnterprisesPage.jsx doesn't exist or is empty, create it with this minimal working version:

```jsx
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import apiClient from '../api/client';

function ndviColor(v) {
  if (!v || v < 0.2) return '#ef4444';
  if (v < 0.35) return '#f97316';
  if (v < 0.5) return '#eab308';
  if (v < 0.65) return '#84cc16';
  return '#4ade80';
}

export default function EnterprisesPage() {
  const [enterprises, setEnterprises] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    setLoading(true);
    apiClient.get('/api/enterprises/')
      .then(res => {
        console.log('Enterprises loaded:', res.data);
        const data = Array.isArray(res.data) ? res.data : [];
        setEnterprises(data);
        setError(null);
      })
      .catch(err => {
        console.error('Enterprises API error:', err.response?.status, err.response?.data, err.message);
        setError('Не удалось загрузить список предприятий');
      })
      .finally(() => setLoading(false));
  }, []);

  const filtered = enterprises.filter(e =>
    (e.name || '').toLowerCase().includes(search.toLowerCase()) ||
    (e.code || '').toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div style={{ padding: '24px', color: '#e2e8f0', height: '100%', overflowY: 'auto' }}>
      <h1 style={{ fontSize: 24, fontWeight: 700, margin: '0 0 4px' }}>Предприятия</h1>
      <p style={{ fontSize: 14, color: '#64748b', marginBottom: 20 }}>
        Дочерние предприятия Бухоро Агрокластер
      </p>

      <input
        value={search}
        onChange={e => setSearch(e.target.value)}
        placeholder="Поиск предприятия..."
        style={{
          width: '100%', maxWidth: 400, marginBottom: 20,
          background: '#132913', border: '1px solid #1e3520',
          borderRadius: 8, padding: '10px 14px',
          color: '#e2e8f0', fontSize: 14, outline: 'none'
        }}
      />

      {error && (
        <div style={{ color: '#ef4444', marginBottom: 16 }}>{error}</div>
      )}

      {loading ? (
        <div style={{ color: '#64748b' }}>Загрузка...</div>
      ) : (
        <>
          <div style={{ fontSize: 13, color: '#64748b', marginBottom: 12 }}>
            {filtered.length} из {enterprises.length} предприятий
          </div>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
            gap: 16
          }}>
            {filtered.map(e => (
              <div
                key={e.id}
                onClick={() => navigate(`/fields?enterprise_id=${e.id}`)}
                style={{
                  background: '#132913', border: '1px solid #1e3520',
                  borderRadius: 12, padding: 20, cursor: 'pointer',
                  transition: 'border-color 0.15s'
                }}
                onMouseEnter={ev => ev.currentTarget.style.borderColor = '#4ade80'}
                onMouseLeave={ev => ev.currentTarget.style.borderColor = '#1e3520'}
              >
                <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>{e.name}</div>
                <div style={{ fontSize: 12, color: '#64748b', marginBottom: 12 }}>
                  {e.code} • {e.region}
                </div>
                <div style={{ display: 'flex', gap: 16 }}>
                  <div>
                    <div style={{ fontSize: 24, fontWeight: 700, color: '#4ade80' }}>
                      {e.total_fields}
                    </div>
                    <div style={{ fontSize: 11, color: '#64748b' }}>Полей</div>
                  </div>
                </div>
                <div style={{ marginTop: 12, fontSize: 12, color: '#4ade80' }}>
                  Смотреть на карте →
                </div>
              </div>
            ))}
          </div>
          {filtered.length === 0 && (
            <div style={{ textAlign: 'center', color: '#64748b', padding: 40 }}>
              Предприятия не найдены
            </div>
          )}
        </>
      )}
    </div>
  );
}
```

---

## STEP 5: Verify the `apiClient` import path

Read `frontend/src/pages/EnterprisesPage.jsx` after your edits.
Verify the import path for apiClient matches the actual file location.

Check what other pages use:
```bash
grep -r "import.*apiClient\|import.*client" frontend/src/pages/
```

The import must match exactly. Common patterns:
- `import apiClient from '../api/client';`
- `import { apiClient } from '../api/client';`
- `import api from '../api/client';`

Use whatever pattern the OTHER pages use (DashboardPage.jsx, FieldsPage.jsx).

---

## STEP 6: Test

After saving all files, test these URLs:

1. `http://localhost:8000/api/ndvi/8567/history?days=90`
   → Must return `{"field_id": <real_db_id>, "records": [...], "count": N}`
   → NOT `"Поле 8567 не найдено"`

2. `http://localhost:8000/api/weather/field/8567`
   → Must return weather data with `current` and `forecast`
   → NOT `"Поле не найдено"`

3. Open `http://localhost:5173/enterprises`
   → Must show 9 enterprise cards (check browser console F12 for errors)

---

## CRITICAL RULES

- In backend: ONLY change the field lookup SQL queries (the WHERE clause)
- Make sure to use `field_row.id` (real DB id) in subsequent queries, NOT the passed `field_id` parameter
- In frontend: check import paths match what other pages use
- DO NOT change router prefixes, they are correct now
- DO NOT rewrite entire files — make targeted edits only
