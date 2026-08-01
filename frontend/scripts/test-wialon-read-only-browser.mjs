import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';


const cdpPort = Number(process.env.TASK209_CDP_PORT || 46332);
const baseUrl = process.env.TASK209_BASE_URL || 'http://127.0.0.1:46331';
const evidenceDir = process.env.TASK209_EVIDENCE_DIR || '';
const consoleErrors = [];
const failedRequests = [];
const apiRequests = [];
const externalRequests = [];


function fixtureFor(url, method) {
  const parsed = new URL(url);
  apiRequests.push({ method, path: parsed.pathname });
  if (method !== 'GET') {
    return { status: 405, body: { detail: 'Fixture writes are disabled' } };
  }
  if (parsed.pathname === '/api/auth/me') {
    return {
      status: 200,
      body: {
        id: 20901,
        full_name: 'TASK209 Manager',
        role: 'manager',
        enterprise_id: 901,
        is_active: true,
      },
    };
  }
  if (parsed.pathname === '/api/enterprises/') return { status: 200, body: [] };
  if (parsed.pathname === '/api/fields/1') {
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
  if (parsed.pathname === '/api/alerts/1') return { status: 200, body: [] };
  return { status: 404, body: { detail: 'Fixture route not found' } };
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
    consoleErrors.push(
      payload.params.args.map((item) => item.value || item.description || '').join(' '),
    );
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
  send('Fetch.enable', {
    patterns: [{ urlPattern: `${baseUrl}/api/*`, requestStage: 'Request' }],
  }),
]);
await send('Network.setBypassServiceWorker', { bypass: true });
await send('Network.setCacheDisabled', { cacheDisabled: true });
await send('Page.addScriptToEvaluateOnNewDocument', {
  source: `localStorage.setItem('agrosat_token', ['task209', Date.now(), Math.random()].join('-'));`,
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

let loaded = waitForEvent('Page.loadEventFired');
await send('Page.navigate', { url: `${baseUrl}/fields/1` });
await loaded;
await waitFor(`document.body.innerText.includes('TASK209 Fixture Field')`);

const viewportResults = [];
for (const [width, height] of [[1440, 900], [1024, 768], [390, 844]]) {
  await send('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: width < 600,
  });
  await new Promise((resolve) => setTimeout(resolve, 100));
  const result = await evaluate(`(() => ({
    width: innerWidth,
    height: innerHeight,
    horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
    telematicsTabCount: [...document.querySelectorAll('button')]
      .filter((item) => item.textContent.trim() === 'Техника').length,
    telematicsPanelCount: document.querySelectorAll('[aria-labelledby="field-telematics-title"]').length
  }))()`);
  assert.equal(result.horizontalOverflow, false, `horizontal overflow at ${width}x${height}`);
  assert.equal(result.telematicsTabCount, 0, `Wialon tab exposed at ${width}x${height}`);
  assert.equal(result.telematicsPanelCount, 0, `Wialon panel exposed at ${width}x${height}`);
  viewportResults.push(result);
}

const telematicsRequests = apiRequests.filter((item) => item.path.startsWith('/api/telematics/'));
const providerRequests = externalRequests.filter((url) => /wialon|hst-api|telematics/i.test(url));
assert.deepEqual(telematicsRequests, [], 'disabled UI attempted the telematics API');
assert.deepEqual(providerRequests, [], 'browser attempted a direct Wialon/provider request');
assert.deepEqual(consoleErrors, [], `console errors: ${consoleErrors.join(' | ')}`);
assert.deepEqual(failedRequests, [], `failed requests: ${failedRequests.join(' | ')}`);

const report = {
  decision: 'PASS_WIALON_FIRST_PILOT_DISABLED_BROWSER',
  runtime_adapter: 'Chrome DevTools Protocol fixture harness',
  fixture_mode: true,
  viewports: viewportResults,
  telematics_api_requests: telematicsRequests.length,
  direct_provider_requests: providerRequests.length,
  console_errors: consoleErrors.length,
  failed_network_requests: failedRequests.length,
  wialon_scope: 'deferred_to_next_pilot_integrated_operations',
  wialon_feature_flag: 'disabled',
  live_wialon_requested: false,
};

if (evidenceDir) {
  await mkdir(evidenceDir, { recursive: true });
  const screenshot = await send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
  });
  await writeFile(
    join(evidenceDir, 'wialon-disabled-390x844.png'),
    Buffer.from(screenshot.data, 'base64'),
  );
  await writeFile(
    join(evidenceDir, 'browser-disabled-contract.json'),
    `${JSON.stringify(report, null, 2)}\n`,
    'utf8',
  );
}

console.log(`TASK209 Wialon first-pilot disabled browser contract: PASS ${JSON.stringify(report)}`);
await send('Page.close');
socket.close();
