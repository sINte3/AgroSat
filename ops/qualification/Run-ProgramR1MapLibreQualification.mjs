import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
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
import { promisify } from 'node:util';


const execFileAsync = promisify(execFile);
const requiredEnvironment = [
  'R1_CREDENTIALS_PATH',
  'R1_EVIDENCE_DIR',
  'R1_PLAYWRIGHT_PACKAGE_ROOT',
  'R1_CHROMIUM_EXECUTABLE',
  'R1_FRONTEND_URL',
  'R1_BACKEND_URL',
  'R1_EXPECTED_HEAD',
  'R1_WORKTREE_ROOT',
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
const worktreeRoot = path.resolve(process.env.R1_WORKTREE_ROOT);
const frontendUrl = new URL(process.env.R1_FRONTEND_URL).origin;
const backendUrl = new URL(process.env.R1_BACKEND_URL).origin;
for (const value of [frontendUrl, backendUrl]) {
  if (new URL(value).hostname !== '127.0.0.1') {
    throw new Error('Qualification endpoints must be loopback-only.');
  }
}

const gitHead = (await execFileAsync('git', ['rev-parse', 'HEAD'], {
  cwd: worktreeRoot,
  windowsHide: true,
  timeout: 10000,
})).stdout.trim();
assert.equal(gitHead, process.env.R1_EXPECTED_HEAD, 'Qualification HEAD is not exact');
const worktreeDirty = Boolean((await execFileAsync('git', ['status', '--porcelain'], {
  cwd: worktreeRoot,
  windowsHide: true,
  timeout: 10000,
})).stdout.trim());
const allowDirtyRepairCandidate = process.env.R1_ALLOW_DIRTY_REPAIR_CANDIDATE === 'true';
if (worktreeDirty && !allowDirtyRepairCandidate) {
  throw new Error('Qualification requires a clean worktree unless an explicit repair-candidate run is requested.');
}

await mkdir(evidenceDir, { recursive: true });
const screenshotDir = path.join(evidenceDir, 'screenshots');
await mkdir(screenshotDir, { recursive: true });
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const requireFromPlaywright = createRequire(
  path.join(path.resolve(process.env.R1_PLAYWRIGHT_PACKAGE_ROOT), 'package.json'),
);
const { chromium } = requireFromPlaywright('playwright-core');

const ONE_PIXEL_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
);
const MAP_STUB_HOSTS = new Set([
  'tile.openstreetmap.org',
  'server.arcgisonline.com',
  'demotiles.maplibre.org',
]);
const MAP_CYCLES = 25;
const HEAP_ABSOLUTE_BUDGET = 50 * 1024 * 1024;

const report = {
  schemaVersion: 1,
  recordedAt: new Date().toISOString(),
  exactHead: process.env.R1_EXPECTED_HEAD,
  worktreeState: worktreeDirty ? 'dirty_repair_candidate' : 'clean_exact_head',
  runtimeClass: 'isolated_ephemeral_real_chromium_maplibre_fastapi_postgis_redis',
  status: 'running',
  marker: null,
  stage: 'initializing',
  vectorViewport: {},
  cycles: [],
  raster: {},
  staleAsync: {},
  anomalyWorkflow: {},
  resumableCleanup: [],
  drillDown: {},
  performance: {},
  browser: {
    viewport: '1440x900',
    screenshots: [],
    consoleErrors: [],
    pageErrors: [],
    unexpectedFailedRequests: [],
    cancelledRequests: [],
    unexpectedHttpErrors: [],
    externalAssetsStubbed: 0,
  },
  network: {
    requests: [],
    responseSizes: [],
    requestCounts: {},
    unboundedAllFieldGeoJsonRequests: 0,
  },
  limitations: [
    'V8 JSHeapUsedSize excludes GPU memory and operating-system allocations; worker termination and DOM resource counts are measured separately.',
    'External basemap tiles and glyphs are deterministic local responses; vector tiles, raster APIs, field APIs, and anomaly APIs are real isolated backend responses.',
  ],
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
    .slice(0, 5000);
}

function safeRequestPath(url) {
  const parsed = new URL(url);
  return parsed.origin === frontendUrl || parsed.origin === backendUrl
    ? `${parsed.pathname}${parsed.search}`
    : `${parsed.hostname}${parsed.pathname}`;
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
    return { status: response.status, payload };
  }, {
    requestPath: pathname,
    requestMethod: method,
    requestBody: body,
    idempotencyKey: key,
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
    return { id: user.id, role: user.role, enterpriseId: user.enterprise_id };
  });
}

