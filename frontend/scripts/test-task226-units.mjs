// TASK_226 unit contracts: Tashkent date input safety (C12), canonical
// vocabulary parity with the TASK_225 backend, canonical inspection requests,
// dashboard server totals.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import './lib/node-browser-env.mjs';
import {
  parseTashkentDateTimeInput,
  tashkentDateTimeInputAfterDays,
  toTashkentDateInput,
  toTashkentDateTimeInput,
} from '../src/utils/tashkentTime.js';
import { validateWorkSchedule, workDraftInputs } from '../src/utils/workSchedule.js';
import {
  attentionInspectionReason,
  buildManualInspectionRequest,
  inspectionDueError,
  irrigationInspectionReason,
} from '../src/utils/inspectionRequests.js';
import {
  canonicalInspectionPriority,
  canCancelInspection,
  caseStatusLabel,
  caseStatusTone,
  FILTERABLE_OPERATIONAL_STATUSES,
  OPERATIONAL_STATUS_LABELS,
  planStatusLabel,
  planStatusTone,
  REMEDIATION_STATUS,
  TONE_CLASSES,
  VERIFICATION_STATUS,
} from '../src/config/canonicalLifecycle.js';
import { dashboardAlertTotals } from '../src/utils/dashboardTotals.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const repository = path.resolve(here, '..', '..');
let assertions = 0;
const equal = (actual, expected, message) => { assert.deepEqual(actual, expected, message); assertions += 1; };
const check = (condition, message) => { assert.ok(condition, message); assertions += 1; };

// ─── Date input safety: partial input never throws ─────────────────────────
const partialInputs = [
  '', '   ', '2', '2026', '2026-', '2026-09', '2026-09-', '2026-09-2', '2026-09-24', '2026-09-24T',
  '2026-09-24T1', '2026-09-24T17', '2026-09-24T17:', '2026-09-24T17:0', '2026-02-30T10:00',
  '2026-13-01T10:00', '2026-09-31T10:00', '2026-09-24T24:00', '2026-09-24T12:60', 'abc', '1999-01-01T00:00',
  null, undefined, 42, {}, [], '2026-09-24T17:00+05:00', '2026-09-24 17:00',
];
for (const value of partialInputs) {
  let parsed;
  assert.doesNotThrow(() => { parsed = parseTashkentDateTimeInput(value); }, `parse ${JSON.stringify(value)}`);
  check(['empty', 'invalid'].includes(parsed.state), `${JSON.stringify(value)} is empty or invalid, never valid`);
  let schedule;
  assert.doesNotThrow(() => { schedule = validateWorkSchedule({ dueInput: value, plannedStartInput: value }); });
  check(schedule.errors.due_at && schedule.values.due_at === null, `${JSON.stringify(value)} is rejected on submit with a visible error`);
  check(inspectionDueError(value) !== '', `${JSON.stringify(value)} is rejected for an inspection deadline`);
}
equal(parseTashkentDateTimeInput(''), { state: 'empty' }, 'empty input stays empty');
equal(parseTashkentDateTimeInput('2026-09-25T17:00'), {
  state: 'valid', iso: '2026-09-25T17:00:00+05:00', epochMs: Date.parse('2026-09-25T12:00:00Z'),
}, 'a complete value becomes a timezone-aware Asia/Tashkent instant');
equal(parseTashkentDateTimeInput('2028-02-29T23:59:30').iso, '2028-02-29T23:59:30+05:00', 'leap days and seconds are accepted');
equal(toTashkentDateTimeInput(new Date('2026-09-24T20:30:00Z')), '2026-09-25T01:30', 'wall clock uses Asia/Tashkent');
equal(toTashkentDateInput(new Date('2026-12-31T19:00:00Z')), '2027-01-01', 'the Tashkent date rolls over at UTC 19:00');
equal(toTashkentDateTimeInput('not a date'), '', 'an invalid instant renders as empty input');
equal(tashkentDateTimeInputAfterDays(1, { now: new Date('2026-09-24T20:30:00Z') }), '2026-09-26T17:00', 'tomorrow is a Tashkent calendar day');

