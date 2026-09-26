// TASK_233 Management Analytics frontend contract: every key and query
// parameter the page uses exists in the accepted TASK_232 backend contract,
// filters are validated before any request, errors keep distinct kinds, values
// are formatted without being recomputed, and navigation is limited to the
// roles the endpoint accepts.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { installBrowserEnvironment } from './lib/node-browser-env.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repository = path.resolve(root, '..');
installBrowserEnvironment();

// Retry back-off timers run immediately; the policy, not the delay, is under test.
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (callback, _delay, ...args) => realSetTimeout(callback, 0, ...args);

const { AxiosError, CanceledError } = await import('axios');
const clientModule = await import('../src/api/client.js');
const api = await import('../src/api/managementAnalytics.js');
const utils = await import('../src/utils/managementAnalytics.js');
const vocabulary = await import('../src/config/managementAnalytics.js');
const roles = await import('../src/config/roleAccess.js');
const client = clientModule.default;

let assertions = 0;
const check = (condition, message) => { assert.ok(condition, message); assertions += 1; };
const equal = (actual, expected, message) => { assert.deepEqual(actual, expected, message); assertions += 1; };
const spaces = (value) => String(value).replace(/[\u00a0\u202f]/g, ' ');
const read = (file) => readFileSync(path.join(root, file), 'utf8');
const readRepository = (file) => readFileSync(path.join(repository, file), 'utf8');
const stripComments = (source) => source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');

