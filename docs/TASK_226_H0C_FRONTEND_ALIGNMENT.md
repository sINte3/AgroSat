# TASK_226 — H0-C: frontend aligned with the TASK_225 canonical closed loop

Branch `task/226-h0c-frontend-alignment`, based on the accepted TASK_225 backend
`a0550407570727e89a49db771b3158589c20db2a` (not on main). Frontend only: no
backend, schema, ops, runtime or Scheduled Task change; not merged, not deployed.
Evidence: `C:\AgroSat_backups\TASK_226_H0C_EVIDENCE_20260924_165522`.

## 1. Outcome

- The UI no longer calls any TASK_225 retired write endpoint (proven by a
  runtime scan of every API helper, a source scan and a production-bundle scan).
- Inspection creation (attention queue, irrigation context, queue, alert, pixel
  NDVI) goes through `POST /api/anomaly-inspections`; remediation from an
  inspection goes through `/api/agronomy-plans`.
- The browser-triggered NDVI refresh is gone; collection is collector-owned.
- The Operational Center renders the canonical `remediation_status`; a closure
  without a verified improvement is never green.
- C7: an involuntary authentication failure can no longer erase unsynchronized
  offline scouting evidence.
- C12: automatic retries are limited to GET/HEAD; `field_ids` serializes as the
  backend expects; partial date input cannot crash React; a route ErrorBoundary
  exists; failed requests are not shown as healthy empty states; dashboard
  totals come from the server summary.
- MapLibre ownership was audited; one object-URL leak was found and fixed.

## 2. Phase A — inventory of retired callers

| caller | retired endpoint(s) | class | resolution |
|---|---|---|---|
| `components/Inspections/InspectionCreateModal.jsx` (live, attention queue) | `POST /api/field-inspections` | 1 canonical inspection | `createAnomalyInspection`, `source_kind: manual` |
| `components/Field/FieldIrrigationContextPanel.jsx` (live) | `POST /api/field-inspections` (`source: irrigation_context`) | 1 | canonical manual inspection, explicit deadline |
| `components/Inspections/AnomalyInspectionDetail.jsx` (live) | `POST /api/anomaly-inspections/{id}/actions`, `…/actions/{id}/transition`, `…/actions/{id}/verify` | 2 canonical agronomy | plan list + "Создать план мер" (`POST /api/agronomy-plans`); historical actions read-only |
| `api/client.js` `refreshNDVI` (no caller) | `POST /api/ndvi/{id}/refresh` | 4 remove | removed |
| `api/fieldInspections.js` (13 writes + reads) | every legacy write | 4 remove | module deleted; its only live read (dashboard count) now uses the canonical queue |
| `offline/offlineScoutingSync.js` | `…/result`, `…/evidence`, `…/actions` | 4 remove | deleted (TASK_209 offline queue) |
| `pages/FieldInspectionsPage.jsx`, `InspectionActionModal`, `InspectionDetailDrawer`, `OperationalClosurePanel`, `OperationalWorkflowModal`, `OfflineScoutingPanel`, `OfflineScoutingStatus`, `InspectionCard`, `InspectionFilters`, `InspectionSummaryCards` | all legacy inspection, closure and verification writes | 4 remove | deleted — unrouted since TASK_217 (`951aa97`, 2026-08-21: `/inspections` renders `AnomalyInspectionsPage`) |
| `pages/DashboardPage.jsx` | `GET /api/field-inspections` (legacy rows only) | 3→1 | canonical queue `summary.open` |
| legacy rows (`source_kind = legacy`) | — | 3 historical read | readable in the canonical detail; admin/manager close-out through `POST /api/anomaly-inspections/{id}/cancel` |

`scripts/test-task226-retired-contract.mjs` fails if a retired URL fragment,
helper, module or a global offline purge reappears in `src/` or in `dist/`.

## 3. Canonical inspection creation (B)

Request (`utils/inspectionRequests.js`, one builder for the manual paths):

```json
{"field_id": 11, "source_kind": "manual", "reason": "…", "priority": "urgent",
 "due_at": "2026-09-26T17:00:00+05:00", "assigned_to_id": 21}
```

- The attention queue and the irrigation context are not backend-recognized
  sources: the inspection is `manual` and the context is kept in the visible
  reason ("Очередь внимания: поле …, приоритет …, причины …" /
  "Контекст орошения: …"). No alert id, scene, pixel value or geometry hash is
  sent; `assigned_to_id` is optional and chosen from
  `GET /api/anomaly-inspections/assignees?field_id=` (agronomists).
- Attention priorities map low→low, medium→normal, high→high, critical→urgent.
- `due_at` is required by the backend: both forms show an explicit required
  date-time control (empty by default, no hidden deadline), interpreted as
  Asia/Tashkent and sent with `+05:00`; past deadlines are rejected visibly.
