// Pure helpers for the H1 Management Analytics page (TASK_233). They validate
// filters, build request keys and format values; they never derive a metric.
// Every number shown on the page is a value of GET /api/management-analytics
// (TASK_232, definitions_version management_analytics_v1).

import { toTashkentDateInput } from './tashkentTime.js';

export const MAX_PERIOD_DAYS = 366;
export const DEFAULT_PERIOD_DAYS = 30;
export const PERIOD_PRESETS = Object.freeze([7, 30, 90, 365]);
export const GRANULARITIES = Object.freeze(['day', 'week', 'month']);
export const DEFAULT_GRANULARITY = 'week';
export const FIELD_PAGE_SIZE = 50;
export const MAX_FIELD_PAGE_SIZE = 200;
export const MAX_FIELD_OFFSET = 10000;

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const DAY_MS = 24 * 60 * 60 * 1000;
const MIN_YEAR = 2000;
const MAX_YEAR = 2100;

/** Days since the epoch of a strict `YYYY-MM-DD` calendar date, or null. */
function dayNumber(value) {
  const match = typeof value === 'string' ? ISO_DATE.exec(value) : null;
  if (!match) return null;
  const [year, month, day] = match.slice(1).map(Number);
  if (year < MIN_YEAR || year > MAX_YEAR || month < 1 || month > 12 || day < 1) return null;
  const epoch = Date.UTC(year, month - 1, day);
  const parsed = new Date(epoch);
  if (parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) return null;
  return epoch / DAY_MS;
}

export function isCalendarDate(value) {
  return dayNumber(value) !== null;
}

/** Calendar arithmetic on `YYYY-MM-DD`; '' when the input is not a date. */
export function shiftCalendarDate(value, days) {
  const number = dayNumber(value);
  if (number === null || !Number.isSafeInteger(days)) return '';
  return new Date((number + days) * DAY_MS).toISOString().slice(0, 10);
}

/** Inclusive day count of a period, or null when either bound is not a date. */
export function inclusiveDays(dateFrom, dateTo) {
  const from = dayNumber(dateFrom);
  const to = dayNumber(dateTo);
  return from === null || to === null ? null : to - from + 1;
}

/** The `days`-long inclusive period ending on `today` (Asia/Tashkent calendar). */
export function presetPeriod(days, today = toTashkentDateInput()) {
  return { dateFrom: shiftCalendarDate(today, -(days - 1)), dateTo: today };
}

/**
 * The TASK_232 window rule (inclusive dates, from <= to, at most 366 days),
 * checked before a request is sent so an invalid period never reaches the API.
 */
export function validatePeriod(dateFrom, dateTo) {
  if (!isCalendarDate(dateFrom) || !isCalendarDate(dateTo)) {
    return { valid: false, error: 'Укажите обе даты периода полностью.' };
  }
  const days = inclusiveDays(dateFrom, dateTo);
  if (days < 1) return { valid: false, error: 'Начало периода не может быть позже его окончания.' };
  if (days > MAX_PERIOD_DAYS) {
    return { valid: false, error: `Период не может быть длиннее ${MAX_PERIOD_DAYS} дней.` };
  }
  return { valid: true, days };
}

export function positiveId(value) {
  if (value === null || value === undefined || value === '') return null;
  const text = String(value);
  if (!/^\d+$/.test(text)) return null;
  const id = Number(text);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

// Parameters that change what is counted. A request that differs only in the
// view parameters (granularity, field page) keeps the same scope key, so the
// page may keep showing the previous numbers while it refreshes.
export const SCOPE_PARAMETERS = Object.freeze([
  'date_from', 'date_to', 'enterprise_id', 'field_id', 'current_crop_type_id',
]);

export function scopeKeyOf(params) {
  return SCOPE_PARAMETERS.map((name) => `${name}=${params?.[name] ?? ''}`).join('&');
}

// ─── Formatting ──────────────────────────────────────────────────────────────

const COUNT = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const DECIMAL = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const PERCENT = new Intl.NumberFormat('ru-RU', { style: 'percent', maximumFractionDigits: 1 });

const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);

export function formatCount(value) {
  return isNumber(value) ? COUNT.format(value) : '—';
}

/** A backend rate (0..1, rounded by the server) as a percentage, or null. */
export function formatRate(rate) {
  return isNumber(rate) ? PERCENT.format(rate) : null;
}

/**
 * "18 из 24 (75 %)": numerator and denominator as the backend returned them,
 * with the backend's own rate. A zero denominator has no rate and says so.
 */
export function formatShare(metric) {
  const numerator = metric?.numerator;
  const denominator = metric?.denominator;
  if (!isNumber(numerator) || !isNumber(denominator)) return '—';
  if (denominator === 0) return 'нет данных в выборке за период';
  const rate = formatRate(metric?.rate);
  return `${formatCount(numerator)} из ${formatCount(denominator)}${rate ? ` (${rate})` : ''}`;
}

/** Hours as a readable duration: minutes below one hour, days from 48 hours. */
export function formatHours(hours) {
  if (!isNumber(hours) || hours < 0) return '—';
  if (hours < 1) return `${COUNT.format(Math.round(hours * 60))} мин`;
  if (hours < 48) return `${DECIMAL.format(hours)} ч`;
  return `${DECIMAL.format(hours / 24)} сут`;
}

