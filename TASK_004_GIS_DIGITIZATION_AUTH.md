# TASK_004_GIS_DIGITIZATION_AUTH.md: Secure Spatial Drawing, Auth Scoping, & Analytical Charts

## Objective

Implement the frontend Stage 3/4 functionality for AgroSat:

1. Secure React authentication context and protected routing.
2. Secure MapLibre field map with on-demand polygon drawing.
3. Strict cleanup against cross-tenant UI leaks on logout.
4. Safe NDVI historical chart rendering with Recharts.

This is a **frontend-only task**.

Do **not** change backend code.
Do **not** change database schema.
Do **not** add Alembic migrations.
Do **not** add SQL indexes in this task.
Do **not** modify unrelated files.

---

## Project Context

AgroSat is a satellite field monitoring system for Bukhara Agrocluster, Uzbekistan.

Frontend stack:

* React
* Vite
* Tailwind CSS
* MapLibre GL JS
* Recharts

Backend stack, for context only:

* FastAPI
* SQLAlchemy
* PostGIS
* Redis

Spatial context:

* Bukhara center: `[64.4286, 39.7747]`
* Bukhara bbox:

  * longitude: `[63.0, 65.5]`
  * latitude: `[38.5, 40.5]`
* Local metric projection for backend calculations: EPSG:32639 / UTM Zone 39N
* Current active season: 2026

---

## Hard Security Rules

### 1. No raw error logging

Never log raw `Exception`, Axios error objects, request configs, response objects, or headers.

Forbidden:

```js
console.error(err);
console.error('Failed', err);
console.log(error);
```

Allowed:

```js
console.error('Session initialization failed');
console.error('Failed to load spatial layers');
console.error('MapboxDraw compatibility check failed');
```

Reason: Axios errors may contain request config and `Authorization` headers with JWT tokens.

---

### 2. Backend remains the authority

Frontend role checks are UX only.

The frontend must never be treated as the source of truth for:

* role permissions
* enterprise access
* field ownership
* write permissions

The backend remains responsible for RBAC and tenant isolation.

---

### 3. User identity must come only from `/api/auth/me`

Do not trust `localStorage.agrosat_user` as authority.

The in-memory `user` object must be loaded only from:

```text
GET /api/auth/me
```

Do not export raw React state mutators like:

```js
setUser
setToken
```

Export only controlled auth actions:

```js
login(accessToken)
logout()
revalidateSession()
```

---

### 4. Login must verify profile before setting React token state

Inside `login(accessToken)`:

1. Save access token into `localStorage`.
2. Call `/api/auth/me`.
3. If `/me` succeeds:

   * set user
   * set React token state
   * optionally store `agrosat_user` as non-authoritative cache
4. If `/me` fails:

   * clear token
   * clear user
   * dispatch logout cleanup event

Do not set React `token` state before `/api/auth/me` succeeds.

---

### 5. Logout must prevent cross-tenant flash

When logout happens, immediately:

* remove token from localStorage
* remove cached user from localStorage
* clear AuthContext user
* clear AuthContext token
* dispatch:

```js
window.dispatchEvent(new Event('agrosat:logout'));
```

All map components must listen to `agrosat:logout` and must:

* abort in-flight GeoJSON requests
* prevent already resolved GeoJSON responses from painting after logout
* remove active draw control
* clear drawn features
* disable drawing mode
* call `onFieldSelect(null)`
* replace `fields-source` data with an empty FeatureCollection

---

## Dependencies

Install only the required drawing package with pinned version:

```bash
npm install @mapbox/mapbox-gl-draw@1.5.0
```

Do not upgrade existing React, Vite, Tailwind, or other major frontend dependencies.

If `react-router-dom` or `recharts` are already installed, preserve existing project-compatible versions.

If they are missing, install versions compatible with the current project setup and do not upgrade React/Vite major versions.

---

## Files To Implement Or Refactor

Only patch these files unless the project structure requires minimal import wiring:

