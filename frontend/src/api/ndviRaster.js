import client from './client';

export async function getNDVIRasterMetadata(fieldId, { dateTo, signal } = {}) {
  const { data } = await client.get(`ndvi-raster/fields/${fieldId}/metadata`, {
    params: { date_to: dateTo },
    signal,
  });
  return data;
}

export async function getNDVIRasterImage(fieldId, { observationDate, size, signal } = {}) {
  const response = await client.get(`ndvi-raster/fields/${fieldId}/image`, {
    params: { observation_date: observationDate, size },
    responseType: 'blob',
    signal,
  });
  return {
    blob: response.data,
    cacheState: response.headers?.['x-agrosat-raster-cache'] || null,
  };
}
