import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';


const cdpPort = Number(process.env.TASK209_CDP_PORT || 46432);
const baseUrl = process.env.TASK209_BASE_URL || 'http://127.0.0.1:46431';
const evidenceDir = process.env.TASK209_EVIDENCE_DIR || '';
const apiRequests = [];
const externalRequests = [];
const consoleErrors = [];
const failedRequests = [];
const events = [];
const fixtureToken = crypto.randomUUID();
let activeInspection = null;
let role = 'manager';


function contextFixture() {
  return {
    generated_at: '2026-07-29T11:00:00Z',
    field: {
      id: 1,
      enterprise_id: 901,
      name: 'TASK209 Fixture Field',
      irrigation_type: 'drip',
    },
    weather: {
      status: 'available',
      provider: 'open_meteo',
      provenance: {
        provider: 'open_meteo',
        fetched_at: '2026-07-29T11:00:00Z',
        provider_observed_at: '2026-07-29T15:00:00+05:00',
        timezone: 'Asia/Tashkent',
      },
      current: {
        temperature: 37,
        humidity: 24,
        wind_speed: 11,
        precipitation: 0,
      },
      forecast: [
        { date: '2026-07-29', temp_min: 25, temp_max: 39, precipitation: 0 },
        { date: '2026-07-30', temp_min: 26, temp_max: 40, precipitation: 0 },
      ],
    },
    events,
    event_limit: 20,
    active_inspection: activeInspection,
    supported_reason_codes: [
      'water_stress_suspicion',
      'weather_water_deficit',
      'irrigation_interruption',
      'irrigation_delivery_check',
      'irrigation_equipment_check',
    ],
    causality_limitation: 'Fixture boundary.',
  };
}


function fixtureFor(url, method, postData) {
  const parsed = new URL(url);
  let body = null;
  try {
    body = postData ? JSON.parse(postData) : null;
  } catch {
    body = null;
  }
  apiRequests.push({ method, path: parsed.pathname, body });
  if (parsed.pathname === '/api/auth/me' && method === 'GET') {
    return {
      status: 200,
      body: {
        id: role === 'viewer' ? 20902 : 20901,
        full_name: `TASK209 ${role}`,
        role,
        enterprise_id: 901,
        is_active: true,
      },
    };
  }
  if (parsed.pathname === '/api/enterprises/' && method === 'GET') {
    return { status: 200, body: [] };
  }
  if (parsed.pathname === '/api/fields/1' && method === 'GET') {
    return {
      status: 200,
      body: {
        type: 'Feature',
        geometry: null,
        properties: {
          id: 1,
          name: 'TASK209 Fixture Field',
          code: 'T209-F1',
          enterprise_id: 901,
          enterprise_name: 'TASK209 Fixture Enterprise',
          area_ha: 42.5,
          irrigation_type: 'drip',
          current_crop: 'cotton',
        },
      },
    };
  }
  if (parsed.pathname === '/api/alerts/1' && method === 'GET') {
    return { status: 200, body: [] };
  }
  if (parsed.pathname === '/api/irrigation-context/fields/1' && method === 'GET') {
    return { status: 200, body: contextFixture() };
  }
  if (parsed.pathname === '/api/irrigation-context/fields/1/events' && method === 'POST') {
    const item = {
      id: events.length + 1,
      enterprise_id: 901,
      field_id: 1,
      inspection_id: body?.inspection_id || null,
      recorded_by: { id: 20901, display_name: `TASK209 ${role}` },
      event_type: body?.event_type,
      occurred_at: body?.occurred_at,
      method_code: body?.method_code,
      water_amount_mm: body?.water_amount_mm,
      evidence_source: body?.evidence_source,
      note: body?.note,
      version: 1,
      created_at: '2026-07-29T11:00:00Z',
      updated_at: '2026-07-29T11:00:00Z',
    };
    events.unshift(item);
    return { status: 201, body: { created: true, event: item } };
  }
  if (parsed.pathname === '/api/field-inspections' && method === 'POST') {
    activeInspection = {
      id: 209,
      status: 'pending',
      source: body?.source,
      source_priority: body?.source_priority,
      source_observation_date: body?.source_observation_date,
      source_reason_codes: body?.source_reason_codes,
    };
    return {
      status: 201,
      body: {
        created: true,
        inspection: {
          ...activeInspection,
          field: { id: 1, name: 'TASK209 Fixture Field', enterprise_id: 901 },
        },
      },
    };
  }
  return {
    status: method === 'GET' ? 404 : 405,
    body: { detail: 'Fixture route not found or write disabled' },
  };
}


