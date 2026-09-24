// Shared presentation helpers. Status, source and priority vocabulary for the
// canonical lifecycles lives in ../../config/canonicalLifecycle.js.

export function positiveId(value) {
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}
export function normalizeRole(role) {
  return typeof role === 'string' ? role.trim().toLowerCase() : '';
}
/** POST /api/anomaly-inspections accepts admin and manager only. */
export function canCreateInspections(role) {
  return ['admin', 'manager'].includes(normalizeRole(role));
}
export function todayTashkentDate(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tashkent', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day}`;
}
export function safeString(value, fallback = '—', max = 4000) { return typeof value === 'string' && value.trim() ? value.trim().slice(0, max) : fallback; }
export function safeArray(value) { return Array.isArray(value) ? value : []; }
export function formatDate(value, time = false) { if (typeof value !== 'string' || !value) return '—'; const parsed = new Date(time ? value : `${value}T12:00:00`); return Number.isNaN(parsed.getTime()) ? '—' : (time ? parsed.toLocaleString('ru-RU') : parsed.toLocaleDateString('ru-RU')); }
export function safeErrorDetail(error) { const value = error?.response?.data?.detail; return typeof value === 'string' && value.length <= 240 && !/[<>\r\n]/.test(value) ? value : null; }
export function createIdempotencyKey() { if (globalThis.crypto?.randomUUID) return crypto.randomUUID(); const bytes = new Uint8Array(16); crypto.getRandomValues(bytes); return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join(''); }
