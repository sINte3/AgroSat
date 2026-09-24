// TASK_226 component harness. Served by the Vite dev server only (it is not an
// entry of the production build). Each runner mounts real application code and
// reports what it left behind; scripts/test-task226-harness-browser.mjs asserts.
import { flushSync } from 'react-dom';
import { createRoot } from 'react-dom/client';

import RenderErrorBoundary, { RouteErrorFallback } from '../../src/components/Routing/RenderErrorBoundary.jsx';
import usePixelNDVIWorkspace from '../../src/hooks/usePixelNDVIWorkspace.js';
import useNDVIRasterLayer from '../../src/hooks/useNDVIRasterLayer.js';
import AgronomyCaseMap from '../../src/components/Agronomy/AgronomyCaseMap.jsx';
import FieldListPanel from '../../src/components/Map/FieldListPanel.jsx';
import FieldDetailPanel from '../../src/components/Map/FieldDetailPanel.jsx';
import maplibregl from '../../src/maplibreRuntime.js';
import {
  createAnomalyWorkflowDraft,
  getAgronomyDraft,
  getOfflineDraft,
  listOfflineDrafts,
  purgeOfflineScope,
  saveAgronomyDraft,
  saveOfflineDraft,
} from '../../src/offline/offlineScoutingStore.js';
import {
  invalidateSession,
  logoutExplicitly,
  SESSION_TOKEN_KEY,
  SESSION_USER_KEY,
} from '../../src/auth/session.js';

// ─── Instrumentation ─────────────────────────────────────────────────────────
const liveUrls = new Set();
const nativeCreateObjectURL = URL.createObjectURL.bind(URL);
const nativeRevokeObjectURL = URL.revokeObjectURL.bind(URL);
URL.createObjectURL = (object) => {
  const url = nativeCreateObjectURL(object);
  liveUrls.add(url);
  return url;
};
URL.revokeObjectURL = (url) => {
  liveUrls.delete(url);
  nativeRevokeObjectURL(url);
};

const liveIntervals = new Set();
const nativeSetInterval = window.setInterval.bind(window);
const nativeClearInterval = window.clearInterval.bind(window);
window.setInterval = (callback, delay, ...args) => {
  const id = nativeSetInterval(callback, delay, ...args);
  liveIntervals.add(id);
  return id;
};
window.clearInterval = (id) => {
  liveIntervals.delete(id);
  nativeClearInterval(id);
};

let deleteDatabaseCalls = 0;
const nativeDeleteDatabase = indexedDB.deleteDatabase.bind(indexedDB);
indexedDB.deleteDatabase = (...args) => {
  deleteDatabaseCalls += 1;
  return nativeDeleteDatabase(...args);
};

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
async function waitFor(predicate, label, timeout = 10000) {
  const deadline = performance.now() + timeout;
  while (performance.now() < deadline) {
    if (predicate()) return;
    await wait(25);
  }
  throw new Error(`Timed out: ${label}`);
}

function mount(element) {
  const container = document.createElement('div');
  container.style.width = '480px';
  container.style.height = '320px';
  document.getElementById('root').appendChild(container);
  const root = createRoot(container);
  flushSync(() => root.render(element));
  return {
    container,
    render(next) { flushSync(() => root.render(next)); },
    unmount() { flushSync(() => root.unmount()); container.remove(); },
  };
}

