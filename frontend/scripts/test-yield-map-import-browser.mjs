import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';


const cdpPort = Number(process.env.TASK209_CDP_PORT || 46532);
const baseUrl = process.env.TASK209_BASE_URL || 'http://127.0.0.1:46531';
const evidenceDir = process.env.TASK209_EVIDENCE_DIR || '';
const fixturePath = path.resolve(
  process.env.TASK209_YIELD_FIXTURE
    || '../backend/tests/fixtures/task209_yield_point_csv_v1.csv',
);
const fixtureToken = crypto.randomUUID();
const apiRequests = [];
const consoleErrors = [];
const failedRequests = [];
const externalRequests = [];
const imports = [];
let previewMode = 'rejected';
let role = 'manager';


function importItem() {
  return {
    id: 301,
    enterprise_id: 901,
    field: { id: 1, name: 'TASK209 Fixture Field' },
    season_year: 2026,
    crop_code: 'cotton',
    schema_code: 'yield_point_csv_v1',
    source_filename: 'task209_yield_point_csv_v1.csv',
    source_sha256: 'd'.repeat(64),
    source_provider: 'machine_export',
    input_unit: 't_ha',
    normalized_unit: 't_ha',
    total_rows: 8,
    accepted_rows: 8,
    rejected_rows: 0,
    yield_min_t_ha: 4.1,
    yield_max_t_ha: 4.35,
    yield_mean_t_ha: 4.23625,
    bounds: {},
    provenance: { schema_code: 'yield_point_csv_v1' },
    status: 'accepted',
    created_by: { id: 20901, display_name: 'TASK209 manager' },
    created_at: '2026-07-29T11:30:00Z',
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
    return { status: 200, body: { limit: 50, offset: 0, items: imports } };
  }
  if (parsed.pathname === '/api/yield-map-imports/preview' && method === 'POST') {
    const rejected = previewMode === 'rejected';
    return {
      status: 200,
      body: {
        schema_code: 'yield_point_csv_v1',
        input_unit: body?.yield_unit,
        normalized_unit: 't_ha',
        source_sha256: body?.source_sha256,
        total_rows: 8,
        accepted_rows: rejected ? 7 : 8,
        rejected_rows: rejected ? 1 : 0,
        accepted: [],
        rejected: rejected ? [{
          source_row: 8,
          reason_code: 'outside_field',
          detail: 'Point is outside field',
        }] : [],
        bounds: {},
        summary: {
          yield_min_t_ha: 4.1,
          yield_max_t_ha: 4.35,
          yield_mean_t_ha: 4.23625,
        },
        warnings: rejected ? ['Review rejection report.'] : [],
        preview_fingerprint: 'c'.repeat(64),
      },
    };
  }
  if (parsed.pathname === '/api/yield-map-imports' && method === 'POST') {
    const item = importItem();
    imports.splice(0, imports.length, item);
    return { status: 201, body: { created: true, import: item } };
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
  send('DOM.enable'),
  send('Fetch.enable', {
    patterns: [{ urlPattern: `${baseUrl}/api/*`, requestStage: 'Request' }],
  }),
]);
await send('Network.setBypassServiceWorker', { bypass: true });
await send('Network.setCacheDisabled', { cacheDisabled: true });
await send('Page.addScriptToEvaluateOnNewDocument', {
  source: `localStorage.setItem('agrosat_token', ${JSON.stringify(fixtureToken)});`,
});

