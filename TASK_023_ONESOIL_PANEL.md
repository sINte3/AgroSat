# TASK_023: OneSoil-Style Left Panel Over Map

## Goal
Add a left panel that floats over the map on the Fields page. The panel shows a searchable list of fields with NDVI indicators. Clicking a field shows its detail view inside the same panel and highlights/zooms to it on the map. This is the core OneSoil-style interaction pattern.

---

## Step 0: Read files first (MANDATORY)

```
frontend/src/pages/FieldsPage.jsx
frontend/src/components/Map/FieldMap.jsx
frontend/src/components/Field/FieldDetail.jsx
frontend/src/api/client.js
frontend/src/App.jsx
```

Also check what field data the API returns:
```bash
curl http://localhost:8000/api/fields/?limit=3 2>nul
curl http://localhost:8000/api/fields/geojson/all 2>nul | head -c 500
```

---

## Step 1: Create FieldListPanel component

Create `frontend/src/components/Map/FieldListPanel.jsx`

This is a floating panel that sits on top of the map. It has TWO modes:
- **List mode**: shows all fields with search/filter
- **Detail mode**: shows detail of one selected field

### Panel container styling:
```jsx
<div className="absolute top-3 left-3 bottom-3 z-30 w-[380px] bg-white rounded-2xl shadow-xl border border-gray-200 flex flex-col overflow-hidden">
  {/* Panel content */}
</div>
```

### List mode — Header section:
```jsx
<div className="p-4 border-b border-gray-100">
  {/* Title row */}
  <div className="flex items-center justify-between mb-3">
    <h2 className="text-lg font-semibold text-gray-900">Поля</h2>
    <span className="text-sm text-gray-500">{fields.length} полей</span>
  </div>
  
  {/* Search input */}
  <div className="relative">
    <input
      type="text"
      placeholder="Поиск по названию..."
      value={searchQuery}
      onChange={(e) => setSearchQuery(e.target.value)}
      className="w-full pl-9 pr-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-green-500/30 focus:border-green-500"
    />
    <svg className="absolute left-3 top-2.5 w-4 h-4 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
    </svg>
  </div>
  
  {/* Enterprise filter — two pill buttons */}
  <div className="flex gap-2 mt-3">
    <button
      onClick={() => setEnterpriseFilter(null)}
      className={`px-3 py-1 text-xs rounded-full border transition-colors ${
        enterpriseFilter === null
          ? 'bg-green-600 text-white border-green-600'
          : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
      }`}
    >
      Все
    </button>
    {enterprises.map(e => (
      <button
        key={e.id}
        onClick={() => setEnterpriseFilter(e.id)}
        className={`px-3 py-1 text-xs rounded-full border transition-colors truncate max-w-[140px] ${
          enterpriseFilter === e.id
            ? 'bg-green-600 text-white border-green-600'
            : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
        }`}
      >
        {e.name.replace(' Агрокластер', '')}
      </button>
    ))}
  </div>
</div>
```

### List mode — Field items:
```jsx
<div className="flex-1 overflow-y-auto">
  {filteredFields.map(field => (
    <div
      key={field.id}
      onClick={() => onFieldSelect(field.id)}
      onMouseEnter={() => onFieldHover?.(field.id)}
      onMouseLeave={() => onFieldHover?.(null)}
      className={`px-4 py-3 border-b border-gray-100 cursor-pointer transition-colors hover:bg-gray-50 ${
        highlightedFieldId === field.id ? 'bg-green-50' : ''
      }`}
    >
      <div className="flex items-center justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            {/* Crop color dot */}
            <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0`} 
                  style={{ backgroundColor: getCropColor(field.current_crop) }} />
            <span className="text-sm font-medium text-gray-900 truncate">{field.name}</span>
          </div>
          <div className="flex items-center gap-3 mt-1 ml-[18px]">
            <span className="text-xs text-gray-500">{field.area_ha?.toFixed(1)} га</span>
            <span className="text-xs text-gray-400">{field.current_crop || '—'}</span>
            {field.active_alerts_count > 0 && (
              <span className="text-xs text-red-500">⚠ {field.active_alerts_count}</span>
            )}
          </div>
        </div>
        
        {/* NDVI indicator */}
        <div className="text-right flex-shrink-0 ml-2">
          {field.current_ndvi != null ? (
            <div className="flex items-center gap-1.5">
              <span className={`text-sm font-mono font-semibold ${getNdviTextColor(field.current_ndvi)}`}>
                {field.current_ndvi.toFixed(2)}
              </span>
              <span className="w-3 h-3 rounded-full" style={{ backgroundColor: getNdviColor(field.current_ndvi) }} />
            </div>
          ) : (
            <span className="text-xs text-gray-400">—</span>
          )}
        </div>
      </div>
    </div>
  ))}
