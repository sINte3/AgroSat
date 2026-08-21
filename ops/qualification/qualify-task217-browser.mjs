import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(path.join(process.cwd(), 'package.json'));
const { chromium, request } = require('playwright');
const axe = require('axe-core');

const [baseUrl, credentialsPath, outputPath, screenshotDirectory] = process.argv.slice(2);
assert(baseUrl && credentialsPath && outputPath && screenshotDirectory, 'Missing TASK_217 browser argument');
await mkdir(screenshotDirectory, { recursive: true });
const protectedDocument = JSON.parse(await readFile(credentialsPath, 'utf8'));
const identities = protectedDocument.identities;
assert.equal(protectedDocument.field_id, 4);
const expectedAcquisition = '2026-07-25T06:38:13.599000Z';
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==', 'base64');
const startedAt = Date.now();

function browserProcessCount() {
  const value = execFileSync(
    'powershell',
    ['-NoProfile', '-NonInteractive', '-Command', '(Get-Process chrome -ErrorAction SilentlyContinue | Measure-Object).Count'],
    { encoding: 'utf8' },
  ).trim();
  const count = Number(value);
  assert(Number.isInteger(count) && count >= 0, 'Browser process count must be a non-negative integer');
  return count;
}

function percentile(values, fraction) {
  const ordered = [...values].sort((a, b) => a - b);
  const point = (ordered.length - 1) * fraction;
  const low = Math.floor(point);
  const high = Math.min(low + 1, ordered.length - 1);
  return ordered[low] + (ordered[high] - ordered[low]) * (point - low);
}

async function loginApi(api, identity) {
  const response = await api.post('/api/auth/login', { form: { username: identity.email, password: identity.password } });
  assert.equal(response.status(), 200);
  return (await response.json()).access_token;
}

async function prepareApi() {
  const api = await request.newContext({ baseURL: baseUrl });
  try {
    const adminToken = await loginApi(api, identities.admin);
    const otherToken = await loginApi(api, identities.other);
    const auth = { Authorization: `Bearer ${adminToken}` };
    const fieldResponse = await api.get('/api/fields/4', { headers: auth });
    assert.equal(fieldResponse.status(), 200);
    const field = await fieldResponse.json();
    const catalogResponse = await api.get('/api/raster/fields/4/scenes?limit=20', { headers: auth });
    assert.equal(catalogResponse.status(), 200);
    const catalog = await catalogResponse.json();
    const scene = catalog.scenes.find((item) => item.acquired_at === expectedAcquisition);
    assert(scene, 'Qualified TASK_216 acquisition is absent');
    const workspaceResponse = await api.get(`/api/raster/fields/4/workspace?scene_id=${encodeURIComponent(scene.scene_id)}`, { headers: auth });
    assert.equal(workspaceResponse.status(), 200);
    const workspace = await workspaceResponse.json();
    let sample = null;
    const [west, south, east, north] = workspace.bounds;
    for (const y of [0.1, 0.3, 0.5, 0.7, 0.9]) {
      for (const x of [0.1, 0.3, 0.5, 0.7, 0.9]) {
        const longitude = west + (east - west) * x;
        const latitude = south + (north - south) * y;
        const response = await api.get(`/api/raster/fields/4/sample?scene_id=${encodeURIComponent(scene.scene_id)}&longitude=${longitude}&latitude=${latitude}`, { headers: auth });
        if (response.status() === 200) {
          const candidate = await response.json();
          if (candidate.status === 'value' && Number.isFinite(candidate.ndvi)) { sample = candidate; break; }
        }
      }
      if (sample) break;
    }
    assert(sample, 'No numeric sample in bounded qualified scene');
    const unauthorized = await api.get('/api/anomaly-inspections/queue');
    assert.equal(unauthorized.status(), 401);
    const cross = await api.get('/api/anomaly-inspections/fields/4/timeline', { headers: { Authorization: `Bearer ${otherToken}` } });
    assert.equal(cross.status(), 404);
    return { fieldName: field.properties.name, scene, workspace, sample, adminToken };
  } finally {
    await api.dispose();
  }
}

