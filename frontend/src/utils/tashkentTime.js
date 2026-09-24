// Asia/Tashkent has kept a fixed UTC+05:00 offset without daylight saving
// since 1992, so wall-clock conversion is exact arithmetic and does not depend
// on the browser's own time zone or ICU data.
export const TASHKENT_TIME_ZONE = 'Asia/Tashkent';
export const TASHKENT_UTC_OFFSET = '+05:00';

const OFFSET_MS = 5 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const DATE_TIME_INPUT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/;
const MIN_YEAR = 2000;
const MAX_YEAR = 2100;

const pad = (value) => String(value).padStart(2, '0');

function instantOf(value) {
  const instant = value instanceof Date ? value : new Date(value);
  return Number.isNaN(instant.getTime()) ? null : instant;
}

function wallClock(instant) {
  const shifted = new Date(instant.getTime() + OFFSET_MS);
  return {
    year: shifted.getUTCFullYear(),
    month: shifted.getUTCMonth() + 1,
    day: shifted.getUTCDate(),
    hour: shifted.getUTCHours(),
    minute: shifted.getUTCMinutes(),
  };
}

/** `YYYY-MM-DDTHH:mm` in Asia/Tashkent for a datetime-local input, or '' when invalid. */
export function toTashkentDateTimeInput(value = new Date()) {
  const instant = instantOf(value);
  if (!instant) return '';
  const { year, month, day, hour, minute } = wallClock(instant);
  return `${year}-${pad(month)}-${pad(day)}T${pad(hour)}:${pad(minute)}`;
}

/** `YYYY-MM-DD` in Asia/Tashkent, or '' when invalid. */
export function toTashkentDateInput(value = new Date()) {
  return toTashkentDateTimeInput(value).slice(0, 10);
}

/** A datetime-local value `days` Tashkent calendar days after `now`, at `hour`:00. */
export function tashkentDateTimeInputAfterDays(days = 1, { hour = 17, now = new Date() } = {}) {
  const today = toTashkentDateInput(now);
  if (!today) return '';
  const [year, month, day] = today.split('-').map(Number);
  const target = new Date(Date.UTC(year, month - 1, day) + days * DAY_MS);
  return `${target.getUTCFullYear()}-${pad(target.getUTCMonth() + 1)}-${pad(target.getUTCDate())}T${pad(hour)}:00`;
}

/**
 * Validates raw datetime-local text without ever throwing.
 *
 * Returns `{ state: 'empty' }`, `{ state: 'invalid', reason }` or
 * `{ state: 'valid', iso, epochMs }` where `iso` carries the explicit
 * Asia/Tashkent offset (for example `2026-09-25T17:00:00+05:00`).
 */
export function parseTashkentDateTimeInput(raw) {
  const value = typeof raw === 'string' ? raw.trim() : '';
  if (!value) return { state: 'empty' };
  const match = DATE_TIME_INPUT.exec(value);
  if (!match) return { state: 'invalid', reason: 'format' };
  const [year, month, day, hour, minute, second = 0] = match.slice(1).map((part) => Number(part || 0));
  if (year < MIN_YEAR || year > MAX_YEAR) return { state: 'invalid', reason: 'range' };
  if (month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) {
    return { state: 'invalid', reason: 'calendar' };
  }
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  if (day < 1 || day > daysInMonth) return { state: 'invalid', reason: 'calendar' };
  const epochMs = Date.UTC(year, month - 1, day, hour, minute, second) - OFFSET_MS;
  return {
    state: 'valid',
    iso: `${year}-${pad(month)}-${pad(day)}T${pad(hour)}:${pad(minute)}:${pad(second)}${TASHKENT_UTC_OFFSET}`,
    epochMs,
  };
}

/** Human-readable Asia/Tashkent date and time, independent of the browser zone. */
export function formatTashkentDateTime(value, fallback = 'не задан') {
  if (value === null || value === undefined || value === '') return fallback;
  const instant = instantOf(value);
  if (!instant) return fallback;
  return instant.toLocaleString('ru-RU', {
    timeZone: TASHKENT_TIME_ZONE,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}