// A MapLibre stand-in that records every listener, source and layer.
function createFakeMap(name) {
  const handlers = new Map();
  const sources = new Map();
  const layers = new Map();
  const keyOf = (type, layerOrHandler) => (typeof layerOrHandler === 'string' ? `${type}::${layerOrHandler}` : type);
  const map = {
    name,
    _removed: false,
    on(type, layerOrHandler, maybeHandler) {
      const handler = typeof layerOrHandler === 'function' ? layerOrHandler : maybeHandler;
      const key = keyOf(type, layerOrHandler);
      if (!handlers.has(key)) handlers.set(key, new Set());
      handlers.get(key).add(handler);
      return map;
    },
    off(type, layerOrHandler, maybeHandler) {
      const handler = typeof layerOrHandler === 'function' ? layerOrHandler : maybeHandler;
      handlers.get(keyOf(type, layerOrHandler))?.delete(handler);
      return map;
    },
    fire(type, event = {}) {
      for (const handler of [...(handlers.get(type) || [])]) handler(event);
    },
    isStyleLoaded: () => true,
    getLayer: (id) => layers.get(id),
    getSource: (id) => sources.get(id),
    addSource(id, spec) {
      if (sources.has(id)) throw new Error(`duplicate source ${id}`);
      sources.set(id, { ...spec, updateImage(next) { Object.assign(this, next); return this; } });
    },
    addLayer(spec) {
      if (layers.has(spec.id)) throw new Error(`duplicate layer ${spec.id}`);
      if (!sources.has(spec.source)) throw new Error(`missing source ${spec.source}`);
      layers.set(spec.id, spec);
    },
    removeLayer(id) { layers.delete(id); },
    removeSource(id) {
      if ([...layers.values()].some((layer) => layer.source === id)) throw new Error(`source ${id} still in use`);
      sources.delete(id);
    },
    setPaintProperty(id, property, value) {
      const layer = layers.get(id);
      if (layer) layer.paint = { ...layer.paint, [property]: value };
    },
    counts() {
      let listeners = 0;
      for (const set of handlers.values()) listeners += set.size;
      return { listeners, sources: sources.size, layers: layers.size };
    },
  };
  return map;
}

// ─── ErrorBoundary ───────────────────────────────────────────────────────────
let throwing = true;
let renders = 0;
function Thrower() {
  renders += 1;
  if (throwing) throw new Error('render failure fixture');
  return <p data-testid="recovered">Раздел восстановлен</p>;
}
function BoundaryHost({ resetKey }) {
  return (
    <div>
      <nav data-testid="shell-nav">Навигация приложения</nav>
      <RenderErrorBoundary
        resetKey={resetKey}
        fallback={({ reset }) => <RouteErrorFallback onRetry={reset} onNavigateHome={() => { window.__task226NavigatedHome = true; reset(); }} />}
      >
        <Thrower />
      </RenderErrorBoundary>
    </div>
  );
}
const fallbackShown = (host) => Boolean(host.container.querySelector('[data-testid="route-error-boundary"]'));
const button = (host, label) => [...host.container.querySelectorAll('button')].find((item) => item.textContent.trim() === label);
async function press(host, label) {
  button(host, label).click();
  await wait(30);
}

async function runErrorBoundary() {
  throwing = true;
  renders = 0;
  const host = mount(<BoundaryHost resetKey="/first" />);
  const result = {
    fallbackShown: fallbackShown(host),
    fallbackText: host.container.querySelector('[data-testid="route-error-boundary"]')?.textContent || '',
    fallbackRole: host.container.querySelector('[data-testid="route-error-boundary"]')?.getAttribute('role'),
    shellKept: Boolean(host.container.querySelector('[data-testid="shell-nav"]')),
    childRendered: Boolean(host.container.querySelector('[data-testid="recovered"]')),
  };
  const rendersBeforeRetry = renders;
  await press(host, 'Повторить');
  result.stillFailingAfterRetry = fallbackShown(host);
  result.rendersPerRetry = renders - rendersBeforeRetry;
  const rendersSettled = renders;
  await wait(300);
  result.rendersWhileIdle = renders - rendersSettled;

  throwing = false;
  await press(host, 'Повторить');
  result.recoveredAfterRetry = Boolean(host.container.querySelector('[data-testid="recovered"]')) && !fallbackShown(host);

  throwing = true;
  host.render(<BoundaryHost resetKey="/second" />);
  result.failsAgainOnNewRoute = fallbackShown(host);
  throwing = false;
  host.render(<BoundaryHost resetKey="/third" />);
  result.resetByRouteChange = Boolean(host.container.querySelector('[data-testid="recovered"]')) && !fallbackShown(host);

  throwing = true;
  host.render(<BoundaryHost resetKey="/fourth" />);
  throwing = false;
  await press(host, 'На главную');
  result.homeActionResets = Boolean(window.__task226NavigatedHome) && Boolean(host.container.querySelector('[data-testid="recovered"]'));
  host.unmount();
  return result;
}