// ─── 1. The accepted TASK_232 contract, read from the backend of this branch ─
const schemaSource = readRepository('backend/schemas/management_analytics.py');
const schema = new Map();
for (const block of schemaSource.split(/\nclass /).slice(1)) {
  const name = block.match(/^(\w+)\(/)?.[1];
  const fields = [...block.matchAll(/^ {4}(\w+): /gm)].map((match) => match[1]).filter((field) => field !== 'model_config');
  if (name) schema.set(name, new Set(fields));
}
const fields = (name) => {
  const value = schema.get(name);
  assert.ok(value, `schema class ${name} exists`);
  return value;
};
const hasAll = (name, keys, message) => {
  const known = fields(name);
  const missing = keys.filter((key) => !known.has(key));
  equal(missing, [], `${message}: keys missing from ${name}`);
};
const sameKeys = (name, keys, message) => equal([...keys].sort(), [...fields(name)].sort(), message);

const routerSource = readRepository('backend/api/management_analytics.py');
const queryParameters = [...routerSource.matchAll(/^\s{4}(\w+): [^=\n]+= Query\(/gm)].map((match) => match[1]);
const serviceSource = readRepository('backend/services/management_analytics.py');

equal(vocabulary.MANAGEMENT_ANALYTICS_DEFINITIONS_VERSION, 'management_analytics_v1', 'the page is written for management_analytics_v1');
check(serviceSource.includes(`DEFINITIONS_VERSION = "${vocabulary.MANAGEMENT_ANALYTICS_DEFINITIONS_VERSION}"`), 'the backend publishes the same definitions version');
check(schemaSource.includes('definitions_version: Literal["management_analytics_v1"]'), 'the response schema pins the version');

equal(
  [...api.MANAGEMENT_ANALYTICS_PARAMETERS].sort(),
  queryParameters.filter((name) => name !== 'crop_type_id').sort(),
  'the client sends exactly the accepted query parameters',
);
check(queryParameters.includes('crop_type_id') && /crop_type_id: int \| None = Query\(None, include_in_schema=False\)/.test(routerSource), 'the backend keeps crop_type_id only to refuse it');
check(!api.MANAGEMENT_ANALYTICS_PARAMETERS.includes('crop_type_id'), 'a bare crop_type_id is never a client parameter');

const phaseStates = vocabulary.LIFECYCLE_PHASES.flatMap((phase) => phase.states);
sameKeys('RemediationStatusCounts', phaseStates.map((state) => state.key), 'the workflow draws every TASK_225 state exactly once');
equal(new Set(phaseStates.map((state) => state.key)).size, phaseStates.length, 'no state is drawn twice');
equal(vocabulary.LIFECYCLE_PHASES.map((phase) => phase.id), ['attention', 'inspection', 'review', 'plan', 'work', 'verification'], 'canonical workflow order');
const nested = {
  needs_inspection: 'NeedsInspection', plan_active: 'PlanActive', awaiting_satellite_verification: 'AwaitingVerification',
  verification_blocked: 'VerificationBlocked', not_improved: 'NotImproved',
};
for (const state of phaseStates) {
  const parts = [...(state.parts || []), ...(state.note ? [state.note] : [])].map(([key]) => key);
  if (parts.length) hasAll(nested[state.key], [...parts, 'total'], `sub-counts of ${state.key}`);
}
sameKeys('NdviFreshness', vocabulary.NDVI_FRESHNESS_LABELS.map(([key]) => key), 'every NDVI freshness state is labelled');
sameKeys('PriorityCounts', vocabulary.PRIORITY_LABELS.map(([key]) => key), 'every priority is labelled');
sameKeys('SourceCounts', vocabulary.SOURCE_LABELS.map(([key]) => key), 'every case source is labelled');
hasAll('Coverage', ['fields_in_scope', 'monitored_fields', 'inactive_fields', 'monitored_fields_with_active_problems', 'ndvi_freshness'], 'coverage tile');
hasAll('ActiveProblems', ['total', 'fields_affected', 'by_source', 'by_priority', 'legacy_open_inspections'], 'active problems tile');
hasAll('CurrentState', ['as_of', 'active_problems', 'by_remediation_status', 'plans_pending_verification', 'work_items', 'overdue_cases', 'data_unavailable'], 'current state section');
sameKeys('OverdueCases', ['total', 'inspection_stage', 'work_stage'], 'overdue cases are the Operational Center flag split');
hasAll('WorkItems', ['active', 'planned', 'in_progress', 'unassigned', 'overdue_work_items'], 'work items tile');
sameKeys('DataUnavailable', ['freshness_cases', 'external_cases'], 'data availability card');

sameKeys('VerifiedOutcomes', [...vocabulary.VERIFIED_OUTCOMES.map((item) => item.key), 'total'], 'verified outcomes are exactly the three conclusive statuses');
equal(vocabulary.VERIFIED_OUTCOMES.map((item) => item.status), ['IMPROVED', 'NO_MATERIAL_CHANGE', 'WORSENED'], 'outcome statuses of policy r3-f-v1');
sameKeys('UnverifiedResolutions', ['total', ...vocabulary.UNVERIFIED_RESOLUTIONS.map(([key]) => key)], 'every non-conclusive resolution is shown apart');
const verifiedKeys = new Set(vocabulary.VERIFIED_OUTCOMES.map((item) => item.key));
equal(vocabulary.UNVERIFIED_RESOLUTIONS.filter(([key]) => verifiedKeys.has(key)), [], 'pending, blocked and inconclusive never share an outcome key');
hasAll('Outcomes', ['resolved_cycles', 'verified', 'unverified', 'closed', 'returned_for_rework', 'reopen_events'], 'outcomes card');
sameKeys('ClosedCycles', ['total', 'improved', 'without_improvement'], 'closed cycles');
sameKeys('ReopenEvents', ['total', 'after_closure', 'after_verification'], 'reopen events');

sameKeys('CycleTimeMetrics', vocabulary.CYCLE_TIME_METRICS.map((item) => item.key), 'every measured cycle time is labelled, none is invented');
check(!vocabulary.CYCLE_TIME_METRICS.some((item) => item.key.startsWith('alert_')), 'no alert-to-inspection duration is fabricated');
check(serviceSource.includes('"alert_signal_to_inspection_opened"') || serviceSource.includes("'alert_signal_to_inspection_opened'"), 'the backend reports the alert duration as unsupported');
check(Boolean(vocabulary.UNSUPPORTED_CYCLE_TIMES.alert_signal_to_inspection_opened), 'the unsupported alert duration is explained');
hasAll('DurationMetric', ['sample_count', 'median_hours', 'p90_hours', 'status', 'population'], 'duration rows');
hasAll('CycleTimes', ['p90_minimum_samples', 'metrics', 'unsupported'], 'cycle times card');

const completionClass = { work_completion: 'WorkCompletion', verification_completion: 'VerificationCompletion', plan_closure: 'PlanClosure' };
sameKeys('Completion', vocabulary.COMPLETION_METRICS.map((item) => item.key), 'every completion cohort is shown');
for (const metric of vocabulary.COMPLETION_METRICS) {
  hasAll(completionClass[metric.key], ['numerator', 'denominator', 'rate', ...metric.parts.map(([key]) => key), ...(metric.extraRate ? [metric.extraRate[0]] : [])], `completion ${metric.key}`);
}

hasAll('PeriodActivity', vocabulary.PERIOD_ACTIVITY.map(([key]) => key), 'period activity summary');
hasAll('InspectionsOpened', vocabulary.INSPECTION_OPENING_PARTS.map(([key]) => key), 'inspection opening split');
hasAll('PeriodBreakdown', vocabulary.PERIOD_SERIES.map((item) => item.key), 'every chart series is a period bucket key');
equal(vocabulary.GRANULARITY_LABELS.map(([key]) => key), [...utils.GRANULARITIES], 'granularity labels');
check(/Granularity = Literal\["day", "week", "month"\]/.test(schemaSource), 'granularities of the contract');

const probe = {
  monitored_fields: 1,
  current: { active_problems: 2, overdue_cases: 3, overdue_work_items: 4, by_remediation_status: Object.fromEntries([...fields('RemediationStatusTotals')].map((key, index) => [key, 10 + index])) },
  period: Object.fromEntries([...fields('BreakdownPeriod')].map((key, index) => [key, 30 + index])),
};
for (const column of vocabulary.BREAKDOWN_CURRENT_COLUMNS) check(Number.isInteger(column.read(probe)), `breakdown column ${column.id} reads a contract value`);
for (const column of vocabulary.BREAKDOWN_PERIOD_COLUMNS) check(Number.isInteger(column.read(probe)), `breakdown column ${column.id} reads a contract value`);
hasAll('BreakdownCurrent', ['active_problems', 'overdue_cases', 'overdue_work_items', 'by_remediation_status'], 'breakdown current values');
hasAll('RemediationStatusTotals', ['needs_inspection', 'work_active', 'awaiting_satellite_verification', 'verification_blocked'], 'breakdown state columns');
hasAll('BreakdownPeriod', vocabulary.BREAKDOWN_PERIOD_COLUMNS.map((column) => column.id), 'breakdown period columns');
hasAll('CurrentCropBreakdown', ['current_crop_type_id', 'current_crop_name'], 'crop breakdown is the current classification');
hasAll('Breakdowns', ['enterprises', 'current_crops', 'periods', 'fields'], 'breakdown dimensions');
hasAll('FieldPage', ['items', 'total', 'limit', 'offset'], 'server paging of the field breakdown');
check(!fields('Breakdowns').has('crops'), 'there is no historical crops dimension to show');
hasAll('CropClassification', ['basis', 'reference_year', 'historical_crop_at_event'], 'crop classification metadata');
check(/historical_crop_at_event: Literal\[False\]/.test(schemaSource), 'the contract never attributes a metric to a historical crop');
hasAll('Provenance', ['verification_policy_version', 'sources', 'excluded_legacy_sources', 'definitions_fingerprint'], 'provenance panel');
hasAll('ManagementAnalyticsResponse', ['definitions_version', 'generated_at', 'timezone', 'scope', 'crop_classification', 'period', 'provenance', 'coverage', 'current', 'period_activity', 'outcomes', 'cycle_times', 'completion', 'breakdowns', 'limitations'], 'top-level sections');
check(/Crop type not found|Enterprise not found|Field not found/.test(serviceSource), 'non-enumerating 404 texts exist in the backend');
for (const text of ['Enterprise not found', 'Field not found', 'Crop type not found', 'Manager has no enterprise_id', 'Management analytics requires a management role']) {
  check(serviceSource.includes(`"${text}"`), `backend detail "${text}" is the one the page maps`);
}

// ─── 2. Period rules (mirror of the TASK_232 window rule) ───────────────────
equal(utils.presetPeriod(30, '2026-09-26'), { dateFrom: '2026-08-28', dateTo: '2026-09-26' }, '30 days = the server default window');
equal(utils.presetPeriod(7, '2026-03-01'), { dateFrom: '2026-02-23', dateTo: '2026-03-01' }, 'preset across a month end');
equal(utils.presetPeriod(365, '2026-09-26'), { dateFrom: '2025-09-27', dateTo: '2026-09-26' }, '365-day preset');
equal(utils.inclusiveDays('2025-09-27', '2026-09-26'), 365, 'inclusive day count');
equal(utils.shiftCalendarDate('2024-02-28', 1), '2024-02-29', 'leap day');
equal(utils.shiftCalendarDate('2026-12-31', 1), '2027-01-01', 'year end');
equal(utils.validatePeriod('2025-09-26', '2026-09-26'), { valid: true, days: 366 }, '366 inclusive days are accepted');
check(!utils.validatePeriod('2025-09-25', '2026-09-26').valid, '367 days are refused');
check(!utils.validatePeriod('2026-09-27', '2026-09-26').valid, 'a reversed period is refused');
check(utils.validatePeriod('2026-09-26', '2026-09-26').valid, 'a one-day period is accepted');
for (const bad of ['', null, undefined, '2026-02-30', '2026-9-1', ' 2026-09-01', '2026-13-01', '1999-12-31', '2026-09-01T00:00']) {
  check(!utils.validatePeriod(bad, '2026-09-26').valid && !utils.validatePeriod('2026-09-01', bad).valid, `invalid date ${JSON.stringify(bad)} is refused`);
}
check(utils.validatePeriod('2024-02-29', '2024-03-01').valid, 'a real leap day is accepted');

// ─── 3. Request parameters ──────────────────────────────────────────────────
equal(api.buildManagementAnalyticsParams({
  dateFrom: '2026-08-28', dateTo: '2026-09-26', enterpriseId: '7', fieldId: '', currentCropTypeId: '301',
  granularity: 'week', fieldLimit: 50, fieldOffset: 0,
}), {
  date_from: '2026-08-28', date_to: '2026-09-26', enterprise_id: 7, current_crop_type_id: 301,
  granularity: 'week', field_limit: 50, field_offset: 0,
}, 'accepted names, integer ids, empty values omitted');
equal(api.buildManagementAnalyticsParams({ enterpriseId: '', fieldId: null, currentCropTypeId: undefined, granularity: '' }), {}, 'nothing empty is sent');
const refused = (filters, message) => {
  assert.throws(() => api.buildManagementAnalyticsParams(filters), api.ManagementAnalyticsFilterError, message);
  assertions += 1;
};
refused({ dateFrom: '2026-09-27', dateTo: '2026-09-26' }, 'a reversed period is refused, not sent');
refused({ dateFrom: '2026-09-01' }, 'a half period is refused, not sent');
refused({ dateFrom: '2026-02-30', dateTo: '2026-03-01' }, 'an impossible date is refused');
refused({ dateFrom: '2025-01-01', dateTo: '2026-09-26' }, 'a period over 366 days is refused');
for (const id of [0, -1, '1.5', 'abc', '1e3', '07x']) refused({ fieldId: id }, `field id ${id} is refused`);
refused({ granularity: 'year' }, 'an unknown granularity is refused');
refused({ fieldLimit: 201 }, 'the field page size is bounded');
refused({ fieldOffset: 10001 }, 'the field offset is bounded');
const cropOnly = api.buildManagementAnalyticsParams({ currentCropTypeId: 3, crop_type_id: 5, cropTypeId: 5 });
equal(cropOnly, { current_crop_type_id: 3 }, 'only the current-crop filter is produced');
equal(utils.scopeKeyOf({ date_from: 'a', date_to: 'b', granularity: 'week', field_offset: 0 }), utils.scopeKeyOf({ date_from: 'a', date_to: 'b', granularity: 'month', field_offset: 50 }), 'view parameters keep the scope key');
check(utils.scopeKeyOf({ enterprise_id: 7 }) !== utils.scopeKeyOf({ enterprise_id: 8 }), 'a different enterprise is a different scope');
check(utils.scopeKeyOf({ current_crop_type_id: 1 }) !== utils.scopeKeyOf({}), 'a crop filter is a different scope');

// ─── 4. HTTP behaviour through the shared client ────────────────────────────
const calls = [];
let script = [];
function outcome(kind, config) {
  if (kind === 'ok') return { data: { definitions_version: 'management_analytics_v1' }, status: 200, statusText: 'OK', headers: {}, config };
  if (kind === 'network') throw new AxiosError('Network Error', 'ERR_NETWORK', config);
  if (kind === 'cancel') throw new CanceledError('canceled', config);
  const status = Number(kind);
  throw new AxiosError(`HTTP ${status}`, status >= 500 ? 'ERR_BAD_RESPONSE' : 'ERR_BAD_REQUEST', config, null, {
    status, statusText: String(status), data: { detail: 'fixture' }, headers: {}, config,
  });
}
client.defaults.adapter = async (config) => {
  calls.push({ method: String(config.method).toUpperCase(), url: client.getUri(config).replace(/^\/api\//, ''), signal: config.signal || null });
  const next = script.length ? script.shift() : 'ok';
  return outcome(next, config);
};
const reset = (sequence = []) => { calls.length = 0; script = [...sequence]; };
const settle = (promise) => promise.then((value) => ({ ok: true, value }), (error) => ({ ok: false, error }));

reset(['ok']);
const controller = new AbortController();
const params = api.buildManagementAnalyticsParams({ dateFrom: '2026-08-28', dateTo: '2026-09-26', enterpriseId: 7, granularity: 'week', fieldLimit: 50, fieldOffset: 0 });
const loaded = await settle(api.getManagementAnalytics(params, controller.signal));
check(loaded.ok && loaded.value.definitions_version === 'management_analytics_v1', 'the snapshot is returned as the server sent it');
equal(calls.map((call) => call.method), ['GET'], 'one GET per snapshot');
equal(calls[0].url, 'management-analytics?date_from=2026-08-28&date_to=2026-09-26&enterprise_id=7&granularity=week&field_limit=50&field_offset=0', 'exact endpoint and query');
check(calls[0].signal === controller.signal, 'the request carries the caller\'s AbortSignal');

reset(['ok']);
await api.getManagementAnalytics({ date_from: '2026-09-01', date_to: '2026-09-02', crop_type_id: 9, field_id: '', unexpected: 1 });
equal(calls[0].url, 'management-analytics?date_from=2026-09-01&date_to=2026-09-02', 'unknown or empty parameters never leave the browser');

reset(['500', '500', '500', '500']);
const failed = await settle(api.getManagementAnalytics(params));
check(!failed.ok, 'a persistent 5xx is surfaced');
equal(calls.length, 1 + clientModule.MAX_AUTOMATIC_RETRIES, 'automatic retries stay within the shared client bound');
equal(utils.describeRequestError(failed.error).kind, 'server', '5xx is a retryable server error');
check(utils.describeRequestError(failed.error).retryable, 'a server error offers an explicit retry');

for (const status of ['401', '403', '404', '422']) {
  reset([status, 'ok']);
  await settle(api.getManagementAnalytics(params));
  equal(calls.length, 1, `HTTP ${status} is not retried automatically`);
}
reset(['network', 'ok']);
const offline = await settle(api.getManagementAnalytics(params));
equal(calls.length, 1, 'a request without a response is not retried automatically');
equal(utils.describeRequestError(offline.error).kind, 'network', 'network failure kind');

reset(['ok']);
const aborted = new AbortController();
aborted.abort();
const cancelled = await settle(api.getManagementAnalytics(params, aborted.signal));
equal(calls.length, 0, 'an aborted request never reaches the transport');
equal(utils.describeRequestError(cancelled.error), null, 'a cancelled request is not an error state');
check(utils.isCancelledRequest(cancelled.error), 'cancellation is recognised');

// ─── 5. Error kinds: error is never empty, 401/403/404/422/5xx are distinct ─
const failure = (status, detail) => ({ response: { status, data: { detail } } });
equal(utils.describeRequestError(failure(401, 'Not authenticated')).kind, 'unauthorized', '401');
equal(utils.describeRequestError(failure(403, 'Management analytics requires a management role')).kind, 'forbidden', '403');
check(/не указано предприятие/.test(utils.describeRequestError(failure(403, 'Manager has no enterprise_id')).message), '403 for a manager without an enterprise is explained');
for (const [detail, fragment] of [['Enterprise not found', 'предприятие'], ['Field not found', 'поле'], ['Crop type not found', 'культура']]) {
  const described = utils.describeRequestError(failure(404, detail));
  check(described.kind === 'not_found' && described.resettable && described.message.toLowerCase().includes(fragment), `404 ${detail}`);
}
for (const [detail, fragment] of [
  ['date_from must not be later than date_to', 'позже'],
  ['date range exceeds 366 inclusive days', '366'],
  ['crop_type_id is not supported: crops are classified by each field\'s current crop season, never by the crop at event time; use current_crop_type_id', 'текущей культуре'],
  [{ code: 'result_too_large', resource: 'management_analytics.enterprises', row_cap: 500 }, 'предел'],
  [[{ type: 'greater_than', loc: ['query', 'field_id'], msg: 'Input should be greater than 0' }], 'отклонил'],
]) {
  const described = utils.describeRequestError(failure(422, detail));
  check(described.kind === 'invalid' && described.resettable && !described.retryable && described.message.includes(fragment), `422 ${JSON.stringify(detail).slice(0, 40)}`);
}
check(!utils.describeRequestError(failure(422, '<script>alert(1)</script>')).message.includes('<script>'), 'unknown server texts are not echoed');
equal(utils.describeRequestError(failure(503, 'x')).kind, 'server', '503');
equal(utils.describeRequestError(failure(418, 'x')).kind, 'unexpected', 'other statuses');
check(/Нет сети/.test(utils.describeRequestError({ code: 'ERR_NETWORK' }, { online: false }).message), 'offline wording');

// ─── 6. Formatting keeps the server's numerator, denominator and rate ──────
equal(spaces(utils.formatShare({ numerator: 18, denominator: 24, rate: 0.75 })), '18 из 24 (75 %)', 'share keeps the population');
equal(spaces(utils.formatShare({ numerator: 7, denominator: 9, rate: 0.7778 })), '7 из 9 (77,8 %)', 'server-rounded rate');
equal(utils.formatShare({ numerator: 0, denominator: 0, rate: null }), 'нет данных в выборке за период', 'a zero denominator has no percentage');
equal(spaces(utils.formatShare({ numerator: 3, denominator: 4, rate: null })), '3 из 4', 'a missing rate is not invented');
equal(utils.formatRate(null), null, 'no rate without a server rate');
equal(spaces(utils.formatCount(1234)), '1 234', 'grouped count');
equal(utils.formatCount(undefined), '—', 'missing count');
equal(utils.formatHours(0.25), '15 мин', 'minutes');
equal(utils.formatHours(5.25), '5,3 ч', 'hours');
equal(utils.formatHours(70.25), '2,9 сут', 'days from 48 hours');
equal(utils.formatExactHours(70.25), '70,3 ч', 'exact hours next to days');
equal(utils.formatHours(null), '—', 'no duration without samples');
equal(utils.formatCalendarDate('2026-09-26'), '26.09.2026', 'calendar date');
equal(utils.bucketLabel({ bucket_start: '2026-08-31', bucket_end: '2026-09-06' }, 'week'), '31.08–06.09', 'week bucket');
equal(utils.bucketLabel({ bucket_start: '2026-09-01', bucket_end: '2026-09-30' }, 'month'), '09.2026', 'whole month bucket');
equal(utils.bucketLabel({ bucket_start: '2026-08-28', bucket_end: '2026-08-31' }, 'month'), '28.08–31.08', 'clipped month bucket');
equal(utils.bucketLabel({ bucket_start: '2026-09-26', bucket_end: '2026-09-26' }, 'day'), '26.09', 'day bucket');

// ─── 7. Source safeguards: one analytics source, no client-side business logic
const maDirectory = path.join(root, 'src', 'components', 'ManagementAnalytics');
const componentFiles = readdirSync(maDirectory).filter((name) => name.endsWith('.jsx'));
const page = read('src/pages/ManagementAnalyticsPage.jsx');
const components = componentFiles.map((name) => ({ name, source: readFileSync(path.join(maDirectory, name), 'utf8') }));
const apiSource = read('src/api/managementAnalytics.js');
const everything = [{ name: 'ManagementAnalyticsPage.jsx', source: page }, ...components];

const pageImports = [...page.matchAll(/from '([^']+)'/g)].map((match) => match[1]);
equal(pageImports.filter((item) => item.includes('/api/')), ['../api/managementAnalytics', '../api/operationalCenter'], 'the page talks to two API modules only');
check(/import \{ getOperationalFilterOptions \} from '\.\.\/api\/operationalCenter'/.test(page), 'only the Operational Center field option list is used');
for (const { name, source } of components) check(!/from '\.\.\/\.\.\/api\//.test(source), `${name} is presentational (no API import)`);
for (const { name, source } of everything) {
  const code = stripComments(source);
  for (const forbidden of [
    'getOperationalSummary', 'listOperationalQueue', 'getOperationalCase', 'getDashboardSummary', 'getAlerts',
    'getExecutiveOverview', 'getExecutiveAccountability', 'executiveAccountability', 'closedLoopAgronomy',
    'getAgronomyPlan', 'anomalyInspections', 'corrective', 'verification-requests', 'operational-actions',
  ]) check(!code.includes(forbidden), `${name} does not use ${forbidden}`);
  check(!/\.reduce\(/.test(code), `${name} aggregates nothing in the browser`);
  check(!/\.overdue\b/.test(code), `${name} never reads a bare overdue figure (plan-workspace rule)`);
  check(!/(?<!current_)crop_type_id/.test(code), `${name} never uses a bare crop_type_id`);
  check(!/maplibre/i.test(code), `${name} does not use MapLibre`);
}
check(!/(?<!current_)crop_type_id/.test(stripComments(apiSource)), 'the API module never produces crop_type_id');
check(/getManagementAnalytics\(params, controller\.signal\)/.test(page), 'every snapshot request is abortable');
check(/return \(\) => controller\.abort\(\)/.test(page), 'effects abort on change and unmount');
check((page.match(/controller\.signal\.aborted \|\| generation !== generationRef\.current/g) || []).length >= 2, 'a superseded response is ignored on both the success and the failure path');
check(/controller\.signal\.aborted/.test(page), 'aborted responses never update state');
check(/\[request\.key, reloadToken\]/.test(page), 'the request effect depends on a stable key');
check(/current\.data && current\.scopeKey === scopeKey/.test(page), 'old numbers stay only for the same scope');
check(/status: 'loading', data: null/.test(page), 'a new scope clears previous numbers');
for (const state of ['AnalyticsSkeleton', 'AnalyticsErrorPanel', 'EmptyScopePanel', 'RefreshErrorBanner']) check(page.includes(`<${state}`), `distinct state ${state}`);
check(/fields_in_scope === 0/.test(page), 'an empty scope is a server fact, not a failed request');
check(/MANAGEMENT_ANALYTICS_DEFINITIONS_VERSION/.test(page), 'a different definitions version is flagged');
const current = components.find((item) => item.name === 'CurrentStateSection.jsx').source;
check(/value=\{overdue\.total\}/.test(current) && /const overdue = current\.overdue_cases/.test(current), 'the overdue KPI is current.overdue_cases.total');
check(/work\.overdue_work_items/.test(current), 'late work items are shown as their own unit');
check(/current\.plans_pending_verification/.test(current), 'the verification KPI is the command-center-aligned figure');
const period = components.find((item) => item.name === 'PeriodSection.jsx').source;
check(/VERIFIED_OUTCOMES\.map/.test(period) && /UNVERIFIED_RESOLUTIONS\.map/.test(period), 'verified and unverified resolutions are rendered from separate lists');
check(/formatShare\(metric\)/.test(period), 'completion keeps numerator and denominator');
check(/metric\.sample_count/.test(period), 'every cycle time shows its sample count');
const breakdown = components.find((item) => item.name === 'BreakdownSection.jsx').source;
check(/currentCropNote\(/.test(breakdown) && /'Текущие культуры'/.test(breakdown), 'the crop dimension is labelled as the current crop');
check(!/\.sort\(/.test(breakdown), 'breakdown rows keep the server order');
const filtersSource = components.find((item) => item.name === 'AnalyticsFilters.jsx').source;
check(/label="Текущая культура"/.test(filtersSource), 'the crop filter says it is the current crop');
check(/isAdmin \?/.test(filtersSource) && /Закреплено сервером/.test(filtersSource), 'only an admin chooses the enterprise');
check(vocabulary.currentCropNote(2026).includes('не культура на момент события') && vocabulary.currentCropNote(2026).includes('2026'), 'crop note states the TASK_232 limitation and reference year');

// ─── 8. Same name, same definition (accepted projections elsewhere) ────────
const executiveSource = readRepository('backend/services/executive_accountability.py');
const executiveComponent = read('src/components/Reports/ExecutiveAccountability.jsx');
const liveWork = serviceSource.match(/live_work AS \(([\s\S]*?)\n\),/)?.[1] || '';
check(liveWork.includes("p.status NOT IN ('closed','cancelled','superseded')") && liveWork.includes("w.status IN ('planned','in_progress')") && liveWork.includes('p.cycle=w.cycle'), 'TASK_232 live work: live plans, active items, current cycle');
check(executiveSource.includes(`LIVE_PLAN = "p.status NOT IN ('closed','cancelled','superseded')"`) && executiveSource.includes(`ACTIVE_WORK = "w.status IN ('planned','in_progress')"`) && /AND p\.cycle=w\.cycle/.test(executiveSource), 'the executive backlog counts the same live work items');
check(executiveComponent.includes("open_actions: 'Незавершённые работы по планам мер'") && current.includes('label="Незавершённые работы по планам мер"'), 'identical work-item population, identical name');
check(executiveSource.includes(`WORK_DUE_DATE = "(w.due_at AT TIME ZONE 'Asia/Tashkent')::date"`) &&/due_date < :as_of_date\) AS overdue_actions/.test(executiveSource), 'the executive overdue work is date-level against the period end');
check(/due_at < :as_of\)::integer AS overdue_work_items/.test(serviceSource), 'TASK_232 late work items compare the due instant with now');
check(executiveComponent.includes("'Просроченные работы по планам") && !current.includes("'Просроченные работы") && !current.includes('"Просроченные работы'), 'a different overdue-work definition never borrows the executive name');
equal(vocabulary.BREAKDOWN_CURRENT_COLUMNS.find((column) => column.id === 'overdue_work_items').label, 'Работы с истёкшим сроком', 'the late-item column has its own name');
check(current.includes('label="Работы с истёкшим сроком" value={work.overdue_work_items}'), 'the late-item KPI line has its own name');
check(current.includes('label="Активные проблемы"') && !current.includes('label="Активные"'), 'active problems never take the command-center name of problems plus unavailable data');
check(!/label="Просрочено"/.test(current) && current.includes('label="Просроченные ситуации"'), 'the overdue KPI is named for its unit (cases)');
equal(vocabulary.COMPLETION_METRICS.map((metric) => metric.title.includes('→')), [true, true, true], 'completion cohorts name their population and their numerator');
check(!period.includes('С проверенным результатом всего') && period.includes('Решено с проверенным результатом'), 'resolved outcomes are not confused with first verifications of the period');

// ─── 9. Navigation and routing follow the endpoint's roles ──────────────────
for (const role of ['admin', 'manager']) {
  check(roles.isViewAllowedForRole(role, 'management-analytics'), `${role} may open the page`);
  check(roles.getNavigationKeysForRole(role).includes('management-analytics'), `${role} sees the navigation item`);
}
for (const role of ['viewer', 'agronomist', 'unknown', undefined]) {
  check(!roles.isViewAllowedForRole(role, 'management-analytics'), `${role} is redirected away`);
  check(!roles.getNavigationKeysForRole(role).includes('management-analytics'), `${role} has no navigation item`);
}
equal(roles.getNavigationKeysForRole('manager').filter((key) => key !== 'management-analytics'), roles.getNavigationKeysForRole('viewer'), 'the manager keeps every other item of the shared list');
check(roles.isViewAllowedForRole('viewer', 'reports') && roles.isViewAllowedForRole('manager', 'reports'), 'other views are unchanged');
equal(roles.getNavigationLabelForRole('manager', 'management-analytics'), 'Управленческая аналитика', 'navigation label');
const app = read('src/App.jsx');
check(app.includes("'/management-analytics': 'management-analytics'"), 'route path');
check(app.includes("case 'management-analytics':") && app.includes('<ManagementAnalyticsPage enterprises={enterprises} />'), 'route renders the page');
check(app.includes("title: 'Управленческая аналитика'"), 'page title');
check(read('src/components/Layout/Sidebar.jsx').includes("{ key: 'management-analytics', icon: AnalyticsIcon }"), 'sidebar item');
check(read('scripts/run-contract-suites.mjs').includes("'test-management-analytics.mjs'"), 'this suite runs with the contract suites');

globalThis.setTimeout = realSetTimeout;
console.log(JSON.stringify({ status: 'PASS', suite: 'TASK_233 management analytics frontend contract', assertions }));
