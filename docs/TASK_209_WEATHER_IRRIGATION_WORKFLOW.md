# TASK 209 Weather and Irrigation Inspection Workflow

## Purpose

Weather, irrigation, satellite, and field evidence must feed the existing inspection, corrective-action, closure, and next-observation verification loop. They must not create a separate advisory dashboard or convert a vegetation-index movement into a confirmed agronomic diagnosis.

## Evidence boundary

- Weather observations and forecasts are contextual evidence with provider, fetched-at time, provider observation or forecast time, timezone, coordinates, and availability status.
- `NDMI`, `NDVI`, or another satellite index may create a `water_stress_suspicion` reason. It cannot confirm irrigation failure, insufficient delivery, equipment failure, or a required application rate.
- An irrigation event is a bounded human-reported operational record. It is not proof of crop response.
- A confirmed irrigation or weather cause requires an inspection result recorded through the operational-closure workflow.
- A later eligible satellite observation may show an improved, unchanged, worsened, or insufficient-data outcome. It does not establish causality by itself.

## Inspection source

`field_inspections.source` adds `irrigation_context`.

An irrigation-context inspection requires:

- a field-authorized user;
- a structured priority;
- a context date;
- one or more reason codes;
- no attention score.

Supported initial reason codes are:

- `water_stress_suspicion`;
- `weather_water_deficit`;
- `irrigation_interruption`;
- `irrigation_delivery_check`;
- `irrigation_equipment_check`.

The existing `attention_queue` and `manual` contracts remain unchanged.

## Irrigation event record

`irrigation_events` stores:

- tenant and field;
- optional linked inspection;
- recorder;
- idempotency key and payload fingerprint;
- event type;
- occurrence time;
- method code;
- optional bounded water amount in millimetres;
- evidence source;
- optional bounded note;
- version and audit timestamps.

Event types:

- `irrigation_applied`;
- `irrigation_interrupted`;
- `equipment_issue`;
- `field_observation`.

Method codes:

- `canal`;
- `drip`;
- `sprinkler`;
- `furrow`;
- `manual`;
- `unknown`.

The first provider supports only `human_reported` evidence. Provider-imported irrigation events remain unsupported until an authoritative provider contract exists.

## API

`GET /api/irrigation-context/fields/{field_id}`:

- authenticates and authorizes the field before cache or provider access;
- returns weather context and at most 100 recent irrigation events;
- uses explicit SQL and no lazy loading;
- returns an explicit unavailable weather state rather than fabricated data;
- reports whether an active inspection already exists.

`POST /api/irrigation-context/fields/{field_id}/events`:

- denies viewers;
- validates tenant, field, optional inspection linkage, role, units, timestamps, and bounds;
- requires an `Idempotency-Key`;
- returns the same record for a repeated key and payload;
- returns `409` when the same key is reused with different content;
- invalidates only tenant-safe field irrigation-context cache keys after commit.

Inspection creation continues through `POST /api/field-inspections` with `source=irrigation_context`. Corrective actions, close/reopen, and next-observation verification continue through the existing operational-closure APIs.

## Roles and tenant scope

- `admin`: read and record for any authorized field.
- `manager`: read and record within the assigned enterprise.
- `agronomist`: read and record within the assigned enterprise; may create an irrigation-context inspection assigned only to self unless an existing policy allows otherwise.
- `viewer`: read-only within the assigned enterprise.
- Cross-tenant and unknown field or inspection identifiers return the same non-enumerating result used by the existing field authorization policy.

## Weather provider behavior

The Open-Meteo boundary uses:

- explicit coordinates;
- `Asia/Tashkent`;
- bounded forecast horizon;
- explicit timeout;
- bounded response parsing;
- provider and retrieval provenance;
- `available` or `unavailable` status;
- no credentials;
- no production mock fallback.

The cache key includes enterprise and field identifiers. Cache failure does not change the source-of-truth result.

## Migration and rollback

The migration:

- adds `irrigation_context` to the field-inspection source constraint;
- creates `irrigation_events` with foreign keys, checks, unique idempotency, tenant/field time indexes, and version/timestamp invariants;
- uses a safe nullable optional inspection link;
- has a downgrade that drops the new table and restores the previous source constraint only after proving no irrigation-context inspections remain.

No migration is applied to production by TASK 209.

## Acceptance

- schema contract and upgrade/downgrade source tests pass;
- role and cross-tenant tests pass;
- event idempotency and conflict tests pass;
- field/inspection linkage is validated;
- context reads are bounded and explicit;
- weather provenance and unavailable state are tested;
- irrigation-context inspection flows into result, action, closure, and later observation verification;
- no satellite or weather context is rendered as a confirmed irrigation failure without inspection evidence.
