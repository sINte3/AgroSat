import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const read = file => readFileSync(resolve(root, file), 'utf8');
const page = read('src/pages/AgronomyPlansPage.jsx');
const map = read('src/components/Agronomy/AgronomyCaseMap.jsx');
const api = read('src/api/closedLoopAgronomy.js');
const app = read('src/App.jsx');
const roles = read('src/config/roleAccess.js');
const offline = read('src/offline/offlineScoutingStore.js');

for (const value of ['open','overdue','pending_verification','improved','ineffective']) assert.ok(page.includes(value));
for (const value of ['PENDING_DATA','IMPROVED','NO_MATERIAL_CHANGE','WORSENED','QUALITY_BLOCKED','PROVIDER_DEGRADED']) assert.ok(page.includes(value));
for (const value of ['createAgronomyDraft','addAgronomyWork','transitionAgronomyWork','reevaluateAgronomyPlan']) assert.ok(page.includes(value) && api.includes(value));
assert.ok(app.includes("'/agronomy-plans': 'agronomy-plans'") && app.includes('<AgronomyPlansPage'));
assert.ok(roles.includes("'agronomy-plans'") && page.includes("user?.role !== 'viewer'"));
assert.ok(offline.includes('saveAgronomyDraft') && offline.includes('FORBIDDEN_KEY'));
assert.ok(map.includes("map.off('load'") && map.includes('removeLayer') && map.includes('removeSource') && map.includes('removeControl') && map.includes('controller.abort()') && map.includes('map.remove()'));
assert.ok(page.includes('min-h-11') && page.includes('role="status"') && page.includes('overflow-y-auto'));
console.log(JSON.stringify({ status: 'PASS', suite: 'TASK_220 frontend contract', assertions: 23 }));
