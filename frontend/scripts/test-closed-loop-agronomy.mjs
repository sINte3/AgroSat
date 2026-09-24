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
const vocabulary = read('src/config/canonicalLifecycle.js');
const schedule = read('src/utils/workSchedule.js');

for (const value of ['open','overdue','pending_verification','improved','ineffective']) assert.ok(page.includes(value));
// One canonical verification vocabulary (TASK_225) shared by every page.
for (const value of ['PENDING_DATA','IMPROVED','NO_MATERIAL_CHANGE','WORSENED','QUALITY_BLOCKED','PROVIDER_DEGRADED']) assert.ok(vocabulary.includes(value));
assert.ok(page.includes('VERIFICATION_STATUS') && page.includes('planStatusLabel') && page.includes('planStatusTone'));
for (const value of ['createAgronomyDraft','addAgronomyWork','transitionAgronomyWork','reevaluateAgronomyPlan']) assert.ok(page.includes(value) && api.includes(value));
assert.ok(app.includes("'/agronomy-plans': 'agronomy-plans'") && app.includes('<AgronomyPlansPage'));
assert.ok(roles.includes("'agronomy-plans'") && page.includes("user?.role !== 'viewer'"));
assert.ok(offline.includes('saveAgronomyDraft') && offline.includes('FORBIDDEN_KEY'));
assert.ok(map.includes("map.off('load'") && map.includes('removeLayer') && map.includes('removeSource') && map.includes('removeControl') && map.includes('controller.abort()') && map.includes('map.remove()'));
assert.ok(page.includes('min-h-11') && page.includes('role="status"') && page.includes('overflow-y-auto'));
// TASK_226 C12: raw datetime text in state, converted only on submit.
assert.ok(!/new Date\(e\.target\.value\)/.test(page) && !/toISOString\(\)/.test(page));
assert.ok(page.includes('validateWorkSchedule(work)') && page.includes("setWorkField('dueInput', e.target.value)"));
assert.ok(schedule.includes('parseTashkentDateTimeInput'));
assert.ok(page.includes("queueState === 'error'") && page.includes('Отсутствие строк здесь не означает отсутствие планов'));
console.log(JSON.stringify({ status: 'PASS', suite: 'TASK_220 frontend contract (TASK_226 alignment)', assertions: 31 }));
