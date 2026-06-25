# TASK_044_FRONTEND_FIELDS_PAGE_REGRESSION_AUDIT

## Role
You are Claude Code working inside the AgroSat repository. Act as a strict senior frontend engineer and regression auditor.

## Project context
AgroSat is an industrial AgTech/GIS platform for satellite NDVI monitoring of agricultural fields.

Recent frontend map commits:

- TASK_040 restored FieldMap UI, NDVI coloring, legend, active style buttons, fit bounds, and field click/hover.
- TASK_041 tuned map zoom/tile behavior and reduced redundant reload/rehydration behavior.
- TASK_042 restored field labels using a working MapLibre glyph source.
- TASK_043 hardened FieldMap lifecycle cleanup, style.load handler cleanup, listener cleanup, unmount/remount behavior.

Now perform a regression audit across the frontend field workflow after those changes.

## Task goal
Audit and, only if necessary, fix frontend regressions in the field workflow:

- `frontend/src/pages/FieldsPage.jsx`
- `frontend/src/components/Map/FieldMap.jsx`
- `frontend/src/components/Map/CreateFieldModal.jsx`

The goal is to verify that the full fields page workflow still works after TASK_040–TASK_043:

1. Fields list loads.
2. Map loads.
3. Field polygons render.
4. Field labels render.
5. Crop/NDVI mode switching works.
6. Satellite/Map/Hybrid switching works.
7. Field click opens selected field details.
8. Field hover is stable.
9. Draw mode starts and exits cleanly.
10. Drawn polygon opens the create-field modal.
11. Create-field modal validates input correctly.
12. Saving a drawn field sends correct API payload.
13. Cancel/close does not leave stale draw state or stale selected field state.
14. No duplicated handlers after repeated create/cancel/style-switch/navigation flows.

## Hard scope limit
This is a frontend-only regression audit. Do not touch backend code.

Allowed files:

- `frontend/src/pages/FieldsPage.jsx`
- `frontend/src/components/Map/FieldMap.jsx`
- `frontend/src/components/Map/CreateFieldModal.jsx`
- `TASK_044_FRONTEND_FIELDS_PAGE_REGRESSION_AUDIT.md`

Forbidden files/directories:

- `backend/**`
- `frontend/src/api/**`
- `frontend/src/App.jsx`
- `package.json`
- `package-lock.json`
- `.gitignore`
- Alembic/migrations/database files
- any deployment/startup scripts

If a regression requires changing a forbidden file, stop and report the blocker instead of making that change.

## Non-negotiable constraints

- Do not commit.
- Do not push.
- Do not create new dependencies.
- Do not change backend API contracts.
- Do not change database schema.
- Do not add APScheduler or any scheduler logic.
- Do not reintroduce `current_ndvi`; FieldMap must use `last_ndvi`.
- Do not remove `lazy="raise_on_sql"` behavior anywhere.
- Do not remove MapLibre cleanup added in TASK_043.
- Do not reintroduce protomaps glyph URLs that caused glyph 404.
- Preserve Cyrillic UI text correctly encoded as UTF-8.
- Preserve the working glyph source from TASK_042 unless you can prove it is broken.
- Keep the task atomic: no backend work, no deployment work, no unrelated UI redesign.

## Audit checklist

### 1. FieldsPage state flow
Inspect `FieldsPage.jsx` for these risks:

- Selected field state becomes stale after map click, list click, create, cancel, or reload.
- Draw mode state is not reset after modal close/cancel/save.
- Newly created field is not loaded into the list/map after save.
- API loading/error state creates broken UI or permanent spinner.
- Field card/list/map state can diverge.
- `onFieldSelect`, `onFieldDrawn`, `drawMode`, and modal state are passed to `FieldMap` consistently.

### 2. FieldMap integration
Inspect `FieldMap.jsx` for these risks:

- Draw mode creates duplicate draw handlers.
- Draw control remains after draw mode exits.
- `draw.create` fires multiple times after repeated enable/disable cycles.
- Style switching duplicates click/hover handlers.
- Labels survive style switching.
- Selected field hover/click still works after mode/style switches.
- Unmount/remount from route navigation does not access a removed map.
- AbortController and logout cleanup remain intact.