```text
frontend/src/context/AuthContext.jsx
frontend/src/components/Routing/PrivateRoute.jsx
frontend/src/components/Map/FieldMap.jsx
frontend/src/components/Field/NDVIChart.jsx
frontend/src/App.jsx
```

Do not modify backend files.

Do not modify database schema.

---

# 1. AuthContext

Create or refactor:

```text
frontend/src/context/AuthContext.jsx
```

Required behavior:

* Store `user`, `token`, `loading` in React state.
* Initial token may be read from `localStorage.getItem('agrosat_token')`.
* If token exists on app startup, verify it via `/api/auth/me`.
* If verification fails, logout.
* Do not export `setUser`.
* Do not export `setToken`.
* Export:

  * `user`
  * `token`
  * `loading`
  * `login`
  * `logout`
  * `revalidateSession`
  * `isAuthenticated`

Required implementation pattern:

```jsx
import React, { createContext, useContext, useState, useEffect } from 'react';
import apiClient from '../api/client';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(localStorage.getItem('agrosat_token'));
  const [loading, setLoading] = useState(true);

  const logout = () => {
    localStorage.removeItem('agrosat_token');
    localStorage.removeItem('agrosat_user');
    setUser(null);
    setToken(null);
    window.dispatchEvent(new Event('agrosat:logout'));
  };

  const login = async (accessToken) => {
    try {
      localStorage.setItem('agrosat_token', accessToken);

      const res = await apiClient.get('/api/auth/me');

      setUser(res.data);
      setToken(accessToken);
      localStorage.setItem('agrosat_user', JSON.stringify(res.data));

      return true;
    } catch {
      console.error('Failed to log in during profile verification');
      logout();
      return false;
    }
  };

  const revalidateSession = async () => {
    if (!token) return;

    try {
      const res = await apiClient.get('/api/auth/me');
      setUser(res.data);
    } catch {
      console.error('Revalidation failed');
      logout();
    }
  };

  useEffect(() => {
    const initAuth = async () => {
      if (!token) {
        setLoading(false);
        return;
      }

      try {
        const res = await apiClient.get('/api/auth/me');
        setUser(res.data);
      } catch {
        console.error('Session initialization failed');
        logout();
      } finally {
        setLoading(false);
      }
    };

    initAuth();

    const handleStorageChange = (event) => {
      if (event.key !== 'agrosat_token') return;

      const newToken = event.newValue;

      if (!newToken || newToken !== token) {
        logout();
        window.location.href = '/login';
      }
    };

    window.addEventListener('storage', handleStorageChange);
    return () => window.removeEventListener('storage', handleStorageChange);
  }, [token]);

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        loading,
        login,
        logout,
        revalidateSession,
        isAuthenticated: Boolean(token && user),
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }

  return context;
}
```

If the existing project has equivalent auth structure, preserve compatible naming but enforce the same security behavior.

---

# 2. PrivateRoute

Create or refactor:

```text
frontend/src/components/Routing/PrivateRoute.jsx
```

Required behavior:

* Use `useAuth()`.
* While auth is loading, show a loading screen.
* If no authenticated user/token, redirect to `/login`.
* If `allowedRoles` is provided and the in-memory `/api/auth/me` user role is not allowed, redirect to `/unauthorized`.
* Do not read role from `localStorage`.

Implementation pattern:

```jsx
import React from 'react';
import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';

export default function PrivateRoute({ allowedRoles }) {
  const { user, token, loading, isAuthenticated } = useAuth();

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-slate-950 text-emerald-400 font-mono text-sm">
        Инициализация сессии AgroSat...
      </div>
    );
  }

  if (!token || !user || !isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  if (allowedRoles && !allowedRoles.includes(user.role)) {
    return <Navigate to="/unauthorized" replace />;
  }

  return <Outlet />;
}
```

---

# 3. App Routing Wiring

Ensure `AuthProvider` wraps the application routes.

Required route protection pattern:

