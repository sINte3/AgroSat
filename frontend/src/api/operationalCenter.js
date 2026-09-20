import client from './client';

const requestKey = () => `operational-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`}`
  .replace(/[^A-Za-z0-9._:-]/g, '-')
  .slice(0, 64);

export const listOperationalQueue = async (params = {}, signal) => (
  await client.get('operational-center/queue', { params, signal })
).data;

export const getOperationalSummary = async (params = {}, signal) => (
  await client.get('operational-center/summary', { params, signal })
).data;

export const getOperationalFilterOptions = async (enterpriseId, signal) => (
  await client.get('operational-center/filter-options', {
    params: enterpriseId ? { enterprise_id: enterpriseId } : {},
    signal,
  })
).data;

export const getOperationalCase = async (caseKey, signal) => (
  await client.get(`operational-center/cases/${encodeURIComponent(caseKey)}`, { signal })
).data;

export const getOperationalFieldTimeline = async (fieldId, params = {}, signal) => (
  await client.get(`operational-center/fields/${fieldId}/timeline`, { params, signal })
).data;

export const listOperationalNotifications = async (params = {}, signal) => (
  await client.get('operational-center/notifications', { params, signal })
).data;

export const transitionOperationalNotification = async (
  notificationId,
  payload,
  idempotencyKey = requestKey(),
) => (
  await client.post(
    `operational-center/notifications/${notificationId}/transition`,
    payload,
    { headers: { 'Idempotency-Key': idempotencyKey } },
  )
).data;

export { requestKey as createOperationalRequestKey };
