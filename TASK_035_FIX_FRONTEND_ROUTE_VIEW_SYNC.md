# TASK_035_FIX_FRONTEND_ROUTE_VIEW_SYNC.md

## Objective

Fix frontend routing/view synchronization in AgroSat.

Confirmed runtime defect:

- Opening `/fields` renders Dashboard instead of FieldsPage.
- FieldsPage exists and contains the `Добавить поле` button.
- Current user is admin, so permissions are not the blocker.
- App.jsx uses React Router, but authenticated app pages are handled by one catch-all route.
- AppLayout starts with `useState('dashboard')` and does not read `location.pathname`.
- Sidebar navigation changes only local state and does not update browser URL.

This is a frontend-only routing defect.

## Strict scope

Allowed files:

- frontend/src/App.jsx
- frontend/src/components/Layout/Sidebar.jsx

Do not modify backend, database, migrations, seed data, auth backend, NDVI/backfill scripts, MapLibre drawing internals, field creation validation, .env, package dependencies.

## Required behavior

Authenticated routes must render correct pages:

- `/` or `/dashboard` -> DashboardPage
- `/fields` -> FieldsPage
- `/alerts` -> AlertsPage
- `/enterprises` -> EnterprisesPage

Public routes must continue working:

- `/login`
- `/unauthorized`

Sidebar clicks must update both visible screen and browser URL:

- fields -> /fields
- dashboard -> /dashboard
- alerts -> /alerts
- enterprises -> /enterprises

Refreshing browser on `/fields`, `/alerts`, `/enterprises` must keep the matching page.

Preserve existing internal flows:

- clicking field opens field detail view
- returning from field detail goes back to fields/map view
- enterprise map/detail flows keep working
- active sidebar highlight keeps working

Keep local state for internal temporary views if needed:

- field-detail
- enterprise-detail
- enterprise-specific fields view

Do not implement new parameterized routes unless required for the minimal fix.

## Implementation guidance

Recommended minimal approach:

- In App.jsx, import `useLocation` and `useNavigate` from react-router-dom.
- Inside AppLayout, derive/sync view from `location.pathname`.
- In handleNavigate, call navigate() for stable top-level views.
- Avoid infinite loops between useEffect, setView, and navigate.
- Sidebar may remain button-based, but its onNavigate call must ultimately update URL through AppLayout.

## Acceptance checks

Run:

- cd C:\AgroSat\frontend
- npm run build

Manual runtime checks:

1. Login as admin.
2. Open http://localhost:5173/fields.
3. Header must be `Поля`, not `Главная`.
4. Map page must render.
5. Button `Добавить поле` must be visible.
6. Open /dashboard and confirm Dashboard renders.
7. Open /alerts and confirm Alerts page renders.
8. Open /enterprises and confirm Enterprises page renders.
9. Click sidebar icons and confirm URL changes.
10. Refresh browser on /fields and confirm it remains on Fields page.

## Non-goals

Do not implement new field creation features.
Do not change backend APIs.
Do not change user roles.
Do not reset passwords.
Do not touch DB/backfill/Sentinel Hub logic.
Do not commit changes.
