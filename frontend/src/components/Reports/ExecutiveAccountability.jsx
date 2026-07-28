import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  downloadExecutiveWorkbook,
  getExecutiveAccountability,
  getExecutiveOverview,
} from '../../api/executiveAccountability';
import {
  formatDate,
  normalizeRole,
  positiveId,
  safeArray,
  safeString,
  todayTashkentDate,
} from '../Inspections/inspectionPresentation';

const QUEUE_LABELS = {
  unassigned_inspections: 'Неназначенные осмотры',
  overdue_inspections: 'Просроченные осмотры',
  open_actions: 'Незакрытые действия',
  overdue_actions: 'Просроченные действия',
  awaiting_verification: 'Ожидают спутниковой проверки',
};

const OUTCOME_LABELS = {
  improved: 'Улучшилось',
  unchanged: 'Без изменений',
  worsened: 'Ухудшилось',
  insufficient_data: 'Недостаточно данных',
};

const INDEX_LABELS = {
  ndvi: 'NDVI',
  savi: 'SAVI',
  evi: 'EVI',
  ndmi: 'NDMI',
  ndre: 'NDRE',
};

function defaultDateFrom() {
  const current = new Date(`${todayTashkentDate()}T12:00:00Z`);
  current.setUTCDate(current.getUTCDate() - 29);
  return current.toISOString().slice(0, 10);
}

function initialFilters() {
  return {
    dateFrom: defaultDateFrom(),
    dateTo: todayTashkentDate(),
    enterpriseId: '',
    attentionLookbackDays: 180,
  };
}

function metric(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString('ru-RU') : '—';
}

function duration(metricValue) {
  if (!metricValue || !metricValue.sample_count) return 'Нет выборки';
  return `${metric(metricValue.median_hours)} ч · p90 ${metric(metricValue.p90_hours)} ч · n=${metric(metricValue.sample_count)}`;
}

function requestError(error, noun = 'данные') {
  const status = error?.response?.status;
  if (status === 403) return `Нет доступа к разделу «${noun}».`;
  if (status === 422) return 'Проверьте период и выбранные фильтры.';
  return `Не удалось загрузить ${noun}.`;
}

function BacklogButton({ value, label, tone = 'neutral', onClick }) {
  const tones = {
    neutral: 'border-agro-border bg-white',
    warning: 'border-amber-200 bg-amber-50/50',
    danger: 'border-red-200 bg-red-50/50',
  };
  return (
    <button type="button" onClick={onClick} className={`min-h-24 rounded-xl border p-4 text-left transition hover:-translate-y-0.5 hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-agro-accent ${tones[tone]}`}>
      <span className="block text-2xl font-bold text-agro-text">{metric(value)}</span>
      <span className="mt-1 block text-sm text-agro-muted">{label}</span>
    </button>
  );
}

