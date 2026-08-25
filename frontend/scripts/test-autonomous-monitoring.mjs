import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const page = readFileSync(resolve(root, 'src/pages/MonitoringPage.jsx'), 'utf8');
const api = readFileSync(resolve(root, 'src/api/monitoring.js'), 'utf8');
const app = readFileSync(resolve(root, 'src/App.jsx'), 'utf8');
const roles = readFileSync(resolve(root, 'src/config/roleAccess.js'), 'utf8');

for (const contract of ['getMonitoringStatus', 'getMonitoringFreshness', 'getMonitoringCandidates']) {
  assert.ok(page.includes(contract), `missing ${contract}`);
}
for (const state of ['FRESH', 'AGING', 'STALE', 'NEVER_COLLECTED', 'CLOUD_BLOCKED', 'PROVIDER_DEGRADED', 'QUALITY_BLOCKED']) {
  assert.ok(page.includes(state), `missing freshness state ${state}`);
}
for (const state of ['NEW', 'CONFIRMED', 'DISMISSED', 'INSPECTION_CREATED', 'RESOLVED', 'SUPERSEDED']) {
  assert.ok(page.includes(state), `missing candidate state ${state}`);
}
assert.ok(page.includes('AbortController') && page.includes("window.removeEventListener('offline'"));
assert.ok(page.includes("map.remove()") && page.includes("removeSource('monitoring-zones')") && page.includes("map.off('click'"));
assert.ok(page.includes('aria-busy') && page.includes('role="alert"') && page.includes('aria-describedby'));
assert.ok(page.includes('min-h-11') && page.includes("h-[calc(100vh-4rem)]"));
assert.ok(!page.includes('start') || !api.includes('all-active-fields'));
assert.ok(api.includes('__noRetry: true'));
assert.ok(app.includes("'/monitoring': 'monitoring'") && app.includes('<MonitoringPage'));
assert.ok(roles.includes("if (role === 'admin') return ADMIN_VIEW_KEYS.has(view)"));
assert.ok(!roles.includes("ADMIN_VIEW_KEYS.has(view);\n  if (role === 'manager'"));

console.log(JSON.stringify({ status: 'PASS', suite: 'TASK_219 frontend monitoring contract', assertions: 24 }));
