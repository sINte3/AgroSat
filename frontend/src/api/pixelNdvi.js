import client from './client';

export async function getPixelNDVIScenes(fieldId, { dateFrom, dateTo, limit = 20, signal } = {}) {
  const { data } = await client.get(`raster/fields/${fieldId}/scenes`, {
    params: { date_from: dateFrom, date_to: dateTo, limit },
    signal,
  });
  return data;
}

export async function getPixelNDVIWorkspace(fieldId, sceneId, signal) {
  const { data } = await client.get(`raster/fields/${fieldId}/workspace`, {
    params: { scene_id: sceneId },
    signal,
  });
  return data;
}

export async function getPixelNDVIImage(fieldId, sceneId, signal) {
  const response = await client.get(`raster/fields/${fieldId}/pixel-image`, {
    params: { scene_id: sceneId },
    responseType: 'blob',
    signal,
  });
  return {
    blob: response.data,
    cacheState: response.headers?.['x-agrosat-raster-cache'] || null,
    etag: response.headers?.etag || null,
  };
}

export async function samplePixelNDVI(fieldId, sceneId, longitude, latitude, signal) {
  const { data } = await client.get(`raster/fields/${fieldId}/sample`, {
    params: { scene_id: sceneId, longitude, latitude },
    signal,
  });
  return data;
}
