import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) => readFile(path.join(root, name), 'utf8');

const [api, panel, detail] = await Promise.all([
  read('src/api/productivityZones.js'),
  read('src/components/Field/ProductivityZonePanel.jsx'),
  read('src/components/Field/FieldDetail.jsx'),
]);

assert.match(api, /productivity-zones\/fields\/\$\{fieldId\}/);
assert.match(api, /productivity-zones\/runs\/\$\{runId\}\/zones/);
assert.match(api, /limit: 10/);
assert.match(api, /signal/);

assert.match(panel, /new AbortController\(\)/);
assert.match(panel, /controllerRef\.current\?\.abort\(\)/);
assert.match(panel, /generationRef/);
assert.match(panel, /insufficient/);
assert.match(panel, /reason_codes/);
assert.match(panel, /selected_seasons/);
assert.match(panel, /area_delta_ha/);
assert.match(panel, /algorithm_version/);
assert.match(panel, /не агрономическое предписание/);
assert.match(panel, /не расчёт только по спутниковым индексам/);
assert.match(panel, /new maplibregl\.Map/);
assert.match(panel, /map\.off\('style\.load'/);
assert.match(panel, /removeMapOwnership\(map\)/);
assert.match(panel, /map\.removeControl/);
assert.match(panel, /map\.remove\(\)/);
assert.match(panel, /map\.removeLayer/);
assert.match(panel, /map\.removeSource/);
assert.match(panel, /prefers-reduced-motion/);
assert.match(panel, /min-h-11/);
assert.match(panel, /role="status"/);
assert.match(panel, /role="alert"/);
assert.doesNotMatch(panel, /setInterval|createObjectURL|localStorage|Authorization/);

assert.match(detail, /ProductivityZonePanel/);
assert.match(detail, /activeTab === 'yield'/);
assert.match(detail, /pl-16 pr-4/);

console.log('TASK209_PRODUCTIVITY_ZONE_FRONTEND_CONTRACT=PASS');
