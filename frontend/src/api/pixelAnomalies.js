import client from './client';


const NO_AUTOMATIC_RETRY_COUNT = 2;
const clean = (values) => Object.fromEntries(
  Object.entries(values).filter(([, value]) => value !== null && value !== undefined && value !== ''),
);

export async function getPixelAnomalySummary(fieldId, signal) {
  return (await client.get(`pixel-anomalies/fields/${fieldId}/summary`, { signal })).data;
}

export async function listPixelAnomalies(filters, signal) {
  const params = clean({
    field_id: filters.fieldId,
    index_code: filters.indexCode,
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
    limit: filters.limit ?? 50,
    offset: filters.offset ?? 0,
  });
  return (await client.get('pixel-anomalies', { params, signal })).data;
}

export async function getPixelAnomaly(id, signal) {
  return (await client.get(`pixel-anomalies/${id}`, { signal })).data;
}

export async function getPixelAnomalyGeometry(id, signal) {
  return (await client.get(`pixel-anomalies/${id}/geometry`, { signal })).data;
}

export async function createInspectionFromPixelAnomaly(
  id,
  payload,
  idempotencyKey,
  signal,
) {
  return (
    await client.post(
      `pixel-anomalies/${id}/inspection`,
      payload,
      {
        signal,
        __retryCount: NO_AUTOMATIC_RETRY_COUNT,
        headers: { 'Idempotency-Key': idempotencyKey },
      },
    )
  ).data;
}
