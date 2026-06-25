# TASK_042_FRONTEND_FIELD_LABELS_GLYPH_SOURCE

## Role

You are a senior frontend/GIS engineer working on AgroSat, an industrial AgTech/GIS platform.

## Context

Current HEAD must be:

- `00b086d TASK_041 improve field map zoom performance`

Recent map tasks:

- TASK_040 restored visible field polygons, `last_ndvi` NDVI coloring, crop/NDVI mode buttons, NDVI legend, and field click behavior.
- TASK_041 improved map zoom/tile behavior and cleaned up the map code.
- Field labels were intentionally kept out of TASK_040/TASK_041 because the previous glyph URL caused glyph 404 errors and because Cyrillic mojibake was found during manual QA.

Do not regress TASK_040 or TASK_041.

## Problem

The field map currently has visible polygons and working NDVI/crop modes, but field labels are not shown.

Labels must be restored safely without:

- broken glyph URL errors,
- MapLibre source/layer/expression errors,
- Cyrillic mojibake,
- duplicate event handlers,
- field polygon/NDVI/style-switch regressions.

## Scope

Allowed file:

- `frontend/src/components/Map/FieldMap.jsx`

Forbidden files:

- backend files,
- API client files,
- database/migration files,
- auth files,
- route files,
- `frontend/src/App.jsx`,
- `.gitignore`,
- `package.json`,
- `package-lock.json`.

Do not commit.

## Requirements

### 1. Preserve existing map behavior

The following behavior from TASK_040/TASK_041 must remain working:

- `/fields` opens normally.
- field polygons are visible immediately after loading.
- initial `fitBounds` behavior remains intact.
- crop color mode works.
- NDVI mode uses `last_ndvi`, not `current_ndvi`.
- NDVI legend appears only in NDVI mode.
- active button states remain correct for `Спутник / Карта / Гибрид` and `Культуры / NDVI`.
- hover still works.
- field click still opens the field details/card.
- draw mode still works.
- style switching keeps fields visible.
- high zoom does not allow broken/blank raster tile behavior within supported zoom limits.

### 2. Restore field labels safely

Add a MapLibre `symbol` layer for field labels.

Expected layer:

- id: `fields-label`
- source: `fields-source`
- type: `symbol`
- label text should use, in order:
  - `name`,
  - `code`,
  - `id` as fallback.
- Use `to-string` around the fallback expression so numeric ids are safe.
- Labels must be readable over satellite imagery and map tiles.
- Add a halo around label text.
- Avoid label overload at low zoom. Use a conservative `minzoom` or zoom-dependent text size.
- Do not use `text-allow-overlap: true` unless there is a clear reason. Prefer readable non-overlapping labels.

Suggested expression:

```js
'\u0074\u0065\u0078\u0074-field': ['to-string', ['coalesce', ['get', 'name'], ['get', 'code'], ['get', 'id']]]
```

Use normal JS property names in the final code. The escaped form above is only to make the intent unambiguous.

### 3. Fix glyph source correctly

The previous Protomaps glyph URL caused 404 errors. Do not reuse a broken glyph source.

Required:

- Replace or remove the old broken glyph source if it is still present.
- Configure a glyph URL that works with the font stack used by `fields-label`.
- Set an explicit `text-font` in the label layer if needed to match the glyph source.
- Manual browser validation must show no glyph 404 errors.

If a reliable glyph source cannot be verified locally, do not fake success. Stop and report the limitation clearly.

### 4. Rehydrate labels after style switch

When `Спутник / Карта / Гибрид` is switched:

- `fields-source` must be restored.
- `fields-fill` must be restored.
- `fields-border` must be restored.
- `fields-label` must be restored.
- current crop/NDVI mode must be preserved.
- no duplicate event handlers should be added.

### 5. Field label interactions

Clicking or hovering directly on a label should behave like interacting with the polygon:

- cursor changes on hover,
- clicking label opens the field details/card,
- cleanup on unmount removes listeners from both polygon and label layers.

Use a controlled interaction layer list. Avoid attaching duplicate listeners.

### 6. Encoding safety

Do not corrupt Cyrillic strings.

The following strings must remain readable in `FieldMap.jsx` and in the browser:

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
- `Р“`
- `Рљ`
- `РҐ`
- `Р›`
- `РёР`
- `СЊ`
- `С‹`

### 7. No unrelated work

Do not change:

