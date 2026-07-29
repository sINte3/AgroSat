import client from './client.js';


export async function getLatestProductivityZones(fieldId, signal) {
  return (await client.get(`productivity-zones/fields/${fieldId}`, {
    signal,
  })).data;
}


export async function listProductivityZones(runId, signal) {
  return (await client.get(`productivity-zones/runs/${runId}/zones`, {
    params: { limit: 10, offset: 0 },
    signal,
  })).data;
}