async function resourceSnapshot(page) {
  return page.evaluate(() => ({
    maps: document.querySelectorAll('.maplibregl-map').length,
    canvases: document.querySelectorAll('.maplibregl-canvas').length,
    controlContainers: document.querySelectorAll('.maplibregl-control-container').length,
    controls: document.querySelectorAll('.maplibregl-ctrl').length,
    popups: document.querySelectorAll('.maplibregl-popup').length,
    workersActive: window.__r1MapProbe?.workers.active.size || 0,
    workersCreated: window.__r1MapProbe?.workers.created || 0,
    workersTerminated: window.__r1MapProbe?.workers.terminated || 0,
    workerSchemes: Array.from(window.__r1MapProbe?.workers.schemes || []),
    objectUrlsActive: window.__r1MapProbe?.objectUrls.active.size || 0,
    objectUrlsCreated: window.__r1MapProbe?.objectUrls.created || 0,
    objectUrlsRevoked: window.__r1MapProbe?.objectUrls.revoked || 0,
    activeDomListeners: window.__r1MapProbe?.listeners.active || 0,
    activeGlobalDomListeners: window.__r1MapProbe?.listeners.activeGlobal || 0,
    activeMapDomListeners: window.__r1MapProbe?.listeners.activeMapLike || 0,
    mapRegion: (() => {
      const node = document.querySelector('[aria-label="Интерактивная карта полей"]');
      return node ? {
        spatialStatus: node.dataset.spatialStatus || null,
        fieldSourceType: node.dataset.fieldSourceType || null,
        fieldLayerCount: Number(node.dataset.fieldLayerCount || 0),
      } : null;
    })(),
  }));
}

