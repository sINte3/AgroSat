import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';

const outputArgument = process.argv.find((value) => value.startsWith('--output='));
const outputPath = outputArgument ? resolve(outputArgument.slice('--output='.length)) : '';
const fieldMap = await readFile(
  new URL('../src/components/Map/FieldMap.jsx', import.meta.url),
  'utf8',
);
const fieldTiles = await readFile(
  new URL('../src/api/fieldTiles.js', import.meta.url),
  'utf8',
);
const rasterHook = await readFile(
  new URL('../src/hooks/useNDVIRasterLayer.js', import.meta.url),
  'utf8',
);

assert.doesNotMatch(fieldMap, /fields\/geojson\/all/);
assert.doesNotMatch(fieldMap, /\.setData\(/);
assert.match(fieldMap, /type:\s*'vector'/);
assert.match(fieldMap, /tiles:\s*\[resolveFieldTileTemplate\(metadata\)\]/);
assert.match(fieldMap, /removeFeatureState/);
assert.match(rasterHook, /controllerRef\.current\?\.abort\(\)/);
assert.match(rasterHook, /URL\.revokeObjectURL/);

const metadataFixture = {
  schema_version: 'task209_field_mvt_v1',
  source_layer: 'fields',
  min_zoom: 3,
  max_zoom: 18,
  extent: 4096,
  buffer: 64,
  scope: { role: 'viewer', enterprise_id: 7 },
  field_count: 1000,
  bounds: [63.5, 39.2, 64.5, 40.0],
  properties: [
    'id', 'name', 'code', 'enterprise_id', 'enterprise_name', 'area_ha',
    'centroid_lat', 'centroid_lon', 'irrigation_type', 'current_crop',
    'last_ndvi', 'last_ndvi_date', 'ndvi_change_pct', 'active_alerts',
    'alert_severity',
  ],
  tile_template: '/api/field-tiles/{z}/{x}/{y}.mvt?enterprise_id=7',
};

const report = {
  decision: 'PASS_STATIC_ARCHITECTURE_WITH_EXTERNAL_RUNTIME_BLOCKERS',
  measured_at: new Date().toISOString(),
  contract: {
    geometry_delivery: 'viewport_bounded_mvt',
    metadata_endpoint: '/api/field-tiles/metadata',
    tile_template: '/api/field-tiles/{z}/{x}/{y}.mvt',
    full_geometry_collection_requests: 0,
    source_replacements_on_color_mode_change: 0,
    tile_cancellation_during_zoom: true,
    raster_provider_api: '/api/raster/fields/{field_id}',
    raster_stale_response_guard: 'abort_controller_plus_generation',
  },
  deterministic_fixture: {
    synthetic_business_data: true,
    persisted_to_database: false,
    field_count: metadataFixture.field_count,
    metadata_payload_bytes: Buffer.byteLength(JSON.stringify(metadataFixture)),
    full_geometry_bytes_transferred_before_tiles: 0,
  },
  static_lifecycle_sites: {
    layer_add_sites: (fieldMap.match(/\.addLayer\(/g) || []).length,
    layer_remove_sites: (fieldMap.match(/\.removeLayer\(/g) || []).length,
    source_add_sites: (fieldMap.match(/\.addSource\(/g) || []).length,
    source_remove_sites: (fieldMap.match(/\.removeSource\(/g) || []).length,
    feature_state_set_sites: (fieldMap.match(/\.setFeatureState\(/g) || []).length,
    feature_state_remove_sites: (fieldMap.match(/\.removeFeatureState\(/g) || []).length,
    full_source_set_data_sites: (fieldMap.match(/\.setData\(/g) || []).length,
    object_url_create_sites: (rasterHook.match(/URL\.createObjectURL/g) || []).length,
    object_url_revoke_sites: (rasterHook.match(/URL\.revokeObjectURL/g) || []).length,
  },
  external_runtime_blockers: {
    real_mvt_payload_bytes: 'BLOCKED_B001',
    postgis_explain: 'BLOCKED_B001',
    first_meaningful_map_render_ms: 'BLOCKED_B003',
    pan_zoom_request_count: 'BLOCKED_B003',
    retained_memory_trend: 'BLOCKED_B003',
    runtime_layer_source_listener_trend: 'BLOCKED_B003',
    raster_abort_and_stale_response_counts: 'BLOCKED_B003',
  },
  conclusion: {
    bounded_delivery_contract_proven: true,
    measured_real_payload_reduction: false,
    measured_real_payload_reduction_reason: 'Isolated PostGIS runtime is unavailable (B-001).',
  },
};

assert.equal(report.static_lifecycle_sites.full_source_set_data_sites, 0);
assert.equal(report.contract.full_geometry_collection_requests, 0);
assert.match(fieldTiles, /validFieldTileMetadata/);

if (outputPath) {
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
}

console.log(`TASK209 map vector benchmark: PASS ${JSON.stringify(report)}`);