- Idempotency key: stable per payload, rotated when the payload changes after a
  failed attempt ("Повторить тот же запрос"); requests are abortable and
  stale responses are ignored.
- The backend lets only admin/manager create; agronomists see an explanation
  instead of a create control (the retired legacy create allowed self-assigned
  agronomist inspections — that is not part of the canonical lifecycle).

## 4. Retirement (C, D, O)

- `api/anomalyInspections.js` keeps the canonical lifecycle, gains
  `cancelAnomalyInspection` (canonical cancel and legacy close-out) and drops
  the three TASK_217 action writes.
- The NDVI refresh helper is removed; empty/verification states say that
  satellite scenes are collected automatically.
- One Russian vocabulary for the canonical lifecycles lives in
  `config/canonicalLifecycle.js` (inspection, remediation, plan, verification,
  work); the touched pages use it instead of private copies.
- Retired suites: `test-operational-closure.mjs`,
  `test-operational-closure-browser.mjs`, `test-offline-scouting-playwright.js`
  (they tested the deleted TASK_209 UI). Updated suites pin the canonical
  contract instead of the retired one (anomaly inspection, offline scouting,
  irrigation, closed-loop agronomy, operational center, executive CDP,
  irrigation CDP, TASK_221 qualification label).

## 5. Operational Center (E) and executive reports (F)

- Queue and case chips show `remediation_status` (17 canonical states);
  `operational_status` only refines "data unavailable"; `not_improved` is
  split by the plan verification in `provenance` into "Ухудшение после мер" /
  "Без существенных изменений после мер". Green is used only for
  `improved_closed` / `improved_awaiting_closure`; `closed_without_improvement`,
  `rejected`, `cancelled`, `inspection_closed` are neutral grey.
- Summary: "Текущая работа" (7 counts) and "Результат мер" (improved closed,
  closed without improvement, not improved, reopened, verification blocked).
- The state filter offers exactly the values the backend filter accepts
  (`closed_without_improvement` is a response value but not a filter value).
- "План мер #N" navigates by `plan_id`; verifications and work items use
  canonical labels. A failed queue is shown as an error, not "no situations".
- Executive: labels say "работы по планам мер"; accountability rows open
  `plan_id` and the inspection; cycle-time and verification-outcome blocks are
  labelled historical (TASK_209) until H1; an unexpected `definitions_version`
  (≠ `task225_canonical_backlog_v1`) is announced.

## 6. C7 — session model

| path | trigger | storage | offline scouting data |
|---|---|---|---|
| A `invalidateSession()` | any 401, failed revalidation (401/403), failed login completion, another tab removing the token | token + cached user of the failing credential only (a 401 for an older token never ends a newer session); user-scoped response caches | never touched |
| B `logoutExplicitly()` | the "Выйти" button | as A | the current user's partition only (`enterprise_id:user_id`); unsynchronized drafts require an explicit "Выйти и удалить" confirmation |
| C `beginSession()` / revalidation | login, identity change | caches dropped when the identity differs | partitioned; another user never reads or sends them |

- The interceptor no longer hard-redirects: React state ends, `PrivateRoute`
  redirects with the return path, and the same user returns to the page.
- A rejected login (401) or an unavailable server is reported
  ("Неверный email или пароль" / "Сервер недоступен…") and never ends a
  session or touches IndexedDB.
- Recovery: the inspection queue lists this user's unsynchronized drafts; a
  draft the user already queued for sending (`pending_sync`) is sent when its
  inspection is opened online, with its original `expected_version`.
