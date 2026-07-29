import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) => readFile(path.join(root, name), 'utf8');

const [api, panel, fieldDetail] = await Promise.all([
  read('src/api/telematics.js'),
  read('src/components/Field/FieldTelematicsPanel.jsx'),
  read('src/components/Field/FieldDetail.jsx'),
]);

assert.ok(api.includes('telematics/fields/${fieldId}'));
assert.match(api, /signal/);
assert.doesNotMatch(api, /wialon[^\n]*(token|password|secret)/i);

assert.ok(panel.includes('new AbortController()'));
assert.ok(panel.includes('controller.abort()'));
assert.match(panel, /generationRef/);
assert.match(panel, /available.*stale.*unsupported.*unavailable/s);
assert.match(panel, /только чтение/i);
assert.match(panel, /не подтверждает выполненную операцию/i);
assert.match(panel, /mapping_provenance/);
assert.match(panel, /requested_range/);
assert.match(panel, /field_intersection/);
assert.match(panel, /geofence_intersection/);
assert.match(panel, /sensors/);
assert.doesNotMatch(panel, /localStorage|sessionStorage|Authorization|token/i);
assert.doesNotMatch(panel, /\b(?:POST|PUT|PATCH|DELETE)\b|\.post\(|\.put\(|\.patch\(|\.delete\(/i);

assert.match(fieldDetail, /FieldTelematicsPanel/);
assert.match(fieldDetail, /key: 'telematics'/);
assert.match(fieldDetail, /activeTab === 'telematics'/);

console.log('TASK209_WIALON_FRONTEND_CONTRACT=PASS');
