# TASK_209 Pixel Anomaly Contract

## Purpose and safety boundary

Pixel anomalies identify spatially connected parts of a field whose spectral
signal differs materially from a comparison observation and from the current
within-field baseline. They are inspection candidates, not agronomic diagnoses.

The system must never label an anomaly as a confirmed irrigation, nutrient,
pest, disease, or yield problem without field evidence. The UI and exports must
show the observed index change, data quality, algorithm version, thresholds,
and confidence.

## Initial capability

- API index vocabulary: `ndvi`, `savi`, `evi`, `ndmi`, `ndre`.
- Initial production pixel provider capability: `ndvi`.
- Unsupported provider/index combinations return `unsupported_data`.
- No mock or colorized PNG may be interpreted as numeric pixel values.
- Deterministic matrix fixtures are allowed only in tests and explicit dry-run
  diagnostics; fixture results cannot be persisted in apply mode.

## Scene contract

Each scene contains:

- field ID and enterprise ID;
- index code;
- observation record type and record ID;
- observation date;
- numeric two-dimensional pixel grid;
- same-shaped provider quality mask;
- same-shaped field-intersection mask;
- EPSG:4326 bounding box;
- pixel width and height;
- cloud-cover percentage;
- within-field valid-pixel percentage;
- provider and processing provenance.

Grid values must be finite and inside `[-1, 1]`. All masks use `true` for an
eligible pixel. The current and comparison scene must have identical dimensions,
bounds, field, enterprise, and index.

## Quality gates

The default run requires:

- cloud cover at or below 30%;
- within-field valid pixels at or above 60%;
- at least 25 eligible field pixels;
- comparison observation earlier than current observation;
- temporal separation of at least 5 days and at most 60 days.

If a required gate fails, the run result is `insufficient_data`, contains reason
codes, and stores no anomaly geometry.

## Baseline and candidate rule

Algorithm version: `paired_within_field_drop_v1`.

For every eligible paired pixel:

1. Calculate the median of all eligible current-scene pixels.
2. Calculate `paired_delta = current - comparison`.
3. A candidate pixel requires both:
   - `current <= current_median - within_field_delta`;
   - `paired_delta <= -comparison_drop`.

Default thresholds:

- `within_field_delta = 0.12`;
- `comparison_drop = 0.10`;
- `minimum_connected_pixels = 4`;
- `minimum_area_ha = 0.05`.

Thresholds are explicit, bounded inputs and are stored with every run. Four-way
orthogonal connectivity defines a component. Components below either minimum are
discarded. Each accepted component is emitted as valid EPSG:4326 polygonal
geometry clipped to the field mask.

The score is a bounded `[0, 1]` combination of median paired drop and current
within-field deficit. Confidence is a bounded `[0, 1]` combination of valid
pixel share, temporal proximity, component size, and mask quality. Score and
confidence are measurement summaries, not causality.

## Temporal classification

- `single_scene`: no prior overlapping anomaly exists for the algorithm/index.
- `persistent`: the current component overlaps a prior component by at least
  30% of the smaller component area.
- `recovering`: a prior component overlaps the current field area, but the
  current component area or median drop is at least 30% smaller.
- `insufficient_data`: quality or temporal gates prevent a comparison.

Persistence evaluation uses stored anomaly geometry and the same algorithm
version. It does not infer a cause.

## Storage contract

`pixel_anomaly_runs` stores:

- tenant, field, index, current and comparison observations;
- algorithm version and deterministic run key;
- threshold hash and full bounded thresholds;
- quality summary, result status, and provenance;
- start, completion, and creation timestamps.

`pixel_anomalies` stores:

- run, tenant, field, and index;
- algorithm version and deterministic zone key;
- valid EPSG:4326 polygonal geometry;
- area hectares, score, severity, persistence count, classification,
  confidence, status, quality summary, and provenance;
- created and updated timestamps.

`pixel_anomaly_inspections` links one anomaly to at most one field inspection.
Every table is explicitly tenant-scoped and indexed. Runs and zones are
deduplicated. No raster pixel arrays are stored in PostgreSQL.

## Processing job

The canonical job is a standalone CLI and is never started by FastAPI.

It requires:

- bounded field batch (`1..100`);
- one explicit index;
- bounded current/comparison date window;
- dry-run by default and explicit `--write` for persistence;
- a process lock;
- run UUID and structured sanitized JSON summary;
- deterministic run/zone keys;
- bounded provider timeout and cancellation;
- resume checkpoint;
- one transaction per field;
- idempotent conflict recovery.

Fixture input is rejected with `--write`. Missing numeric pixel capability fails
closed as `unsupported_data`.

## API contract

- `GET /api/pixel-anomalies/fields/{field_id}/summary`
- `GET /api/pixel-anomalies`
- `GET /api/pixel-anomalies/{anomaly_id}`
- `GET /api/pixel-anomalies/{anomaly_id}/geometry`
- `POST /api/pixel-anomalies/{anomaly_id}/inspection`

Read endpoints allow active `admin`, `manager`, `agronomist`, and `viewer` roles
within their tenant scope. Inspection creation denies `viewer`. Cross-tenant
objects return non-enumerable 404 where an object ID is supplied. List and
summary endpoints use the same tenant predicates and bounded pagination.

The create-inspection endpoint requires an idempotency key, uses the existing
field-inspection authorization and conflict contract, links the inspection to
the anomaly transactionally, and never creates multiple inspections for one
anomaly.

## Frontend contract

Field analytics presents:

- anomaly overlay and bounded zone list;
- current and comparison dates;
- single-scene, persistent, recovering, and insufficient-data states;
- area, score, confidence, and quality warnings;
- explicit non-diagnostic copy;
- create-inspection action for writable roles;
- linked inspection status;
- provider and algorithm provenance.

The owner removes its MapLibre layers, sources, listeners, popups, pending
requests, and stale results on unmount or field/index/date change.

## External blockers

- B-001 blocks migration execution, PostGIS geometry validation, real area
  reconciliation, spatial query plans, and database-backed E2E.
- B-003 blocks Playwright MCP map lifecycle acceptance.
- B-004 blocks live numeric Sentinel pixel-scene validation.
