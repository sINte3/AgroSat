import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const cdpPort = Number(process.env.TASK209_CDP_PORT || 45226);
const baseUrl = process.env.TASK209_BASE_URL || 'http://127.0.0.1:45225';
const evidenceDir = process.env.TASK209_EVIDENCE_DIR || '';
const consoleErrors = [];
const failedRequests = [];
const apiRequests = [];
const requestUrls = new Map();
let writesAttempted = 0;
let tileRequests = 0;
let rasterImageRequests = 0;
let tileAuthorizationPresent = false;
let externalAuthorizationLeaks = 0;
let expectedTileCancellations = 0;

const tinyPng = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X8c4WQAAAABJRU5ErkJggg==';
const field = {
  id: 11,
  name: 'Synthetic Field',
  code: 'SYN-11',
  enterprise_id: 5,
  enterprise_name: 'Synthetic Enterprise',
  area_ha: 42.5,
  centroid_lat: 39.77,
  centroid_lon: 64.42,
  irrigation_type: 'drip',
  current_crop: 'Хлопок',
  last_ndvi: 0.58,
  last_ndvi_date: '2026-07-25',
  ndvi_change_pct: 1.5,
  active_alerts: 0,
  alert_severity: 'ok',
};
const coverage = {
  field_id: 11,
  has_any_data: true,
  coverage_status: 'complete',
  freshness_status: 'fresh',
  latest_captured_date: '2026-07-25',
  indices: Object.fromEntries(
    ['savi', 'evi', 'ndmi', 'ndre'].map((code, index) => [
      code,
      {
        has_data: true,
        record_count: 2,
        latest_mean_value: 0.48 - index * 0.05,
        latest_captured_date: '2026-07-25',
      },
    ]),
  ),
};

function jsonFixture(pathname, searchParams) {
  if (pathname === '/api/auth/me') {
    return {
      id: 7,
      full_name: 'Synthetic Manager',
      display_name: 'Synthetic Manager',
      role: 'manager',
      enterprise_id: 5,
      is_active: true,
    };
  }
  if (pathname === '/api/enterprises/') {
    return [{ id: 5, name: 'Synthetic Enterprise' }];
  }
  if (pathname === '/api/fields/') return [field];
  if (pathname === '/api/fields/11') {
    return {
      type: 'Feature',
      geometry: null,
      properties: field,
    };
  }
  if (pathname === '/api/alerts/') return [];
  if (pathname === '/api/weather/field/11') return null;
  if (pathname === '/api/ndvi/11/latest') return null;
  if (pathname === '/api/ndvi/11/history') return [];
  if (pathname === '/api/satellite-indices/11/latest') {
    return { record: null, index_code: searchParams.get('index_code') };
  }
  if (pathname === '/api/satellite-indices/coverage') {
    return {
      filters: {},
      summary: {
        fields_total: 1,
        fields_with_any_data: 1,
        fields_without_data: 0,
        fields_with_all_requested_indices: 1,
        fields_with_partial_indices: 0,
        fields_stale: 0,
        index_summary: {},
        latest_captured_date: '2026-07-25',
        record_count_total: 8,
      },
      fields: [coverage],
    };
  }
  if (pathname === '/api/field-tiles/metadata') {
    return {
      schema_version: 'task209_field_mvt_v1',
      source_layer: 'fields',
      min_zoom: 3,
      max_zoom: 18,
      extent: 4096,
      buffer: 64,
      scope: { role: 'manager', enterprise_id: 5 },
      field_count: 1,
      bounds: [63, 39, 66, 41],
      properties: Object.keys(field),
      tile_template: '/api/field-tiles/{z}/{x}/{y}.mvt?enterprise_id=5',
    };
  }
  if (pathname === '/api/raster/fields/11/metadata') {
    const observationDate = searchParams.get('date_to') || '2026-07-25';
    return {
      schema_version: 'task209_raster_provider_v1',
      field_id: 11,
      enterprise_id: 5,
      index_code: 'ndvi',
      observation_date: observationDate,
      bbox: [64.39, 39.74, 64.45, 39.8],
      default_size: 512,
      allowed_sizes: [256, 512, 768, 1024],
      legend: [],
      limitations: ['Synthetic browser fixture.'],
      quality: {
        accepted_observation: true,
        mask: 'Deterministic fixture mask',
      },
      provenance: {
        provider: 'sentinel_process',
        satellite: 'Fixture satellite',
        observation_id: 91,
        processing_version: 'fixture-v1',
        request_timeout_class: 'bounded_httpx_provider_timeout',
      },
      image_template: `/api/raster/fields/11/image?index_code=ndvi&observation_date=${observationDate}&size={size}`,
    };
  }
  return null;
}

