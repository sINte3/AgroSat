# TASK_209 operational closure and observation verification contract

Status: implementation contract, not production-applied.

## Current-domain inventory

The current branch has an explicit-SQL `field_inspections` workflow introduced
by Alembic revision `0005_field_inspections`. Its states are `pending`,
`in_progress`, `completed`, and `cancelled`; it already has tenant scope,
assignment, optimistic version checks, creation idempotency, bounded listing,
and no ORM relationship loading. Completion currently stores only
`completion_summary`. It does not represent a corrective action, operational
closure, reopen history, evidence metadata, or later-observation verification.

The attention queue is a read-only derived view over active fields, alerts,
legacy `ndvi_records`, and `satellite_index_records`. It explicitly states that
spectral signals are indirect and not an agronomic diagnosis. Alerts have an
acknowledgement lifecycle but are not operational actions. Users have one role
and an optional enterprise. Tenant roles are `manager`, `agronomist`, and
`viewer`; `admin` is global. SQLAlchemy relationships use `raise_on_sql`.

Accepted satellite index codes are `ndvi`, `savi`, `evi`, `ndmi`, and `ndre`.
Current quality boundaries are at least 50 percent valid pixels and no more
than 30 percent cloud cover. NDVI observations use `ndvi_records`; the other
supported indices use `satellite_index_records`.

## Aggregate ownership

An inspection remains the field-work assignment. A structured inspection
result is a one-to-one completion record. An inspection can have multiple
corrective actions. An action can have multiple verification cycles across
reopens, but only one unresolved verification request at a time. Every entity
copies `enterprise_id` for indexed tenant enforcement and also retains its
parent foreign key. Cross-enterprise parent/child combinations are rejected by
service queries and migration constraints where PostgreSQL can express them.

Binary photo upload is out of scope because the current repository has no
approved object-storage contract. Evidence stores metadata only through a
provider boundary. `metadata_only` is the deterministic test provider. A
production provider must return an opaque storage reference and must never put
provider credentials or signed URLs in audit events.

## State machines

Inspection:

```text
pending -> in_progress -> completed
   |            |
   +----------> cancelled
```

The new structured-result write is permitted only from `in_progress` and
atomically moves the inspection to `completed`. The legacy completion endpoint
remains compatible and does not fabricate a confirmed cause.

Corrective action:

```text
open -> in_progress -> blocked -> in_progress
  |          |             |
  +----------+-------------+-> closed
closed -> open  (reopen with reason)
```

`closed` requires `closure_reason`, `closed_by_id`, and `closed_at`. Every other
state requires those fields to be null. Reopen clears the closure fields,
records the latest reopen metadata, increments `version`, and appends an audit
event. The audit timeline preserves all earlier close/reopen events.

Verification:

```text
awaiting_observation -> resolved
```

A resolved result is exactly one of `improved`, `unchanged`, `worsened`, or
`insufficient_data`. Reopening an action does not delete an earlier result.
Closing it again can create a new verification cycle.

## Structured values

Confirmed-cause codes are:

```text
irrigation
pest
disease
nutrient
weather
soil
mechanical
crop_stage
no_issue
other
unconfirmed
```

`unconfirmed` is an explicit result, not a diagnosis. Cause details and
evidence notes are bounded text. Optional evidence geolocation is a PostGIS
`POINT` in EPSG:4326. Corrective action description, owner, due date, status,
closure reason, and reopen reason are separate fields rather than encoded in
free text.

Evidence metadata includes evidence type, provider, opaque provider reference,
original filename, media type, byte size, SHA-256, capture time, optional
EPSG:4326 point, and bounded provider metadata. No binary content is stored by
this phase.

## Role and tenant transitions

All reads are tenant-scoped for tenant roles and global for `admin`.
Cross-tenant identifiers return non-enumerable 404 responses. `viewer` is
read-only.

- `admin`: all transitions across enterprises after explicit object lookup.
- `manager`: all transitions inside the assigned enterprise.
- `agronomist`: record a result only for an assigned in-progress inspection;
  add evidence to that inspection; create an action from that result; update or
  close an action only when assigned as action owner; cannot assign another
  owner or reopen.
- `viewer`: list/detail/timeline only.

An owner must be active, belong to the action enterprise, and have role
`manager` or `agronomist`. Reads, lists, overdue counts, awaiting-verification
counts, timelines, and exports must use identical tenant predicates.

## Write safety

Every new write requires `Idempotency-Key` and an optimistic
`expected_version` when it changes an existing aggregate. Each successful write
creates one `operational_audit_events` row with actor, enterprise, bounded event
type, entity references, request fingerprint, idempotency key, and sanitized
JSON metadata. `(actor_id, idempotency_key)` is unique. Reusing a key with a
different fingerprint returns 409. A same-fingerprint replay returns the
current authorized entity without repeating the transition.

Transactions lock the target aggregate row with `FOR UPDATE`; they perform the
authorized state predicate and version predicate in the same SQL statement.
There is no lazy loading.

## Next-observation eligibility and result

Algorithm version: `observation_direction_v1`.

1. The action must be closed and belong to the same enterprise and field as the
   request.
2. The index must be one of the five supported codes.
3. Reference date is the action `closed_at` date in Asia/Tashkent.
4. Reference observation is the latest same-field, same-index accepted
   observation on or before the reference date.
5. Candidate observation must be same-field and same-index, strictly later than
   both the reference observation and reference date, and at least three
   calendar days after the reference date.
6. Both observations require non-null valid-pixel and cloud values, at least
   50 percent valid pixels, and at most 30 percent cloud.
7. No candidate, missing reference, or rejected quality resolves explicitly as
   `insufficient_data`; a wrong-date record is never reused.
8. Values are rounded to four decimal places. A higher later value is reported
   as `improved`, an equal value as `unchanged`, and a lower value as
   `worsened`.

The direction is only an observed spectral change. It is not proof that the
action caused the change and is not an agronomic diagnosis. The API returns
both values, dates, delta, quality, provenance, algorithm version, and this
limitation.

Verification confidence is `high` only when both observations have at least
80 percent valid pixels and at most 10 percent cloud. Other accepted pairs are
`medium`. `insufficient_data` has `low` confidence.

## API contract

All collections use `limit <= 200` and `offset <= 10000`.

```text
POST /api/field-inspections/{id}/result
POST /api/field-inspections/{id}/evidence
POST /api/field-inspections/{id}/actions
PATCH /api/operational-actions/{id}
POST /api/operational-actions/{id}/close
POST /api/operational-actions/{id}/reopen
POST /api/operational-actions/{id}/verification-requests
POST /api/verification-requests/{id}/resolve
GET /api/operational-actions?overdue_only=...
GET /api/operational-actions?awaiting_verification=...
GET /api/field-inspections/{id}/timeline
```

No read endpoint writes. Timeline rows are ordered by `(occurred_at, id)`,
bounded, and contain no user email, provider credential, binary evidence, or
signed storage URL.

## Migration and rollout

The migration is nullable-first for references to existing inspections and
does not backfill a confirmed cause. It creates new tables and indexes only.
The program never applies it to production. Downgrade is safe only before any
production closure data exist; after data entry it is destructive and the
release classification is roll-forward-only.

Runtime upgrade, downgrade, constraint, and end-to-end tests require the
isolated PostGIS prerequisite B-001. Static migration and service-contract
tests may proceed independently, but they do not replace those runtime gates.
