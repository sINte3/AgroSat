import axios, { CanceledError } from 'axios';
import {
  invalidateSession,
  readSessionToken,
  registerUserScopedCacheClearer,
} from '../auth/session.js';

const client = axios.create({
  baseURL: '/api/',
  timeout: 60000,  // 60 seconds
  headers: { 'Content-Type': 'application/json' },
});

export function getSameOriginApiAuthorizationHeaders(url) {
  if (typeof window === 'undefined' || !url) return {};
  try {
    const target = new URL(url, window.location.origin);
    if (
      target.origin !== window.location.origin
      || !target.pathname.startsWith('/api/')
    ) return {};
  } catch (_) {
    return {};
  }
  const token = readSessionToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ─── Fix double /api/ prefix ─────────────────────────────────────────────────
// Некоторые компоненты вызывают client.get('/api/...') — при baseURL='/api/'
// это даёт /api/api/... → 404. Этот interceptor убирает лишний префикс.

// TODO: For production, migrate authentication to HttpOnly SameSite=Strict cookies
// with short token lifetimes. localStorage is acceptable only for local development.
client.interceptors.request.use(config => {
  if (config.url && config.url.startsWith('/api/')) {
    config.url = config.url.replace(/^\/api\//, '');
  }

  const token = readSessionToken();
  // Remember which credential this request carried: a 401 for an older
  // credential must not end a session established after the request left.
  config.__sessionToken = token;
  if (token) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }

  return config;
});

// ─── Simple in-memory cache ──────────────────────────────────────────────────

const cache = new Map();
const TTL = 3 * 60 * 1000;

async function cachedGet(url, params = {}, ttl = TTL) {
  const key = url + JSON.stringify(params);
  const hit = cache.get(key);
  if (hit && Date.now() - hit.ts < ttl) return hit.data;
  const { data } = await client.get(url, { params });
  cache.set(key, { data, ts: Date.now() });
  return data;
}

// Cached responses belong to one authenticated user; a session end or a
// different user drops them. Offline scouting data is not a cache and is kept.
function clearApiResponseCaches() {
  cache.clear();
  try {
    sessionStorage.removeItem(GEO_CACHE_KEY);
  } catch {
    // sessionStorage may be unavailable.
  }
}
registerUserScopedCacheClearer(clearApiResponseCaches);

// ─── Automatic retry policy ──────────────────────────────────────────────────
// Only safe read methods are retried, and only after a timeout or a 5xx. A
// mutation is never re-sent automatically, even after a timeout: its outcome is
// unknown and an Idempotency-Key does not make a hidden duplicate acceptable.

export const SAFE_RETRY_METHODS = Object.freeze(['get', 'head']);
export const MAX_AUTOMATIC_RETRIES = 2;
const RETRY_BASE_DELAY_MS = 1000;
const TIMEOUT_CODES = new Set(['ECONNABORTED', 'ETIMEDOUT']);

function isCancelledRequest(error, config) {
  return axios.isCancel(error)
    || error?.code === 'ERR_CANCELED'
    || error?.name === 'CanceledError'
    || error?.name === 'AbortError'
    || Boolean(config?.signal?.aborted);
}

export function isAutomaticRetryAllowed(error) {
  const config = error?.config;
  if (!config || config.__noRetry) return false;
  if (isCancelledRequest(error, config)) return false;
  if (!SAFE_RETRY_METHODS.includes(String(config.method || 'get').toLowerCase())) return false;
  if ((Number(config.__retryCount) || 0) >= MAX_AUTOMATIC_RETRIES) return false;
  const status = Number(error?.response?.status || 0);
  if (status) return status >= 500;
  return TIMEOUT_CODES.has(error?.code);
}

function waitBeforeRetry(attempt, config) {
  const signal = config.signal;
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      reject(new CanceledError('Request aborted before retry', config));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener?.('abort', onAbort);
      resolve();
    }, RETRY_BASE_DELAY_MS * attempt);
    signal?.addEventListener?.('abort', onAbort, { once: true });
  });
}

client.interceptors.response.use(
  response => response,
  async error => {
    const config = error?.config;
    if (error?.response?.status === 401 && !config?.__skipSessionInvalidation) {
      // Involuntary authentication loss: end the web session only. Offline
      // scouting drafts stay on the device for the same user to recover.
      invalidateSession({ reason: 'unauthorized', failedToken: config?.__sessionToken ?? null });
      return Promise.reject(error);
    }

    if (!isAutomaticRetryAllowed(error)) return Promise.reject(error);
    config.__retryCount = (Number(config.__retryCount) || 0) + 1;
    await waitBeforeRetry(config.__retryCount, config);
    return client(config);
  }
);

// ─── Enterprises ────────────────────────────────────────────────────────────

export async function getEnterprises() {
  return cachedGet('enterprises/', {}, 10 * 60 * 1000);
}
export const getCachedEnterprises = getEnterprises;

export async function getEnterprise(id) {
  const { data } = await client.get(`enterprises/${id}`);
  return data;
}