function hasAuthorization(headers) {
  return Object.keys(headers || {}).some((name) => name.toLowerCase() === 'authorization');
}

async function openTarget() {
  const response = await fetch(`http://127.0.0.1:${cdpPort}/json/new?about:blank`, { method: 'PUT' });
  assert.equal(response.ok, true, `CDP target creation failed: ${response.status}`);
  return response.json();
}

const target = await openTarget();
let targetClosed = false;
async function closeTarget() {
  if (targetClosed) return;
  targetClosed = true;
  try {
    await fetch(`http://127.0.0.1:${cdpPort}/json/close/${target.id}`);
  } catch (_) {}
}
process.once('uncaughtException', (error) => {
  console.error(error);
  closeTarget().finally(() => {
    process.exit(1);
  });
});
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

async function fulfillRequest(params) {
  try {
    await send('Fetch.fulfillRequest', params);
  } catch (error) {
    if (!String(error?.message).includes('Invalid InterceptionId')) throw error;
  }
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
  if (payload.method === 'Network.requestWillBeSent') {
    requestUrls.set(payload.params.requestId, payload.params.request.url);
  }
  if (payload.method === 'Network.loadingFinished') {
    requestUrls.delete(payload.params.requestId);
  }
  if (payload.method === 'Network.loadingFailed') {
    const requestUrl = requestUrls.get(payload.params.requestId) || '';
    requestUrls.delete(payload.params.requestId);
    const tileRequest = requestUrl.includes('/api/field-tiles/')
      || requestUrl.includes('tile.openstreetmap.org')
      || requestUrl.includes('arcgisonline.com');
    if (
      payload.params.canceled
      || (tileRequest && ['net::ERR_ABORTED', 'net::ERR_FAILED'].includes(payload.params.errorText))
    ) {
      expectedTileCancellations += 1;
    } else {
      failedRequests.push(payload.params.errorText);
    }
  }
  if (payload.method !== 'Fetch.requestPaused') return;

  const { requestId, request } = payload.params;
  const parsed = new URL(request.url);
  const isApplicationApi = parsed.origin === baseUrl && parsed.pathname.startsWith('/api/');
  if (!isApplicationApi) {
    if (hasAuthorization(request.headers)) externalAuthorizationLeaks += 1;
    await fulfillRequest({
      requestId,
      responseCode: 200,
      responseHeaders: [
        { name: 'Content-Type', value: 'image/png' },
        { name: 'Access-Control-Allow-Origin', value: '*' },
        { name: 'Cross-Origin-Resource-Policy', value: 'cross-origin' },
      ],
      body: tinyPng,
    });
    return;
  }

  apiRequests.push(`${request.method} ${parsed.pathname}`);
  if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) writesAttempted += 1;
  if (/^\/api\/field-tiles\/\d+\/\d+\/\d+\.mvt$/.test(parsed.pathname)) {
    tileRequests += 1;
    if (hasAuthorization(request.headers)) tileAuthorizationPresent = true;
    await fulfillRequest({
      requestId,
      responseCode: 200,
      responseHeaders: [
        { name: 'Content-Type', value: 'application/vnd.mapbox-vector-tile' },
        { name: 'Cache-Control', value: 'private, max-age=300' },
      ],
      body: '',
    });
    return;
  }
  if (parsed.pathname === '/api/raster/fields/11/image') {
    rasterImageRequests += 1;
    await fulfillRequest({
      requestId,
      responseCode: 200,
      responseHeaders: [
        { name: 'Content-Type', value: 'image/png' },
        { name: 'X-AgroSat-Raster-Cache', value: 'BYPASS' },
        { name: 'X-AgroSat-Raster-Provider', value: 'sentinel_process' },
      ],
      body: tinyPng,
    });
    return;
  }
  const body = jsonFixture(parsed.pathname, parsed.searchParams);
  await fulfillRequest({
    requestId,
    responseCode: body === null ? 404 : 200,
    responseHeaders: [
      { name: 'Content-Type', value: 'application/json; charset=utf-8' },
      { name: 'Cache-Control', value: 'no-store' },
    ],
    body: Buffer.from(JSON.stringify(body ?? { detail: 'Fixture route not found' })).toString('base64'),
  });
});

