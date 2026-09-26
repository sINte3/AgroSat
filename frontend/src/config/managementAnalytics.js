// Russian vocabulary for GET /api/management-analytics (TASK_232). Every entry
// names a key of the accepted response contract; nothing here derives a
// number. Labels of the canonical states come from canonicalLifecycle.js, so a
// state reads the same here as on an Operational Center queue item.

import { REMEDIATION_STATUS, VERIFICATION_STATUS } from './canonicalLifecycle.js';

export const MANAGEMENT_ANALYTICS_DEFINITIONS_VERSION = 'management_analytics_v1';

const state = (key) => REMEDIATION_STATUS[key].label;

// The canonical workflow as the page draws it. `states` are keys of
// current.by_remediation_status; `parts` are that object's own sub-keys. The
// counts are a current snapshot (every active case is in exactly one state),
// not a conversion funnel.
export const LIFECYCLE_PHASES = Object.freeze([
  {
    id: 'attention',
    title: 'Проблема выявлена',
    states: [{
      key: 'needs_inspection',
      label: state('needs_inspection'),
      parts: [['inspections', 'осмотров'], ['candidates', 'спутниковых аномалий'], ['alerts', 'предупреждений']],
      note: ['unassigned', 'осмотров без исполнителя'],
    }],
  },
  {
    id: 'inspection',
    title: 'Осмотр в поле',
    states: [{ key: 'inspection_active', label: state('inspection_active') }],
  },
  {
    id: 'review',
    title: 'Разбор осмотра',
    states: [
      { key: 'awaiting_review', label: state('awaiting_review') },
      { key: 'awaiting_decision', label: state('awaiting_decision') },
    ],
  },
  {
    id: 'plan',
    title: 'План мер',
    states: [
      { key: 'plan_active', label: state('plan_active'), parts: [['draft', 'черновик'], ['approved', 'согласован']] },
      { key: 'reopened', label: state('reopened') },
    ],
  },
  {
    id: 'work',
    title: 'Работы',
    states: [{ key: 'work_active', label: state('work_active') }],
  },
  {
    id: 'verification',
    title: 'Спутниковая проверка',
    states: [
      {
        key: 'awaiting_satellite_verification',
        label: state('awaiting_satellite_verification'),
        parts: [['pending_data', 'нет нового снимка'], ['too_early', 'слишком рано']],
      },
      {
        key: 'verification_blocked',
        label: state('verification_blocked'),
        parts: [
          ['cloud_blocked', 'облачность'], ['quality_blocked', 'качество снимка'],
          ['provider_degraded', 'источник снимков'], ['inconclusive', 'недостаточно доказательств'],
        ],
      },
      { key: 'improved_awaiting_closure', label: state('improved_awaiting_closure') },
      {
        key: 'not_improved',
        label: state('not_improved'),
        parts: [['unchanged', 'без существенных изменений'], ['worsened', 'ухудшение']],
      },
    ],
  },
]);

export const PRIORITY_LABELS = Object.freeze([
  ['critical', 'Критический'], ['high', 'Высокий'], ['normal', 'Обычный'], ['low', 'Низкий'],
]);

export const SOURCE_LABELS = Object.freeze([
  ['inspection', 'Осмотры'], ['candidate', 'Спутниковые аномалии'], ['alert', 'Предупреждения'],
]);

export const NDVI_FRESHNESS_LABELS = Object.freeze([
  ['fresh', 'Свежие'], ['aging', 'Стареют'], ['stale', 'Устарели'], ['never_collected', 'Не собирались'],
  ['cloud_blocked', 'Облачность'], ['provider_degraded', 'Источник недоступен'],
  ['quality_blocked', 'Низкое качество'], ['not_evaluated', 'Ещё не оценивались'],
]);

// Verified outcomes: only the three conclusive r3-f-v1 statuses. Status
// colours carry meaning together with the label, never alone.
export const VERIFIED_OUTCOMES = Object.freeze([
  { key: 'improved', status: 'IMPROVED', label: 'Улучшение', color: '#059669' },
  { key: 'unchanged', status: 'NO_MATERIAL_CHANGE', label: 'Без существенных изменений', color: '#f59e0b' },
  { key: 'worsened', status: 'WORSENED', label: 'Ухудшение', color: '#e11d48' },
]);

