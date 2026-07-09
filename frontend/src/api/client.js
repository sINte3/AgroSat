import axios from 'axios';

const client = axios.create({
  baseURL: '/api/',
  timeout: 60000,  // 60 seconds
  headers: { 'Content-Type': 'application/json' },
});

// ─── Fix double /api/ prefix ─────────────────────────────────────────────────
// Некоторые компоненты вызывают client.get('/api/...') — при baseURL='/api/'
// это даёт /api/api/... → 404. Этот interceptor убирает лишний префикс.

// TODO: For production, migrate authentication to HttpOnly SameSite=Strict cookies
// with short token lifetimes. localStorage is acceptable only for local development.
client.interceptors.request.use(config => {
  if (config.url && config.url.startsWith('/api/')) {
    config.url = config.url.replace(/^\/api\//, '');
  }

  const token = localStorage.getItem('agrosat_token');
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

// ─── Retry interceptor (timeout + 5xx, up to 2 retries) ─────────────────────

client.interceptors.response.use(
  response => response,
  async error => {
    if (error?.response?.status === 401) {
      localStorage.removeItem('agrosat_token');
      localStorage.removeItem('agrosat_user');
      window.dispatchEvent(new Event('agrosat:logout'));

      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }

      return Promise.reject(error);
    }

    const config = error.config;
    if (!config || config.__retryCount >= 2) return Promise.reject(error);
    if (error.code === 'ECONNABORTED' || error.response?.status >= 500) {
      config.__retryCount = (config.__retryCount || 0) + 1;
      await new Promise(r => setTimeout(r, 1000 * config.__retryCount));
      return client(config);
    }
    return Promise.reject(error);
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

export async function getFields(params = {}) {
  const { data } = await client.get('fields/', { params });
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

export async function getNDVIHistory(fieldId, days = 90) {
  const { data } = await client.get(`ndvi/${fieldId}/history`, { params: { days } });
  return data;
}

export async function getLatestNDVI(fieldId) {
  const { data } = await client.get(`ndvi/${fieldId}/latest`);
  return data;
}

export async function refreshNDVI(fieldId) {
  const { data } = await client.post(`ndvi/${fieldId}/refresh`);
  return data;
}

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

export async function getManagementReportSummary() {
  const { data } = await client.get('reports/management/summary');
  return data;
}

export async function getManagementSatelliteIndicesSummary(params = {}) {
  const { data } = await client.get('reports/management/satellite-indices/summary', { params });
  return data;
}

export async function downloadManagementReportPdf() {
  const response = await client.get('reports/management/pdf', {
    responseType: 'blob',
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
  });
  return data;
}

export async function getSatelliteIndexHistory(fieldId, indexCode, options = {}) {
  const { data } = await client.get(`satellite-indices/${fieldId}/history`, {
    params: { index_code: indexCode, days: options.days ?? 30, include_cloudy: options.includeCloudy ?? false },
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
  });

  return data;
}

// ─── Satellite Coverage ──────────────────────────────────────────────

const VALID_INDEX_CODES = ['savi', 'evi', 'ndmi', 'ndre'];

/**
 * Fetch satellite-index coverage summary.
 *
 * GET /api/satellite-indices/coverage
 *
 * Accepted params: enterprise_id, field_ids, index_codes, date_from,
 * date_to, active_only, include_empty, stale_after_days, as_of.
 *
 * Rules:
 * - NDVI is never included in index_codes — stripped before request.
 * - Response is normalized defensively: missing summary/fields are safe.
 * - 401/403 are surfaced via the retry interceptor's 401 handler.
 * - 422 surfaces a configuration error string.
 */
export async function getSatelliteCoverage(params = {}) {
  const cleaned = { ...params };

  // Strip NDVI from index_codes if present
  if (cleaned.index_codes) {
    const codes = Array.isArray(cleaned.index_codes)
      ? cleaned.index_codes
      : String(cleaned.index_codes).split(',').map(s => s.trim().toLowerCase());
    cleaned.index_codes = codes.filter(c => c !== 'ndvi');
    if (cleaned.index_codes.length === 0) {
      cleaned.index_codes = VALID_INDEX_CODES;
    }
  }

  try {
    const { data } = await client.get('satellite-indices/coverage', {
      params: cleaned,
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
