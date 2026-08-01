import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';


const required = [
  'R1_CREDENTIALS_PATH',
  'R1_EVIDENCE_PATH',
  'R1_PLAYWRIGHT_PACKAGE_ROOT',
  'R1_CHROMIUM_EXECUTABLE',
  'R1_FRONTEND_URL',
  'R1_BACKEND_URL',
  'R1_EXPECTED_HEAD',
];
for (const name of required) {
  if (!process.env[name]) throw new Error(`Missing required qualification setting: ${name}`);
}

const evidencePath = path.resolve(process.env.R1_EVIDENCE_PATH);
if (!evidencePath.toLowerCase().startsWith('c:\\agrosat_backups\\program_r1_completion_run\\')) {
  throw new Error('Evidence path is outside the approved completion root.');
}
const credentialPath = path.resolve(process.env.R1_CREDENTIALS_PATH);
if (credentialPath.toLowerCase().startsWith('c:\\agrosat_backups\\')) {
  throw new Error('Protected credentials must remain outside evidence.');
}

const requireFromPlaywright = createRequire(
  path.join(path.resolve(process.env.R1_PLAYWRIGHT_PACKAGE_ROOT), 'package.json'),
);
const { chromium } = requireFromPlaywright('playwright-core');
const credentials = JSON.parse(await readFile(credentialPath, 'utf8'));
const frontendUrl = new URL(process.env.R1_FRONTEND_URL).origin;
const backendUrl = new URL(process.env.R1_BACKEND_URL).origin;
for (const value of [frontendUrl, backendUrl]) {
  const parsed = new URL(value);
  if (parsed.hostname !== '127.0.0.1') throw new Error('Qualification endpoints must be loopback-only.');
}

async function atomicJson(value) {
  const temporary = `${evidencePath}.${crypto.randomUUID()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  await rename(temporary, evidencePath);
}

async function loginApi(role) {
  const account = credentials.users[role];
  const response = await fetch(`${backendUrl}/api/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ username: account.email, password: account.password }),
  });
  assert.equal(response.status, 200, `${role} setup authentication failed`);
  const payload = await response.json();
  assert.ok(payload.access_token, `${role} setup token missing`);
  return payload.access_token;
}

