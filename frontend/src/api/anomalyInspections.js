import client from './client';

// Canonical inspection lifecycle (/api/anomaly-inspections). Remediation after
// review is the agronomy-plan lifecycle in ./closedLoopAgronomy.js; the TASK_217
// corrective-action writes are retired by the backend (HTTP 410).
const noRetry = { __noRetry: true };

export function workflowKey(prefix = 'workflow') {
  const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  return `${prefix}-${random}`.replace(/[^A-Za-z0-9._:-]/g, '-').slice(0, 64);
}

export async function createAnomalyInspection(payload, idempotencyKey, signal) {
  const { data } = await client.post('anomaly-inspections', payload, {
    headers: { 'Idempotency-Key': idempotencyKey }, signal, ...noRetry,
  });
  return data;
}

export async function getAnomalyInspectionQueue(params = {}, signal) {
  const { data } = await client.get('anomaly-inspections/queue', { params, signal });
  return data;
}

export async function getInspectionAssignees({ enterpriseId, fieldId } = {}, signal) {
  const { data } = await client.get('anomaly-inspections/assignees', {
    params: {
      ...(enterpriseId ? { enterprise_id: enterpriseId } : {}),
      ...(fieldId ? { field_id: fieldId } : {}),
    }, signal,
  });
  return data;
}

export async function getAnomalyInspection(id, signal) {
  const { data } = await client.get(`anomaly-inspections/${id}`, { signal });
  return data;
}

export async function assignAnomalyInspection(id, payload, signal) {
  const { data } = await client.post(`anomaly-inspections/${id}/assignment`, payload, { signal, ...noRetry });
  return data;
}

export async function startAnomalyInspection(id, expectedVersion, signal) {
  const { data } = await client.post(`anomaly-inspections/${id}/start`, { expected_version: expectedVersion }, { signal, ...noRetry });
  return data;
}

export async function saveAnomalyFinding(id, payload, signal) {
  const { data } = await client.put(`anomaly-inspections/${id}/finding`, payload, { signal, ...noRetry });
  return data;
}

export async function submitAnomalyInspection(id, expectedVersion, signal) {
  const { data } = await client.post(`anomaly-inspections/${id}/submit`, { expected_version: expectedVersion }, { signal, ...noRetry });
  return data;
}

export async function reviewAnomalyInspection(id, payload, signal) {
  const { data } = await client.post(`anomaly-inspections/${id}/review`, payload, { signal, ...noRetry });
  return data;
}

/**
 * Cancels an open canonical inspection, or closes out an open legacy
 * (TASK_209) inspection with a reason — the only transition the canonical
 * workflow offers a legacy row.
 */
export async function cancelAnomalyInspection(id, expectedVersion, reason, signal) {
  const { data } = await client.post(`anomaly-inspections/${id}/cancel`, {
    expected_version: expectedVersion, reason,
  }, { signal, ...noRetry });
  return data;
}

export async function uploadInspectionPhoto(id, expectedVersion, file, capturedAt, signal) {
  const body = new FormData();
  body.set('expected_version', String(expectedVersion));
  if (capturedAt) body.set('captured_at', capturedAt);
  body.set('photo', file, file.name);
  const { data } = await client.post(`anomaly-inspections/${id}/photos`, body, {
    headers: { 'Content-Type': undefined }, signal, ...noRetry,
  });
  return data;
}

export async function downloadInspectionPhoto(id, photoId, signal) {
  const { data } = await client.get(`anomaly-inspections/${id}/photos/${photoId}`, {
    responseType: 'blob', signal, ...noRetry,
  });
  return data;
}

export async function deleteInspectionPhoto(id, photoId, expectedVersion, signal) {
  const { data } = await client.delete(`anomaly-inspections/${id}/photos/${photoId}`, {
    data: { expected_version: expectedVersion }, signal, ...noRetry,
  });
  return data;
}

export async function getFieldInspectionTimeline(fieldId, params = {}, signal) {
  const { data } = await client.get(`anomaly-inspections/fields/${fieldId}/timeline`, { params, signal });
  return data;
}
