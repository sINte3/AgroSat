import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  getOperationalCase,
  getOperationalFilterOptions,
  getOperationalSummary,
  listOperationalQueue,
  transitionOperationalNotification,
} from '../api/operationalCenter';
import {
  caseStatusLabel,
  caseStatusTone,
  FILTERABLE_OPERATIONAL_STATUSES,
  OPERATIONAL_STATUS_LABELS as STATUS,
  TONE_CLASSES,
  VERIFICATION_STATUS,
  WORK_STATUS_LABELS,
} from '../config/canonicalLifecycle';

const SOURCE = Object.freeze({
  inspection: 'Осмотр', candidate: 'Спутниковая аномалия', alert: 'Оповещение',
  freshness: 'Свежесть данных', external: 'Внешний цикл',
});
const PRIORITY = Object.freeze({
  critical: 'Критический', extreme: 'Критический', urgent: 'Срочный', high: 'Высокий',
  medium: 'Средний', moderate: 'Средний', normal: 'Обычный', warning: 'Предупреждение',
  low: 'Низкий', info: 'Информация',
});
const REASON = Object.freeze({
  overdue_work: 'срок истёк', critical_source: 'критический источник',
  due_within_24h: 'срок в ближайшие 24 часа', awaiting_assignee_evidence: 'нет итоговой заметки',
  awaiting_satellite_observation: 'нужно новое наблюдение', blocked_context: 'контекст заблокирован',
  external_context_unavailable: 'внешний контекст недоступен', accepted_source_state: 'активное состояние источника',
});
const EMPTY_FILTERS = Object.freeze({
  enterprise_id: '', field_id: '', crop_type_id: '', assignee_id: '', operational_status: '',
  source: '', due_from: '', due_to: '', overdue: '', blocked: '', awaiting_verification: '', external_state: '',
});
const SUMMARY_GROUPS = [
  {
    id: 'current-work',
    title: 'Текущая работа',
    items: [
      ['active_situations', 'Активные'], ['overdue_work', 'Просрочено'],
      ['blocked_or_external_unavailable', 'Заблокировано / недоступно'], ['awaiting_field_inspection', 'Ждут осмотра'],
      ['awaiting_work', 'Ждут работы'], ['awaiting_evidence', 'Ждут результата'],
      ['awaiting_satellite_verification', 'Ждут снимка'],
    ],
  },
  {
    id: 'outcomes',
    title: 'Результат мер',
    items: [
      ['improved_or_closed_recent', 'Закрыто с улучшением · 30 дн.', 'success'],
      ['closed_without_improvement_recent', 'Закрыто без улучшения · 30 дн.', 'closed'],
      ['not_improved', 'Улучшение не подтверждено', 'danger'],
      ['reopened', 'На доработке', 'warning'],
      ['verification_blocked', 'Проверка заблокирована', 'warning'],
    ],
  },
];
const SUMMARY_VALUE_TONE = Object.freeze({
  success: 'text-emerald-800', closed: 'text-slate-700', danger: 'text-rose-800', warning: 'text-amber-900',
});
const NOTIFICATION_TYPE = Object.freeze({
  new_critical: 'Новая критическая ситуация', assignment: 'Назначение', due_soon: 'Приближается срок',
  overdue: 'Просрочено', missing_execution_evidence: 'Нет результата выполнения',
  awaiting_satellite_verification: 'Ожидается спутниковая проверка', external_source_unavailable: 'Источник недоступен',
});

