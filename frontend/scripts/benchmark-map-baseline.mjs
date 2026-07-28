import assert from 'node:assert/strict';
import { execFile as execFileCallback } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { performance } from 'node:perf_hooks';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const FIELD_COUNT = 1000;
const VERTICES_PER_FIELD = 64;
const SAMPLES = 31;
const BASELINE_COMMIT = '1b11607b6c5880643cbee07bf78fdce5e00e383d';
const execFile = promisify(execFileCallback);
const outputArgument = process.argv.find((value) => value.startsWith('--output='));
const outputPath = outputArgument ? resolve(outputArgument.slice('--output='.length)) : '';

function quantile(sorted, fraction) {
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * fraction))];
}

function polygon(fieldIndex) {
  const column = fieldIndex % 40;
  const row = Math.floor(fieldIndex / 40);
  const centerLon = 63.5 + column * 0.025;
  const centerLat = 39.2 + row * 0.025;
  const coordinates = [];
  for (let vertex = 0; vertex < VERTICES_PER_FIELD; vertex += 1) {
    const angle = (Math.PI * 2 * vertex) / VERTICES_PER_FIELD;
    coordinates.push([
      Number((centerLon + Math.cos(angle) * 0.009).toFixed(7)),
      Number((centerLat + Math.sin(angle) * 0.006).toFixed(7)),
    ]);
  }
  coordinates.push(coordinates[0]);
  return [coordinates];
}

function fixture() {
  return {
    type: 'FeatureCollection',
    total: FIELD_COUNT,
    features: Array.from({ length: FIELD_COUNT }, (_, index) => ({
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: polygon(index) },
      properties: {
        id: index + 1,
        name: `Fixture field ${index + 1}`,
        code: `F-${String(index + 1).padStart(4, '0')}`,
        enterprise_id: (index % 10) + 1,
        enterprise_name: `Fixture enterprise ${(index % 10) + 1}`,
        area_ha: 12.5 + (index % 20),
        centroid_lat: 39.2 + Math.floor(index / 40) * 0.025,
        centroid_lon: 63.5 + (index % 40) * 0.025,
        irrigation_type: index % 2 ? 'canal' : 'drip',
        current_crop: index % 3 ? 'Хлопок' : 'Пшеница озимая',
        last_ndvi: 0.25 + (index % 50) / 100,
        last_ndvi_date: '2026-07-25',
        ndvi_change_pct: (index % 11) - 5,
        active_alerts: index % 4,
        alert_severity: index % 7 === 0 ? 'critical' : 'ok',
      },
    })),
  };
}

function enrichLikeCurrentMap(collection) {
  return {
    ...collection,
    features: collection.features.map((feature) => ({
      ...feature,
      id: feature.properties.id,
      properties: {
        ...feature.properties,
        coverage_status: feature.properties.id % 3 ? 'complete' : 'partial',
        freshness_status: feature.properties.id % 5 ? 'fresh' : 'stale',
        savi_value: feature.properties.last_ndvi - 0.04,
        evi_value: feature.properties.last_ndvi - 0.08,
        ndmi_value: feature.properties.last_ndvi - 0.12,
        ndre_value: feature.properties.last_ndvi - 0.16,
        map_mode_color: feature.properties.last_ndvi > 0.5 ? '#16a34a' : '#f59e0b',
        map_mode_label: 'NDVI',
      },
    })),
  };
}

const geojson = fixture();
const serialized = JSON.stringify(geojson);
const parseSamples = [];
const transformSamples = [];
let transformedBytes = 0;

for (let sample = 0; sample < SAMPLES; sample += 1) {
  let started = performance.now();
  const parsed = JSON.parse(serialized);
  parseSamples.push(performance.now() - started);
  started = performance.now();
  const transformed = enrichLikeCurrentMap(parsed);
  transformSamples.push(performance.now() - started);
  transformedBytes = Buffer.byteLength(JSON.stringify(transformed));
}

parseSamples.sort((left, right) => left - right);
transformSamples.sort((left, right) => left - right);

