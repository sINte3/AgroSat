import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) => readFile(path.join(root, name), 'utf8');

const [api, panel, wrapper, fieldDetail] = await Promise.all([
  read('src/api/irrigationContext.js'),
  read('src/components/Field/FieldIrrigationContextPanel.jsx'),
  read('src/components/Field/WeatherWidget.jsx'),
  read('src/components/Field/FieldDetail.jsx'),
]);

assert.match(api, /irrigation-context\/fields\/\$\{fieldId\}/);
assert.match(api, /Idempotency-Key/);
assert.match(api, /signal/);
assert.doesNotMatch(api, /open-meteo\.com|token|password|Authorization/i);

assert.match(panel, /new AbortController\(\)/);
assert.match(panel, /controller\.abort\(\)/);
assert.match(panel, /generationRef/);
assert.match(panel, /source: 'irrigation_context'/);
assert.match(panel, /source_attention_score: null/);
assert.match(panel, /source_reason_codes: \[reason\]/);
assert.match(panel, /evidence_source: 'human_reported'/);
assert.match(panel, /water_stress_suspicion/);
assert.match(panel, /irrigation_interruption/);
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
