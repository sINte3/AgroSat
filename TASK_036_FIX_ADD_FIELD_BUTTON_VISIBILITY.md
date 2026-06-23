# TASK_036_FIX_ADD_FIELD_BUTTON_VISIBILITY.md

## Objective

Fix visibility of the "Добавить поле" button on the AgroSat Fields page.

## Confirmed runtime state

* `/fields` renders `FieldsPage`.
* Map and left field list panel render correctly.
* User is authenticated as admin.
* `FieldsPage.jsx` contains the create button with `id="add-field-btn"`.
* The button is not visible in the UI.

## Confirmed likely cause

* In `frontend/src/pages/FieldsPage.jsx`, the draw controls wrapper is positioned as:
  `absolute top-3 left-3 z-10`
* In `frontend/src/components/Map/FieldListPanel.jsx`, the left field panel is positioned as:
  `absolute top-3 bottom-3 z-30 w-[380px] ... left-3`
* Therefore the create button is likely rendered behind the left field list panel.

This is a frontend-only layout defect.

## Strict scope

Allowed file:

* `frontend/src/pages/FieldsPage.jsx`

Allowed only if absolutely necessary for a clean layout:

* `frontend/src/components/Map/FieldListPanel.jsx`

Do not modify:

* backend files
* database schema
* migrations
* auth backend
* user roles
* password/reset logic
* NDVI/backfill scripts
* MapLibre drawing internals
* create field API client logic
* create field validation rules
* routing in `App.jsx`
* `.env`
* package dependencies

## Required behavior

On `/fields`, for users with role `admin`, `manager`, or `agronomist`:

* the "Добавить поле" button must be clearly visible;
* it must not be hidden behind the field list panel;
* it must not block the field search input;
* it must not block top-right map layer controls;
* it must remain usable when the field list panel is expanded;
* it must remain usable when the field list panel is collapsed.

## Recommended minimal fix

Move the draw control overlay away from `top-3 left-3`.

Preferred minimal position:

`absolute top-3 left-[408px] z-50`

This places the control outside the 380px left panel footprint.

Keep:

* existing `id="add-field-btn"`;
* existing draw flow;
* existing modal flow;
* existing role-based visibility logic;
* existing create field API logic;
* existing field validation.

## Acceptance checks

Run from `C:\AgroSat\frontend`:

```cmd
npm run build
```

Manual runtime checks:

1. Start the app.
2. Login as admin.
3. Open `http://localhost:5173/fields`.
4. Confirm the map renders.
5. Confirm the left field list panel renders.
6. Confirm the "Добавить поле" button is visible without collapsing the panel.
7. Click "Добавить поле".
8. Confirm drawing instruction appears.
9. Confirm "Отменить рисование" button is visible and clickable.
10. Collapse and expand the field list panel.
11. Confirm the draw controls remain usable and are not hidden behind the panel.
12. Confirm no red browser console errors.

## Non-goals

Do not implement new field creation features.
Do not change field validation.
Do not change backend APIs.
Do not change auth.
Do not change routing.
Do not commit changes.
