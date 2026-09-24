// TASK_226 component qualification in Chromium: route ErrorBoundary, MapLibre
// ownership cleanup (listeners, custom sources/layers, intervals, object URLs,
// stale async generations), real MapLibre teardown, and offline partitions with
// the session model on a real IndexedDB.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { createServer } from 'vite';

import { chromiumExecutable, PNG_BYTES } from './lib/browser.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const evidenceDirectory = process.argv[2] || process.env.TASK226_EVIDENCE_DIR || '';

const server = await createServer({
  configFile: path.join(root, 'vite.config.js'),
  root,
  logLevel: 'silent',
  server: { port: 0, strictPort: false, host: '127.0.0.1' },
});
await server.listen();
const baseUrl = server.resolvedUrls.local[0].replace(/\/$/, '');
const browser = await chromium.launch({ headless: true, executablePath: chromiumExecutable() });

const workspace = {
  schema_version: 'program_r3_pixel_ndvi_v1',
  corners: [[64.4, 39.81], [64.41, 39.81], [64.41, 39.8], [64.4, 39.8]],
  width: 1,
  height: 1,
};
const scenes = {
  scenes: [
    { scene_id: 'scene-a', raster_available: true, acquired_at: '2026-09-20T06:00:00Z' },
    { scene_id: 'scene-b', raster_available: true, acquired_at: '2026-09-15T06:00:00Z' },
  ],
};
const apiRequests = [];
const pageErrors = [];
const consoleErrors = [];
let results;
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  page.on('pageerror', (error) => pageErrors.push(String(error.message)));
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text().slice(0, 300)); });
  if (process.env.TASK226_DEBUG) {
    page.on('console', (message) => console.log('[page]', message.type(), message.text().slice(0, 300)));
    page.on('pageerror', (error) => console.log('[pageerror]', error.message.slice(0, 300)));
    page.on('response', (response) => { if (response.status() >= 400) console.log('[http]', response.status(), response.url()); });
  }
  // Match the HTTP API only: Vite serves application modules under /src/api/.
  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    apiRequests.push(`${method} ${url.pathname}${url.search}`);
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const png = () => route.fulfill({ status: 200, contentType: 'image/png', body: PNG_BYTES, headers: { 'x-agrosat-raster-cache': 'hit' } });
    if (method === 'GET' && url.pathname === '/api/alerts/') {
      return url.searchParams.get('field_id') === '77' ? json({ detail: 'fixture failure' }, 500) : json([]);
    }
    if (method === 'GET' && url.pathname.startsWith('/api/weather/field/')) {
      return json({ current: { temperature: 30, humidity: 20, wind_speed: 5, weather_code: 1 }, forecast: [] });
    }
    const match = url.pathname.match(/^\/api\/raster\/fields\/(\d+)\/([a-z-]+)$/);
    if (method !== 'GET' || !match) return json({ detail: 'not found' }, 404);
    const [, fieldId, resource] = match;
    const sceneId = url.searchParams.get('scene_id');
    if (resource === 'scenes') return json(scenes);
    if (resource === 'workspace') return json({ ...workspace, scene_id: sceneId });
    if (resource === 'pixel-image') return fieldId === '8' && sceneId === 'scene-b' ? json({ detail: 'scene unavailable' }, 404) : png();
    if (resource === 'sample') return json({ status: 'value', ndvi: 0.52, longitude: 64.41, latitude: 39.81, acquired_at: '2026-09-20T06:00:00Z' });
    if (resource === 'metadata') return json({ bbox: [64.4, 39.8, 64.41, 39.81], default_size: 64, observation_date: '2026-09-20' });
    if (resource === 'image') return png();
    return json({ detail: 'not found' }, 404);
  });

  await page.goto(`${baseUrl}/scripts/harness/task226-harness.html`, { waitUntil: 'load' });
  // The first dev-server load may optimize dependencies and reload the page once.
  await page.waitForFunction(() => window.__task226Ready === true, null, { timeout: 180000 });
  results = {};
  for (const runner of ['runFieldListStates', 'runFieldDetailPanelStates', 'runErrorBoundary', 'runPixelWorkspace', 'runPixelWorkspacePartialFailure', 'runRasterLayer', 'runAgronomyCaseMap', 'runOfflineStore']) {
    results[runner] = await page.evaluate((name) => window.__task226[name](), runner);
  }
  await context.close();
} finally {
  await browser.close();
  await server.close();
}

const zero = { listeners: 0, sources: 0, layers: 0 };
assert.deepEqual(results.runFieldListStates, {
  loading: { loadingShown: true, emptyShown: false },
  failed: { errorShown: true, emptyShown: false, count: '—', role: true },
  readyEmpty: { emptyShown: true, errorShown: false },
  retries: 1,
}, 'a failed field list is an explicit error with retry, never "no fields"');
assert.deepEqual(results.runFieldDetailPanelStates, {
  failed: { heading: 'Алерты (—)', healthyEmptyShown: false, role: 'alert' },
  empty: { heading: 'Алерты (0)', healthyEmptyShown: true },
}, 'a failed alert request never renders "no active alerts"');
const boundary = results.runErrorBoundary;
assert.equal(boundary.fallbackShown, true, 'a throwing child renders the fallback');
assert.match(boundary.fallbackText, /Раздел не удалось отобразить/);
assert.equal(boundary.fallbackRole, 'alert');
assert.equal(boundary.shellKept, true, 'the shell outside the boundary stays rendered');
assert.equal(boundary.childRendered, false);
assert.equal(boundary.stillFailingAfterRetry, true, 'retry of a still failing child shows the fallback again');
assert.ok(boundary.rendersPerRetry >= 1 && boundary.rendersPerRetry <= 4, `bounded renders per retry: ${boundary.rendersPerRetry}`);
assert.equal(boundary.rendersWhileIdle, 0, 'no automatic re-render loop');
assert.equal(boundary.recoveredAfterRetry, true, 'retry recovers once the cause is gone');
assert.equal(boundary.failsAgainOnNewRoute, true);
assert.equal(boundary.resetByRouteChange, true, 'a route change resets the boundary');
assert.equal(boundary.homeActionResets, true, 'the safe navigation action resets the boundary');

