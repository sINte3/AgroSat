# TASK_008: Enterprises Page + Performance Optimizations

## Overview

Two things to implement:
1. A working `/enterprises` page (currently the nav button does nothing)
2. Performance optimizations: sessionStorage GeoJSON cache + fields list pagination

All UI text in Russian. Dark theme: bg `#0f1b0d`, card `#132913`, border `#1e3520`, accent `#4ade80`.

---

## TASK 1: Create Enterprises Page

### File: `frontend/src/pages/EnterprisesPage.jsx`

Create this file from scratch.

**What it does:**
- Fetches all enterprises from `GET /api/enterprises/`
- For each enterprise, fetches its dashboard summary from `GET /api/dashboard/enterprises/{id}`
- Renders a grid of enterprise cards with KPIs
- Clicking an enterprise filters the Fields map to show only its fields

**API: GET /api/enterprises/**
```json
[
  {
    "id": 1,
    "name": "Гарден Бухоро Агрокластер",
    "code": "BAK-01",
    "region": "Buxoro viloyati",
    "total_fields": 94
  }
]
```

**API: GET /api/dashboard/enterprises/{id}**
```json
{
  "enterprise_id": 1,
  "enterprise_name": "Гарден Бухоро Агрокластер",
  "total_fields": 94,
  "active_alerts": 40,
  "critical_alerts": 20,
  "avg_ndvi": 0.3241,
  "fields_with_problems": 25,
  "last_updated": "2026-06-16T05:00:00"
}
```

**Component implementation:**

```jsx
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import apiClient from '../api/client';

// Цвет по среднему NDVI
function ndviColor(v) {
  if (!v || v < 0.2) return '#ef4444';
  if (v < 0.35) return '#f97316';
  if (v < 0.5) return '#eab308';
  if (v < 0.65) return '#84cc16';
  return '#4ade80';
}

// Скелетон для карточки
function CardSkeleton() {
  return (
    <div style={{
      background: '#132913', border: '1px solid #1e3520',
      borderRadius: 12, padding: 20,
      animation: 'pulse 1.5s ease-in-out infinite'
    }}>
      <div style={{ height: 20, background: '#1e3520', borderRadius: 4, marginBottom: 12, width: '70%' }} />
      <div style={{ height: 14, background: '#1e3520', borderRadius: 4, marginBottom: 20, width: '40%' }} />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        {[...Array(4)].map((_, i) => (
          <div key={i} style={{ height: 52, background: '#1e3520', borderRadius: 8 }} />
        ))}
      </div>
    </div>
  );
}

// Карточка предприятия
function EnterpriseCard({ enterprise, onSelect }) {
  const [summary, setSummary] = useState(null);
  const [loadingSummary, setLoadingSummary] = useState(true);

  useEffect(() => {
    apiClient.get(`/api/dashboard/enterprises/${enterprise.id}`)
      .then(r => setSummary(r.data))
      .catch(() => setSummary(null))
      .finally(() => setLoadingSummary(false));
  }, [enterprise.id]);

  const ndvi = summary?.avg_ndvi;
  const hasCritical = summary?.critical_alerts > 0;

  return (
    <div
      onClick={() => onSelect(enterprise)}
      style={{
        background: '#132913',
        border: `1px solid ${hasCritical ? '#7f1d1d' : '#1e3520'}`,
        borderRadius: 12,
        padding: 20,
        cursor: 'pointer',
        transition: 'all 0.15s',
        position: 'relative',
        overflow: 'hidden',
      }}
      onMouseEnter={e => {
        e.currentTarget.style.border = `1px solid #4ade80`;
        e.currentTarget.style.background = '#1a3520';
      }}
      onMouseLeave={e => {
        e.currentTarget.style.border = `1px solid ${hasCritical ? '#7f1d1d' : '#1e3520'}`;
        e.currentTarget.style.background = '#132913';
      }}
    >
      {/* Бейдж критических алертов */}
      {hasCritical && (
        <div style={{
          position: 'absolute', top: 12, right: 12,
          background: '#ef4444', color: '#fff',
          fontSize: 11, fontWeight: 700,
          padding: '2px 8px', borderRadius: 20,
        }}>
          ⚠ {summary.critical_alerts} крит.
        </div>
      )}

      {/* Название */}
      <div style={{ fontSize: 15, fontWeight: 600, color: '#e2e8f0', marginBottom: 4, paddingRight: hasCritical ? 80 : 0 }}>
        {enterprise.name}
      </div>
      <div style={{ fontSize: 12, color: '#64748b', marginBottom: 16 }}>
        {enterprise.code} • {enterprise.region}
      </div>

      {loadingSummary ? (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {[...Array(4)].map((_, i) => (
            <div key={i} style={{ height: 52, background: '#1e3520', borderRadius: 8 }} />
          ))}
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {/* Поля */}
          <div style={{ background: '#0f1b0d', borderRadius: 8, padding: '10px 12px' }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: '#e2e8f0' }}>
              {summary?.total_fields ?? enterprise.total_fields ?? '—'}
            </div>
            <div style={{ fontSize: 11, color: '#64748b' }}>Полей</div>
          </div>

          {/* NDVI */}
          <div style={{ background: '#0f1b0d', borderRadius: 8, padding: '10px 12px' }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: ndviColor(ndvi) }}>
              {ndvi != null ? ndvi.toFixed(3) : '—'}
            </div>
            <div style={{ fontSize: 11, color: '#64748b' }}>Средний NDVI</div>
          </div>

          {/* Алерты */}
          <div style={{ background: '#0f1b0d', borderRadius: 8, padding: '10px 12px' }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: summary?.active_alerts > 0 ? '#f97316' : '#4ade80' }}>
              {summary?.active_alerts ?? '—'}
            </div>
            <div style={{ fontSize: 11, color: '#64748b' }}>Алертов</div>
          </div>

          {/* Проблемные поля */}
          <div style={{ background: '#0f1b0d', borderRadius: 8, padding: '10px 12px' }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: summary?.fields_with_problems > 0 ? '#ef4444' : '#4ade80' }}>
              {summary?.fields_with_problems ?? '—'}
            </div>
            <div style={{ fontSize: 11, color: '#64748b' }}>С проблемами</div>
          </div>
        </div>
      )}

      <div style={{ marginTop: 14, fontSize: 12, color: '#4ade80', display: 'flex', alignItems: 'center', gap: 4 }}>
        Смотреть на карте →
      </div>
    </div>
  );
}

