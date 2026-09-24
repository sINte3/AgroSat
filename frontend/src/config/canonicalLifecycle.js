// One Russian vocabulary for the TASK_225 canonical lifecycles: the inspection
// (/api/anomaly-inspections), the agronomy plan (/api/agronomy-plans) and the
// remediation status the backend projects from both. Legacy TASK_209 values are
// labelled as historical so they are never read as the current lifecycle.

export const INSPECTION_STATUS_LABELS = Object.freeze({
  new: 'Новый',
  assigned: 'Назначен',
  in_progress: 'В работе',
  submitted: 'На проверке',
  confirmed: 'Подтверждён',
  rejected: 'Отклонён',
  cancelled: 'Отменён',
  // TASK_209 rows (source_kind = legacy): read-only history.
  pending: 'Ожидает (устаревший контур)',
  completed: 'Завершён (устаревший контур)',
});

export const INSPECTION_PRIORITY_LABELS = Object.freeze({
  low: 'Низкий',
  normal: 'Обычный',
  high: 'Высокий',
  urgent: 'Срочный',
});

export const INSPECTION_PRIORITY_OPTIONS = Object.freeze(
  ['low', 'normal', 'high', 'urgent'].map((value) => [value, INSPECTION_PRIORITY_LABELS[value]]),
);

export const INSPECTION_SOURCE_LABELS = Object.freeze({
  pixel_ndvi: 'Пиксельный NDVI',
  alert: 'Предупреждение',
  manual: 'Ручной',
  legacy: 'Устаревший контур (TASK_209)',
  // Legacy `source` values of TASK_209 rows.
  attention_queue: 'Очередь внимания (устаревший контур)',
  irrigation_context: 'Контекст орошения (устаревший контур)',
});

export const OPEN_INSPECTION_STATUSES = Object.freeze(['new', 'assigned', 'in_progress', 'submitted']);
export const LEGACY_OPEN_INSPECTION_STATUSES = Object.freeze(['pending', 'in_progress']);

/**
 * Attention-queue and TASK_209 priorities (low/medium/high/critical) mapped to
 * the canonical inspection priority (low/normal/high/urgent).
 */
export function canonicalInspectionPriority(value) {
  const key = String(value || '').trim().toLowerCase();
  if (key === 'critical' || key === 'urgent') return 'urgent';
  if (key === 'high') return 'high';
  if (key === 'low') return 'low';
  return 'normal';
}

export function isLegacyInspection(inspection) {
  return (inspection?.source?.kind ?? inspection?.source_kind) === 'legacy';
}

export function canCancelInspection(inspection) {
  const status = inspection?.status;
  return isLegacyInspection(inspection)
    ? LEGACY_OPEN_INSPECTION_STATUSES.includes(status)
    : OPEN_INSPECTION_STATUSES.includes(status);
}

// Canonical remediation status (backend services/remediation_status.py).
// `tone` never renders a case green unless an improvement was verified.
export const REMEDIATION_STATUS = Object.freeze({
  needs_inspection: { label: 'Нужен осмотр', tone: 'neutral' },
  inspection_active: { label: 'Осмотр выполняется', tone: 'neutral' },
  awaiting_review: { label: 'Результат осмотра на проверке', tone: 'neutral' },
  awaiting_decision: { label: 'Ожидает решения по мерам', tone: 'neutral' },
  plan_active: { label: 'План мер готовится', tone: 'neutral' },
  work_active: { label: 'Работы выполняются', tone: 'neutral' },
  awaiting_satellite_verification: { label: 'Ожидает спутниковой проверки', tone: 'neutral' },
  verification_blocked: { label: 'Спутниковая проверка заблокирована', tone: 'warning' },
  improved_awaiting_closure: { label: 'Улучшение подтверждено, ожидает закрытия', tone: 'success' },
  not_improved: { label: 'Улучшение не подтверждено', tone: 'danger' },
  reopened: { label: 'Возвращено на доработку', tone: 'warning' },
  improved_closed: { label: 'Закрыто: улучшение подтверждено', tone: 'success' },
  closed_without_improvement: { label: 'Закрыто без подтверждённого улучшения', tone: 'closed' },
  rejected: { label: 'Аномалия отклонена', tone: 'closed' },
  cancelled: { label: 'Отменено', tone: 'closed' },
  inspection_closed: { label: 'Осмотр закрыт', tone: 'closed' },
  data_unavailable: { label: 'Данные недоступны', tone: 'warning' },
});

export const TERMINAL_REMEDIATION_STATUSES = Object.freeze([
  'improved_closed', 'closed_without_improvement', 'rejected', 'cancelled', 'inspection_closed',
]);

// Operational Center compatibility vocabulary (`operational_status`).
export const OPERATIONAL_STATUS_LABELS = Object.freeze({
  needs_review: 'Требует решения',
  awaiting_inspection: 'Ожидает осмотра',
  awaiting_review: 'Ожидает проверки',
  awaiting_work: 'Ожидает работы',
  awaiting_evidence: 'Ожидает результата',
  awaiting_verification: 'Ожидает спутниковой проверки',
  stale: 'Данные устарели',
  external_unavailable: 'Источник недоступен',
  improved_closed: 'Улучшение подтверждено',
  closed_without_improvement: 'Закрыто без подтверждённого улучшения',
});

