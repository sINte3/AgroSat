# TASK_209 Productivity Zone Contract

## Decision boundary

Productivity zones summarize repeated measured yield patterns. They are not derived from satellite indices alone and are not agronomic prescriptions.

The first algorithm is `yield_grid_stability_v1`.

## Minimum evidence

A field is eligible only when all conditions hold:

- at least three distinct accepted yield-map seasons;
- at least 20 valid measured points per selected season;
- at least 60 measured points in total;
- all inputs use normalized `t_ha`;
- every eligible spatial cell contains evidence from at least two seasons.

Failure returns `insufficient_data` with explicit reason codes and does not
persist fabricated zone geometry.

## Season normalization

Each season is normalized independently:

1. compute the season median;
2. compute median absolute deviation (MAD);
3. convert each measurement to a robust z-score using `1.4826 * MAD`;
4. clamp the score to `-3..3`;
5. map it to `0..1`.

A season with zero MAD is insufficient because it cannot support relative
spatial differentiation.

## Spatial rules

- Coordinates enter and leave the API as EPSG:4326.
- Points are assigned to a deterministic 30 metre local grid anchored to the
  field input centroid.
- A cell requires evidence from at least two selected seasons.
- Cell score is the median of its normalized observations.
- Smoothing uses the cell with weight 2 and its eight immediate neighbours with
  weight 1.
- Cells are classified by deterministic tercile thresholds into `low`,
  `medium`, and `high`.
- Cell rectangles are clipped to the authorized field in PostGIS.
- Rectangles of the same class are unioned into valid EPSG:4326 multipolygons.

No interpolation beyond eligible grid cells is claimed.

## Confidence

Confidence is a bounded `0..1` evidence score based on:

- selected season count, capped at five seasons;
- total point count, capped at 200 points;
- fraction of retained cells observed in all selected seasons;
- zoned-area coverage relative to field area.

Confidence communicates evidence support, not causal certainty.

## Area reconciliation

For a ready run:

- every zone geometry is valid and non-empty;
- zone geometries are clipped to the field;
- zones do not overlap;
- each zone area is positive;
- the sum of zone areas equals `zoned_area_ha` within `0.01 ha`;
- `zoned_area_ha` cannot exceed `field_area_ha` beyond `0.01 ha`;
- `area_delta_ha = field_area_ha - zoned_area_ha`.

Unzoned area is reported explicitly and is not silently assigned a class.

## Provenance and reproducibility

Each run stores:

- field and tenant;
- algorithm and version;
- sorted season list;
- sorted source import identifiers and source SHA-256 values;
- parameters;
- point and eligible-cell counts;
- result status and reason codes;
- confidence;
- field, zoned, and unzoned areas;
- a deterministic 64-character run key;
- timestamps and sanitized provenance.

The run key hashes the field, algorithm version, parameters, seasons, import
identities, and source hashes. The same inputs produce the same run key and
identical cell classifications.

## Execution and idempotency

Computation runs through a standalone bounded CLI, never a FastAPI scheduler or
lifespan hook. It supports:

- explicit field identifiers;
- bounded season selection;
- dry-run;
- apply only against an authorized isolated database;
- a run UUID;
- idempotent run keys;
- structured sanitized JSON output;
- bounded batches;
- cancellation;
- checkpoint/resume.

No mock or synthetic result is written in production mode.

## Authorization and API

- `GET /api/productivity-zones/fields/{field_id}` returns the latest run summary.
- `GET /api/productivity-zones/runs/{run_id}` returns one tenant-scoped run.
- `GET /api/productivity-zones/runs/{run_id}/zones` returns bounded geometry.

All roles may read within their tenant scope. Computation is an operations CLI,
not a browser write. Cross-tenant identifiers return `404`.

## Frontend

The field view shows:

- `insufficient_data` and reason codes;
- selected seasons and input provenance;
- low/medium/high zones;
- area and unzoned area;
- confidence and its explanation;
- algorithm version;
- the statement that zones are historical evidence, not a prescription.

Map ownership must remove zone layers, sources, listeners, and pending requests
on field, route, or component change.

## External prerequisite

Migration, PostGIS clipping/union, real field-area reconciliation, and
database-backed CLI apply remain blocked until B-001 provides an isolated
PostgreSQL/PostGIS environment.
