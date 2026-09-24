const DATABASE_NAME = 'agrosat-offline-scouting';
const DATABASE_VERSION = 1;
const SNAPSHOTS = 'snapshots';
const DRAFTS = 'drafts';
const QUEUE = 'queue';
const STORES = Object.freeze([SNAPSHOTS, DRAFTS, QUEUE]);
const MAX_SNAPSHOTS = 100;
const MAX_DRAFTS = 100;
const MAX_QUEUE = 200;
const MAX_RECORD_BYTES = 512 * 1024;
const FORBIDDEN_KEY = /(^|_)(access_?token|refresh_?token|password|authorization|cookie|jwt)($|_)/i;

const indexedDbAvailable = () => (
  typeof window !== 'undefined'
  && typeof window.indexedDB !== 'undefined'
);

const positiveId = (value) => {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
};

export function offlineScope(user) {
  const enterpriseId = positiveId(user?.enterprise_id);
  const userId = positiveId(user?.id);
  return enterpriseId && userId ? `${enterpriseId}:${userId}` : null;
}

function assertSafeValue(value, path = 'record', seen = new WeakSet()) {
  if (value == null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return;
  if (
    (typeof Blob !== 'undefined' && value instanceof Blob)
    || (typeof File !== 'undefined' && value instanceof File)
    || value instanceof ArrayBuffer
    || ArrayBuffer.isView(value)
  ) {
    throw new Error(`${path} contains unsupported binary content`);
  }
  if (typeof value !== 'object') throw new Error(`${path} contains an unsupported value`);
  if (seen.has(value)) throw new Error(`${path} contains a cycle`);
  seen.add(value);
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertSafeValue(item, `${path}[${index}]`, seen));
  } else {
    Object.entries(value).forEach(([key, item]) => {
      if (FORBIDDEN_KEY.test(key)) throw new Error(`${path} contains a forbidden credential field`);
      assertSafeValue(item, `${path}.${key}`, seen);
    });
  }
  seen.delete(value);
}

function safeRecord(record) {
  assertSafeValue(record);
  const encoded = JSON.stringify(record);
  if (new TextEncoder().encode(encoded).byteLength > MAX_RECORD_BYTES) {
    throw new Error('offline record exceeds 512 KiB');
  }
  return record;
}

function openDatabase() {
  if (!indexedDbAvailable()) return Promise.reject(new Error('IndexedDB is unavailable'));
  return new Promise((resolve, reject) => {
    const request = window.indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      for (const name of STORES) {
        if (!database.objectStoreNames.contains(name)) {
          database.createObjectStore(name, { keyPath: 'key' });
        }
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('IndexedDB open failed'));
    request.onblocked = () => reject(new Error('IndexedDB upgrade is blocked'));
  });
}

async function storeRequest(storeName, mode, operation) {
  const database = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction(storeName, mode);
      const store = transaction.objectStore(storeName);
      let request;
      try {
        request = operation(store);
      } catch (error) {
        reject(error);
        return;
      }
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error('IndexedDB request failed'));
      transaction.onabort = () => reject(transaction.error || new Error('IndexedDB transaction aborted'));
    });
  } finally {
    database.close();
  }
}

const getAll = (storeName) => storeRequest(storeName, 'readonly', (store) => store.getAll());
const put = (storeName, value) => storeRequest(storeName, 'readwrite', (store) => store.put(safeRecord(value)));
const remove = (storeName, key) => storeRequest(storeName, 'readwrite', (store) => store.delete(key));

async function prune(storeName, scope, maximum) {
  const records = (await getAll(storeName))
    .filter((item) => item.scope === scope)
    .sort((left, right) => String(right.updatedAt).localeCompare(String(left.updatedAt)));
  await Promise.all(records.slice(maximum).map((item) => remove(storeName, item.key)));
}

export async function cacheAssignedInspections(scope, value) {
  if (!scope) return;
  const items = Array.isArray(value?.items) ? value.items.slice(0, MAX_SNAPSHOTS) : [];
  await put(SNAPSHOTS, {
    key: `${scope}:assigned-list`,
    scope,
    kind: 'assigned-list',
    data: { ...value, items },
    updatedAt: new Date().toISOString(),
    schemaVersion: DATABASE_VERSION,
  });
  await prune(SNAPSHOTS, scope, MAX_SNAPSHOTS);
}

export async function getCachedAssignedInspections(scope) {
  if (!scope) return null;
  const record = await storeRequest(SNAPSHOTS, 'readonly', (store) => store.get(`${scope}:assigned-list`));
  return record?.scope === scope ? record.data : null;
}

export function createAnomalyWorkflowDraft({ scope, inspectionId, baseVersion, finding }) {
  const id = positiveId(inspectionId);
  const version = positiveId(baseVersion);
  if (!scope || !id || !version || !finding || typeof finding !== 'object') {
    throw new Error('inspection scope, version and finding are required');
  }
  const now = new Date().toISOString();
  return safeRecord({
    key: `${scope}:inspection:${id}`,
    scope,
    inspectionId: id,
    baseVersion: version,
    finding: { ...finding },
    status: 'local_draft',
    failure: null,
    createdAt: now,
    updatedAt: now,
    schemaVersion: 2,
  });
}

