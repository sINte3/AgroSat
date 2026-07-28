# TASK_209 Vector and Raster Architecture Contract

## Status and scope

This document defines the Phase 7 contract for replacing the full-geometry map path with bounded vector delivery and for removing Sentinel/NDVI coupling from React.

The legacy `GET /api/fields/geojson/all` contract remains available for compatibility, but the primary map must not use it after the Phase 7 frontend cutover.

## Field vector-tile contract

### Endpoints

- `GET /api/field-tiles/metadata`
- `GET /api/field-tiles/{z}/{x}/{y}.mvt`

Both endpoints require an active authenticated user.

Admin may request global scope or one explicit enterprise. Manager, agronomist, and viewer are limited to their assigned enterprise. An explicit cross-enterprise request is rejected without executing the tile query.

### Bounds

- Minimum zoom: 3.
- Maximum zoom: 18.
- `x` and `y` must satisfy `0 <= coordinate < 2^z`.
- MVT extent: 4096.
- MVT buffer: 64.
- Source layer: `fields`.
- Schema version: `task209_field_mvt_v1`.

Every tile query starts with `ST_TileEnvelope`. The field predicate uses the existing EPSG:4326 geometry with `&&` and `ST_Intersects` against the tile envelope transformed to EPSG:4326, allowing the existing `idx_fields_geometry` GiST index to remain usable. Geometry is transformed to EPSG:3857 only after spatial filtering.

Simplification is zoom-bounded and topology-preserving:

| Zoom | Tolerance |
|---|---:|
| 3–7 | 150 m |
| 8–10 | 40 m |
| 11–13 | 10 m |
| 14–18 | 0.5 m |

### Stable MVT properties

- `id`
- `name`
- `code`
- `enterprise_id`
- `enterprise_name`
- `area_ha`
- `centroid_lat`
- `centroid_lon`
- `irrigation_type`
- `current_crop`
- `last_ndvi`
- `last_ndvi_date`
- `ndvi_change_pct`
- `active_alerts`
- `alert_severity`

Dynamic coverage, freshness, per-index values, hover, and selection are MapLibre feature state. They are not a second geometry source and do not cause tile-source replacement.

### Metadata

Metadata returns the effective scope, scope bounds, active field count, source layer, zoom bounds, schema version, property names, and a relative tile template. Empty scope returns `field_count = 0` and `bounds = null`.

Metadata uses one constant SQL statement. Each uncached tile uses one constant SQL statement. Neither endpoint uses ORM relationship loading.

### Caching

Tile cache keys include schema version, effective tenant scope, z, x, and y. Global admin and each enterprise have distinct namespaces. Field mutation invalidates metadata and tile namespaces only after a successful database commit.

Responses use `private` cache control and an ETag derived from the authorized tile bytes. `If-None-Match` may return 304. Redis failure falls through to PostgreSQL and does not change response truth.

## Raster-provider contract

### Generic endpoints

- `GET /api/raster/fields/{field_id}/metadata`
- `GET /api/raster/fields/{field_id}/image`

Inputs include:

- `index_code`
- `date_to` for metadata or `observation_date` for an exact image
- `size`

Metadata includes:

- provider-neutral schema version
- field and tenant-scoped observation identity
- index code
- observation date
- field/bbox
- quality and provenance
- supported sizes
- legend and limitations
- relative image template

The React map consumes only the generic contract. Provider credentials, Sentinel evalscripts, and provider selection never enter frontend code.

### Provider boundary

A provider implements:

- capability check by index code
- deterministic cache identity
- bounded timeout
- cancellation-compatible request execution
- sanitized error classification
- byte and media-type validation
- provenance without credentials

Phase 7 initially registers one real `sentinel_process` provider for NDVI. Unsupported indices return an explicit 422 response. Missing credentials return a closed 503 response. A mock provider is never selected in production paths.

### Compatibility

Existing `/api/ndvi-raster/*` endpoints remain operational as tested compatibility adapters. They use the same provider implementation and preserve their current response fields, media type, cache headers, and error status semantics.

## MapLibre ownership

The field-map owner creates one vector source and the field fill, border, and label layers. A style replacement or unmount removes:

- field layers
- field source
- raster layer and source
- event listeners
- timers
- draw control
- AbortControllers
- object URLs

Tile requests are owned by MapLibre and use its cancellation behavior. Generic raster metadata/image requests use an AbortController plus a generation check. A stale field, index, date, route, or ownership generation cannot render.

Coverage and index changes update feature state and paint expressions; they do not replace the vector source. Camera movement must not fetch the full dataset.

## Evidence rules

Before/after comparisons must label deterministic fixtures as fixtures. Real field counts, PostGIS EXPLAIN, production payload sizes, and live Sentinel validation remain blocked unless the isolated prerequisites become available. No Wialon-equivalent or production-performance claim is permitted from fixture evidence.
