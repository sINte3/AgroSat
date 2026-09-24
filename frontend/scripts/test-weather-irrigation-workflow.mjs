import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) => readFile(path.join(root, name), 'utf8');

const [api, panel, wrapper, fieldDetail, requests] = await Promise.all([
  read('src/api/irrigationContext.js'),
  read('src/components/Field/FieldIrrigationContextPanel.jsx'),
  read('src/components/Field/WeatherWidget.jsx'),
  read('src/components/Field/FieldDetail.jsx'),
  read('src/utils/inspectionRequests.js'),
]);

assert.match(api, /irrigation-context\/fields\/\$\{fieldId\}/);
assert.match(api, /Idempotency-Key/);
assert.match(api, /signal/);
assert.doesNotMatch(api, /open-meteo\.com|token|password|Authorization/i);

assert.match(panel, /new AbortController\(\)/);
assert.match(panel, /controller\.abort\(\)/);
assert.match(panel, /generationRef/);
// TASK_226: the irrigation context creates a canonical manual inspection
// (POST /api/anomaly-inspections); the retired TASK_209 create is never used.
assert.match(panel, /createAnomalyInspection\(payload, keyRef\.current, controller\.signal\)/);
assert.match(panel, /source_kind: 'manual'/);
assert.match(panel, /reason: irrigationInspectionReason\(reason\)/);
assert.match(panel, /due_at: parsed\.state === 'valid' \? parsed\.iso : null/);
assert.match(panel, /type="datetime-local" required/);
assert.match(panel, /Срок осмотра \(Ташкент\)/);
assert.match(panel, /canCreateInspections\(role\)/);
assert.doesNotMatch(panel, /source: 'irrigation_context'|source_reason_codes|createFieldInspection|field-inspections/);
assert.match(panel, /evidence_source: 'human_reported'/);
assert.match(requests, /water_stress_suspicion/);
assert.match(requests, /irrigation_interruption/);
assert.match(requests, /Контекст орошения: /);
assert.match(panel, /не подтверждают агрономическую\s+причину/i);
assert.match(panel, /Погодный контекст недоступен/);
assert.match(panel, /Подтверждённые события/);
assert.match(panel, /Только просмотр/);
assert.match(panel, /role === 'viewer'/);
assert.match(panel, /min-h-11/);
assert.match(panel, /aria-live/);
assert.match(panel, /provider_observed_at/);
assert.match(panel, /Asia\/Tashkent/);
assert.match(panel, /\$\{occurredAt\}:00\+05:00/);
assert.doesNotMatch(panel, /увеличить норму полива|подтверждённ(?:ая|ый) причина|confirmed_irrigation_failure/i);
assert.doesNotMatch(panel, /localStorage|sessionStorage|Authorization|token/i);

assert.match(wrapper, /FieldIrrigationContextPanel/);
assert.match(fieldDetail, /WeatherWidget/);
assert.match(fieldDetail, /activeTab === 'weather'/);

console.log('TASK209_WEATHER_IRRIGATION_FRONTEND_CONTRACT=PASS');
