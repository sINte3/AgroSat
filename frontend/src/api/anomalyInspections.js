import client from './client';

const noRetry = { __retryCount: 0 };

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

export async function createInspectionAction(id, payload, signal) {
  const { data } = await client.post(`anomaly-inspections/${id}/actions`, payload, { signal, ...noRetry });
  return data;
}

export async function transitionInspectionAction(actionId, payload, signal) {
  const { data } = await client.post(`anomaly-inspections/actions/${actionId}/transition`, payload, { signal, ...noRetry });
  return data;
}

export async function verifyInspectionAction(actionId, payload, signal) {
  const { data } = await client.post(`anomaly-inspections/actions/${actionId}/verify`, payload, { signal, ...noRetry });
  return data;
}

export async function getFieldInspectionTimeline(fieldId, params = {}, signal) {
  const { data } = await client.get(`anomaly-inspections/fields/${fieldId}/timeline`, { params, signal });
  return data;
}
