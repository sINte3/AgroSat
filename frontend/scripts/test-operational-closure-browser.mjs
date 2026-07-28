import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';

const cdpPort = Number(process.env.TASK209_CDP_PORT || 45220);
const baseUrl = process.env.TASK209_BASE_URL || 'http://127.0.0.1:45219';
const evidenceDir = process.env.TASK209_EVIDENCE_DIR || '';
let activeRole = 'manager';
const consoleErrors = [];
const failedRequests = [];

const inspection = {
  id: 101,
  field: { id: 11, name: 'Synthetic Field', enterprise_id: 5, enterprise_name: 'Synthetic Enterprise' },
  created_by: { id: 7, display_name: 'Synthetic Manager' },
  assigned_to: { id: 8, display_name: 'Synthetic Agronomist' },
  source: 'attention_queue',
  source_priority: 'high',
  source_attention_score: 73.2,
  source_observation_date: '2026-07-20',
  source_reason_codes: ['ndvi_drop'],
  title: 'Inspect synthetic field',
  instructions: 'Check the bounded fixture zone.',
  due_date: '2026-07-30',
  status: 'completed',
  is_overdue: false,
  version: 4,
  created_at: '2026-07-21T08:00:00+05:00',
  updated_at: '2026-07-22T10:00:00+05:00',
  started_at: '2026-07-21T09:00:00+05:00',
  completed_at: '2026-07-22T10:00:00+05:00',
  cancelled_at: null,
  completion_summary: 'irrigation',
  cancellation_reason: null,
};

const closure = {
  inspection: { id: 101, field_id: 11, enterprise_id: 5, assigned_to_id: 8, status: 'completed', version: 4 },
  result: {
    id: 201,
    inspection_id: 101,
    cause_code: 'irrigation',
    cause_details: 'Synthetic fixture cause',
    evidence_note: 'Synthetic evidence note',
    latitude: 39.77,
    longitude: 64.42,
    version: 1,
    created_at: '2026-07-22T10:00:00+05:00',
  },
  evidence: [{
    id: 301,
    evidence_type: 'geolocation',
    provider: 'metadata_only',
    latitude: 39.77,
    longitude: 64.42,
    created_at: '2026-07-22T10:01:00+05:00',
  }],
  actions: [{
    id: 401,
    inspection_id: 101,
    result_id: 201,
    owner: { id: 8, display_name: 'Synthetic Agronomist' },
    description: 'Inspect irrigation line and document the outcome.',
    due_date: '2026-07-31',
    status: 'closed',
    is_overdue: false,
    closure_reason: 'Synthetic corrective action completed.',
    reopen_reason: null,
    version: 3,
    latest_verification: {
      id: 501,
      status: 'awaiting_observation',
      result: null,
      confidence: null,
      version: 1,
    },
  }],
  evidence_limit: 100,
};

const timeline = {
  inspection_id: 101,
  limit: 100,
  offset: 0,
  items: [
    { id: 0, occurred_at: '2026-07-21T08:00:00+05:00', event_type: 'inspection_created', actor_id: 7 },
    { id: 1, occurred_at: '2026-07-22T10:00:00+05:00', event_type: 'inspection_result_recorded', actor_id: 8 },
    { id: 2, occurred_at: '2026-07-23T11:00:00+05:00', event_type: 'verification_requested', actor_id: 7 },
  ],
};

function user() {
  return {
    id: activeRole === 'viewer' ? 9 : 7,
    display_name: activeRole === 'viewer' ? 'Synthetic Viewer' : 'Synthetic Manager',
    full_name: activeRole === 'viewer' ? 'Synthetic Viewer' : 'Synthetic Manager',
    role: activeRole,
    enterprise_id: 5,
    is_active: true,
  };
}