async function waitForMapRemoved(page) {
  await page.waitForFunction(() => (
    document.querySelectorAll('.maplibregl-map').length === 0
    && document.querySelectorAll('.maplibregl-canvas').length === 0
    && document.querySelectorAll('.maplibregl-control-container').length === 0
    && (window.__r1MapProbe?.objectUrls.active.size || 0) === 0
  ), null, { timeout: 15000 });
  await page.waitForTimeout(250);
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

await context.addInitScript(() => {
  const probe = {
    objectUrls: { active: new Set(), created: 0, revoked: 0 },
    workers: { active: new Set(), created: 0, terminated: 0, schemes: new Set() },
    listeners: { active: 0, activeGlobal: 0, activeMapLike: 0 },
  };
  window.__r1MapProbe = probe;

  const nativeCreateObjectUrl = URL.createObjectURL.bind(URL);
  const nativeRevokeObjectUrl = URL.revokeObjectURL.bind(URL);
  URL.createObjectURL = (value) => {
    const url = nativeCreateObjectUrl(value);
    probe.objectUrls.active.add(url);
    probe.objectUrls.created += 1;
    return url;
  };
  URL.revokeObjectURL = (url) => {
    if (probe.objectUrls.active.delete(url)) probe.objectUrls.revoked += 1;
    return nativeRevokeObjectUrl(url);
  };

  const NativeWorker = window.Worker;
  window.Worker = class ProgramR1Worker extends NativeWorker {
    constructor(url, options) {
      super(url, options);
      probe.workers.active.add(this);
      probe.workers.created += 1;
      const stringUrl = String(url);
      probe.workers.schemes.add(stringUrl.split(':', 1)[0] || 'relative');
    }

    terminate() {
      if (probe.workers.active.delete(this)) probe.workers.terminated += 1;
      return super.terminate();
    }
  };

  const nativeAdd = EventTarget.prototype.addEventListener;
  const nativeRemove = EventTarget.prototype.removeEventListener;
  const targetRecords = new WeakMap();
  const captureValue = (options) => typeof options === 'boolean' ? options : Boolean(options?.capture);
  const mapLike = (target) => {
    if (!(target instanceof Element)) return false;
    return String(target.className || '').includes('maplibregl')
      || Boolean(target.closest?.('.maplibregl-map'));
  };
  EventTarget.prototype.addEventListener = function addEventListener(type, listener, options) {
    if (listener) {
      const records = targetRecords.get(this) || [];
      const record = {
        type,
        listener,
        capture: captureValue(options),
        global: this === window || this === document,
        mapLike: mapLike(this),
        active: true,
      };
      records.push(record);
      targetRecords.set(this, records);
      probe.listeners.active += 1;
      if (record.global) probe.listeners.activeGlobal += 1;
      if (record.mapLike) probe.listeners.activeMapLike += 1;
    }
    return nativeAdd.call(this, type, listener, options);
  };
  EventTarget.prototype.removeEventListener = function removeEventListener(type, listener, options) {
    const records = targetRecords.get(this) || [];
    const capture = captureValue(options);
    const record = [...records].reverse().find((candidate) => (
      candidate.active
      && candidate.type === type
      && candidate.listener === listener
      && candidate.capture === capture
    ));
    if (record) {
      record.active = false;
      probe.listeners.active -= 1;
      if (record.global) probe.listeners.activeGlobal -= 1;
      if (record.mapLike) probe.listeners.activeMapLike -= 1;
    }
    return nativeRemove.call(this, type, listener, options);
  };
});

await context.route('**/*', async (route) => {
  const parsed = new URL(route.request().url());
  if (parsed.hostname === 'fonts.googleapis.com') {
    return route.fulfill({ status: 200, contentType: 'text/css', body: '' });
  }
  if (parsed.hostname === 'fonts.gstatic.com') {
    return route.fulfill({ status: 204, body: '' });
  }
  if (!MAP_STUB_HOSTS.has(parsed.hostname)) return route.continue();
  report.browser.externalAssetsStubbed += 1;
  if (parsed.pathname.endsWith('.pbf')) {
    return route.fulfill({ status: 200, contentType: 'application/x-protobuf', body: Buffer.alloc(0) });
  }
  return route.fulfill({ status: 200, contentType: 'image/png', body: ONE_PIXEL_PNG });
});

const page = await context.newPage();
page.setDefaultTimeout(18000);
const responseSizeTasks = [];
page.on('console', (message) => {
  if (message.type() === 'error') {
    report.browser.consoleErrors.push({ stage: report.stage, text: message.text().slice(0, 400) });
  }
});
page.on('pageerror', (error) => report.browser.pageErrors.push({
  stage: report.stage, text: String(error).slice(0, 400),
}));
page.on('request', (request) => {
  const item = {
    stage: report.stage,
    method: request.method(),
    path: safeRequestPath(request.url()),
    resourceType: request.resourceType(),
  };
  report.network.requests.push(item);
  if (/\/api\/fields\/geojson\/all/i.test(item.path)) {
    report.network.unboundedAllFieldGeoJsonRequests += 1;
  }
});
page.on('requestfailed', (request) => {
  const item = {
    stage: report.stage,
    method: request.method(),
    path: safeRequestPath(request.url()),
    error: request.failure()?.errorText || 'failed',
  };
  if (/ERR_ABORTED|NS_BINDING_ABORTED|cancel/i.test(item.error)) {
    report.browser.cancelledRequests.push(item);
  } else {
    report.browser.unexpectedFailedRequests.push(item);
  }
});
page.on('response', (response) => {
  const request = response.request();
  const item = {
    stage: report.stage,
    method: request.method(),
    path: safeRequestPath(response.url()),
    status: response.status(),
  };
  if (response.status() >= 400) {
    if (request.headers()['x-r1-qualification-probe'] !== 'true') {
      report.browser.unexpectedHttpErrors.push(item);
    }
  }
  const parsed = new URL(response.url());
  if (parsed.origin === frontendUrl && parsed.pathname.startsWith('/api/')) {
    const task = response.body()
      .then((body) => report.network.responseSizes.push({
        path: parsed.pathname,
        bytes: body.length,
        status: response.status(),
      }))
      .catch(() => {});
    responseSizeTasks.push(task);
  }
});

const cdp = await context.newCDPSession(page);
await cdp.send('Performance.enable');
async function collectHeap() {
  await cdp.send('HeapProfiler.collectGarbage');
  await page.waitForTimeout(100);
  const metrics = await cdp.send('Performance.getMetrics');
  const value = metrics.metrics.find((item) => item.name === 'JSHeapUsedSize')?.value;
  assert.ok(Number.isFinite(value));
  return Math.round(value);
}

async function openFieldsFromNavigation() {
  const navigation = page.getByRole('navigation', { name: 'Основная навигация' });
  await navigation.getByRole('button', { name: 'Поля', exact: true }).click();
  await page.waitForURL(/\/fields$/);
  const region = page.getByRole('region', { name: 'Интерактивная карта полей' });
  await region.waitFor();
  await page.waitForFunction(() => (
    document.querySelector('[aria-label="Интерактивная карта полей"]')?.dataset.spatialStatus === 'ready'
  ));
  return region;
}

async function leaveMapForDashboard() {
  const navigation = page.getByRole('navigation', { name: 'Основная навигация' });
  await navigation.getByRole('button', { name: 'Сегодня', exact: true }).click();
  await page.waitForURL(/\/dashboard$/);
  await waitForMapRemoved(page);
}

try {
  trace('login_manager');
  const manager = await loginUi(page, 'manager');
  assert.deepEqual(manager, { id: 2, role: 'manager', enterpriseId: 1 });
  const previousQualificationInspections = await browserApi(
    page,
    '/api/field-inspections?field_id=2&limit=50',
  );
  assert.equal(previousQualificationInspections.status, 200);
  for (const inspection of previousQualificationInspections.payload.items.filter((item) => (
    item.title === 'PROGRAM R1 anomaly GIS qualification'
    && ['pending', 'in_progress'].includes(item.status)
  ))) {
    const cleanupResponse = await browserApi(page, `/api/field-inspections/${inspection.id}/cancel`, {
      method: 'POST',
      body: {
        expected_version: inspection.version,
        cancellation_reason: 'Resumable isolated PROGRAM R1 qualification cleanup.',
      },
    });
    assert.equal(cleanupResponse.status, 200);
    report.resumableCleanup.push({
      resource: 'isolated_field_inspection',
      id: inspection.id,
      previousStatus: inspection.status,
      finalStatus: cleanupResponse.payload.status,
      productionWrites: 0,
    });
  }
  const fieldsResponse = await browserApi(page, '/api/fields/?include_ndvi=true');
  assert.equal(fieldsResponse.status, 200);
  const fields = fieldsResponse.payload;
  const fieldOne = fields.find((item) => Number(item.id) === 1);
  const fieldTwo = fields.find((item) => Number(item.id) === 2);
  assert.ok(fieldOne?.name && fieldTwo?.name);

  trace('map_warmup');
  await openFieldsFromNavigation();
  const warmMap = await resourceSnapshot(page);
  assert.equal(warmMap.maps, 1);
  assert.equal(warmMap.canvases, 1);
  assert.equal(warmMap.mapRegion.fieldSourceType, 'vector');
  assert.ok(warmMap.mapRegion.fieldLayerCount > 0 && warmMap.mapRegion.fieldLayerCount <= 4);
  assert.equal(warmMap.objectUrlsActive, 0);
  assert.ok(warmMap.workersActive > 0);
  assert.ok(warmMap.workerSchemes.every((scheme) => scheme !== 'blob'));
  await leaveMapForDashboard();
  const warmRemoved = await resourceSnapshot(page);
  report.vectorViewport.warmup = { alive: warmMap, afterUnmount: warmRemoved };
  assert.equal(warmRemoved.maps, 0);
  const expectedSharedWorkerCount = warmRemoved.workersActive;
  assert.equal(expectedSharedWorkerCount, 1);
  assert.equal(warmRemoved.workersCreated, 1);
  assert.equal(warmRemoved.objectUrlsActive, 0);
  const baselineHeap = await collectHeap();
  const baselineGlobalListeners = warmRemoved.activeGlobalDomListeners;

  trace('twenty_five_map_cycles');
  let expectedControlCount = null;
  let maximumVectorLayers = 0;
  let maximumWorkers = 0;
  let maximumMapListeners = 0;
  for (let cycle = 1; cycle <= MAP_CYCLES; cycle += 1) {
    report.stage = `map_cycle_${cycle}`;
    const requestStart = report.network.requests.length;
    const region = await openFieldsFromNavigation();
    const panel = page.locator('#field-list-panel');
    const selectedField = cycle % 2 === 0 ? fieldTwo : fieldOne;
    await panel.getByText(selectedField.name, { exact: true }).click();
    await page.locator('div.w-96.flex-shrink-0').waitFor();
    const mapOwner = page.getByRole('region', { name: 'Интерактивная карта полей' }).locator('xpath=..');

    for (const mode of ['NDVI', 'SAVI', 'EVI', 'NDMI', 'NDRE', 'Покрытие', 'Актуальность', 'Культуры']) {
      await mapOwner.getByRole('button', { name: mode, exact: true }).click();
    }

    if (cycle === 1 || cycle === 13 || cycle === 25) {
      for (const style of ['Карта', 'Гибрид', 'Спутник']) {
        const button = page.getByRole('button', { name: style, exact: true });
        await button.click();
        await expectPressed(button);
      }
    }

    if (cycle === 1) {
      await panel.getByText(fieldTwo.name, { exact: true }).click();
      const rasterToggle = page.getByLabel('Пиксельный NDVI');
      await rasterToggle.check();
      await page.getByText('Фактическая дата снимка: 2026-07-28', { exact: false }).waitFor();
      const rasterReady = await resourceSnapshot(page);
      assert.equal(rasterReady.objectUrlsActive, 1);
      const rasterDate = page.getByLabel('Снимок не позднее');
      await rasterDate.fill('2026-07-28');
      const opacity = page.getByLabel('Прозрачность слоя');
      await opacity.fill('0.45');
      for (const style of ['Карта', 'Гибрид', 'Спутник']) {
        const button = page.getByRole('button', { name: style, exact: true });
        await button.click();
        await expectPressed(button);
      }
      const rasterAfterStyles = await resourceSnapshot(page);
      assert.equal(rasterAfterStyles.objectUrlsActive, 1);
      await capture(page, 'manager', { width: 1440, height: 900 }, '/fields', 'vector-map-raster-style-reload');
      await rasterToggle.uncheck();
      await page.waitForFunction(() => (window.__r1MapProbe?.objectUrls.active.size || 0) === 0);
      report.raster = {
        fieldId: 2,
        actualObservationDate: '2026-07-28',
        metadataAndImageFromRealIsolatedApi: true,
        objectUrlsWhileEnabled: 1,
        objectUrlsAfterDisable: 0,
        styleReloadsWithRaster: 3,
        staleGenerationGuardExercised: true,
      };
    }

    if (cycle % 5 === 0) {
      for (const viewport of [
        { width: 1024, height: 768 },
        { width: 390, height: 844 },
        { width: 1440, height: 900 },
      ]) {
        await page.setViewportSize(viewport);
        await page.waitForTimeout(40);
      }
    }

    const alive = await resourceSnapshot(page);
    assert.equal(alive.maps, 1);
    assert.equal(alive.canvases, 1);
    assert.equal(alive.controlContainers, 1);
    assert.equal(alive.popups, 0);
    assert.equal(alive.objectUrlsActive, 0);
    assert.equal(alive.mapRegion.fieldSourceType, 'vector');
    assert.ok(alive.mapRegion.fieldLayerCount > 0 && alive.mapRegion.fieldLayerCount <= 4);
    if (expectedControlCount === null) expectedControlCount = alive.controls;
    assert.equal(alive.controls, expectedControlCount, `Map controls duplicated in cycle ${cycle}`);
    maximumVectorLayers = Math.max(maximumVectorLayers, alive.mapRegion.fieldLayerCount);
    maximumWorkers = Math.max(maximumWorkers, alive.workersActive);
    maximumMapListeners = Math.max(maximumMapListeners, alive.activeMapDomListeners);
    if (cycle === 13 || cycle === 25) {
      await capture(page, 'manager', { width: 1440, height: 900 }, '/fields', `map-cycle-${cycle}`);
    }

    await leaveMapForDashboard();
    const removed = await resourceSnapshot(page);
    assert.equal(removed.maps, 0);
    assert.equal(removed.canvases, 0);
    assert.equal(removed.controlContainers, 0);
    assert.equal(removed.controls, 0);
    assert.equal(removed.popups, 0);
    assert.equal(removed.workersActive, expectedSharedWorkerCount);
    assert.equal(removed.workersCreated, expectedSharedWorkerCount);
    assert.equal(removed.objectUrlsActive, 0);
    assert.ok(removed.activeGlobalDomListeners <= baselineGlobalListeners + 1);
    const cycleRequests = report.network.requests.slice(requestStart);
    report.cycles.push({
      cycle,
      alive: {
        maps: alive.maps,
        canvases: alive.canvases,
        controls: alive.controls,
        workers: alive.workersActive,
        objectUrls: alive.objectUrlsActive,
        vectorLayers: alive.mapRegion.fieldLayerCount,
      },
      afterUnmount: {
        maps: removed.maps,
        canvases: removed.canvases,
        controls: removed.controls,
        sharedWorkers: removed.workersActive,
        unexpectedWorkerDuplicates: removed.workersActive - expectedSharedWorkerCount,
        objectUrls: removed.objectUrlsActive,
        globalDomListeners: removed.activeGlobalDomListeners,
      },
      requests: cycleRequests.length,
      vectorTileRequests: cycleRequests.filter((item) => item.path.includes('/api/field-tiles/')).length,
      selectedFieldId: selectedField.id,
    });
  }

  trace('map_heap_after_cycles');
  const finalHeap = await collectHeap();
  const heapGrowth = Math.max(0, finalHeap - baselineHeap);
  const heapBudget = Math.max(HEAP_ABSOLUTE_BUDGET, Math.round(baselineHeap * 0.25));
  assert.ok(heapGrowth <= heapBudget, `Stabilized heap growth ${heapGrowth} exceeds ${heapBudget}`);
  const listenersAfterCycles = (await resourceSnapshot(page)).activeGlobalDomListeners;
  assert.ok(
    listenersAfterCycles <= baselineGlobalListeners + 1,
    `Persistent DOM listener count grew from ${baselineGlobalListeners} to ${listenersAfterCycles}`,
  );
  report.performance = {
    method: 'Chrome DevTools Protocol Performance.JSHeapUsedSize after HeapProfiler.collectGarbage',
    baselineHeapBytes: baselineHeap,
    finalHeapBytes: finalHeap,
    stabilizedGrowthBytes: heapGrowth,
    stabilizedGrowthPct: baselineHeap ? Number(((heapGrowth / baselineHeap) * 100).toFixed(2)) : null,
    budgetBytes: heapBudget,
    budgetRule: '<= 50 MiB or <= 25% of baseline, whichever is larger',
    cycles: MAP_CYCLES,
    maximumMapWorkers: maximumWorkers,
    expectedSharedWorkerCount,
    unexpectedWorkerDuplicates: 0,
    sharedWorkerClassification: 'MapLibre 4.7 global RTL dispatcher singleton retained for the SPA document; no per-cycle growth',
    maximumMapDomListeners: maximumMapListeners,
    expectedControlCount,
    maximumVectorLayers,
    mapResourcesAfterEveryUnmount: 0,
  };

  trace('stale_index_response_probe');
  const delayed = [];
  const staleRoute = async (route) => {
    const url = new URL(route.request().url());
    const code = url.searchParams.get('index_code');
    if (code === 'savi') {
      delayed.push({ code, delayMs: 900, path: url.pathname });
      await new Promise((resolve) => setTimeout(resolve, 900));
    } else if (code === 'ndre') {
      delayed.push({ code, delayMs: 40, path: url.pathname });
      await new Promise((resolve) => setTimeout(resolve, 40));
    }
    await route.continue();
  };
  await page.route('**/api/satellite-indices/1/**', staleRoute);
  await page.goto(`${frontendUrl}/fields/1`, { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: fieldOne.name }).waitFor();
  await page.getByRole('tab', { name: 'Индексы', exact: true }).click();
  await page.getByRole('button', { name: 'SAVI', exact: true }).click();
  await page.waitForTimeout(25);
  await page.getByRole('button', { name: 'NDRE', exact: true }).click();
  await page.waitForTimeout(1250);
  const staleIndexDom = await page.evaluate(() => ({
    ndreSelected: Array.from(document.querySelectorAll('button')).some((button) => (
      button.textContent.trim() === 'NDRE' && button.className.includes('bg-agro-accent')
    )),
    ndreRecordVisible: document.body.innerText.includes('Последний NDRE (NDRE)'),
    saviRecordVisible: document.body.innerText.includes('Последний SAVI (SAVI)'),
  }));
  report.staleAsync.indexSwitch = {
    delayedRequests: delayed,
    selectedIndex: 'ndre',
    ...staleIndexDom,
    staleSaviRendered: staleIndexDom.saviRecordVisible,
  };
  assert.equal(staleIndexDom.ndreSelected, true);
  assert.equal(staleIndexDom.ndreRecordVisible, true);
  assert.equal(staleIndexDom.saviRecordVisible, false, 'Stale SAVI response rendered after NDRE selection');
  await page.unroute('**/api/satellite-indices/1/**', staleRoute);

  trace('direct_routes_and_enterprise_drilldown');
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(new URL(page.url()).pathname, '/fields/1');
  await page.goto(`${frontendUrl}/enterprises/1`, { waitUntil: 'networkidle' });
  await page.getByText(fieldTwo.name, { exact: true }).waitFor();
  await page.getByText(fieldTwo.name, { exact: true }).click();
  await page.getByRole('heading', { name: `NDVI История — ${fieldTwo.name}` }).waitFor();
  report.drillDown = {
    directFieldRouteRefresh: true,
    enterpriseRoute: '/enterprises/1',
    enterpriseFieldRowOpenedHistory: true,
    fieldId: 2,
  };
  await capture(page, 'manager', { width: 1440, height: 900 }, '/enterprises/1', 'enterprise-field-drilldown');
  await page.locator('button').filter({ hasText: '×' }).last().click();

  trace('insert_non_live_anomaly_fixture');
  const fixtureEvidence = path.join(evidenceDir, 'PIXEL_ANOMALY_FIXTURE.json');
  const helper = path.join(worktreeRoot, 'ops', 'qualification', 'Insert-ProgramR1PixelAnomalyFixture.py');
  const helperResult = await execFileAsync(
    'C:\\Program Files\\Python314\\python.exe',
    [
      helper,
      '--credentials', credentialsPath,
      '--evidence', fixtureEvidence,
      '--field-id', '2',
      '--port', '55439',
    ],
    { cwd: worktreeRoot, windowsHide: true, timeout: 30000 },
  );
  const fixtureResult = JSON.parse(helperResult.stdout);
  assert.equal(fixtureResult.productionWrites, 0);
  assert.equal(fixtureResult.liveSentinelEvidence, false);

  trace('anomaly_polygon_to_inspection');
  await page.goto(`${frontendUrl}/fields/2/analytics`, { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: /Пиксельные аномалии/ }).waitFor();
  const fixtureZone = page.getByRole('button', { name: new RegExp(`Зона #${fixtureResult.anomalyId}`) });
  await fixtureZone.click();
  await page.getByRole('region', { name: `Карта выбранной аномальной зоны ${fixtureResult.anomalyId}` }).waitFor();
  await page.getByText('Причину необходимо подтвердить полевым осмотром.', { exact: false }).waitFor();
  const anomalyMap = await resourceSnapshot(page);
  assert.equal(anomalyMap.maps, 1);
  assert.equal(anomalyMap.canvases, 1);
  const createButton = page.getByRole('button', { name: 'Создать осмотр', exact: true });
  await createButton.click();
  const dialog = page.getByRole('dialog', { name: 'Создать полевой осмотр' });
  await dialog.waitFor();
  const initialFocusName = await page.evaluate(() => document.activeElement?.getAttribute('aria-label'));
  assert.equal(initialFocusName, 'Закрыть');
  await dialog.getByLabel('Заголовок').fill('PROGRAM R1 anomaly GIS qualification');
  const createResponsePromise = page.waitForResponse((response) => (
    response.request().method() === 'POST'
    && new URL(response.url()).pathname === `/api/pixel-anomalies/${fixtureResult.anomalyId}/inspection`
  ));
  await dialog.getByRole('button', { name: 'Создать осмотр', exact: true }).click();
  const createResponse = await createResponsePromise;
  assert.ok([200, 201].includes(createResponse.status()));
  const createPayload = await createResponse.json();
  const inspectionId = createPayload.inspection.id;
  await page.getByText(new RegExp(`Осмотр #${inspectionId}`)).waitFor();
  await capture(page, 'manager', { width: 1440, height: 900 }, '/fields/2/analytics', 'anomaly-zone-inspection-created');
  report.anomalyWorkflow = {
    fieldId: 2,
    anomalyId: fixtureResult.anomalyId,
    polygonSelected: true,
    anomalyMapVisible: true,
    measurementNotDiagnosisCopyVisible: true,
    dialogAccessibleName: true,
    initialDialogFocus: 'Закрыть',
    inspectionCreated: true,
    inspectionId,
    productionWrites: 0,
    isolatedDatabaseWritesOnly: true,
    liveSentinelEvidence: false,
  };

  trace('cleanup_anomaly_inspection');
  const inspection = await browserApi(page, `/api/field-inspections/${inspectionId}`);
  assert.equal(inspection.status, 200);
  const cancelled = await browserApi(page, `/api/field-inspections/${inspectionId}/cancel`, {
    method: 'POST',
    body: {
      expected_version: inspection.payload.version,
      cancellation_reason: 'Isolated anomaly-to-inspection qualification completed.',
    },
  });
  assert.equal(cancelled.status, 200);
  await leaveMapForDashboard();
  const afterAnomalyUnmount = await resourceSnapshot(page);
  assert.equal(afterAnomalyUnmount.maps, 0);
  assert.equal(afterAnomalyUnmount.canvases, 0);
  assert.equal(afterAnomalyUnmount.controlContainers, 0);
  assert.equal(afterAnomalyUnmount.controls, 0);
  assert.equal(afterAnomalyUnmount.popups, 0);
  assert.ok([0, 1].includes(afterAnomalyUnmount.workersActive));
  assert.equal(afterAnomalyUnmount.workersCreated, 1);
  assert.equal(afterAnomalyUnmount.objectUrlsActive, 0);
  report.anomalyWorkflow.resourcesAfterUnmount = {
    maps: 0,
    canvases: 0,
    controls: 0,
    popups: 0,
    activeSharedWorkers: afterAnomalyUnmount.workersActive,
    createdSharedWorkers: afterAnomalyUnmount.workersCreated,
    terminatedSharedWorkers: afterAnomalyUnmount.workersTerminated,
    sharedWorkerLifecycle: afterAnomalyUnmount.workersActive === 0
      ? 'terminated_after_last_map_owner'
      : 'single_global_dispatcher_retained_without_cycle_growth',
    unexpectedWorkerDuplicates: 0,
    objectUrls: 0,
  };

  trace('network_and_final_assertions');
  await Promise.allSettled(responseSizeTasks);
  const counts = {};
  for (const item of report.network.requests) {
    const key = item.path.split('?', 1)[0];
    counts[key] = (counts[key] || 0) + 1;
  }
  report.network.requestCounts = counts;
  const vectorSizes = report.network.responseSizes.filter((item) => item.path.startsWith('/api/field-tiles/'));
  const rasterSizes = report.network.responseSizes.filter((item) => item.path.startsWith('/api/raster/'));
  report.vectorViewport = {
    sourceType: 'vector',
    sourceLayerCountMaximum: maximumVectorLayers,
    cycleCount: MAP_CYCLES,
    vectorTileRequests: report.network.requests.filter((item) => item.path.includes('/api/field-tiles/')).length,
    maximumVectorTileBytes: vectorSizes.length ? Math.max(...vectorSizes.map((item) => item.bytes)) : 0,
    unboundedAllFieldGeoJsonRequests: report.network.unboundedAllFieldGeoJsonRequests,
    maximumRasterResponseBytes: rasterSizes.length ? Math.max(...rasterSizes.map((item) => item.bytes)) : 0,
    geometryBoundary: 'EPSG:4326 API metadata with MVT viewport delivery',
  };
  assert.equal(report.network.unboundedAllFieldGeoJsonRequests, 0);
  assert.ok(report.vectorViewport.maximumVectorTileBytes <= 2 * 1024 * 1024);
  assert.equal(report.cycles.length, MAP_CYCLES);
  assert.ok(report.cycles.every((item) => (
    item.afterUnmount.maps === 0
    && item.afterUnmount.canvases === 0
    && item.afterUnmount.controls === 0
    && item.afterUnmount.unexpectedWorkerDuplicates === 0
    && item.afterUnmount.objectUrls === 0
  )));
  assert.equal(report.browser.consoleErrors.length, 0);
  assert.equal(report.browser.pageErrors.length, 0);
  assert.equal(report.browser.unexpectedFailedRequests.length, 0);
  assert.equal(report.browser.unexpectedHttpErrors.length, 0);
  report.status = 'PASS';
  report.marker = 'PASS_MAPLIBRE_GIS_25_CYCLES_AND_STALE_GUARDS';
  report.stage = 'complete';
  report.completedAt = new Date().toISOString();
  await atomicJson('MAPLIBRE_GIS_QUALIFICATION.json', report);
  await atomicJson('MAPLIBRE_SCREENSHOT_INDEX.json', {
    schemaVersion: 1,
    exactHead: report.exactHead,
    screenshots: report.browser.screenshots,
  });
  process.stdout.write(`${report.marker}\n`);
  process.stdout.write(`MAP_CYCLES=${MAP_CYCLES}\n`);
  process.stdout.write(`HEAP_GROWTH_BYTES=${report.performance.stabilizedGrowthBytes}\n`);
  process.stdout.write('PRODUCTION_WRITES=0\n');
} catch (error) {
  report.status = 'FAIL';
  report.marker = 'FAIL_MAPLIBRE_GIS_QUALIFICATION';
  report.failure = safeFailure(error);
  report.failedAt = new Date().toISOString();
  await Promise.allSettled(responseSizeTasks);
  await atomicJson('MAPLIBRE_GIS_QUALIFICATION_FAILURE.json', report);
  throw error;
} finally {
  await context.close().catch(() => {});
  await browser.close().catch(() => {});
}


async function expectPressed(button) {
  await assertEventually(async () => (await button.getAttribute('aria-pressed')) === 'true');
}

async function assertEventually(predicate, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error('Timed out waiting for the expected browser state.');
}
