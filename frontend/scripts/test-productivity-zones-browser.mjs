import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';


const cdpPort = Number(process.env.TASK209_CDP_PORT || 46542);
const baseUrl = process.env.TASK209_BASE_URL || 'http://127.0.0.1:46541';
const evidenceDir = process.env.TASK209_EVIDENCE_DIR || '';
const fixtureToken = crypto.randomUUID();
const apiRequests = [];
const consoleErrors = [];
const failedRequests = [];
const externalRequests = [];
let resultMode = 'ready';


function run(status = 'ready') {
  return {
    id: status === 'ready' ? 401 : 402,
    enterprise_id: 901,
    field: { id: 1, name: 'TASK209 Fixture Field' },
    algorithm: 'yield_grid_stability',
    algorithm_version: 'yield_grid_stability_v1',
    run_key: 'a'.repeat(64),
    run_uuid: '11111111-1111-4111-8111-111111111111',
    selected_seasons: status === 'ready' ? [2024, 2025, 2026] : [2025, 2026],
    source_import_ids: status === 'ready' ? [100, 101, 102] : [101, 102],
    source_sha256s: ['1'.repeat(64), '2'.repeat(64), '3'.repeat(64)],
    parameters: { grid_metres: 30 },
    result_status: status,
    reason_codes: status === 'ready' ? [] : ['minimum_seasons_not_met'],
    point_count: status === 'ready' ? 72 : 48,
    eligible_cell_count: status === 'ready' ? 12 : 0,
    field_area_ha: 42.5,
    zoned_area_ha: status === 'ready' ? 1.08 : null,
    area_delta_ha: status === 'ready' ? 41.42 : null,
    confidence: status === 'ready' ? 0.78 : 0,
    provenance: { input_kind: 'accepted_measured_yield' },
    created_at: '2026-07-29T12:00:00Z',
  };
}


function polygon(west, south, east, north) {
  return {
    type: 'MultiPolygon',
    coordinates: [[[
      [west, south],
      [east, south],
      [east, north],
      [west, north],
      [west, south],
    ]]],
  };
}


function zones() {
  return [
    ['low', 0.25, 64.4200, 39.7700, 64.4208, 39.7708],
    ['medium', 0.50, 64.4208, 39.7700, 64.4216, 39.7708],
    ['high', 0.75, 64.4216, 39.7700, 64.4224, 39.7708],
  ].map(([zoneClass, score, west, south, east, north], index) => ({
    id: 500 + index,
    zone_class: zoneClass,
    area_ha: 0.36,
    mean_score: score,
    confidence: 0.78,
    provenance: { algorithm_version: 'yield_grid_stability_v1' },
    geometry: polygon(west, south, east, north),
    created_at: '2026-07-29T12:00:00Z',
  }));
}


function fixtureFor(url, method) {
  const parsed = new URL(url);
  apiRequests.push({ method, path: parsed.pathname });
  if (parsed.pathname === '/api/auth/me' && method === 'GET') {
    return {
      status: 200,
      body: {
        id: 20901,
        full_name: 'TASK209 viewer',
        role: 'viewer',
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
          enterprise_id: 901,
          enterprise_name: 'TASK209 Fixture Enterprise',
          area_ha: 42.5,
          current_crop: 'cotton',
        },
      },
    };
  }
  if (parsed.pathname === '/api/alerts/1' && method === 'GET') {
    return { status: 200, body: [] };
  }
  if (parsed.pathname === '/api/yield-map-imports' && method === 'GET') {
    return { status: 200, body: { limit: 50, offset: 0, items: [] } };
  }
  if (parsed.pathname === '/api/productivity-zones/fields/1' && method === 'GET') {
    return { status: 200, body: { field: { id: 1 }, latest_run: run(resultMode) } };
  }
  if (
    parsed.pathname === '/api/productivity-zones/runs/401/zones'
    && method === 'GET'
  ) {
    return {
      status: 200,
      body: { run: run(), limit: 10, offset: 0, items: zones() },
    };
  }
  return { status: 404, body: { detail: 'Fixture route not found' } };
}


async function openTarget() {
  const response = await fetch(`http://127.0.0.1:${cdpPort}/json/new?about:blank`, {
    method: 'PUT',
  });
  assert.equal(response.ok, true);
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
    ) externalRequests.push(requestUrl);
  }
  if (payload.method === 'Fetch.requestPaused') {
    const { requestId, request } = payload.params;
    const fixture = fixtureFor(request.url, request.method);
    try {
      await send('Fetch.fulfillRequest', {
        requestId,
        responseCode: fixture.status,
        responseHeaders: [
          { name: 'Content-Type', value: 'application/json; charset=utf-8' },
          { name: 'Cache-Control', value: 'no-store' },
        ],
        body: Buffer.from(JSON.stringify(fixture.body)).toString('base64'),
      });
    } catch (error) {
      // A route change may cancel an intercepted request before fulfillment.
      if (!String(error?.message).includes('Invalid InterceptionId')) throw error;
    }
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
  source: `localStorage.setItem('agrosat_token', ${JSON.stringify(fixtureToken)});`,
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
  const body = await evaluate(`document.body?.innerText?.slice(0, 1200) || ''`);
  throw new Error(`Timed out: ${expression}; body=${JSON.stringify(body)}`);
}

async function navigate(url) {
  const loaded = waitForEvent('Page.loadEventFired');
  await send('Page.navigate', { url });
  await loaded;
}

async function clickText(label) {
  const clicked = await evaluate(`(() => {
    const item = [...document.querySelectorAll('button,a')]
      .find((candidate) => candidate.textContent.trim().includes(${JSON.stringify(label)}));
    if (!item) return false;
    item.click();
    return true;
  })()`);
  assert.equal(clicked, true, `Missing control: ${label}`);
}