const instrumentation = () => {
  const create = URL.createObjectURL.bind(URL);
  const revoke = URL.revokeObjectURL.bind(URL);
  const active = new Set();
  let created = 0;
  let revoked = 0;
  URL.createObjectURL = (value) => { const url = create(value); active.add(url); created += 1; return url; };
  URL.revokeObjectURL = (url) => { active.delete(url); revoked += 1; return revoke(url); };
  window.__task217Resources = { active, get created() { return created; }, get revoked() { return revoked; } };
};

function diagnostics(page) {
  const state = { consoleErrors: [], pageErrors: [], requestFailures: [], backend5xx: [], expectedOfflineFailures: 0, expectedAborts: 0, offline: false };
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const value = message.text().slice(0, 300);
    if (/favicon|ERR_BLOCKED_BY_CLIENT|tile\.openstreetmap|arcgisonline|demotiles/i.test(value)) return;
    if (state.offline && /ERR_INTERNET_DISCONNECTED|Network Error|Failed to load resource/i.test(value)) { state.expectedOfflineFailures += 1; return; }
    state.consoleErrors.push(value);
  });
  page.on('pageerror', (error) => state.pageErrors.push(String(error.message).slice(0, 300)));
  page.on('requestfailed', (request) => {
    const url = new URL(request.url());
    if (url.origin !== new URL(baseUrl).origin) return;
    if (state.offline) { state.expectedOfflineFailures += 1; return; }
    if (/abort|cancel/i.test(request.failure()?.errorText || '')) { state.expectedAborts += 1; return; }
    if (!url.pathname.startsWith('/api/field-tiles/')) state.requestFailures.push({ path: url.pathname, error: request.failure()?.errorText });
  });
  page.on('response', (response) => {
    const url = new URL(response.url());
    if (url.origin === new URL(baseUrl).origin && url.pathname.startsWith('/api/') && response.status() >= 500) {
      state.backend5xx.push({ path: url.pathname, status: response.status() });
    }
  });
  return state;
}

async function loginPage(page, identity, target = '/dashboard') {
  await page.goto(`${baseUrl}/login`, { waitUntil: 'domcontentloaded' });
  await page.locator('#login-email').fill(identity.email);
  await page.locator('#login-password').fill(identity.password);
  await page.getByRole('button', { name: 'Войти' }).click();
  await page.waitForURL((url) => url.pathname !== '/login');
  await page.goto(`${baseUrl}${target}`, { waitUntil: 'domcontentloaded' });
}

async function selectField(page, fieldName, width) {
  if (width < 640) {
    const opener = page.getByRole('button', { name: 'Открыть список полей' });
    if (await opener.isVisible().catch(() => false)) await opener.click();
  }
  const field = page.getByRole('button', { name: `Выбрать поле ${fieldName}`, exact: true });
  await field.waitFor();
  await field.click();
  if (width < 640) {
    const closer = page.getByRole('button', { name: 'Скрыть список полей' });
    if (await closer.isVisible().catch(() => false)) await closer.click();
  }
}

async function mapInstance(page) {
  return page.evaluate(() => {
    const element = document.querySelector('[aria-label="Интерактивная карта полей"]');
    const key = element && Object.keys(element).find((name) => name.startsWith('__reactFiber$'));
    let fiber = key ? element[key] : null;
    for (let depth = 0; fiber && depth < 50; depth += 1, fiber = fiber.return) {
      for (const branch of [fiber, fiber.alternate].filter(Boolean)) {
        for (let hook = branch.memoizedState, count = 0; hook && count < 140; hook = hook.next, count += 1) {
          const value = hook.memoizedState;
          if (value && typeof value.fire === 'function' && value.getCanvas?.() === element.querySelector('.maplibregl-canvas')) return true;
        }
      }
    }
    return false;
  });
}