function QueuePanel({ queue, state, error, onClose, onRetry, onNavigate, onPage }) {
  const items = safeArray(queue?.items);
  return (
    <section className="mt-5 rounded-xl border border-agro-border bg-white p-4" aria-labelledby="accountability-queue-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 id="accountability-queue-title" className="font-semibold">{QUEUE_LABELS[queue?.kind] || 'Очередь ответственности'}</h4>
          {queue?.owner_id && <p className="mt-1 text-xs text-agro-muted">Фильтр по владельцу #{queue.owner_id}</p>}
        </div>
        <button type="button" onClick={onClose} className="btn-secondary px-3 py-2 text-sm">Закрыть очередь</button>
      </div>
      <div className="mt-3" aria-live="polite">
        {state === 'loading' && <p role="status" className="text-sm">Загружаем очередь…</p>}
        {state === 'error' && <div role="alert" className="text-sm text-red-700"><p>{error}</p><button type="button" onClick={onRetry} className="mt-2 underline">Повторить</button></div>}
        {state === 'ready' && !items.length && <p className="text-sm text-agro-muted">В выбранной очереди нет записей.</p>}
        {state === 'ready' && items.length > 0 && <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="border-b border-agro-border text-left text-xs text-agro-muted">
                <th className="p-2 font-medium">Поле / предприятие</th>
                <th className="p-2 font-medium">Ответственный</th>
                <th className="p-2 font-medium">Содержание</th>
                <th className="p-2 font-medium">Срок</th>
                <th className="p-2 font-medium">Статус</th>
                <th className="p-2 font-medium">Переход</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={`${item.action_id || 'inspection'}-${item.id}`} className="border-b border-agro-border/60 align-top">
                  <td className="p-2"><strong>{safeString(item.field_name)}</strong><span className="block text-xs text-agro-muted">{safeString(item.enterprise_name)}</span></td>
                  <td className="p-2">{safeString(item.owner_name, 'Не назначен')}</td>
                  <td className="max-w-xs p-2">{safeString(item.description)}</td>
                  <td className="p-2 whitespace-nowrap">{formatDate(item.due_date)}</td>
                  <td className="p-2">{safeString(item.verification_status || item.status)}</td>
                  <td className="p-2"><button type="button" onClick={() => onNavigate('field-inspection-detail', item.inspection_id)} className="text-agro-accent underline">Осмотр #{item.inspection_id}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>}
      </div>
      {state === 'ready' && queue && <div className="mt-4 flex items-center justify-between gap-3">
        <button type="button" disabled={queue.offset === 0} onClick={() => onPage(Math.max(0, queue.offset - queue.limit))} className="btn-secondary px-3 py-2 text-sm disabled:opacity-50">Назад</button>
        <span className="text-xs text-agro-muted">Показано {queue.total ? queue.offset + 1 : 0}–{Math.min(queue.offset + items.length, queue.total)} из {queue.total}</span>
        <button type="button" disabled={queue.offset + items.length >= queue.total} onClick={() => onPage(queue.offset + queue.limit)} className="btn-secondary px-3 py-2 text-sm disabled:opacity-50">Вперёд</button>
      </div>}
    </section>
  );
}

export default function ExecutiveAccountability({
  user,
  enterprises = [],
  onNavigate,
}) {
  const role = normalizeRole(user?.role);
  const management = role === 'admin' || role === 'manager';
  const [draft, setDraft] = useState(initialFilters);
  const [applied, setApplied] = useState(initialFilters);
  const [data, setData] = useState(null);
  const [state, setState] = useState('loading');
  const [error, setError] = useState('');
  const [reloadToken, setReloadToken] = useState(0);
  const [queueKind, setQueueKind] = useState('');
  const [queueOwnerId, setQueueOwnerId] = useState('');
  const [queueOffset, setQueueOffset] = useState(0);
  const [queue, setQueue] = useState(null);
  const [queueState, setQueueState] = useState('idle');
  const [queueError, setQueueError] = useState('');
  const [queueReloadToken, setQueueReloadToken] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const mountedRef = useRef(false);
  const overviewGenerationRef = useRef(0);
  const queueGenerationRef = useRef(0);
  const overviewControllerRef = useRef(null);
  const queueControllerRef = useRef(null);
  const exportControllerRef = useRef(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      overviewGenerationRef.current += 1;
      queueGenerationRef.current += 1;
      overviewControllerRef.current?.abort();
      queueControllerRef.current?.abort();
      exportControllerRef.current?.abort();
    };
  }, []);

  const enterpriseOptions = useMemo(() => {
    const known = new Map();
    safeArray(enterprises).forEach((item) => {
      const id = positiveId(item?.id || item?.enterprise_id);
      const name = item?.name || item?.enterprise_name;
      if (id && typeof name === 'string' && name.trim()) known.set(id, name.trim());
    });
    safeArray(data?.enterprises).forEach((item) => {
      const id = positiveId(item?.enterprise_id);
      if (id && item?.enterprise_name) known.set(id, item.enterprise_name);
    });
    return Array.from(known, ([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name, 'ru'));
  }, [data, enterprises]);

  useEffect(() => {
    if (!management) return undefined;
    overviewControllerRef.current?.abort();
    const controller = new AbortController();
    overviewControllerRef.current = controller;
    const generation = ++overviewGenerationRef.current;
    setState('loading');
    setError('');
    getExecutiveOverview(applied, controller.signal)
      .then((value) => {
        if (!mountedRef.current || controller.signal.aborted || generation !== overviewGenerationRef.current) return;
        setData(value);
        setState('ready');
      })
      .catch((requestFailure) => {
        if (!mountedRef.current || controller.signal.aborted || generation !== overviewGenerationRef.current || requestFailure?.code === 'ERR_CANCELED') return;
        setState('error');
        setError(requestError(requestFailure, 'операционную сводку'));
      });
    return () => controller.abort();
  }, [applied, management, reloadToken]);

  useEffect(() => {
    if (!queueKind || !management) return undefined;
    queueControllerRef.current?.abort();
    const controller = new AbortController();
    queueControllerRef.current = controller;
    const generation = ++queueGenerationRef.current;
    setQueueState('loading');
    setQueueError('');
    getExecutiveAccountability({
      ...applied,
      kind: queueKind,
      ownerId: queueOwnerId,
      limit: 25,
      offset: queueOffset,
    }, controller.signal)
      .then((value) => {
        if (!mountedRef.current || controller.signal.aborted || generation !== queueGenerationRef.current) return;
        setQueue(value);
        setQueueState('ready');
      })
      .catch((requestFailure) => {
        if (!mountedRef.current || controller.signal.aborted || generation !== queueGenerationRef.current || requestFailure?.code === 'ERR_CANCELED') return;
        setQueueState('error');
        setQueueError(requestError(requestFailure, 'очередь ответственности'));
      });
    return () => controller.abort();
  }, [applied, management, queueKind, queueOffset, queueOwnerId, queueReloadToken]);

  const openQueue = useCallback((kind, ownerId = '') => {
    setQueue(null);
    setQueueKind(kind);
    setQueueOwnerId(ownerId ? String(ownerId) : '');
    setQueueOffset(0);
  }, []);

  const closeQueue = useCallback(() => {
    queueControllerRef.current?.abort();
    setQueueKind('');
    setQueueOwnerId('');
    setQueue(null);
    setQueueState('idle');
  }, []);

  const applyFilters = useCallback((event) => {
    event.preventDefault();
    if (draft.dateFrom > draft.dateTo) {
      setError('Начало периода не может быть позже окончания.');
      setState('error');
      return;
    }
    closeQueue();
    setApplied({ ...draft });
  }, [closeQueue, draft]);

  const drillEnterprise = useCallback((enterpriseId) => {
    const next = { ...applied, enterpriseId: String(enterpriseId) };
    setDraft(next);
    setApplied(next);
    closeQueue();
  }, [applied, closeQueue]);

  const exportWorkbook = useCallback(async () => {
    if (exporting) return;
    setExporting(true);
    setExportError('');
    const controller = new AbortController();
    exportControllerRef.current = controller;
    let objectUrl = '';
    try {
      const response = await downloadExecutiveWorkbook(applied, controller.signal);
      if (!response?.data?.size) throw new Error('empty_export');
      const contentType = response.headers?.['content-type'] || response.data.type || '';
      if (!contentType.includes('spreadsheetml')) throw new Error('invalid_export');
      objectUrl = URL.createObjectURL(response.data);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = 'agrosat-executive-accountability.xlsx';
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (requestFailure) {
      if (requestFailure?.name !== 'AbortError' && requestFailure?.code !== 'ERR_CANCELED') setExportError(requestError(requestFailure, 'Excel-отчёт'));
    } finally {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      if (!controller.signal.aborted) setExporting(false);
    }
  }, [applied, exporting]);

  if (!management) return null;
  const backlog = data?.backlog || {};
  const quality = data?.data_quality || {};
  const qualityWarnings = Number(quality.attention_stale_fields || 0)
    + Number(quality.attention_no_data_fields || 0)
    + Number(quality.attention_low_confidence_fields || 0);

  return (
    <section className="mb-8 rounded-2xl border border-agro-border bg-agro-surface2/40 p-4 md:p-6" aria-labelledby="executive-accountability-title">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-agro-accent">Операционный контроль</p>
          <h2 id="executive-accountability-title" className="mt-1 text-xl font-bold text-agro-text md:text-2xl">Ответственность и замыкание цикла</h2>
          <p className="mt-1 text-sm text-agro-muted">Кому назначена работа, что просрочено и подтверждён ли результат следующим наблюдением.</p>
        </div>
        <button type="button" onClick={exportWorkbook} disabled={exporting || state !== 'ready'} className="btn-secondary px-4 py-2 disabled:opacity-50">{exporting ? 'Готовим Excel…' : 'Скачать Excel'}</button>
      </div>
      {exportError && <p role="alert" className="mt-2 text-sm text-red-700">{exportError}</p>}

      <form onSubmit={applyFilters} className="mt-5 grid grid-cols-1 gap-3 rounded-xl bg-white p-4 sm:grid-cols-2 lg:grid-cols-4">
        <label className="text-sm">С даты<input type="date" required className="input mt-1 w-full p-2" value={draft.dateFrom} onChange={(event) => setDraft((current) => ({ ...current, dateFrom: event.target.value }))} /></label>
        <label className="text-sm">По дату<input type="date" required className="input mt-1 w-full p-2" value={draft.dateTo} onChange={(event) => setDraft((current) => ({ ...current, dateTo: event.target.value }))} /></label>
        {role === 'admin' ? <label className="text-sm">Предприятие
          <select className="input mt-1 w-full p-2" value={draft.enterpriseId} onChange={(event) => setDraft((current) => ({ ...current, enterpriseId: event.target.value }))}>
            <option value="">Все предприятия</option>
            {enterpriseOptions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label> : <div className="text-sm"><span className="block">Область доступа</span><p className="mt-1 rounded-lg bg-agro-surface2 p-2">Назначенное предприятие</p></div>}
        <div className="flex items-end gap-2"><button className="btn-primary min-h-11 flex-1 px-4 py-2">Применить</button><button type="button" onClick={() => setReloadToken((value) => value + 1)} className="btn-secondary min-h-11 px-3 py-2" aria-label="Обновить операционную сводку">Обновить</button></div>
      </form>

      <div className="mt-5" aria-live="polite">
        {state === 'loading' && <div className="rounded-xl bg-white p-5" role="status">Загружаем операционную сводку…</div>}
        {state === 'error' && <div className="rounded-xl bg-white p-5" role="alert"><p className="text-red-700">{error}</p><button type="button" onClick={() => setReloadToken((value) => value + 1)} className="btn-primary mt-3 px-3 py-2">Повторить</button></div>}
        {state === 'ready' && data && <>
          <div className={`rounded-xl border p-4 ${qualityWarnings ? 'border-amber-200 bg-amber-50' : 'border-emerald-200 bg-emerald-50'}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-semibold">{qualityWarnings ? 'Есть ограничения качества данных' : 'Критичных ограничений данных не выявлено'}</h3>
              <span className="text-xs">{data.date_range?.from} — {data.date_range?.to} · {data.timezone}</span>
            </div>
            <p className="mt-2 text-sm">Устаревшие: {metric(quality.attention_stale_fields)} · без данных: {metric(quality.attention_no_data_fields)} · низкая уверенность: {metric(quality.attention_low_confidence_fields)}</p>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-agro-muted">
              {Object.entries(quality.latest_observation_by_index || {}).map(([code, value]) => <span key={code}>{INDEX_LABELS[code] || code}: {formatDate(value)}</span>)}
            </div>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
            <BacklogButton value={backlog.attention_fields_now} label="Поля требуют внимания" tone={backlog.attention_critical ? 'danger' : 'warning'} onClick={() => onNavigate('field-attention')} />
            <BacklogButton value={backlog.unassigned_inspections} label="Осмотры без исполнителя" tone={backlog.unassigned_inspections ? 'warning' : 'neutral'} onClick={() => openQueue('unassigned_inspections')} />
            <BacklogButton value={backlog.overdue_inspections} label="Просроченные осмотры" tone={backlog.overdue_inspections ? 'danger' : 'neutral'} onClick={() => openQueue('overdue_inspections')} />
            <BacklogButton value={backlog.overdue_actions} label="Просроченные действия" tone={backlog.overdue_actions ? 'danger' : 'neutral'} onClick={() => openQueue('overdue_actions')} />
            <BacklogButton value={backlog.awaiting_verification} label="Ожидают проверки" tone={backlog.awaiting_verification ? 'warning' : 'neutral'} onClick={() => openQueue('awaiting_verification')} />
          </div>

          {queueKind && <QueuePanel queue={queue || { kind: queueKind, owner_id: queueOwnerId ? Number(queueOwnerId) : null }} state={queueState} error={queueError} onClose={closeQueue} onRetry={() => setQueueReloadToken((value) => value + 1)} onNavigate={onNavigate} onPage={setQueueOffset} />}

          <div className="mt-5 grid grid-cols-1 gap-4 xl:grid-cols-2">
            <article className="rounded-xl bg-white p-4">
              <h3 className="font-semibold">Скорость операционного цикла</h3>
              <dl className="mt-3 space-y-3 text-sm">
                <div><dt className="text-agro-muted">Сигнал внимания → создан осмотр</dt><dd className="font-medium">{duration(data.cycle_times?.attention_signal_to_inspection_hours)}</dd></div>
                <div><dt className="text-agro-muted">Завершён осмотр → создано действие</dt><dd className="font-medium">{duration(data.cycle_times?.inspection_to_action_hours)}</dd></div>
                <div><dt className="text-agro-muted">Создано действие → закрыто</dt><dd className="font-medium">{duration(data.cycle_times?.action_to_close_hours)}</dd></div>
              </dl>
              <p className="mt-3 text-xs text-agro-muted">Первый интервал — proxy от даты исходного наблюдения: точного времени постановки в очередь текущая схема не хранит.</p>
            </article>
            <article className="rounded-xl bg-white p-4">
              <h3 className="font-semibold">Результат следующего наблюдения</h3>
              <div className="mt-3 grid grid-cols-2 gap-3">
                {Object.entries(OUTCOME_LABELS).map(([code, label]) => <div key={code} className="rounded-lg bg-agro-surface2 p-3"><strong className="block text-xl">{metric(data.verification_outcomes?.[code])}</strong><span className="text-xs text-agro-muted">{label}</span></div>)}
              </div>
              <p className="mt-3 text-xs text-agro-muted">Изменение индекса не доказывает агрономическую причинность.</p>
            </article>
          </div>

          <div className="mt-5 grid grid-cols-1 gap-4 xl:grid-cols-2">
            <article className="rounded-xl bg-white p-4">
              <h3 className="font-semibold">Предприятия с незакрытой работой</h3>
              {!safeArray(data.enterprises).length && <p className="mt-3 text-sm text-agro-muted">Нет предприятий в выбранной области.</p>}
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[560px] text-sm">
                  <thead><tr className="border-b border-agro-border text-left text-xs text-agro-muted"><th className="p-2 font-medium">Предприятие</th><th className="p-2 font-medium">Внимание</th><th className="p-2 font-medium">Осмотры</th><th className="p-2 font-medium">Действия</th><th className="p-2 font-medium">Просрочено</th></tr></thead>
                  <tbody>{safeArray(data.enterprises).map((item) => <tr key={item.enterprise_id} className="border-b border-agro-border/60"><td className="p-2"><button type="button" disabled={role !== 'admin'} onClick={() => drillEnterprise(item.enterprise_id)} className="font-medium text-left enabled:text-agro-accent enabled:underline disabled:text-agro-text">{safeString(item.enterprise_name)}</button></td><td className="p-2">{metric(item.attention_fields_now)}</td><td className="p-2">{metric(item.open_inspections)}</td><td className="p-2">{metric(item.open_actions)}</td><td className="p-2 font-medium text-red-700">{metric(Number(item.overdue_inspections || 0) + Number(item.overdue_actions || 0))}</td></tr>)}</tbody>
                </table>
              </div>
            </article>
            <article className="rounded-xl bg-white p-4">
              <h3 className="font-semibold">Владельцы незакрытых действий</h3>
              {!safeArray(data.owners).length && <p className="mt-3 text-sm text-agro-muted">Незакрытых действий нет.</p>}
              <ul className="mt-3 divide-y divide-agro-border">
                {safeArray(data.owners).map((item) => <li key={item.owner_id} className="flex flex-wrap items-center justify-between gap-3 py-3"><div><strong>{safeString(item.owner_name)}</strong><p className="text-xs text-agro-muted">Ближайший срок: {formatDate(item.next_due_date)}</p></div><button type="button" onClick={() => openQueue('open_actions', item.owner_id)} className="btn-secondary px-3 py-2 text-sm">{metric(item.unresolved_actions)} незакрыто · {metric(item.overdue_actions)} просрочено</button></li>)}
              </ul>
            </article>
          </div>
        </>}
      </div>
    </section>
  );
}
