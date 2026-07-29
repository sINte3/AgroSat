# TASK_209 Yield Map Import Contract

## Purpose and safety boundary

AgroSat imports measured yield observations as field evidence. It does not infer
yield from satellite indices and does not treat an uploaded file as trusted until
the preview contract passes.

The first supported provider-neutral schema is `yield_point_csv_v1`. A frontend
may parse a local CSV into the same structured request, but the backend remains
the authority for validation, normalization, field intersection, idempotency,
and persistence.

Production upload and production database execution are outside TASK_209.

## Supported schema inventory

`yield_point_csv_v1` requires these logical columns:

| Column | Type | Contract |
| --- | --- | --- |
| `longitude` | decimal | EPSG:4326, `-180..180` |
| `latitude` | decimal | EPSG:4326, `-90..90` |
| `yield_value` | decimal | finite and positive |
| `observed_at` | timestamp | timezone-aware ISO 8601 |

Optional columns are `machine_point_id`, `speed_kph`, and `moisture_pct`.
Unknown columns are ignored by the CSV adapter and are not persisted.

Accepted yield units are:

- `t_ha`, stored without scaling;
- `kg_ha`, divided by 1000 and stored as `t_ha`.

Crop-dependent units such as bushels per acre are unsupported because a safe
conversion requires a crop-specific mass contract. Unsupported units fail the
entire preview instead of being guessed.

## Import metadata

Every request includes:

- `field_id`;
- `season_year`;
- `crop_code`;
- `schema_code = yield_point_csv_v1`;
- `source_filename`, reduced to a safe basename;
- `source_sha256`, computed from the exact local source bytes;
- `source_provider`, the source provider identifier;
- optional `machine_id` and `machine_model`;
- `yield_unit`;
- bounded structured rows.

The API never stores an arbitrary source path, raw upload bytes, access token,
or provider credential.

## Preview

`POST /api/yield-map-imports/preview` is read-only. It:

1. authorizes the field and tenant;
2. validates at most 5,000 rows;
3. normalizes supported units to `t_ha`;
4. rejects duplicate `machine_point_id` values within the request;
5. rejects invalid coordinates, timestamps, and non-finite measurements;
6. rejects values outside `0.01..100 t/ha`;
7. flags statistical outliers using a deterministic median absolute deviation
   rule when at least seven valid rows exist;
8. sends all otherwise valid coordinates through one bounded PostGIS
   `ST_Covers` statement;
9. returns accepted rows, rejected rows, warnings, counts, bounds, and summary
   statistics without writing.

An outlier is rejected with `yield_statistical_outlier`; it is not silently
clipped or winsorized. The user must correct the source or explicitly prepare a
new source file with a new hash.

Rows outside the selected field are rejected with `outside_field`.

## Accept

`POST /api/yield-map-imports` requires:

- the same validated payload;
- an `Idempotency-Key`;
- the preview fingerprint returned by the preview;
- explicit `confirm = true`;
- zero rejected rows.

The backend recomputes the preview and fingerprint. It inserts one import and
all points in one transaction. A repeated key and identical request returns the
existing import. Reusing a key with a different request returns `409`.

The source identity is unique within enterprise, field, season, schema, and
source hash. A second actor cannot duplicate the same source under a different
idempotency key.

## Persistence

`yield_map_imports` stores tenant, field, season, crop, schema, safe source
metadata, source hash, input and normalized units, counts, summary statistics,
provenance, actor, request fingerprint, status, and timestamps.

`yield_map_points` stores tenant, field, import, source row number, optional
machine point identity, observation time, `POINT` geometry in EPSG:4326,
normalized `yield_t_ha`, optional speed and moisture, quality flags, and
timestamps.

Every tenant-scoped table has an enterprise foreign key and indexes. Point
geometry has a GiST index. The application uses explicit SQL only; no lazy loading
is introduced.

## Authorization

- `admin`, `manager`, and `agronomist` may preview and accept within authorized
  field scope.
- `viewer` may list and inspect accepted import metadata but cannot preview or
  accept an import.
- Cross-tenant field and import identifiers return `404`.
- Unknown and disabled roles fail through the shared authorization policy.

## Read API

- `GET /api/yield-map-imports?field_id=...&limit=...&offset=...`
- `GET /api/yield-map-imports/{import_id}`
- `GET /api/yield-map-imports/{import_id}/points?limit=...&offset=...`

Lists are bounded and tenant-scoped. Raw full-dataset geometry is not returned
without pagination.

## Frontend workflow

The field yield tab provides:

1. local `.csv` selection;
2. explicit schema and unit selection;
3. local SHA-256 calculation;
4. preview request;
5. accepted/rejected counts and row-level rejection reasons;
6. explicit confirmation;
7. accepted import summary;
8. viewer read-only history.

The frontend never sends a local filesystem path and never accepts without a
successful backend preview. It cancels stale requests when the field, file,
component ownership, or route changes.

## External prerequisite

Migration and database-backed acceptance require a dedicated isolated
PostgreSQL/PostGIS target. Until B-001 is resolved, only offline migration
compilation, pure validation fixtures, explicit-SQL contract tests, and
deterministic browser fixtures may pass.