// ─── Fields ─────────────────────────────────────────────────────────────────

export async function getFields(params = {}, signal) {
  const { data } = await client.get('fields/', { params, signal });
  return data;
}

export async function getField(id) {
  const { data } = await client.get(`fields/${id}`);
  return data;
}

export async function getFieldsGeoJSON(params = {}) {
  return cachedGet('fields/geojson/all', params, 5 * 60 * 1000);
}
// Keep the old export name for backward compatibility
export const getCachedFieldsGeoJSON = getFieldsGeoJSON;

export async function createField(payload) {
  const { data } = await client.post('fields/', payload);
  return data;
}

export async function addFieldSeason(fieldId, payload) {
  const { data } = await client.post(`fields/${fieldId}/season`, payload);
  return data;
}

// ─── GeoJSON sessionStorage cache ──────────────────────────────────────────

const GEO_CACHE_KEY = 'agrosat_geo_v1';
const GEO_CACHE_TTL = 5 * 60 * 1000; // 5 минут в миллисекундах

export async function fetchFieldsGeoJson(params = {}) {
  // sessionStorage — только если нет параметров (все поля)
  if (Object.keys(params).length === 0) {
    try {
      const raw = sessionStorage.getItem(GEO_CACHE_KEY);
      if (raw) {
        const { data, ts } = JSON.parse(raw);
        if (Date.now() - ts < GEO_CACHE_TTL) {
          console.log('[GeoCache] Из кеша');
          return data;
        }
      }
    } catch (e) { /* игнорировать ошибки sessionStorage */ }
  }

  console.log('[GeoCache] Загружаем с сервера...');
  const { data } = await client.get('fields/geojson/all', { params });
  if (Object.keys(params).length === 0) {
    try {
      sessionStorage.setItem(GEO_CACHE_KEY, JSON.stringify({ data, ts: Date.now() }));
    } catch (e) { /* sessionStorage переполнен — работаем без кеша */ }
  }
  return data;
}

// Сбросить кеш (вызывать после ручного обновления полей)
export function clearGeoCache() {
  sessionStorage.removeItem(GEO_CACHE_KEY);
}

// ─── NDVI ───────────────────────────────────────────────────────────────────

export async function getNDVIHistory(fieldId, days = 90, options = {}) {
  const { data } = await client.get(`ndvi/${fieldId}/history`, {
    params: { days },
    signal: options.signal,
  });
  return data;
}

export async function getLatestNDVI(fieldId) {
  const { data } = await client.get(`ndvi/${fieldId}/latest`);
  return data;
}

// Satellite collection is owned by the standalone collector: the web client
// only reads persisted observations and never triggers a provider request.

// ─── Alerts ─────────────────────────────────────────────────────────────────
// NOT cached — alerts must always be fresh

export async function getAlerts(params = {}) {
  const { data } = await client.get('alerts/', { params });
  return data;
}

export async function getFieldAlerts(fieldId, params = {}) {
  const { data } = await client.get(`alerts/${fieldId}`, { params });
  return data;
}

export async function acknowledgeAlert(alertId) {
  const { data } = await client.put(`alerts/${alertId}/acknowledge`);
  return data;
}

// ─── Dashboard ──────────────────────────────────────────────────────────────

export async function getDashboardSummary() {
  return cachedGet('dashboard/summary', {}, 2 * 60 * 1000);
}
export const getCachedDashboardSummary = getDashboardSummary;

export async function getEnterpriseDashboard(enterpriseId) {
  const { data } = await client.get(`dashboard/enterprises/${enterpriseId}`);
  return data;
}

// ─── Reports ────────────────────────────────────────────────────────────────

export async function getManagementReportSummary(signal) {
  const { data } = await client.get('reports/management/summary', { signal });
  return data;
}

export async function getManagementSatelliteIndicesSummary(params = {}, signal) {
  const { data } = await client.get('reports/management/satellite-indices/summary', { params, signal });
  return data;
}

export async function downloadManagementReportPdf(signal) {
  const response = await client.get('reports/management/pdf', {
    responseType: 'blob',
    signal,
  });
  return response;
}

// ─── Weather ────────────────────────────────────────────────────────────────

export async function getFieldWeather(fieldId) {
  const { data } = await client.get(`weather/field/${fieldId}`);
  return data;
}

export async function getLocationWeather(lat, lon) {
  const { data } = await client.get('weather/location', { params: { lat, lon } });
  return data;
}

// Satellite Indices (SAVI, EVI, NDMI, NDRE)
// Read-only. Uses /api/satellite-indices/* (not /api/ndvi/*).

export async function getSatelliteIndexLatest(fieldId, indexCode, options = {}) {
  const { data } = await client.get(`satellite-indices/${fieldId}/latest`, {
    params: { index_code: indexCode, include_cloudy: options.includeCloudy ?? false },
    signal: options.signal,
  });
  return data;
}

export async function getSatelliteIndexHistory(fieldId, indexCode, options = {}) {
  const { data } = await client.get(`satellite-indices/${fieldId}/history`, {
    params: { index_code: indexCode, days: options.days ?? 30, include_cloudy: options.includeCloudy ?? false },
    signal: options.signal,
  });
  return data;
}