// The conversion is independent of the process time zone.
const probe = `import { parseTashkentDateTimeInput, toTashkentDateTimeInput } from ${JSON.stringify(new URL('../src/utils/tashkentTime.js', import.meta.url).href)};
process.stdout.write(JSON.stringify([parseTashkentDateTimeInput('2026-09-25T17:00').iso, toTashkentDateTimeInput(new Date('2026-09-24T20:30:00Z'))]));`;
for (const zone of ['UTC', 'America/New_York', 'Asia/Tokyo', 'Europe/Moscow']) {
  const output = execFileSync(process.execPath, ['--input-type=module', '-e', probe], { env: { ...process.env, TZ: zone } }).toString();
  equal(JSON.parse(output), ['2026-09-25T17:00:00+05:00', '2026-09-25T01:30'], `deterministic under TZ=${zone}`);
}

// ─── Agronomy work schedule (submitted values) ─────────────────────────────
equal(validateWorkSchedule({ dueInput: '2026-10-01T09:30', plannedStartInput: '' }), {
  errors: {}, values: { planned_start_at: null, due_at: '2026-10-01T09:30:00+05:00' },
}, 'an empty optional start stays null');
equal(validateWorkSchedule({ dueInput: '2026-10-01T09:30', plannedStartInput: '2026-09-30T08:00' }).values, {
  planned_start_at: '2026-09-30T08:00:00+05:00', due_at: '2026-10-01T09:30:00+05:00',
}, 'both submitted values carry the Tashkent offset');
check(validateWorkSchedule({ dueInput: '2026-09-30T07:00', plannedStartInput: '2026-09-30T08:00' }).errors.due_at, 'a deadline before the start is rejected');
check(validateWorkSchedule({ dueInput: '', plannedStartInput: '' }).errors.due_at, 'the deadline is required');
check(!validateWorkSchedule({ dueInput: '', plannedStartInput: '' }, { dueRequired: false }).errors.due_at, 'an optional deadline may stay empty');
equal(workDraftInputs({ due_at: '2026-10-01T04:30:00.000Z', planned_start_at: null }), { dueInput: '2026-10-01T09:30', plannedStartInput: '' }, 'a draft saved in the former ISO form is restored as raw input');
equal(workDraftInputs({ dueInput: '2026-10-0', plannedStartInput: '' }), { dueInput: '2026-10-0', plannedStartInput: '' }, 'raw partial draft input is restored as typed');

// ─── Canonical inspection requests (no fabricated sources) ────────────────
const now = Date.parse('2026-09-24T06:00:00Z');
const manual = buildManualInspectionRequest({ fieldId: '12', reason: '  Проверить северный угол поля ', priority: 'high', dueInput: '2026-09-25T17:00', assignedToId: '7' }, now);
equal(manual, { payload: {
  field_id: 12, source_kind: 'manual', reason: 'Проверить северный угол поля', priority: 'high',
  due_at: '2026-09-25T17:00:00+05:00', assigned_to_id: 7,
} }, 'a manual request carries exactly the canonical fields');
const unassigned = buildManualInspectionRequest({ fieldId: 12, reason: 'Проверить поле', dueInput: '2026-09-25T17:00' }, now);
check(!('assigned_to_id' in unassigned.payload), 'an unassigned inspection omits assigned_to_id');
for (const forbidden of ['source_alert_id', 'provider', 'item_id', 'acquired_at', 'index_name', 'sampled_value', 'geometry_hash', 'point', 'zone']) {
  check(!(forbidden in manual.payload), `a manual request never fabricates ${forbidden}`);
}
equal(buildManualInspectionRequest({ fieldId: 12, reason: 'Проверить поле', dueInput: '' }, now), { error: 'Укажите срок осмотра.' }, 'the deadline is required');
equal(buildManualInspectionRequest({ fieldId: 12, reason: 'Проверить поле', dueInput: '2026-09-2' }, now), { error: 'Срок указан неполностью или некорректно.' }, 'a partial deadline is rejected');
equal(buildManualInspectionRequest({ fieldId: 12, reason: 'Проверить поле', dueInput: '2026-09-23T10:00' }, now), { error: 'Срок не может быть в прошлом.' }, 'a past deadline is rejected');
check(buildManualInspectionRequest({ fieldId: 12, reason: 'abc', dueInput: '2026-09-25T17:00' }, now).error, 'a too short reason is rejected');
check(buildManualInspectionRequest({ fieldId: 0, reason: 'Проверить поле', dueInput: '2026-09-25T17:00' }, now).error, 'a field is required');

