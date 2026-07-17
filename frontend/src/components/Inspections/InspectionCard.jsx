import {
  STATUS_LABELS, SOURCE_LABELS, PRIORITY_LABELS, canWriteInspections, displayName,
  finiteInteger, formatDate, normalizeRole, positiveId, safeString,
} from './inspectionPresentation';

export function allowedActions(item, user) {
  const role = normalizeRole(user?.role);
  if (!canWriteInspections(role)) return { edit: false, start: false, complete: false, cancel: false };
  const userId = positiveId(user?.id);
  const assignedId = positiveId(item?.assigned_to?.id);
  const creatorId = positiveId(item?.created_by?.id);
  const active = ['pending', 'in_progress'].includes(item?.status);
  const globalRole = role === 'admin' || role === 'manager';
  const ownAssignment = role === 'agronomist' && userId === assignedId;
  return {
    edit: active && (globalRole || ownAssignment),
    start: item?.status === 'pending' && Boolean(assignedId) && (globalRole || ownAssignment),
    complete: item?.status === 'in_progress' && (globalRole || ownAssignment),
    cancel: (active && globalRole) || (role === 'agronomist' && creatorId === userId && item?.status === 'pending'),
  };
}

export default function InspectionCard({ inspection: item, user, onNavigate, onAction, compact = false }) {
  const fieldId = positiveId(item?.field?.id);
  const id = positiveId(item?.id);
  const actions = allowedActions(item, user);
  return (
    <article className="card min-w-0 space-y-3 p-4 focus-within:ring-1 focus-within:ring-agro-accent">
      <div className="flex flex-wrap justify-between gap-2">
        <div>
          <p className="text-xs text-agro-muted">Осмотр #{id || '—'} · Версия {finiteInteger(item?.version)}</p>
          <h2 className="break-words text-lg font-bold text-agro-text">{safeString(item?.title, 'Без названия', 255)}</h2>
          <p className="text-sm text-agro-muted">{safeString(item?.field?.name, 'Поле без названия', 255)} · ID {fieldId || '—'} · {safeString(item?.field?.enterprise_name)}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="rounded-full bg-agro-surface2 px-3 py-1 text-xs font-semibold">{STATUS_LABELS[item?.status] || 'Статус не указан'}</span>
          {item?.is_overdue && <span className="rounded-full bg-red-100 px-3 py-1 text-xs font-semibold text-red-700">Просрочен</span>}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <p>
<span className="text-agro-muted">Источник:</span> {SOURCE_LABELS[item?.source] || '—'}{item?.source_priority ? ` · ${PRIORITY_LABELS[item.source_priority] || '—'}` : ''}{Number.isFinite(item?.source_attention_score) ? ` · ${item.source_attention_score}` : ''}</p>
        <p>
<span className="text-agro-muted">Исполнитель:</span> {displayName(item?.assigned_to)}</p>
        <p>
<span className="text-agro-muted">Создал:</span> {displayName(item?.created_by, 'Не указан')}</p>
        <p>
<span className="text-agro-muted">Срок:</span> {formatDate(item?.due_date)}</p>
      </div>
      {!compact && <div className="grid grid-cols-1 gap-1 text-xs text-agro-muted sm:grid-cols-2 lg:grid-cols-3">
<span>Создан: {formatDate(item?.created_at, true)}</span>
<span>Обновлён: {formatDate(item?.updated_at, true)}</span>{item?.started_at && <span>Начат: {formatDate(item.started_at, true)}</span>}{item?.completed_at && <span>Завершён: {formatDate(item.completed_at, true)}</span>}{item?.cancelled_at && <span>Отменён: {formatDate(item.cancelled_at, true)}</span>}</div>}
      <div className="flex flex-wrap gap-2 border-t border-agro-border pt-3">
        <button type="button" disabled={!fieldId} onClick={() => fieldId && onNavigate('field-detail', fieldId)} className="btn-secondary rounded-lg px-3 py-2 text-sm disabled:opacity-50">Открыть поле</button>
        <button type="button" disabled={!fieldId} onClick={() => fieldId && onNavigate('field-analytics', fieldId)} className="btn-secondary rounded-lg px-3 py-2 text-sm disabled:opacity-50">Аналитика</button>
        <button type="button" disabled={!id} onClick={() => id && onNavigate('field-inspection-detail', id)} className="btn-secondary rounded-lg px-3 py-2 text-sm disabled:opacity-50">Подробнее</button>
        {actions.edit && <button type="button" onClick={() => onAction('edit', item)} className="btn-secondary rounded-lg px-3 py-2 text-sm">Редактировать</button>}
        {actions.start && <button type="button" onClick={() => onAction('start', item)} className="btn-primary rounded-lg px-3 py-2 text-sm">Начать</button>}
        {actions.complete && <button type="button" onClick={() => onAction('complete', item)} className="btn-primary rounded-lg px-3 py-2 text-sm">Завершить</button>}
        {actions.cancel && <button type="button" onClick={() => onAction('cancel', item)} className="rounded-lg border border-red-200 px-3 py-2 text-sm text-red-700">Отменить</button>}
        {item?.status === 'pending' && !item?.assigned_to && <span className="self-center text-xs text-agro-muted">Исполнитель не назначен</span>}
      </div>
    </article>
  );
}
