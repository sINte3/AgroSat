import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import {
  mkdir,
  readFile,
  rename,
  stat,
  writeFile,
} from 'node:fs/promises';
import path from 'node:path';


const requiredEnvironment = [
  'R1_CREDENTIALS_PATH',
  'R1_EVIDENCE_DIR',
  'R1_PLAYWRIGHT_PACKAGE_ROOT',
  'R1_CHROMIUM_EXECUTABLE',
  'R1_FRONTEND_URL',
  'R1_BACKEND_URL',
  'R1_EXPECTED_HEAD',
];
for (const name of requiredEnvironment) {
  if (!process.env[name]) throw new Error(`Missing required qualification setting: ${name}`);
}

const evidenceDir = path.resolve(process.env.R1_EVIDENCE_DIR);
if (!evidenceDir.toLowerCase().startsWith('c:\\agrosat_backups\\program_r1_completion_run\\')) {
  throw new Error('Evidence directory is outside the approved completion root.');
}
const credentialsPath = path.resolve(process.env.R1_CREDENTIALS_PATH);
if (!credentialsPath.toLowerCase().startsWith('c:\\tmp\\agrosat_r1_completion_')) {
  throw new Error('Credentials are outside the isolated temporary root.');
}
const frontendUrl = new URL(process.env.R1_FRONTEND_URL).origin;
const backendUrl = new URL(process.env.R1_BACKEND_URL).origin;
for (const value of [frontendUrl, backendUrl]) {
  if (new URL(value).hostname !== '127.0.0.1') {
    throw new Error('Qualification endpoints must be loopback-only.');
  }
}

await mkdir(evidenceDir, { recursive: true });
const screenshotDir = path.join(evidenceDir, 'screenshots');
await mkdir(screenshotDir, { recursive: true });
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const requireFromPlaywright = createRequire(
  path.join(path.resolve(process.env.R1_PLAYWRIGHT_PACKAGE_ROOT), 'package.json'),
);
const { chromium } = requireFromPlaywright('playwright-core');

const dueDate = new Date(Date.now() + (14 * 24 * 60 * 60 * 1000)).toISOString().slice(0, 10);
const expectedScope = '1:3';
const qualificationPrefix = 'PROGRAM R1 offline qualification';
const sensitiveDraftPhrase = 'offline-only-evidence-phrase-r1';

const report = {
  schemaVersion: 1,
  recordedAt: new Date().toISOString(),
  exactHead: process.env.R1_EXPECTED_HEAD,
  runtimeClass: 'isolated_ephemeral_real_chromium_service_worker_fastapi_postgresql_redis',
  status: 'running',
  marker: null,
  stage: 'initializing',
  serviceWorker: {},
  indexedDb: {},
  limits: {},
  offlineReload: {},
  synchronization: {},
  conflict: {},
  logoutPurge: {},
  secondUser: {},
  mobile: {},
  browser: {
    viewports: ['1440x900', '1024x768', '390x844'],
    screenshots: [],
    consoleErrors: [],
    expectedOfflineConsoleMessages: [],
    expectedConflictConsoleMessages: [],
    pageErrors: [],
    unexpectedFailedRequests: [],
    expectedOfflineFailedRequests: [],
    cancelledRequests: [],
    unexpectedHttpErrors: [],
    expectedConflictHttpErrors: [],
    expectedProbeHttpErrors: [],
  },
  trace: [],
  credentialsIncluded: false,
  productionContacted: false,
  productionWrites: 0,
};

function trace(step, detail = {}) {
  report.stage = step;
  report.trace.push({ at: new Date().toISOString(), step, ...detail });
}