const attention = attentionInspectionReason({
  field_name: 'Поле 7', field_id: 7, priority: 'critical', attention_score: 91,
  observation_date: '2026-09-20', reason_codes: ['ndvi_drop', 'weather_water_deficit'], recommended_checks: ['Проверить полив'],
});
check(attention.includes('Очередь внимания: поле «Поле 7»') && attention.includes('критический') && attention.includes('ndvi_drop') && attention.includes('Проверить полив'), 'the attention context is kept in the visible reason');
equal(canonicalInspectionPriority('critical'), 'urgent', 'critical maps to urgent');
equal(canonicalInspectionPriority('medium'), 'normal', 'medium maps to normal');
equal(canonicalInspectionPriority('high'), 'high', 'high stays high');
equal(canonicalInspectionPriority('low'), 'low', 'low stays low');
equal(canonicalInspectionPriority(undefined), 'normal', 'unknown maps to normal');
equal(irrigationInspectionReason('irrigation_interruption'), 'Контекст орошения: Проверить прерывание полива. Проверить состояние в поле и зафиксировать фактические доказательства.', 'the irrigation reason is kept in the visible reason text');

check(canCancelInspection({ status: 'pending', source: { kind: 'legacy' } }), 'an open legacy row can be closed out');
check(!canCancelInspection({ status: 'completed', source: { kind: 'legacy' } }), 'a finished legacy row cannot');
check(canCancelInspection({ status: 'submitted', source: { kind: 'manual' } }), 'an open canonical row can be cancelled');
check(!canCancelInspection({ status: 'confirmed', source: { kind: 'manual' } }), 'a confirmed canonical row cannot');

