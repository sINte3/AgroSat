# TASK_043_FRONTEND_FIELDMAP_LIFECYCLE_CLEANUP_AUDIT

## Role

You are a senior frontend engineer and code reviewer working on AgroSat, an industrial AgTech/GIS platform.

## Context

Current HEAD must be:

- `89ba1da TASK_042 restore field map labels`

The last map-related tasks were:

- TASK_040 restored field polygons, NDVI coloring, legend, active buttons, and field click behavior.
- TASK_041 improved zoom/tile usability and reduced redundant style reloads.
- TASK_042 restored field labels through a working glyph source.

This task is a lifecycle and cleanup audit for `FieldMap.jsx` after these changes.

## Problem

`frontend/src/components/Map/FieldMap.jsx` now has multiple MapLibre lifecycle concerns:

- map instance creation/removal
- style switching with `setStyle`
- layer/source rehydration after style reload
- field layers and label layers
- draw mode via MapboxDraw
- event listeners for hover, click, draw, map load, style load, and logout
- aborting in-flight API requests
- React unmount cleanup

The risk is not a visible feature bug. The risk is hidden lifecycle damage:

- duplicate event handlers after style switch
- listeners left attached after unmount
- layers/sources accessed after style reset
- pending `style.load` handler firing after unmount
- draw control not removed safely
- map removed while async logic still tries to modify it
- field hover/click handlers attached multiple times
- regression of labels, NDVI, style switching, or draw mode

## Scope

Allowed file:

- `frontend/src/components/Map/FieldMap.jsx`

Forbidden files:

- backend files
- API client files
- database/migration files
- auth files
- route files
- `frontend/src/App.jsx`
- `package.json`
- `package-lock.json`
- `.gitignore`

Do not create new dependencies.
Do not change backend API behavior.
Do not change database schema.
Do not commit.

## Hard requirements

### 1. Preserve all current visible behavior

Do not regress:

- `/fields` opens normally
- fields are visible after load
- map fits to fields once after initial data load
- crop color mode works
- NDVI mode uses `last_ndvi`
- NDVI legend appears only in NDVI mode
- active states for `Спутник / Карта / Гибрид`
- active states for `Культуры / NDVI`
- field labels remain visible at intended zoom
- field label Cyrillic stays readable
- clicking polygon opens field details
- clicking label opens field details
- hover works on polygon and label
- style switching preserves fields and labels
- draw mode works
- zoom limits from TASK_041 remain intact
- glyph source from TASK_042 remains intact

### 2. Audit and harden lifecycle cleanup

Review and improve, only where needed:

- `map.on` / `map.off`
- `m.once('style.load', ...)`
- `setStyle`
- `rehydrateLayers`
- handlers for `fields-fill` and `fields-label`
- draw control add/remove
- `draw.create` listener add/remove
- logout listener add/remove
- `AbortController` usage
- `map.remove()` cleanup
- source/layer existence checks after style reload
- guards for `isMountedRef`, `mapRef.current`, `m.isStyleLoaded()`

Expected result:

- no duplicate field handlers after repeated style switches
- no duplicate draw handlers after entering/exiting draw mode
- no pending style-load handler runs after unmount
- no MapLibre source/layer/expression errors in console
- no React cleanup race with async API response

### 3. Keep changes minimal

This is not a rewrite.

Allowed examples:

- introduce a `styleLoadHandlerRef` if current `once('style.load')` is not cancellable
- centralize attach/detach of field interaction handlers if current logic risks duplication
- add safe guards before rehydrating layers after style switch
- make cleanup explicitly remove listeners from both `fields-fill` and `fields-label`
- ensure draw control and draw listener are removed safely

Forbidden examples:

- do not redesign the map UI
- do not change the visual color scale
- do not change labels styling unless necessary for lifecycle safety
- do not change API calls
- do not change field data shape
- do not remove labels
- do not remove draw mode
- do not add clustering
- do not add geometry simplification
- do not change routing/auth/backend

### 4. Encoding safety

Do not corrupt Cyrillic strings.

Required strings must remain readable in `FieldMap.jsx`:

- `Спутник`
- `Карта`
- `Гибрид`
- `Культуры`
- `Пшеница озимая`
- `Хлопок`
- `Люцерна`
- `Рис`
- `Кукуруза`

Forbidden mojibake fragments must not appear:

- `РЎ`
- `Рџ`
- `Рљ`
- `РҐ`
- `Р›`
- `СЊ`
- `С‹`

### 5. No commit

Do not commit.

## Required validation

Run this after changes:

```powershell
Set-Location "C:\AgroSat"

git status --short
git diff --name-status
git diff --check

$changed = git diff --name-only
$forbidden = $changed | Where-Object {
    $_ -match "^backend/" -or
    $_ -match "^frontend/src/api/" -or
    $_ -match "^backend/alembic/" -or
    $_ -match "^backend/main.py$" -or
    $_ -match "^frontend/src/App.jsx$" -or
    $_ -match "^package.json$" -or
    $_ -match "^package-lock.json$" -or
    $_ -match "^\.gitignore$"
}
if ($forbidden) {
    Write-Host "FORBIDDEN_CHANGES_FOUND:"
    $forbidden
} else {
    Write-Host "OK: no forbidden changes"
}

Select-String -Path "frontend\src\components\Map\FieldMap.jsx" -Pattern "style.load|styleLoadHandler|setStyle|rehydrateLayers|fields-fill|fields-label|draw.create|addControl|removeControl|AbortController|abort\(|addEventListener|removeEventListener|on\(|off\(|map.remove|isMountedRef|sessionPurgedRef|last_ndvi|current_ndvi|glyphs|demotiles|protomaps|Спутник|Карта|Гибрид|Культуры|РЎ|Рџ|Рљ|РҐ|Р›|СЊ|С‹" |
    ForEach-Object { "{0}:{1}: {2}" -f $_.Path, $_.LineNumber, $_.Line.TrimEnd() }

Set-Location "C:\AgroSat\frontend"
npm run build -- --outDir C:\AgroSat_temp\frontend_task043_build

Set-Location "C:\AgroSat"
git status --short
git diff --name-status
git diff --check
```

## Manual browser validation required

The user must verify:

1. `/fields` opens normally.
2. Fields are visible immediately after load.
3. Field labels are visible at the intended zoom.
4. Cyrillic labels and UI text are readable.
5. Crop/NDVI mode switching works.
6. NDVI legend appears only in NDVI mode.
7. `Спутник / Карта / Гибрид` switching works repeatedly.
8. Repeated style switches do not duplicate hover/click behavior.
9. Clicking polygon opens field details.
10. Clicking label opens field details.
11. Hover works on polygon and label.
12. Draw mode works.
13. Leaving draw mode does not leave duplicate handlers.
14. Browser console has no MapLibre source/layer/expression errors.
15. Browser console has no glyph 404 errors.
16. Browser console has no errors after switching styles several times.
17. Browser console has no errors after navigating away from `/fields` and back.

## Deliverable

Return:

- files changed
- concise summary of lifecycle fixes
- validation output
- manual checks still required
- confirmation that no commit was made