// ─── MapLibre ownership: usePixelNDVIWorkspace ──────────────────────────────
function PixelHost({ map, fieldId = 7, enabled = true, comparisonEnabled = false, sink }) {
  sink.current = usePixelNDVIWorkspace({ map, fieldId, enabled, opacity: 0.8, comparisonEnabled, divider: 40 });
  return null;
}

async function runPixelWorkspace() {
  const baseline = { urls: liveUrls.size, intervals: liveIntervals.size };
  const delta = () => ({ urls: liveUrls.size - baseline.urls, intervals: liveIntervals.size - baseline.intervals });
  const mapA = createFakeMap('A');
  const mapB = createFakeMap('B');
  const sink = { current: null };
  const host = mount(<PixelHost map={mapA} comparisonEnabled sink={sink} />);
  await waitFor(() => sink.current?.layer.status === 'ready' && mapA.counts().layers === 2, 'pixel workspace ready on map A');
  const ready = { mapA: mapA.counts(), ...delta() };

  mapA.fire('click', { lngLat: { lng: 64.41, lat: 39.81 } });
  await waitFor(() => sink.current?.sample.status === 'ready', 'pixel sample on map A');

  host.render(<PixelHost map={mapB} comparisonEnabled sink={sink} />);
  await waitFor(() => sink.current?.layer.status === 'ready' && mapB.counts().layers === 2, 'pixel workspace ready on map B');
  const afterOwnershipChange = { mapA: mapA.counts(), mapB: mapB.counts(), ...delta() };

  host.render(<PixelHost map={mapB} comparisonEnabled={false} sink={sink} />);
  await waitFor(() => sink.current?.layer.status === 'ready' && mapB.counts().layers === 1, 'comparison off keeps one layer');
  const afterComparisonOff = { mapB: mapB.counts(), ...delta() };

  host.render(<PixelHost map={mapB} enabled={false} sink={sink} />);
  await wait(50);
  const afterDisable = { mapB: mapB.counts(), ...delta() };

  host.unmount();
  await wait(100);
  return { ready, afterOwnershipChange, afterComparisonOff, afterDisable, afterUnmount: { mapA: mapA.counts(), mapB: mapB.counts(), ...delta() } };
}

async function runPixelWorkspacePartialFailure() {
  const baseline = { urls: liveUrls.size, intervals: liveIntervals.size };
  const map = createFakeMap('partial-failure');
  const sink = { current: null };
  const host = mount(<PixelHost map={map} fieldId={8} comparisonEnabled sink={sink} />);
  await waitFor(() => sink.current?.layer.status === 'error', 'scene B failure surfaces as an error');
  await wait(100);
  const whileMounted = {
    status: sink.current.layer.status,
    errorStatus: sink.current.layer.errorStatus,
    liveUrls: liveUrls.size - baseline.urls,
    map: map.counts(),
  };
  host.unmount();
  await wait(100);
  return {
    whileMounted,
    afterUnmount: { liveUrls: liveUrls.size - baseline.urls, intervals: liveIntervals.size - baseline.intervals, map: map.counts() },
  };
}

// ─── MapLibre ownership: useNDVIRasterLayer ─────────────────────────────────
function RasterHost({ map, enabled = true, sink }) {
  sink.current = useNDVIRasterLayer({ map, fieldId: 7, enabled, dateTo: '2026-09-24', opacity: 0.7 });
  return null;
}