const pixel = results.runPixelWorkspace;
assert.deepEqual(pixel.ready, { mapA: { listeners: 3, sources: 2, layers: 2 }, urls: 3, intervals: 1 }, 'workspace owns 3 listeners, 2 sources, 2 layers, 3 URLs, 1 interval');
assert.deepEqual(pixel.afterOwnershipChange, { mapA: zero, mapB: { listeners: 3, sources: 2, layers: 2 }, urls: 3, intervals: 1 }, 'ownership change cleans the previous map completely');
assert.deepEqual(pixel.afterComparisonOff, { mapB: { listeners: 3, sources: 1, layers: 1 }, urls: 1, intervals: 1 }, 'comparison off releases scene B artifacts and URLs');
assert.deepEqual(pixel.afterDisable, { mapB: { listeners: 2, sources: 0, layers: 0 }, urls: 0, intervals: 0 }, 'disabling releases the raster, click handler, URLs and interval');
assert.deepEqual(pixel.afterUnmount, { mapA: zero, mapB: zero, urls: 0, intervals: 0 }, 'unmount leaves nothing behind');

const partial = results.runPixelWorkspacePartialFailure;
assert.deepEqual(partial.whileMounted, { status: 'error', errorStatus: 404, liveUrls: 0, map: { listeners: 2, sources: 0, layers: 0 } }, 'a failed scene B does not leak scene A\'s object URL');
assert.deepEqual(partial.afterUnmount, { liveUrls: 0, intervals: 0, map: zero });

const raster = results.runRasterLayer;
assert.deepEqual(raster.ready, { mapA: { listeners: 1, sources: 1, layers: 1 }, urls: 1 });
assert.deepEqual(raster.afterStyleReload, { mapA: { listeners: 1, sources: 1, layers: 1 } }, 'a style reload re-adds without duplicates');
assert.deepEqual(raster.afterOwnershipChange, { mapA: zero, mapB: { listeners: 1, sources: 1, layers: 1 }, urls: 1 });
assert.deepEqual(raster.afterDisable, { mapB: { listeners: 1, sources: 0, layers: 0 }, urls: 0 });
assert.deepEqual(raster.afterUnmount, { mapA: zero, mapB: zero, urls: 0 });

assert.deepEqual(results.runAgronomyCaseMap, { created: 5, removed: 5, canvases: 0, mapContainers: 0 }, 'every real MapLibre map is removed on unmount');

const offline = results.runOfflineStore;
assert.deepEqual(offline.listed, {
  a: ['agronomy-plan:9:local_draft', 'inspection-finding:41:pending_sync'],
  b: ['inspection-finding:41:local_draft'],
}, 'each user lists only their own drafts');
assert.equal(offline.invalidated, true);
assert.deepEqual(offline.afterInvalidation, {
  token: null, user: null, a: 2, b: 1, draftAStatus: 'pending_sync', agronomyDraftA: true,
  crossScopeRead: 'disease', agronomyDraftForB: false,
}, 'session invalidation clears the credential and keeps every unsynchronized draft');
assert.deepEqual(offline.afterExplicitLogoutB, { token: null, a: 2, b: 0 }, 'an explicit logout removes only its own partition');
assert.deepEqual(offline.purgedEmpty, { snapshots: 0, drafts: 0, queue: 0 });
assert.deepEqual(offline.afterExplicitLogoutA, { a: 0, b: 0 });
assert.equal(offline.deleteDatabaseCalls, 0, 'the offline database is never deleted wholesale');

const unexpectedPageErrors = pageErrors.filter((message) => !message.includes('render failure fixture'));
assert.deepEqual(unexpectedPageErrors, [], 'no unexpected page errors');
// Expected: the deliberate render failure, fixture 404s, and the deliberate 500s of the alert-failure case.
const unexpectedConsoleErrors = consoleErrors.filter((message) => !/render failure fixture|The above error occurred|\[AgroSat\] render failure|status of (404|500) \(/.test(message));
assert.deepEqual(unexpectedConsoleErrors, [], 'no unexpected console errors');
assert.ok(apiRequests.some((request) => request.includes('/pixel-image?scene_id=scene-b')), 'the comparison scene was requested');

const report = {
  result: 'PASS_TASK226_COMPONENT_HARNESS',
  chromium: chromiumExecutable() || 'playwright-default',
  field_list_states: results.runFieldListStates,
  field_detail_panel_states: results.runFieldDetailPanelStates,
  error_boundary: boundary,
  pixel_workspace: pixel,
  pixel_workspace_partial_failure: partial,
  ndvi_raster_layer: raster,
  agronomy_case_map: results.runAgronomyCaseMap,
  offline_partitions: offline,
  api_requests: apiRequests.length,
};
if (evidenceDirectory) {
  await mkdir(evidenceDirectory, { recursive: true });
  await writeFile(path.join(evidenceDirectory, 'TASK226_COMPONENT_HARNESS.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
}
console.log(JSON.stringify({ result: report.result, runners: Object.keys(results).length, api_requests: apiRequests.length }));
