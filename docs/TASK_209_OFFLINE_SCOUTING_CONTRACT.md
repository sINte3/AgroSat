# TASK 209 Offline Scouting Contract

## Scope

Offline scouting supports an authenticated agronomist who has already loaded assigned inspections while online. It provides:

- a cached assigned-inspection list;
- cached inspection detail;
- one versioned draft per inspection;
- structured inspection result, evidence metadata, and optional corrective action;
- an explicit queued, syncing, conflict, failed, or synchronized state;
- manual synchronization when connectivity returns.

It does not create an offline authorization boundary. The backend rechecks authentication, role, tenant, assignment, state, version, and every payload during synchronization.

## Storage and identity partition

The browser database is `agrosat-offline-scouting`, schema version 1.

Every snapshot, draft, and queue item is partitioned by:

```text
enterprise_id:user_id
```

Records from another partition are never returned. Logout deletes the complete offline scouting database so that a later user cannot inherit cached business data.

The database never stores:

- access tokens, passwords, cookies, or authorization headers;
- binary photos, object URLs, or arbitrary files;
- API response headers;
- data from a user or enterprise other than the active partition.

Storage is bounded:

- at most 100 inspection snapshots per partition;
- at most 100 drafts per partition;
- at most 200 queue items per partition;
- at most 20 evidence metadata records per draft;
- no binary attachment payload;
- photo metadata retains the server limit of 25 MiB per referenced item.

## Service worker boundary

The service worker caches only same-origin application-shell resources. It never intercepts or caches `/api`, authentication, external provider, raster, vector-tile, or report/export responses.

The application shell uses versioned cache names. Activation removes only older AgroSat shell cache versions. It never clears unrelated browser caches.

Background Sync is not required because support cannot be assumed. Manual synchronization is always available. An `online` event may update status but must not silently submit queued writes.

## Draft contract

A draft contains:

- inspection and partition identifiers;
- the server inspection version observed when drafting began;
- structured cause code and optional cause details;
- evidence note and optional EPSG:4326 location;
- zero to twenty geolocation or photo-metadata evidence items;
- an optional corrective action with owner, description, and due date;
- stable idempotency keys for result, every evidence item, and action;
- timestamps and schema version.

Photo drafts contain metadata only: safe basename, media type, byte size, SHA-256, optional provider reference, and capture timestamp. They do not claim that binary content was uploaded or preserved.

## Queue and synchronization

Queue submission order is deterministic:

```text
inspection result
→ evidence metadata in draft order
→ corrective action
```

Each write uses its persisted idempotency key. The result identifier returned by the backend is propagated into evidence and action requests. The expected inspection version advances after each accepted write.

A crash or reload may replay a completed step with the same key. The backend replay contract must return the original entity without another business write.

The client never changes `expected_version` to match an unexpected server version. HTTP 409 produces an explicit conflict state with server refresh guidance. HTTP 401/403 purges or blocks the queue as appropriate; validation errors remain visible and require draft correction. Transient network/5xx failures remain retryable with the same keys.

## Lifecycle and cancellation

All component requests own AbortControllers and reject stale results after inspection, route, partition, or component ownership changes. IndexedDB transactions are allowed to complete, but their results are ignored after ownership changes.

The service worker registration is unregistered only by an explicit maintenance action, not by component unmount.

## Accessibility and mobile behavior

The offline state, unsynchronized item count, conflict, and last synchronization outcome are exposed as text and live status, not color alone.

All controls are keyboard reachable, at least 44 CSS pixels high on mobile, and retain visible focus. The draft form uses native labels and error messages. No submission is implicit when connectivity returns.

## Failure semantics

- `queued`: stored locally and never claimed as server data.
- `syncing`: an explicit user-triggered synchronization is in progress.
- `conflict`: server state/version rejected the draft; no silent overwrite.
- `failed`: validation, authorization, or non-transient server failure needs intervention.
- `queued` after transient failure: safe retry is available with unchanged keys.
- `synchronized`: all steps were accepted or idempotently replayed; the local draft and queue item are removed after the final acknowledgement.

## Evidence and acceptance

Deterministic tests must prove:

- schema creation and upgrade behavior;
- partition isolation;
- bounded retention;
- no token or binary storage;
- stable idempotency keys;
- ordered resume-safe synchronization;
- explicit conflict behavior;
- transient retry;
- logout purge;
- API exclusion from service-worker caching;
- mobile and keyboard states;
- successful synchronization only after every requested step is acknowledged.

Live browser persistence is evidence for the browser contract only. DB-backed end-to-end validation remains blocked until the isolated PostGIS database is available.
