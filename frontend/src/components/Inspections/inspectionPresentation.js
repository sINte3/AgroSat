export const STATUS_LABELS = { pending: 'Ожидает', in_progress: 'В работе', completed: 'Завершён', cancelled: 'Отменён' };
export const SOURCE_LABELS = { attention_queue: 'Очередь внимания', manual: 'Вручную' };
export const PRIORITY_LABELS = { low: 'Низкий', medium: 'Средний', high: 'Высокий', critical: 'Критический' };
export function positiveId(value) {
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}
export function normalizeRole(role) {
  return typeof role === 'string' ? role.trim().toLowerCase() : '';
}
export function canWriteInspections(role) {
  return ['admin', 'manager', 'agronomist'].includes(normalizeRole(role));
}
export function filterAssigneesForEnterprise(options, enterpriseId) {
  const selectedEnterpriseId = positiveId(enterpriseId);
  const seen = new Set();
  return safeArray(options).filter((option) => {
    const id = positiveId(option?.id);
    if (!id || seen.has(id)) return false;
    const candidateEnterpriseId = positiveId(option?.enterpriseId);
    if (selectedEnterpriseId && candidateEnterpriseId !== selectedEnterpriseId) return false;
    seen.add(id);
    return true;
  });
}
export function todayTashkentDate(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tashkent', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day}`;
}
export function finiteInteger(value, fallback = '—') { return Number.isSafeInteger(value) && Number.isFinite(value) ? value : fallback; }
export function safeString(value, fallback = '—', max = 4000) { return typeof value === 'string' && value.trim() ? value.trim().slice(0, max) : fallback; }
export function safeArray(value) { return Array.isArray(value) ? value : []; }
export function displayName(user, fallback = 'Не назначен') { return positiveId(user?.id) && typeof user?.display_name === 'string' && user.display_name.trim() ? user.display_name.trim().slice(0, 160) : fallback; }
export function formatDate(value, time = false) { if (typeof value !== 'string' || !value) return '—'; const parsed = new Date(time ? value : `${value}T12:00:00`); return Number.isNaN(parsed.getTime()) ? '—' : (time ? parsed.toLocaleString('ru-RU') : parsed.toLocaleDateString('ru-RU')); }
export function safeErrorDetail(error) { const value = error?.response?.data?.detail; return typeof value === 'string' && value.length <= 240 && !/[<>\r\n]/.test(value) ? value : null; }
export function createIdempotencyKey() { if (globalThis.crypto?.randomUUID) return crypto.randomUUID(); const bytes = new Uint8Array(16); crypto.getRandomValues(bytes); return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join(''); }
export function inspectionError(error, detail = false) {
  const status = error?.response?.status;
  if (status === 403) return 'Недостаточно прав для выполнения действия.';
  if (status === 404) return 'Осмотр не найден или недоступен.';
  if (status === 422) return 'Проверьте параметры запроса.';
  if (status === 409) {
    return 'Осмотр уже изменён другим пользователем или действие больше недоступно.';
  }
  return detail ? 'Не удалось загрузить осмотр.' : 'Не удалось выполнить действие.';
}