const dateTime = value => {
  if (!value) return 'не задан';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'не определён' : parsed.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
};
// Filter dates are Asia/Tashkent calendar days; the explicit offset keeps the
// bound independent of the browser time zone and never parses partial input.
const dateInputToIso = (value, endOfDay = false) => (
  /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? `${value}${endOfDay ? 'T23:59:59' : 'T00:00:00'}+05:00` : ''
);
const safeError = error => {
  if (!navigator.onLine) return 'Нет сети. Показано последнее загруженное состояние.';
  if (error?.response?.status === 404) return 'Ситуация недоступна или уже вышла из вашей области ответственности.';
  if (error?.response?.status === 409) return 'Состояние изменилось. Обновите данные и повторите действие.';
  return error?.response?.data?.detail || 'Не удалось загрузить оперативные данные.';
};
function SummaryStrip({ summary, loading }) {
  return <div className="space-y-2" role="group" aria-label="Оперативная сводка">
    {SUMMARY_GROUPS.map(group => <section key={group.id} aria-labelledby={`summary-${group.id}`}>
      <h2 id={`summary-${group.id}`} className="mb-1 text-xs font-semibold uppercase tracking-wide text-agro-muted">{group.title}</h2>
      <dl className={`grid grid-cols-2 overflow-hidden rounded-xl border border-agro-border bg-white ${group.items.length > 5 ? 'sm:grid-cols-4 xl:grid-cols-7' : 'sm:grid-cols-3 xl:grid-cols-5'}`}>
        {group.items.map(([key, label, tone], index) => <div className={`min-w-0 px-3 py-3 ${index % 2 ? 'border-l' : ''} border-agro-border sm:border-l`} key={key}>
          <dt className="text-xs leading-4 text-agro-muted">{label}</dt><dd className={`mt-1 text-xl font-semibold tabular-nums ${SUMMARY_VALUE_TONE[tone] || 'text-agro-text'}`} aria-busy={loading}>{loading || !summary ? '—' : summary[key] ?? '—'}</dd>
        </div>)}
      </dl>
    </section>)}
  </div>;
}

