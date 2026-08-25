import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import 'maplibre-gl/dist/maplibre-gl.css';
import maplibregl from '../maplibreRuntime';
import {
  createMonitoringInspection, getMonitoringCandidates, getMonitoringFreshness,
  getMonitoringStatus, transitionMonitoringCandidate,
} from '../api/monitoring';

const FRESHNESS = {
  FRESH: 'Свежие', AGING: 'Стареют', STALE: 'Устарели', NEVER_COLLECTED: 'Не собирались',
  CLOUD_BLOCKED: 'Облачность', PROVIDER_DEGRADED: 'Провайдер', QUALITY_BLOCKED: 'Качество',
};
const STATES = { NEW: 'На рассмотрении', CONFIRMED: 'Подтверждена', DISMISSED: 'Отклонена', INSPECTION_CREATED: 'Создан осмотр', RESOLVED: 'Закрыта', SUPERSEDED: 'Заменена' };
const TONES = { FRESH: 'bg-emerald-100 text-emerald-800', AGING: 'bg-amber-100 text-amber-900', STALE: 'bg-red-100 text-red-800', NEVER_COLLECTED: 'bg-slate-200 text-slate-800', CLOUD_BLOCKED: 'bg-sky-100 text-sky-900', PROVIDER_DEGRADED: 'bg-violet-100 text-violet-900', QUALITY_BLOCKED: 'bg-orange-100 text-orange-900' };
const date = value => value ? new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—';

