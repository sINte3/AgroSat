import client from './client.js';


const writeConfig = (signal, idempotencyKey) => ({
  signal,
  __retryCount: 2,
  ...(idempotencyKey ? { headers: { 'Idempotency-Key': idempotencyKey } } : {}),
});


export async function listVariableRateRecommendations(fieldId, signal) {
  return (await client.get('variable-rate-recommendations', {
    params: { field_id: fieldId, limit: 50, offset: 0 },
    signal,
  })).data;
}


export async function createVariableRateRecommendation(payload, key, signal) {
  return (await client.post(
    'variable-rate-recommendations',
    payload,
    writeConfig(signal, key),
  )).data;
}


export async function decideVariableRateRecommendation(id, decision, payload, signal) {
  return (await client.post(
    `variable-rate-recommendations/${id}/${decision}`,
    payload,
    writeConfig(signal),
  )).data;
}


export async function exportVariableRateRecommendation(id, signal) {
  return (await client.get(
    `variable-rate-recommendations/${id}/export.geojson`,
    { signal },
  )).data;
}
