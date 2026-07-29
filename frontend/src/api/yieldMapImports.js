import client from './client.js';


const writeConfig = (signal, idempotencyKey) => ({
  signal,
  __retryCount: 2,
  ...(idempotencyKey ? { headers: { 'Idempotency-Key': idempotencyKey } } : {}),
});


export async function listYieldMapImports(fieldId, signal) {
  return (await client.get('yield-map-imports', {
    params: { field_id: fieldId, limit: 50, offset: 0 },
    signal,
  })).data;
}


export async function previewYieldMapImport(payload, signal) {
  return (await client.post(
    'yield-map-imports/preview',
    payload,
    writeConfig(signal),
  )).data;
}


export async function acceptYieldMapImport(payload, idempotencyKey, signal) {
  return (await client.post(
    'yield-map-imports',
    payload,
    writeConfig(signal, idempotencyKey),
  )).data;
}
