import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const cdpPort = Number(process.env.TASK209_CDP_PORT || 45224);
const baseUrl = process.env.TASK209_BASE_URL || 'http://127.0.0.1:45223';
const evidenceDir = process.env.TASK209_EVIDENCE_DIR || '';
let activeRole = 'manager';
let writesAttempted = 0;
const consoleErrors = [];
const failedRequests = [];
const executiveRequests = [];

const managementSummary = {
  generated_at: '2026-07-28T12:00:00+05:00',
  date_range: { from: '2026-07-01', to: '2026-07-28' },
  summary: {
    total_fields: 10,
    total_hectares: 250,
    fields_with_data: 9,
    fields_without_data: 1,
    avg_ndvi: 0.61,
  },
  enterprises: [{
    id: 5,
    name: 'Synthetic Enterprise',
    field_count: 10,
    total_hectares: 250,
    active_alerts: 2,
    problem_fields: 2,
    fields_no_data: 1,
    avg_ndvi: 0.61,
  }],
  alerts: { active_total: 0, latest_items: [] },
  data_freshness: {
    latest_satellite_index_date: '2026-07-25',
    fields_without_data: 1,
    note: 'Deterministic fixture.',
  },
  limitations: [],
};

const executiveOverview = {
  definitions_version: 'task209_executive_v1',
  generated_at: '2026-07-28T12:00:00+05:00',
  timezone: 'Asia/Tashkent',
  scope: { role: 'manager', enterprise_id: 5 },
  date_range: { from: '2026-06-29', to: '2026-07-28', inclusive: true },
  backlog: {
    attention_fields_now: 2,
    attention_critical: 1,
    attention_high: 1,
    attention_medium: 0,
    open_inspections: 4,
    unassigned_inspections: 2,
    overdue_inspections: 1,
    open_actions: 3,
    overdue_actions: 2,
    awaiting_verification: 1,
  },
  cycle_times: {
    attention_signal_to_inspection_hours: { sample_count: 3, median_hours: 12.25, p90_hours: 24.5 },
    inspection_to_action_hours: { sample_count: 2, median_hours: 4, p90_hours: 7.5 },
    action_to_close_hours: { sample_count: 1, median_hours: 30, p90_hours: 30 },
  },
  verification_outcomes: { improved: 2, unchanged: 1, worsened: 1, insufficient_data: 3 },
  data_quality: {
    attention_stale_fields: 1,
    attention_no_data_fields: 1,
    attention_low_confidence_fields: 2,
    latest_observation_by_index: { ndvi: '2026-07-25', ndmi: '2026-07-24' },
  },
  enterprises: [{
    enterprise_id: 5,
    enterprise_name: 'Synthetic Enterprise',
    attention_fields_now: 2,
    attention_critical: 1,
    attention_high: 1,
    attention_medium: 0,
    open_inspections: 4,
    unassigned_inspections: 2,
    overdue_inspections: 1,
    open_actions: 3,
    overdue_actions: 2,
    awaiting_verification: 1,
    verification_outcomes: { improved: 2, unchanged: 1, worsened: 1, insufficient_data: 3 },
  }],
  owners: [{
    owner_id: 8,
    owner_name: 'Synthetic Agronomist',
    unresolved_actions: 3,
    overdue_actions: 2,
    next_due_date: '2026-07-30',
  }],
  limitations: ['Attention timing is an observation-date proxy.'],
};

function currentUser() {
  return {
    id: activeRole === 'viewer' ? 9 : activeRole === 'admin' ? 1 : 7,
    full_name: `Synthetic ${activeRole}`,
    role: activeRole,
    enterprise_id: activeRole === 'admin' ? null : 5,
    is_active: true,
  };
}

function accountability(parsed) {
  const kind = parsed.searchParams.get('kind') || 'overdue_actions';
  const ownerId = parsed.searchParams.get('owner_id');
  return {
    definitions_version: 'task209_executive_v1',
    generated_at: '2026-07-28T12:00:00+05:00',
    timezone: 'Asia/Tashkent',
    scope: { role: activeRole, enterprise_id: 5 },
    date_range: { from: '2026-06-29', to: '2026-07-28', inclusive: true },
    kind,
    owner_id: ownerId ? Number(ownerId) : null,
    total: 1,
    limit: Number(parsed.searchParams.get('limit') || 25),
    offset: Number(parsed.searchParams.get('offset') || 0),
    items: [{
      id: 401,
      inspection_id: 101,
      action_id: 401,
      field_id: 11,
      field_name: 'Synthetic Field',
      enterprise_id: 5,
      enterprise_name: 'Synthetic Enterprise',
      owner_id: 8,
      owner_name: 'Synthetic Agronomist',
      description: 'Replace irrigation valve',
      due_date: '2026-07-27',
      status: 'in_progress',
      version: 2,
      verification_id: null,
      verification_status: null,
    }],
  };
}

function fixtureFor(url, method) {
  const parsed = new URL(url);
  if (method !== 'GET') {
    writesAttempted += 1;
    return { status: 405, body: { detail: 'Fixture writes are disabled' } };
  }
  if (parsed.pathname === '/api/auth/me') return { status: 200, body: currentUser() };
  if (parsed.pathname === '/api/enterprises/') return { status: 200, body: [{ id: 5, name: 'Synthetic Enterprise' }] };
  if (parsed.pathname === '/api/reports/management/summary') return { status: 200, body: managementSummary };
  if (parsed.pathname === '/api/reports/management/satellite-indices/summary') {
    return {
      status: 200,
      body: {
        date_range: managementSummary.date_range,
        cluster: {},
        enterprises: [],
        data_freshness: { latest_satellite_index_date: '2026-07-25', note: 'Deterministic fixture.' },
        limitations: [],
      },
    };
  }
  if (parsed.pathname === '/api/executive/overview') {
    executiveRequests.push(parsed.href);
    return {
      status: 200,
      body: {
        ...executiveOverview,
        scope: { role: activeRole, enterprise_id: activeRole === 'admin' ? null : 5 },
      },
    };
  }
  if (parsed.pathname === '/api/executive/accountability') {
    executiveRequests.push(parsed.href);
    return { status: 200, body: accountability(parsed) };
  }
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
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
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
  await send('Page.navigate', { url: `${baseUrl}/reports` });
  await loaded;
}

