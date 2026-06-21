# TASK_024: Enhanced Field Detail Mode in Panel

## Goal
When a user clicks a field in the left panel (FieldListPanel), the panel switches to a rich detail view showing NDVI history chart, crop info, active alerts, and weather. The map zooms/flies to the selected field and highlights it. This makes the detail mode actually useful — not just a placeholder.

---

## Step 0: Read files first (MANDATORY)

Read ALL of these before making any changes:

```
frontend/src/components/Map/FieldListPanel.jsx
frontend/src/components/Map/FieldMap.jsx
frontend/src/pages/FieldsPage.jsx
frontend/src/components/Field/NDVIChart.jsx
frontend/src/components/Field/WeatherWidget.jsx
frontend/src/api/client.js
```

Also verify what the API returns for a single field:
```bash
curl http://localhost:8000/api/fields/1 2>nul
curl "http://localhost:8000/api/ndvi/1/history?days=90" 2>nul
curl http://localhost:8000/api/alerts/field/1 2>nul
curl "http://localhost:8000/api/weather/field/1" 2>nul
```

Check the exact alert endpoint path — it might be `/api/alerts/?field_id=1` or `/api/alerts/field/1`. Use whichever one returns data.

---

## Step 1: Create FieldDetailPanel sub-component

Create `frontend/src/components/Map/FieldDetailPanel.jsx`

This is the detail view that renders INSIDE FieldListPanel when a field is selected. It replaces the field list content.

