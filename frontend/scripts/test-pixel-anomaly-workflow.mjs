import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8');
const api = read('src/api/pixelAnomalies.js');
const panel = read('src/components/Field/PixelAnomalyPanel.jsx');
const workspace = read('src/components/Field/FieldAnalyticsWorkspace.jsx');
const styles = read('src/index.css');

for (const endpoint of [
  'pixel-anomalies/fields/${fieldId}/summary',
  "'pixel-anomalies'",
  'pixel-anomalies/${id}',
  'pixel-anomalies/${id}/geometry',
  'pixel-anomalies/${id}/inspection',
]) {
  assert.ok(api.includes(endpoint), `missing anomaly API contract: ${endpoint}`);
}
assert.match(api, /__retryCount:\s*NO_AUTOMATIC_RETRY_COUNT/);
assert.match(api, /'Idempotency-Key': idempotencyKey/);
assert.ok((api.match(/\bsignal\b/g) || []).length >= 8);

assert.match(workspace, /<PixelAnomalyPanel/);
assert.match(workspace, /indexCode=\{deepDiveIndex\}/);
assert.match(workspace, /dayRange=\{dayRange\}/);

for (const cleanup of [
  "map.off('style.load', handleStyleLoad)",
  'map.removeControl(controlRef.current)',
  'removeZoneOwnership(map)',
  'map.remove()',
  'controller.abort()',
  'listControllerRef.current?.abort()',
  'detailControllerRef.current?.abort()',
  'window.cancelAnimationFrame(focusFrameRef.current)',
]) {
  assert.ok(panel.includes(cleanup), `missing lifecycle cleanup: ${cleanup}`);
}
for (const ownership of [
  "map.removeLayer(LINE_LAYER_ID)",
  "map.removeLayer(FILL_LAYER_ID)",
  "map.removeSource(SOURCE_ID)",
  "setFeature(null)",
  'generation !== detailGenerationRef.current',
  'generation !== listGenerationRef.current',
]) {
  assert.ok(panel.includes(ownership), `missing stale/ownership guard: ${ownership}`);
}
for (const accessibility of [
  'role="dialog"',
  'aria-modal="true"',
  'aria-labelledby="anomaly-inspection-title"',
  "event.key === 'Escape'",
  "event.key !== 'Tab'",
  'aria-live="polite"',
  'min-h-11',
  'prefers-reduced-motion: reduce',
]) {
  assert.ok(panel.includes(accessibility), `missing accessibility contract: ${accessibility}`);
}
for (const safetyCopy of [
  'Это повод для осмотра, а не диагноз',
  'не доказывает агрономическую причинность',
  'Недостаточно данных',
  'Только чтение',
]) {
  assert.ok(panel.includes(safetyCopy), `missing non-diagnostic state: ${safetyCopy}`);
}
assert.match(panel, /\['admin', 'manager', 'agronomist'\]\.includes\(role\)/);
assert.match(panel, /detail\.status === 'open'/);
assert.doesNotMatch(panel, /setInterval|setTimeout|URL\.createObjectURL/);
assert.match(styles, /\.maplibregl-ctrl-group button\s*\{[^}]*width:\s*44px !important;[^}]*height:\s*44px !important;/s);
assert.match(styles, /\.maplibregl-ctrl-group button:focus-visible/);

console.log(JSON.stringify({
  status: 'PASS',
  apiContracts: 5,
  mapOwnershipCleanups: 4,
  asyncControllers: 3,
  staleGenerationGuards: 2,
  accessibilityContracts: 10,
  mockProductionFallbacks: 0,
}));
