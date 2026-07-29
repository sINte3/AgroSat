import client from './client';

export async function getCommercialBoundary(enterpriseId, signal) {
  const { data } = await client.get(`commercial/tenants/${enterpriseId}`, { signal });
  return data;
}

export async function getCommercialMemberships(enterpriseId, signal) {
  const { data } = await client.get(
    `commercial/tenants/${enterpriseId}/memberships`,
    { params: { limit: 50, offset: 0 }, signal },
  );
  return data;
}

export async function getTenantLifecycleRequests(enterpriseId, signal) {
  const { data } = await client.get(
    `commercial/tenants/${enterpriseId}/lifecycle-requests`,
    { params: { limit: 50, offset: 0 }, signal },
  );
  return data;
}

export async function createTenantLifecycleRequest(enterpriseId, payload, idempotencyKey) {
  const { data } = await client.post(
    `commercial/tenants/${enterpriseId}/lifecycle-requests`,
    payload,
    { headers: { 'Idempotency-Key': idempotencyKey } },
  );
  return data;
}

export async function decideTenantLifecycleRequest(enterpriseId, requestId, decision, note) {
  const { data } = await client.post(
    `commercial/tenants/${enterpriseId}/lifecycle-requests/${requestId}/decision`,
    { decision, confirm: true, note },
  );
  return data;
}