### 3. CreateFieldModal validation and payload
Inspect `CreateFieldModal.jsx` for these risks:

- Invalid names are accepted.
- Control characters are accepted.
- Empty names are accepted.
- Required fields can be missing.
- Irrigation type uses the wrong value (`rainfed` must remain valid if already used).
- Geometry is lost or mutated before save.
- Modal close/cancel leaves draw state active.
- Double-submit can create duplicate API requests.
- Save error handling leaves the UI in a locked state.

### 4. Regression-sensitive strings
These strings must remain valid and not mojibake:

- `Спутник`
- `Карта`
- `Гибрид`
- `Культуры`
- `Пшеница озимая`
- `Хлопок`
- `Люцерна`
- `Рис`
- `Кукуруза`

Forbidden mojibake fragments include:

- `РЎ`
- `Рџ`
- `Рљ`
- `РҐ`
- `Р›`
- `СЊ`
- `С‹`

## Implementation rules

1. First audit the three allowed frontend files.
2. Make the smallest possible fixes only if you find a real regression or a high-probability lifecycle/state bug.
3. Do not rewrite the components wholesale.
4. Preserve existing UI structure unless fixing a concrete bug.
5. If no code fix is required, leave code unchanged and report that the audit found no required changes.
6. If code changes are made, they must be limited to the allowed files.
7. Add comments only where they explain a non-obvious lifecycle guard. Do not add noisy comments.

## Required validation commands
Run these from PowerShell after changes:

```powershell
Set-Location "C:\AgroSat"

git status --short
git diff --name-status
git diff --check
```

Forbidden file check:

```powershell
Set-Location "C:\AgroSat"
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
    exit 1
} else {
    Write-Host "OK: no forbidden changes"
}
```

Static regression search:

```powershell
Set-Location "C:\AgroSat"
Select-String -Path "frontend\src\pages\FieldsPage.jsx","frontend\src\components\Map\FieldMap.jsx","frontend\src\components\Map\CreateFieldModal.jsx" -Pattern "drawMode|onFieldDrawn|onFieldSelect|selectedField|CreateFieldModal|AbortController|abort\(|draw\.create|styleLoadHandlerRef|style\.load|fields-label|fields-fill|fields-border|last_ndvi|current_ndvi|rainfed|NAME_FORBIDDEN|Спутник|Карта|Гибрид|Культуры|РЎ|Рџ|Рљ|РҐ|Р›|СЊ|С‹|ponytail|TODO TASK_041" |
    ForEach-Object { "{0}:{1}: {2}" -f $_.Path, $_.LineNumber, $_.Line.TrimEnd() }
```

Frontend build:

```powershell
Set-Location "C:\AgroSat\frontend"
npm run build -- --outDir C:\AgroSat_temp\frontend_task044_verify_build
```

## Manual browser verification required by the user after your changes
Do not claim full completion until the user manually verifies:

1. `/fields` opens normally after Ctrl+F5.
2. Fields list loads.
3. Map polygons load.
4. Field labels are visible at intended zoom.
5. Clicking a polygon opens the field details/card.
6. Clicking a label opens the field details/card.
7. Hover works on polygon and label.
8. Crop/NDVI switching works.
9. NDVI legend appears only in NDVI mode.
10. Satellite/Map/Hybrid switching works repeatedly.
11. Draw mode starts.
12. Drawing a polygon opens the create-field modal.
13. Cancel closes modal and exits/cleans draw state.
14. Re-enter draw mode works after cancel.
15. Save creates or attempts to create the field with correct geometry payload.
16. Save error handling does not lock the modal permanently.
17. Navigate away from `/fields` and back: no console errors, no broken map.
18. Console has no MapLibre source/layer/expression errors.
19. Console has no glyph 404.
20. Console has no duplicated click/hover symptoms after repeated style switches and draw cancel cycles.

## Final report format
Return a concise report with:

1. Files changed.
2. Whether any code fix was required.
3. Exact bugs fixed, if any.
4. Validation command results.
5. Whether frontend build passed.
6. Remaining manual browser checks for the user.
7. Explicit statement: `No commit was made.`
