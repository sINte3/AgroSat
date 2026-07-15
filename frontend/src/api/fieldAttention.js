import client from './client';

export async function getFieldAttentionQueue({
  enterpriseId,
  cropTypeId,
  dateTo,
  lookbackDays,
  minPriority,
  limit,
  signal,
} = {}) {
  const candidates = {
    enterprise_id: enterpriseId,
    crop_type_id: cropTypeId,
    date_to: dateTo,
    lookback_days: lookbackDays,
    min_priority: minPriority,
    limit,
  };
  const params = Object.fromEntries(
    Object.entries(candidates).filter(([, value]) => value !== null && value !== undefined && value !== '')
  );
  const response = await client.get('field-attention/queue', { params, signal });
  return response.data;
}
