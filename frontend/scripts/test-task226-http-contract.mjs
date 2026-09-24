// TASK_226 HTTP contract: retry policy (C12), 401 session invalidation (C7),
// field_ids serialization (C12), user-scoped caches, and a runtime scan of every
// API helper against the TASK_225 retired write endpoints.
import assert from 'node:assert/strict';
import { readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { installBrowserEnvironment } from './lib/node-browser-env.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const env = installBrowserEnvironment();

// Retry back-off timers run immediately; the policy, not the delay, is under test.
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (callback, _delay, ...args) => realSetTimeout(callback, 0, ...args);

const { AxiosError, CanceledError } = await import('axios');
const clientModule = await import('../src/api/client.js');
const session = await import('../src/auth/session.js');
const client = clientModule.default;

let assertions = 0;
const check = (condition, message) => { assert.ok(condition, message); assertions += 1; };
const equal = (actual, expected, message) => { assert.deepEqual(actual, expected, message); assertions += 1; };

// ─── Fake transport ─────────────────────────────────────────────────────────
const calls = [];
let script = [];
function outcome(kind, config) {
  if (kind === 'ok') return { data: { ok: true, items: [] }, status: 200, statusText: 'OK', headers: {}, config };
  if (kind === 'timeout') throw new AxiosError('timeout of 60000ms exceeded', 'ECONNABORTED', config);
  if (kind === 'cancel') throw new CanceledError('canceled', config);
  if (kind === 'network') throw new AxiosError('Network Error', 'ERR_NETWORK', config);
  const status = Number(kind);
  throw new AxiosError(`HTTP ${status}`, status >= 500 ? 'ERR_BAD_RESPONSE' : 'ERR_BAD_REQUEST', config, null, {
    status, statusText: String(status), data: { detail: 'fixture' }, headers: {}, config,
  });
}
client.defaults.adapter = async (config) => {
  const url = client.getUri(config).replace(/^\/api\//, '');
  calls.push({
    method: String(config.method).toUpperCase(),
    url,
    authorization: config.headers?.Authorization || config.headers?.get?.('Authorization') || null,
    idempotencyKey: config.headers?.['Idempotency-Key'] || null,
  });
  const next = script.length ? script.shift() : 'ok';
  if (typeof next === 'function') return next(config);
  return outcome(next, config);
};
const reset = (sequence = []) => { calls.length = 0; script = [...sequence]; };
const settle = (promise) => promise.then((value) => ({ ok: true, value }), (error) => ({ ok: false, error }));

// ─── 1. Automatic retries only for safe read methods (C12) ─────────────────
for (const [method, failure] of [['get', '500'], ['get', 'timeout'], ['head', '503'], ['head', 'timeout']]) {
  reset([failure, failure, failure, failure]);
  const result = await settle(client.request({ method, url: 'fixture/read' }));
  check(!result.ok, `${method} ${failure} still fails after bounded retries`);
  equal(calls.length, 1 + clientModule.MAX_AUTOMATIC_RETRIES, `${method.toUpperCase()} ${failure} retried within the bound`);
}
reset(['500', 'ok']);
check((await settle(client.get('fixture/read'))).ok, 'GET recovers when a retry succeeds');
equal(calls.length, 2, 'GET 500 then 200 executes twice');

for (const method of ['post', 'put', 'patch', 'delete']) {
  for (const failure of ['500', '502', 'timeout']) {
    reset([failure, 'ok', 'ok']);
    const result = await settle(client.request({ method, url: 'fixture/write', data: { value: 1 } }));
    check(!result.ok, `${method} ${failure} is surfaced, not retried`);
    equal(calls.length, 1, `${method.toUpperCase()} ${failure} executes exactly once`);
  }
}

reset(['500', 'ok']);
await settle(client.get('fixture/read', { __noRetry: true }));
equal(calls.length, 1, '__noRetry GET executes once');

for (const status of ['400', '403', '404', '409', '422']) {
  reset([status, 'ok']);
  await settle(client.get('fixture/read'));
  equal(calls.length, 1, `GET ${status} is not retried`);
}

reset(['network', 'ok']);
await settle(client.get('fixture/read'));
equal(calls.length, 1, 'GET without a response (offline) is not retried');

reset(['cancel', 'ok']);
const cancelled = await settle(client.get('fixture/read'));
check(!cancelled.ok && cancelled.error?.code === 'ERR_CANCELED', 'a cancelled GET stays cancelled');
equal(calls.length, 1, 'a cancelled GET is not retried');

{
  const controller = new AbortController();
  controller.abort();
  reset(['ok']);
  const aborted = await settle(client.get('fixture/read', { signal: controller.signal }));
  check(!aborted.ok && aborted.error?.code === 'ERR_CANCELED', 'an already aborted signal is preserved');
  equal(calls.length, 0, 'an already aborted request never reaches the transport');
}
{
  const controller = new AbortController();
  reset([(config) => { controller.abort(); return outcome('500', config); }, 'ok']);
  const aborted = await settle(client.get('fixture/read', { signal: controller.signal }));
  check(!aborted.ok && aborted.error?.code === 'ERR_CANCELED', 'abort during back-off cancels the retry');
  equal(calls.length, 1, 'no retry is issued after the signal aborted');
}

// Real API helpers: canonical mutations never repeat, reads may.
const inspections = await import('../src/api/anomalyInspections.js');
const agronomy = await import('../src/api/closedLoopAgronomy.js');
for (const [label, run] of [
  ['createAnomalyInspection', () => inspections.createAnomalyInspection({ field_id: 1 }, 'create-inspection-key', undefined)],
  ['saveAnomalyFinding', () => inspections.saveAnomalyFinding(1, { expected_version: 1 })],
  ['cancelAnomalyInspection', () => inspections.cancelAnomalyInspection(1, 1, 'Причина отмены')],
  ['createAgronomyDraft', () => agronomy.createAgronomyDraft(1, 'Основание')],
  ['transitionAgronomyPlan', () => agronomy.transitionAgronomyPlan(1, 'approve', 1, 'Основание')],
  ['addAgronomyWork', () => agronomy.addAgronomyWork(1, { expected_version: 1 })],
  ['transitionAgronomyWork', () => agronomy.transitionAgronomyWork(1, 2, { operation: 'start' })],
  ['reevaluateAgronomyPlan', () => agronomy.reevaluateAgronomyPlan(1, 1, 'Основание')],
]) {
  for (const failure of ['500', 'timeout']) {
    reset([failure, 'ok', 'ok']);
    await settle(run());
    equal(calls.length, 1, `${label} ${failure}: no hidden duplicate mutation`);
  }
}
reset(['500', '500', 'ok']);
check((await settle(clientModule.getAlerts({ is_active: true }))).ok, 'getAlerts recovers through read retries');
equal(calls.length, 3, 'getAlerts retried twice');

// ─── 2. 401 ends the web session and never touches offline data (C7) ──────
function seedSession(token = 'token-A') {
  localStorage.setItem(session.SESSION_TOKEN_KEY, token);
  localStorage.setItem(session.SESSION_USER_KEY, JSON.stringify({ id: 5, role: 'agronomist', enterprise_id: 7, is_active: true }));
}
env.events.length = 0;
env.indexedDbCalls.length = 0;
seedSession('token-A');
reset(['401', 'ok']);
const unauthorized = await settle(client.get('fixture/read'));
check(!unauthorized.ok && unauthorized.error?.response?.status === 401, '401 is surfaced to the caller');
equal(calls.length, 1, '401 is not retried');
equal(calls[0].authorization, 'Bearer token-A', 'the failing request carried the stored credential');
equal(localStorage.getItem(session.SESSION_TOKEN_KEY), null, '401 clears the token');
equal(localStorage.getItem(session.SESSION_USER_KEY), null, '401 clears the cached user');
equal(env.events.filter((event) => event.type === session.SESSION_INVALIDATED_EVENT).map((event) => event.detail?.reason), ['unauthorized'], '401 invalidates the session once');
check(env.events.some((event) => event.type === session.SESSION_ENDED_EVENT), 'in-memory consumers are told the session ended');
equal(env.indexedDbCalls, [], '401 opens and deletes no IndexedDB database');

// A 401 for an older credential does not end a newer session.
env.events.length = 0;
seedSession('token-A');
reset([(config) => { localStorage.setItem(session.SESSION_TOKEN_KEY, 'token-B'); return outcome('401', config); }]);
await settle(client.get('fixture/read'));
equal(localStorage.getItem(session.SESSION_TOKEN_KEY), 'token-B', 'a stale 401 keeps the newer credential');
equal(env.events.filter((event) => event.type === session.SESSION_INVALIDATED_EVENT).length, 0, 'a stale 401 does not end the newer session');

// A rejected login is a login failure, not a session loss.
env.events.length = 0;
seedSession('token-current');
reset(['401']);
const login = await settle(clientModule.loginWithPassword('user@example.com', 'wrong'));
check(!login.ok && login.error?.response?.status === 401, 'a rejected login is reported to the caller');
equal(localStorage.getItem(session.SESSION_TOKEN_KEY), 'token-current', 'a rejected login leaves the current credential');
equal(env.events.length, 0, 'a rejected login dispatches no session event');
equal(env.indexedDbCalls, [], 'a rejected login touches no IndexedDB database');

// ─── 3. field_ids and index_codes serialization (C12) ─────────────────────
const coverage = async (params) => { reset(['ok']); await clientModule.getSatelliteCoverage(params); return calls[0].url; };
equal(await coverage({ field_ids: [123] }), 'satellite-indices/coverage?field_ids=123', 'one field id serializes to field_ids=123');
equal(await coverage({ field_ids: [123, 456] }), 'satellite-indices/coverage?field_ids=123,456', 'field ids serialize comma-separated');
equal(await coverage({ field_ids: '123, 456' }), 'satellite-indices/coverage?field_ids=123,456', 'a comma string is normalized');
equal(await coverage({ field_ids: [123], index_codes: ['NDVI', 'savi', 'evi'] }), 'satellite-indices/coverage?field_ids=123&index_codes=savi,evi', 'index codes serialize comma-separated without NDVI');
equal(await coverage({ index_codes: ['ndvi'] }), 'satellite-indices/coverage?index_codes=savi,evi,ndmi,ndre', 'an NDVI-only request falls back to the supported indices');
equal(await coverage({ include_empty: true, active_only: true }), 'satellite-indices/coverage?include_empty=true&active_only=true', 'no list parameters are invented');
check(!(await coverage({ field_ids: [1, 2] })).includes('[]'), 'axios bracket serialization is never used');
reset(['ok']);
await assert.rejects(() => clientModule.getSatelliteCoverage({ field_ids: [1, 'x'] }), /positive integer/);
equal(calls.length, 0, 'an invalid field id is rejected before any request');

// ─── 4. User-scoped response caches ────────────────────────────────────────
seedSession('token-A');
reset(['ok', 'ok', 'ok', 'ok']);
await clientModule.getEnterprises();
await clientModule.getEnterprises();
equal(calls.length, 1, 'enterprises are cached within one session');
session.invalidateSession({ reason: 'test' });
await clientModule.getEnterprises();
equal(calls.length, 2, 'ending the session drops cached business data');
session.beginSession('token-B', { id: 9, role: 'manager', enterprise_id: 7 }, { id: 5, role: 'agronomist', enterprise_id: 7 });
await clientModule.getEnterprises();
equal(calls.length, 3, 'another identity never receives the previous user\'s cached data');
session.beginSession('token-B2', { id: 9, role: 'manager', enterprise_id: 7 }, { id: 9, role: 'manager', enterprise_id: 7 });
await clientModule.getEnterprises();
equal(calls.length, 3, 'the same identity keeps its cache');
env.indexedDbCalls.length = 0;
session.invalidateSession({ reason: 'test' });
equal(env.indexedDbCalls, [], 'session invalidation never touches IndexedDB');

// ─── 5. No API helper can reach a retired TASK_225 write endpoint ─────────
export const RETIRED_WRITE_PATTERNS = [
  /^(POST|PUT|PATCH|DELETE) field-inspections(\/|\?|$)/,
  /^[A-Z]+ operational-actions(\/|\?|$)/,
  /^[A-Z]+ verification-requests(\/|\?|$)/,
  /^POST anomaly-inspections\/[^/]+\/actions(\/|\?|$)/,
  /^POST anomaly-inspections\/actions\//,
  /^POST ndvi\/[^/]+\/refresh(\/|\?|$)/,
];
const apiDirectory = path.join(root, 'src', 'api');
const probeFile = new File(['probe'], 'probe.png', { type: 'image/png' });
const argumentSets = [[1, 1, 1, 1, 1], [1, 1, probeFile, '2026-09-24T12:00:00+05:00'], [{}, {}, {}, {}], [1, 2, { operation: 'start' }]];
const exercised = [];
for (const filename of readdirSync(apiDirectory).filter((name) => name.endsWith('.js')).sort()) {
  const module = await import(pathToFileURL(path.join(apiDirectory, filename)).href);
  for (const [name, value] of Object.entries(module)) {
    if (typeof value !== 'function' || name === 'default') continue;
    for (const args of argumentSets) {
      reset(['ok', 'ok', 'ok', 'ok']);
      try {
        const result = value(...args);
        if (result && typeof result.then === 'function') await settle(result);
      } catch {
        // Helpers that validate their input may reject a probe shape; others are tried.
      }
      for (const call of calls) exercised.push({ module: filename, helper: name, request: `${call.method} ${call.url.split('?')[0]}` });
    }
  }
}
const requests = Array.from(new Set(exercised.map((item) => item.request)));
check(requests.length >= 60, `the API helper scan exercised the API layer (${requests.length} distinct requests)`);
for (const pattern of RETIRED_WRITE_PATTERNS) {
  const hits = exercised.filter((item) => pattern.test(item.request));
  equal(hits, [], `no API helper issues ${pattern}`);
}
check(exercised.some((item) => item.request.startsWith('POST anomaly-inspections') && item.helper === 'createAnomalyInspection'), 'canonical inspection creation is reachable');
check(exercised.some((item) => item.request === 'POST agronomy-plans' && item.helper === 'createAgronomyDraft'), 'canonical remediation is reachable');
equal(exercised.filter((item) => item.request.startsWith('GET field-inspections')), [], 'the retired legacy inspection API module is gone');

globalThis.setTimeout = realSetTimeout;
console.log(JSON.stringify({
  status: 'PASS',
  suite: 'TASK_226 HTTP contract (retry, 401, serialization, caches, retired writes)',
  assertions,
  distinct_requests_scanned: requests.length,
}));
