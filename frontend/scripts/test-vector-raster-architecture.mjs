import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const read = (path) => readFile(new URL(path, import.meta.url), 'utf8');
const [
  fieldMap,
  fieldTiles,
  rasterApi,
  rasterHook,
  rasterControl,
  client,
  fieldsPage,
  fieldListPanel,
  maplibreRuntime,
  productivityZonePanel,
  pixelAnomalyPanel,
] = await Promise.all([
  read('../src/components/Map/FieldMap.jsx'),
  read('../src/api/fieldTiles.js'),
  read('../src/api/raster.js'),
  read('../src/hooks/useNDVIRasterLayer.js'),
  read('../src/components/Map/NDVIRasterControl.jsx'),
  read('../src/api/client.js'),
  read('../src/pages/FieldsPage.jsx'),
  read('../src/components/Map/FieldListPanel.jsx'),
  read('../src/maplibreRuntime.js'),
  read('../src/components/Field/ProductivityZonePanel.jsx'),
  read('../src/components/Field/PixelAnomalyPanel.jsx'),
]);

assert.doesNotMatch(fieldMap, /fields\/geojson\/all/);
assert.doesNotMatch(fieldMap, /\.setData\(/);
assert.match(fieldMap, /type:\s*'vector'/);
assert.match(fieldMap, /'source-layer':\s*FIELD_SOURCE_LAYER/g);
assert.match(fieldMap, /sourceLayer:\s*FIELD_SOURCE_LAYER/);
assert.match(fieldMap, /cancelPendingTileRequestsWhileZooming:\s*true/);
assert.match(fieldMap, /removeLayer\(layerId\)/);
assert.match(fieldMap, /removeSource\(FIELD_SOURCE_ID\)/);
assert.match(fieldMap, /mapRef\.current\.remove\(\)/);
assert.match(fieldMap, /map\.on\('style\.load', handlersRef\.current\.onLoad\)/);
assert.match(fieldMap, /map\.off\('style\.load', handlersRef\.current\.onLoad\)/);
assert.match(fieldMap, /removeFeatureState/);
assert.match(fieldMap, /setFeatureState/);
assert.match(fieldMap, /getSameOriginApiAuthorizationHeaders\(url\)/);
assert.match(fieldMap, /max-sm:flex-nowrap/);
assert.match(fieldMap, /max-sm:overflow-x-auto/);
assert.match(fieldTiles, /field-tiles\/metadata/);
assert.match(fieldTiles, /task209_field_mvt_v1/);
assert.match(fieldTiles, /startsWith\('\/api\/field-tiles\/'\)/);
assert.match(fieldTiles, /target\.origin !== window\.location\.origin/);
assert.match(fieldTiles, /window\.location\.origin\}\$\{metadata\.tile_template/);

assert.match(client, /target\.origin !== window\.location\.origin/);
assert.match(client, /target\.pathname\.startsWith\('\/api\/'\)/);
assert.match(client, /token \? \{ Authorization:/);
assert.match(fieldsPage, /key=\{`\$\{mapReloadKey\}:\$\{enterpriseId \|\| 'all'\}`\}/);
assert.match(fieldsPage, /controller\.abort\(\)/);
assert.match(fieldsPage, /left-3 sm:left-\[408px\]/);
assert.match(fieldListPanel, /matchMedia\('\(max-width: 639px\)'\)/);
assert.match(fieldListPanel, /removeEventListener\('change', collapseForMobile\)/);
assert.match(fieldListPanel, /aria-controls="field-list-panel"/);

assert.match(maplibreRuntime, /maplibre-gl\/dist\/maplibre-gl-csp/);
assert.match(maplibreRuntime, /maplibre-gl-csp-worker\?url/);
assert.match(maplibreRuntime, /setWorkerUrl\(mapLibreWorkerUrl\)/);
for (const owner of [fieldMap, productivityZonePanel, pixelAnomalyPanel]) {
  assert.match(owner, /maplibreRuntime/);
  assert.doesNotMatch(owner, /from ['"]maplibre-gl['"]/);
}

assert.match(rasterApi, /raster\/fields\/\$\{fieldId\}\/metadata/);
assert.match(rasterApi, /index_code:\s*indexCode/);
assert.match(rasterHook, /getRasterMetadata/);
assert.match(rasterHook, /getRasterImage/);
assert.match(rasterHook, /controllerRef\.current\?\.abort\(\)/);
assert.match(rasterHook, /generationRef\.current/);
assert.match(rasterHook, /URL\.revokeObjectURL/);
assert.match(rasterHook, /removeLayer\(NDVI_RASTER_LAYER_ID\)/);
assert.match(rasterHook, /removeSource\(NDVI_RASTER_SOURCE_ID\)/);
assert.doesNotMatch(rasterControl, /Sentinel-2/);
assert.match(rasterControl, /max-sm:bottom-3/);
assert.match(rasterControl, /max-sm:max-h-\[46vh\]/);
assert.match(rasterControl, /top-\[140px\]/);

console.log('TASK209 vector/raster frontend static contract: PASS');
