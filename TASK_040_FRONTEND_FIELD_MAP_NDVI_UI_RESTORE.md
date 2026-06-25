# TASK_040_FRONTEND_FIELD_MAP_NDVI_UI_RESTORE

Role: Senior React + MapLibre engineer for AgroSat.

Goal: frontend-only fix in frontend/src/components/Map/FieldMap.jsx. Do not commit.

Allowed files:
- frontend/src/components/Map/FieldMap.jsx
- TASK_040_FRONTEND_FIELD_MAP_NDVI_UI_RESTORE.md only if needed

Forbidden:
- Do not touch backend.
- Do not touch API client.
- Do not touch routes.
- Do not touch database or migrations.
- Do not reintroduce rejected TASK_039 lifecycle refactor.
- Do not rewrite the component.
- Do not change draw polygon flow.
- Do not fix tile performance or max zoom in this task.

Facts:
- GeoJSON API returns last_ndvi, not current_ndvi.
- Current FieldMap uses current_ndvi for NDVI coloring, so NDVI mode is wrong.
- Current FieldMap lacks active button state for Satellite/Map/Hybrid and Crop/NDVI.
- Current FieldMap lacks NDVI legend.
- Current FieldMap lacks field label symbol layer.

Required changes:
1. Replace map NDVI color expressions from current_ndvi to last_ndvi.
2. Add React state for active map style: default satellite.
3. Add React state for active color mode: default crop unless current behavior clearly requires ndvi.
4. Make Satellite / Map / Hybrid active button visually distinct.
5. Make Культуры / NDVI active button visually distinct.
6. In NDVI mode, color fields by last_ndvi.
7. Show compact NDVI legend only in NDVI mode.
8. Add MapLibre symbol layer fields-label using fields-source.
9. Label text should prefer name, fallback code, fallback id.
10. Add halo around labels for satellite readability.
11. Rehydrate fields-source, fields-fill, fields-border, and fields-label after style switch.
12. Preserve current color mode after style switch. Do not reset NDVI to crop when switching map style.
13. Preserve hover, click, selectedFieldId highlight, drawing mode, logout behavior, map cleanup.

Validation commands:
Set-Location C:\AgroSat
git status --short
git diff --name-status
git diff --check
Set-Location C:\AgroSat\frontend
npm run build -- --outDir C:\AgroSat_temp\frontend_task040_build
Set-Location C:\AgroSat
git status --short
git diff --name-status

Manual browser checklist:
1. Open http://localhost:5173/fields.
2. Fields render.
3. Satellite / Map / Hybrid active state visible.
4. Культуры / NDVI active state visible.
5. NDVI colors use last_ndvi.
6. NDVI legend appears only in NDVI mode.
7. Labels are visible.
8. Style switch keeps fields, labels, and current color mode.
9. Hover/click still work.
10. Draw polygon still works.
11. No MapLibre layer/source console errors.

Acceptance:
- Only FieldMap.jsx changed plus this task file.
- No backend changes.
- current_ndvi no longer used for map coloring.
- last_ndvi used for NDVI coloring.
- Active buttons, NDVI legend, and field labels work.
- Frontend build passes.
- No commit.