- Product contract change (supersedes "Logout deletes the complete offline
  scouting database" of `TASK_209_OFFLINE_SCOUTING_CONTRACT.md`): no code path
  deletes the whole database; an explicit logout removes only its own partition.

## 7. C12

- Retries: GET/HEAD only, timeout or 5xx only, at most 2, `__noRetry`
  respected, never after an abort (also during back-off), never for 4xx or an
  offline network error. POST/PUT/PATCH/DELETE execute once.
- `serializeCoverageParams`: `field_ids=123` / `field_ids=123,456`,
  `index_codes=savi,evi` (NDVI stripped). `FieldAnalyticsWorkspace` now takes
  the coverage row whose `field_id` matches instead of row 0 (with the old
  bracket serialization row 0 was an arbitrary field of the scope).
- Dates: `utils/tashkentTime.js` (fixed UTC+05:00 arithmetic, independent of the
  browser zone) and `utils/workSchedule.js`. `AgronomyPlansPage` keeps raw
  datetime text in state, validates on submit (required deadline, optional
  planned start, start ≤ deadline) and sends `+05:00` values; old drafts in ISO
  form are restored. The inspection finding, source dialog and filters use the
  same semantics.
- `RenderErrorBoundary` wraps the routed content (shell stays usable; retry and
  "На главную"; resets on route change; no automatic loop) and the whole app
  (reload / go to login). API errors stay page state.
- Error ≠ empty: `EnterpriseDetailPage` (alerts loading/available/failed;
  KPIs show "—"; list capped at 200 is marked as a lower bound),
  `FieldDetailPanel` (alerts and weather states, abortable), `FieldsPage` /
  `FieldListPanel` (failed list with retry), plus the touched OC, agronomy and
  inspection queues.
- Dashboard: KPI and severity chips come from `dashboard/summary`
  (`active_alerts`, `critical_alerts`, `warning_alerts`); the 20 fetched alerts
  are a "Первые по приоритету: 5 из N" list; no info count is invented.

## 8. MapLibre ownership audit (N)

| owner | resources | result |
|---|---|---|
| `hooks/usePixelNDVIWorkspace.js` | `style.load`, `idle`, `click` listeners; 250 ms restore interval; 2 image sources + 2 raster layers; 3 object URLs; 3 AbortControllers; generation counters | **leak fixed**: with comparison on, a failed or aborted scene B left scene A's object URL unrevoked (`Promise.all`); now `Promise.allSettled` and every created URL is revoked |
| `hooks/useNDVIRasterLayer.js` (no production consumer) | `style.load`; image source + layer; object URL; controller | correct, unchanged |
| `AnomalyInspectionDetail` source map | map, `load` listener, ResizeObserver, 2 sources, 4 layers | correct, unchanged |
| `components/Agronomy/AgronomyCaseMap.jsx` | map, NavigationControl, `load`, source, 3 layers, controller | correct, unchanged |

Proof (`scripts/test-task226-harness-browser.mjs`, Chromium): listener,
source, layer, interval and object-URL counts return to zero after unmount, on
map ownership change, on disable and after a partial failure; five real
`AgronomyCaseMap` mounts end with five `remove()` calls and no canvas. The
partial-failure check fails with the base hook (`liveUrls: 1`).

## 9. Tests

| suite | result |
|---|---|
| `npm run test:contracts` (22 non-browser suites incl. `test-task226-units` 433, `-http-contract` 102 + 152 requests scanned, `-retired-contract` 2708 assertions) | 22/22 PASS |
| `npm run test:task226-harness` (ErrorBoundary, MapLibre ×4, error≠empty ×2, IndexedDB partitions) | PASS, 8 runners |
| `npm run test:task226-browser` (production build + TASK_225 API fixture; login/expiry, dashboard totals, attention create, irrigation create, OC states, agronomy dates, offline 401 recovery + user isolation + cross-tab, ErrorBoundary, enterprise alerts; 24 WCAG 2.2 AA audits) | 18/18 PASS (desktop 1440×900, mobile 390×844) |
| CDP harnesses `test-weather-irrigation-browser`, `test-executive-accountability-browser` | PASS |
| same browser flows against base a055040 | 18/18 FAIL at the defects (see `BASE_DISCRIMINATION.json`) |
| production build | PASS |
| `npm audit` / `npm audit --omit=dev` | 0 vulnerabilities |

Pre-existing colour-contrast findings (login button, SummaryCards warning
values, enterprise page inline palette) are identical on base and recorded, not
fixed (no cosmetic redesign); two new subtitles found by the audit were fixed.

## 10. Backend contract observations (not blockers)

1. `GET /api/operational-center/queue?operational_status=` does not accept
   `closed_without_improvement`, although responses contain it; the UI does not
   offer it as a filter.
2. `POST /api/anomaly-inspections` is admin/manager only; agronomists can no
   longer self-create (canonical design).
3. `api/client.js#getEnterpriseDashboard` targets
   `/api/dashboard/enterprises/{id}`, which the backend does not expose (no
   caller; left untouched).

## 11. Residual risks

1. Legacy TASK_209 offline records (if any exist in a browser) stay local and
   are listed as "устаревший контур, отправка недоступна"; their endpoints are
   retired, so re-entry must happen in a canonical inspection.
2. Canonical inspection detail is not cached for offline opening; an
   agronomist must open the inspection online once before working offline.
3. Browser qualification uses a deterministic TASK_225-contract fixture, not a
   live backend; H0-D should run a short live smoke (create inspection, plan,
   401 recovery) after deployment.
4. Pre-existing colour-contrast issues outside the TASK_226 scope remain.
5. Executive cycle-time/outcome metrics remain TASK_209 history until H1.