async function api(token, pathname, { method = 'GET', body, idempotency } = {}) {
  const headers = { authorization: `Bearer ${token}` };
  if (body !== undefined) headers['content-type'] = 'application/json';
  if (idempotency) headers['idempotency-key'] = idempotency;
  const response = await fetch(`${backendUrl}${pathname}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = response.headers.get('content-type')?.includes('json')
    ? await response.json()
    : null;
  return { status: response.status, payload };
}

const managerToken = await loginApi('manager');
const agronomistToken = await loginApi('agronomist');
const existing = await api(managerToken, '/api/field-inspections?field_id=1&limit=200');
assert.equal(existing.status, 200, 'isolated inspection setup lookup failed');
let createdInspection = existing.payload.items.find((item) => (
  item.title === 'PROGRAM R1 offline reload qualification'
  && Number(item.assigned_to?.id) === 3
  && ['pending', 'in_progress'].includes(item.status)
));
if (!createdInspection) {
  const idempotency = `r1-offline-probe-${crypto.randomUUID()}`.slice(0, 64);
  const created = await api(managerToken, '/api/field-inspections', {
    method: 'POST',
    idempotency,
    body: {
      field_id: 1,
      assigned_to_id: 3,
      source: 'manual',
      title: 'PROGRAM R1 offline reload qualification',
      instructions: 'Isolated qualification record; not a live agronomic diagnosis.',
      due_date: '2026-08-10',
    },
  });
  assert.equal(created.status, 201, 'isolated inspection setup failed');
  createdInspection = created.payload.inspection || created.payload;
}
if (createdInspection.status === 'pending') {
  const started = await api(
    agronomistToken,
    `/api/field-inspections/${createdInspection.id}/start`,
    { method: 'POST', body: { expected_version: createdInspection.version } },
  );
  assert.equal(started.status, 200, 'isolated inspection start failed');
}

const browser = await chromium.launch({
  executablePath: process.env.R1_CHROMIUM_EXECUTABLE,
  headless: true,
});
const context = await browser.newContext({
  viewport: { width: 390, height: 844 },
  serviceWorkers: 'allow',
  locale: 'ru-RU',
  timezoneId: 'Asia/Tashkent',
});
const page = await context.newPage();
const consoleErrors = [];
const pageErrors = [];
const failedRequests = [];
let offlineWindow = false;
page.on('console', (message) => {
  if (message.type() === 'error') consoleErrors.push(message.text().slice(0, 300));
});
page.on('pageerror', (error) => pageErrors.push(String(error).slice(0, 300)));
page.on('requestfailed', (request) => {
  const parsed = new URL(request.url());
  failedRequests.push({
    path: parsed.origin === frontendUrl ? parsed.pathname : `${parsed.hostname}${parsed.pathname}`,
    category: offlineWindow ? 'expected_offline_window' : 'unexpected',
    error: request.failure()?.errorText || 'failed',
  });
});

let result;
try {
  await page.goto(`${frontendUrl}/login`, { waitUntil: 'networkidle' });
  await page.getByLabel('Email').fill(credentials.users.agronomist.email);
  await page.locator('#login-password').fill(credentials.users.agronomist.password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(/\/inspections|\/dashboard/, { timeout: 15000 });
  await page.goto(`${frontendUrl}/inspections/${createdInspection.id}`, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.body.innerText.includes('PROGRAM R1 offline reload qualification'));

  await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.ready;
    if (!registration.active) throw new Error('service worker is not active');
  });
  if (!await page.evaluate(() => Boolean(navigator.serviceWorker.controller))) {
    await page.reload({ waitUntil: 'networkidle' });
  }

  const before = await page.evaluate(async ({ inspectionId }) => {
    const scope = '1:3';
    const request = indexedDB.open('agrosat-offline-scouting', 1);
    const database = await new Promise((resolve, reject) => {
      request.onupgradeneeded = () => {
        for (const name of ['snapshots', 'drafts', 'queue']) {
          if (!request.result.objectStoreNames.contains(name)) {
            request.result.createObjectStore(name, { keyPath: 'key' });
          }
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const draft = {
      key: `${scope}:inspection:${inspectionId}`,
      scope,
      inspectionId,
      baseVersion: 2,
      result: { cause_code: 'unconfirmed', cause_details: 'offline reload probe' },
      evidence: [],
      action: null,
      idempotency: { result: 'offline-probe-result', evidence: [], action: null },
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      schemaVersion: 1,
    };
    await new Promise((resolve, reject) => {
      const transaction = database.transaction('drafts', 'readwrite');
      transaction.objectStore('drafts').put(draft);
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
    });
    database.close();
    const cacheEntries = [];
    for (const cacheName of await caches.keys()) {
      for (const cacheRequest of await (await caches.open(cacheName)).keys()) {
        cacheEntries.push(new URL(cacheRequest.url).pathname);
      }
    }
    return {
      serviceWorkerControlled: Boolean(navigator.serviceWorker.controller),
      serviceWorkerRegistrations: (await navigator.serviceWorker.getRegistrations()).length,
      draftCount: 1,
      apiCacheEntries: cacheEntries.filter((item) => item.startsWith('/api/')).length,
      shellCacheEntries: cacheEntries.length,
    };
  }, { inspectionId: createdInspection.id });

  offlineWindow = true;
  await context.setOffline(true);
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForTimeout(2500);

  const after = await page.evaluate(async () => {
    let draftCount = 0;
    let databasePresent = false;
    if (indexedDB.databases) {
      databasePresent = (await indexedDB.databases())
        .some((item) => item.name === 'agrosat-offline-scouting');
    }
    if (databasePresent) {
      const request = indexedDB.open('agrosat-offline-scouting', 1);
      const database = await new Promise((resolve, reject) => {
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      if (database.objectStoreNames.contains('drafts')) {
        draftCount = await new Promise((resolve, reject) => {
          const query = database.transaction('drafts', 'readonly').objectStore('drafts').count();
          query.onsuccess = () => resolve(query.result);
          query.onerror = () => reject(query.error);
        });
      }
      database.close();
    }
    return {
      pathname: location.pathname,
      tokenPresent: localStorage.getItem('agrosat_token') !== null,
      cachedUserPresent: localStorage.getItem('agrosat_user') !== null,
      databasePresent,
      draftCount,
      loginVisible: Boolean(document.querySelector('#login-email')),
      appShellRendered: Boolean(document.querySelector('#root')?.childElementCount),
      serviceWorkerControlled: Boolean(navigator.serviceWorker.controller),
    };
  });
  const retained = (
    after.pathname === `/inspections/${createdInspection.id}`
    && after.tokenPresent
    && after.cachedUserPresent
    && after.databasePresent
    && after.draftCount === 1
    && !after.loginVisible
  );
  const screenshotPath = evidencePath.replace(/\.json$/i, '.png');
  await page.screenshot({ path: screenshotPath, fullPage: true });
  result = {
    schemaVersion: 1,
    recordedAt: new Date().toISOString(),
    exactHead: process.env.R1_EXPECTED_HEAD,
    runtimeClass: 'isolated_ephemeral_real_chromium',
    browser: 'Chromium via playwright-core',
    viewport: '390x844',
    serviceWorkers: 'allow',
    setup: {
      inspectionId: createdInspection.id,
      assignedRole: 'agronomist',
      productionContacted: false,
      productionWrites: 0,
    },
    beforeOfflineReload: before,
    afterOfflineReload: after,
    expectedOfflineFailures: failedRequests.filter((item) => item.category === 'expected_offline_window').length,
    unexpectedFailedRequests: failedRequests.filter((item) => item.category === 'unexpected'),
    pageErrors,
    consoleErrorCount: consoleErrors.length,
    sensitiveValuesIncluded: false,
    decision: retained
      ? 'PASS_OFFLINE_RELOAD_RETAINS_SCOPED_DRAFT'
      : 'FAIL_OFFLINE_RELOAD_PURGES_SCOPED_DRAFT',
    productionWrites: 0,
  };
  await atomicJson(result);
  process.exitCode = retained ? 0 : 1;
} finally {
  await context.setOffline(false).catch(() => {});
  await context.close();
  await browser.close();
}

console.log(`${result.decision}; DRAFTS_AFTER=${result.afterOfflineReload.draftCount}; PRODUCTION_WRITES=0`);