// Resolutions without a conclusive verification. They are never outcomes.
export const UNVERIFIED_RESOLUTIONS = Object.freeze([
  ['pending_data', VERIFICATION_STATUS.PENDING_DATA.label],
  ['too_early', VERIFICATION_STATUS.TOO_EARLY.label],
  ['cloud_blocked', VERIFICATION_STATUS.CLOUD_BLOCKED.label],
  ['quality_blocked', VERIFICATION_STATUS.QUALITY_BLOCKED.label],
  ['provider_degraded', VERIFICATION_STATUS.PROVIDER_DEGRADED.label],
  ['inconclusive', VERIFICATION_STATUS.INCONCLUSIVE.label],
]);

export const PERIOD_ACTIVITY = Object.freeze([
  ['anomaly_candidates_detected', 'Спутниковых аномалий выявлено'],
  ['inspections_opened', 'Осмотров открыто'],
  ['inspections_confirmed', 'Осмотров подтверждено'],
  ['inspections_rejected', 'Осмотров отклонено'],
  ['inspections_cancelled', 'Осмотров отменено'],
  ['plans_drafted', 'Планов мер создано'],
  ['plan_cycles_approved', 'Циклов плана согласовано'],
  ['plan_cycles_work_completed', 'Циклов с завершёнными работами'],
  ['plan_cycles_verified', 'Первых завершённых спутниковых проверок'],
  ['plans_cancelled', 'Планов мер отменено'],
]);

export const INSPECTION_OPENING_PARTS = Object.freeze([
  ['manual', 'вручную'], ['alert', 'по предупреждению'], ['pixel_ndvi', 'по пиксельному NDVI'],
]);

// Keys of breakdowns.periods[] that the dynamics chart can plot, one at a time.
export const PERIOD_SERIES = Object.freeze([
  { key: 'inspections_opened', label: 'Осмотров открыто', group: 'Поток работ' },
  { key: 'anomaly_candidates_detected', label: 'Спутниковых аномалий выявлено', group: 'Поток работ' },
  { key: 'plans_drafted', label: 'Планов мер создано', group: 'Поток работ' },
  { key: 'plan_cycles_approved', label: 'Циклов плана согласовано', group: 'Поток работ' },
  { key: 'plan_cycles_work_completed', label: 'Циклов с завершёнными работами', group: 'Поток работ' },
  { key: 'plan_cycles_verified', label: 'Первых завершённых спутниковых проверок', group: 'Поток работ' },
  { key: 'resolved_cycles', label: 'Циклов решено', group: 'Результаты' },
  { key: 'improved', label: 'Решено с улучшением', group: 'Результаты' },
  { key: 'unchanged', label: 'Решено без существенных изменений', group: 'Результаты' },
  { key: 'worsened', label: 'Решено с ухудшением', group: 'Результаты' },
  { key: 'unverified', label: 'Решено без проверенного результата', group: 'Результаты' },
  { key: 'closed', label: 'Планов закрыто', group: 'Результаты' },
  { key: 'returned_for_rework', label: 'Возвратов на доработку после проверки', group: 'Результаты' },
  { key: 'reopen_events', label: 'Переоткрытий', group: 'Результаты' },
]);

export const GRANULARITY_LABELS = Object.freeze([
  ['day', 'Дни'], ['week', 'Недели'], ['month', 'Месяцы'],
]);

// cycle_times.metrics keys of management_analytics_v1 in workflow order.
export const CYCLE_TIME_METRICS = Object.freeze([
  {
    key: 'signal_to_inspection_opened',
    label: 'Спутниковая аномалия → осмотр открыт',
    population: 'Осмотры, открытые из спутниковой аномалии; в период попадают по времени открытия.',
  },
  {
    key: 'inspection_opened_to_reviewed',
    label: 'Осмотр открыт → подтверждён или отклонён',
    population: 'Осмотры, подтверждённые или отклонённые; в период попадают по времени этого решения.',
  },
  {
    key: 'inspection_submitted_to_plan_drafted',
    label: 'Результат осмотра отправлен → создан первый план мер',
    population: 'Осмотры, по которым создан первый план; в период попадают по созданию плана.',
  },
  {
    key: 'plan_approved_to_work_completed',
    label: 'План согласован → работы завершены',
    population: 'Циклы плана с завершёнными работами; в период попадают по завершению работ.',
  },
  {
    key: 'work_completed_to_verified',
    label: 'Работы завершены → результат спутниковой проверки',
    population: 'Циклы с первой завершённой проверкой (улучшение, без изменений или ухудшение); включает обязательное ожидание нового снимка.',
  },
  {
    key: 'case_opened_to_verified_closure',
    label: 'Осмотр открыт → план закрыт после проверки',
    population: 'Планы, закрытые с завершённой проверкой; в период попадают по времени закрытия.',
  },
]);