function fixtureFor(url, method) {
  const parsed = new URL(url);
  if (method !== 'GET') return { status: 405, body: { detail: 'Fixture writes are disabled' } };
  if (parsed.pathname === '/api/auth/me') return { status: 200, body: user() };
  if (parsed.pathname === '/api/enterprises/') return { status: 200, body: [{ id: 5, name: 'Synthetic Enterprise' }] };
  if (parsed.pathname === '/api/field-inspections') {
    return {
      status: 200,
      body: {
        items: [inspection],
        summary: { total: 1, pending: 0, in_progress: 0, completed: 1, cancelled: 0, overdue: 0 },
        limit: 50,
        offset: 0,
      },
    };
  }
  if (parsed.pathname === '/api/field-inspections/101') return { status: 200, body: inspection };
  if (parsed.pathname === '/api/field-inspections/101/closure') return { status: 200, body: closure };
  if (parsed.pathname === '/api/field-inspections/101/timeline') return { status: 200, body: timeline };
  return { status: 404, body: { detail: 'Fixture route not found' } };
}

async function openTarget() {
  const response = await fetch(`http://127.0.0.1:${cdpPort}/json/new?about:blank`, { method: 'PUT' });
  assert.equal(response.ok, true, `CDP target creation failed: ${response.status}`);
  return response.json();
}

const target = await openTarget();
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true });
  socket.addEventListener('error', reject, { once: true });
});

let sequence = 0;
const pending = new Map();
const eventWaiters = new Map();

function send(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

function waitForEvent(name, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      eventWaiters.delete(name);
      reject(new Error(`Timed out waiting for ${name}`));
    }, timeoutMs);
    eventWaiters.set(name, (params) => {
      clearTimeout(timeout);
      eventWaiters.delete(name);
      resolve(params);
    });
  });
}

socket.addEventListener('message', async (message) => {
  const payload = JSON.parse(message.data);
  if (payload.id) {
    const waiter = pending.get(payload.id);
    if (!waiter) return;
    pending.delete(payload.id);
    if (payload.error) waiter.reject(new Error(payload.error.message));
    else waiter.resolve(payload.result);
    return;
  }
  eventWaiters.get(payload.method)?.(payload.params);
  if (payload.method === 'Runtime.consoleAPICalled' && payload.params.type === 'error') {
    consoleErrors.push(payload.params.args.map((item) => item.value || item.description || '').join(' '));
  }
  if (payload.method === 'Network.loadingFailed' && !payload.params.canceled) {
    failedRequests.push(payload.params.errorText);
  }
  if (payload.method === 'Fetch.requestPaused') {
    const { requestId, request } = payload.params;
    const fixture = fixtureFor(request.url, request.method);
    await send('Fetch.fulfillRequest', {
      requestId,
      responseCode: fixture.status,
      responseHeaders: [
        { name: 'Content-Type', value: 'application/json; charset=utf-8' },
        { name: 'Cache-Control', value: 'no-store' },
      ],
      body: Buffer.from(JSON.stringify(fixture.body)).toString('base64'),
    });
  }
});

await Promise.all([
  send('Page.enable'),
  send('Runtime.enable'),
  send('Network.enable'),
  send('Fetch.enable', { patterns: [{ urlPattern: `${baseUrl}/api/*`, requestStage: 'Request' }] }),
]);
await send('Page.addScriptToEvaluateOnNewDocument', {
  source: `localStorage.setItem('agrosat_token','synthetic-runtime-token');`,
});

async function evaluate(expression) {
  const result = await send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
}

