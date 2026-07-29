import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const cdpPort = Number(process.env.TASK209_CDP_PORT || 46552);
const baseUrl = process.env.TASK209_BASE_URL || 'http://127.0.0.1:46551';
const evidenceDir = process.env.TASK209_EVIDENCE_DIR || '';
const apiRequests = [];
const consoleErrors = [];
const failedRequests = [];
const externalRequests = [];
let lifecycle = [{
  id: 91,
  enterprise_id: 5,
  request_type: 'export',
  status: 'requested',
  reason: 'Annual tenant portability review',
}];

function fixture(url, method, body) {
  const parsed = new URL(url);
  apiRequests.push({ method, path: parsed.pathname });
  if (parsed.pathname === '/api/auth/me') {
    return { status: 200, body: { id: 7, full_name: 'TASK209 Admin', role: 'admin', enterprise_id: null, is_active: true } };
  }
  if (parsed.pathname === '/api/enterprises/') return { status: 200, body: [] };
  if (parsed.pathname === '/api/enterprises/5') {
    return { status: 200, body: { id: 5, name: 'TASK209 Fixture Enterprise', code: 'TASK209', region: 'Bukhara', fields: [] } };
  }
  if (parsed.pathname === '/api/alerts/' || parsed.pathname === '/api/alerts') {
    return { status: 200, body: [] };
  }
  if (parsed.pathname === '/api/commercial/tenants/5' && method === 'GET') {
    return {
      status: 200,
      body: {
        enterprise: { id: 5, name: 'TASK209 Fixture Enterprise', code: 'TASK209', is_active: true },
        profile: {
          plan_code: 'industrial',
          subscription_state: 'active',
          feature_flags: { pixel_anomalies: true, offline_scouting: true },
          quota_limits: { fields: 1000, users: 50 },
          retention_policy: { audit_days: 2555 },
          branding: { display_name: 'TASK209 Fixture Enterprise' },
        },
        namespaces: {
          storage: 'tenants/5/',
          cache: 'agrosat:tenant:5:',
          jobs: 'tenant.5.',
          exports: 'tenant-5/',
        },
        providers: [{ provider_code: 'wialon', configured: true, status: 'configured', last_validated_at: null, last_failure_category: null }],
        billing: { supported: false, state: 'active', payment_processing: false },
      },
    };
  }
  if (parsed.pathname === '/api/commercial/tenants/5/memberships') {
    return { status: 200, body: { limit: 50, offset: 0, items: [{ id: 1, user_id: 7, full_name: 'TASK209 Admin', membership_role: 'owner', status: 'active' }] } };
  }
  if (parsed.pathname === '/api/commercial/tenants/5/lifecycle-requests' && method === 'GET') {
    return { status: 200, body: { limit: 50, offset: 0, items: lifecycle } };
  }
  if (parsed.pathname === '/api/commercial/tenants/5/lifecycle-requests' && method === 'POST') {
    const payload = JSON.parse(body || '{}');
    lifecycle = [...lifecycle, { id: 92, enterprise_id: 5, status: 'requested', ...payload }];
    return { status: 201, body: { created: true, request: lifecycle.at(-1) } };
  }
  if (parsed.pathname === '/api/commercial/tenants/5/lifecycle-requests/91/decision' && method === 'POST') {
    lifecycle = lifecycle.map((item) => item.id === 91 ? { ...item, status: 'approved', decision_note: 'Scope reviewed' } : item);
    return { status: 200, body: lifecycle[0] };
  }
  return { status: method === 'GET' ? 404 : 405, body: { detail: 'Fixture route not found' } };
}

const targetResponse = await fetch(`http://127.0.0.1:${cdpPort}/json/new?about:blank`, { method: 'PUT' });
assert.equal(targetResponse.ok, true);
const target = await targetResponse.json();
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true });
  socket.addEventListener('error', reject, { once: true });
});

let sequence = 0;
const pending = new Map();
function send(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject, method });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

socket.addEventListener('message', (message) => {
  const payload = JSON.parse(message.data);
  if (payload.id) {
    const waiter = pending.get(payload.id);
    if (!waiter) return;
    pending.delete(payload.id);
    if (payload.error) waiter.reject(new Error(`${waiter.method}: ${payload.error.message}`));
    else waiter.resolve(payload.result);
    return;
  }
  if (payload.method === 'Runtime.consoleAPICalled' && payload.params.type === 'error') {
    consoleErrors.push(payload.params.args.map((item) => item.value || item.description || '').join(' '));
  }
  if (payload.method === 'Network.loadingFailed' && !payload.params.canceled) {
    failedRequests.push(payload.params.errorText);
  }
  if (payload.method === 'Network.requestWillBeSent') {
    const url = payload.params.request.url;
    const host = /^https?:/.test(url) ? new URL(url).hostname : '';
    if (
      /^https?:/.test(url)
      && !url.startsWith(baseUrl)
      && !['fonts.googleapis.com', 'fonts.gstatic.com'].includes(host)
    ) externalRequests.push(url);
  }
  if (payload.method === 'Fetch.requestPaused') {
    const { requestId, request } = payload.params;
    const response = fixture(request.url, request.method, request.postData);
    void send('Fetch.fulfillRequest', {
      requestId,
      responseCode: response.status,
      responseHeaders: [
        { name: 'Content-Type', value: 'application/json; charset=utf-8' },
        { name: 'Cache-Control', value: 'no-store' },
      ],
      body: Buffer.from(JSON.stringify(response.body)).toString('base64'),
    });
  }
});