export async function markAnomalyDraftPending(draft) {
  return saveOfflineDraft({
    ...draft,
    status: 'pending_sync',
    failure: null,
  });
}

export async function markAnomalyDraftConflict(draft) {
  return saveOfflineDraft({
    ...draft,
    status: 'conflict',
    failure: { category: 'conflict', occurredAt: new Date().toISOString() },
  });
}

export async function saveOfflineDraft(draft) {
  if (!draft?.scope || !positiveId(draft?.inspectionId)) throw new Error('invalid offline draft');
  await put(DRAFTS, { ...draft, updatedAt: new Date().toISOString() });
  await prune(DRAFTS, draft.scope, MAX_DRAFTS);
  return draft;
}

export async function getOfflineDraft(scope, inspectionId) {
  const id = positiveId(inspectionId);
  if (!scope || !id) return null;
  const record = await storeRequest(DRAFTS, 'readonly', (store) => store.get(`${scope}:inspection:${id}`));
  return record?.scope === scope ? record : null;
}

export async function saveAgronomyDraft(user, planId, data) {
  const scope = offlineScope(user);
  const id = positiveId(planId);
  if (!scope || !id) throw new Error('agronomy plan scope is required');
  const record = { key: `${scope}:agronomy:${id}`, scope, kind: 'agronomy-draft', planId: id, ...data, updatedAt: new Date().toISOString(), schemaVersion: DATABASE_VERSION };
  await put(DRAFTS, record);
  await prune(DRAFTS, scope, MAX_DRAFTS);
  return record;
}

export async function getAgronomyDraft(user, planId) {
  const scope = offlineScope(user);
  const id = positiveId(planId);
  if (!scope || !id) return null;
  const record = await storeRequest(DRAFTS, 'readonly', store => store.get(`${scope}:agronomy:${id}`));
  return record?.scope === scope && record?.kind === 'agronomy-draft' ? record : null;
}

export async function removeAgronomyDraft(user, planId) {
  const scope = offlineScope(user);
  const id = positiveId(planId);
  if (scope && id) await remove(DRAFTS, `${scope}:agronomy:${id}`);
}

export async function discardOfflineDraft(scope, inspectionId) {
  const id = positiveId(inspectionId);
  if (!scope || !id) return;
  const key = `${scope}:inspection:${id}`;
  await Promise.all([remove(DRAFTS, key), remove(QUEUE, key)]);
}

function describeDraft(record, store) {
  if (record?.kind === 'agronomy-draft') {
    return { key: record.key, kind: 'agronomy-plan', planId: positiveId(record.planId), status: 'local_draft', updatedAt: record.updatedAt || null };
  }
  if (store === DRAFTS && record?.schemaVersion === 2) {
    return {
      key: record.key,
      kind: 'inspection-finding',
      inspectionId: positiveId(record.inspectionId),
      status: record.status || 'local_draft',
      updatedAt: record.updatedAt || null,
    };
  }
  // TASK_209 drafts and queue items: their write endpoints are retired (HTTP 410),
  // so they stay readable on this device but can no longer be synchronized.
  return {
    key: record.key,
    kind: 'legacy-inspection',
    inspectionId: positiveId(record?.inspectionId),
    status: 'legacy_unsynchronizable',
    updatedAt: record?.updatedAt || null,
  };
}

/**
 * Unsynchronized local records of one partition. Records of any other
 * partition are never returned.
 */
export async function listOfflineDrafts(scope) {
  if (!scope) return [];
  const [drafts, queue] = await Promise.all([getAll(DRAFTS), getAll(QUEUE)]);
  const described = new Map();
  for (const record of drafts) {
    if (record?.scope === scope) described.set(record.key, describeDraft(record, DRAFTS));
  }
  for (const record of queue) {
    if (record?.scope === scope && !described.has(record.key)) described.set(record.key, describeDraft(record, QUEUE));
  }
  return Array.from(described.values())
    .sort((left, right) => String(right.updatedAt).localeCompare(String(left.updatedAt)));
}

/**
 * Removes one partition only (the product contract for an explicit logout).
 * Other users' partitions, including their unsynchronized drafts, are kept.
 */
export async function purgeOfflineScope(scope) {
  if (!scope) return { snapshots: 0, drafts: 0, queue: 0 };
  const removed = {};
  for (const storeName of STORES) {
    const records = (await getAll(storeName)).filter((item) => item?.scope === scope);
    await Promise.all(records.map((item) => remove(storeName, item.key)));
    removed[storeName] = records.length;
  }
  return { snapshots: removed[SNAPSHOTS], drafts: removed[DRAFTS], queue: removed[QUEUE] };
}

export const OFFLINE_SCOUTING_LIMITS = Object.freeze({
  databaseVersion: DATABASE_VERSION,
  maxSnapshots: MAX_SNAPSHOTS,
  maxDrafts: MAX_DRAFTS,
  maxQueue: MAX_QUEUE,
  maxRecordBytes: MAX_RECORD_BYTES,
});
