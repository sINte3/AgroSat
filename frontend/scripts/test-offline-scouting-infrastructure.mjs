import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  OFFLINE_SCOUTING_LIMITS,
  createAnomalyWorkflowDraft,
  offlineScope,
} from '../src/offline/offlineScoutingStore.js';
import {
  isSessionRevoked,
  isTransientSessionFailure,
  readCachedActiveUser,
} from '../src/offline/offlineSession.js';


// TASK_226: the TASK_209 offline queue (result -> evidence -> corrective action)
// synchronized to endpoints TASK_225 retired (HTTP 410) and was removed with
// its unrouted UI. Offline scouting is the canonical finding draft of
// AnomalyInspectionDetail, partitioned per enterprise:user, and it survives an
// involuntary loss of the web session.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8');
const store = read('src/offline/offlineScoutingStore.js');
const session = read('src/auth/session.js');
const client = read('src/api/client.js');
const auth = read('src/context/AuthContext.jsx');
const sidebar = read('src/components/Layout/Sidebar.jsx');
const worker = read('public/sw.js');
const detail = read('src/components/Inspections/AnomalyInspectionDetail.jsx');
const queuePage = read('src/pages/AnomalyInspectionsPage.jsx');

assert.equal(offlineScope({ id: 5, enterprise_id: 7 }), '7:5');
assert.equal(offlineScope({ id: 5, enterprise_id: null }), null);
assert.deepEqual(OFFLINE_SCOUTING_LIMITS, {
  databaseVersion: 1,
  maxSnapshots: 100,
  maxDrafts: 100,
  maxQueue: 200,
  maxRecordBytes: 512 * 1024,
});

const draftA = createAnomalyWorkflowDraft({ scope: '7:5', inspectionId: 11, baseVersion: 3, finding: { cause: 'pest' } });
const draftB = createAnomalyWorkflowDraft({ scope: '7:6', inspectionId: 11, baseVersion: 3, finding: { cause: 'pest' } });
assert.notEqual(draftA.key, draftB.key, 'two users never share a draft key for the same inspection');
assert.ok(draftA.key.startsWith('7:5:') && draftB.key.startsWith('7:6:'));
assert.throws(
  () => createAnomalyWorkflowDraft({ scope: '7:5', inspectionId: 11, baseVersion: 3, finding: { refresh_token: 'x' } }),
  /forbidden credential field/,
);

const cachedUser = readCachedActiveUser({
  getItem: () => JSON.stringify({
    id: 5,
    role: 'agronomist',
    enterprise_id: 7,
    is_active: true,
  }),
});
assert.deepEqual(
  { id: cachedUser.id, role: cachedUser.role, enterprise_id: cachedUser.enterprise_id },
  { id: 5, role: 'agronomist', enterprise_id: 7 },
);
assert.equal(readCachedActiveUser({
  getItem: () => JSON.stringify({ id: 5, role: 'viewer', enterprise_id: 7, is_active: false }),
}), null);
assert.equal(readCachedActiveUser({
  getItem: () => JSON.stringify({ id: 5, role: 'owner', enterprise_id: 7, is_active: true }),
}), null);
assert.equal(isTransientSessionFailure({ code: 'ERR_NETWORK' }), true);
assert.equal(isTransientSessionFailure({ response: { status: 401 } }), false);
assert.equal(isSessionRevoked({ response: { status: 401 } }), true);
assert.equal(isSessionRevoked({ response: { status: 403 } }), true);
assert.equal(isSessionRevoked({ code: 'ERR_NETWORK' }), false);

for (const required of [
  'const DATABASE_VERSION = 1',
  'const MAX_SNAPSHOTS = 100',
  'const MAX_DRAFTS = 100',
  'const MAX_QUEUE = 200',
  'contains unsupported binary content',
  'export async function purgeOfflineScope(scope)',
  'export async function listOfflineDrafts(scope)',
  "item?.scope === scope",
]) {
  assert.ok(store.includes(required), `missing store contract: ${required}`);
}
assert.doesNotMatch(store, /deleteDatabase|purgeOfflineScoutingData|enqueueOfflineDraft|syncOfflineQueue/);
assert.ok(!fs.existsSync(path.join(root, 'src/offline/offlineScoutingSync.js')), 'retired TASK_209 sync is removed');

// A. involuntary session loss: storage credential only.
assert.match(session, /export function invalidateSession\(/);
assert.doesNotMatch(session.slice(session.indexOf('export function invalidateSession('), session.indexOf('export async function logoutExplicitly(')), /purgeOffline|indexedDB/);
assert.match(client, /status === 401 && !config\?\.__skipSessionInvalidation/);
assert.doesNotMatch(client, /purgeOffline|offlineScoutingStore|indexedDB/);
// B. explicit logout: only the user's own partition.
assert.match(session, /export async function logoutExplicitly\(user\)[\s\S]*purgeOfflineScope\(scope\)/);
assert.match(sidebar, /listOfflineDrafts\(scope\)/);
assert.match(sidebar, /Несинхронизированные черновики/);
assert.match(sidebar, /Выйти и удалить/);
// C. identity change: caches only.
assert.match(session, /export function beginSession\(token, user, previousUser\)/);
assert.match(auth, /SESSION_INVALIDATED_EVENT/);
assert.doesNotMatch(auth, /purgeOffline|deleteDatabase|indexedDB/);
assert.match(auth, /window\.addEventListener\('storage', handleStorageChange\)/);

// Recovery path after re-authentication.
assert.match(detail, /getOfflineDraft\(scope, detail\.id\)/);
assert.match(detail, /saved\.scope !== scope/);
assert.match(detail, /saved\.status === 'pending_sync' && navigator\.onLine/);
assert.match(queuePage, /Несинхронизированные черновики на этом устройстве/);

assert.match(worker, /url\.pathname\.startsWith\('\/api\/'\)/);
assert.doesNotMatch(worker, /sync|authorization|cookie|token/i);
assert.match(worker, /CACHE_NAME = `\$\{CACHE_PREFIX\}v1`/);

console.log(JSON.stringify({
  status: 'PASS',
  suite: 'Offline scouting contract (TASK_226 session model)',
  databaseVersion: 1,
  partition: draftA.scope,
  globalPurge: 0,
  involuntaryLossPurges: 0,
  explicitLogoutScope: 'own partition only',
  apiCachePaths: 0,
}));
