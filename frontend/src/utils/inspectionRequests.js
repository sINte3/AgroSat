import { parseTashkentDateTimeInput } from './tashkentTime.js';

// Canonical POST /api/anomaly-inspections request construction for the UI
// paths that have no backend-recognized source of their own (attention queue,
// irrigation context, manual). They are `manual`; their context is kept in the
// visible reason text. No alert id, scene, pixel value or geometry hash is
// fabricated here.

export const INSPECTION_REASON_MIN = 5;
export const INSPECTION_REASON_MAX = 2000;

const ATTENTION_PRIORITY_LABELS = { low: 'низкий', medium: 'средний', high: 'высокий', critical: 'критический' };

export const IRRIGATION_REASON_LABELS = Object.freeze({
  water_stress_suspicion: 'Подозрение на водный стресс',
  weather_water_deficit: 'Проверить дефицит влаги по погодному контексту',
  irrigation_interruption: 'Проверить прерывание полива',
  irrigation_delivery_check: 'Проверить доставку воды',
  irrigation_equipment_check: 'Проверить оборудование орошения',
});

const list = (value) => (Array.isArray(value) ? value : []);
const positiveId = (value) => {
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
};

export function attentionInspectionReason(source) {
  const parts = [`Очередь внимания: поле «${source?.field_name || `#${source?.field_id}`}».`];
  if (source?.priority) parts.push(`Приоритет очереди: ${ATTENTION_PRIORITY_LABELS[source.priority] || source.priority}.`);
  if (Number.isFinite(source?.attention_score)) parts.push(`Индекс внимания: ${source.attention_score}.`);
  if (source?.observation_date) parts.push(`Дата наблюдения: ${source.observation_date}.`);
  const codes = list(source?.reason_codes).filter((code) => typeof code === 'string' && code);
  if (codes.length) parts.push(`Причины: ${codes.join(', ')}.`);
  const checks = list(source?.recommended_checks).filter((check) => typeof check === 'string' && check.trim());
  if (checks.length) parts.push(`Проверить: ${checks.map((check) => check.trim()).join('; ')}.`);
  return parts.join(' ').slice(0, INSPECTION_REASON_MAX);
}

export function irrigationInspectionReason(reasonCode) {
  return `Контекст орошения: ${IRRIGATION_REASON_LABELS[reasonCode] || reasonCode}. `
    + 'Проверить состояние в поле и зафиксировать фактические доказательства.';
}

/** Visible validation message for a required inspection deadline, or ''. */
export function inspectionDueError(raw, now = Date.now()) {
  const parsed = parseTashkentDateTimeInput(raw);
  if (parsed.state === 'empty') return 'Укажите срок осмотра.';
  if (parsed.state === 'invalid') return 'Срок указан неполностью или некорректно.';
  if (parsed.epochMs < now - 60_000) return 'Срок не может быть в прошлом.';
  return '';
}

/**
 * Builds the canonical manual inspection request. Returns `{ payload }` or
 * `{ error }`; it never throws on partial input.
 */
export function buildManualInspectionRequest({ fieldId, reason, priority = 'normal', dueInput, assignedToId } = {}, now = Date.now()) {
  const id = positiveId(fieldId);
  if (!id) return { error: 'Выберите поле.' };
  const text = typeof reason === 'string' ? reason.trim() : '';
  if (text.length < INSPECTION_REASON_MIN || text.length > INSPECTION_REASON_MAX) {
    return { error: `Причина осмотра: от ${INSPECTION_REASON_MIN} до ${INSPECTION_REASON_MAX} символов.` };
  }
  const dueError = inspectionDueError(dueInput, now);
  if (dueError) return { error: dueError };
  const payload = {
    field_id: id,
    source_kind: 'manual',
    reason: text,
    priority,
    due_at: parseTashkentDateTimeInput(dueInput).iso,
  };
  const assignee = positiveId(assignedToId);
  if (assignee) payload.assigned_to_id = assignee;
  return { payload };
}