export const UNSUPPORTED_CYCLE_TIMES = Object.freeze({
  alert_signal_to_inspection_opened: {
    label: 'Предупреждение → осмотр открыт',
    reason: 'Не измеряется: время предупреждения хранится без часового пояса, поэтому интервал нельзя посчитать надёжно.',
  },
});

// Completion cohorts: the population sentence and the splits of each object
// in `completion`, exactly as the API names them.
export const COMPLETION_METRICS = Object.freeze([
  {
    key: 'work_completion',
    title: 'Согласованные за период циклы → работы завершены',
    population: 'Выборка — циклы плана, согласованные за период.',
    parts: [['completed', 'завершены'], ['ended_without_completion', 'закончились без выполнения'], ['open', 'ещё открыты']],
  },
  {
    key: 'verification_completion',
    title: 'Циклы с завершёнными за период работами → есть результат проверки',
    population: 'Выборка — циклы, работы по которым завершены за период.',
    parts: [
      ['improved', 'улучшение'], ['unchanged', 'без существенных изменений'], ['worsened', 'ухудшение'],
      ['not_conclusive', 'без результата проверки'],
    ],
    extraRate: ['improved_rate', 'доля с улучшением во всей выборке'],
  },
  {
    key: 'plan_closure',
    title: 'Созданные за период планы мер → закрыты',
    population: 'Выборка — планы мер, созданные за период (кроме заменённых).',
    parts: [
      ['closed_improved', 'закрыты с улучшением'], ['closed_without_improvement', 'закрыты без улучшения'],
      ['cancelled', 'отменены'], ['open', 'ещё открыты'],
    ],
  },
]);

// Breakdown columns. `current` values are the lifecycle state now, `period`
// values are windowed facts; the two groups are never mixed in one view.
export const BREAKDOWN_CURRENT_COLUMNS = Object.freeze([
  { id: 'monitored_fields', label: 'Поля под мониторингом', read: (row) => row?.monitored_fields },
  { id: 'active_problems', label: 'Активные проблемы', read: (row) => row?.current?.active_problems },
  { id: 'overdue_cases', label: 'Просроченные ситуации', read: (row) => row?.current?.overdue_cases },
  { id: 'overdue_work_items', label: 'Работы с истёкшим сроком', read: (row) => row?.current?.overdue_work_items },
  { id: 'needs_inspection', label: state('needs_inspection'), read: (row) => row?.current?.by_remediation_status?.needs_inspection },
  { id: 'work_active', label: state('work_active'), read: (row) => row?.current?.by_remediation_status?.work_active },
  {
    id: 'awaiting_satellite_verification',
    label: state('awaiting_satellite_verification'),
    read: (row) => row?.current?.by_remediation_status?.awaiting_satellite_verification,
  },
  { id: 'verification_blocked', label: state('verification_blocked'), read: (row) => row?.current?.by_remediation_status?.verification_blocked },
]);

export const BREAKDOWN_PERIOD_COLUMNS = Object.freeze([
  { id: 'inspections_opened', label: 'Осмотров открыто', read: (row) => row?.period?.inspections_opened },
  { id: 'resolved_cycles', label: 'Циклов решено', read: (row) => row?.period?.resolved_cycles },
  { id: 'improved', label: 'Улучшение', read: (row) => row?.period?.improved },
  { id: 'unchanged', label: 'Без существенных изменений', read: (row) => row?.period?.unchanged },
  { id: 'worsened', label: 'Ухудшение', read: (row) => row?.period?.worsened },
  { id: 'unverified', label: 'Без проверенного результата', read: (row) => row?.period?.unverified },
  { id: 'reopen_events', label: 'Переоткрытий', read: (row) => row?.period?.reopen_events },
]);

// The crop dimension of TASK_232 is the field's CURRENT classification only
// (crop_classification.basis = current_crop_season).
export function currentCropNote(referenceYear) {
  const year = Number.isSafeInteger(referenceYear) ? `${referenceYear} года` : 'года формирования отчёта';
  return `Культура — текущая культура поля: последний сезон поля не позже ${year}. `
    + 'Итоги периода по культуре — это итоги полей, у которых эта культура сейчас, '
    + 'а не культура на момент события: по сохранённым данным её установить нельзя.';
}

export const NO_CROP_LABEL = 'Культура не указана';