await navigate();
await waitFor(`document.body.innerText.includes('Ответственность и замыкание цикла') && document.body.innerText.includes('Synthetic Agronomist')`);

const viewportResults = [];
for (const [width, height] of [[1440, 900], [1024, 768], [390, 844]]) {
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
    executiveVisible: document.body.innerText.includes('Ответственность и замыкание цикла'),
    qualityVisible: document.body.innerText.includes('Есть ограничения качества данных'),
    liveRegions: document.querySelectorAll('[aria-live="polite"]').length
  })`);
  assert.equal(metrics.horizontalOverflow, false, `horizontal overflow at ${width}x${height}`);
  assert.equal(metrics.executiveVisible, true, `executive overview hidden at ${width}x${height}`);
  assert.equal(metrics.qualityVisible, true, `quality warning hidden at ${width}x${height}`);
  assert.ok(metrics.liveRegions > 0, `live status region missing at ${width}x${height}`);
  viewportResults.push(metrics);
}
const managerScreenshot = await send('Page.captureScreenshot', { format: 'png', fromSurface: true });

const openedOverdue = await evaluate(`(() => {
  const button = [...document.querySelectorAll('button')].find((item) => item.textContent.includes('Просроченные действия'));
  button?.click();
  return Boolean(button);
})()`);
assert.equal(openedOverdue, true, 'overdue action drill-down control is missing');
await waitFor(`document.body.innerText.includes('Replace irrigation valve')`);
assert.ok(executiveRequests.some((url) => new URL(url).searchParams.get('kind') === 'overdue_actions'));

await evaluate(`(() => {
  const close = [...document.querySelectorAll('button')].find((item) => item.textContent.trim() === 'Закрыть очередь');
  close?.click();
})()`);
const openedOwner = await evaluate(`(() => {
  const button = [...document.querySelectorAll('button')].find((item) => item.textContent.includes('3 незакрыто'));
  button?.click();
  return Boolean(button);
})()`);
assert.equal(openedOwner, true, 'owner drill-down control is missing');
await waitFor(`document.body.innerText.includes('Replace irrigation valve')`);
assert.ok(executiveRequests.some((url) => {
  const parsed = new URL(url);
  return parsed.searchParams.get('kind') === 'open_actions' && parsed.searchParams.get('owner_id') === '8';
}));

activeRole = 'admin';
await navigate();
await waitFor(`document.querySelector('select') !== null && document.body.innerText.includes('Ответственность и замыкание цикла')`);
const adminFilter = await evaluate(`(() => {
  const select = [...document.querySelectorAll('select')].find((item) => item.parentElement?.textContent.includes('Предприятие'));
  if (!select) return false;
  select.value = '5';
  select.dispatchEvent(new Event('change', { bubbles: true }));
  select.form?.requestSubmit();
  return true;
})()`);
assert.equal(adminFilter, true, 'admin enterprise filter is missing');
await waitFor(`document.body.innerText.includes('Synthetic Enterprise')`);
await new Promise((resolve) => setTimeout(resolve, 200));
assert.ok(executiveRequests.some((url) => new URL(url).searchParams.get('enterprise_id') === '5'));

activeRole = 'viewer';
const beforeViewerRequests = executiveRequests.length;
await navigate();
await waitFor(`document.body.innerText.includes('Управленческая сводка по состоянию полей')`);
await new Promise((resolve) => setTimeout(resolve, 200));
const viewerAudit = await evaluate(`({
  reportsVisible: document.body.innerText.includes('Управленческая сводка по состоянию полей'),
  executiveHidden: !document.body.innerText.includes('Ответственность и замыкание цикла')
})`);
assert.deepEqual(viewerAudit, { reportsVisible: true, executiveHidden: true });
assert.equal(executiveRequests.length, beforeViewerRequests, 'viewer triggered an executive API request');

assert.equal(writesAttempted, 0, 'browser acceptance attempted a business write');
assert.deepEqual(consoleErrors, [], `console errors: ${consoleErrors.join(' | ')}`);
assert.deepEqual(failedRequests, [], `failed network requests: ${failedRequests.join(' | ')}`);

const report = {
  decision: 'PASS',
  fixture_mode: true,
  roles: ['manager', 'admin', 'viewer'],
  viewports: viewportResults,
  manager_drilldowns: ['overdue_actions', 'open_actions_owner_8'],
  admin_enterprise_filter: true,
  viewer: viewerAudit,
  executive_requests: executiveRequests.length,
  writes_attempted: writesAttempted,
  console_errors: consoleErrors.length,
  failed_network_requests: failedRequests.length,
};

if (evidenceDir) {
  await mkdir(evidenceDir, { recursive: true });
  const screenshot = await send('Page.captureScreenshot', { format: 'png', fromSurface: true });
  await writeFile(join(evidenceDir, 'executive-manager-390x844.png'), Buffer.from(managerScreenshot.data, 'base64'));
  await writeFile(join(evidenceDir, 'executive-viewer-390x844.png'), Buffer.from(screenshot.data, 'base64'));
  await writeFile(join(evidenceDir, 'executive-browser-contract.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
}

console.log(`TASK209 executive accountability browser contract: PASS ${JSON.stringify(report)}`);
await send('Page.close');
socket.close();