const OVERDUE_TONE = 'border-red-200 bg-red-50 text-red-900';

/**
 * Operational Center case label. The canonical meaning comes from
 * `remediation_status`; `operational_status` (compatibility view) only refines
 * "data unavailable", and the verification outcome distinguishes a worsening
 * from no material change. Nothing here infers an agronomic cause.
 */
export function caseStatusLabel(item) {
  const remediation = item?.remediation_status;
  if (remediation === 'data_unavailable' && OPERATIONAL_STATUS_LABELS[item?.operational_status]) {
    return OPERATIONAL_STATUS_LABELS[item.operational_status];
  }
  if (remediation === 'not_improved') {
    const verification = item?.provenance?.verification_status;
    if (verification === 'WORSENED') return 'Ухудшение после мер';
    if (verification === 'NO_MATERIAL_CHANGE') return 'Без существенных изменений после мер';
  }
  return REMEDIATION_STATUS[remediation]?.label
    || OPERATIONAL_STATUS_LABELS[item?.operational_status]
    || item?.operational_status
    || 'Статус не определён';
}

/** Green is reserved for a verified improvement; a closure without one is never green. */
export function caseStatusTone(item) {
  const remediation = item?.remediation_status;
  if (TERMINAL_REMEDIATION_STATUSES.includes(remediation)) {
    return TONE_CLASSES[remediation === 'improved_closed' ? 'success' : 'closed'];
  }
  if (item?.is_overdue || ['critical', 'extreme', 'urgent'].includes(String(item?.priority).toLowerCase())) return OVERDUE_TONE;
  const tone = REMEDIATION_STATUS[remediation]?.tone;
  if (tone && tone !== 'neutral') return TONE_CLASSES[tone];
  if (item?.blocked || ['stale', 'external_unavailable'].includes(item?.operational_status)) return TONE_CLASSES.warning;
  if (!REMEDIATION_STATUS[remediation] && item?.operational_status === 'improved_closed') return TONE_CLASSES.success;
  return TONE_CLASSES.neutral;
}

// Values accepted by GET /api/operational-center/queue?operational_status=…
// (the backend filter does not accept closed_without_improvement).
export const FILTERABLE_OPERATIONAL_STATUSES = Object.freeze([
  'needs_review', 'awaiting_inspection', 'awaiting_review', 'awaiting_work', 'awaiting_evidence',
  'awaiting_verification', 'stale', 'external_unavailable', 'improved_closed',
]);

export const PLAN_STATUS_LABELS = Object.freeze({
  draft: 'Черновик',
  approved: 'Согласован',
  in_progress: 'В работе',
  pending_verification: 'Ожидает спутниковой проверки',
  rework: 'Доработка',
  closed: 'Закрыт',
  cancelled: 'Отменён',
  superseded: 'Заменён',
});

export const TERMINAL_PLAN_STATUSES = Object.freeze(['closed', 'cancelled', 'superseded']);

export const VERIFICATION_STATUS = Object.freeze({
  PENDING_DATA: { label: 'Нет нового допустимого снимка', tone: 'neutral' },
  TOO_EARLY: { label: 'Слишком рано для проверки', tone: 'neutral' },
  CLOUD_BLOCKED: { label: 'Облачность блокирует проверку', tone: 'warning' },
  QUALITY_BLOCKED: { label: 'Недостаточное качество снимка', tone: 'warning' },
  PROVIDER_DEGRADED: { label: 'Источник снимков недоступен', tone: 'warning' },
  INCONCLUSIVE: { label: 'Недостаточно доказательств', tone: 'warning' },
  IMPROVED: { label: 'Улучшение подтверждено', tone: 'success' },
  NO_MATERIAL_CHANGE: { label: 'Без существенных изменений', tone: 'danger' },
  WORSENED: { label: 'Ухудшение', tone: 'danger' },
});

export const WORK_STATUS_LABELS = Object.freeze({
  planned: 'Запланирована',
  in_progress: 'Выполняется',
  completed: 'Выполнена',
  cancelled: 'Отменена',
});

export function planStatusLabel(plan) {
  if (plan?.status === 'closed') {
    return plan.verification_status === 'IMPROVED'
      ? 'Закрыт: улучшение подтверждено'
      : 'Закрыт без подтверждённого улучшения';
  }
  return PLAN_STATUS_LABELS[plan?.status] || plan?.status || 'Статус не определён';
}

export function planStatusTone(plan) {
  if (plan?.status === 'closed') return plan.verification_status === 'IMPROVED' ? 'success' : 'closed';
  if (['cancelled', 'superseded'].includes(plan?.status)) return 'closed';
  if (plan?.status === 'rework') return 'warning';
  if (plan?.status === 'pending_verification') return VERIFICATION_STATUS[plan.verification_status]?.tone || 'neutral';
  return 'neutral';
}

export const TONE_CLASSES = Object.freeze({
  neutral: 'border-slate-200 bg-slate-50 text-slate-800',
  success: 'border-emerald-200 bg-emerald-50 text-emerald-900',
  warning: 'border-amber-300 bg-amber-50 text-amber-950',
  danger: 'border-rose-300 bg-rose-50 text-rose-900',
  closed: 'border-slate-300 bg-slate-100 text-slate-700',
});
