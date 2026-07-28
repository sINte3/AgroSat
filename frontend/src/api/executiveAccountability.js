import client from './client';

const clean = (values) => Object.fromEntries(
  Object.entries(values).filter(([, value]) => value !== null && value !== undefined && value !== ''),
);

function params(filters = {}) {
  return clean({
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
    enterprise_id: filters.enterpriseId,
    attention_lookback_days: filters.attentionLookbackDays,
  });
}

export async function getExecutiveOverview(filters = {}, signal) {
  return (await client.get('executive/overview', {
    params: params(filters),
    signal,
  })).data;
}

export async function getExecutiveAccountability(filters = {}, signal) {
  return (await client.get('executive/accountability', {
    params: {
      ...params(filters),
      kind: filters.kind,
      owner_id: filters.ownerId || undefined,
      limit: filters.limit,
      offset: filters.offset,
    },
    signal,
  })).data;
}

export async function downloadExecutiveWorkbook(filters = {}, signal) {
  return client.get('executive/export.xlsx', {
    params: params(filters),
    responseType: 'blob',
    signal,
  });
}