async function runRasterLayer() {
  const baseline = liveUrls.size;
  const mapA = createFakeMap('raster-A');
  const mapB = createFakeMap('raster-B');
  const sink = { current: null };
  const host = mount(<RasterHost map={mapA} sink={sink} />);
  await waitFor(() => sink.current?.status === 'ready' && mapA.counts().layers === 1, 'raster ready on map A');
  const ready = { mapA: mapA.counts(), urls: liveUrls.size - baseline };
  mapA.fire('style.load');
  const afterStyleReload = { mapA: mapA.counts() };
  host.render(<RasterHost map={mapB} sink={sink} />);
  await waitFor(() => sink.current?.status === 'ready' && mapB.counts().layers === 1, 'raster ready on map B');
  const afterOwnershipChange = { mapA: mapA.counts(), mapB: mapB.counts(), urls: liveUrls.size - baseline };
  host.render(<RasterHost map={mapB} enabled={false} sink={sink} />);
  await wait(50);
  const afterDisable = { mapB: mapB.counts(), urls: liveUrls.size - baseline };
  host.unmount();
  await wait(50);
  return { ready, afterStyleReload, afterOwnershipChange, afterDisable, afterUnmount: { mapA: mapA.counts(), mapB: mapB.counts(), urls: liveUrls.size - baseline } };
}

// ─── Real MapLibre owner: AgronomyCaseMap ───────────────────────────────────
async function runAgronomyCaseMap() {
  const nativeRemove = maplibregl.Map.prototype.remove;
  let created = 0;
  let removed = 0;
  maplibregl.Map.prototype.remove = function countedRemove(...args) {
    removed += 1;
    return nativeRemove.apply(this, args);
  };
  const geometry = { type: 'Polygon', coordinates: [[[64.4, 39.8], [64.41, 39.8], [64.41, 39.81], [64.4, 39.81], [64.4, 39.8]]] };
  const workItems = [{ id: 1, work_geometry: { type: 'Point', coordinates: [64.405, 39.805] } }];
  try {
    for (let cycle = 0; cycle < 5; cycle += 1) {
      const host = mount(<AgronomyCaseMap geometry={geometry} workItems={workItems} />);
      await waitFor(() => host.container.querySelector('canvas'), 'agronomy map canvas');
      created += 1;
      await wait(150);
      host.unmount();
    }
  } finally {
    maplibregl.Map.prototype.remove = nativeRemove;
  }
  return {
    created,
    removed,
    canvases: document.querySelectorAll('.maplibregl-canvas').length,
    mapContainers: document.querySelectorAll('.maplibregl-map').length,
  };
}

// ─── Offline partitions and the session model (real IndexedDB) ──────────────
async function runOfflineStore() {
  const scopeA = '7:5';
  const scopeB = '7:6';
  const userA = { id: 5, enterprise_id: 7, role: 'agronomist' };
  const userB = { id: 6, enterprise_id: 7, role: 'agronomist' };
  deleteDatabaseCalls = 0;
  const draftA = createAnomalyWorkflowDraft({ scope: scopeA, inspectionId: 41, baseVersion: 3, finding: { cause: 'pest', inspected_at: '2026-09-24T10:00' } });
  await saveOfflineDraft({ ...draftA, status: 'pending_sync' });
  await saveOfflineDraft(createAnomalyWorkflowDraft({ scope: scopeB, inspectionId: 41, baseVersion: 3, finding: { cause: 'disease' } }));
  await saveAgronomyDraft(userA, 9, { reason: 'Основание плана', work: { dueInput: '2026-10-0' } });
  const listed = {
    a: (await listOfflineDrafts(scopeA)).map((item) => `${item.kind}:${item.inspectionId || item.planId}:${item.status}`).sort(),
    b: (await listOfflineDrafts(scopeB)).map((item) => `${item.kind}:${item.inspectionId || item.planId}:${item.status}`).sort(),
  };

  localStorage.setItem(SESSION_TOKEN_KEY, 'token-A');
  localStorage.setItem(SESSION_USER_KEY, JSON.stringify(userA));
  const invalidated = invalidateSession({ reason: 'unauthorized', failedToken: 'token-A' });
  const afterInvalidation = {
    token: localStorage.getItem(SESSION_TOKEN_KEY),
    user: localStorage.getItem(SESSION_USER_KEY),
    a: (await listOfflineDrafts(scopeA)).length,
    b: (await listOfflineDrafts(scopeB)).length,
    draftAStatus: (await getOfflineDraft(scopeA, 41))?.status || null,
    agronomyDraftA: Boolean(await getAgronomyDraft(userA, 9)),
    crossScopeRead: await getOfflineDraft(scopeB, 41).then((record) => record?.finding?.cause || null),
    agronomyDraftForB: Boolean(await getAgronomyDraft(userB, 9)),
  };

  localStorage.setItem(SESSION_TOKEN_KEY, 'token-B');
  await logoutExplicitly(userB);
  const afterExplicitLogoutB = {
    token: localStorage.getItem(SESSION_TOKEN_KEY),
    a: (await listOfflineDrafts(scopeA)).length,
    b: (await listOfflineDrafts(scopeB)).length,
  };
  const purgedEmpty = await purgeOfflineScope(null);
  await logoutExplicitly(userA);
  const afterExplicitLogoutA = { a: (await listOfflineDrafts(scopeA)).length, b: (await listOfflineDrafts(scopeB)).length };
  return { listed, invalidated, afterInvalidation, afterExplicitLogoutB, purgedEmpty, afterExplicitLogoutA, deleteDatabaseCalls };
}