/** Exact hours next to a converted duration, for example "74,5 ч". */
export function formatExactHours(hours) {
  return isNumber(hours) && hours >= 48 ? `${DECIMAL.format(hours)} ч` : '';
}

export function formatCalendarDate(value) {
  const match = typeof value === 'string' ? ISO_DATE.exec(value) : null;
  return match ? `${match[3]}.${match[2]}.${match[1]}` : '—';
}

export function formatPeriod(dateFrom, dateTo) {
  return `${formatCalendarDate(dateFrom)} — ${formatCalendarDate(dateTo)}`;
}

/** Axis and table label of a period bucket (the server clips the first and last one). */
export function bucketLabel(bucket, granularity) {
  const start = typeof bucket?.bucket_start === 'string' ? bucket.bucket_start : '';
  const end = typeof bucket?.bucket_end === 'string' ? bucket.bucket_end : '';
  const short = (value) => formatCalendarDate(value).slice(0, 5);
  if (!isCalendarDate(start)) return '—';
  if (granularity === 'day' || start === end) return short(start);
  if (granularity === 'month' && start.slice(0, 7) === end.slice(0, 7)) {
    const whole = start.endsWith('-01') && shiftCalendarDate(end, 1).endsWith('-01');
    if (whole) return `${start.slice(5, 7)}.${start.slice(0, 4)}`;
  }
  return `${short(start)}–${short(end)}`;
}

// ─── Request failures ────────────────────────────────────────────────────────

export function isCancelledRequest(error) {
  return error?.code === 'ERR_CANCELED'
    || error?.name === 'CanceledError'
    || error?.name === 'AbortError';
}

const NOT_FOUND = Object.freeze({
  'Enterprise not found': 'Выбранное предприятие не найдено или не входит в вашу область доступа.',
  'Field not found': 'Выбранное поле не найдено или не входит в вашу область доступа.',
  'Crop type not found': 'Выбранная культура не найдена.',
});

const FORBIDDEN = Object.freeze({
  'Manager has no enterprise_id': 'У учётной записи руководителя не указано предприятие. Обратитесь к администратору.',
});

const PERIOD_REFUSALS = Object.freeze({
  'date_from must not be later than date_to': 'Начало периода не может быть позже его окончания.',
  'date range exceeds 366 inclusive days': `Период не может быть длиннее ${MAX_PERIOD_DAYS} дней.`,
  'granularity must be day, week or month': 'Шаг динамики должен быть днём, неделей или месяцем.',
});

/**
 * A request failure as page state. The kinds are deliberately distinct: an
 * error is never shown as an empty result. Server texts are mapped to Russian
 * messages; unknown texts are not echoed.
 */
export function describeRequestError(error, { online = true } = {}) {
  if (isCancelledRequest(error)) return null;
  const status = Number(error?.response?.status || 0);
  const detail = error?.response?.data?.detail;
  if (status === 401) {
    return {
      kind: 'unauthorized', status, retryable: false, resettable: false,
      title: 'Сессия завершена',
      message: 'Войдите снова, чтобы открыть управленческую аналитику.',
    };
  }
  if (status === 403) {
    return {
      kind: 'forbidden', status, retryable: false, resettable: false,
      title: 'Нет доступа к управленческой аналитике',
      message: FORBIDDEN[detail]
        || 'Раздел доступен администраторам и руководителям предприятий. Сервер отклонил запрос для вашей учётной записи.',
    };
  }
  if (status === 404) {
    return {
      kind: 'not_found', status, retryable: false, resettable: true,
      title: 'Объект фильтра недоступен',
      message: NOT_FOUND[detail] || 'Выбранный объект фильтра не найден или не входит в вашу область доступа.',
    };
  }
  if (status === 422) {
    let message = 'Сервер отклонил параметры фильтра. Сбросьте фильтры и выберите их заново.';
    if (typeof detail === 'string' && PERIOD_REFUSALS[detail]) message = PERIOD_REFUSALS[detail];
    else if (typeof detail === 'string' && detail.startsWith('crop_type_id is not supported')) {
      message = 'Фильтр по культуре работает только по текущей культуре поля.';
    } else if (detail && typeof detail === 'object' && detail.code === 'result_too_large') {
      const cap = Number.isSafeInteger(detail.row_cap) ? ` (предел — ${formatCount(detail.row_cap)} строк)` : '';
      message = `В разбивке слишком много строк${cap}. Выберите предприятие, чтобы сузить область.`;
    }
    return { kind: 'invalid', status, retryable: false, resettable: true, title: 'Фильтр не принят', message };
  }
  if (status >= 500) {
    return {
      kind: 'server', status, retryable: true, resettable: false,
      title: 'Сервер не смог сформировать аналитику',
      message: 'Показатели не показаны, чтобы не выдавать неполные данные. Повторите попытку.',
    };
  }
  if (status) {
    return {
      kind: 'unexpected', status, retryable: true, resettable: false,
      title: 'Аналитика не загружена',
      message: 'Сервер вернул неожиданный ответ. Повторите попытку.',
    };
  }
  return {
    kind: 'network', status: 0, retryable: true, resettable: false,
    title: 'Нет связи с сервером',
    message: online
      ? 'Запрос не выполнен: сеть недоступна или истекло время ожидания. Повторите попытку.'
      : 'Нет сети. Показатели появятся после восстановления связи и повторной загрузки.',
  };
}
