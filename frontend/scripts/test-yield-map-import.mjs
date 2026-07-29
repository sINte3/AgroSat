import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) => readFile(path.join(root, name), 'utf8');

const [api, panel, detail] = await Promise.all([
  read('src/api/yieldMapImports.js'),
  read('src/components/Field/YieldMapImportPanel.jsx'),
  read('src/components/Field/FieldDetail.jsx'),
]);

assert.match(api, /yield-map-imports\/preview/);
assert.match(api, /Idempotency-Key/);
assert.match(api, /limit: 50/);
assert.match(api, /signal/);

assert.match(panel, /yield_point_csv_v1/);
assert.match(panel, /MAX_ROWS = 5000/);
assert.match(panel, /MAX_FILE_BYTES = 5 \* 1024 \* 1024/);
assert.match(panel, /crypto\.subtle\.digest\('SHA-256'/);
assert.match(panel, /parseYieldPointCsv/);
assert.match(panel, /new AbortController\(\)/);
assert.match(panel, /actionControllerRef\.current\?\.abort\(\)/);
assert.match(panel, /historyControllerRef\.current\?\.abort\(\)/);
assert.match(panel, /actionGenerationRef/);
assert.match(panel, /historyGenerationRef/);
assert.match(panel, /preview_fingerprint/);
assert.match(panel, /confirm: true/);
assert.match(panel, /preview\.rejected_rows > 0/);
assert.match(panel, /Только просмотр/);
assert.match(panel, /role === 'viewer'/);
assert.match(panel, /Спутниковые индексы не используются/);
assert.match(panel, /outside_field/);
assert.match(panel, /yield_statistical_outlier/);
assert.match(panel, /min-h-11/);
assert.match(panel, /aria-live/);
assert.doesNotMatch(panel, /FileReader|createObjectURL|localStorage|sessionStorage|Authorization/);

assert.match(detail, /YieldMapImportPanel/);
assert.match(detail, /key: 'yield'/);
assert.match(detail, /activeTab === 'yield'/);

console.log('TASK209_YIELD_MAP_IMPORT_FRONTEND_CONTRACT=PASS');
