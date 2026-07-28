import {
  attachInspectionEvidence,
  createCorrectiveAction,
  recordInspectionResult,
} from '../api/fieldInspections.js';
import {
  listOfflineQueue,
  markOfflineSynchronized,
  updateOfflineQueue,
} from './offlineScoutingStore.js';

export function classifyOfflineSyncError(error) {
  const status = Number(error?.response?.status || 0);
  if (status === 409) return { status: 'conflict', category: 'conflict', retryable: false };
  if (status === 401) return { status: 'failed', category: 'authentication', retryable: false };
  if (status === 403) return { status: 'failed', category: 'authorization', retryable: false };
  if (status === 404) return { status: 'failed', category: 'not_found', retryable: false };
  if (status === 422) return { status: 'failed', category: 'validation', retryable: false };
  if (!status || status >= 500 || error?.code === 'ECONNABORTED') {
    return { status: 'queued', category: 'transient', retryable: true };
  }
  return { status: 'failed', category: 'server', retryable: false };
}

const canceled = (error) => error?.name === 'AbortError' || error?.code === 'ERR_CANCELED';

export async function syncOfflineQueueItem(item, signal) {
  let current = await updateOfflineQueue({ ...item, status: 'syncing', failure: null });
  try {
    let progress = { ...current.progress };
    if (!progress.resultId) {
      const result = await recordInspectionResult(
        current.inspectionId,
        { ...current.result, expected_version: current.baseVersion },
        current.idempotency.result,
        signal,
      );
      progress = {
        ...progress,
        resultId: result.id,
        currentVersion: current.baseVersion + 1,
      };
      current = await updateOfflineQueue({ ...current, progress, status: 'syncing' });
    }

    for (
      let index = progress.nextEvidenceIndex;
      index < current.evidence.length;
      index += 1
    ) {
      await attachInspectionEvidence(
        current.inspectionId,
        {
          ...current.evidence[index],
          result_id: progress.resultId,
          expected_version: progress.currentVersion,
        },
        current.idempotency.evidence[index],
        signal,
      );
      progress = {
        ...progress,
        currentVersion: progress.currentVersion + 1,
        nextEvidenceIndex: index + 1,
      };
      current = await updateOfflineQueue({ ...current, progress, status: 'syncing' });
    }

    if (current.action && !progress.actionDone) {
      await createCorrectiveAction(
        current.inspectionId,
        {
          ...current.action,
          result_id: progress.resultId,
          expected_inspection_version: progress.currentVersion,
        },
        current.idempotency.action,
        signal,
      );
      progress = {
        ...progress,
        currentVersion: progress.currentVersion + 1,
        actionDone: true,
      };
      current = await updateOfflineQueue({ ...current, progress, status: 'syncing' });
    }

    await markOfflineSynchronized(current.scope, current.inspectionId);
    return { inspectionId: current.inspectionId, status: 'synchronized' };
  } catch (error) {
    if (canceled(error)) throw error;
    const classification = classifyOfflineSyncError(error);
    await updateOfflineQueue({
      ...current,
      status: classification.status,
      failure: {
        category: classification.category,
        retryable: classification.retryable,
        occurredAt: new Date().toISOString(),
      },
    });
    return {
      inspectionId: current.inspectionId,
      status: classification.status,
      category: classification.category,
      retryable: classification.retryable,
    };
  }
}

export async function syncOfflineQueue(scope, signal, onProgress) {
  const items = await listOfflineQueue(scope);
  const results = [];
  for (let index = 0; index < items.length; index += 1) {
    if (signal?.aborted) throw new DOMException('Synchronization aborted', 'AbortError');
    onProgress?.({ current: index + 1, total: items.length, inspectionId: items[index].inspectionId });
    results.push(await syncOfflineQueueItem(items[index], signal));
  }
  return results;
}
