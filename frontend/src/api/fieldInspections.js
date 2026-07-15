import client from './client';

const clean = (values) => Object.fromEntries(Object.entries(values).filter(([, value]) => value !== null && value !== undefined && value !== ''));
const once = (signal, extra = {}) => ({ signal, __retryCount: 2, ...extra });

export async function listFieldInspections(filters = {}, signal) {
  const params = clean({ enterprise_id: filters.enterpriseId, field_id: filters.fieldId, assigned_to_id: filters.assignedToId, status: filters.status, overdue_only: filters.overdueOnly, due_before: filters.dueBefore, created_after: filters.createdAfter, limit: filters.limit, offset: filters.offset });
  return (await client.get('field-inspections', { params, signal })).data;
}
export async function getFieldInspection(id, signal) { return (await client.get(`field-inspections/${id}`, { signal })).data; }
export async function createFieldInspection(payload, idempotencyKey, signal) { return (await client.post('field-inspections', payload, once(signal, { headers: { 'Idempotency-Key': idempotencyKey } }))).data; }
export async function updateFieldInspection(id, payload, signal) { return (await client.patch(`field-inspections/${id}`, payload, once(signal))).data; }
export async function startFieldInspection(id, expectedVersion, signal) { return (await client.post(`field-inspections/${id}/start`, { expected_version: expectedVersion }, once(signal))).data; }
export async function completeFieldInspection(id, expectedVersion, completionSummary, signal) { return (await client.post(`field-inspections/${id}/complete`, { expected_version: expectedVersion, completion_summary: completionSummary }, once(signal))).data; }
export async function cancelFieldInspection(id, expectedVersion, cancellationReason, signal) { return (await client.post(`field-inspections/${id}/cancel`, { expected_version: expectedVersion, cancellation_reason: cancellationReason }, once(signal))).data; }
export async function listInspectionFields(signal) { return (await client.get('fields/', { signal, params: { limit: 200 } })).data; }