```jsx
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import PrivateRoute from './components/Routing/PrivateRoute';
import DashboardPage from './pages/DashboardPage';
import LoginPage from './pages/LoginPage';

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          <Route
            element={
              <PrivateRoute
                allowedRoles={['admin', 'manager', 'agronomist', 'viewer']}
              />
            }
          >
            <Route path="/" element={<DashboardPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
```

If the current project already has routing, integrate without breaking existing routes.

---

# 4. FieldMap

Create or refactor:

```text
frontend/src/components/Map/FieldMap.jsx
```

Required imports:

```jsx
import React, { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';
import apiClient from '../../api/client';
```

Do not import unused `useState`.

---

## Required FieldMap Props

```jsx
export default function FieldMap({
  onFieldSelect,
  onDrawComplete,
  isDrawingMode,
  setIsDrawingMode,
  canDraw,
}) {
  ...
}
```

`canDraw` must be passed from parent UI based on in-memory auth role.

Expected allowed roles for drawing:

```js
['admin', 'manager', 'agronomist']
```

Viewer must not be able to activate drawing controls.

Backend still remains the final authority.

---

## Required Refs

Use these refs:

```jsx
const mapContainerRef = useRef(null);
const mapRef = useRef(null);
const drawRef = useRef(null);
const isDrawingRef = useRef(false);
const abortControllerRef = useRef(null);
const isMountedRef = useRef(true);
const sessionPurgedRef = useRef(false);

const callbacksRef = useRef({
  onFieldSelect,
  onDrawComplete,
  setIsDrawingMode,
});

const handlersRef = useRef({
  onLoad: null,
  onMouseMove: null,
  onMouseLeave: null,
  onFieldClick: null,
  onDrawCreate: null,
});
```

Update callback refs on prop changes:

```jsx
useEffect(() => {
  callbacksRef.current = {
    onFieldSelect,
    onDrawComplete,
    setIsDrawingMode,
  };
}, [onFieldSelect, onDrawComplete, setIsDrawingMode]);
```

Update drawing ref:

```jsx
useEffect(() => {
  isDrawingRef.current = isDrawingMode;
}, [isDrawingMode]);
```

---

## Required MapboxDraw Compatibility Patch

Before creating `new MapboxDraw(...)`, validate constants:

```jsx
if (!MapboxDraw.constants?.classes) {
  console.error('MapboxDraw compatibility check failed');
  callbacksRef.current.setIsDrawingMode?.(false);
  return;
}
```

Then patch class constants:

```jsx
MapboxDraw.constants.classes.CANVAS = 'maplibregl-canvas';
MapboxDraw.constants.classes.CONTROL_BASE = 'maplibregl-ctrl';
MapboxDraw.constants.classes.CONTROL_PREFIX = 'maplibregl-ctrl-';
MapboxDraw.constants.classes.CONTROL_GROUP = 'maplibregl-ctrl-group';
MapboxDraw.constants.classes.ATTRIBUTION = 'maplibregl-ctrl-attrib';
```

---

## Enable Draw Mode

Draw control must be mounted only when:

```js
isDrawingMode === true && canDraw === true
```

Before enabling draw mode, check style readiness:

```jsx
if (!mapRef.current.isStyleLoaded()) {
  console.error('Map style is not ready for drawing mode');
  callbacksRef.current.setIsDrawingMode?.(false);
  return;
}
```

Then create and add draw control:

```jsx
const draw = new MapboxDraw({
  displayControlsDefault: false,
  controls: {
    polygon: true,
    trash: true,
  },
  defaultMode: 'draw_polygon',
});

drawRef.current = draw;
mapRef.current.addControl(draw);
```

Register `draw.create` handler through `handlersRef`.

When polygon is completed:

* accept only `Polygon`
* call `onDrawComplete(feature.geometry)`
* immediately call `setIsDrawingMode(false)`

Do not auto-submit to backend from the map component.

---

## Disable Draw Mode

When leaving draw mode:

* unregister `draw.create`
* call `draw.deleteAll()`
* call `map.removeControl(draw)`
* set `drawRef.current = null`
* set `isDrawingRef.current = false`
* clear cursor
* set `handlersRef.current.onDrawCreate = null`

---

## Map Initialization

Initialize map once:

```jsx
const map = new maplibregl.Map({
  container: mapContainerRef.current,
  style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
  center: [64.4286, 39.7747],
  zoom: 9,
});
```

Store map in `mapRef`.

---

## GeoJSON Loading

On map `load`:

1. Set:

```jsx
sessionPurgedRef.current = false;
```

2. Load protected GeoJSON:

```jsx
const res = await apiClient.get('/api/fields/geojson/all', {
  signal: abortControllerRef.current.signal,
});
```

3. Immediately after request resolves, check:

```jsx
if (
  sessionPurgedRef.current ||
  !isMountedRef.current ||
  !mapRef.current
) {
  return;
}
```

This is mandatory. Abort alone is not enough, because a resolved promise may still continue after logout.

4. Add source/layers only if they do not already exist:

```jsx
if (!map.getSource('fields-source')) {
  map.addSource('fields-source', {
    type: 'geojson',
    data: res.data,
    promoteId: 'id',
  });
}
```

Add `fields-fill` and `fields-border` only after checking `map.getLayer(...)`.

---

## Field Layer Styling

Use NDVI expression:

```jsx
'fill-color': [
  'interpolate',
  ['linear'],
  ['coalesce', ['get', 'current_ndvi'], -1],
  -1, '#4b5563',
  0.0, '#8B0000',
  0.2, '#FF4500',
  0.35, '#FFD700',
  0.5, '#9ACD32',
  0.65, '#228B22',
  0.8, '#006400'
]
```

Use `feature-state` hover for opacity and border width.

Do not mutate GeoJSON in React state on hover.

---

## Map Interaction Handlers

Use layer handlers:

* `mousemove` on `fields-fill`
* `mouseleave` on `fields-fill`
* `click` on `fields-fill`

Each handler must instantly return if drawing is active:

```jsx
if (isDrawingRef.current) return;
```

On field click:

```jsx
callbacksRef.current.onFieldSelect?.(featureId);
```

Do not use stale prop callbacks directly inside map event handlers.

---

## Logout Handling Inside FieldMap

Listen to:

```jsx
window.addEventListener('agrosat:logout', handleLogout);
```

On logout:

```jsx
sessionPurgedRef.current = true;

if (abortControllerRef.current) {
  abortControllerRef.current.abort();
  abortControllerRef.current = null;
}

disableDrawMode();

callbacksRef.current.setIsDrawingMode?.(false);
callbacksRef.current.onFieldSelect?.(null);

const src = mapRef.current?.getSource('fields-source');
if (src) {
  src.setData({
    type: 'FeatureCollection',
    features: [],
  });
}
```

This is mandatory to prevent old tenant GeoJSON from being rendered after logout.

---

## FieldMap Cleanup On Unmount

On unmount:

* set `isMountedRef.current = false`
* set `sessionPurgedRef.current = true`
* abort pending request
* remove logout event listener
* unregister all map event handlers
* disable draw mode
* remove map
* set `mapRef.current = null`

Do not attach custom properties to the MapLibre map instance.

Forbidden:

```js
map._customHandlers = ...
map._drawCreateHandler = ...
```

---

# 5. NDVIChart

Create or refactor:

```text
frontend/src/components/Field/NDVIChart.jsx
```

Requirements:

* Use `useMemo`.
* Do not mutate original `records`.
* Filter invalid dates.
* Filter `NaN`, `Infinity`, non-finite NDVI values.
* Sort ascending by date.
* Use fixed parent height.
* Use `ResponsiveContainer debounce={100}`.
* Disable chart animation.

Implementation pattern:

```jsx
import React, { useMemo } from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts';

export default function NDVIChart({ records }) {
  const chartData = useMemo(() => {
    if (!records || records.length === 0) return [];

    return [...records]
      .map((record) => {
        const timestamp = Date.parse(record.captured_date);
        const ndvi = Number(record.mean_ndvi);

        if (!Number.isFinite(timestamp) || !Number.isFinite(ndvi)) {
          return null;
        }

        return {
          date: record.captured_date,
          timestamp,
          ndvi: Number(ndvi.toFixed(4)),
        };
      })
      .filter(Boolean)
      .sort((a, b) => a.timestamp - b.timestamp);
  }, [records]);

  if (chartData.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 bg-slate-950/20 border border-slate-900 rounded-lg text-slate-500 font-mono text-xs">
        Нет доступной истории вегетации NDVI
      </div>
    );
  }

  return (
    <div className="w-full h-64 bg-slate-950/40 border border-slate-900/50 rounded-lg p-4 shadow-inner">
      <ResponsiveContainer width="100%" height="100%" debounce={100}>
        <LineChart
          data={chartData}
          margin={{ top: 10, right: 10, left: -20, bottom: 0 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />

          <XAxis
            dataKey="date"
            stroke="#64748b"
            fontSize={10}
            tickLine={false}
            tickFormatter={(tick) => {
              try {
                const date = new Date(tick);
                return date.toLocaleDateString('ru-RU', {
                  month: 'short',
                  day: 'numeric',
                });
              } catch {
                return tick;
              }
            }}
          />

          <YAxis
            stroke="#64748b"
            fontSize={10}
            tickLine={false}
            domain={[0.0, 1.0]}
            ticks={[0.0, 0.2, 0.4, 0.6, 0.8, 1.0]}
          />

          <Tooltip
            contentStyle={{
              backgroundColor: '#020617',
              borderColor: '#10b981',
              color: '#f8fafc',
            }}
            labelStyle={{
              color: '#10b981',
              fontWeight: 'bold',
              fontSize: '11px',
            }}
            itemStyle={{
              color: '#34d399',
              fontSize: '11px',
            }}
          />

          <Line
            type="monotone"
            dataKey="ndvi"
            stroke="#10b981"
            strokeWidth={2}
            dot={{ r: 2, strokeWidth: 1 }}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
```

---

# 6. Acceptance Criteria

Claude Code must confirm all of the following:

1. Only frontend files were changed.
2. No backend files were changed.
3. No database schema files or migrations were changed.
4. No SQL DDL was added.
5. `@mapbox/mapbox-gl-draw` is pinned at version `1.5.0`.
6. `AuthContext` does not export raw `setUser` or `setToken`.
7. The user object is loaded from `/api/auth/me`.
8. `login()` sets React token state only after `/api/auth/me` succeeds.
9. No raw Axios error objects are logged.
10. `FieldMap` does not attach custom attributes to the map instance.
11. `FieldMap` uses `handlersRef`.
12. `FieldMap` uses `callbacksRef`.
13. Draw control is mounted only when drawing mode is active and `canDraw === true`.
14. Draw control is removed when drawing mode exits.
15. `sessionPurgedRef` exists and is checked immediately after GeoJSON response resolves.
16. Logout aborts pending GeoJSON request.
17. Logout clears `fields-source`.
18. Logout calls `onFieldSelect(null)`.
19. Logout disables drawing mode.
20. NDVIChart filters invalid dates and non-finite NDVI values.
21. NDVIChart sorts records ascending without mutating original data.
22. `npm build` passes.

---

# 7. Final Execution Instruction

Read this task file and execute it exactly.

Do not change backend code.

Do not change database schema.

Patch only the listed frontend files.

Ensure `FieldMap` uses `sessionPurgedRef` so no resolved GeoJSON response can repopulate the map after logout.

Run the frontend build after implementation.

Return a concise implementation report with:

1. Files changed.
2. Installed package versions.
3. Confirmation that backend/schema were not changed.
4. Confirmation that build passed.
5. Confirmation that no raw error objects are logged.
6. Confirmation that `FieldMap` cleanup removes draw control, aborts GeoJSON requests, clears source data, and calls `onFieldSelect(null)`.