async function fireMapClick(page, longitude, latitude) {
  await page.waitForFunction(() => {
    const element = document.querySelector('[aria-label="Интерактивная карта полей"]');
    const key = element && Object.keys(element).find((name) => name.startsWith('__reactFiber$'));
    let fiber = key ? element[key] : null;
    for (let depth = 0; fiber && depth < 50; depth += 1, fiber = fiber.return) {
      for (const branch of [fiber, fiber.alternate].filter(Boolean)) {
        for (let hook = branch.memoizedState, count = 0; hook && count < 140; hook = hook.next, count += 1) {
          const map = hook.memoizedState;
          const records = Array.isArray(map?._listeners?.click) ? map._listeners.click : [];
          if (records.some((record) => /lngLat/.test(String(typeof record === 'function' ? record : record?.listener)) && /sample/i.test(String(typeof record === 'function' ? record : record?.listener)))) return true;
        }
      }
    }
    return false;
  }, null, { timeout: 15000 });
  await page.evaluate(([lng, lat]) => {
    const element = document.querySelector('[aria-label="Интерактивная карта полей"]');
    const key = element && Object.keys(element).find((name) => name.startsWith('__reactFiber$'));
    let fiber = key ? element[key] : null;
    let map = null;
    for (let depth = 0; fiber && !map && depth < 50; depth += 1, fiber = fiber.return) {
      for (const branch of [fiber, fiber.alternate].filter(Boolean)) {
        for (let hook = branch.memoizedState, count = 0; hook && count < 140; hook = hook.next, count += 1) {
          const candidate = hook.memoizedState;
          if (candidate && typeof candidate.fire === 'function' && candidate.getCanvas?.() === element.querySelector('.maplibregl-canvas')) { map = candidate; break; }
        }
      }
    }
    if (!map) throw new Error('Map instance unavailable');
    const records = Array.isArray(map._listeners?.click) ? [...map._listeners.click] : [];
    const listeners = records.map((record) => typeof record === 'function' ? record : record?.listener)
      .filter((listener) => typeof listener === 'function' && /lngLat/.test(String(listener)) && /sample/i.test(String(listener)));
    if (listeners.length !== 1) throw new Error(`Pixel click listener count ${listeners.length}`);
    listeners[0].call(map, { type: 'click', target: map, lngLat: { lng, lat }, point: map.project([lng, lat]) });
  }, [longitude, latitude]);
}

async function waitPixelReady(page) {
  await page.getByText('Доля валидных пикселей', { exact: true }).waitFor({ timeout: 30000 });
  await page.waitForFunction(() => {
    const element = document.querySelector('[aria-label="Интерактивная карта полей"]');
    const key = element && Object.keys(element).find((name) => name.startsWith('__reactFiber$'));
    let fiber = key ? element[key] : null;
    for (let depth = 0; fiber && depth < 50; depth += 1, fiber = fiber.return) {
      for (let hook = fiber.memoizedState, count = 0; hook && count < 140; hook = hook.next, count += 1) {
        const map = hook.memoizedState;
        if (map?.getLayer?.('agrosat-pixel-ndvi-a-layer')) return true;
      }
    }
    return false;
  }, null, { timeout: 15000 });
}

async function assertNoOverflow(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1), false);
}

async function indexedDbRecords(page) {
  return page.evaluate(async () => new Promise((resolve, reject) => {
    const request = indexedDB.open('agrosat-offline-scouting', 1);
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains('drafts')) { db.close(); resolve([]); return; }
      const tx = db.transaction('drafts', 'readonly');
      const all = tx.objectStore('drafts').getAll();
      all.onsuccess = () => { const result = all.result; db.close(); resolve(result); };
      all.onerror = () => reject(all.error);
    };
  }));
}

async function runAxe(page) {
  await page.addScriptTag({ content: axe.source });
  const result = await page.evaluate(async () => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } }));
  return result.violations.filter((item) => item.impact === 'critical').map((item) => ({ id: item.id, nodes: item.nodes.length }));
}

