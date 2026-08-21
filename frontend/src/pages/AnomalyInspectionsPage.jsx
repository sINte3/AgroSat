import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { useAuth } from '../context/AuthContext';
import {
  getAnomalyInspection,
  getAnomalyInspectionQueue,
} from '../api/anomalyInspections';
import AnomalyInspectionDetail from '../components/Inspections/AnomalyInspectionDetail';
import InspectionSourceDialog from '../components/Inspections/InspectionSourceDialog';
import {
  cacheAssignedInspections,
  getCachedAssignedInspections,
  offlineScope,
} from '../offline/offlineScoutingStore';

const FILTER_VALUES = {
  status: ['', 'new', 'assigned', 'in_progress', 'submitted', 'confirmed', 'rejected', 'cancelled'],
  priority: ['', 'low', 'normal', 'high', 'urgent'],
  source_kind: ['', 'pixel_ndvi', 'alert', 'manual'],
  due_state: ['', 'overdue', 'due'],
  sort: ['priority', 'due_at', 'created_at'],
};
const STATUS = { new: 'Новый', assigned: 'Назначен', in_progress: 'В работе', submitted: 'На проверке', confirmed: 'Подтверждён', rejected: 'Отклонён', cancelled: 'Отменён' };
const PRIORITY = { low: 'Низкий', normal: 'Обычный', high: 'Высокий', urgent: 'Срочный' };
const SOURCE = { pixel_ndvi: 'Пиксельный NDVI', alert: 'Предупреждение', manual: 'Ручной' };

function validFilter(name, value) {
  return FILTER_VALUES[name]?.includes(value) ? value : FILTER_VALUES[name]?.[0] || '';
}

function filtersFrom(searchParams, user) {
  return {
    search: (searchParams.get('q') || '').slice(0, 100),
    enterprise_id: searchParams.get('enterprise') || '',
    field_id: searchParams.get('field') || '',
    assigned_to_id: user?.role === 'agronomist' ? String(user.id) : (searchParams.get('assignee') || ''),
    status: validFilter('status', searchParams.get('status') || ''),
    priority: validFilter('priority', searchParams.get('priority') || ''),
    source_kind: validFilter('source_kind', searchParams.get('source') || ''),
    due_state: validFilter('due_state', searchParams.get('due') || ''),
    sort: validFilter('sort', searchParams.get('sort') || 'priority'),
    limit: 50,
    offset: 0,
  };
}

function query(filters) {
  return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== '' && value != null));
}

function loadMessage(error) {
  if (!error?.response) return 'Нет соединения с сервером.';
  if (error.response.status === 403) return 'У вашей роли нет доступа к этой очереди.';
  return 'Не удалось загрузить очередь осмотров.';
}

function Kpis({ summary }) {
  const items = [
    ['Открыто', summary?.open], ['Просрочено', summary?.overdue], ['Ожидают проверки', summary?.awaiting_review],
    ['Активные действия', summary?.active_actions], ['Нужна верификация', summary?.verification_due],
  ];
  return <dl className="grid grid-cols-2 divide-x divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white sm:grid-cols-5 sm:divide-y-0">{items.map(([label, value]) => <div key={label} className="min-w-0 p-3 sm:p-4"><dt className="text-xs font-semibold leading-4 text-slate-600">{label}</dt><dd className="mt-1 font-mono text-2xl font-bold tabular-nums text-slate-950">{Number(value || 0)}</dd></div>)}</dl>;
}