function AnomalyMap({ items, selectedId, onSelect }) {
  const container = useRef(null);
  const mapRef = useRef(null);
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;
  const geojson = useMemo(() => ({ type: 'FeatureCollection', features: items.filter(item => item.geometry).map(item => ({
    type: 'Feature', id: item.id, geometry: item.geometry,
    properties: { id: item.id, selected: item.id === selectedId ? 1 : 0, severity: item.severity },
  })) }), [items, selectedId]);

  useEffect(() => {
    if (!container.current) return undefined;
    const map = new maplibregl.Map({ container: container.current, center: [64.4, 39.8], zoom: 7, style: {
      version: 8, sources: {},
      layers: [{ id: 'monitoring-background', type: 'background', paint: { 'background-color': '#eef3f0' } }],
    } });
    mapRef.current = map;
    const click = event => {
      const id = Number(event.features?.[0]?.properties?.id);
      if (Number.isSafeInteger(id)) selectRef.current(id);
    };
    const load = () => {
      map.addSource('monitoring-zones', { type: 'geojson', data: geojson });
      map.addLayer({ id: 'monitoring-zone-fill', type: 'fill', source: 'monitoring-zones', paint: {
        'fill-color': ['case', ['==', ['get', 'selected'], 1], '#065f46', ['match', ['get', 'severity'], 'EXTREME', '#b91c1c', 'HIGH', '#dc2626', 'MODERATE', '#d97706', '#64748b']],
        'fill-opacity': ['case', ['==', ['get', 'selected'], 1], 0.62, 0.36],
      } });
      map.addLayer({ id: 'monitoring-zone-outline', type: 'line', source: 'monitoring-zones', paint: { 'line-color': '#0f172a', 'line-width': ['case', ['==', ['get', 'selected'], 1], 3, 1.5] } });
      map.on('click', 'monitoring-zone-fill', click);
    };
    map.on('load', load);
    return () => {
      map.off('load', load);
      if (map.getLayer('monitoring-zone-fill')) map.off('click', 'monitoring-zone-fill', click);
      if (map.getLayer('monitoring-zone-outline')) map.removeLayer('monitoring-zone-outline');
      if (map.getLayer('monitoring-zone-fill')) map.removeLayer('monitoring-zone-fill');
      if (map.getSource('monitoring-zones')) map.removeSource('monitoring-zones');
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => { mapRef.current?.getSource('monitoring-zones')?.setData(geojson); }, [geojson]);
  return <div ref={container} className="h-64 min-h-64 w-full lg:h-full" aria-label="Карта зон спутниковых аномалий" />;
}

function Metric({ label, value, danger }) {
  return <div className="min-w-0 border-b border-agro-border pb-3 lg:border-b-0 lg:border-r lg:pb-0 lg:pr-4 last:border-0"><dt className="text-sm text-agro-muted">{label}</dt><dd className={`mt-1 text-xl font-semibold tabular-nums ${danger ? 'text-red-700' : 'text-agro-text'}`}>{value ?? '—'}</dd></div>;
}

export default function MonitoringPage({ enterprises = [], onNavigate }) {
  const [data, setData] = useState({ status: null, freshness: [], candidates: [] });
  const [filters, setFilters] = useState({ enterprise_id: '', freshness: '', state: '' });
  const [selectedId, setSelectedId] = useState(null);
  const [reason, setReason] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [online, setOnline] = useState(() => navigator.onLine);
  const selected = data.candidates.find(item => item.id === selectedId) || null;

  const load = useCallback(async signal => {
    setLoading(true); setError('');
    const scope = filters.enterprise_id ? { enterprise_id: Number(filters.enterprise_id) } : {};
    const [status, freshness, candidates] = await Promise.all([
      getMonitoringStatus(signal),
      getMonitoringFreshness({ ...scope, ...(filters.freshness ? { status: filters.freshness } : {}) }, signal),
      getMonitoringCandidates({ ...scope, ...(filters.state ? { state: filters.state } : {}) }, signal),
    ]);
    setData({ status, freshness: freshness.items || [], candidates: candidates.items || [] });
    setSelectedId(current => candidates.items?.some(item => item.id === current) ? current : candidates.items?.[0]?.id || null);
    setLoading(false);
  }, [filters]);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal).catch(value => {
      if (!['CanceledError', 'AbortError'].includes(value?.name)) {
        setError(value?.response?.status === 403 ? 'Раздел доступен администратору.' : 'Не удалось загрузить состояние мониторинга.');
        setLoading(false);
      }
    });
    return () => controller.abort();
  }, [load]);
  useEffect(() => {
    const yes = () => setOnline(true); const no = () => setOnline(false);
    window.addEventListener('online', yes); window.addEventListener('offline', no);
    return () => { window.removeEventListener('online', yes); window.removeEventListener('offline', no); };
  }, []);

  const mutate = async action => {
    if (!selected || reason.trim().length < 3) return;
    setSaving(true); setError('');
    try {
      const payload = { reason: reason.trim(), expected_version: selected.version };
      const result = action === 'inspection'
        ? await createMonitoringInspection(selected.id, payload)
        : await transitionMonitoringCandidate(selected.id, { ...payload, action });
      setData(current => ({ ...current, candidates: current.candidates.map(item => item.id === selected.id ? { ...item, ...result } : item) }));
      setReason('');
    } catch (value) {
      setError(value?.response?.status === 409 ? 'Запись уже изменена. Обновите данные и повторите решение.' : 'Не удалось сохранить решение.');
    } finally { setSaving(false); }
  };

  const latest = data.status?.latest_run;
  const counts = data.status?.freshness || {};
  return <section className="h-[calc(100vh-4rem)] overflow-y-auto bg-slate-50 p-3 sm:p-5" aria-busy={loading}>
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4">
      {!online && <div role="status" className="rounded-lg bg-amber-100 px-4 py-3 text-sm font-medium text-amber-950">Нет сети. Показано последнее загруженное состояние; решения временно недоступны.</div>}
      {error && <div role="alert" className="flex items-center justify-between gap-3 rounded-lg bg-red-100 px-4 py-3 text-sm text-red-900"><span>{error}</span><button type="button" className="btn-secondary min-h-11" onClick={() => load(new AbortController().signal)}>Повторить</button></div>}
      <div className="card">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">Автономный спутниковый цикл</h2><p className="mt-1 max-w-3xl text-sm text-agro-muted">Состояние провайдера отображается отдельно и не влияет на готовность веб-приложения.</p></div><span className={`badge ${latest?.status === 'succeeded' ? 'bg-emerald-100 text-emerald-800' : latest?.status === 'running' ? 'bg-blue-100 text-blue-800' : 'bg-amber-100 text-amber-900'}`}>{latest?.status || (loading ? 'Загрузка' : 'Нет запусков')}</span></div>
        <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5"><Metric label="Последний heartbeat" value={date(latest?.heartbeat_at)} /><Metric label="Принято" value={latest?.counters?.inserted_count ?? 0} /><Metric label="Без новой сцены" value={latest?.counters?.skipped_existing_count ?? 0} /><Metric label="Отклонено качеством" value={latest?.counters?.quality_blocked_count ?? 0} /><Metric label="Ошибки провайдера" value={latest?.counters?.failure_count ?? 0} danger={latest?.counters?.failure_count > 0} /></dl>
      </div>
      <div className="card">
        <h2 className="text-base font-semibold">Свежесть по полям и индексам</h2>
        <div className="mt-3 flex flex-wrap gap-2" role="list" aria-label="Распределение свежести">{Object.entries(FRESHNESS).map(([key, label]) => <span role="listitem" key={key} className={`badge ${TONES[key]}`}>{label}: {counts[key] || 0}</span>)}</div>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <label className="text-sm font-medium">Предприятие<select className="input mt-1 min-h-11 w-full" value={filters.enterprise_id} onChange={event => setFilters(value => ({ ...value, enterprise_id: event.target.value }))}><option value="">Все предприятия</option>{enterprises.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label className="text-sm font-medium">Свежесть<select className="input mt-1 min-h-11 w-full" value={filters.freshness} onChange={event => setFilters(value => ({ ...value, freshness: event.target.value }))}><option value="">Все состояния</option>{Object.entries(FRESHNESS).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          <label className="text-sm font-medium">Состояние аномалии<select className="input mt-1 min-h-11 w-full" value={filters.state} onChange={event => setFilters(value => ({ ...value, state: event.target.value }))}><option value="">Все состояния</option>{Object.entries(STATES).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        </div>
        {!loading && data.freshness.length === 0 && <p className="mt-4 text-sm text-agro-muted">Нет полей, соответствующих фильтрам. Свежесть появится после первого квалифицированного цикла.</p>}
      </div>
      <div className="grid min-h-[520px] overflow-hidden rounded-lg border border-agro-border bg-white lg:grid-cols-[minmax(360px,0.9fr)_minmax(420px,1.1fr)]">
        <div className="min-w-0 border-b border-agro-border lg:border-b-0 lg:border-r"><div className="border-b border-agro-border p-4"><h2 className="font-semibold">Очередь аномалий</h2><p className="mt-1 text-sm text-agro-muted">Ноль валидных аномалий является штатным результатом.</p></div><div className="max-h-[520px] overflow-y-auto" role={data.candidates.length ? 'list' : undefined} aria-label={data.candidates.length ? 'Кандидаты аномалий' : undefined}>{data.candidates.map(item => <button type="button" role="listitem" key={item.id} onClick={() => setSelectedId(item.id)} className={`block min-h-11 w-full border-b border-agro-border p-4 text-left focus:outline-none focus:ring-2 focus:ring-inset focus:ring-agro-accent ${selectedId === item.id ? 'bg-emerald-50' : 'hover:bg-agro-hover'}`}><span className="flex items-center justify-between gap-2"><strong className="truncate text-sm">{item.field_name}</strong><span className="badge bg-slate-100 text-slate-800">{STATES[item.state] || item.state}</span></span><span className="mt-2 block text-sm text-agro-muted">{item.index_code.toUpperCase()} · уверенность {(item.confidence * 100).toFixed(0)}% · {item.affected_area_ha.toFixed(2)} га</span></button>)}{!loading && data.candidates.length === 0 && <p className="p-5 text-sm text-agro-muted">Подтверждённых геометрических зон пока нет. Система не создаёт искусственные сигналы.</p>}</div></div>
        <div className="grid min-w-0 grid-rows-[auto_1fr]"><AnomalyMap items={data.candidates} selectedId={selectedId} onSelect={setSelectedId} /><div className="min-w-0 border-t border-agro-border p-4">{selected ? <div><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold">{selected.field_name} · {selected.index_code.toUpperCase()}</h3><p className="mt-1 text-sm text-agro-muted">Сцена {date(selected.acquired_at)} · правило {selected.rule_version}</p></div>{selected.inspection_id && <button type="button" className="btn-secondary min-h-11" onClick={() => onNavigate('field-inspection-detail', selected.inspection_id)}>Осмотр #{selected.inspection_id}</button>}</div><p className="mt-3 max-w-3xl text-sm leading-6">{selected.explanation}</p><dl className="mt-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4"><div><dt className="text-agro-muted">Отклонение</dt><dd>{selected.robust_deviation.toFixed(2)} MAD</dd></div><div><dt className="text-agro-muted">Повторяемость</dt><dd>{selected.persistence_scenes} сцен</dd></div><div><dt className="text-agro-muted">Индексы</dt><dd>{selected.multi_index_agreement + 1}</dd></div><div><dt className="text-agro-muted">Качество</dt><dd>{(selected.data_quality * 100).toFixed(0)}%</dd></div></dl>{['NEW','CONFIRMED'].includes(selected.state) && <div className="mt-4 border-t border-agro-border pt-4"><label htmlFor="monitoring-review-reason" className="text-sm font-medium">Основание решения</label><textarea id="monitoring-review-reason" className="input mt-1 min-h-24 w-full" value={reason} maxLength={2000} onChange={event => setReason(event.target.value)} aria-describedby="monitoring-review-help"/><p id="monitoring-review-help" className="mt-1 text-xs text-agro-muted">Минимум 3 символа. Решение сохраняется в журнале переходов.</p><div className="mt-3 flex flex-wrap gap-2"><button type="button" disabled={saving || !online || reason.trim().length < 3} className="btn-primary min-h-11" onClick={() => mutate('confirm')}>Подтвердить</button><button type="button" disabled={saving || !online || reason.trim().length < 3} className="btn-secondary min-h-11" onClick={() => mutate('dismiss')}>Отклонить</button><button type="button" disabled={saving || !online || reason.trim().length < 3} className="btn-secondary min-h-11" onClick={() => mutate('inspection')}>Создать осмотр</button></div></div>}</div> : <p className="text-sm text-agro-muted">Выберите зону в списке или на карте, чтобы увидеть доказательства.</p>}</div></div>
      </div>
    </div>
  </section>;
}
