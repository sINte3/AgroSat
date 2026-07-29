import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) => readFile(path.join(root, name), 'utf8');
const [api, panel, detail] = await Promise.all([
  read('src/api/variableRateRecommendations.js'),
  read('src/components/Field/VariableRateRecommendationPanel.jsx'),
  read('src/components/Field/FieldDetail.jsx'),
]);

assert.match(api, /Idempotency-Key/);
assert.match(api, /variable-rate-recommendations\/\$\{id\}\/\$\{decision\}/);
assert.match(api, /export\.geojson/);
assert.match(api, /signal/);

for (const phrase of [
  'fertilizer',
  'seed',
  'pesticide',
  'irrigation',
  'minimum_rate',
  'maximum_rate',
  'zone_rates',
  'equipment_capability',
  'safety_acknowledged',
  'expected_version',
  'confirm: true',
  'canCreate',
  'canApprove',
]) assert.match(panel, new RegExp(phrase));
assert.match(panel, /не является автономным предписанием/);
assert.match(panel, /нормы введены человеком/);
assert.match(panel, /new AbortController\(\)/);
assert.match(panel, /generationRef/);
assert.match(panel, /URL\.createObjectURL/);
assert.match(panel, /URL\.revokeObjectURL/);
assert.match(panel, /min-h-11/);
assert.match(panel, /aria-live/);
assert.doesNotMatch(panel, /setInterval|localStorage|Authorization/);

assert.match(detail, /VariableRateRecommendationPanel/);
assert.match(detail, /activeTab === 'yield'/);

console.log('TASK209_VARIABLE_RATE_FRONTEND_CONTRACT=PASS');