export default function AnomalyInspectionsPage({ onNavigate, enterprises = [], selectedInspectionId }) {
  const { user } = useAuth();
  const scope = offlineScope(user);
  const [searchParams, setSearchParams] = useSearchParams();
  const initialFilters = useMemo(() => filtersFrom(searchParams, user), [searchParams, user]);
  const [filters, setFilters] = useState(initialFilters);
  const [data, setData] = useState(null);
  const [state, setState] = useState('loading');
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [detail, setDetail] = useState(null);
  const [detailState, setDetailState] = useState('idle');
  const [manualFieldId, setManualFieldId] = useState('');
  const [manualOpen, setManualOpen] = useState(false);
  const [manualSource, setManualSource] = useState(null);
  const queueControllerRef = useRef(null);
  const detailControllerRef = useRef(null);
  const canCreate = ['admin', 'manager'].includes(user?.role);

  useEffect(() => setFilters(initialFilters), [initialFilters]);

  useEffect(() => {
    queueControllerRef.current?.abort();
    const controller = new AbortController();
    queueControllerRef.current = controller;
    setState('loading'); setError('');
    getAnomalyInspectionQueue(query(filters), controller.signal).then(async (result) => {
      if (controller.signal.aborted) return;
      setData(result); setState(navigator.onLine ? 'ready' : 'offline');
      if (scope) await cacheAssignedInspections(scope, result).catch(() => {});
    }).catch(async (requestError) => {
      if (controller.signal.aborted) return;
      const cached = scope ? await getCachedAssignedInspections(scope).catch(() => null) : null;
      if (cached) { setData(cached); setState('offline'); setError('Показана последняя сохранённая очередь.'); }
      else { setState('error'); setError(loadMessage(requestError)); }
    });
    return () => controller.abort();
  }, [filters, reload, scope]);

  const refreshDetail = useCallback(async () => {
    if (!selectedInspectionId) return;
    detailControllerRef.current?.abort();
    const controller = new AbortController();
    detailControllerRef.current = controller;
    setDetailState('loading');
    try {
      const result = await getAnomalyInspection(selectedInspectionId, controller.signal);
      if (!controller.signal.aborted) { setDetail(result); setDetailState('ready'); setReload((value) => value + 1); }
    } catch (requestError) {
      if (!controller.signal.aborted) { setDetailState('error'); setError(loadMessage(requestError)); }
    }
  }, [selectedInspectionId]);

  useEffect(() => { if (selectedInspectionId) void refreshDetail(); else { setDetail(null); setDetailState('idle'); } return () => detailControllerRef.current?.abort(); }, [refreshDetail, selectedInspectionId]);

  function applyFilters(event) {
    event.preventDefault();
    const next = new URLSearchParams();
    if (filters.search) next.set('q', filters.search);
    if (filters.enterprise_id) next.set('enterprise', filters.enterprise_id);
    if (filters.field_id) next.set('field', filters.field_id);
    if (filters.assigned_to_id && user?.role !== 'agronomist') next.set('assignee', filters.assigned_to_id);
    if (filters.status) next.set('status', filters.status);
    if (filters.priority) next.set('priority', filters.priority);
    if (filters.source_kind) next.set('source', filters.source_kind);
    if (filters.due_state) next.set('due', filters.due_state);
    if (filters.sort !== 'priority') next.set('sort', filters.sort);
    setSearchParams(next, { replace: true });
  }

  if (selectedInspectionId) {
    if (detailState === 'loading' || detailState === 'idle') return <div className="h-full overflow-y-auto p-5"><div className="mx-auto h-72 max-w-6xl animate-pulse rounded-xl bg-slate-200" aria-label="Загрузка осмотра" /></div>;
    if (detailState === 'error' || !detail) return <div className="flex h-full items-center justify-center p-5"><div className="max-w-md text-center"><p role="alert" className="font-semibold text-red-900">{error}</p><button type="button" onClick={refreshDetail} className="mt-4 min-h-11 rounded-lg bg-green-700 px-4 font-bold text-white">Повторить</button></div></div>;
    return <AnomalyInspectionDetail detail={detail} onRefresh={refreshDetail} onBack={() => onNavigate('field-inspections')} />;
  }

  return (
    <div className="h-full overflow-y-auto bg-slate-50 px-4 py-5 sm:px-6" data-testid="inspection-queue">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-xl font-bold text-slate-950">Осмотры</h1><p className="mt-1 max-w-[72ch] text-sm text-slate-600">От аномалии к полевому подтверждению, действию и проверке результата.</p></div>{canCreate && <button type="button" onClick={() => setManualOpen((value) => !value)} aria-expanded={manualOpen} className="min-h-11 rounded-lg bg-green-700 px-4 font-bold text-white hover:bg-green-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-800 focus-visible:ring-offset-2">Новый ручной осмотр</button>}</div>
        {manualOpen && <form className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4 sm:flex-row sm:items-end" onSubmit={(event) => { event.preventDefault(); const id = Number(manualFieldId); if (Number.isSafeInteger(id) && id > 0) setManualSource({ kind: 'manual', field_id: id, reason: 'Проверить состояние поля по ручному наблюдению', priority: 'normal' }); }}><label className="flex-1 text-sm font-semibold text-slate-900">ID поля<input type="number" min="1" required value={manualFieldId} onChange={(event) => setManualFieldId(event.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 font-normal" /></label><button type="submit" className="min-h-11 rounded-lg border border-green-700 px-4 font-bold text-green-800">Продолжить</button></form>}
        <Kpis summary={data?.summary} />
        <form onSubmit={applyFilters} className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex flex-wrap gap-3"><label className="min-w-[14rem] flex-[2] text-xs font-semibold text-slate-700">Поиск<input value={filters.search} onChange={(event) => setFilters((current) => ({ ...current, search: event.target.value }))} placeholder="Поле или причина" className="mt-1 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm font-normal text-slate-950" /></label><label className="min-w-[10rem] flex-1 text-xs font-semibold text-slate-700">Предприятие<select value={filters.enterprise_id} onChange={(event) => setFilters((current) => ({ ...current, enterprise_id: event.target.value }))} disabled={user?.role !== 'admin'} className="mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm font-normal"><option value="">Все доступные</option>{enterprises.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="min-w-[8rem] flex-1 text-xs font-semibold text-slate-700">Поле<input type="number" min="1" value={filters.field_id} onChange={(event) => setFilters((current) => ({ ...current, field_id: event.target.value }))} className="mt-1 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm font-normal" /></label><FilterSelect label="Статус" value={filters.status} onChange={(value) => setFilters((current) => ({ ...current, status: value }))} options={[['', 'Все'], ...FILTER_VALUES.status.slice(1).map((value) => [value, STATUS[value]])]} /><FilterSelect label="Приоритет" value={filters.priority} onChange={(value) => setFilters((current) => ({ ...current, priority: value }))} options={[['', 'Все'], ...FILTER_VALUES.priority.slice(1).map((value) => [value, PRIORITY[value]])]} /><FilterSelect label="Источник" value={filters.source_kind} onChange={(value) => setFilters((current) => ({ ...current, source_kind: value }))} options={[['', 'Все'], ...FILTER_VALUES.source_kind.slice(1).map((value) => [value, SOURCE[value]])]} /><FilterSelect label="Срок" value={filters.due_state} onChange={(value) => setFilters((current) => ({ ...current, due_state: value }))} options={[['', 'Любой'], ['overdue', 'Просрочено'], ['due', 'В срок']]} /><button type="submit" className="min-h-11 self-end rounded-lg bg-slate-900 px-4 text-sm font-bold text-white">Применить</button></div></form>
        {state === 'offline' && <p role="status" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm font-medium text-amber-950">Офлайн-режим. {error || 'Показана сохранённая очередь.'}</p>}
        {state === 'error' && <div className="rounded-xl border border-red-300 bg-white p-6 text-center"><p role="alert" className="font-semibold text-red-900">{error}</p><button type="button" onClick={() => setReload((value) => value + 1)} className="mt-4 min-h-11 rounded-lg bg-green-700 px-4 font-bold text-white">Повторить</button></div>}
        {state === 'loading' && <div className="space-y-2" aria-label="Загрузка очереди">{[0, 1, 2].map((item) => <div key={item} className="h-24 animate-pulse rounded-xl bg-slate-200" />)}</div>}
        {state !== 'loading' && state !== 'error' && !data?.items?.length && <div className="rounded-xl border border-slate-200 bg-white px-5 py-12 text-center"><p className="font-bold text-slate-950">Осмотров по выбранным условиям нет</p><p className="mt-1 text-sm text-slate-600">Измените фильтры или создайте осмотр из Pixel NDVI, предупреждения либо поля.</p></div>}
        {data?.items?.length > 0 && <ol className="divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white">{data.items.map((item) => <li key={item.id}><button type="button" onClick={() => onNavigate('field-inspection-detail', item.id)} className="grid min-h-24 w-full gap-3 px-4 py-4 text-left hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-green-700 sm:grid-cols-[minmax(14rem,2fr)_repeat(3,minmax(7rem,1fr))]"><div className="min-w-0"><p className="truncate font-bold text-slate-950">#{item.id} · {item.field_name}</p><p className="mt-1 line-clamp-2 text-sm text-slate-700">{item.source.reason}</p></div><QueueMeta label="Статус" value={STATUS[item.status]} urgent={item.status === 'submitted'} /><QueueMeta label="Приоритет" value={PRIORITY[item.priority]} urgent={item.priority === 'urgent'} /><QueueMeta label={item.is_overdue ? 'Просрочено' : 'Срок'} value={new Date(item.due_at).toLocaleString('ru-RU')} urgent={item.is_overdue} /></button></li>)}</ol>}
      </div>
      {manualSource && <InspectionSourceDialog source={manualSource} onClose={() => setManualSource(null)} onCreated={(inspection) => { setManualSource(null); onNavigate('field-inspection-detail', inspection.id); }} />}
    </div>
  );
}

function FilterSelect({ label, value, onChange, options }) { return <label className="min-w-[9rem] flex-1 text-xs font-semibold text-slate-700">{label}<select value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm font-normal">{options.map(([optionValue, optionLabel]) => <option key={optionValue || 'all'} value={optionValue}>{optionLabel}</option>)}</select></label>; }
function QueueMeta({ label, value, urgent }) { return <div className="min-w-0"><span className="block text-xs font-semibold text-slate-600">{label}</span><span className={`mt-1 block break-words text-sm font-bold ${urgent ? 'text-red-800' : 'text-slate-900'}`}>{value}</span></div>; }

