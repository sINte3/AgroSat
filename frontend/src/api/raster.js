import client from './client';

export async function getRasterMetadata(
  fieldId,
  { indexCode = 'ndvi', dateTo, signal } = {},
) {
  const { data } = await client.get(`raster/fields/${fieldId}/metadata`, {
    params: { index_code: indexCode, date_to: dateTo },
    signal,
  });
  return data;
}

export async function getRasterImage(
  fieldId,
  { indexCode = 'ndvi', observationDate, size, signal } = {},
) {
  const response = await client.get(`raster/fields/${fieldId}/image`, {
    params: {
      index_code: indexCode,
      observation_date: observationDate,
      size,
    },
    responseType: 'blob',
    signal,
  });
  return {
    blob: response.data,
    cacheState: response.headers?.['x-agrosat-raster-cache'] || null,
    provider: response.headers?.['x-agrosat-raster-provider'] || null,
  };
}
