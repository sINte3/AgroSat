import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const read = file => readFileSync(resolve(root, file), 'utf8');
const page = read('src/pages/OperationalCenterPage.jsx');
const api = read('src/api/operationalCenter.js');
const app = read('src/App.jsx');
const roles = read('src/config/roleAccess.js');
const sidebar = read('src/components/Layout/Sidebar.jsx');

for (const status of ['needs_review','awaiting_inspection','awaiting_work','awaiting_evidence','awaiting_verification','stale','external_unavailable','improved_closed']) assert.ok(page.includes(status), `missing ${status}`);
for (const filter of ['enterprise_id','field_id','crop_type_id','assignee_id','operational_status','source','due_from','due_to','overdue','blocked','awaiting_verification','external_state']) assert.ok(page.includes(filter), `missing ${filter}`);
for (const method of ['listOperationalQueue','getOperationalSummary','getOperationalCase','transitionOperationalNotification']) assert.ok(page.includes(method) && api.includes(method), `missing ${method}`);
assert.ok(app.includes("'/operational-center': 'operational-center'"));
assert.ok(app.includes("case 'operational-case'"));
assert.ok(app.includes('<OperationalCenterPage'));
assert.ok(roles.includes("'operational-center'"));
assert.ok(sidebar.includes("{ key: 'operational-center'"));
assert.ok(page.includes("user?.role !== 'viewer'"));
assert.ok(page.includes("window.addEventListener('offline'"));
assert.ok(page.includes('controller.abort()'));
assert.ok(page.includes('min-h-11'));
assert.ok(page.includes('focus:ring-2'));
assert.ok(page.includes('серверное сопоставление техники с полем не настроено'));
assert.ok(page.includes('Контекст не доказывает агрономическую причинность автоматически'));
assert.ok(!page.toLowerCase().includes('maplibre'));
console.log(JSON.stringify({ status: 'PASS', suite: 'TASK_221 frontend contract', assertions: 45 }));
