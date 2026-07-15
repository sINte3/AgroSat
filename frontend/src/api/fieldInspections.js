import client from './client';

const NO_AUTOMATIC_RETRY_COUNT = 2;
const clean = (values) => Object.fromEntries(
  Object.entries(values).filter(([, value]) => value !== null && value !== undefined && value !== ''),
);
const deliberateWriteConfig = (signal, extra = {}) => ({
  signal,
  __retryCount: NO_AUTOMATIC_RETRY_COUNT,
  ...extra,
});

export async function listFieldInspections(filters = {}, signal) {
  const params = clean({
    enterprise_id: filters.enterpriseId,
    field_id: filters.fieldId,
    assigned_to_id: filters.assignedToId,
    status: filters.status,
    overdue_only: filters.overdueOnly,
    due_before: filters.dueBefore,
    created_after: filters.createdAfter,
    limit: filters.limit,
    offset: filters.offset,
  });
  return (await client.get('field-inspections', { params, signal })).data;
}
export async function getFieldInspection(id, signal) {
  return (await client.get(`field-inspections/${id}`, { signal })).data;
}
export async function createFieldInspection(payload, idempotencyKey, signal) {
  const config = deliberateWriteConfig(signal, { headers: { 'Idempotency-Key': idempotencyKey } });
  return (await client.post('field-inspections', payload, config)).data;
}
export async function updateFieldInspection(id, payload, signal) {
  return (await client.patch(`field-inspections/${id}`, payload, deliberateWriteConfig(signal))).data;
}
export async function startFieldInspection(id, expectedVersion, signal) {
  const payload = { expected_version: expectedVersion };
  return (await client.post(`field-inspections/${id}/start`, payload, deliberateWriteConfig(signal))).data;
}
export async function completeFieldInspection(id, expectedVersion, completionSummary, signal) {
  const payload = { expected_version: expectedVersion, completion_summary: completionSummary };
  return (await client.post(`field-inspections/${id}/complete`, payload, deliberateWriteConfig(signal))).data;
}
export async function cancelFieldInspection(id, expectedVersion, cancellationReason, signal) {
  const payload = { expected_version: expectedVersion, cancellation_reason: cancellationReason };
  return (await client.post(`field-inspections/${id}/cancel`, payload, deliberateWriteConfig(signal))).data;
}
export async function listInspectionFields(signal) {
  return (await client.get('fields/', { signal })).data;
}
