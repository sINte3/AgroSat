const DATABASE_NAME = 'agrosat-offline-scouting';
const DATABASE_VERSION = 1;
const SNAPSHOTS = 'snapshots';
const DRAFTS = 'drafts';
const QUEUE = 'queue';
const MAX_SNAPSHOTS = 100;
const MAX_DRAFTS = 100;
const MAX_QUEUE = 200;
const MAX_EVIDENCE = 20;
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

export function createOfflineIdempotencyKey() {
  const random = globalThis.crypto?.randomUUID?.()
    || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
  return `offline-${random}`.replace(/[^A-Za-z0-9._:-]/g, '-').slice(0, 64);
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
      for (const name of [SNAPSHOTS, DRAFTS, QUEUE]) {
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

export async function cacheInspectionDetail(scope, inspection) {
  const inspectionId = positiveId(inspection?.id);
  if (!scope || !inspectionId) return;
  await put(SNAPSHOTS, {
    key: `${scope}:inspection:${inspectionId}`,
    scope,
    kind: 'inspection-detail',
    inspectionId,
    data: inspection,
    updatedAt: new Date().toISOString(),
    schemaVersion: DATABASE_VERSION,
  });
  await prune(SNAPSHOTS, scope, MAX_SNAPSHOTS);
}

export async function getCachedInspectionDetail(scope, inspectionId) {
  const id = positiveId(inspectionId);
  if (!scope || !id) return null;
  const record = await storeRequest(SNAPSHOTS, 'readonly', (store) => store.get(`${scope}:inspection:${id}`));
  return record?.scope === scope ? record.data : null;
}

export function createOfflineDraft({ scope, inspection, result, evidence = [], action = null }) {
  const inspectionId = positiveId(inspection?.id);
  const baseVersion = positiveId(inspection?.version);
  if (!scope || !inspectionId || !baseVersion) throw new Error('inspection scope and version are required');
  if (!result || !result.cause_code) throw new Error('structured inspection result is required');
  if (!Array.isArray(evidence) || evidence.length > MAX_EVIDENCE) throw new Error('evidence metadata limit exceeded');
  const now = new Date().toISOString();
  const draft = {
    key: `${scope}:inspection:${inspectionId}`,
    scope,
    inspectionId,
    baseVersion,
    result: { ...result },
    evidence: evidence.map((item) => ({ ...item })),
    action: action ? { ...action } : null,
    idempotency: {
      result: createOfflineIdempotencyKey(),
      evidence: evidence.map(() => createOfflineIdempotencyKey()),
      action: action ? createOfflineIdempotencyKey() : null,
    },
    createdAt: now,
    updatedAt: now,
    schemaVersion: DATABASE_VERSION,
  };
  return safeRecord(draft);
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

export async function enqueueOfflineDraft(draft) {
  if (!draft?.scope || !positiveId(draft?.inspectionId)) throw new Error('invalid offline draft');
  const queued = {
    ...draft,
    status: 'queued',
    progress: {
      resultId: null,
      currentVersion: draft.baseVersion,
      nextEvidenceIndex: 0,
      actionDone: false,
    },
    failure: null,
    updatedAt: new Date().toISOString(),
  };
  await put(QUEUE, queued);
  await prune(QUEUE, draft.scope, MAX_QUEUE);
  return queued;
}

export async function listOfflineQueue(scope) {
  if (!scope) return [];
  return (await getAll(QUEUE))
    .filter((item) => item.scope === scope)
    .sort((left, right) => String(left.createdAt).localeCompare(String(right.createdAt)));
}

export async function updateOfflineQueue(item) {
  if (!item?.scope || !item?.key) throw new Error('invalid queue item');
  const updated = { ...item, updatedAt: new Date().toISOString() };
  await put(QUEUE, updated);
  return updated;
}

export async function discardOfflineDraft(scope, inspectionId) {
  const id = positiveId(inspectionId);
  if (!scope || !id) return;
  const key = `${scope}:inspection:${id}`;
  await Promise.all([remove(DRAFTS, key), remove(QUEUE, key)]);
}

export async function markOfflineSynchronized(scope, inspectionId) {
  return discardOfflineDraft(scope, inspectionId);
}

export async function purgeOfflineScoutingData() {
  if (!indexedDbAvailable()) return false;
  return new Promise((resolve, reject) => {
    const request = window.indexedDB.deleteDatabase(DATABASE_NAME);
    request.onsuccess = () => resolve(true);
    request.onerror = () => reject(request.error || new Error('offline data purge failed'));
    request.onblocked = () => reject(new Error('offline data purge is blocked'));
  });
}

export const OFFLINE_SCOUTING_LIMITS = Object.freeze({
  databaseVersion: DATABASE_VERSION,
  maxSnapshots: MAX_SNAPSHOTS,
  maxDrafts: MAX_DRAFTS,
  maxQueue: MAX_QUEUE,
  maxEvidence: MAX_EVIDENCE,
  maxRecordBytes: MAX_RECORD_BYTES,
});
