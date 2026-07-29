# TASK_209 Variable-Rate Recommendation Safety Contract

## Decision boundary

AgroSat creates reviewable variable-rate recommendation drafts. It does not create autonomous agronomic prescriptions.

Satellite observations, pixel anomalies, and productivity zones may provide context, but they cannot determine fertilizer, seed, pesticide, or irrigation rates. Every numeric rate is entered by an authorized human and remains bounded
by explicit human-entered agronomic minimum and maximum values.

## Required inputs

A draft requires:

- one authorized field;
- one ready `yield_grid_stability_v1` productivity-zone run for that field;
- crop and season context;
- recommendation kind;
- rate unit;
- explicit minimum and maximum rates;
- explicit low, medium, and high zone rates;
- equipment capability and compatibility notes;
- an agronomist safety acknowledgement;
- sanitized provenance and an idempotency key.

Supported initial kind/unit pairs are:

- `fertilizer` with `kg_ha`;
- `seed` with `seeds_ha`;
- `pesticide` with `l_ha`;
- `irrigation` with `mm`.

The system rejects missing zone classes, non-finite or negative rates, rates
outside the stated bounds, and unit/kind mismatches.

## Status and approval

The state machine is:

```text
draft -> approved
draft -> rejected
approved -> superseded by a new version
rejected -> superseded by a new version
```

Creating a draft does not approve it. Approval is restricted to admin and manager roles, requires an explicit confirmation and a safety note, and records
the approver and Asia/Tashkent-aware timestamp. Agronomists may create drafts
inside their tenant but cannot approve them. Viewers are read-only.

No silent overwrite is permitted. Revision conflicts return `409`. A new
version links to the prior recommendation and preserves immutable audit events.

## Tenant and provenance boundary

Every recommendation, zone rate, and audit event stores the enterprise scope.
The field, productivity run, and productivity zones must belong to the same
enterprise and field. Cross-tenant object identifiers return `404`.

The source run, its algorithm version, input imports, confidence, and
recommendation creator remain visible. Low-confidence or materially unzoned
source data is displayed as a warning and is never hidden by approval.

## Export

The supported deterministic export is GeoJSON:

- one feature per available productivity class;
- EPSG:4326 geometry from the approved source productivity run;
- human-entered rate and unit;
- recommendation version and status;
- source algorithm and confidence;
- safety statement and sanitized provenance.

Draft exports are visibly marked `draft`. Export does not convert units and
does not claim equipment-ready prescription compatibility.

## API

- `POST /api/variable-rate-recommendations`
- `GET /api/variable-rate-recommendations`
- `GET /api/variable-rate-recommendations/{recommendation_id}`
- `POST /api/variable-rate-recommendations/{recommendation_id}/approve`
- `POST /api/variable-rate-recommendations/{recommendation_id}/reject`
- `GET /api/variable-rate-recommendations/{recommendation_id}/export.geojson`

All lists are bounded. Writes are idempotent and use optimistic version checks.
Reads use explicit joins without SQLAlchemy lazy loading.

## Frontend

The field yield view shows:

- source productivity run, seasons, confidence, and uncovered area;
- kind and unit;
- human-entered minimum, maximum, and three zone rates;
- equipment capability;
- draft, approved, rejected, or superseded state;
- explicit human-validation warning;
- role-aware create and approval actions;
- deterministic export.

The form is mobile accessible, labels every input, exposes validation and
conflict states, has 44-pixel touch targets, and cancels stale requests.

## External prerequisite

Migration, transaction, concurrency, real PostGIS export, and database-backed
browser validation remain blocked until B-001 provides an isolated
PostgreSQL/PostGIS environment.