await navigate(baseUrl);
await evaluate(`localStorage.setItem('agrosat_token', ${JSON.stringify(fixtureToken)})`);
await navigate(`${baseUrl}/fields/1`);
await new Promise((resolve) => setTimeout(resolve, 250));
if (await evaluate(`document.body.innerText.includes('Войти')`)) {
  await evaluate(`localStorage.setItem('agrosat_token', ${JSON.stringify(fixtureToken)})`);
  await navigate(`${baseUrl}/fields/1`);
}
await waitFor(`document.body.innerText.includes('TASK209 Fixture Field')`);
await clickText('Урожай');
await waitFor(`document.body.innerText.includes('Зоны продуктивности')`);
await waitFor(`document.querySelectorAll('.maplibregl-canvas').length === 1`);

const viewportResults = [];
for (const [width, height] of [[1920, 1080], [1440, 900], [1024, 768], [390, 844]]) {
  await send('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: width < 600,
  });
  await new Promise((resolve) => setTimeout(resolve, 120));
  const audit = await evaluate(`(() => {
    const tab = [...document.querySelectorAll('button')]
      .find((item) => item.textContent.trim() === 'Урожай');
    const rect = tab?.getBoundingClientRect();
    return {
      width: innerWidth,
      height: innerHeight,
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
      safetyVisible: document.body.innerText.includes('не агрономическое предписание'),
      seasonsVisible: document.body.innerText.includes('2024, 2025, 2026'),
      confidenceVisible: document.body.innerText.includes('78%'),
      unzonedVisible: document.body.innerText.includes('41.42 га'),
      legendEntries: ['Низкая', 'Средняя', 'Высокая']
        .filter((label) => document.body.innerText.includes(label)).length,
      mapCanvases: document.querySelectorAll('.maplibregl-canvas').length,
      touchTargetHeight: rect ? Math.round(rect.height) : 0
    };
  })()`);
  assert.equal(audit.horizontalOverflow, false);
  assert.equal(audit.safetyVisible, true);
  assert.equal(audit.seasonsVisible, true);
  assert.equal(audit.confidenceVisible, true);
  assert.equal(audit.unzonedVisible, true);
  assert.equal(audit.legendEntries, 3);
  assert.equal(audit.mapCanvases, 1);
  if (width === 390) assert.ok(audit.touchTargetHeight >= 44);
  viewportResults.push(audit);
}

if (evidenceDir) {
  await mkdir(evidenceDir, { recursive: true });
  const readyScreenshot = await send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
  });
  await writeFile(
    path.join(evidenceDir, 'productivity-ready-390x844.png'),
    Buffer.from(readyScreenshot.data, 'base64'),
  );
}

await clickText('Инфо');
await waitFor(`document.querySelectorAll('.maplibregl-canvas').length === 0`);
await clickText('Урожай');
await waitFor(`document.querySelectorAll('.maplibregl-canvas').length === 1`);
const cleanupAudit = await evaluate(`(() => ({
  canvasesAfterRemount: document.querySelectorAll('.maplibregl-canvas').length,
  controlsAfterRemount: document.querySelectorAll('.maplibregl-ctrl').length
}))()`);
assert.ok(cleanupAudit.controlsAfterRemount <= 2);

resultMode = 'insufficient_data';
await clickText('Обновить');
await waitFor(`document.body.innerText.includes('Недостаточно надёжной истории')`);
await waitFor(`document.querySelectorAll('.maplibregl-canvas').length === 0`);
const insufficientAudit = await evaluate(`(() => ({
  reasonVisible: document.body.innerText.includes('не менее трёх принятых сезонов'),
  seasonsVisible: document.body.innerText.includes('2025, 2026'),
  algorithmVisible: document.body.innerText.includes('yield_grid_stability_v1'),
  mapCanvases: document.querySelectorAll('.maplibregl-canvas').length
}))()`);
assert.deepEqual(insufficientAudit, {
  reasonVisible: true,
  seasonsVisible: true,
  algorithmVisible: true,
  mapCanvases: 0,
});

assert.deepEqual(apiRequests.filter((item) => item.method !== 'GET'), []);
const nonFontExternalRequests = externalRequests.filter(
  (url) => !url.startsWith('https://fonts.googleapis.com/')
    && !url.startsWith('https://fonts.gstatic.com/'),
);
assert.deepEqual(nonFontExternalRequests, []);
assert.deepEqual(consoleErrors, []);
assert.deepEqual(failedRequests, []);

const report = {
  decision: 'PASS_CDP_FIXTURE_RUNTIME',
  runtime_adapter: 'Chrome DevTools Protocol fixture harness',
  fixture_mode: true,
  role: 'viewer',
  journeys: ['ready_zones', 'route_cleanup', 'remount', 'insufficient_data'],
  viewports: viewportResults,
  cleanup_audit: cleanupAudit,
  insufficient_audit: insufficientAudit,
  api_write_requests: [],
  external_font_requests: externalRequests.length,
  unexpected_external_requests: nonFontExternalRequests.length,
  console_errors: 0,
  failed_network_requests: 0,
  unexpected_business_writes: 0,
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
    path.join(evidenceDir, 'productivity-insufficient-390x844.png'),
    Buffer.from(screenshot.data, 'base64'),
  );
  await writeFile(
    path.join(evidenceDir, 'browser-contract.json'),
    `${JSON.stringify(report, null, 2)}\n`,
    'utf8',
  );
}

console.log(`TASK209 productivity zone browser contract: PASS ${JSON.stringify(report)}`);
await send('Page.close');
socket.close();