export default function EnterprisesPage() {
  const [enterprises, setEnterprises] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    apiClient.get('/api/enterprises/')
      .then(r => setEnterprises(r.data))
      .catch(() => setError('Не удалось загрузить список предприятий'))
      .finally(() => setLoading(false));
  }, []);

  // При клике → открываем страницу Поля с фильтром по предприятию
  function handleSelect(enterprise) {
    navigate(`/fields?enterprise_id=${enterprise.id}`);
  }

  const filtered = enterprises.filter(e =>
    e.name.toLowerCase().includes(search.toLowerCase()) ||
    e.code?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div style={{ padding: '0 24px 24px', color: '#e2e8f0', height: '100%', overflowY: 'auto' }}>
      {/* Заголовок */}
      <div style={{ paddingTop: 24, marginBottom: 24 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, color: '#e2e8f0', margin: 0 }}>
          Предприятия
        </h1>
        <p style={{ fontSize: 14, color: '#64748b', marginTop: 4 }}>
          Дочерние предприятия Бухоро Агрокластер
        </p>
      </div>

      {/* Поиск */}
      <div style={{ marginBottom: 20 }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="🔍  Поиск предприятия..."
          style={{
            width: '100%', maxWidth: 400,
            background: '#132913', border: '1px solid #1e3520',
            borderRadius: 8, padding: '10px 14px',
            color: '#e2e8f0', fontSize: 14,
            outline: 'none', boxSizing: 'border-box'
          }}
        />
      </div>

      {error && (
        <div style={{ color: '#ef4444', padding: '20px 0', fontSize: 14 }}>{error}</div>
      )}

      {loading ? (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
          gap: 16
        }}>
          {[...Array(6)].map((_, i) => <CardSkeleton key={i} />)}
        </div>
      ) : (
        <>
          <div style={{ fontSize: 13, color: '#64748b', marginBottom: 12 }}>
            {filtered.length} из {enterprises.length} предприятий
          </div>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
            gap: 16
          }}>
            {filtered.map(e => (
              <EnterpriseCard
                key={e.id}
                enterprise={e}
                onSelect={handleSelect}
              />
            ))}
            {filtered.length === 0 && (
              <div style={{ color: '#64748b', padding: '40px 0', fontSize: 14, gridColumn: '1/-1', textAlign: 'center' }}>
                Предприятия не найдены
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
```

---

### File: `frontend/src/App.jsx` (or wherever routes are defined)

Add the import and route for EnterprisesPage:

```jsx
import EnterprisesPage from './pages/EnterprisesPage';

// Inside the router/routes config, add:
<Route path="/enterprises" element={<EnterprisesPage />} />
```

If the app uses React Router v6 with `<Routes>`, it will look like:
```jsx
<Routes>
  {/* existing routes */}
  <Route path="/" element={<DashboardPage />} />
  <Route path="/fields" element={<FieldsPage />} />
  <Route path="/alerts" element={<AlertsPage />} />
  <Route path="/enterprises" element={<EnterprisesPage />} />   {/* ADD THIS */}
  <Route path="/fields/:id" element={<FieldDetailPage />} />
</Routes>
```

Read `frontend/src/App.jsx` first to find the exact location before editing.

---

### File: `frontend/src/components/Layout/Sidebar.jsx`

Verify the Enterprises nav link uses the correct path. Find the enterprise nav item and make sure it links to `/enterprises`:

```jsx
// It should look like this:
<NavLink to="/enterprises">
  {/* icon + "Предприятия" label */}
</NavLink>
```

If it's using `onClick` with a missing handler, replace with `<NavLink to="/enterprises">`.

---

## TASK 2: Performance Optimization — GeoJSON sessionStorage cache

This is the biggest win for load time. The `/api/fields/geojson/all` response (all 278 field polygons + labels) is fetched on every page refresh. Cache it in sessionStorage for 5 minutes.

### File: `frontend/src/api/client.js`

Find where `apiClient` is exported and add this cache helper at the top of the file:

```javascript
const GEO_CACHE_KEY = 'agrosat_geo_v1';
const GEO_CACHE_TTL = 5 * 60 * 1000; // 5 минут в миллисекундах

export async function fetchFieldsGeoJson() {
  try {
    const raw = sessionStorage.getItem(GEO_CACHE_KEY);
    if (raw) {
      const { data, ts } = JSON.parse(raw);
      if (Date.now() - ts < GEO_CACHE_TTL) {
        console.log('[GeoCache] Из кеша');
        return data;
      }
    }
  } catch (e) { /* игнорировать ошибки sessionStorage */ }

  console.log('[GeoCache] Загружаем с сервера...');
  const res = await apiClient.get('/api/fields/geojson/all');
  try {
    sessionStorage.setItem(GEO_CACHE_KEY, JSON.stringify({ data: res.data, ts: Date.now() }));
  } catch (e) { /* sessionStorage переполнен — работаем без кеша */ }
  return res.data;
}

// Сбросить кеш (вызывать после ручного обновления полей)
export function clearGeoCache() {
  sessionStorage.removeItem(GEO_CACHE_KEY);
}
```

### File: `frontend/src/components/Map/FieldMap.jsx`

Find where the map component fetches GeoJSON (likely `apiClient.get('/api/fields/geojson/all')`).

Replace that call with the cached version:

```javascript
import { fetchFieldsGeoJson } from '../../api/client';

// Inside the useEffect/loadData function, replace:
// const res = await apiClient.get('/api/fields/geojson/all');
// const geojson = res.data;

// With:
const geojson = await fetchFieldsGeoJson();
```

---

## TASK 3: Performance Optimization — Paginate Fields List

The FieldsPage currently renders ALL 278 field cards at once. This makes the right panel sluggish to scroll.

### File: `frontend/src/pages/FieldsPage.jsx`

Find the section that renders the field cards list. Add simple "показать ещё" pagination:

```jsx
// Add at top of FieldsPage component:
const [visibleCount, setVisibleCount] = useState(30);

// In the render, slice the fields array:
const visibleFields = filteredFields.slice(0, visibleCount);

// Render only visible fields:
{visibleFields.map(field => (
  <FieldCard key={field.id} field={field} ... />
))}

// Add "Показать ещё" button at the bottom of the list:
{visibleCount < filteredFields.length && (
  <button
    onClick={() => setVisibleCount(prev => prev + 30)}
    style={{
      width: '100%', padding: '12px',
      background: '#132913', border: '1px solid #1e3520',
      borderRadius: 8, color: '#4ade80',
      cursor: 'pointer', fontSize: 14, marginTop: 8
    }}
  >
    Показать ещё ({filteredFields.length - visibleCount} осталось)
  </button>
)}
```

Also reset visibleCount when search/filter changes:
```jsx
// Add to the useEffect or handler where filters change:
useEffect(() => {
  setVisibleCount(30);
}, [searchQuery, selectedEnterprise]); // adjust variable names to match the file
```

---

## TASK 4: Verify backend /api/enterprises/ endpoint

Check that `backend/api/enterprises.py` exists and has `GET /api/enterprises/` returning a list.

If the endpoint doesn't exist, create `backend/api/enterprises.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/enterprises", tags=["enterprises"])

@router.get("/")
async def list_enterprises(db: Session = Depends(get_db)):
    """Список всех предприятий с количеством полей"""
    rows = db.execute(text("""
        SELECT 
            e.id,
            e.name,
            e.code,
            e.region,
            COUNT(f.id) as total_fields
        FROM enterprises e
        LEFT JOIN fields f ON f.enterprise_id = e.id
        GROUP BY e.id, e.name, e.code, e.region
        ORDER BY e.name
    """)).fetchall()
    
    return [
        {
            "id": r.id,
            "name": r.name,
            "code": r.code,
            "region": r.region,
            "total_fields": r.total_fields,
        }
        for r in rows
    ]

@router.get("/{enterprise_id}")
async def get_enterprise(enterprise_id: int, db: Session = Depends(get_db)):
    """Предприятие + его поля"""
    row = db.execute(
        text("SELECT id, name, code, region FROM enterprises WHERE id = :id"),
        {"id": enterprise_id}
    ).fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail=f"Предприятие {enterprise_id} не найдено")
    
    fields = db.execute(text("""
        SELECT f.id, f.name, f.code, f.area_ha,
               n.mean_ndvi as current_ndvi, n.captured_date as last_ndvi_date
        FROM fields f
        LEFT JOIN LATERAL (
            SELECT mean_ndvi, captured_date 
            FROM ndvi_records 
            WHERE field_id = f.id 
            ORDER BY captured_date DESC 
            LIMIT 1
        ) n ON true
        WHERE f.enterprise_id = :eid
        ORDER BY f.name
    """), {"eid": enterprise_id}).fetchall()
    
    return {
        "id": row.id,
        "name": row.name,
        "code": row.code,
        "region": row.region,
        "fields": [
            {
                "id": f.id,
                "name": f.name,
                "code": f.code,
                "area_ha": f.area_ha,
                "current_ndvi": float(f.current_ndvi) if f.current_ndvi else None,
                "last_ndvi_date": str(f.last_ndvi_date) if f.last_ndvi_date else None,
            }
            for f in fields
        ]
    }
```

Then in `backend/main.py`, make sure the router is included:
```python
from api.enterprises import router as enterprises_router
app.include_router(enterprises_router)
```

---

## TASK 5: Smoke test

After completing all tasks, verify:

1. Click **"Предприятия"** in the sidebar → `/enterprises` page loads with enterprise cards
2. Each card shows: name, code, total fields count, avg NDVI (colored), alerts count
3. Click an enterprise card → navigates to `/fields?enterprise_id=N` (fields page filtered)
4. Open browser DevTools → Network tab → refresh the fields page twice → the second time `/api/fields/geojson/all` should NOT appear in network requests (served from sessionStorage cache)
5. On the Fields page, only 30 cards are rendered initially, "Показать ещё" button appears at the bottom