async function evaluate(expression, returnByValue = true) {
  const result = await send('Runtime.evaluate', {
    expression,
    returnByValue,
    awaitPromise: true,
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return returnByValue ? result.result.value : result.result;
}

async function waitFor(expression, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const body = await evaluate(`document.body?.innerText?.slice(0, 1000) || ''`);
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

async function attachFixture() {
  const input = await evaluate(
    `document.querySelector('input[type="file"]')`,
    false,
  );
  assert.ok(input.objectId, 'file input is missing');
  await send('DOM.setFileInputFiles', {
    files: [fixturePath],
    objectId: input.objectId,
  });
}

await navigate(baseUrl);
await evaluate(`localStorage.setItem('agrosat_token', ${JSON.stringify(fixtureToken)})`);
await navigate(`${baseUrl}/fields/1`);
await waitFor(`document.body.innerText.includes('TASK209 Fixture Field')`);
await clickText('Урожай');
await waitFor(`document.body.innerText.includes('Импорт карты урожайности')`);

const viewportResults = [];
for (const [width, height] of [[1920, 1080], [1440, 900], [1024, 768], [390, 844]]) {
  await send('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: width < 600,
  });
  await new Promise((resolve) => setTimeout(resolve, 100));
  const result = await evaluate(`(() => {
    const tab = [...document.querySelectorAll('button')]
      .find((item) => item.textContent.trim() === 'Урожай');
    const rect = tab?.getBoundingClientRect();
    return {
      width: innerWidth,
      height: innerHeight,
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
      measuredEvidenceVisible: document.body.innerText.includes('измеренные точки урожайности'),
      schemaVisible: document.body.innerText.includes('yield_point_csv_v1'),
      touchTargetHeight: rect ? Math.round(rect.height) : 0
    };
  })()`);
  assert.equal(result.horizontalOverflow, false);
  assert.equal(result.measuredEvidenceVisible, true);
  assert.equal(result.schemaVisible, true);
  if (width === 390) assert.ok(result.touchTargetHeight >= 44);
  viewportResults.push(result);
}

await attachFixture();
await waitFor(`document.body.innerText.includes('8 строк')`);
await clickText('Проверить файл');
await waitFor(`document.body.innerText.includes('Исправьте исходный файл')`);
await waitFor(`document.body.innerText.includes('Точка вне границы поля')`);
const rejectedAudit = await evaluate(`(() => {
  const button = [...document.querySelectorAll('button')]
    .find((item) => item.textContent.trim().includes('Подтвердить импорт'));
  return {
    rejectedVisible: document.body.innerText.includes('Отклонено'),
    acceptDisabled: Boolean(button?.disabled)
  };
})()`);
assert.deepEqual(rejectedAudit, { rejectedVisible: true, acceptDisabled: true });

previewMode = 'accepted';
await clickText('Проверить файл');
await waitFor(`document.body.innerText.includes('Предпросмотр прошёл')`);
await clickText('Подтвердить импорт');
await waitFor(`document.body.innerText.includes('task209_yield_point_csv_v1.csv')`);
await waitFor(`document.body.innerText.includes('8 точек')`);

const writes = apiRequests.filter((item) => item.method !== 'GET');
assert.deepEqual(writes.map((item) => item.path), [
  '/api/yield-map-imports/preview',
  '/api/yield-map-imports/preview',
  '/api/yield-map-imports',
]);
assert.equal(writes[0].body.rows.length, 8);
assert.equal(writes[0].body.schema_code, 'yield_point_csv_v1');
assert.match(writes[0].body.source_sha256, /^[0-9a-f]{64}$/);
assert.equal(writes[0].body.source_filename, 'task209_yield_point_csv_v1.csv');
assert.equal(writes[2].body.preview_fingerprint, 'c'.repeat(64));
assert.equal(writes[2].body.confirm, true);
assert.equal(JSON.stringify(writes).includes(fixturePath), false);

role = 'viewer';
const writeCountBeforeViewer = writes.length;
await navigate(`${baseUrl}/fields/1`);
await waitFor(`document.body.innerText.includes('TASK209 Fixture Field')`);
await clickText('Урожай');
await waitFor(`document.body.innerText.includes('Только просмотр')`);
const viewerAudit = await evaluate(`(() => ({
  historyVisible: document.body.innerText.includes('8 точек'),
  fileInputCount: document.querySelectorAll('input[type="file"]').length,
  previewButtonCount: [...document.querySelectorAll('button')]
    .filter((item) => item.textContent.includes('Проверить файл')).length,
  horizontalOverflow: document.documentElement.scrollWidth > innerWidth
}))()`);
assert.deepEqual(viewerAudit, {
  historyVisible: true,
  fileInputCount: 0,
  previewButtonCount: 0,
  horizontalOverflow: false,
});
assert.equal(apiRequests.filter((item) => item.method !== 'GET').length, writeCountBeforeViewer);

const providerRequests = externalRequests.filter((url) => /yield|machine|provider/i.test(url));
assert.deepEqual(providerRequests, []);
assert.deepEqual(consoleErrors, []);
assert.deepEqual(failedRequests, []);

const report = {
  decision: 'PASS_CDP_FIXTURE_RUNTIME',
  runtime_adapter: 'Chrome DevTools Protocol fixture harness',
  fixture_mode: true,
  roles: ['manager', 'viewer'],
  journeys: ['preview_reject', 'preview_accept', 'accept', 'viewer_history'],
  viewports: viewportResults,
  api_write_requests: writes.map(({ method, path: requestPath }) => ({
    method,
    path: requestPath,
  })),
  structured_rows: writes[0].body.rows.length,
  rejection_audit: rejectedAudit,
  viewer_audit: viewerAudit,
  local_path_transmitted: false,
  direct_provider_requests: providerRequests.length,
  console_errors: consoleErrors.length,
  failed_network_requests: failedRequests.length,
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
    path.join(evidenceDir, 'yield-import-viewer-390x844.png'),
    Buffer.from(screenshot.data, 'base64'),
  );
  await writeFile(
    path.join(evidenceDir, 'browser-contract.json'),
    `${JSON.stringify(report, null, 2)}\n`,
    'utf8',
  );
}

console.log(`TASK209 yield import browser contract: PASS ${JSON.stringify(report)}`);
await send('Page.close');
socket.close();