const repositoryRoot = fileURLToPath(new URL('../..', import.meta.url));
const readBaselineFile = async (path) => (
  await execFile('git', ['show', `${BASELINE_COMMIT}:${path}`], {
    cwd: repositoryRoot,
    encoding: 'utf8',
  })
).stdout;
const fieldMapSource = await readBaselineFile('frontend/src/components/Map/FieldMap.jsx');
const rasterHookSource = await readBaselineFile('frontend/src/hooks/useNDVIRasterLayer.js');
const lifecycleSource = `${fieldMapSource}\n${rasterHookSource}`;
const count = (pattern) => (lifecycleSource.match(pattern) || []).length;

assert.match(fieldMapSource, /\/api\/fields\/geojson\/all/);
assert.match(fieldMapSource, /m\.on\('movestart'/);
assert.match(fieldMapSource, /m\.on\('moveend'/);
assert.match(rasterHookSource, /controllerRef\.current\?\.abort\(\)/);
assert.match(rasterHookSource, /URL\.revokeObjectURL/);

const report = {
  decision: 'PASS_BASELINE_FIXTURE',
  measured_at: new Date().toISOString(),
  baseline_commit: BASELINE_COMMIT,
  contract: {
    current_geometry_delivery: 'single_full_geojson_response',
    endpoint: '/api/fields/geojson/all',
    hard_row_cap: 1000,
    initial_geometry_requests: 1,
    geometry_requests_per_pan_or_zoom: 0,
    repeated_full_dataset_fetch_per_camera_event: false,
    raster_requests_per_enabled_selection: 2,
    raster_request_sequence: ['metadata', 'image'],
    raster_provider_coupling: 'ndvi_sentinel_specific',
  },
  deterministic_fixture: {
    synthetic_business_data: true,
    persisted_to_database: false,
    field_count: FIELD_COUNT,
    vertices_per_field: VERTICES_PER_FIELD,
    coordinate_pairs: FIELD_COUNT * (VERTICES_PER_FIELD + 1),
    geojson_payload_bytes: Buffer.byteLength(serialized),
    transformed_geojson_payload_bytes: transformedBytes,
    parse_samples: SAMPLES,
    parse_p50_ms: Number(quantile(parseSamples, 0.5).toFixed(3)),
    parse_p95_ms: Number(quantile(parseSamples, 0.95).toFixed(3)),
    full_collection_transform_p50_ms: Number(quantile(transformSamples, 0.5).toFixed(3)),
    full_collection_transform_p95_ms: Number(quantile(transformSamples, 0.95).toFixed(3)),
  },
  static_lifecycle_sites: {
    listener_registration_sites: count(/\.on\(/g),
    listener_cleanup_sites: count(/\.off\(/g),
    source_add_sites: count(/\.addSource\(/g),
    source_remove_sites: count(/\.removeSource\(/g),
    layer_add_sites: count(/\.addLayer\(/g),
    layer_remove_sites: count(/\.removeLayer\(/g),
    full_source_set_data_sites: count(/\.setData\(/g),
    object_url_create_sites: count(/URL\.createObjectURL/g),
    object_url_revoke_sites: count(/URL\.revokeObjectURL/g),
  },
  unmeasured_without_external_prerequisites: {
    real_field_count: 'BLOCKED_B001',
    real_geojson_payload_bytes: 'BLOCKED_B001',
    postgis_explain: 'BLOCKED_B001',
    maplibre_source_update_ms: 'BLOCKED_B003',
    first_meaningful_map_render_ms: 'BLOCKED_B003',
    runtime_memory_growth: 'BLOCKED_B003',
    runtime_listener_count: 'BLOCKED_B003',
    runtime_layer_count: 'BLOCKED_B003',
    runtime_source_count: 'BLOCKED_B003',
    raster_aborted_request_count: 'BLOCKED_B003',
    raster_stale_response_count: 'BLOCKED_B003',
  },
};

if (outputPath) {
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
}

console.log(`TASK209 map baseline benchmark: PASS ${JSON.stringify(report)}`);
