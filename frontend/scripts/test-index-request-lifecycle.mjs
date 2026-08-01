import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';


const fieldDetail = await readFile(new URL('../src/components/Field/FieldDetail.jsx', import.meta.url), 'utf8');
const apiClient = await readFile(new URL('../src/api/client.js', import.meta.url), 'utf8');

assert.match(fieldDetail, /indexRequestController\.current\?\.abort\(\)/);
assert.match(fieldDetail, /new AbortController\(\)/);
assert.match(fieldDetail, /indexRequestGeneration\.current/);
assert.match(fieldDetail, /generation !== indexRequestGeneration\.current/);
assert.match(fieldDetail, /controller\.signal\.aborted/);
assert.match(fieldDetail, /err\?\.code === 'ERR_CANCELED'/);
assert.match(fieldDetail, /getSatelliteIndexLatest\(fid, code, \{ signal: controller\.signal \}\)/);
assert.match(fieldDetail, /getSatelliteIndexHistory\(fid, code, \{ days: 30, signal: controller\.signal \}\)/);
assert.match(apiClient, /satellite-indices\/\$\{fieldId\}\/latest[\s\S]*signal: options\.signal/);
assert.match(apiClient, /satellite-indices\/\$\{fieldId\}\/history[\s\S]*signal: options\.signal/);

console.log('PROGRAM R1 field index request lifecycle contract: PASS');
