import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';


const modal = await readFile(new URL('../src/components/Enterprise/NDVIHistoryModal.jsx', import.meta.url), 'utf8');
const apiClient = await readFile(new URL('../src/api/client.js', import.meta.url), 'utf8');

assert.match(modal, /getNDVIHistory\(field\.id, 30, \{ signal: controller\.signal \}\)/);
assert.match(modal, /Array\.isArray\(data\?\.records\) \? data\.records : \[\]/);
assert.match(modal, /new AbortController\(\)/);
assert.match(modal, /return \(\) => controller\.abort\(\)/);
assert.match(modal, /controller\.signal\.aborted \|\| err\?\.code === 'ERR_CANCELED'/);
assert.doesNotMatch(modal, /setNdviHistory\(r\.data \|\| \[\]\)/);
assert.match(apiClient, /getNDVIHistory\(fieldId, days = 90, options = \{\}\)/);
assert.match(apiClient, /ndvi\/\$\{fieldId\}\/history[\s\S]*signal: options\.signal/);

console.log('PROGRAM R1 enterprise NDVI history response and lifecycle contract: PASS');
