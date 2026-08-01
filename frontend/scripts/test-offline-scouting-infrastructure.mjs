import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  OFFLINE_SCOUTING_LIMITS,
  createOfflineDraft,
  offlineScope,
} from '../src/offline/offlineScoutingStore.js';
import { classifyOfflineSyncError } from '../src/offline/offlineScoutingSync.js';
import {
  isSessionRevoked,
  isTransientSessionFailure,
  readCachedActiveUser,
} from '../src/offline/offlineSession.js';


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8');
const store = read('src/offline/offlineScoutingStore.js');
const sync = read('src/offline/offlineScoutingSync.js');
const worker = read('public/sw.js');
const statusPanel = read('src/components/Inspections/OfflineScoutingStatus.jsx');
const draftPanel = read('src/components/Inspections/OfflineScoutingPanel.jsx');
const listPage = read('src/pages/FieldInspectionsPage.jsx');
const detailDrawer = read('src/components/Inspections/InspectionDetailDrawer.jsx');

assert.equal(offlineScope({ id: 5, enterprise_id: 7 }), '7:5');
assert.equal(offlineScope({ id: 5, enterprise_id: null }), null);
assert.deepEqual(OFFLINE_SCOUTING_LIMITS, {
  databaseVersion: 1,
  maxSnapshots: 100,
  maxDrafts: 100,
  maxQueue: 200,
  maxEvidence: 20,
  maxRecordBytes: 512 * 1024,
});

const draft = createOfflineDraft({
  scope: '7:5',
  inspection: { id: 11, version: 3 },
  result: { cause_code: 'irrigation', cause_details: 'Fixture observation' },
  evidence: [{ evidence_type: 'geolocation', latitude: 39.7, longitude: 64.1 }],
  action: {
    owner_id: 5,
    description: 'Inspect the irrigation outlet',
    due_date: '2026-08-01',
  },
});
assert.equal(draft.baseVersion, 3);
assert.equal(draft.evidence.length, 1);
assert.match(draft.idempotency.result, /^[A-Za-z0-9._:-]{8,64}$/);
assert.match(draft.idempotency.evidence[0], /^[A-Za-z0-9._:-]{8,64}$/);
assert.match(draft.idempotency.action, /^[A-Za-z0-9._:-]{8,64}$/);
assert.equal(new Set([
  draft.idempotency.result,
  draft.idempotency.evidence[0],
  draft.idempotency.action,
]).size, 3);

assert.throws(
  () => createOfflineDraft({
    scope: '7:5',
    inspection: { id: 11, version: 3 },
    result: { cause_code: 'irrigation', access_token: 'forbidden' },
  }),
  /forbidden credential field/,
);
assert.throws(
  () => createOfflineDraft({
    scope: '7:5',
    inspection: { id: 11, version: 3 },
    result: { cause_code: 'irrigation' },
    evidence: Array.from({ length: 21 }, () => ({ evidence_type: 'geolocation' })),
  }),
  /evidence metadata limit/,
);

assert.deepEqual(classifyOfflineSyncError({ response: { status: 409 } }), {
  status: 'conflict',
  category: 'conflict',
  retryable: false,
});
assert.deepEqual(classifyOfflineSyncError({ code: 'ECONNABORTED' }), {
  status: 'queued',
  category: 'transient',
  retryable: true,
});

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
  "const DATABASE_VERSION = 1",
  "const MAX_SNAPSHOTS = 100",
  "const MAX_DRAFTS = 100",
  "const MAX_QUEUE = 200",
  "const MAX_EVIDENCE = 20",
  'deleteDatabase(DATABASE_NAME)',
  'contains unsupported binary content',
]) {
  assert.ok(store.includes(required), `missing store contract: ${required}`);
}
for (const required of [
  'recordInspectionResult(',
  'attachInspectionEvidence(',
  'createCorrectiveAction(',
  'expected_version: progress.currentVersion',
  'expected_inspection_version: progress.currentVersion',
  'markOfflineSynchronized',
]) {
  assert.ok(sync.includes(required), `missing sync contract: ${required}`);
}
assert.match(worker, /url\.pathname\.startsWith\('\/api\/'\)/);
assert.doesNotMatch(worker, /sync|authorization|cookie|token/i);
assert.match(worker, /CACHE_NAME = `\$\{CACHE_PREFIX\}v1`/);
for (const required of [
  'Отправка не начнётся автоматически',
  'syncOfflineQueue(scope',
  "window.addEventListener('online'",
  "window.removeEventListener('online'",
]) {
  assert.ok(statusPanel.includes(required), `missing offline status contract: ${required}`);
}
for (const required of [
  'Офлайн-черновик осмотра',
  'Изменения ещё не являются серверными данными',
  'Конфликт версии',
  'syncOfflineQueueItem',
  'crypto.subtle.digest',
  'Файл не сохраняется офлайн и не загружается',
  'controllerRef.current?.abort()',
]) {
  assert.ok(draftPanel.includes(required), `missing offline draft contract: ${required}`);
}
assert.match(listPage, /getCachedAssignedInspections/);
assert.match(listPage, /state === 'offline'/);
assert.match(detailDrawer, /getCachedInspectionDetail/);
assert.match(detailDrawer, /offline=\{state === 'offline'\}/);

console.log(JSON.stringify({
  status: 'PASS',
  databaseVersion: 1,
  partition: draft.scope,
  orderedWriteSteps: 3,
  stableIdempotencyKeys: 3,
  maxEvidence: OFFLINE_SCOUTING_LIMITS.maxEvidence,
  apiCachePaths: 0,
  binaryPayloads: 0,
  manualSyncControls: 2,
  offlineFallbacks: 2,
}));
