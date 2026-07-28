import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../src/', import.meta.url);
const read = (path) => readFile(new URL(path, root), 'utf8');

const [api, component, reportsPage, app, legacyClient] = await Promise.all([
  read('api/executiveAccountability.js'),
  read('components/Reports/ExecutiveAccountability.jsx'),
  read('pages/ReportsPage.jsx'),
  read('App.jsx'),
  read('api/client.js'),
]);

for (const contract of [
  'getExecutiveOverview',
  'getExecutiveAccountability',
  'downloadExecutiveWorkbook',
]) {
  assert.match(api, new RegExp(`export async function ${contract}\\b`), `${contract} API is missing`);
}
assert.match(api, /responseType: 'blob'/);
assert.match(api, /date_from: filters\.dateFrom/);
assert.match(api, /enterprise_id: filters\.enterpriseId/);

assert.match(component, /role === 'admin' \|\| role === 'manager'/);
assert.match(component, /if \(!management\) return null/);
assert.match(component, /new AbortController\(\)/g);
assert.match(component, /generation !== overviewGenerationRef\.current/);
assert.match(component, /generation !== queueGenerationRef\.current/);
assert.match(component, /overviewControllerRef\.current\?\.abort\(\)/);
assert.match(component, /queueControllerRef\.current\?\.abort\(\)/);
assert.match(component, /exportControllerRef\.current\?\.abort\(\)/);
assert.match(component, /if \(objectUrl\) URL\.revokeObjectURL\(objectUrl\)/);
assert.match(component, /openQueue\('open_actions', item\.owner_id\)/);
assert.match(component, /aria-live="polite"/);
assert.match(component, /role="alert"/);
assert.match(component, /state === 'loading'/);
assert.match(component, /state === 'error'/);
assert.match(component, /state === 'ready'/);
assert.match(component, /!safeArray\(data\.enterprises\)\.length/);
assert.doesNotMatch(component, /\.reduce\(/, 'enterprise aggregates must come from the backend');

assert.match(reportsPage, /<ExecutiveAccountability user=\{user\} enterprises=\{enterprises\} onNavigate=\{onNavigate\} \/>/);
assert.match(reportsPage, /reportControllerRef\.current\?\.abort\(\)/);
assert.match(reportsPage, /satelliteControllerRef\.current\?\.abort\(\)/);
assert.match(reportsPage, /pdfControllerRef\.current\?\.abort\(\)/);
assert.match(reportsPage, /generation !== reportGenerationRef\.current/);
assert.match(reportsPage, /generation !== satelliteGenerationRef\.current/);
assert.match(reportsPage, /if \(objectUrl\) window\.URL\.revokeObjectURL\(objectUrl\)/);
assert.match(app, /<ReportsPage onNavigate=\{handleNavigate\} \/>/);
assert.match(legacyClient, /getManagementReportSummary\(signal\)/);
assert.match(legacyClient, /getManagementSatelliteIndicesSummary\(params = \{\}, signal\)/);

console.log('TASK209 executive accountability frontend contract: PASS');