await Promise.all([
  send('Page.enable'),
  send('Runtime.enable'),
  send('Network.enable'),
  send('Fetch.enable', {
    patterns: [
      { urlPattern: `${baseUrl}/api/*`, requestStage: 'Request' },
      { urlPattern: 'https://*', requestStage: 'Request' },
    ],
  }),
]);
await send('Page.addScriptToEvaluateOnNewDocument', {
  source: `
    localStorage.setItem('agrosat_token','fixture');
    window.__task209ObjectUrls = { created: 0, revoked: 0 };
    const rasterObjectUrls = new Set();
    const createObjectURL = URL.createObjectURL.bind(URL);
    const revokeObjectURL = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (value) => {
      const url = createObjectURL(value);
      if (value instanceof Blob && /^image\\/png(?:;|$)/i.test(value.type)) {
        window.__task209ObjectUrls.created += 1;
        rasterObjectUrls.add(url);
      }
      return url;
    };
    URL.revokeObjectURL = (value) => {
      if (rasterObjectUrls.delete(value)) {
        window.__task209ObjectUrls.revoked += 1;
      }
      return revokeObjectURL(value);
    };
  `,
});

async function evaluate(expression) {
  const response = await send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.exception?.description || 'Runtime evaluation failed');
  }
  return response.result.value;
}