// ─── Error is not empty ──────────────────────────────────────────────────────
async function runFieldListStates() {
  let retries = 0;
  const props = {
    enterprises: [], selectedFieldId: null, highlightedFieldId: null, onFieldSelect() {}, onFieldHover() {},
    onOpenFullDetail() {}, onNavigate() {}, enterpriseId: null, coverageMap: null, coverageLoading: false, selectedMapMode: 'crop',
  };
  const host = mount(<FieldListPanel {...props} fields={[]} loadState="loading" onRetry={() => { retries += 1; }} />);
  const text = () => host.container.textContent;
  const loading = { loadingShown: text().includes('Загружаем поля…'), emptyShown: text().includes('Нет полей') };
  host.render(<FieldListPanel {...props} fields={[]} loadState="failed" onRetry={() => { retries += 1; }} />);
  const failed = {
    errorShown: text().includes('Не удалось загрузить поля.'),
    emptyShown: text().includes('Нет полей'),
    count: host.container.querySelector('#field-list-panel h2')?.nextElementSibling?.textContent?.trim(),
    role: host.container.querySelector('[role="alert"]')?.textContent?.includes('Не удалось загрузить поля.') || false,
  };
  button(host, 'Повторить').click();
  host.render(<FieldListPanel {...props} fields={[]} loadState="ready" onRetry={() => { retries += 1; }} />);
  const readyEmpty = { emptyShown: text().includes('Нет полей'), errorShown: text().includes('Не удалось загрузить поля.') };
  host.unmount();
  return { loading, failed, readyEmpty, retries };
}

async function runFieldDetailPanelStates() {
  const field = (id) => ({ id, name: `Поле ${id}`, enterprise_name: 'Тестовое предприятие' });
  const alertsBlock = (host) => [...host.container.querySelectorAll('h4')].find((item) => item.textContent.startsWith('Алерты'))?.parentElement;
  const failedHost = mount(<FieldDetailPanel field={field(77)} onBack={() => {}} />);
  await waitFor(() => /Не удалось загрузить алерты/.test(alertsBlock(failedHost)?.textContent || ''), 'failed alerts state', 15000);
  const failed = {
    heading: alertsBlock(failedHost).querySelector('h4').textContent.trim(),
    healthyEmptyShown: /Нет активных алертов/.test(alertsBlock(failedHost).textContent),
    role: alertsBlock(failedHost).querySelector('[role="alert"]') ? 'alert' : null,
  };
  failedHost.unmount();
  const emptyHost = mount(<FieldDetailPanel field={field(78)} onBack={() => {}} />);
  await waitFor(() => /Нет активных алертов/.test(alertsBlock(emptyHost)?.textContent || ''), 'available empty alerts state');
  const empty = { heading: alertsBlock(emptyHost).querySelector('h4').textContent.trim(), healthyEmptyShown: true };
  emptyHost.unmount();
  return { failed, empty };
}

window.__task226 = {
  runFieldListStates,
  runFieldDetailPanelStates,
  runErrorBoundary,
  runPixelWorkspace,
  runPixelWorkspacePartialFailure,
  runRasterLayer,
  runAgronomyCaseMap,
  runOfflineStore,
};
window.__task226Ready = true;
