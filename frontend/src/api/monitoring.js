import client from './client';

export async function getMonitoringStatus(signal) {
  return (await client.get('monitoring/status', { signal })).data;
}

export async function getMonitoringFreshness(params = {}, signal) {
  return (await client.get('monitoring/freshness', { params, signal })).data;
}

export async function getMonitoringCandidates(params = {}, signal) {
  return (await client.get('monitoring/candidates', { params, signal })).data;
}

export async function transitionMonitoringCandidate(id, payload, signal) {
  return (await client.post(`monitoring/candidates/${id}/transition`, payload, { signal, __noRetry: true })).data;
}

export async function createMonitoringInspection(id, payload, signal) {
  return (await client.post(`monitoring/candidates/${id}/inspection`, payload, { signal, __noRetry: true })).data;
}
