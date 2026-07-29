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
let telematicsMode = 'unsupported';


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
  if (parsed.pathname === '/api/telematics/fields/1') {
    if (telematicsMode === 'unsupported') {
      return {
        status: 200,
        body: {
          status: 'unsupported',
          provider: 'wialon',
          reason: 'mapping_unavailable',
          units: [],
        },
      };
    }
    if (telematicsMode === 'unavailable') {
      return {
        status: 200,
        body: {
          status: 'unavailable',
          provider: 'wialon',
          reason: 'timeout',
          retryable: true,
          mapping_provenance: 'contract_fixture',
          units: [],
        },
      };
    }
    const stale = telematicsMode === 'stale';
    return {
      status: 200,
      body: {
        status: stale ? 'stale' : 'available',
        provider: 'wialon',
        mapping_provenance: 'contract_fixture',
        requested_range: {
          started_at: '2026-07-28T10:30:00+00:00',
          ended_at: '2026-07-29T10:30:00+00:00',
        },
        unit_limit: 50,
        page_limit: 5,
        units: [{
          unit_id: 'fixture-unit-1',
          label: 'TASK209 Fixture Tractor',
          position: {
            latitude: 39.77421,
            longitude: 64.42861,
            observed_at: stale
              ? '2026-07-28T08:00:00+00:00'
              : '2026-07-29T10:28:00+00:00',
          },
          movement: true,
          ignition: true,
          sensors: [{ code: 'fuel_level', value: 68.4, unit: '%' }],
          field_intersection: true,
          geofence_intersection: null,
          age_seconds: stale ? 95400 : 120,
          stale,
        }],
      },
    };
  }
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
  source: `
    (() => {
      const value = ['task209', Date.now(), Math.random()].join('-');
      localStorage.setItem('agrosat_token', value);
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

async function waitFor(expression, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const bodyText = await evaluate(`document.body?.innerText?.slice(0, 500) || ''`);
  throw new Error(
    `Timed out waiting for DOM condition: ${expression}; `
    + `body=${JSON.stringify(bodyText)}; api=${JSON.stringify(apiRequests)}`,
  );
}

async function selectTab(label) {
  const clicked = await evaluate(`(() => {
    const button = [...document.querySelectorAll('button')]
      .find((item) => item.textContent.trim() === ${JSON.stringify(label)});
    if (!button) return false;
    button.click();
    return true;
  })()`);
  assert.equal(clicked, true, `Missing tab: ${label}`);
}

async function showTelematics(mode, expectedText) {
  telematicsMode = mode;
  await selectTab('Инфо');
  await selectTab('Техника');
  await waitFor(`document.body.innerText.includes(${JSON.stringify(expectedText)})`);
}

let loaded = waitForEvent('Page.loadEventFired');
await send('Page.navigate', { url: baseUrl });
await loaded;
await evaluate(`
  localStorage.setItem(
    'agrosat_token',
    ['task209', Date.now(), Math.random()].join('-')
  )
`);
loaded = waitForEvent('Page.loadEventFired');
await send('Page.navigate', { url: `${baseUrl}/fields/1` });
await loaded;
await waitFor(`document.body.innerText.includes('TASK209 Fixture Field')`);

await selectTab('Техника');
await waitFor(`document.body.innerText.includes('Телематика не подключена')`);
await showTelematics('available', 'TASK209 Fixture Tractor');

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
      .find((item) => item.textContent.trim() === 'Техника');
    const rect = tab?.getBoundingClientRect();
    return {
      width: innerWidth,
      height: innerHeight,
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
      availableVisible: document.body.innerText.includes('Данные доступны'),
      provenanceVisible: document.body.innerText.includes('contract_fixture'),
      readOnlyVisible: document.body.innerText.includes('только чтение'),
      operationCaveatVisible: document.body.innerText.includes('не подтверждает выполненную операцию'),
      touchTargetHeight: rect ? Math.round(rect.height) : 0
    };
  })()`);
  assert.equal(metrics.horizontalOverflow, false, `horizontal overflow at ${width}x${height}`);
  assert.equal(metrics.availableVisible, true, `available state hidden at ${width}x${height}`);
  assert.equal(metrics.provenanceVisible, true, `provenance hidden at ${width}x${height}`);
  assert.equal(metrics.readOnlyVisible, true, `read-only label hidden at ${width}x${height}`);
  assert.equal(metrics.operationCaveatVisible, true, `operation caveat hidden at ${width}x${height}`);
  if (width === 390) {
    assert.ok(metrics.touchTargetHeight >= 44, `mobile tab target is ${metrics.touchTargetHeight}px`);
  }
  viewportResults.push(metrics);
}

await showTelematics('stale', 'Все данные устарели');
await waitFor(`document.body.innerText.includes('Данные устарели')`);
await showTelematics('unavailable', 'Телематика временно недоступна');
await waitFor(`document.body.innerText.includes('Повторить')`);
await showTelematics('available', 'TASK209 Fixture Tractor');

const semanticAudit = await evaluate(`(() => {
  const section = document.querySelector('[aria-labelledby="field-telematics-title"]');
  const title = document.getElementById('field-telematics-title');
  const unitList = document.querySelector('[aria-label="Сопоставленная техника"]');
  const writeLabels = ['Создать', 'Изменить', 'Удалить', 'Отправить', 'Сохранить'];
  return {
    labelledSection: Boolean(section && title && section.getAttribute('aria-labelledby') === title.id),
    unitListPresent: Boolean(unitList),
    writeControls: [...document.querySelectorAll('button')]
      .filter((button) => writeLabels.includes(button.textContent.trim())).length
  };
})()`);
assert.deepEqual(semanticAudit, {
  labelledSection: true,
  unitListPresent: true,
  writeControls: 0,
});

const writeRequests = apiRequests.filter((request) => request.method !== 'GET');
const providerRequests = externalRequests.filter((url) => (
  /wialon|hst-api|telematics/i.test(url)
));
assert.deepEqual(writeRequests, [], 'browser attempted an API write');
assert.deepEqual(providerRequests, [], 'browser attempted a direct Wialon/provider request');
assert.deepEqual(consoleErrors, [], `console errors: ${consoleErrors.join(' | ')}`);
assert.deepEqual(failedRequests, [], `failed requests: ${failedRequests.join(' | ')}`);

const report = {
  decision: 'PASS_CDP_FIXTURE_RUNTIME',
  runtime_adapter: 'Chrome DevTools Protocol fixture harness',
  fixture_mode: true,
  states: ['unsupported', 'available', 'stale', 'unavailable'],
  viewports: viewportResults,
  semantic_audit: semanticAudit,
  api_request_count: apiRequests.length,
  api_write_requests: writeRequests.length,
  direct_provider_requests: providerRequests.length,
  external_asset_requests: externalRequests.length,
  console_errors: consoleErrors.length,
  failed_network_requests: failedRequests.length,
  live_wialon_validated: false,
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
    join(evidenceDir, 'wialon-available-390x844.png'),
    Buffer.from(screenshot.data, 'base64'),
  );
  await writeFile(
    join(evidenceDir, 'browser-contract.json'),
    `${JSON.stringify(report, null, 2)}\n`,
    'utf8',
  );
}

console.log(`TASK209 Wialon read-only browser contract: PASS ${JSON.stringify(report)}`);
await send('Page.close');
socket.close();
