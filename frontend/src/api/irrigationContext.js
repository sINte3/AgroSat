import client from './client.js';


const deliberateWriteConfig = (idempotencyKey, signal) => ({
  headers: { 'Idempotency-Key': idempotencyKey },
  signal,
  __retryCount: 2,
});


export async function getFieldIrrigationContext(fieldId, signal) {
  return (await client.get(`irrigation-context/fields/${fieldId}`, {
    params: { limit: 20 },
    signal,
  })).data;
}


export async function createIrrigationEvent(fieldId, payload, idempotencyKey, signal) {
  return (await client.post(
    `irrigation-context/fields/${fieldId}/events`,
    payload,
    deliberateWriteConfig(idempotencyKey, signal),
  )).data;
}