/**
 * Fetch latest values for all supported satellite indices for a field.
 *
 * Only fetches SAVI, EVI, NDMI, NDRE — never NDVI.
 * Returns object keyed by index code, each value being { record, error }.
 * A missing or errored index produces { record: null, error: true }.
 */
export async function getAllSupportedSatelliteIndicesForField(fieldId) {
  const codes = ['savi', 'evi', 'ndmi', 'ndre'];
  const results = {};

  const responses = await Promise.allSettled(
    codes.map((code) =>
      getSatelliteIndexLatest(fieldId, code, { includeCloudy: false })
    )
  );

  codes.forEach((code, i) => {
    const res = responses[i];
    if (res.status === 'fulfilled' && res.value?.record) {
      results[code] = { record: res.value.record, error: null };
    } else {
      results[code] = { record: null, error: true };
    }
  });

  return results;
}

// ─── Auth ────────────────────────────────────────────────────────────────────

export async function loginWithPassword(email, password) {
  const params = new URLSearchParams();
  params.append('username', String(email || '').trim().toLowerCase());
  params.append('password', password);

  const { data } = await client.post('auth/login', params, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    // A rejected credential is a login failure, not the loss of the current
    // session; the caller reports it.
    __skipSessionInvalidation: true,
  });

  return data;
}

// ─── Satellite Coverage ──────────────────────────────────────────────

const VALID_INDEX_CODES = ['savi', 'evi', 'ndmi', 'ndre'];

function listValues(value) {
  if (value === null || value === undefined || value === '') return [];
  return (Array.isArray(value) ? value : String(value).split(','))
    .map(item => String(item).trim())
    .filter(Boolean);
}

/**
 * Query parameters for GET /api/satellite-indices/coverage.
 *
 * The backend reads `field_ids` and `index_codes` as comma-separated strings;
 * axios would send an array as `field_ids[]=…`, which the backend ignores and
 * which silently widens the query to every field in scope. Lists are therefore
 * serialized here, once, for every caller.
 */
export function serializeCoverageParams(params = {}) {
  const cleaned = { ...params };

  if ('field_ids' in cleaned) {
    const ids = listValues(cleaned.field_ids).map(Number);
    if (ids.some(id => !Number.isSafeInteger(id) || id <= 0)) {
      throw new Error('field_ids must contain positive integer field identifiers');
    }
    if (ids.length) cleaned.field_ids = Array.from(new Set(ids)).join(',');
    else delete cleaned.field_ids;
  }

  // NDVI is never requested from the satellite-indices coverage endpoint.
  if (cleaned.index_codes === undefined || cleaned.index_codes === null || cleaned.index_codes === '') {
    delete cleaned.index_codes;
  } else {
    const codes = listValues(cleaned.index_codes)
      .map(code => code.toLowerCase())
      .filter(code => code !== 'ndvi');
    cleaned.index_codes = (codes.length ? Array.from(new Set(codes)) : VALID_INDEX_CODES).join(',');
  }

  return cleaned;
}

/**
 * Fetch satellite-index coverage summary.
 *
 * GET /api/satellite-indices/coverage
 *
 * Accepted params: enterprise_id, field_ids, index_codes, date_from,
 * date_to, active_only, include_empty, stale_after_days, as_of.
 *
 * Rules:
 * - field_ids / index_codes are sent as comma-separated strings.
 * - NDVI is never included in index_codes — stripped before request.
 * - Response is normalized defensively: missing summary/fields are safe.
 * - 401 ends the web session through the response interceptor.
 * - 422 surfaces a configuration error string.
 */
export async function getSatelliteCoverage(params = {}, signal) {
  const cleaned = serializeCoverageParams(params);

  try {
    const { data } = await client.get('satellite-indices/coverage', {
      params: cleaned,
      signal,
      // Do not retry 422 — it's a configuration error, not transient
      __noRetry: true,
    });

    // Defensive normalization
    return {
      filters: data?.filters ?? null,
      summary: {
        fields_total: data?.summary?.fields_total ?? 0,
        fields_with_any_data: data?.summary?.fields_with_any_data ?? 0,
        fields_without_data: data?.summary?.fields_without_data ?? 0,
        fields_with_all_requested_indices: data?.summary?.fields_with_all_requested_indices ?? 0,
        fields_with_partial_indices: data?.summary?.fields_with_partial_indices ?? 0,
        fields_stale: data?.summary?.fields_stale ?? 0,
        index_summary: data?.summary?.index_summary ?? {},
        latest_captured_date: data?.summary?.latest_captured_date ?? null,
        record_count_total: data?.summary?.record_count_total ?? 0,
      },
      fields: Array.isArray(data?.fields) ? data.fields : [],
    };
  } catch (err) {
    if (err?.response?.status === 422) {
      throw new Error(
        'Ошибка конфигурации спутниковых данных. Обратитесь к администратору.'
      );
    }
    throw err;
  }
}

export default client;