function FilterSelect({ label, value, onChange, options }) {
  return <label className="text-sm font-medium text-agro-text">{label}<select className="input mt-1 min-h-11 w-full" value={value} onChange={event => onChange(event.target.value)}><option value="">Все</option>{options.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>;
}

function Filters({ filters, setFilters, options, userRole, open, setOpen }) {
  const update = (key, value) => setFilters(current => ({ ...current, [key]: value }));
  return <section className="rounded-xl border border-agro-border bg-white" aria-labelledby="operational-filters-title">
    <div className="flex min-h-12 items-center justify-between gap-3 px-3 py-2"><div><h2 id="operational-filters-title" className="text-sm font-semibold">Фильтры очереди</h2><p className="text-xs text-agro-muted">Ограничения применяются сервером внутри вашей роли и предприятия.</p></div><button type="button" className="btn-secondary min-h-11 flex-none text-sm focus:ring-2 focus:ring-agro-accent" onClick={() => setOpen(value => !value)} aria-expanded={open} aria-controls="operational-filter-fields">{open ? 'Скрыть' : 'Показать'}</button></div>
    {open && <div id="operational-filter-fields" className="grid gap-3 border-t border-agro-border p-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {userRole === 'admin' && <FilterSelect label="Предприятие" value={filters.enterprise_id} onChange={value => setFilters(current => ({ ...current, enterprise_id: value, field_id: '', crop_type_id: '', assignee_id: '' }))} options={options.enterprises} />}
      <FilterSelect label="Поле" value={filters.field_id} onChange={value => update('field_id', value)} options={options.fields} />
      <FilterSelect label="Культура" value={filters.crop_type_id} onChange={value => update('crop_type_id', value)} options={options.crops} />
      <FilterSelect label="Ответственный" value={filters.assignee_id} onChange={value => update('assignee_id', value)} options={options.assignees} />
      <FilterSelect label="Состояние" value={filters.operational_status} onChange={value => update('operational_status', value)} options={FILTERABLE_OPERATIONAL_STATUSES.map(id => ({ id, label: STATUS[id] }))} />
      <FilterSelect label="Источник" value={filters.source} onChange={value => update('source', value)} options={[...Object.entries(SOURCE).map(([id, label]) => ({ id, label })), { id: 'notification', label: 'Есть моё уведомление' }]} />
      <FilterSelect label="Просрочено" value={filters.overdue} onChange={value => update('overdue', value)} options={[{ id: 'true', label: 'Да' }, { id: 'false', label: 'Нет' }]} />
      <FilterSelect label="Заблокировано" value={filters.blocked} onChange={value => update('blocked', value)} options={[{ id: 'true', label: 'Да' }, { id: 'false', label: 'Нет' }]} />
      <FilterSelect label="Ожидает проверки" value={filters.awaiting_verification} onChange={value => update('awaiting_verification', value)} options={[{ id: 'true', label: 'Да' }, { id: 'false', label: 'Нет' }]} />
      <FilterSelect label="Внешний источник" value={filters.external_state} onChange={value => update('external_state', value)} options={[{ id: 'stale', label: 'Устарел' }, { id: 'unavailable', label: 'Недоступен' }]} />
      <label className="text-sm font-medium text-agro-text">Срок от<input className="input mt-1 min-h-11 w-full" type="date" value={filters.due_from} onChange={event => update('due_from', event.target.value)} /></label>
      <label className="text-sm font-medium text-agro-text">Срок до<input className="input mt-1 min-h-11 w-full" type="date" value={filters.due_to} onChange={event => update('due_to', event.target.value)} /></label>
      <div className="flex items-end sm:col-span-2 lg:col-span-3 xl:col-span-4"><button type="button" className="btn-secondary min-h-11 text-sm focus:ring-2 focus:ring-agro-accent" onClick={() => setFilters({ ...EMPTY_FILTERS })}>Сбросить фильтры</button></div>
    </div>}
  </section>;
}

function QueueRow({ item, selected, onSelect }) {
  const reasons = item.priority_reasons.map(value => REASON[value] || String(value).replaceAll('_', ' '));
  return <li><button type="button" onClick={() => onSelect(item.case_key)} aria-current={selected ? 'true' : undefined} className={`block min-h-24 w-full border-b border-agro-border px-4 py-3 text-left outline-none transition-colors focus:ring-2 focus:ring-inset focus:ring-agro-accent ${selected ? 'bg-emerald-50' : 'bg-white hover:bg-agro-hover'}`}>
    <span className="flex items-start justify-between gap-3"><span className="min-w-0"><span className="block truncate font-semibold text-agro-text">{item.field_name || item.enterprise_name}</span><span className="mt-0.5 block truncate text-xs text-agro-muted">{item.crop_name || SOURCE[item.source] || item.source} · {SOURCE[item.source] || item.source}</span></span><span className={`inline-flex max-w-[55%] flex-none items-center rounded-full border px-2 py-1 text-right text-xs font-medium ${caseStatusTone(item)}`} data-remediation-status={item.remediation_status}>{caseStatusLabel(item)}</span></span>
    <span className="mt-2 block text-sm text-agro-text">{item.title}</span><span className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-agro-muted"><span>{PRIORITY[String(item.priority).toLowerCase()] || item.priority}</span><span>{item.assignee_name || 'Ответственный не назначен'}</span><span className={item.is_overdue ? 'font-semibold text-red-800' : ''}>Срок: {dateTime(item.due_at)}</span>{item.unread_notifications > 0 && <span className="font-semibold text-agro-accent">Новых уведомлений: {item.unread_notifications}</span>}</span><span className="mt-1 block text-xs text-agro-muted">Причина приоритета: {reasons.join(', ')}</span>
  </button></li>;
}

function QueuePanel({ data, loading, failed, selectedKey, onSelect, offset, setOffset }) {
  return <section className="min-w-0 overflow-hidden rounded-xl border border-agro-border bg-white" aria-labelledby="operational-queue-title"><div className="flex min-h-14 items-center justify-between gap-3 border-b border-agro-border px-4 py-2"><div><h2 id="operational-queue-title" className="font-semibold">Приоритетная очередь</h2><p className="text-xs text-agro-muted">{loading ? 'Обновляем…' : `Найдено: ${data.total}`}</p></div><span className="text-xs text-agro-muted">Без скрытого универсального балла</span></div><ul className="max-h-[760px] overflow-y-auto" aria-busy={loading}>
    {loading && data.items.length === 0 && Array.from({ length: 5 }).map((_, index) => <li key={index} className="border-b border-agro-border p-4" aria-hidden="true"><div className="h-4 w-2/3 animate-pulse rounded bg-slate-200 motion-reduce:animate-none" /><div className="mt-3 h-3 w-full animate-pulse rounded bg-slate-100 motion-reduce:animate-none" /><div className="mt-2 h-3 w-4/5 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" /></li>)}
    {!loading && failed && data.items.length === 0 && <li role="alert" className="p-6 text-sm leading-6 text-red-900">Очередь не загружена. Отсутствие строк здесь не означает отсутствие ситуаций.</li>}
    {!loading && !failed && data.items.length === 0 && <li className="p-6 text-sm leading-6 text-agro-muted">Активных ситуаций по выбранным фильтрам нет. Сбросьте ограничения или проверьте закрытые результаты.</li>}
    {data.items.map(item => <QueueRow key={item.case_key} item={item} selected={selectedKey === item.case_key} onSelect={onSelect} />)}
  </ul><div className="flex items-center justify-between gap-3 border-t border-agro-border p-3"><button type="button" className="btn-secondary min-h-11 text-sm focus:ring-2 focus:ring-agro-accent" disabled={offset === 0 || loading} onClick={() => setOffset(value => Math.max(0, value - 50))}>Назад</button><span className="text-xs text-agro-muted">{data.total ? `${offset + 1}–${Math.min(offset + data.items.length, data.total)} из ${data.total}` : '0'}</span><button type="button" className="btn-secondary min-h-11 text-sm focus:ring-2 focus:ring-agro-accent" disabled={offset + data.items.length >= data.total || loading} onClick={() => setOffset(value => value + 50)}>Далее</button></div></section>;
}

function Definition({ label, children, danger = false }) { return <div className="min-w-0"><dt className="text-xs text-agro-muted">{label}</dt><dd className={`mt-1 break-words text-sm ${danger ? 'font-semibold text-red-800' : 'text-agro-text'}`}>{children ?? 'не определено'}</dd></div>; }
function ContextHeading({ id, value, children }) {
  const available = value?.status === 'available'; const unsupported = value?.status === 'unsupported';
  return <div className="flex flex-wrap items-center justify-between gap-2"><h3 id={id} className="font-semibold text-agro-text">{children}</h3><span className={`rounded-full border px-2 py-1 text-xs font-medium ${available ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : unsupported ? 'border-slate-300 bg-slate-100 text-slate-800' : 'border-amber-300 bg-amber-50 text-amber-950'}`}>{available ? 'Доступно' : unsupported ? 'Не поддерживается' : value?.status === 'stale' ? 'Устарело' : 'Недоступно'}</span></div>;
}

function NotificationRow({ item, editable, online, onTransition, busy }) {
  const [dismissOpen, setDismissOpen] = useState(false); const [reason, setReason] = useState(''); const active = ['unread', 'read'].includes(item.status);
  return <li className="border-t border-agro-border py-3 first:border-t-0"><div className="flex flex-wrap items-start justify-between gap-2"><div className="min-w-0"><p className="font-medium text-agro-text">{item.title}</p><p className="mt-1 text-sm leading-5 text-agro-muted">{item.message}</p><p className="mt-1 text-xs text-agro-muted">{NOTIFICATION_TYPE[item.notification_type] || item.notification_type} · {item.status === 'unread' ? 'не прочитано' : item.status === 'read' ? 'прочитано' : item.status}</p></div>{editable && active && <div className="flex flex-wrap gap-2">{item.status === 'unread' && <button type="button" className="btn-secondary min-h-11 text-sm focus:ring-2 focus:ring-agro-accent" disabled={!online || busy} onClick={() => onTransition(item, 'read')}>Прочитано</button>}<button type="button" className="btn-secondary min-h-11 text-sm focus:ring-2 focus:ring-agro-accent" disabled={!online || busy} onClick={() => setDismissOpen(value => !value)} aria-expanded={dismissOpen}>Скрыть</button></div>}</div>{dismissOpen && editable && active && <div className="mt-3 flex flex-col gap-2 sm:flex-row"><label className="flex-1 text-sm font-medium">Причина<input className="input mt-1 min-h-11 w-full" value={reason} minLength={3} maxLength={1000} onChange={event => setReason(event.target.value)} placeholder="Например: принято в работу" /></label><button type="button" className="btn-primary min-h-11 self-end text-sm" disabled={!online || busy || reason.trim().length < 3} onClick={() => onTransition(item, 'dismiss', reason)}>Подтвердить</button></div>}</li>;
}

function DetailPanel({ detail, loading, error, user, online, onBack, onNavigate, onTransition, busy }) {
  if (loading) return <section className="min-h-[420px] rounded-xl border border-agro-border bg-white p-5" aria-busy="true" aria-label="Загрузка карточки ситуации"><div className="h-6 w-1/2 animate-pulse rounded bg-slate-200 motion-reduce:animate-none" /><div className="mt-5 h-24 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" /><div className="mt-5 h-48 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" /></section>;
  if (error) return <section role="alert" className="min-h-[300px] rounded-xl border border-red-200 bg-white p-5"><h2 className="font-semibold text-red-900">Карточка недоступна</h2><p className="mt-2 text-sm text-red-800">{error}</p><button type="button" className="btn-secondary mt-4 min-h-11 sm:hidden" onClick={onBack}>Вернуться к очереди</button></section>;
  if (!detail) return <section className="hidden min-h-[420px] rounded-xl border border-agro-border bg-white p-6 text-sm leading-6 text-agro-muted sm:block"><h2 className="font-semibold text-agro-text">Карточка ситуации</h2><p className="mt-2 max-w-xl">Выберите строку в очереди. Здесь появятся источник, ответственный, срок, доказательства, погода, телематика и история проверки.</p></section>;
  const item = detail.case; const weather = detail.weather || { status: 'unavailable' }; const telematics = detail.telematics || { status: 'unavailable' }; const editableNotifications = user?.role !== 'viewer';
  const sourceFacts = Object.entries(detail.source_snapshot || {}).filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value) && value !== '').slice(0, 8);
  return <article className="min-w-0 rounded-xl border border-agro-border bg-white px-4 py-4 sm:px-5" aria-labelledby="operational-case-title"><button type="button" className="btn-secondary mb-3 min-h-11 text-sm sm:hidden" onClick={onBack}>← К очереди</button><header className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><p className="text-xs font-medium text-agro-muted">{SOURCE[item.source] || item.source} · {item.case_key}</p><h2 id="operational-case-title" className="mt-1 text-lg font-semibold text-agro-text">{item.title}</h2><p className="mt-1 text-sm text-agro-muted">{item.enterprise_name}{item.field_name ? ` · ${item.field_name}` : ''}{item.crop_name ? ` · ${item.crop_name}` : ''}</p></div><span className={`rounded-full border px-3 py-1.5 text-sm font-medium ${caseStatusTone(item)}`} data-remediation-status={item.remediation_status}>{caseStatusLabel(item)}</span></header>
    <dl className="mt-4 grid gap-3 border-y border-agro-border py-4 sm:grid-cols-2 xl:grid-cols-4"><Definition label="Приоритет">{PRIORITY[String(item.priority).toLowerCase()] || item.priority}</Definition><Definition label="Ответственный">{item.assignee_name || 'не назначен'}</Definition><Definition label="Срок" danger={item.is_overdue}>{dateTime(item.due_at)}{item.is_overdue ? ' · просрочено' : ''}</Definition><Definition label="Последнее событие">{dateTime(item.source_time)}</Definition></dl>
    <section className="py-4" aria-labelledby="case-source-title"><h3 id="case-source-title" className="font-semibold text-agro-text">Почему ситуация в очереди</h3><ul className="mt-2 flex flex-wrap gap-2" aria-label="Причины приоритета">{item.priority_reasons.map(reason => <li key={reason} className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-800">{REASON[reason] || reason.replaceAll('_', ' ')}</li>)}</ul>{sourceFacts.length > 0 && <dl className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{sourceFacts.map(([key, value]) => <Definition key={key} label={key.replaceAll('_', ' ')}>{String(value)}</Definition>)}</dl>}<p className="mt-3 text-xs leading-5 text-agro-muted">Источник и его снимок зафиксированы сервером. Контекст не доказывает агрономическую причинность автоматически.</p></section>
    <section className="border-t border-agro-border py-4" aria-labelledby="case-work-title"><h3 id="case-work-title" className="font-semibold text-agro-text">Работы и доказательства</h3>{detail.work_items.length === 0 ? <p className="mt-2 text-sm text-agro-muted">Связанных работ пока нет.</p> : <ul className="mt-2 divide-y divide-agro-border">{detail.work_items.map(work => <li key={work.id} className="py-3 first:pt-0"><div className="flex flex-wrap justify-between gap-2"><span className="font-medium">{work.instruction}</span><span className="text-sm text-agro-muted">{WORK_STATUS_LABELS[work.status] || work.status}</span></div><p className="mt-1 text-sm text-agro-muted">{work.assignee_name || 'не назначено'} · срок {dateTime(work.due_at)}{work.result_note ? ` · ${work.result_note}` : ''}</p></li>)}</ul>}<p className="mt-2 text-sm text-agro-muted">Материалы выполнения: {detail.evidence.length}. {detail.evidence.length === 0 ? 'Файлы или метаданные не приложены.' : 'Показаны только безопасные метаданные.'}</p></section>
    <section className="border-t border-agro-border py-4" aria-labelledby="weather-context-title"><ContextHeading id="weather-context-title" value={weather}>Погода как контекст</ContextHeading><div className="mt-2 text-sm leading-6 text-agro-muted"><p>Источник: {weather.provider || 'open_meteo'} · {weather.fetched_at ? `получено ${dateTime(weather.fetched_at)}` : weather.reason ? `причина: ${weather.reason}` : 'время получения не определено'}</p>{weather.status === 'available' && <p>Температура: {weather.current?.temperature_2m ?? weather.current?.temperature ?? 'нет данных'} · часовой пояс: {weather.timezone || weather.provenance?.timezone || 'не указан'}</p>}<p className="mt-1">{weather.causality_limitation || detail.causality_limitation}</p></div></section>
    <section className="border-t border-agro-border py-4" aria-labelledby="telematics-context-title"><ContextHeading id="telematics-context-title" value={telematics}>Техника и присутствие</ContextHeading><div className="mt-2 text-sm leading-6 text-agro-muted"><p>Провайдер: {telematics.provider || 'wialon'} · {telematics.reason === 'mapping_unavailable' ? 'серверное сопоставление техники с полем не настроено' : telematics.reason || 'состояние без пояснения'}.</p><p>{telematics.units?.length ? `Авторизованных единиц техники: ${telematics.units.length}.` : 'Отсутствие телематики не означает отсутствие работ на поле.'}</p></div></section>
    <section className="border-t border-agro-border py-4" aria-labelledby="case-verification-title"><h3 id="case-verification-title" className="font-semibold text-agro-text">Проверка результата</h3>{detail.verifications.length === 0 ? <p className="mt-2 text-sm text-agro-muted">Проверка новым допустимым наблюдением ещё не создана.</p> : <ul className="mt-2 divide-y divide-agro-border">{detail.verifications.map(value => <li key={value.id} className="flex flex-wrap items-center gap-2 py-2 text-sm"><span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${TONE_CLASSES[VERIFICATION_STATUS[value.status]?.tone || 'neutral']}`}>{VERIFICATION_STATUS[value.status]?.label || value.status}</span><span className="text-agro-muted">цикл {value.cycle} · {dateTime(value.created_at)}</span></li>)}</ul>}<p className="mt-2 text-xs leading-5 text-agro-muted">Изменение индекса на новом снимке не доказывает агрономическую причину; снимки собирает автоматический сборщик.</p></section>
    <section className="border-t border-agro-border py-4" aria-labelledby="case-notifications-title"><h3 id="case-notifications-title" className="font-semibold text-agro-text">Мои уведомления</h3>{!online && editableNotifications && <p className="mt-2 text-sm text-amber-900">Без сети уведомления доступны только для чтения; действия не будут отправлены при восстановлении связи.</p>}{detail.notifications.length === 0 ? <p className="mt-2 text-sm text-agro-muted">Активных уведомлений по этой ситуации нет.</p> : <ul className="mt-2">{detail.notifications.map(notification => <NotificationRow key={notification.id} item={notification} editable={editableNotifications} online={online} busy={busy} onTransition={onTransition} />)}</ul>}</section>
    <section className="border-t border-agro-border py-4" aria-labelledby="case-timeline-title"><h3 id="case-timeline-title" className="font-semibold text-agro-text">Хронология</h3>{detail.timeline.length === 0 ? <p className="mt-2 text-sm text-agro-muted">События пока не зафиксированы.</p> : <ol className="mt-3 space-y-3">{detail.timeline.map((event, index) => <li key={`${event.source_kind}-${event.source_id}-${event.occurred_at}-${index}`} className="grid grid-cols-[0.75rem_minmax(0,1fr)] gap-3"><span className="mt-1.5 h-2.5 w-2.5 rounded-full border-2 border-agro-accent bg-white" aria-hidden="true" /><div><p className="text-sm font-medium text-agro-text">{event.event_type.replaceAll('_', ' ')}</p><p className="text-xs text-agro-muted">{dateTime(event.occurred_at)} · {event.actor_name || 'Система'} · {event.source_kind}</p></div></li>)}</ol>}</section>
    <nav className="flex flex-wrap gap-2 border-t border-agro-border pt-4" aria-label="Связанные рабочие разделы">{item.inspection_id && <button type="button" className="btn-secondary min-h-11 text-sm focus:ring-2 focus:ring-agro-accent" onClick={() => onNavigate('field-inspection-detail', item.inspection_id)}>Осмотр #{item.inspection_id}</button>}{item.plan_id && <button type="button" className="btn-secondary min-h-11 text-sm focus:ring-2 focus:ring-agro-accent" onClick={() => onNavigate('agronomy-plans', item.plan_id)}>План мер #{item.plan_id}</button>}{item.field_id && <button type="button" className="btn-secondary min-h-11 text-sm focus:ring-2 focus:ring-agro-accent" onClick={() => onNavigate('field-detail', item.field_id)}>Поле</button>}<button type="button" className="btn-secondary min-h-11 text-sm focus:ring-2 focus:ring-agro-accent" onClick={() => onNavigate('monitoring')}>Мониторинг</button></nav>
  </article>;
}

export default function OperationalCenterPage({ onNavigate, selectedCaseKey }) {
  const { user } = useAuth(); const [online, setOnline] = useState(() => navigator.onLine); const [filters, setFilters] = useState({ ...EMPTY_FILTERS });
  const [filtersOpen, setFiltersOpen] = useState(() => typeof window === 'undefined' || window.innerWidth >= 768); const [offset, setOffset] = useState(0); const [queue, setQueue] = useState({ items: [], total: 0 }); const [summary, setSummary] = useState(null); const [options, setOptions] = useState({ enterprises: [], fields: [], crops: [], assignees: [] }); const [selected, setSelected] = useState(selectedCaseKey || ''); const [detail, setDetail] = useState(null); const [loadingQueue, setLoadingQueue] = useState(true); const [loadingDetail, setLoadingDetail] = useState(false); const [queueError, setQueueError] = useState(''); const [detailError, setDetailError] = useState(''); const [busy, setBusy] = useState(false);
  useEffect(() => { const yes = () => setOnline(true); const no = () => setOnline(false); window.addEventListener('online', yes); window.addEventListener('offline', no); return () => { window.removeEventListener('online', yes); window.removeEventListener('offline', no); }; }, []);
  useEffect(() => setSelected(selectedCaseKey || ''), [selectedCaseKey]);
  const query = useMemo(() => { const value = { limit: 50, offset }; for (const [key, raw] of Object.entries(filters)) { if (raw === '') continue; if (['overdue', 'blocked', 'awaiting_verification'].includes(key)) value[key] = raw === 'true'; else if (key === 'due_from') value[key] = dateInputToIso(raw); else if (key === 'due_to') value[key] = dateInputToIso(raw, true); else value[key] = raw; } return value; }, [filters, offset]);
  const summaryQuery = useMemo(() => { const { limit: _limit, offset: _offset, ...value } = query; return value; }, [query]);
  const [queueFailed, setQueueFailed] = useState(false);
  const loadQueue = useCallback(async signal => { setLoadingQueue(true); setQueueError(''); try { const [nextQueue, nextSummary] = await Promise.all([listOperationalQueue(query, signal), getOperationalSummary(summaryQuery, signal)]); setQueue(nextQueue); setSummary(nextSummary); setQueueFailed(false); } catch (error) { if (error.name !== 'CanceledError' && error.name !== 'AbortError') { setQueueError(safeError(error)); setQueueFailed(true); if (navigator.onLine) setSummary(null); } } finally { if (!signal.aborted) setLoadingQueue(false); } }, [query, summaryQuery]);
  useEffect(() => { const controller = new AbortController(); void loadQueue(controller.signal); return () => controller.abort(); }, [loadQueue]);
  useEffect(() => { const controller = new AbortController(); getOperationalFilterOptions(filters.enterprise_id || undefined, controller.signal).then(setOptions).catch(error => { if (error.name !== 'CanceledError' && error.name !== 'AbortError') setQueueError(safeError(error)); }); return () => controller.abort(); }, [filters.enterprise_id]);
  const loadDetail = useCallback(async (caseKey, signal) => { if (!caseKey) { setDetail(null); setDetailError(''); return; } setLoadingDetail(true); setDetailError(''); try { setDetail(await getOperationalCase(caseKey, signal)); } catch (error) { if (error.name !== 'CanceledError' && error.name !== 'AbortError') { setDetail(null); setDetailError(safeError(error)); } } finally { if (!signal.aborted) setLoadingDetail(false); } }, []);
  useEffect(() => { const controller = new AbortController(); void loadDetail(selected, controller.signal); return () => controller.abort(); }, [loadDetail, selected]);
  const selectCase = caseKey => { setSelected(caseKey); onNavigate('operational-case', caseKey); };
  const closeCase = () => { setSelected(''); setDetail(null); onNavigate('operational-center'); };
  const transitionNotification = async (notification, action, reason = null) => { if (!online || busy) return; setBusy(true); setDetailError(''); try { await transitionOperationalNotification(notification.id, { action, expected_version: notification.version, reason: action === 'dismiss' ? reason.trim() : null }); const controller = new AbortController(); await Promise.all([loadDetail(selected, controller.signal), loadQueue(controller.signal)]); } catch (error) { setDetailError(safeError(error)); } finally { setBusy(false); } };
  const changeFilters = value => { setOffset(0); setFilters(value); };
  return <section className="h-full overflow-y-auto px-3 pb-24 pt-20 sm:px-4 md:px-6" aria-labelledby="operational-center-page-title"><div className="mx-auto max-w-[1560px] space-y-4"><div className="sr-only"><h1 id="operational-center-page-title">Операционный центр</h1></div>{!online && <div role="status" className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-950">Нет сети. Уже загруженные данные остаются видимыми; изменения уведомлений отключены. Для поддерживаемой офлайн-работы откройте раздел «Осмотры».</div>}{queueError && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">{queueError}</div>}<SummaryStrip summary={summary} loading={loadingQueue} /><Filters filters={filters} setFilters={changeFilters} options={options} userRole={user?.role} open={filtersOpen} setOpen={setFiltersOpen} /><div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(330px,0.78fr)_minmax(0,1.22fr)]"><div className={selected ? 'hidden sm:block' : 'block'}><QueuePanel data={queue} loading={loadingQueue} failed={queueFailed} selectedKey={selected} onSelect={selectCase} offset={offset} setOffset={setOffset} /></div><div className={!selected ? 'hidden sm:block' : 'block'}><DetailPanel detail={detail} loading={loadingDetail} error={detailError} user={user} online={online} onBack={closeCase} onNavigate={onNavigate} onTransition={transitionNotification} busy={busy} /></div></div></div></section>;
}