async function waitFor(expression, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for DOM condition: ${expression}`);
}

async function navigate() {
  const loaded = waitForEvent('Page.loadEventFired');
  await send('Page.navigate', { url: `${baseUrl}/inspections/101` });
  await loaded;
  await waitFor(`document.body.innerText.includes('Операционный цикл') && document.body.innerText.includes('Synthetic corrective action completed')`);
}

await navigate();

const viewportResults = [];
for (const [width, height] of [[1920, 1080], [1440, 900], [1024, 768], [390, 844]]) {
  await send('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: width < 600,
  });
  await new Promise((resolve) => setTimeout(resolve, 100));
  const metrics = await evaluate(`({
    width: innerWidth,
    height: innerHeight,
    horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
    closureVisible: document.body.innerText.includes('Операционный цикл'),
    limitationVisible: document.body.innerText.includes('не доказательство агрономической причинности')
  })`);
  assert.equal(metrics.horizontalOverflow, false, `horizontal overflow at ${width}x${height}`);
  assert.equal(metrics.closureVisible, true, `closure panel hidden at ${width}x${height}`);
  assert.equal(metrics.limitationVisible, true, `causality limitation hidden at ${width}x${height}`);
  viewportResults.push(metrics);
}

await send('Emulation.setDeviceMetricsOverride', {
  width: 390,
  height: 844,
  deviceScaleFactor: 1,
  mobile: true,
});
const clicked = await evaluate(`(() => {
  const button = [...document.querySelectorAll('button')].find((item) => item.textContent.trim() === 'Проверить наблюдение');
  if (!button) return false;
  button.click();
  return true;
})()`);
assert.equal(clicked, true, 'manager verification action is unavailable');
await waitFor(`document.querySelector('[role="dialog"][aria-labelledby="workflow-title"]') !== null`);
const modalAudit = await evaluate(`(() => {
  const dialog = document.querySelector('[role="dialog"][aria-labelledby="workflow-title"]');
  const title = document.getElementById('workflow-title');
  return {
    labelled: Boolean(dialog && title && dialog.getAttribute('aria-labelledby') === title.id),
    focusInside: Boolean(dialog?.contains(document.activeElement)),
    cancelTarget: [...dialog.querySelectorAll('button')].some((item) => item.textContent.trim() === 'Отмена'),
    text: title?.textContent || ''
  };
})()`);
assert.deepEqual(modalAudit, {
  labelled: true,
  focusInside: true,
  cancelTarget: true,
  text: 'Проверить по новому наблюдению',
});
await evaluate(`([...document.querySelectorAll('[role="dialog"] button')].find((item) => item.textContent.trim() === 'Отмена'))?.click()`);
await waitFor(`document.getElementById('workflow-title') === null`);

activeRole = 'viewer';
await navigate();
const viewerAudit = await evaluate(`({
  readOnlyVisible: document.body.innerText.includes('Только чтение'),
  createActionHidden: ![...document.querySelectorAll('button')].some((item) => item.textContent.trim() === 'Создать действие'),
  mutateActionsHidden: ![...document.querySelectorAll('button')].some((item) => ['Изменить','Закрыть','Переоткрыть','Запросить проверку','Проверить наблюдение'].includes(item.textContent.trim()))
})`);
assert.deepEqual(viewerAudit, {
  readOnlyVisible: true,
  createActionHidden: true,
  mutateActionsHidden: true,
});

assert.deepEqual(consoleErrors, [], `console errors: ${consoleErrors.join(' | ')}`);
assert.deepEqual(failedRequests, [], `failed network requests: ${failedRequests.join(' | ')}`);

const report = {
  decision: 'PASS',
  fixture_mode: true,
  writes_attempted: 0,
  viewports: viewportResults,
  manager_modal: modalAudit,
  viewer: viewerAudit,
  console_errors: consoleErrors.length,
  failed_network_requests: failedRequests.length,
};

if (evidenceDir) {
  await mkdir(evidenceDir, { recursive: true });
  const screenshot = await send('Page.captureScreenshot', { format: 'png', fromSurface: true });
  await writeFile(join(evidenceDir, 'closure-viewer-mobile.png'), Buffer.from(screenshot.data, 'base64'));
  await writeFile(join(evidenceDir, 'browser-contract.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
}

console.log(`TASK209 operational closure browser contract: PASS ${JSON.stringify(report)}`);
await send('Page.close');
socket.close();
