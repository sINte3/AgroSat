import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import {
  createAnomalyWorkflowDraft,
  offlineScope,
} from '../src/offline/offlineScoutingStore.js';

const read = (name) => readFile(new URL(name, import.meta.url), 'utf8');
const [api, dialog, detail, queue, pixel, alerts, app, offline, agronomyApi] = await Promise.all([
  read('../src/api/anomalyInspections.js'),
  read('../src/components/Inspections/InspectionSourceDialog.jsx'),
  read('../src/components/Inspections/AnomalyInspectionDetail.jsx'),
  read('../src/pages/AnomalyInspectionsPage.jsx'),
  read('../src/components/Map/NDVIRasterControl.jsx'),
  read('../src/components/Dashboard/AlertsList.jsx'),
  read('../src/App.jsx'),
  read('../src/offline/offlineScoutingStore.js'),
  read('../src/api/closedLoopAgronomy.js'),
]);

assert.equal(offlineScope({ enterprise_id: 7, id: 11 }), '7:11');
assert.equal(offlineScope({ enterprise_id: 7, id: null }), null);
assert.equal(offlineScope({ enterprise_id: 0, id: 11 }), null);
const draft = createAnomalyWorkflowDraft({
  scope: '7:11', inspectionId: 41, baseVersion: 3,
  finding: { cause: 'water_stress', observations: 'bounded draft', affected_value: '1.2' },
});
assert.equal(draft.key, '7:11:inspection:41');
assert.equal(draft.scope, '7:11');
assert.equal(draft.baseVersion, 3);
assert.equal(draft.status, 'local_draft');
assert.equal(draft.schemaVersion, 2);
assert.throws(() => createAnomalyWorkflowDraft({
  scope: '7:11', inspectionId: 41, baseVersion: 3,
  finding: { access_token: 'forbidden' },
}), /forbidden credential field/);
assert.throws(() => createAnomalyWorkflowDraft({
  scope: '7:11', inspectionId: 41, baseVersion: 3,
  finding: { photo: new Blob(['private']) },
}), /binary content/);

// Canonical lifecycle routes (TASK_225): the TASK_217 action writes are retired (410).
for (const route of [
  'anomaly-inspections/queue', 'anomaly-inspections/assignees',
  'anomaly-inspections/${id}/finding', 'anomaly-inspections/${id}/photos',
  'anomaly-inspections/${id}/cancel',
]) assert.match(api, new RegExp(route.replaceAll('$', '\\$')));
for (const retired of ['anomaly-inspections/${id}/actions', 'anomaly-inspections/actions/']) {
  assert.doesNotMatch(api, new RegExp(retired.replaceAll('$', '\\$')));
}
assert.doesNotMatch(api, /createInspectionAction|transitionInspectionAction|verifyInspectionAction/);
assert.match(api, /responseType:\s*'blob'/);
assert.match(api, /Content-Type': undefined/);
assert.match(api, /Idempotency-Key/);
assert.match(agronomyApi, /'agronomy-plans'/);
assert.match(dialog, /role="dialog"/);
assert.match(dialog, /aria-modal="true"/);
assert.match(dialog, /Причина осмотра/);
assert.match(dialog, /Агроном/);
assert.match(dialog, /Срок/);
assert.match(dialog, /parseTashkentDateTimeInput/);
assert.match(dialog, /min-h-11/);
assert.match(dialog, /window\.addEventListener\('keydown'/);
assert.match(dialog, /window\.removeEventListener\('keydown'/);
assert.match(detail, /Карта источника аномалии и границ поля/);
assert.match(detail, /map\.off\('load'/);
assert.match(detail, /map\.remove\(\)/);
assert.match(detail, /observer\.disconnect\(\)/);
assert.match(detail, /URL\.revokeObjectURL/);
assert.match(detail, /previewRequestRef\.current\?\.abort/);
assert.match(detail, /Сохранено локально/);
assert.match(detail, /Ожидает синхронизации/);
assert.match(detail, /Синхронизировано/);
assert.match(detail, /Конфликт/);
assert.match(detail, /createAgronomyDraft\(/);
assert.match(detail, /listAgronomyPlans\(\{ inspection_id: detail\.id/);
assert.match(detail, /Исторические корректирующие действия \(только чтение\)/);
assert.match(detail, /isLegacyInspection\(detail\)/);
assert.match(detail, /cancelAnomalyInspection\(/);
for (const cause of [
  'water_stress', 'irrigation_failure', 'pest', 'disease', 'nutrient_deficiency',
  'weed_pressure', 'mechanical_damage', 'soil_salinity', 'weather_damage',
  'false_positive', 'other',
]) assert.match(detail, new RegExp(cause));
assert.match(queue, /useSearchParams/);
assert.match(queue, /inspection-queue/);
assert.match(queue, /Просрочено/);
assert.match(queue, /Ожидают проверки/);
assert.match(queue, /Активные планы мер/);
assert.match(queue, /Ждут спутниковой проверки/);
assert.match(queue, /controller\.abort\(\)/);
assert.match(queue, /listOfflineDrafts/);
assert.match(pixel, /Создать осмотр/);
assert.match(pixel, /sample\.value\?\.status === 'value'/);
assert.match(alerts, /Создать осмотр/);
assert.match(app, /'\/inspections': 'field-inspections'/);
assert.match(app, /\/inspections\/\$\{validInspectionId\}/);
assert.match(offline, /purgeOfflineScope/);
assert.doesNotMatch(offline, /deleteDatabase\(/);
assert.match(offline, /MAX_RECORD_BYTES = 512 \* 1024/);

console.log('TASK 217 anomaly inspection frontend contract (TASK_225/226 canonical lifecycle): PASS (82 assertions)');
