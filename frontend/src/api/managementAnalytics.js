import client from './client';
import {
  GRANULARITIES,
  MAX_FIELD_OFFSET,
  MAX_FIELD_PAGE_SIZE,
  positiveId,
  validatePeriod,
} from '../utils/managementAnalytics.js';

// GET /api/management-analytics (TASK_232) is the only source of the Management
// Analytics figures. Filters only narrow the server-side scope: the server
// binds a manager to the user's own enterprise whatever the browser sends.

export const MANAGEMENT_ANALYTICS_PARAMETERS = Object.freeze([
  'date_from', 'date_to', 'enterprise_id', 'field_id', 'current_crop_type_id',
  'granularity', 'field_limit', 'field_offset',
]);

export class ManagementAnalyticsFilterError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ManagementAnalyticsFilterError';
  }
}

const absent = (value) => value === null || value === undefined || value === '';

function boundedInteger(value, minimum, maximum, name) {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < minimum || number > maximum) {
    throw new ManagementAnalyticsFilterError(`${name} is out of range`);
  }
  return number;
}

/**
 * Query parameters of one analytics request. Only accepted parameter names are
 * produced, empty values are omitted, and an invalid period or identifier is
 * refused instead of being sent (or silently dropped, which would widen the
 * result). The crop filter is `current_crop_type_id`: the API refuses a bare
 * `crop_type_id` because crops are a current classification of the field.
 */
export function buildManagementAnalyticsParams(filters = {}) {
  const params = {};
  if (!absent(filters.dateFrom) || !absent(filters.dateTo)) {
    const period = validatePeriod(filters.dateFrom, filters.dateTo);
    if (!period.valid) throw new ManagementAnalyticsFilterError(period.error);
    params.date_from = filters.dateFrom;
    params.date_to = filters.dateTo;
  }
  for (const [name, value] of [
    ['enterprise_id', filters.enterpriseId],
    ['field_id', filters.fieldId],
    ['current_crop_type_id', filters.currentCropTypeId],
  ]) {
    if (absent(value)) continue;
    const id = positiveId(value);
    if (!id) throw new ManagementAnalyticsFilterError(`${name} must be a positive integer`);
    params[name] = id;
  }
  if (!absent(filters.granularity)) {
    if (!GRANULARITIES.includes(filters.granularity)) {
      throw new ManagementAnalyticsFilterError('granularity must be day, week or month');
    }
    params.granularity = filters.granularity;
  }
  if (!absent(filters.fieldLimit)) {
    params.field_limit = boundedInteger(filters.fieldLimit, 1, MAX_FIELD_PAGE_SIZE, 'field_limit');
  }
  if (!absent(filters.fieldOffset)) {
    params.field_offset = boundedInteger(filters.fieldOffset, 0, MAX_FIELD_OFFSET, 'field_offset');
  }
  return params;
}

/** One versioned snapshot; only the accepted parameter names are ever sent. */
export async function getManagementAnalytics(params = {}, signal) {
  const accepted = Object.fromEntries(
    Object.entries(params || {}).filter(([name, value]) => (
      MANAGEMENT_ANALYTICS_PARAMETERS.includes(name) && !absent(value)
    )),
  );
  const { data } = await client.get('management-analytics', { params: accepted, signal });
  return data;
}