async function waitUntil(expression, timeoutMs = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await evaluate(expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for: ${expression}`);
}

const load = waitForEvent('Page.loadEventFired');
await send('Page.navigate', { url: `${baseUrl}/fields` });
await load;
try {
  await waitUntil(`document.querySelector('[aria-label="Интерактивная карта полей"]')?.dataset.spatialStatus === 'ready'`);
} catch (error) {
  const diagnostic = await evaluate(`({
    pathname: location.pathname,
    status: document.querySelector('[aria-label="Интерактивная карта полей"]')?.dataset.spatialStatus || null,
    body: document.body.innerText.slice(0, 500),
  })`);
  throw new Error(
    `${error.message}; diagnostic=${JSON.stringify(diagnostic)}; `
    + `api=${JSON.stringify(apiRequests)}; console=${JSON.stringify(consoleErrors)}`,
  );
}
for (let index = 0; index < 50 && tileRequests === 0; index += 1) {
  await new Promise((resolve) => setTimeout(resolve, 100));
}

const initialMapContract = await evaluate(`(() => {
  const node = document.querySelector('[aria-label="Интерактивная карта полей"]');
  return {
    sourceType: node?.dataset.fieldSourceType,
    fieldLayerCount: Number(node?.dataset.fieldLayerCount || 0),
    fullGeometryResources: performance.getEntriesByType('resource')
      .filter((entry) => entry.name.includes('/api/fields/geojson/all')).length,
  };
})()`);
assert.equal(initialMapContract.sourceType, 'vector');
assert.equal(initialMapContract.fieldLayerCount, 3);
assert.equal(initialMapContract.fullGeometryResources, 0);
assert.ok(
  tileRequests > 0,
  `MapLibre did not request an MVT tile; API requests: ${JSON.stringify(apiRequests)}`,
);
assert.ok(tileRequests <= 64, `Initial MVT request count is unbounded: ${tileRequests}`);
assert.equal(tileAuthorizationPresent, true);
assert.equal(externalAuthorizationLeaks, 0);

await evaluate(`(() => {
  const label = [...document.querySelectorAll('span')]
    .find((node) => node.textContent.trim() === 'Synthetic Field');
  const row = label?.closest('.cursor-pointer');
  row?.click();
  return Boolean(row);
})()`);
await waitUntil(`Boolean(document.querySelector('section input[type="checkbox"]'))`);
await evaluate(`document.querySelector('section input[type="checkbox"]').click()`);
await waitUntil(`document.body.innerText.includes('Фактическая дата снимка:')`);
assert.equal(rasterImageRequests, 1);

await evaluate(`(() => {
  const checkbox = document.querySelector('section input[type="checkbox"]');
  checkbox?.click();
  return Boolean(checkbox);
})()`);
try {
  await waitUntil(`
    document.querySelector('section input[type="checkbox"]')?.checked === false
    && window.__task209ObjectUrls.created === window.__task209ObjectUrls.revoked
  `);
} catch (error) {
  const diagnostic = await evaluate(`({
    checked: document.querySelector('section input[type="checkbox"]')?.checked,
    objectUrls: window.__task209ObjectUrls,
    rasterTextPresent: document.body.innerText.includes('Фактическая дата снимка:'),
  })`);
  throw new Error(`${error.message}; raster=${JSON.stringify(diagnostic)}`);
}
const rasterLifecycle = await evaluate('window.__task209ObjectUrls');
assert.equal(rasterLifecycle.created, 1);
await evaluate(`document.querySelector('[title="Назад к списку"]')?.click()`);
await waitUntil(`!document.querySelector('[title="Назад к списку"]')`);

const viewports = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'tablet', width: 1024, height: 768 },
  { name: 'mobile', width: 390, height: 844 },
];
const viewportEvidence = [];
if (evidenceDir) await mkdir(evidenceDir, { recursive: true });
for (const viewport of viewports) {
  await send('Emulation.setDeviceMetricsOverride', {
    width: viewport.width,
    height: viewport.height,
    deviceScaleFactor: 1,
    mobile: viewport.width < 600,
  });
  await new Promise((resolve) => setTimeout(resolve, 150));
  const metrics = await evaluate(`({
    width: innerWidth,
    height: innerHeight,
    scrollWidth: document.documentElement.scrollWidth,
    overflow: document.documentElement.scrollWidth > innerWidth,
    status: document.querySelector('[aria-label="Интерактивная карта полей"]')?.dataset.spatialStatus,
    mapWidth: Math.round(document.querySelector('[aria-label="Интерактивная карта полей"]')?.getBoundingClientRect().width || 0),
    fieldListCollapsed: Boolean(document.querySelector('[aria-label="Открыть список полей"]')),
  })`);
  assert.equal(metrics.overflow, false, `${viewport.name} has horizontal overflow`);
  assert.equal(metrics.status, 'ready');
  if (viewport.name === 'mobile') {
    assert.equal(metrics.fieldListCollapsed, true);
    assert.ok(metrics.mapWidth >= 360, `Mobile map width is too small: ${metrics.mapWidth}`);
  }
  viewportEvidence.push({ ...viewport, ...metrics });
  if (evidenceDir) {
    const screenshot = await send('Page.captureScreenshot', {
      format: 'png',
      fromSurface: true,
    });
    await writeFile(join(evidenceDir, `vector-map-${viewport.name}.png`), Buffer.from(screenshot.data, 'base64'));
  }
}

assert.equal(writesAttempted, 0);
assert.equal(apiRequests.some((request) => request.includes('/api/fields/geojson/all')), false);
assert.deepEqual(consoleErrors, []);
assert.deepEqual(failedRequests, []);

const report = {
  decision: 'PASS_CDP_FIXTURE_RUNTIME',
  runtime_adapter: 'Chrome DevTools Protocol fixture harness',
  playwright_mcp_status: 'BLOCKED_B003',
  viewports: viewportEvidence,
  map: {
    source_type: initialMapContract.sourceType,
    field_layer_count: initialMapContract.fieldLayerCount,
    initial_tile_requests: tileRequests,
    expected_tile_cancellations: expectedTileCancellations,
    full_geometry_requests: initialMapContract.fullGeometryResources,
    tile_authorization_present: tileAuthorizationPresent,
    external_authorization_leaks: externalAuthorizationLeaks,
  },
  raster: {
    image_requests: rasterImageRequests,
    object_urls_created: 1,
    object_urls_revoked: 1,
  },
  console_errors: consoleErrors.length,
  failed_requests: failedRequests.length,
  unexpected_writes: writesAttempted,
};
if (evidenceDir) {
  await writeFile(join(evidenceDir, 'vector-raster-browser-report.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
}
console.log(`TASK209 vector/raster browser fixture: PASS ${JSON.stringify(report)}`);

await closeTarget();
socket.close();