async function openTarget() {
  const response = await fetch(`http://127.0.0.1:${cdpPort}/json/new?about:blank`, {
    method: 'PUT',
  });
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
    pending.set(id, {
      resolve,
      reject: (error) => reject(new Error(`${method}: ${error.message}`)),
    });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

function waitForEvent(name, timeoutMs = 10_000) {
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
  if (payload.method === 'Network.requestWillBeSent') {
    const requestUrl = payload.params.request.url;
    if (
      !requestUrl.startsWith(baseUrl)
      && !requestUrl.startsWith('data:')
      && !requestUrl.startsWith('blob:')
    ) {
      externalRequests.push(requestUrl);
    }
  }
  if (payload.method === 'Fetch.requestPaused') {
    const { requestId, request } = payload.params;
    const fixture = fixtureFor(request.url, request.method, request.postData);
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
  send('Fetch.enable', {
    patterns: [{ urlPattern: `${baseUrl}/api/*`, requestStage: 'Request' }],
  }),
]);
await send('Network.setBypassServiceWorker', { bypass: true });
await send('Network.setCacheDisabled', { cacheDisabled: true });
await send('Page.addScriptToEvaluateOnNewDocument', {
  source: `
    (() => {
      localStorage.setItem('agrosat_token', ${JSON.stringify(fixtureToken)});
    })();
  `,
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

async function waitFor(expression, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const text = await evaluate(`document.body?.innerText?.slice(0, 800) || ''`);
  throw new Error(
    `Timed out: ${expression}; body=${JSON.stringify(text)}; `
    + `api=${JSON.stringify(apiRequests.slice(-20))}`,
  );
}

async function navigate(url) {
  const loaded = waitForEvent('Page.loadEventFired');
  await send('Page.navigate', { url });
  await loaded;
}

async function clickText(label) {
  const clicked = await evaluate(`(() => {
    const control = [...document.querySelectorAll('button, a')]
      .find((item) => item.textContent.trim().includes(${JSON.stringify(label)}));
    if (!control) return false;
    control.click();
    return true;
  })()`);
  assert.equal(clicked, true, `Missing control: ${label}`);
}

await navigate(baseUrl);
await evaluate(`
  localStorage.setItem('agrosat_token', ${JSON.stringify(fixtureToken)})
`);
await navigate(`${baseUrl}/fields/1`);
await waitFor(`document.body.innerText.includes('TASK209 Fixture Field')`);
await clickText('Погода');
await waitFor(`document.body.innerText.includes('Погода: наблюдение и прогноз')`);

const viewportResults = [];
for (const [width, height] of [[1920, 1080], [1440, 900], [1024, 768], [390, 844]]) {
  await send('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: width < 600,
  });
  await new Promise((resolve) => setTimeout(resolve, 100));
  const metrics = await evaluate(`(() => {
    const tab = [...document.querySelectorAll('button')]
      .find((item) => item.textContent.trim() === 'Погода');
    const rect = tab?.getBoundingClientRect();
    return {
      width: innerWidth,
      height: innerHeight,
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
      provenanceVisible: document.body.innerText.includes('Open-Meteo'),
      timezoneVisible: document.body.innerText.includes('Asia/Tashkent'),
      caveatVisible: document.body.innerText.includes('не подтверждают агрономическую'),
      eventControlVisible: document.body.innerText.includes('Зафиксировать событие'),
      inspectionControlVisible: document.body.innerText.includes('Создать осмотр'),
      touchTargetHeight: rect ? Math.round(rect.height) : 0
    };
  })()`);
  assert.equal(metrics.horizontalOverflow, false, `horizontal overflow at ${width}x${height}`);
  assert.equal(metrics.provenanceVisible, true);
  assert.equal(metrics.timezoneVisible, true);
  assert.equal(metrics.caveatVisible, true);
  assert.equal(metrics.eventControlVisible, true);
  assert.equal(metrics.inspectionControlVisible, true);
  if (width === 390) assert.ok(metrics.touchTargetHeight >= 44);
  viewportResults.push(metrics);
}

await clickText('Зафиксировать событие');
await waitFor(`document.body.innerText.includes('Наблюдение в поле')`);
await waitFor(`document.body.innerText.includes('Создать осмотр')`);
await clickText('Создать осмотр');
await waitFor(`document.body.innerText.includes('Активный осмотр #209')`);

const managerWrites = apiRequests.filter((item) => item.method !== 'GET');
assert.equal(managerWrites.length, 2);
assert.deepEqual(managerWrites.map((item) => item.path), [
  '/api/irrigation-context/fields/1/events',
  '/api/field-inspections',
]);
assert.equal(managerWrites[0].body.evidence_source, 'human_reported');
assert.match(managerWrites[0].body.occurred_at, /\+05:00$/);
assert.equal(managerWrites[1].body.source, 'irrigation_context');
assert.equal(managerWrites[1].body.source_attention_score, null);
assert.equal(Array.isArray(managerWrites[1].body.source_reason_codes), true);

role = 'viewer';
const writesBeforeViewer = managerWrites.length;
await navigate(`${baseUrl}/fields/1`);
await waitFor(`document.body.innerText.includes('TASK209 Fixture Field')`);
await clickText('Погода');
await waitFor(`document.body.innerText.includes('Только просмотр')`);
const viewerAudit = await evaluate(`(() => ({
  readOnlyVisible: document.body.innerText.includes('Viewer может видеть контекст'),
  writeEventVisible: [...document.querySelectorAll('button')]
    .some((item) => item.textContent.trim() === 'Зафиксировать событие'),
  createInspectionVisible: [...document.querySelectorAll('button')]
    .some((item) => item.textContent.trim() === 'Создать осмотр'),
  horizontalOverflow: document.documentElement.scrollWidth > innerWidth
}))()`);
assert.deepEqual(viewerAudit, {
  readOnlyVisible: true,
  writeEventVisible: false,
  createInspectionVisible: false,
  horizontalOverflow: false,
});
assert.equal(apiRequests.filter((item) => item.method !== 'GET').length, writesBeforeViewer);

const providerRequests = externalRequests.filter((url) => /open-meteo|wialon|sentinel/i.test(url));
assert.deepEqual(providerRequests, []);
assert.deepEqual(consoleErrors, [], `console errors: ${consoleErrors.join(' | ')}`);
assert.deepEqual(failedRequests, [], `failed requests: ${failedRequests.join(' | ')}`);

const report = {
  decision: 'PASS_CDP_FIXTURE_RUNTIME',
  runtime_adapter: 'Chrome DevTools Protocol fixture harness',
  fixture_mode: true,
  roles: ['manager', 'viewer'],
  viewports: viewportResults,
  manager_write_requests: managerWrites.map(({ method, path }) => ({ method, path })),
  viewer_audit: viewerAudit,
  direct_provider_requests: providerRequests.length,
  external_asset_requests: externalRequests.length,
  console_errors: consoleErrors.length,
  failed_network_requests: failedRequests.length,
  unexpected_business_writes: 0,
  live_weather_validated: false,
  isolated_database_validated: false,
  playwright_mcp_validated: false,
};

if (evidenceDir) {
  await mkdir(evidenceDir, { recursive: true });
  const screenshot = await send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
  });
  await writeFile(
    join(evidenceDir, 'weather-irrigation-viewer-390x844.png'),
    Buffer.from(screenshot.data, 'base64'),
  );
  await writeFile(
    join(evidenceDir, 'browser-contract.json'),
    `${JSON.stringify(report, null, 2)}\n`,
    'utf8',
  );
}

console.log(`TASK209 weather irrigation browser contract: PASS ${JSON.stringify(report)}`);
await send('Page.close');
socket.close();
