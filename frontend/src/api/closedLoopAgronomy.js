import client from './client';

const key = () => `agronomy-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`}`.replace(/[^A-Za-z0-9._:-]/g, '-').slice(0, 64);
export const listAgronomyPlans = async (params = {}, signal) => (await client.get('agronomy-plans/queue', { params, signal })).data;
export const getAgronomySummary = async (params = {}, signal) => (await client.get('agronomy-plans/summary', { params, signal })).data;
export const getAgronomyPlan = async (id, signal) => (await client.get(`agronomy-plans/${id}`, { signal })).data;
export const createAgronomyDraft = async (inspectionId, reason) => (await client.post('agronomy-plans', { inspection_id: inspectionId, reason }, { headers: { 'Idempotency-Key': key() } })).data;
export const editAgronomyPlan = async (id, payload, requestKey = key()) => (await client.put(`agronomy-plans/${id}`, payload, { headers: { 'Idempotency-Key': requestKey } })).data;
export const transitionAgronomyPlan = async (id, operation, expectedVersion, reason, requestKey = key()) => (await client.post(`agronomy-plans/${id}/transition`, { operation, expected_version: expectedVersion, reason }, { headers: { 'Idempotency-Key': requestKey } })).data;
export const addAgronomyWork = async (id, payload, requestKey = key()) => (await client.post(`agronomy-plans/${id}/work`, payload, { headers: { 'Idempotency-Key': requestKey } })).data;
export const transitionAgronomyWork = async (planId, itemId, payload, requestKey = key()) => (await client.post(`agronomy-plans/${planId}/work/${itemId}/transition`, payload, { headers: { 'Idempotency-Key': requestKey } })).data;
export const reevaluateAgronomyPlan = async (id, expectedVersion, reason, requestKey = key()) => (await client.post(`agronomy-plans/${id}/reevaluate`, { expected_version: expectedVersion, reason, observation_id: null }, { headers: { 'Idempotency-Key': requestKey } })).data;
export const agronomyExportUrl = (params = {}) => `/api/agronomy-plans/export.csv?${new URLSearchParams(Object.entries(params).filter(([, value]) => value != null && value !== '')).toString()}`;
export { key as createAgronomyKey };