```jsx
import React, { useState, useEffect } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine
} from 'recharts';
import apiClient from '../../api/client';

// --- NDVI color helper ---
const getNdviColor = (v) => {
  if (v == null) return '#9ca3af';
  if (v < 0.15) return '#dc2626';
  if (v < 0.3) return '#f97316';
  if (v < 0.45) return '#eab308';
  if (v < 0.6) return '#84cc16';
  return '#16a34a';
};

const getNdviLabel = (v) => {
  if (v == null) return 'Нет данных';
  if (v < 0.15) return 'Критический';
  if (v < 0.3) return 'Слабый';
  if (v < 0.45) return 'Умеренный';
  if (v < 0.6) return 'Хороший';
  return 'Отличный';
};

const severityColors = {
  critical: { bg: 'bg-red-50', text: 'text-red-700', dot: 'bg-red-500' },
  warning: { bg: 'bg-amber-50', text: 'text-amber-700', dot: 'bg-amber-500' },
  info: { bg: 'bg-blue-50', text: 'text-blue-700', dot: 'bg-blue-500' },
};

export default function FieldDetailPanel({ field, onBack, onNavigate }) {
  const [ndviHistory, setNdviHistory] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [weather, setWeather] = useState(null);
  const [loading, setLoading] = useState(true);
  const [dayRange, setDayRange] = useState(90);

  useEffect(() => {
    if (!field?.id) return;
    setLoading(true);

    Promise.allSettled([
      apiClient.get(`/api/ndvi/${field.id}/history?days=${dayRange}`),
      // Try both alert endpoint patterns
      apiClient.get(`/api/alerts/field/${field.id}`).catch(() =>
        apiClient.get(`/api/alerts/?field_id=${field.id}`)
      ),
      apiClient.get(`/api/weather/field/${field.id}`),
    ]).then(([ndviRes, alertRes, weatherRes]) => {
      if (ndviRes.status === 'fulfilled') {
        const records = ndviRes.value.data?.records || ndviRes.value.data || [];
        setNdviHistory(
          records
            .map(r => ({
              date: r.captured_date,
              ndvi: r.mean_ndvi,
              min: r.min_ndvi,
              max: r.max_ndvi,
              cloud: r.cloud_cover_pct,
            }))
            .sort((a, b) => a.date.localeCompare(b.date))
        );
      }
      if (alertRes.status === 'fulfilled') {
        const data = alertRes.value.data;
        setAlerts(Array.isArray(data) ? data.filter(a => a.is_active !== false) : []);
      }
      if (weatherRes.status === 'fulfilled') {
        setWeather(weatherRes.value.data);
      }
      setLoading(false);
    });
  }, [field?.id, dayRange]);

  if (!field) return null;

  const ndvi = field.current_ndvi ?? field.mean_ndvi ?? null;
  const ndviColor = getNdviColor(ndvi);
  const crop = field.current_crop || field.crop_name || 'Не указана';

  return (
    <div className="flex flex-col h-full">
      {/* --- Header with back button --- */}
      <div className="flex items-center gap-2 p-3 border-b border-gray-200 bg-white sticky top-0 z-10">
        <button
          onClick={onBack}
          className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors"
          title="Назад к списку"
        >
          <svg className="w-5 h-5 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-gray-900 truncate">{field.name}</h3>
          <p className="text-xs text-gray-500">{field.enterprise_name || ''}</p>
        </div>
        {onNavigate && (
          <button
            onClick={() => onNavigate('field-detail', field.id)}
            className="text-xs text-green-600 hover:text-green-700 whitespace-nowrap font-medium"
            title="Открыть полную карточку"
          >
            Подробнее →
          </button>
        )}
      </div>

      {/* --- Scrollable content --- */}
      <div className="flex-1 overflow-y-auto">

        {/* NDVI + Crop summary card */}
        <div className="p-3 border-b border-gray-100">
          <div className="flex items-center gap-3">
            <div
              className="w-12 h-12 rounded-xl flex items-center justify-center text-white text-sm font-bold shadow-sm"
              style={{ backgroundColor: ndviColor }}
            >
              {ndvi != null ? ndvi.toFixed(2) : '—'}
            </div>
            <div className="flex-1">
              <div className="text-sm font-medium text-gray-900">
                NDVI: <span style={{ color: ndviColor }}>{getNdviLabel(ndvi)}</span>
              </div>
              <div className="text-xs text-gray-500 mt-0.5">
                Культура: <span className="text-gray-700">{crop}</span>
              </div>
              {field.area_ha && (
                <div className="text-xs text-gray-500">
                  Площадь: <span className="text-gray-700">{Number(field.area_ha).toFixed(1)} га</span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* --- NDVI History Chart --- */}
        <div className="p-3 border-b border-gray-100">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
              История NDVI
            </h4>
            <div className="flex gap-1">
              {[30, 60, 90].map(d => (
                <button
                  key={d}
                  onClick={() => setDayRange(d)}
                  className={`text-xs px-2 py-0.5 rounded ${
                    dayRange === d
                      ? 'bg-green-600 text-white'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  {d}д
                </button>
              ))}
            </div>
          </div>

          {loading ? (
            <div className="h-32 flex items-center justify-center text-xs text-gray-400">
              Загрузка графика...
            </div>
          ) : ndviHistory.length === 0 ? (
            <div className="h-32 flex items-center justify-center text-xs text-gray-400">
              Нет данных за период
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={140}>
              <LineChart data={ndviHistory} margin={{ top: 5, right: 5, bottom: 5, left: -15 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 9, fill: '#9ca3af' }}
                  tickFormatter={d => {
                    const parts = d.split('-');
                    return `${parts[2]}/${parts[1]}`;
                  }}
                  interval="preserveStartEnd"
                />
                <YAxis
                  domain={[0, 1]}
                  tick={{ fontSize: 9, fill: '#9ca3af' }}
                  tickCount={5}
                />
                <Tooltip
                  contentStyle={{
                    fontSize: 11,
                    borderRadius: 8,
                    border: '1px solid #e5e7eb',
                    boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
                  }}
                  formatter={(value, name) => {
                    if (name === 'ndvi') return [value?.toFixed(4), 'NDVI'];
                    return [value, name];
                  }}
                  labelFormatter={d => `Дата: ${d}`}
                />
                {/* Reference zones */}
                <ReferenceLine y={0.2} stroke="#f97316" strokeDasharray="3 3" strokeOpacity={0.5} />
                <ReferenceLine y={0.5} stroke="#84cc16" strokeDasharray="3 3" strokeOpacity={0.5} />
                <Line
                  type="monotone"
                  dataKey="ndvi"
                  stroke="#16a34a"
                  strokeWidth={2}
                  dot={{ r: 2.5, fill: '#16a34a' }}
                  activeDot={{ r: 4, strokeWidth: 2 }}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* --- Active Alerts --- */}
        <div className="p-3 border-b border-gray-100">
          <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-2">
            Алерты ({alerts.length})
          </h4>
          {loading ? (
            <div className="text-xs text-gray-400">Загрузка...</div>
          ) : alerts.length === 0 ? (
            <div className="text-xs text-gray-400 py-2">Нет активных алертов ✓</div>
          ) : (
            <div className="space-y-1.5 max-h-40 overflow-y-auto">
              {alerts.slice(0, 5).map((alert, i) => {
                const sev = severityColors[alert.severity] || severityColors.info;
                return (
                  <div key={alert.id || i} className={`${sev.bg} rounded-lg p-2`}>
                    <div className="flex items-start gap-1.5">
                      <div className={`w-1.5 h-1.5 rounded-full mt-1 ${sev.dot}`} />
                      <div className="flex-1 min-w-0">
                        <div className={`text-xs font-medium ${sev.text} truncate`}>
                          {alert.title || alert.alert_type}
                        </div>
                        {alert.recommendation && (
                          <div className="text-xs text-gray-600 mt-0.5 line-clamp-2">
                            {alert.recommendation}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
              {alerts.length > 5 && (
                <div className="text-xs text-gray-500 text-center pt-1">
                  +{alerts.length - 5} ещё
                </div>
              )}
            </div>
          )}
        </div>

        {/* --- Weather --- */}
        <div className="p-3">
          <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-2">
            Погода
          </h4>
          {loading ? (
            <div className="text-xs text-gray-400">Загрузка...</div>
          ) : !weather ? (
            <div className="text-xs text-gray-400">Нет данных о погоде</div>
          ) : (
            <WeatherMini data={weather} />
          )}
        </div>
      </div>
    </div>
  );
}

/* --- Mini weather widget for the panel --- */
function WeatherMini({ data }) {
  // Adapt to whatever shape the weather API returns
  // Common shapes: data.current, data.daily, data.forecast
  const current = data.current || data;
  const daily = data.daily || data.forecast || [];

  const weatherIcon = (code) => {
    // WMO weather codes → emoji
    if (code <= 1) return '☀️';
    if (code <= 3) return '⛅';
    if (code <= 48) return '🌫️';
    if (code <= 57) return '🌧️';
    if (code <= 67) return '🌧️';
    if (code <= 77) return '❄️';
    if (code <= 82) return '🌧️';
    if (code <= 86) return '❄️';
    if (code >= 95) return '⛈️';
    return '🌤️';
  };

  return (
    <div>
      {/* Current temperature if available */}
      {current?.temperature_2m != null && (
        <div className="flex items-center gap-2 mb-2">
          <span className="text-lg">
            {weatherIcon(current.weather_code || current.weathercode || 0)}
          </span>
          <span className="text-lg font-semibold text-gray-900">
            {Math.round(current.temperature_2m)}°C
          </span>
          {current.relative_humidity_2m != null && (
            <span className="text-xs text-gray-500">
              💧 {current.relative_humidity_2m}%
            </span>
          )}
          {current.wind_speed_10m != null && (
            <span className="text-xs text-gray-500">
              💨 {Math.round(current.wind_speed_10m)} км/ч
            </span>
          )}
        </div>
      )}

      {/* Daily forecast - show up to 5 days */}
      {daily.time && daily.time.length > 0 && (
        <div className="grid grid-cols-5 gap-1 mt-1">
          {daily.time.slice(0, 5).map((day, i) => (
            <div key={day} className="text-center">
              <div className="text-xs text-gray-500">
                {new Date(day).toLocaleDateString('ru', { weekday: 'short' })}
              </div>
              <div className="text-sm">
                {weatherIcon(daily.weather_code?.[i] || daily.weathercode?.[i] || 0)}
              </div>
              <div className="text-xs text-gray-700 font-medium">
                {daily.temperature_2m_max?.[i] != null
                  ? `${Math.round(daily.temperature_2m_max[i])}°`
                  : '—'}
              </div>
              <div className="text-xs text-gray-400">
                {daily.temperature_2m_min?.[i] != null
                  ? `${Math.round(daily.temperature_2m_min[i])}°`
                  : ''}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Precipitation sum if available */}
      {daily.precipitation_sum && (
        <div className="text-xs text-gray-500 mt-2">
          Осадки за 5 дн: {daily.precipitation_sum.slice(0, 5).reduce((s, v) => s + (v || 0), 0).toFixed(1)} мм
        </div>
      )}
    </div>
  );
}
```

---

## Step 2: Integrate FieldDetailPanel into FieldListPanel

Edit `frontend/src/components/Map/FieldListPanel.jsx`

### 2a. Add import at the top

Find the import section and add:
```javascript
import FieldDetailPanel from './FieldDetailPanel';
```

### 2b. Replace the detail view section

Find the block that renders when `selectedField` is truthy (the current detail view).
It likely looks something like:
```jsx
{selectedField ? (
  <div>
    {/* ... existing basic detail view ... */}
  </div>
) : (
  // field list
)}
```

Replace ONLY the detail branch content (the part inside `selectedField ?`) with:
```jsx
{selectedField ? (
  <FieldDetailPanel
    field={selectedField}
    onBack={() => setSelectedField(null)}
    onNavigate={onNavigate}
  />
) : (
  // keep the existing list view EXACTLY as-is
)}
```

**Important:** The `onNavigate` prop must come from FieldListPanel's own props. Check if FieldListPanel already receives `onNavigate` from FieldsPage. If not, pass it through from FieldsPage.

---

## Step 3: Ensure map zoom on field selection

Edit `frontend/src/components/Map/FieldMap.jsx`

The map should fly to the selected field when it changes. Check if this is already implemented. If NOT, add this.

### 3a. Accept `selectedFieldId` prop

Make sure FieldMap receives a `selectedFieldId` (or `selectedField`) prop. Check what FieldsPage currently passes.

### 3b. Add flyTo effect

If not already present, add this `useEffect` inside FieldMap (after the map is initialized):

```javascript
// Fly to selected field
useEffect(() => {
  if (!mapRef.current || !selectedFieldId || !geojsonData) return;

  const feature = geojsonData.features?.find(
    f => f.properties?.id === selectedFieldId
  );
  if (!feature) return;

  // Calculate bounds of the field polygon
  const coords = feature.geometry?.coordinates;
  if (!coords || !coords[0]) return;

  const bounds = coords[0].reduce(
    (b, c) => {
      return [
        [Math.min(b[0][0], c[0]), Math.min(b[0][1], c[1])],
        [Math.max(b[1][0], c[0]), Math.max(b[1][1], c[1])],
      ];
    },
    [[Infinity, Infinity], [-Infinity, -Infinity]]
  );

  mapRef.current.fitBounds(bounds, {
    padding: { top: 80, bottom: 80, left: 420, right: 80 },
    maxZoom: 16,
    duration: 1000,
  });
}, [selectedFieldId, geojsonData]);
```

**Note the `left: 420` padding** — this accounts for the 380px panel width so the field is centered in the visible map area, not behind the panel.

### 3c. Highlight selected field on map

If not already present, add a highlight layer for the selected field. After the map loads and sources are set up:

```javascript
// Add selected field highlight layer (if not already present)
if (!map.getLayer('field-selected-outline')) {
  map.addLayer({
    id: 'field-selected-outline',
    type: 'line',
    source: 'fields',  // or whatever the GeoJSON source is named
    filter: ['==', ['get', 'id'], -1],  // nothing selected initially
    paint: {
      'line-color': '#2563eb',
      'line-width': 3,
      'line-dasharray': [2, 1],
    },
  });
}
```

Then update the filter when `selectedFieldId` changes:

```javascript
// Update selected field highlight
useEffect(() => {
  const map = mapRef.current;
  if (!map || !map.getLayer('field-selected-outline')) return;

  map.setFilter('field-selected-outline',
    selectedFieldId
      ? ['==', ['get', 'id'], selectedFieldId]
      : ['==', ['get', 'id'], -1]
  );
}, [selectedFieldId]);
```

---

## Step 4: Wire FieldsPage to pass selectedField to map

Edit `frontend/src/pages/FieldsPage.jsx`

Make sure FieldsPage manages the selected field state and passes it to both FieldListPanel and FieldMap.

### Check existing state

Look for existing state like `selectedField`, `selectedFieldId`, or similar. If it already exists and is wired to both FieldListPanel and FieldMap, skip this step.

If NOT wired properly, ensure:

```jsx
const [selectedFieldId, setSelectedFieldId] = useState(null);

// In FieldListPanel:
<FieldListPanel
  onFieldSelect={(field) => setSelectedFieldId(field?.id || null)}
  selectedFieldId={selectedFieldId}
  onNavigate={onNavigate}  // pass through from App.jsx
  // ... other existing props
/>

// In FieldMap:
<FieldMap
  selectedFieldId={selectedFieldId}
  onFieldClick={(fieldId) => setSelectedFieldId(fieldId)}
  // ... other existing props
/>
```

### Bidirectional selection

When user clicks a field ON THE MAP:
- FieldMap calls `onFieldClick(fieldId)`
- FieldsPage sets `selectedFieldId`
- FieldListPanel receives the new `selectedFieldId` and shows detail mode

When user clicks a field IN THE PANEL:
- FieldListPanel calls `onFieldSelect(field)`
- FieldsPage sets `selectedFieldId`
- FieldMap receives the new `selectedFieldId` and flies to it

---

## Step 5: Map click handler for field selection

Edit `frontend/src/components/Map/FieldMap.jsx`

If not already present, add a click handler on the fields layer:

```javascript
map.on('click', 'fields-fill', (e) => {  // or whatever the fill layer is named
  if (e.features && e.features.length > 0) {
    const fieldId = e.features[0].properties.id;
    if (onFieldClick) {
      onFieldClick(fieldId);
    }
  }
});
```

Also add a cursor change on hover:
```javascript
map.on('mouseenter', 'fields-fill', () => {
  map.getCanvas().style.cursor = 'pointer';
});
map.on('mouseleave', 'fields-fill', () => {
  map.getCanvas().style.cursor = '';
});
```

---

## Verification Checklist

- [ ] Clicking a field in the panel list shows the FieldDetailPanel
- [ ] FieldDetailPanel shows: NDVI value with color, crop name, area
- [ ] NDVI mini-chart loads and renders with Recharts (line chart, 140px height)
- [ ] Day range buttons (30/60/90) work and reload the chart
- [ ] Active alerts for the field are shown (up to 5, with severity colors)
- [ ] Weather section shows current temp and 5-day forecast
- [ ] "Назад к списку" (back arrow) returns to the field list
- [ ] "Подробнее →" navigates to the full FieldDetailPage
- [ ] Map flies/zooms to the selected field polygon
- [ ] Map adds a blue dashed outline around the selected field
- [ ] Left padding of 420px keeps the field visible (not behind the panel)
- [ ] Clicking a field ON THE MAP also selects it in the panel
- [ ] Clicking outside fields on the map OR pressing back clears selection
- [ ] No console errors
- [ ] Build passes: `cd frontend && npm run build`

---

## CRITICAL WARNINGS

1. **DO NOT remove the `glyphs` property** from any map style object — field labels will break
2. **DO NOT change the `onNavigate` callback system** — it's the navigation backbone
3. **DO NOT replace FieldMap.jsx entirely** — make targeted additions only
4. **DO NOT replace FieldListPanel.jsx entirely** — only swap the detail view branch and add the import
5. **Read every file BEFORE editing** — the code may already have partial implementations of some features
6. **The `field-centroids` source** must remain untouched — it powers map labels
7. **Alert endpoint:** check BOTH `/api/alerts/field/{id}` and `/api/alerts/?field_id={id}` — use whichever works
8. **`recharts` is already installed** in the project — do not reinstall