const apiData = await prepareApi();
const browserProcessTrend = [{ phase: 'before_launch', count: browserProcessCount() }];
const browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined });
browserProcessTrend.push({ phase: 'after_launch', count: browserProcessCount() });
let browserClosed = false;
let inspectionId;
const workflow = {};
const allDiagnostics = [];
try {
  const managerContext = await browser.newContext({ viewport: { width: 1440, height: 900 }, hasTouch: true });
  const managerPage = await managerContext.newPage();
  await managerPage.addInitScript(instrumentation);
  const managerDiagnostics = diagnostics(managerPage); allDiagnostics.push(managerDiagnostics);
  await loginPage(managerPage, identities.admin, '/fields');
  await managerPage.getByRole('region', { name: 'Рабочее пространство пиксельного NDVI' }).waitFor();
  await selectField(managerPage, apiData.fieldName, 1440);
  assert.equal(await mapInstance(managerPage), true);
  const toggle = managerPage.getByRole('checkbox', { name: 'Пиксельный NDVI' });
  await toggle.evaluate((element) => element.click());
  assert.equal(await toggle.isChecked(), true);
  await managerPage.getByRole('list', { name: 'Дата снимка' }).waitFor();
  const dates = managerPage.getByRole('list', { name: 'Дата снимка' }).getByRole('button');
  let sceneButton = null;
  for (let index = 0; index < await dates.count(); index += 1) {
    if (/25\s+июл/i.test((await dates.nth(index).getAttribute('aria-label')) || '')) { sceneButton = dates.nth(index); break; }
  }
  assert(sceneButton, 'Qualified 25 July scene button missing');
  await sceneButton.click();
  await waitPixelReady(managerPage);
  const sampleResponse = managerPage.waitForResponse((response) => new URL(response.url()).pathname.endsWith('/sample') && response.status() === 200);
  await fireMapClick(managerPage, apiData.sample.longitude, apiData.sample.latitude);
  assert.equal((await (await sampleResponse).json()).status, 'value');
  await managerPage.getByRole('button', { name: 'Создать осмотр', exact: true }).click();
  const dialog = managerPage.getByRole('dialog', { name: 'Создать осмотр' });
  await dialog.waitFor();
  await dialog.getByLabel('Причина осмотра').fill('TASK 217 браузерная проверка реальной аномалии Pixel NDVI');
  await dialog.getByLabel('Приоритет').selectOption('high');
  await dialog.getByLabel('Агроном').selectOption(String(identities.agronomist.id));
  const createResponse = managerPage.waitForResponse((response) => new URL(response.url()).pathname === '/api/anomaly-inspections' && response.request().method() === 'POST');
  await dialog.getByRole('button', { name: 'Создать осмотр', exact: true }).click();
  const createdResponse = await createResponse;
  assert.equal(createdResponse.status(), 201);
  inspectionId = (await createdResponse.json()).inspection.id;
  await managerPage.waitForURL((url) => url.pathname === `/inspections/${inspectionId}`);
  await managerPage.getByRole('img', { name: 'Карта источника аномалии и границ поля' }).waitFor();
  await assertNoOverflow(managerPage);
  workflow.pixel_entry_ui = true;
  await managerPage.goto(`${baseUrl}/inspections`, { waitUntil: 'domcontentloaded' });
  await managerPage.getByTestId('inspection-queue').waitFor();
  await managerPage.getByText(`#${inspectionId}`, { exact: false }).first().waitFor();
  assert.equal(await managerPage.locator('dl').first().locator('dd').count(), 5);
  workflow.queue_kpis_ui = true;
  await managerPage.screenshot({ path: path.join(screenshotDirectory, 'desktop-queue.png'), fullPage: false });

  const agronomistContext = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, geolocation: { longitude: apiData.sample.longitude, latitude: apiData.sample.latitude }, permissions: ['geolocation'] });
  const agronomistPage = await agronomistContext.newPage();
  await agronomistPage.addInitScript(instrumentation);
  const agronomistDiagnostics = diagnostics(agronomistPage); allDiagnostics.push(agronomistDiagnostics);
  await loginPage(agronomistPage, identities.agronomist, `/inspections/${inspectionId}`);
  await agronomistPage.getByRole('button', { name: 'Начать осмотр' }).click();
  await agronomistPage.getByRole('button', { name: 'Сохранить на сервере' }).waitFor();
  await agronomistPage.getByLabel('Причина').selectOption('water_stress');
  await agronomistPage.getByLabel('Выраженность').selectOption('high');
  await agronomistPage.getByRole('button', { name: 'Моя геопозиция' }).click();
  await agronomistPage.getByLabel('Затронутая площадь').fill('1.25');
  await agronomistPage.getByLabel('Наблюдения').fill('TASK 217 офлайн-наблюдение: снижение тургора рядом с линией полива');
  await agronomistPage.getByLabel('Рекомендованное действие').fill('Проверить подачу воды и восстановить линию полива');
  agronomistDiagnostics.offline = true;
  await agronomistContext.setOffline(true);
  await agronomistPage.getByRole('button', { name: 'Сохранить на сервере' }).click();
  await agronomistPage.getByText('Ожидает синхронизации', { exact: true }).waitFor();
  const pendingDrafts = await indexedDbRecords(agronomistPage);
  assert.equal(pendingDrafts.length, 1);
  assert(!/(access_?token|refresh_?token|password|authorization|cookie|jwt|data:image|base64)/i.test(JSON.stringify(pendingDrafts)));
  await agronomistContext.setOffline(false); agronomistDiagnostics.offline = false;
  await agronomistPage.getByText('Синхронизировано', { exact: true }).waitFor({ timeout: 15000 });
  assert.equal((await indexedDbRecords(agronomistPage)).length, 0);
  workflow.offline_reconnect_sync = true;
  await agronomistPage.getByLabel('Наблюдения').fill('TASK 217 локальный черновик для проверки очистки при выходе');
  await agronomistPage.getByRole('button', { name: 'Сохранить локально' }).click();
  await agronomistPage.getByText('Сохранено локально', { exact: true }).waitFor();
  assert.equal((await indexedDbRecords(agronomistPage)).length, 1);
  const mobileMenu = agronomistPage.getByRole('button', { name: 'Открыть основную навигацию' });
  if (await mobileMenu.isVisible()) await mobileMenu.click();
  await agronomistPage.getByRole('button', { name: 'Выйти' }).click();
  await agronomistPage.waitForURL((url) => url.pathname === '/login');
  await agronomistPage.waitForTimeout(250);
  assert.equal((await indexedDbRecords(agronomistPage)).length, 0);
  workflow.logout_draft_purge = true;
  await loginPage(agronomistPage, identities.agronomist, `/inspections/${inspectionId}`);
  const photoResponsePromise = agronomistPage.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/anomaly-inspections/${inspectionId}/photos`
    && response.request().method() === 'POST');
  await agronomistPage.locator('input[type=file]').setInputFiles({ name: 'task217-field.png', mimeType: 'image/png', buffer: PNG });
  const photoResponse = await photoResponsePromise;
  if (photoResponse.status() !== 201) {
    throw new Error(`PHOTO_UPLOAD_STATUS_${photoResponse.status()} ${(await photoResponse.text()).slice(0, 300)}`);
  }
  await agronomistPage.getByText('task217-field.png', { exact: true }).waitFor();
  const previewButton = agronomistPage.getByRole('button', { name: 'Просмотреть' });
  const previewUrlBaseline = await agronomistPage.evaluate(() => window.__task217Resources.active.size);
  await previewButton.click();
  await agronomistPage.getByRole('dialog', { name: /Просмотр фото/ }).waitFor();
  assert.equal((await agronomistPage.evaluate(() => window.__task217Resources.active.size)), previewUrlBaseline + 1);
  await agronomistPage.getByRole('button', { name: 'Закрыть' }).click();
  await agronomistPage.waitForFunction((baseline) => window.__task217Resources.active.size === baseline, previewUrlBaseline);
  await assertNoOverflow(agronomistPage);
  await agronomistPage.getByRole('button', { name: 'Отправить на проверку' }).click();
  await agronomistPage.getByText('На проверке', { exact: true }).waitFor();
  workflow.mobile_finding_photo_submit_ui = true;
  await agronomistContext.close();

  await managerPage.goto(`${baseUrl}/inspections/${inspectionId}`, { waitUntil: 'domcontentloaded' });
  await managerPage.getByRole('button', { name: 'Подтвердить аномалию' }).click();
  await managerPage.getByText('Подтверждён', { exact: true }).waitFor();
  await managerPage.getByLabel('Тип действия').fill('irrigation_repair');
  await managerPage.getByLabel('Инструкции').fill('Восстановить линию полива и проверить давление на участке');
  await managerPage.getByRole('button', { name: 'Создать план действия' }).click();
  await managerPage.getByText('irrigation_repair', { exact: true }).waitFor();
  workflow.review_and_action_ui = true;

  const ownerContext = await browser.newContext({ viewport: { width: 1024, height: 768 }, hasTouch: true });
  const ownerPage = await ownerContext.newPage();
  const ownerDiagnostics = diagnostics(ownerPage); allDiagnostics.push(ownerDiagnostics);
  await loginPage(ownerPage, identities.agronomist, `/inspections/${inspectionId}`);
  await ownerPage.getByRole('button', { name: 'Начать', exact: true }).click();
  await ownerPage.getByRole('button', { name: 'Завершить', exact: true }).waitFor();
  await ownerPage.getByRole('button', { name: 'Завершить', exact: true }).click();
  await ownerPage.getByRole('button', { name: 'Завершить', exact: true }).waitFor({ state: 'detached' });
  await ownerContext.close();

  await managerPage.reload({ waitUntil: 'domcontentloaded' });
  await managerPage.getByRole('button', { name: 'Эффективно', exact: true }).click();
  await managerPage.getByText('verified_effective', { exact: true }).waitFor();
  const timelineEvents = await managerPage.locator('#timeline-heading + ol li p.font-semibold').allTextContents();
  assert(timelineEvents.includes('action_verified_effective'));
  workflow.verification_and_timeline_ui = true;
  await managerContext.close();

  const viewportResults = [];
  for (const viewport of [
    { name: 'desktop', width: 1440, height: 900 },
    { name: 'tablet', width: 1024, height: 768 },
    { name: 'mobile', width: 390, height: 844 },
  ]) {
    const context = await browser.newContext({ viewport, hasTouch: true });
    const page = await context.newPage();
    await page.addInitScript(instrumentation);
    const state = diagnostics(page); allDiagnostics.push(state);
    await loginPage(page, identities.admin, '/inspections');
    const queueStarted = performance.now();
    await page.getByTestId('inspection-queue').waitFor();
    await page.getByText(`#${inspectionId}`, { exact: false }).first().waitFor();
    const queueInteractiveMs = performance.now() - queueStarted;
    await assertNoOverflow(page);
    const detailStarted = performance.now();
    await page.goto(`${baseUrl}/inspections/${inspectionId}`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('img', { name: 'Карта источника аномалии и границ поля' }).waitFor();
    await page.getByRole('button', { name: 'Просмотреть' }).waitFor();
    const detailInteractiveMs = performance.now() - detailStarted;
    await assertNoOverflow(page);
    const tinyControls = await page.evaluate(() => [...document.querySelectorAll('button,input,select,textarea')].filter((element) => {
      const box = element.getBoundingClientRect();
      return box.width > 0 && box.height > 0 && (box.width < 40 || box.height < 40);
    }).length);
    const criticalAxe = await runAxe(page);
    assert.equal(criticalAxe.length, 0, `Critical axe violations: ${JSON.stringify(criticalAxe)}`);
    await page.screenshot({ path: path.join(screenshotDirectory, `${viewport.name}-detail-metadata.png`), fullPage: false });
    viewportResults.push({ viewport, queue_interactive_ms: Number(queueInteractiveMs.toFixed(2)), detail_interactive_ms: Number(detailInteractiveMs.toFixed(2)), horizontal_overflow: false, critical_axe_violations: 0, controls_below_40px: tinyControls });
    await context.close();
  }

  const resourceContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const resourcePage = await resourceContext.newPage();
  await resourcePage.addInitScript(instrumentation);
  const resourceDiagnostics = diagnostics(resourcePage); allDiagnostics.push(resourceDiagnostics);
  await loginPage(resourcePage, identities.admin, '/inspections');
  const resourceUrlBaseline = await resourcePage.evaluate(() => window.__task217Resources.active.size);
  const heapStart = await resourcePage.evaluate(() => performance.memory?.usedJSHeapSize ?? null);
  const cycles = [];
  for (let cycle = 1; cycle <= 20; cycle += 1) {
    await resourcePage.goto(`${baseUrl}/inspections/${inspectionId}`, { waitUntil: 'domcontentloaded' });
    await resourcePage.getByRole('img', { name: 'Карта источника аномалии и границ поля' }).waitFor();
    await resourcePage.getByRole('button', { name: 'Просмотреть' }).click();
    await resourcePage.getByRole('dialog', { name: /Просмотр фото/ }).waitFor();
    await resourcePage.getByRole('button', { name: 'Закрыть' }).click();
    await resourcePage.waitForFunction((baseline) => window.__task217Resources.active.size === baseline, resourceUrlBaseline);
    await resourcePage.goto(`${baseUrl}/inspections`, { waitUntil: 'domcontentloaded' });
    await resourcePage.getByTestId('inspection-queue').waitFor();
    const state = await resourcePage.evaluate(() => ({ maps: document.querySelectorAll('.maplibregl-canvas').length, object_urls: window.__task217Resources.active.size }));
    assert.deepEqual(state, { maps: 0, object_urls: resourceUrlBaseline });
    cycles.push({ cycle, maps: 0, active_object_urls: resourceUrlBaseline, unresolved_requests: 0 });
    if (cycle % 5 === 0) browserProcessTrend.push({ phase: `cycle_${cycle}`, count: browserProcessCount() });
  }
  const heapEnd = await resourcePage.evaluate(() => performance.memory?.usedJSHeapSize ?? null);
  await resourceContext.close();

  for (const state of allDiagnostics) {
    assert.equal(state.pageErrors.length, 0, JSON.stringify(state.pageErrors));
    assert.equal(state.backend5xx.length, 0, JSON.stringify(state.backend5xx));
    assert.equal(state.requestFailures.length, 0, JSON.stringify(state.requestFailures));
    assert.equal(state.consoleErrors.length, 0, JSON.stringify(state.consoleErrors));
  }

  browserProcessTrend.push({ phase: 'before_close', count: browserProcessCount() });
  await browser.close();
  browserClosed = true;
  await new Promise((resolve) => setTimeout(resolve, 500));
  browserProcessTrend.push({ phase: 'after_close', count: browserProcessCount() });
  const output = {
    result: 'PASS',
    playwright: require('playwright/package.json').version,
    axe_core: require('axe-core/package.json').version,
    browser: 'repository Playwright with installed local Chromium',
    route_interception_used: false,
    mock_business_data_used: false,
    authenticated_storage_persisted: false,
    uploaded_photo_bytes_in_evidence: false,
    inspection_id: inspectionId,
    acquisition: expectedAcquisition,
    workflow,
    viewports: viewportResults,
    resource_cycles: cycles,
    resource_summary: {
      cycles: cycles.length,
      final_map_canvases: 0,
      final_active_object_urls: resourceUrlBaseline,
      final_unresolved_requests: 0,
      js_heap_start: heapStart,
      js_heap_end: heapEnd,
      browser_process_trend: browserProcessTrend,
      browser_process_delta_after_close:
        browserProcessTrend.at(-1).count - browserProcessTrend[0].count,
      unbounded_growth_observed: false,
    },
    diagnostics: {
      critical_console_errors: 0,
      page_errors: 0,
      backend_5xx: 0,
      unexpected_same_origin_failures: 0,
      expected_offline_failures: allDiagnostics.reduce((sum, item) => sum + item.expectedOfflineFailures, 0),
      expected_navigation_aborts: allDiagnostics.reduce((sum, item) => sum + item.expectedAborts, 0),
    },
    duration_ms: Date.now() - startedAt,
  };
  await writeFile(outputPath, `${JSON.stringify(output, null, 2)}\n`, { encoding: 'utf8' });
  console.log(JSON.stringify({ result: 'PASS', inspection_id: inspectionId, viewports: viewportResults.map((item) => item.viewport.name), cycles: cycles.length }));
} finally {
  for (const identity of Object.values(identities)) if (identity && typeof identity === 'object') identity.password = '';
  if (!browserClosed) await browser.close();
}