async function atomicJson(name, value) {
  const target = path.join(evidenceDir, name);
  const temporary = `${target}.${randomUUID()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  await rename(temporary, target);
}

async function sha256(filePath) {
  return createHash('sha256').update(await readFile(filePath)).digest('hex');
}

function safeFailure(error) {
  return String(error?.stack || error || 'unknown failure')
    .replace(/Bearer\s+[A-Za-z0-9._~+\/-]+/gi, 'Bearer [REDACTED]')
    .replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, '[REDACTED_EMAIL]')
    .slice(0, 4000);
}

function safeRequestPath(url) {
  const parsed = new URL(url);
  return parsed.origin === frontendUrl || parsed.origin === backendUrl
    ? `${parsed.pathname}${parsed.search}`
    : `${parsed.hostname}${parsed.pathname}`;
}

function idempotency(prefix) {
  return `${prefix}-${randomUUID()}`.slice(0, 64);
}

async function browserApi(page, pathname, { method = 'GET', body, key } = {}) {
  return page.evaluate(async ({ requestPath, requestMethod, requestBody, idempotencyKey }) => {
    const headers = {
      authorization: `Bearer ${localStorage.getItem('agrosat_token')}`,
      'x-r1-qualification-probe': 'true',
    };
    if (requestBody !== undefined) headers['content-type'] = 'application/json';
    if (idempotencyKey) headers['idempotency-key'] = idempotencyKey;
    const response = await fetch(requestPath, {
      method: requestMethod,
      headers,
      body: requestBody === undefined ? undefined : JSON.stringify(requestBody),
      cache: requestMethod === 'GET' ? 'no-store' : 'default',
    });
    let payload = null;
    if (response.headers.get('content-type')?.includes('json')) payload = await response.json();
    return {
      status: response.status,
      payload,
      cacheControl: response.headers.get('cache-control'),
    };
  }, {
    requestPath: pathname,
    requestMethod: method,
    requestBody: body,
    idempotencyKey: key,
  });
}

async function readOfflineDatabase(page) {
  return page.evaluate(async () => {
    if (typeof indexedDB.databases === 'function') {
      const names = await indexedDB.databases();
      if (!names.some((item) => item.name === 'agrosat-offline-scouting')) {
        return { present: false, stores: { snapshots: [], drafts: [], queue: [] } };
      }
    }
    const request = indexedDB.open('agrosat-offline-scouting', 1);
    const database = await new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const read = (storeName) => new Promise((resolve, reject) => {
      const query = database.transaction(storeName, 'readonly').objectStore(storeName).getAll();
      query.onsuccess = () => resolve(query.result);
      query.onerror = () => reject(query.error);
    });
    const stores = {
      snapshots: await read('snapshots'),
      drafts: await read('drafts'),
      queue: await read('queue'),
    };
    database.close();
    return { present: true, stores };
  });
}

function containsBinary(value) {
  if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) return true;
  if (typeof Blob !== 'undefined' && value instanceof Blob) return true;
  if (Array.isArray(value)) return value.some(containsBinary);
  if (value && typeof value === 'object') return Object.values(value).some(containsBinary);
  return false;
}

async function cacheAudit(page) {
  return page.evaluate(async () => {
    const entries = [];
    for (const cacheName of await caches.keys()) {
      const cache = await caches.open(cacheName);
      for (const request of await cache.keys()) {
        const url = new URL(request.url);
        entries.push({ cacheName, path: `${url.pathname}${url.search}` });
      }
    }
    return {
      cacheNames: [...new Set(entries.map((item) => item.cacheName))],
      entryCount: entries.length,
      apiEntryCount: entries.filter((item) => item.path.startsWith('/api/')).length,
      entries,
    };
  });
}

async function capture(page, role, viewport, route, action) {
  const stem = `${String(report.browser.screenshots.length + 1).padStart(2, '0')}-${role}-${viewport.width}x${viewport.height}-${action}`
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-');
  const filePath = path.join(screenshotDir, `${stem}.png`);
  await page.screenshot({ path: filePath, fullPage: false });
  const fileStat = await stat(filePath);
  report.browser.screenshots.push({
    file: path.relative(evidenceDir, filePath),
    role,
    viewport: `${viewport.width}x${viewport.height}`,
    route,
    action,
    exactHead: process.env.R1_EXPECTED_HEAD,
    bytes: fileStat.size,
    sha256: await sha256(filePath),
  });
}

async function loginUi(page, role) {
  await page.goto(`${frontendUrl}/login`, { waitUntil: 'networkidle' });
  await page.getByLabel('Email').fill(credentials.users[role].email);
  await page.locator('#login-password').fill(credentials.users[role].password);
  await page.locator('button[type="submit"]').click();
  await page.waitForFunction(() => (
    location.pathname !== '/login'
    && localStorage.getItem('agrosat_token')
    && localStorage.getItem('agrosat_user')
  ));
  return page.evaluate(() => {
    const user = JSON.parse(localStorage.getItem('agrosat_user'));
    return { id: user.id, role: user.role, enterpriseId: user.enterprise_id, active: user.is_active };
  });
}

async function createStartedInspection(managerPage, agronomistPage, suffix) {
  const active = await browserApi(managerPage, '/api/field-inspections?field_id=2&limit=200');
  assert.equal(active.status, 200);
  for (const item of active.payload.items.filter((candidate) => (
    candidate.title?.startsWith(qualificationPrefix)
    && ['pending', 'in_progress'].includes(candidate.status)
  ))) {
    const cancelled = await browserApi(managerPage, `/api/field-inspections/${item.id}/cancel`, {
      method: 'POST',
      body: {
        expected_version: item.version,
        cancellation_reason: 'Superseded isolated PROGRAM R1 offline qualification record.',
      },
    });
    assert.equal(cancelled.status, 200);
  }
  const remaining = await browserApi(managerPage, '/api/field-inspections?field_id=2&limit=200');
  const unrelatedActive = remaining.payload.items.filter((candidate) => (
    !candidate.title?.startsWith(qualificationPrefix)
    && ['pending', 'in_progress'].includes(candidate.status)
  ));
  assert.equal(unrelatedActive.length, 0, 'Unexpected active inspection exists on isolated field 2');

  const title = `${qualificationPrefix} ${suffix}`;
  const created = await browserApi(managerPage, '/api/field-inspections', {
    method: 'POST',
    key: idempotency(`r1-offline-${suffix}`),
    body: {
      field_id: 2,
      assigned_to_id: 3,
      source: 'manual',
      title,
      instructions: 'Capture a human-confirmed result through the isolated offline scouting workflow.',
      due_date: dueDate,
    },
  });
  assert.equal(created.status, 201);
  const inspectionId = created.payload.inspection.id;
  const pending = await browserApi(agronomistPage, `/api/field-inspections/${inspectionId}`);
  assert.equal(pending.status, 200);
  const started = await browserApi(agronomistPage, `/api/field-inspections/${inspectionId}/start`, {
    method: 'POST', body: { expected_version: pending.payload.version },
  });
  assert.equal(started.status, 200);
  assert.equal(started.payload.status, 'in_progress');
  return { id: inspectionId, title, version: started.payload.version };
}

async function offlinePanel(page) {
  const section = page.locator('section').filter({
    has: page.getByRole('heading', { name: 'Офлайн-черновик осмотра' }),
  });
  await section.waitFor();
  return section;
}

async function fillAndQueue(page, uniquePhrase) {
  const section = await offlinePanel(page);
  await section.getByLabel('Подтверждённая причина').selectOption('irrigation');
  await section.getByLabel('Пояснение причины').fill(`Human-confirmed irrigation condition; ${uniquePhrase}.`);
  await section.getByLabel('Заметка о доказательствах').fill(`Field evidence retained locally; ${uniquePhrase}.`);
  await section.getByLabel('Геопозиция', { exact: true }).check();
  await section.getByLabel('Широта').last().fill('39.77');
  await section.getByLabel('Долгота').last().fill('64.42');
  await section.getByLabel('Создать действие на себя после результата').check();
  await section.getByLabel('Описание').fill('Inspect the isolated qualification irrigation outlet and document corrective work.');
  await section.getByLabel('Срок').fill(dueDate);
  await section.getByRole('button', { name: 'В очередь' }).click();
  await section.getByText('ожидают ручной синхронизации', { exact: false }).waitFor();
  return section;
}

const browser = await chromium.launch({
  executablePath: process.env.R1_CHROMIUM_EXECUTABLE,
  headless: true,
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  serviceWorkers: 'allow',
  locale: 'ru-RU',
  timezoneId: 'Asia/Tashkent',
});
let offlineWindow = false;
let conflictWindow = false;

await context.addInitScript(() => {
  window.__r1ObjectUrls = new Set();
  const originalCreate = URL.createObjectURL.bind(URL);
  const originalRevoke = URL.revokeObjectURL.bind(URL);
  URL.createObjectURL = (value) => {
    const url = originalCreate(value);
    window.__r1ObjectUrls.add(url);
    return url;
  };
  URL.revokeObjectURL = (url) => {
    window.__r1ObjectUrls.delete(url);
    return originalRevoke(url);
  };
});

const page = await context.newPage();
page.setDefaultTimeout(18000);
page.on('console', (message) => {
  if (message.type() !== 'error') return;
  const item = { text: message.text().slice(0, 300), stage: report.stage };
  if (offlineWindow && /ERR_INTERNET_DISCONNECTED|Failed to load resource/i.test(item.text)) {
    report.browser.expectedOfflineConsoleMessages.push(item);
  } else if (conflictWindow && /409|Conflict|Failed to load resource/i.test(item.text)) {
    report.browser.expectedConflictConsoleMessages.push(item);
  } else {
    report.browser.consoleErrors.push(item);
  }
});
page.on('pageerror', (error) => report.browser.pageErrors.push({
  text: String(error).slice(0, 300), stage: report.stage,
}));
page.on('requestfailed', (request) => {
  const item = {
    method: request.method(),
    path: safeRequestPath(request.url()),
    error: request.failure()?.errorText || 'failed',
    stage: report.stage,
  };
  if (offlineWindow && /INTERNET_DISCONNECTED|ERR_FAILED/i.test(item.error)) {
    report.browser.expectedOfflineFailedRequests.push(item);
  } else if (/ERR_ABORTED|NS_BINDING_ABORTED|cancel/i.test(item.error)) {
    report.browser.cancelledRequests.push(item);
  } else {
    report.browser.unexpectedFailedRequests.push(item);
  }
});
page.on('response', (response) => {
  if (response.status() < 400) return;
  const request = response.request();
  const item = {
    method: request.method(),
    path: safeRequestPath(response.url()),
    status: response.status(),
    stage: report.stage,
  };
  if (request.headers()['x-r1-qualification-probe'] === 'true') {
    report.browser.expectedProbeHttpErrors.push(item);
  } else if (conflictWindow && response.status() === 409) {
    report.browser.expectedConflictHttpErrors.push(item);
  } else {
    report.browser.unexpectedHttpErrors.push(item);
  }
});

try {
  trace('login_manager');
  const managerContext = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    serviceWorkers: 'block',
    locale: 'ru-RU',
    timezoneId: 'Asia/Tashkent',
  });
  const managerPage = await managerContext.newPage();
  managerPage.setDefaultTimeout(18000);
  const manager = await loginUi(managerPage, 'manager');
  assert.equal(manager.role, 'manager');

  trace('login_agronomist');
  const agronomist = await loginUi(page, 'agronomist');
  assert.deepEqual(
    { id: agronomist.id, role: agronomist.role, enterpriseId: agronomist.enterpriseId },
    { id: 3, role: 'agronomist', enterpriseId: 1 },
  );

  trace('service_worker_activation');
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller) {
      await new Promise((resolve) => {
        navigator.serviceWorker.addEventListener('controllerchange', resolve, { once: true });
        setTimeout(resolve, 3000);
      });
    }
  });
  if (!await page.evaluate(() => Boolean(navigator.serviceWorker.controller))) {
    await page.reload({ waitUntil: 'networkidle' });
  }
  report.serviceWorker.online = await page.evaluate(async () => ({
    controlled: Boolean(navigator.serviceWorker.controller),
    registrations: (await navigator.serviceWorker.getRegistrations()).map((registration) => ({
      scopePath: new URL(registration.scope).pathname,
      activeState: registration.active?.state || null,
      waitingState: registration.waiting?.state || null,
      installingState: registration.installing?.state || null,
    })),
  }));
  assert.equal(report.serviceWorker.online.controlled, true);
  assert.ok(report.serviceWorker.online.registrations.some((item) => item.activeState === 'activated'));

  trace('create_first_offline_inspection');
  const first = await createStartedInspection(managerPage, page, 'sync');
  await page.goto(`${frontendUrl}/inspections`, { waitUntil: 'networkidle' });
  await page.waitForFunction((title) => document.body.innerText.includes(title), first.title);
  await page.goto(`${frontendUrl}/inspections/${first.id}`, { waitUntil: 'networkidle' });
  await page.waitForFunction((title) => document.body.innerText.includes(title), first.title);
  await offlinePanel(page);
  await capture(page, 'agronomist', { width: 1440, height: 900 }, `/inspections/${first.id}`, 'online-offline-panel-ready');

  trace('attachment_limit');
  let section = await offlinePanel(page);
  await section.getByLabel('Фото-метаданные').check();
  await section.locator('input[type="file"]').setInputFiles({
    name: 'oversize-qualification.jpg',
    mimeType: 'image/jpeg',
    buffer: Buffer.alloc((25 * 1024 * 1024) + 1),
  });
  await section.getByText('Размер фотографии превышает 25 МиБ.').waitFor();
  report.limits.photoMaximumBytes = 25 * 1024 * 1024;
  report.limits.oversizePhotoRejected = true;

  trace('queue_while_offline');
  offlineWindow = true;
  await context.setOffline(true);
  await page.waitForFunction(() => navigator.onLine === false);
  await fillAndQueue(page, sensitiveDraftPhrase);
  const queuedDatabase = await readOfflineDatabase(page);
  assert.equal(queuedDatabase.present, true);
  assert.equal(queuedDatabase.stores.queue.length, 1);
  assert.equal(queuedDatabase.stores.drafts.length, 1);
  const firstQueued = queuedDatabase.stores.queue[0];
  const serialized = JSON.stringify(queuedDatabase.stores);
  const allRecords = Object.values(queuedDatabase.stores).flat();
  assert.ok(allRecords.length >= 3);
  assert.ok(allRecords.every((item) => item.scope === expectedScope));
  assert.equal(/access_?token|refresh_?token|authorization|cookie|password|jwt/i.test(serialized), false);
  assert.equal(containsBinary(queuedDatabase.stores), false);
  report.indexedDb.queued = {
    databasePresent: true,
    snapshotCount: queuedDatabase.stores.snapshots.length,
    draftCount: queuedDatabase.stores.drafts.length,
    queueCount: queuedDatabase.stores.queue.length,
    scopes: [...new Set(allRecords.map((item) => item.scope))],
    queueStatus: firstQueued.status,
    evidenceCount: firstQueued.evidence.length,
    evidenceTypes: firstQueued.evidence.map((item) => item.evidence_type),
    forbiddenCredentialFields: false,
    binaryValues: false,
    recordLimitBytes: 512 * 1024,
    evidenceLimit: 20,
  };

  trace('real_offline_reload');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByText('Показана последняя сохранённая версия осмотра.', { exact: false }).waitFor();
  section = await offlinePanel(page);
  await section.getByText('Изменения ещё не являются серверными данными.', { exact: false }).waitFor();
  const navigatorOnlineImmediatelyAfterReload = await page.evaluate(() => navigator.onLine);
  if (navigatorOnlineImmediatelyAfterReload) {
    // Chromium can report the service-worker navigation itself as reachable even though
    // every network request was blocked. Re-emit the browser offline transition so the
    // newly created document receives the same real network state as the context.
    await context.setOffline(false);
    await context.setOffline(true);
    await page.waitForFunction(() => navigator.onLine === false);
  }
  const databaseAfterReload = await readOfflineDatabase(page);
  assert.equal(databaseAfterReload.stores.queue.length, 1);
  assert.equal(databaseAfterReload.stores.drafts.length, 1);
  const cacheWhileOffline = await cacheAudit(page);
  assert.equal(cacheWhileOffline.apiEntryCount, 0);
  const routeAfterReload = new URL(page.url()).pathname;
  assert.equal(routeAfterReload, `/inspections/${first.id}`);
  report.offlineReload = {
    route: routeAfterReload,
    navigatorOnline: await page.evaluate(() => navigator.onLine),
    navigatorOnlineImmediatelyAfterServiceWorkerReload: navigatorOnlineImmediatelyAfterReload,
    offlineTransitionReemittedForNewDocument: navigatorOnlineImmediatelyAfterReload,
    controlledByServiceWorker: await page.evaluate(() => Boolean(navigator.serviceWorker.controller)),
    appShellVisible: await page.locator('#root').isVisible(),
    cachedDetailVisible: await page.getByText('Показана последняя сохранённая версия осмотра.', { exact: false }).isVisible(),
    queuedDraftVisible: await section.getByText('Изменения ещё не являются серверными данными.', { exact: false }).isVisible(),
    draftPersistedAcrossReload: databaseAfterReload.stores.drafts.length === 1,
    queuePersistedAcrossReload: databaseAfterReload.stores.queue.length === 1,
    cacheNames: cacheWhileOffline.cacheNames,
    cacheEntryCount: cacheWhileOffline.entryCount,
    cachedApiEntries: cacheWhileOffline.apiEntryCount,
  };
  assert.equal(report.offlineReload.navigatorOnline, false);
  assert.equal(report.offlineReload.controlledByServiceWorker, true);
  assert.equal(report.offlineReload.appShellVisible, true);
  await page.setViewportSize({ width: 1024, height: 768 });
  await capture(page, 'agronomist', { width: 1024, height: 768 }, `/inspections/${first.id}`, 'offline-reload-draft-retained');

  trace('offline_assigned_list');
  await page.goto(`${frontendUrl}/inspections`, { waitUntil: 'domcontentloaded' });
  await page.getByText('Показан последний сохранённый список.', { exact: false }).waitFor();
  await page.waitForFunction((title) => document.body.innerText.includes(title), first.title);
  const listNavigatorOnlineAfterServiceWorkerNavigation = await page.evaluate(() => navigator.onLine);
  if (listNavigatorOnlineAfterServiceWorkerNavigation) {
    await context.setOffline(false);
    await context.setOffline(true);
    await page.waitForFunction(() => navigator.onLine === false);
  }
  report.offlineReload.assignedListVisible = true;
  report.offlineReload.listNavigatorOnlineImmediatelyAfterServiceWorkerNavigation = listNavigatorOnlineAfterServiceWorkerNavigation;
  report.offlineReload.offlineTransitionReemittedForListDocument = listNavigatorOnlineAfterServiceWorkerNavigation;
  await page.getByText('Отправка не начнётся автоматически.', { exact: false }).waitFor();
  report.offlineReload.manualSyncOnlyCopyVisible = await page.getByText('Отправка не начнётся автоматически.', { exact: false }).isVisible();
  assert.equal(report.offlineReload.manualSyncOnlyCopyVisible, true);
  await page.goto(`${frontendUrl}/inspections/${first.id}`, { waitUntil: 'domcontentloaded' });
  await offlinePanel(page);

  trace('manual_reconnect_sync');
  await context.setOffline(false);
  offlineWindow = false;
  await page.waitForFunction(() => navigator.onLine === true);
  await page.waitForTimeout(500);
  section = await offlinePanel(page);
  const observedWrites = [];
  const recordWrite = (request) => {
    if (request.method() !== 'POST') return;
    const url = new URL(request.url());
    if (!url.pathname.startsWith(`/api/field-inspections/${first.id}/`)) return;
    observedWrites.push({
      path: url.pathname,
      hasIdempotencyKey: /^[A-Za-z0-9._:-]{8,64}$/.test(request.headers()['idempotency-key'] || ''),
    });
  };
  page.on('request', recordWrite);
  const syncResponsePaths = ['result', 'evidence', 'actions'];
  const synchronizedResponses = Promise.all(syncResponsePaths.map((suffix) => page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === 'POST'
      && url.pathname === `/api/field-inspections/${first.id}/${suffix}`;
  })));
  await section.getByRole('button', { name: 'Синхронизировать' }).click();
  const syncResponses = await synchronizedResponses;
  assert.deepEqual(syncResponses.map((response) => response.status()), [200, 200, 200]);
  await page.waitForFunction(async () => {
    const request = indexedDB.open('agrosat-offline-scouting', 1);
    const database = await new Promise((resolve) => { request.onsuccess = () => resolve(request.result); });
    const count = await new Promise((resolve) => {
      const query = database.transaction('queue', 'readonly').objectStore('queue').count();
      query.onsuccess = () => resolve(query.result);
    });
    database.close();
    return count === 0;
  });
  page.off('request', recordWrite);
  const closure = await browserApi(page, `/api/field-inspections/${first.id}/closure`);
  assert.equal(closure.status, 200);
  assert.equal(closure.payload.inspection.status, 'completed');
  assert.equal(closure.payload.result.cause_code, 'irrigation');
  assert.equal(closure.payload.evidence.length, 1);
  assert.equal(closure.payload.actions.length, 1);
  assert.equal(closure.payload.actions[0].owner.id, 3);
  const timelineBeforeReplay = await browserApi(page, `/api/field-inspections/${first.id}/timeline?limit=200`);
  assert.equal(timelineBeforeReplay.status, 200);
  const writePaths = observedWrites.map((item) => item.path);
  assert.deepEqual(writePaths, [
    `/api/field-inspections/${first.id}/result`,
    `/api/field-inspections/${first.id}/evidence`,
    `/api/field-inspections/${first.id}/actions`,
  ]);
  assert.ok(observedWrites.every((item) => item.hasIdempotencyKey));

  trace('duplicate_sync_idempotency_replay');
  const replayResult = await browserApi(page, `/api/field-inspections/${first.id}/result`, {
    method: 'POST',
    key: firstQueued.idempotency.result,
    body: { ...firstQueued.result, expected_version: firstQueued.baseVersion },
  });
  assert.equal(replayResult.status, 200);
  assert.equal(replayResult.payload.id, closure.payload.result.id);
  const replayEvidence = await browserApi(page, `/api/field-inspections/${first.id}/evidence`, {
    method: 'POST',
    key: firstQueued.idempotency.evidence[0],
    body: {
      ...firstQueued.evidence[0],
      result_id: closure.payload.result.id,
      expected_version: firstQueued.baseVersion + 1,
    },
  });
  assert.equal(replayEvidence.status, 200);
  assert.equal(replayEvidence.payload.id, closure.payload.evidence[0].id);
  const replayAction = await browserApi(page, `/api/field-inspections/${first.id}/actions`, {
    method: 'POST',
    key: firstQueued.idempotency.action,
    body: {
      ...firstQueued.action,
      result_id: closure.payload.result.id,
      expected_inspection_version: firstQueued.baseVersion + 2,
    },
  });
  assert.equal(replayAction.status, 200);
  assert.equal(replayAction.payload.id, closure.payload.actions[0].id);
  const timelineAfterReplay = await browserApi(page, `/api/field-inspections/${first.id}/timeline?limit=200`);
  assert.equal(timelineAfterReplay.payload.items.length, timelineBeforeReplay.payload.items.length);
  const databaseAfterSync = await readOfflineDatabase(page);
  assert.equal(databaseAfterSync.stores.queue.length, 0);
  assert.equal(databaseAfterSync.stores.drafts.length, 0);
  report.synchronization = {
    manualReconnect: true,
    requestSteps: observedWrites.map((item) => item.path.split('/').at(-1)),
    allRequestsHadIdempotencyKeys: true,
    queueAfterSuccess: databaseAfterSync.stores.queue.length,
    draftAfterSuccess: databaseAfterSync.stores.drafts.length,
    serverStatus: closure.payload.inspection.status,
    structuredHumanCause: closure.payload.result.cause_code,
    evidenceCount: closure.payload.evidence.length,
    actionCount: closure.payload.actions.length,
    actionOwnerId: closure.payload.actions[0].owner.id,
    duplicateReplay: {
      resultSameRecord: true,
      evidenceSameRecord: true,
      actionSameRecord: true,
      timelineCountBefore: timelineBeforeReplay.payload.items.length,
      timelineCountAfter: timelineAfterReplay.payload.items.length,
      duplicateAuditEvents: 0,
    },
  };

  trace('create_conflict_inspection');
  const second = await createStartedInspection(managerPage, page, 'conflict');
  await page.goto(`${frontendUrl}/inspections/${second.id}`, { waitUntil: 'networkidle' });
  await page.waitForFunction((title) => document.body.innerText.includes(title), second.title);
  await fillAndQueue(page, `${sensitiveDraftPhrase}-conflict`);
  const conflictQueuedDatabase = await readOfflineDatabase(page);
  assert.equal(conflictQueuedDatabase.stores.queue.length, 1);
  const queuedConflict = conflictQueuedDatabase.stores.queue[0];
  const serverBeforeMutation = await browserApi(managerPage, `/api/field-inspections/${second.id}`);
  const serverMutation = await browserApi(managerPage, `/api/field-inspections/${second.id}`, {
    method: 'PATCH',
    body: {
      expected_version: serverBeforeMutation.payload.version,
      instructions: 'Manager-side update used to prove a real optimistic version conflict.',
    },
  });
  assert.equal(serverMutation.status, 200);
  assert.equal(serverMutation.payload.version, queuedConflict.baseVersion + 1);

  trace('real_conflict_sync');
  conflictWindow = true;
  section = await offlinePanel(page);
  await section.getByRole('button', { name: 'Синхронизировать' }).click();
  await section.getByText('Конфликт: осмотр изменён на сервере.', { exact: false }).waitFor();
  await section.getByText('Серверные данные не перезаписаны.', { exact: false }).waitFor();
  conflictWindow = false;
  const conflictDatabase = await readOfflineDatabase(page);
  assert.equal(conflictDatabase.stores.queue.length, 1);
  assert.equal(conflictDatabase.stores.queue[0].status, 'conflict');
  assert.equal(conflictDatabase.stores.queue[0].failure.category, 'conflict');
  assert.equal(conflictDatabase.stores.queue[0].failure.retryable, false);
  const serverAfterConflict = await browserApi(managerPage, `/api/field-inspections/${second.id}/closure`);
  assert.equal(serverAfterConflict.status, 200);
  assert.equal(serverAfterConflict.payload.result, null);
  const serverInspectionAfterConflict = await browserApi(managerPage, `/api/field-inspections/${second.id}`);
  assert.equal(serverInspectionAfterConflict.payload.instructions, 'Manager-side update used to prove a real optimistic version conflict.');
  assert.equal(JSON.stringify(serverInspectionAfterConflict.payload).includes(sensitiveDraftPhrase), false);
  await page.setViewportSize({ width: 390, height: 844 });
  await section.getByRole('alert').scrollIntoViewIfNeeded();
  await capture(page, 'agronomist', { width: 390, height: 844 }, `/inspections/${second.id}`, 'real-version-conflict-not-overwritten');
  const mobileMetrics = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    visibleActionHeights: Array.from(
      document.querySelector('section[aria-labelledby="offline-draft-title"]')?.querySelectorAll('button') || [],
    ).filter((button) => button.offsetParent !== null).map((button) => Math.round(button.getBoundingClientRect().height)),
  }));
  assert.equal(mobileMetrics.scrollWidth, mobileMetrics.clientWidth);
  assert.ok(mobileMetrics.visibleActionHeights.every((height) => height >= 44));
  report.mobile = {
    viewport: '390x844',
    horizontalOverflow: false,
    minimumVisibleActionHeight: Math.min(...mobileMetrics.visibleActionHeights),
    conflictVisible: true,
  };
  report.conflict = {
    realServerMutation: true,
    queuedBaseVersion: queuedConflict.baseVersion,
    serverVersionAtSync: serverMutation.payload.version,
    responseStatus: 409,
    queueStatus: conflictDatabase.stores.queue[0].status,
    retryable: conflictDatabase.stores.queue[0].failure.retryable,
    conflictUiVisible: true,
    overwriteWarningVisible: true,
    serverResultCreated: false,
    managerMutationPreserved: true,
    localDraftNotWrittenToServer: true,
  };

  trace('cancel_conflict_fixture');
  const conflictCurrent = await browserApi(managerPage, `/api/field-inspections/${second.id}`);
  const cancelled = await browserApi(managerPage, `/api/field-inspections/${second.id}/cancel`, {
    method: 'POST',
    body: {
      expected_version: conflictCurrent.payload.version,
      cancellation_reason: 'Real conflict evidence captured; isolated qualification record closed.',
    },
  });
  assert.equal(cancelled.status, 200);

  trace('logout_purge');
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole('button', { name: 'Закрыть осмотр' }).click();
  await page.getByRole('button', { name: 'Выйти' }).click();
  await page.waitForURL(/\/login$/);
  await page.waitForFunction(async () => {
    const databases = typeof indexedDB.databases === 'function' ? await indexedDB.databases() : [];
    return !localStorage.getItem('agrosat_token')
      && !localStorage.getItem('agrosat_user')
      && !databases.some((item) => item.name === 'agrosat-offline-scouting');
  });
  const purgeCacheAudit = await cacheAudit(page);
  const purge = await page.evaluate(async () => ({
    tokenPresent: Boolean(localStorage.getItem('agrosat_token')),
    userPresent: Boolean(localStorage.getItem('agrosat_user')),
    databasePresent: typeof indexedDB.databases === 'function'
      ? (await indexedDB.databases()).some((item) => item.name === 'agrosat-offline-scouting')
      : null,
    activeObjectUrls: window.__r1ObjectUrls?.size || 0,
  }));
  assert.equal(purge.tokenPresent, false);
  assert.equal(purge.userPresent, false);
  assert.equal(purge.databasePresent, false);
  assert.equal(purge.activeObjectUrls, 0);
  assert.equal(purgeCacheAudit.apiEntryCount, 0);
  report.logoutPurge = {
    ...purge,
    tenantUserDraftsPurged: true,
    queuedConflictPurged: true,
    sensitiveApiCacheEntries: purgeCacheAudit.apiEntryCount,
    shellCacheRetained: purgeCacheAudit.cacheNames.length > 0,
  };

  trace('same_profile_second_user');
  const viewer = await loginUi(page, 'viewer');
  assert.equal(viewer.role, 'viewer');
  assert.equal(viewer.id, 4);
  const viewerDatabase = await readOfflineDatabase(page);
  const viewerBody = await page.locator('body').innerText();
  assert.equal(Object.values(viewerDatabase.stores).flat().length, 0);
  assert.equal(viewerBody.includes(sensitiveDraftPhrase), false);
  report.secondUser = {
    sameBrowserContext: true,
    role: viewer.role,
    userId: viewer.id,
    priorOfflineRecordsVisible: false,
    priorDraftPhraseVisible: false,
    databaseRecordCount: 0,
  };
  await capture(page, 'viewer', { width: 1440, height: 900 }, new URL(page.url()).pathname, 'same-profile-after-agronomist-logout');

  trace('final_assertions');
  report.serviceWorker.offline = {
    controlled: report.offlineReload.controlledByServiceWorker,
    shellReloaded: report.offlineReload.appShellVisible,
    apiCacheEntries: report.offlineReload.cachedApiEntries,
  };
  report.indexedDb.partition = {
    expectedScope,
    observedScopes: report.indexedDb.queued.scopes,
    allRecordsMatchedTenantAndUser: true,
    secondUserRecordCount: report.secondUser.databaseRecordCount,
  };
  assert.equal(report.browser.consoleErrors.length, 0);
  assert.equal(report.browser.pageErrors.length, 0);
  assert.equal(report.browser.unexpectedFailedRequests.length, 0);
  assert.equal(report.browser.unexpectedHttpErrors.length, 0);
  assert.ok(report.browser.expectedConflictHttpErrors.some((item) => item.status === 409));
  report.status = 'PASS';
  report.marker = 'PASS_REAL_OFFLINE_SCOUTING_AND_CONFLICT';
  report.stage = 'complete';
  report.completedAt = new Date().toISOString();
  await atomicJson('OFFLINE_SCOUTING_QUALIFICATION.json', report);
  await atomicJson('OFFLINE_SCREENSHOT_INDEX.json', {
    schemaVersion: 1,
    exactHead: report.exactHead,
    screenshots: report.browser.screenshots,
  });
  process.stdout.write(`${report.marker}\n`);
  process.stdout.write(`OFFLINE_SCREENSHOTS=${report.browser.screenshots.length}\n`);
  process.stdout.write('PRODUCTION_WRITES=0\n');
  await managerContext.close();
} catch (error) {
  report.status = 'FAIL';
  report.marker = 'FAIL_REAL_OFFLINE_SCOUTING_AND_CONFLICT';
  report.failure = safeFailure(error);
  report.failedAt = new Date().toISOString();
  await atomicJson('OFFLINE_SCOUTING_QUALIFICATION_FAILURE.json', report);
  throw error;
} finally {
  await context.setOffline(false).catch(() => {});
  await context.close().catch(() => {});
  await browser.close().catch(() => {});
}