</div>
```

### Helper functions (put inside or above the component):
```jsx
const getCropColor = (crop) => {
  const colors = {
    'Пшеница': '#eab308',
    'Хлопок (капельн.)': '#3b82f6',
    'Хлопок': '#ec4899',
    'Хлопок (открыт.)': '#ec4899',
    'Люцерна': '#22c55e',
    'Овощи': '#f97316',
    'Кукуруза': '#8b5cf6',
    'Рис': '#06b6d4',
  };
  if (!crop) return '#9ca3af';
  for (const [key, color] of Object.entries(colors)) {
    if (crop.includes(key) || key.includes(crop)) return color;
  }
  return '#9ca3af';
};

const getNdviColor = (ndvi) => {
  if (ndvi < 0.15) return '#dc2626';
  if (ndvi < 0.3) return '#f97316';
  if (ndvi < 0.45) return '#eab308';
  if (ndvi < 0.6) return '#84cc16';
  return '#16a34a';
};

const getNdviTextColor = (ndvi) => {
  if (ndvi < 0.15) return 'text-red-600';
  if (ndvi < 0.3) return 'text-orange-600';
  if (ndvi < 0.45) return 'text-yellow-600';
  if (ndvi < 0.6) return 'text-lime-600';
  return 'text-green-600';
};
```

### Detail mode:
When a field is selected (user clicked a field item), the panel switches to detail mode:

```jsx
{selectedField ? (
  <div className="flex flex-col h-full">
    {/* Back button + field name */}
    <div className="p-4 border-b border-gray-100">
      <button
        onClick={() => setSelectedField(null)}
        className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-2"
      >
        <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M15 18l-6-6 6-6"/>
        </svg>
        Назад к списку
      </button>
      <h2 className="text-lg font-semibold text-gray-900">{selectedField.name}</h2>
      <p className="text-sm text-gray-500 mt-0.5">
        {selectedField.enterprise_name} · {selectedField.area_ha?.toFixed(1)} га
      </p>
    </div>
    
    {/* Field detail content — scrollable */}
    <div className="flex-1 overflow-y-auto p-4 space-y-4">
      {/* NDVI card */}
      <div className="bg-gray-50 rounded-xl p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm text-gray-500">Текущий NDVI</span>
          <span className="text-xs text-gray-400">{selectedField.last_ndvi_date || '—'}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-3xl font-bold" style={{ color: getNdviColor(selectedField.current_ndvi || 0) }}>
            {selectedField.current_ndvi?.toFixed(3) || '—'}
          </span>
          <div className="w-16 h-3 rounded-full bg-gray-200 overflow-hidden">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.max(0, Math.min(100, (selectedField.current_ndvi || 0) * 100))}%`,
                backgroundColor: getNdviColor(selectedField.current_ndvi || 0)
              }}
            />
          </div>
        </div>
      </div>
      
      {/* Crop info */}
      <div className="bg-gray-50 rounded-xl p-4">
        <span className="text-sm text-gray-500">Культура</span>
        <div className="flex items-center gap-2 mt-1">
          <span className="w-3 h-3 rounded-full" style={{ backgroundColor: getCropColor(selectedField.current_crop) }} />
          <span className="text-sm font-medium text-gray-900">{selectedField.current_crop || 'Не указана'}</span>
        </div>
        {selectedField.irrigation_type && (
          <p className="text-xs text-gray-400 mt-1">Полив: {selectedField.irrigation_type}</p>
        )}
      </div>
      
      {/* Active alerts */}
      {selectedField.active_alerts_count > 0 && (
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-2">
            Предупреждения ({selectedField.active_alerts_count})
          </h3>
          {/* Load alerts for this field from API: GET /api/alerts/{field_id} */}
          {/* Show them as compact cards */}
        </div>
      )}
      
      {/* Button to open full detail page */}
      <button
        onClick={() => onOpenFullDetail?.(selectedField.id)}
        className="w-full py-2.5 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 transition-colors"
      >
        Подробная карточка поля →
      </button>
    </div>
  </div>
) : (
  /* List mode content (header + field items) */
)}
```

### Panel collapse button:
Add a small toggle button on the RIGHT edge of the panel to collapse/expand it:
```jsx
<button
  onClick={() => setCollapsed(!collapsed)}
  className="absolute -right-3 top-1/2 -translate-y-1/2 w-6 h-12 bg-white rounded-r-lg shadow-md border border-l-0 border-gray-200 flex items-center justify-center text-gray-400 hover:text-gray-600 z-40"
>
  <svg className={`w-4 h-4 transition-transform ${collapsed ? 'rotate-180' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M15 18l-6-6 6-6"/>
  </svg>
</button>
```

When collapsed, the panel slides off-screen:
```jsx
<div className={`absolute top-3 bottom-3 z-30 w-[380px] bg-white rounded-2xl shadow-xl border border-gray-200 flex flex-col overflow-hidden transition-transform duration-300 ${
  collapsed ? '-translate-x-[calc(100%+12px)]' : 'left-3'
}`}>
```

---

## Step 2: Update FieldsPage.jsx

The FieldsPage should now:
1. Fetch the field list data from API
2. Render FieldMap at full size
3. Overlay FieldListPanel on top of the map
4. Handle field selection → zoom map to field + show detail in panel
5. Handle field hover → highlight field on map

```jsx
import { useState, useEffect, useCallback } from 'react';
import FieldMap from '../components/Map/FieldMap';
import FieldListPanel from '../components/Map/FieldListPanel';
import { getCachedEnterprises } from '../api/client';
import axios from 'axios';

const API = 'http://localhost:8000/api';

export default function FieldsPage({ onFieldClick, enterpriseId }) {
  const [fields, setFields] = useState([]);
  const [enterprises, setEnterprises] = useState([]);
  const [selectedFieldId, setSelectedFieldId] = useState(null);
  const [highlightedFieldId, setHighlightedFieldId] = useState(null);
  const [mapRef, setMapRef] = useState(null);

  useEffect(() => {
    // Fetch fields list with NDVI and alert data
    axios.get(`${API}/fields/`, { params: { include_ndvi: true } })
      .then(res => setFields(res.data))
      .catch(err => console.error('Error loading fields:', err));
    
    getCachedEnterprises()
      .then(setEnterprises)
      .catch(err => console.error('Error loading enterprises:', err));
  }, []);

  const handleFieldSelect = useCallback((fieldId) => {
    setSelectedFieldId(fieldId);
    // Zoom map to field
    if (mapRef) {
      const field = fields.find(f => f.id === fieldId);
      if (field && field.centroid_lat && field.centroid_lon) {
        mapRef.flyTo({
          center: [field.centroid_lon, field.centroid_lat],
          zoom: 15,
          duration: 1000
        });
      }
    }
  }, [mapRef, fields]);

  const handleFieldHover = useCallback((fieldId) => {
    setHighlightedFieldId(fieldId);
  }, []);

  const handleOpenFullDetail = useCallback((fieldId) => {
    if (onFieldClick) onFieldClick(fieldId);
  }, [onFieldClick]);

  const handleMapFieldClick = useCallback((fieldId) => {
    setSelectedFieldId(fieldId);
  }, []);

  return (
    <div className="relative w-full h-full">
      <FieldMap
        onFieldClick={handleMapFieldClick}
        highlightedFieldId={highlightedFieldId}
        selectedFieldId={selectedFieldId}
        enterpriseId={enterpriseId}
        onMapReady={setMapRef}
      />
      <FieldListPanel
        fields={fields}
        enterprises={enterprises}
        selectedFieldId={selectedFieldId}
        highlightedFieldId={highlightedFieldId}
        onFieldSelect={handleFieldSelect}
        onFieldHover={handleFieldHover}
        onOpenFullDetail={handleOpenFullDetail}
        enterpriseId={enterpriseId}
      />
    </div>
  );
}
```

---

## Step 3: Update FieldMap.jsx

Add these capabilities to FieldMap:

### 1. Accept new props:
- `highlightedFieldId` — field to highlight on hover (thicker border)
- `selectedFieldId` — field that is selected (bright highlight)
- `onMapReady` — callback to expose the map instance to parent

### 2. Expose map via onMapReady:
When the map initializes, call `onMapReady(map)` so the parent can call `map.flyTo()`.

### 3. Highlight hovered field:
When `highlightedFieldId` changes, update the field outline layer to show a thicker/brighter border on that field. Use MapLibre's `setFilter` or `setPaintProperty` on a dedicated highlight layer.

Add a new layer specifically for highlighting:
```javascript
// After adding field-borders layer, add:
map.addLayer({
  id: 'field-highlight',
  type: 'line',
  source: 'fields',
  paint: {
    'line-color': '#16a34a',
    'line-width': 3,
    'line-opacity': 0.9
  },
  filter: ['==', ['get', 'id'], '']  // empty filter = nothing highlighted
});

map.addLayer({
  id: 'field-selected',
  type: 'line',
  source: 'fields',
  paint: {
    'line-color': '#ffffff',
    'line-width': 4,
    'line-opacity': 1
  },
  filter: ['==', ['get', 'id'], '']
});
```

When `highlightedFieldId` prop changes:
```javascript
useEffect(() => {
  if (!map) return;
  if (map.getLayer('field-highlight')) {
    map.setFilter('field-highlight', 
      highlightedFieldId ? ['==', ['get', 'id'], highlightedFieldId] : ['==', ['get', 'id'], '']
    );
  }
}, [map, highlightedFieldId]);

useEffect(() => {
  if (!map) return;
  if (map.getLayer('field-selected')) {
    map.setFilter('field-selected',
      selectedFieldId ? ['==', ['get', 'id'], selectedFieldId] : ['==', ['get', 'id'], '']
    );
  }
}, [map, selectedFieldId]);
```

### 4. Map click → onFieldClick:
When user clicks a field polygon on the map, call `onFieldClick(fieldId)`. This should already be implemented — make sure it passes the field ID to the parent.

---

## Step 4: Load field detail data

When a field is selected in the panel, fetch its full details:
```javascript
// In FieldListPanel, when selectedFieldId changes:
useEffect(() => {
  if (!selectedFieldId) {
    setSelectedField(null);
    return;
  }
  // First check if we have it in the fields list
  const basicField = fields.find(f => f.id === selectedFieldId);
  setSelectedField(basicField || null);
  
  // Fetch full detail
  axios.get(`${API}/fields/${selectedFieldId}`)
    .then(res => setSelectedField(prev => ({ ...prev, ...res.data })))
    .catch(err => console.error('Error loading field detail:', err));
}, [selectedFieldId, fields]);
```

---

## Step 5: Sort fields by NDVI (worst first)

In the panel, fields with the lowest NDVI (most problematic) should appear first:
```javascript
const sortedFields = [...filteredFields].sort((a, b) => {
  // Fields with alerts first
  if (a.active_alerts_count > 0 && b.active_alerts_count === 0) return -1;
  if (a.active_alerts_count === 0 && b.active_alerts_count > 0) return 1;
  // Then by NDVI ascending (worst first)
  const aNdvi = a.current_ndvi ?? 999;
  const bNdvi = b.current_ndvi ?? 999;
  return aNdvi - bNdvi;
});
```

---

## Step 6: Panel stats bar

At the bottom of the panel (list mode only), show a compact stats bar:
```jsx
<div className="p-3 border-t border-gray-100 bg-gray-50">
  <div className="flex items-center justify-between text-xs text-gray-500">
    <span>Ср. NDVI: <strong className="text-gray-700">{avgNdvi.toFixed(3)}</strong></span>
    <span>Проблемных: <strong className="text-red-600">{alertCount}</strong></span>
    <span>Площадь: <strong className="text-gray-700">{totalArea.toFixed(0)} га</strong></span>
  </div>
</div>
```

---

## Verification Checklist

- [ ] Panel appears on the left side of the map page
- [ ] Search filters fields by name
- [ ] Enterprise filter pills work
- [ ] Clicking a field in the panel zooms the map to it
- [ ] Hovering a field in the panel highlights it on the map
- [ ] Clicking a field shows detail view in the panel
- [ ] "Назад к списку" returns to list
- [ ] "Подробная карточка поля" opens the full FieldDetailPage
- [ ] Clicking a field on the MAP selects it in the panel
- [ ] Collapse button hides/shows the panel
- [ ] Fields sorted by alerts first, then NDVI ascending
- [ ] Stats bar shows average NDVI, problem count, total area
- [ ] Panel does NOT block map controls (zoom, style switcher)
- [ ] Map is still fully interactive (zoom, pan, click) in areas outside the panel
- [ ] No console errors
- [ ] Build passes

---

## CRITICAL WARNINGS

1. **DO NOT remove the `glyphs` property** from any map style object
2. **DO NOT change the `onNavigate` callback system** in App.jsx
3. **DO NOT break the field-centroids source** for map labels
4. **DO NOT replace FieldMap.jsx entirely** — ADD to it. It has complex existing logic for layers, styles, labels that must be preserved
5. **The panel must use `position: absolute`** to float over the map, NOT flex layout that pushes the map
6. **Field IDs in GeoJSON properties** may be numbers or strings — handle both in filters: `['==', ['get', 'id'], fieldId]` and `['==', ['get', 'id'], String(fieldId)]`
