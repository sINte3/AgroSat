# TASK_041_FRONTEND_MAP_PERFORMANCE_AND_TILE_ZOOM_AUDIT

## Role

You are a senior frontend engineer working on AgroSat, an industrial AgTech/GIS platform.

## Context

Current HEAD must be:

- `9503f05 TASK_040 restore field map NDVI UI`

TASK_040 restored field visibility, NDVI coloring, NDVI legend, active map buttons, `fitBounds`, and field click behavior.

Do not regress TASK_040.

## Problem

The field map works, but map usage is still heavy and high zoom may cause tile usability problems.

Observed issues:

1. Satellite map can be slow/heavy.
2. At high zoom levels, raster tiles may become unavailable or visually blank.
3. Field labels were intentionally removed from TASK_040 because of broken glyph URL.
4. Do not reintroduce labels in this task.

## Scope

Allowed files:

- `frontend/src/components/Map/FieldMap.jsx`

Forbidden files:

- backend files
- API client files
- database/migration files
- auth files
- route files
- `package.json`
- `package-lock.json`

## Requirements

### 1. Preserve TASK_040 behavior

Preserve all of the following:

- fields are visible after `/fields` loads
- `fitBounds` still works
- crop color mode works
- NDVI color mode uses `last_ndvi`
- NDVI legend works
- active state for `Спутник / Карта / Гибрид` works
- active state for `Культуры / NDVI` works
- field hover works
- field click opens details/card
- draw mode works
- cleanup on unmount remains safe

### 2. Fix zoom/tile usability

Review the current raster sources:

- OSM
- Esri satellite
- Esri labels/hybrid

Make zoom behavior explicit and safe.

Expected result:

- the map must not allow users to zoom into levels where the selected raster source visibly breaks or becomes blank
- set realistic map `minZoom` / `maxZoom` limits and/or source `maxzoom` strategy
- keep field polygons visible over all supported zoom levels
- do not hide or remove fields during style switching

### 3. Low-risk map performance improvements only

Implement only low-risk map-level optimizations, for example:

- define explicit `minZoom` / `maxZoom`
- use a conservative initial zoom
- avoid style reload when the user clicks the already active map style
- avoid unnecessary rehydration when data and style state have not changed
- avoid duplicate event handlers
- avoid repeated `fitBounds` after initial data load

Do not do any of the following:

- do not add clustering
- do not simplify backend geometry
- do not change backend API response
- do not change global Vite chunk splitting
- do not change dependencies

### 4. Keep labels out

Do not add:

- `fields-label`
- `symbol` layer
- `text-field`
- glyph source changes

Leave at most one short TODO comment for TASK_042 if needed.

### 5. No commit

Do not commit.

## Validation

Run:

```powershell
Set-Location "C:\AgroSat"

git status --short
git diff --name-status
git diff --check

Set-Location "C:\AgroSat\frontend"
npm run build -- --outDir C:\AgroSat_temp\frontend_task041_build

Set-Location "C:\AgroSat"
git status --short
git diff --name-status
```

## Manual browser validation required

The user must verify:

1. `/fields` opens normally.
2. Fields are visible immediately.
3. Zooming in does not produce blank satellite behavior within supported zoom range.
4. Switching `Спутник / Карта / Гибрид` works.
5. Switching `Культуры / NDVI` works.
6. NDVI legend appears only in NDVI mode.
7. Field click still opens details.
8. Draw mode still works.
9. Browser console has no MapLibre source/layer/expression errors.
10. No glyph 404 errors are introduced.
