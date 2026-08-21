# Anomaly inspection workflow

TASK_217 extends the existing field-inspection domain into a tenant-safe operational loop:

`Pixel NDVI or alert context -> inspection -> finding -> review -> intervention -> verification`

The implementation deliberately reuses `field_inspections`, `inspection_results`,
`inspection_evidence`, `corrective_actions`, and `audit_events`. Rows created by the
older operational-closure API retain `source_kind=legacy`; the TASK_217 state machine
applies only to `pixel_ndvi`, `alert`, and `manual` source rows.

## Lifecycle and roles

- Managers and admins create, assign, reassign, cancel, confirm or reject inspections,
  create interventions, and verify completed interventions.
- The assigned agronomist starts an inspection, saves a structured finding, manages
  photo evidence, submits it, and may start or complete an assigned intervention.
- Viewers have read-only, tenant-scoped access.
- Inspection transitions are `new -> assigned -> in_progress -> submitted -> confirmed`
  or `rejected`. Cancellation is explicit and reasoned.
- Intervention transitions are `planned -> in_progress -> completed ->
  verified_effective|verified_ineffective`. Cancellation is explicit and reasoned.
- Every mutation uses optimistic versions and returns a deterministic conflict for a
  stale version.

Cross-tenant objects and assignees are hidden with the established non-enumerating
not-found response. Tenant identity always comes from the authenticated principal and
the authorized field, never from a client-supplied tenant identifier.

## API

The OpenAPI group `anomaly_inspection_workflow` exposes:

- `POST /api/anomaly-inspections` for immutable Pixel NDVI, alert, or manual snapshots;
- `GET /api/anomaly-inspections/queue`, `/assignees`, `/fields/{field_id}/timeline`, and
  `/{inspection_id}` for queue, detail, assignment choices, and field history;
- assignment, start, finding, submit, review, and cancellation mutations;
- authenticated photo upload/download/delete endpoints;
- intervention creation, transition, and verification endpoints.

Creation accepts an EPSG:4326 point or bounded GeoJSON zone. PostGIS validates the
geometry against the authorized field. Pixel values must be finite and inside the
supported index range. Pixel source snapshots retain provider item identity,
acquisition time, index values, and the field geometry hash, but never provider URLs or
credentials.

Photo uploads accept only JPEG, PNG, or WebP after MIME and magic-byte agreement.
The service enforces per-file, per-inspection count, and aggregate-size limits; uses a
random storage key below an absolute runtime media root; and records only metadata and
SHA-256 in the database. Media storage must be outside Git and evidence packages.

## Frontend and offline drafts

`/inspections` is the responsive operational queue. Pixel NDVI sampling and eligible
alerts expose `Create inspection` entry points with immutable source context. The detail
workspace contains the source map, structured finding, photo controls, review,
intervention, verification, and the immutable audit timeline.

Unsubmitted findings may be retained as a bounded browser draft. Its key contains the
authenticated user, enterprise, and inspection. It stores structured finding and GPS
metadata only: no tokens, credentials, cookies, image bytes, or cross-tenant data.
Reconnect synchronizes with the last observed server version; a conflict is surfaced
without overwriting the server. Logout removes drafts for the departing identity.

## Runtime configuration

- `INSPECTION_MEDIA_DIRECTORY` must be an absolute runtime-only path.
- The database must be upgraded to the single Alembic head
  `0013_anomaly_inspection_workflow` before serving these routes.
- The application does not start schedulers, collectors, backfills, Telegram, or Wialon
  as part of this workflow.

Repository contract tests cover schemas, authorization, state transitions, offline
isolation, cleanup, and UI copy. Database-backed qualification and repository Playwright
exercise the real API, PostGIS, private media path, responsive UI, and full lifecycle.