// ─── Canonical remediation vocabulary == TASK_225 backend ─────────────────
const readSource = (...parts) => readFileSync(path.join(...parts), 'utf8').replace(/\r\n/g, '\n');
const schema = readSource(repository, 'backend', 'schemas', 'operational_center.py').replace(/#.*$/gm, '');
const literal = (name) => {
  const match = schema.match(new RegExp(`${name} = Literal\\[([\\s\\S]*?)\\]`));
  return Array.from(match[1].matchAll(/"([a-z_]+)"/g), (item) => item[1]);
};
equal(Object.keys(REMEDIATION_STATUS).sort(), literal('RemediationStatus').sort(), 'every canonical remediation status has one Russian label');
equal(Object.keys(OPERATIONAL_STATUS_LABELS).sort(), literal('OperationalStatus').sort(), 'every operational status has one Russian label');
const summaryFields = Array.from(schema.match(/class SummaryResponse\(StrictModel\):([\s\S]*?)\n\n/)[1].matchAll(/^ {4}([a-z_]+): int$/gm), (item) => item[1]);
const page = readSource(here, '..', 'src', 'pages', 'OperationalCenterPage.jsx');
for (const field of summaryFields) check(page.includes(`'${field}'`), `summary count ${field} is rendered`);
const api = readSource(repository, 'backend', 'api', 'operational_center.py');
const accepted = api.match(/operational_status: str \| None = Query\([\s\S]*?pattern="\^\(([^)]*)\)\$"/)[1].split('|');
equal([...FILTERABLE_OPERATIONAL_STATUSES].sort(), [...accepted].sort(), 'the state filter offers exactly the values the backend accepts');
check(!FILTERABLE_OPERATIONAL_STATUSES.includes('closed_without_improvement'), 'the filter never sends a value the backend rejects');

for (const status of Object.keys(REMEDIATION_STATUS)) {
  for (const priority of ['low', 'normal', 'urgent', 'critical']) {
    for (const overdue of [false, true]) {
      const item = { remediation_status: status, operational_status: 'awaiting_verification', priority, is_overdue: overdue };
      const tone = caseStatusTone(item);
      const label = caseStatusLabel(item);
      const verified = ['improved_closed', 'improved_awaiting_closure'].includes(status);
      check(tone.includes('emerald') === (verified && (status === 'improved_closed' || (!overdue && !['urgent', 'critical'].includes(priority)))), `${status}/${priority}/${overdue}: green only for a verified improvement`);
      check(!/улучшение подтверждено/i.test(label) || verified, `${status}: never labelled as a confirmed improvement`);
    }
  }
}
const closedWithout = { remediation_status: 'closed_without_improvement', operational_status: 'closed_without_improvement', priority: 'urgent', is_overdue: false };
equal(caseStatusTone(closedWithout), TONE_CLASSES.closed, 'closed without improvement is never green');
equal(caseStatusLabel(closedWithout), 'Закрыто без подтверждённого улучшения', 'closed without improvement is labelled as such');
equal(caseStatusLabel({ remediation_status: 'not_improved', provenance: { verification_status: 'WORSENED' } }), 'Ухудшение после мер', 'WORSENED is distinguished');
equal(caseStatusLabel({ remediation_status: 'not_improved', provenance: { verification_status: 'NO_MATERIAL_CHANGE' } }), 'Без существенных изменений после мер', 'NO_MATERIAL_CHANGE is distinguished');
equal(caseStatusTone({ remediation_status: 'not_improved', priority: 'normal' }), TONE_CLASSES.danger, 'not improved is a danger tone');
equal(caseStatusLabel({ remediation_status: 'data_unavailable', operational_status: 'stale' }), 'Данные устарели', 'data unavailable keeps its operational detail');
equal(caseStatusLabel({ remediation_status: 'improved_closed', operational_status: 'improved_closed' }), 'Закрыто: улучшение подтверждено', 'verified improvement closure');
equal(planStatusTone({ status: 'closed', verification_status: 'IMPROVED' }), 'success', 'a plan closed after IMPROVED is green');
equal(planStatusTone({ status: 'closed', verification_status: 'WORSENED' }), 'closed', 'an override closure is never green');
equal(planStatusLabel({ status: 'closed', verification_status: 'INCONCLUSIVE' }), 'Закрыт без подтверждённого улучшения', 'an override closure is labelled as such');
equal(VERIFICATION_STATUS.WORSENED.tone, 'danger', 'WORSENED is not a success');
equal(VERIFICATION_STATUS.NO_MATERIAL_CHANGE.tone, 'danger', 'NO_MATERIAL_CHANGE is not a success');

// ─── Dashboard totals come from the server summary ────────────────────────
equal(dashboardAlertTotals({ active_alerts: 57, critical_alerts: 12, warning_alerts: 30 }), { active: 57, critical: 12, warning: 30 }, 'KPI totals are the server totals');
equal(dashboardAlertTotals(null), null, 'no summary means no totals, not zero');
equal(dashboardAlertTotals({ active_alerts: null }), null, 'a missing total is unknown, not zero');
check(!('info' in dashboardAlertTotals({ active_alerts: 57, critical_alerts: 12, warning_alerts: 30 })), 'no info total is fabricated');

console.log(JSON.stringify({ status: 'PASS', suite: 'TASK_226 unit contracts', assertions }));