- backend,
- API contracts,
- database,
- migrations,
- auth,
- routes,
- global bundle splitting,
- package dependencies,
- scheduler logic.

Do not commit.

## Required console validation

Run after implementation:

```powershell
Set-Location "C:\AgroSat"

Write-Host "===== STATUS / DIFF ====="
git status --short
git diff --name-status
git diff --check

Write-Host "`n===== FORBIDDEN CHANGE CHECK ====="
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

Write-Host "`n===== FIELDMAP LABEL / GLYPH CHECK ====="
Select-String -Path "frontend\src\components\Map\FieldMap.jsx" -Pattern "fields-label|type: 'symbol'|text-field|text-font|glyphs|GLYPHS|text-halo|text-color|minzoom|maxzoom|minZoom|maxZoom|LAYERS|INTERACTION|on\(|off\(|last_ndvi|current_ndvi|Спутник|Карта|Гибрид|Культуры|РЎ|Рџ|Р\u201c|Рљ|РҐ|Р›|СЊ|С‹|ponytail|TODO TASK_041|TODO TASK_042" |
    ForEach-Object { "{0}:{1}: {2}" -f $_.Path, $_.LineNumber, $_.Line.TrimEnd() }

Write-Host "`n===== TEXT VALIDATION ====="
$py = "C:\AgroSat\backend\venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$script = "C:\AgroSat_temp\validate_fieldmap_task042.py"
@'
from pathlib import Path

path = Path(r"C:\AgroSat\frontend\src\components\Map\FieldMap.jsx")
text = path.read_text(encoding="utf-8", errors="replace")

required = [
    "Спутник",
    "Карта",
    "Гибрид",
    "Культуры",
    "Пшеница озимая",
    "Хлопок",
    "Люцерна",
    "Рис",
    "Кукуруза",
    "last_ndvi",
    "fields-label",
    "type: 'symbol'",
    "text-field",
]

forbidden = [
    "TODO TASK_041",
    "ponytail",
    "current_ndvi",
    "РЎ",
    "Рџ",
    "Р“",
    "Рљ",
    "РҐ",
    "Р›",
    "РёР",
    "СЊ",
    "С‹",
]

missing = [item for item in required if item not in text]
bad = [item for item in forbidden if item in text]

print("REQUIRED_CHECK")
for item in required:
    print(f"{item}: {'OK' if item in text else 'MISSING'}")

print("FORBIDDEN_CHECK")
for item in forbidden:
    print(f"{item}: {'FOUND' if item in text else 'OK'}")

if missing:
    print("MISSING_ITEMS=" + ", ".join(missing))
if bad:
    print("BAD_ITEMS=" + ", ".join(bad))

if not missing and not bad:
    print("FIELDMAP_TASK042_TEXT_VALIDATION_OK")
else:
    print("FIELDMAP_TASK042_TEXT_VALIDATION_FAILED")
'@ | Set-Content -Path $script -Encoding UTF8
& $py $script
Remove-Item $script -Force -ErrorAction SilentlyContinue

Write-Host "`n===== FRONTEND BUILD ====="
Set-Location "C:\AgroSat\frontend"
npm run build -- --outDir C:\AgroSat_temp\frontend_task042_verify_build

Write-Host "`n===== FINAL STATUS ====="
Set-Location "C:\AgroSat"
git status --short
git diff --name-status
git diff --check
```

## Manual browser validation required

The user must verify manually:

1. `/fields` opens normally.
2. Buttons show correct Cyrillic: `Спутник / Карта / Гибрид / Культуры`.
3. Fields are visible immediately.
4. Field labels appear at a reasonable zoom level.
5. Labels are readable over satellite imagery.
6. Clicking a label opens the same field details/card as clicking the polygon.
7. Hovering a label behaves correctly.
8. `Спутник / Карта / Гибрид` switching keeps polygons and labels visible.
9. `Культуры / NDVI` switching keeps labels visible and preserves fill colors.
10. NDVI legend appears only in NDVI mode.
11. Draw mode still works.
12. Browser console has no MapLibre source/layer/expression errors.
13. Browser console has no glyph 404 errors.
14. There is no Cyrillic mojibake in the map controls.

## Output required from Claude

After implementation, report:

- changed files,
- exact glyph URL used,
- exact `text-font` used,
- whether `fields-label` is added in both initial load and `rehydrateLayers`,
- validation command output summary,
- whether any limitation remains.

Do not commit.