await Promise.all([
  send('Page.enable'),
  send('Runtime.enable'),
  send('Network.enable'),
  send('Fetch.enable', { patterns: [{ urlPattern: `${baseUrl}/api/*`, requestStage: 'Request' }] }),
]);

async function evaluate(expression) {
  const result = await send('Runtime.evaluate', {
    expression: `JSON.stringify((${expression}))`,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value === undefined ? undefined : JSON.parse(result.result.value);
}

async function waitFor(expression, timeout = 12000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out: ${expression}`);
}

await send('Page.navigate', { url: `${baseUrl}/enterprises/5` });
await waitFor("document.readyState === 'complete'");
await evaluate("localStorage.setItem('agrosat_token','fixture-runtime-token')");
await send('Page.navigate', { url: `${baseUrl}/enterprises/5` });
await waitFor("document.body && document.body.innerText.includes('TASK209 Fixture Enterprise')");
await evaluate(`Array.from(document.querySelectorAll('button')).find((button) => button.textContent.includes('Коммерческий контур')).click()`);
await waitFor("document.querySelector('[data-testid=\"commercial-tenant-panel\"]')");

const viewports = [
  [1920, 1080],
  [1440, 900],
  [1024, 768],
  [390, 844],
];
const viewportResults = [];
for (const [width, height] of viewports) {
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 500 });
  await new Promise((resolve) => setTimeout(resolve, 120));
  viewportResults.push(await evaluate(`(() => ({
    viewport: '${width}x${height}',
    overflow: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
    panel: Boolean(document.querySelector('[data-testid="commercial-tenant-panel"]')),
    touchTargets: Array.from(document.querySelectorAll('[data-testid="commercial-tenant-panel"] button, [data-testid="commercial-tenant-panel"] select')).every((node) => node.getBoundingClientRect().height >= 44),
    paymentDisabled: document.body.innerText.includes('payment_processing') && document.body.innerText.includes('disabled'),
    namespacesVisible: document.body.innerText.includes('agrosat:tenant:5:') && document.body.innerText.includes('tenant.5.'),
    secretReferenceVisible: document.body.innerText.includes('vault/') || document.body.innerText.includes('secret_reference'),
  }))()`));
}

await evaluate(`(() => {
  const textareas = document.querySelectorAll('[data-testid="commercial-tenant-panel"] textarea');
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  setter.call(textareas[0], 'TASK209 deterministic tenant export request');
  textareas[0].dispatchEvent(new Event('input', { bubbles: true }));
})()`);
await waitFor("!document.querySelector('[data-testid=\"commercial-tenant-panel\"] button[type=\"submit\"]').disabled");
await evaluate("document.querySelector('[data-testid=\"commercial-tenant-panel\"] button[type=\"submit\"]').click()");
await waitFor("document.body.innerText.includes('#92')");
await evaluate(`(() => {
  const article = Array.from(document.querySelectorAll('[data-testid="commercial-tenant-panel"] article')).find((node) => node.innerText.includes('#91'));
  const textarea = article.querySelector('textarea');
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  setter.call(textarea, 'Scope reviewed');
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
})()`);
await evaluate(`Array.from(document.querySelectorAll('[data-testid="commercial-tenant-panel"] article button')).find((button) => button.textContent.includes('Одобрить')).click()`);
await waitFor("document.body.innerText.includes('#91') && document.body.innerText.includes('Одобрено')");

const writes = apiRequests.filter((item) => item.method !== 'GET');
const finalText = await evaluate('document.body.innerText');
assert.equal(viewportResults.every((item) => item.panel && item.touchTargets && item.overflow === 0), true);
assert.equal(viewportResults.every((item) => item.paymentDisabled && item.namespacesVisible && !item.secretReferenceVisible), true);
assert.deepEqual(writes, [
  { method: 'POST', path: '/api/commercial/tenants/5/lifecycle-requests' },
  { method: 'POST', path: '/api/commercial/tenants/5/lifecycle-requests/91/decision' },
]);
assert.equal(finalText.includes('Данные ещё не экспортированы и не удалены'), false);
assert.equal(consoleErrors.length, 0);
assert.equal(failedRequests.length, 0);
assert.equal(externalRequests.length, 0);

const screenshot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
const summary = {
  status: 'PASS',
  role: 'admin',
  viewports: viewportResults,
  expectedFixtureMemoryWrites: writes,
  productionDatabaseWrites: 0,
  unexpectedBusinessWrites: 0,
  credentialReferencesVisible: false,
  consoleErrors,
  failedRequests,
  externalRequests,
};
if (evidenceDir) {
  await mkdir(evidenceDir, { recursive: true });
  await writeFile(join(evidenceDir, 'browser-contract.json'), `${JSON.stringify(summary, null, 2)}\n`);
  await writeFile(join(evidenceDir, 'commercial-tenant-mobile.png'), screenshot.data, 'base64');
}
console.log(JSON.stringify(summary));
socket.close();
