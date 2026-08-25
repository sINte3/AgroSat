import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';

import { useAuth } from '../../context/AuthContext';
import {
  createInspectionAction,
  deleteInspectionPhoto,
  downloadInspectionPhoto,
  reviewAnomalyInspection,
  saveAnomalyFinding,
  startAnomalyInspection,
  submitAnomalyInspection,
  transitionInspectionAction,
  uploadInspectionPhoto,
  verifyInspectionAction,
} from '../../api/anomalyInspections';
import {
  createAnomalyWorkflowDraft,
  discardOfflineDraft,
  getOfflineDraft,
  markAnomalyDraftConflict,
  markAnomalyDraftPending,
  offlineScope,
  saveOfflineDraft,
} from '../../offline/offlineScoutingStore';

const STATUS = {
  new: 'Новый', assigned: 'Назначен', in_progress: 'В работе', submitted: 'На проверке',
  confirmed: 'Подтверждён', rejected: 'Отклонён', cancelled: 'Отменён',
};
const PRIORITY = { low: 'Низкий', normal: 'Обычный', high: 'Высокий', urgent: 'Срочный' };
const CAUSES = [
  ['water_stress', 'Водный стресс'], ['irrigation_failure', 'Сбой орошения'], ['pest', 'Вредители'],
  ['disease', 'Болезнь'], ['nutrient_deficiency', 'Дефицит питания'], ['weed_pressure', 'Сорняки'],
  ['mechanical_damage', 'Механическое повреждение'], ['soil_salinity', 'Почва / засоление'],
  ['weather_damage', 'Погодное повреждение'], ['false_positive', 'Ложное срабатывание'], ['other', 'Другое'],
];
const SEVERITIES = [
  ['none', 'Нет'], ['low', 'Низкая'], ['moderate', 'Средняя'], ['high', 'Высокая'], ['critical', 'Критическая'],
];
const SYNC_COPY = {
  local_draft: 'Сохранено локально', pending_sync: 'Ожидает синхронизации',
  synchronized: 'Синхронизировано', conflict: 'Конфликт: серверная версия новее',
};

function localInput(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) return '';
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function initialFinding(detail) {
  const row = detail?.finding;
  const coordinates = row?.gps_point?.coordinates;
  return {
    inspected_at: localInput(row?.actual_inspected_at || new Date()),
    longitude: Array.isArray(coordinates) ? String(coordinates[0]) : '',
    latitude: Array.isArray(coordinates) ? String(coordinates[1]) : '',
    gps_accuracy_m: row?.gps_accuracy_m == null ? '' : String(row.gps_accuracy_m),
    cause: row?.cause_code || 'water_stress',
    other_explanation: row?.cause_details || '',
    severity: row?.severity || 'moderate',
    affected_mode: row?.affected_area_pct == null ? 'ha' : 'pct',
    affected_value: String(row?.affected_area_pct ?? row?.affected_area_ha ?? ''),
    observations: row?.observations || row?.evidence_note || '',
    recommended_action: row?.recommended_action || '',
  };
}

function findingPayload(form, version, syncState = 'server') {
  const hasGps = form.longitude !== '' && form.latitude !== '';
  return {
    expected_version: version,
    inspected_at: new Date(form.inspected_at).toISOString(),
    gps_point: hasGps ? { longitude: Number(form.longitude), latitude: Number(form.latitude) } : null,
    gps_accuracy_m: form.gps_accuracy_m === '' ? null : Number(form.gps_accuracy_m),
    cause: form.cause,
    other_explanation: form.cause === 'other' ? form.other_explanation : null,
    severity: form.severity,
    affected_area_ha: form.affected_mode === 'ha' ? Number(form.affected_value) : null,
    affected_area_pct: form.affected_mode === 'pct' ? Number(form.affected_value) : null,
    observations: form.observations,
    recommended_action: form.recommended_action,
    sync_state: syncState,
  };
}

function apiError(error) {
  const status = Number(error?.response?.status || 0);
  if (status === 409) return 'Данные изменились на сервере. Обновите осмотр перед продолжением.';
  if (status === 422) return 'Проверьте обязательные поля и допустимые значения.';
  if (status === 403) return 'У вашей роли нет права выполнить это действие.';
  return 'Операция не выполнена. Повторите попытку.';
}

function geometryCoordinates(geometry) {
  if (!geometry?.coordinates) return [];
  const values = [];
  const visit = (item) => {
    if (Array.isArray(item) && item.length >= 2 && item.every(Number.isFinite)) values.push(item);
    else if (Array.isArray(item)) item.forEach(visit);
  };
  visit(geometry.coordinates);
  return values;
}

function InspectionSourceMap({ detail }) {
  const containerRef = useRef(null);
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !detail.field_geometry) return undefined;
    const field = { type: 'Feature', properties: {}, geometry: detail.field_geometry };
    const selectedGeometry = detail.source?.zone || detail.source?.point;
    const selected = selectedGeometry ? { type: 'Feature', properties: {}, geometry: selectedGeometry } : null;
    const points = geometryCoordinates(detail.field_geometry);
    if (!points.length) return undefined;
    const bounds = points.reduce((value, coordinate) => value.extend(coordinate), new maplibregl.LngLatBounds(points[0], points[0]));
    const map = new maplibregl.Map({
      container,
      style: { version: 8, sources: {}, layers: [{ id: 'inspection-map-background', type: 'background', paint: { 'background-color': '#f1f5f9' } }] },
      attributionControl: false,
      interactive: true,
      preserveDrawingBuffer: false,
    });
    const onLoad = () => {
      map.addSource('inspection-field', { type: 'geojson', data: field });
      map.addLayer({ id: 'inspection-field-fill', type: 'fill', source: 'inspection-field', paint: { 'fill-color': '#16a34a', 'fill-opacity': 0.14 } });
      map.addLayer({ id: 'inspection-field-line', type: 'line', source: 'inspection-field', paint: { 'line-color': '#166534', 'line-width': 2 } });
      if (selected) {
        map.addSource('inspection-source', { type: 'geojson', data: selected });
        if (selected.geometry.type === 'Point') {
          map.addLayer({ id: 'inspection-source-point', type: 'circle', source: 'inspection-source', paint: { 'circle-radius': 8, 'circle-color': '#b91c1c', 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 3 } });
        } else {
          map.addLayer({ id: 'inspection-source-zone', type: 'fill', source: 'inspection-source', paint: { 'fill-color': '#b91c1c', 'fill-opacity': 0.32, 'fill-outline-color': '#7f1d1d' } });
        }
      }
      map.fitBounds(bounds, { padding: 28, duration: 0, maxZoom: 16 });
    };
    map.on('load', onLoad);
    const observer = new ResizeObserver(() => map.resize());
    observer.observe(container);
    return () => {
      observer.disconnect();
      map.off('load', onLoad);
      for (const layer of ['inspection-source-point', 'inspection-source-zone', 'inspection-field-line', 'inspection-field-fill']) {
        if (map.getLayer(layer)) map.removeLayer(layer);
      }
      for (const source of ['inspection-source', 'inspection-field']) {
        if (map.getSource(source)) map.removeSource(source);
      }
      map.remove();
    };
  }, [detail.field_geometry, detail.source?.point, detail.source?.zone]);
  return <div ref={containerRef} role="img" aria-label="Карта источника аномалии и границ поля" className="mt-4 h-56 w-full overflow-hidden rounded-xl border border-slate-200 bg-slate-100 sm:h-64" />;
}

function SourceContext({ detail }) {
  const source = detail.source || {};
  const point = source.point?.coordinates;
  return (
    <section aria-labelledby="source-heading" className="border-b border-slate-200 pb-5">
      <h2 id="source-heading" className="text-base font-bold text-slate-950">Почему создан осмотр</h2>
      <div className="mt-3 grid gap-x-5 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
        <div><span className="block text-slate-600">Поле</span><strong>{detail.field_name}</strong></div>
        <div><span className="block text-slate-600">Источник</span><strong>{source.kind === 'pixel_ndvi' ? 'Пиксельный NDVI' : source.kind === 'alert' ? 'Предупреждение' : 'Ручной'}</strong></div>
        {source.acquired_at && <div><span className="block text-slate-600">Снимок</span><strong className="font-mono">{new Date(source.acquired_at).toLocaleString('ru-RU')}</strong></div>}
        {Number.isFinite(source.sampled_value) && <div><span className="block text-slate-600">{String(source.index_name).toUpperCase()}</span><strong className="font-mono">{source.sampled_value.toFixed(3)}</strong></div>}
        {Array.isArray(point) && <div><span className="block text-slate-600">Точка на карте</span><strong className="font-mono">{point[0].toFixed(6)}, {point[1].toFixed(6)}</strong></div>}
        {source.zone && <div><span className="block text-slate-600">Зона</span><strong>В границах поля</strong></div>}
      </div>
      <p className="mt-3 max-w-[72ch] text-sm leading-6 text-slate-800">{source.reason}</p>
      <InspectionSourceMap detail={detail} />
    </section>
  );
}

function FindingWorkspace({ detail, onRefresh }) {
  const { user } = useAuth();
  const scope = offlineScope(user);
  const [form, setForm] = useState(() => initialFinding(detail));
  const [state, setState] = useState('idle');
  const [syncState, setSyncState] = useState(detail.finding ? 'synchronized' : null);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState(null);
  const draftRef = useRef(null);
  const mountedRef = useRef(true);
  const canEdit = user?.role === 'agronomist' && detail.status === 'in_progress';

  useEffect(() => {
    setForm(initialFinding(detail));
  }, [detail.id, detail.finding]);

  useEffect(() => {
    mountedRef.current = true;
    if (!scope) return () => { mountedRef.current = false; };
    getOfflineDraft(scope, detail.id).then((saved) => {
      if (!mountedRef.current || saved?.schemaVersion !== 2) return;
      setDraft(saved); draftRef.current = saved;
      setForm(saved.finding); setSyncState(saved.status || 'local_draft');
    }).catch(() => {});
    return () => { mountedRef.current = false; };
  }, [detail.id, scope]);

  const syncDraft = useCallback(async (candidate = draftRef.current) => {
    if (!candidate || !navigator.onLine || !scope) return;
    setState('saving'); setError('');
    try {
      await saveAnomalyFinding(detail.id, findingPayload(candidate.finding, candidate.baseVersion, 'pending_sync'));
      await discardOfflineDraft(scope, detail.id);
      draftRef.current = null; setDraft(null); setSyncState('synchronized');
      await onRefresh();
    } catch (requestError) {
      if (Number(requestError?.response?.status) === 409) {
        const conflicted = await markAnomalyDraftConflict(candidate);
        draftRef.current = conflicted; setDraft(conflicted); setSyncState('conflict');
      } else {
        setError(apiError(requestError));
      }
    } finally { setState('idle'); }
  }, [detail.id, onRefresh, scope]);

  useEffect(() => {
    const reconnect = () => {
      if (draftRef.current?.status === 'pending_sync') void syncDraft(draftRef.current);
    };
    window.addEventListener('online', reconnect);
    return () => window.removeEventListener('online', reconnect);
  }, [syncDraft]);

  function update(name, value) { setForm((current) => ({ ...current, [name]: value })); }

  async function save(event) {
    event.preventDefault(); setError('');
    if (!navigator.onLine) {
      const local = createAnomalyWorkflowDraft({ scope, inspectionId: detail.id, baseVersion: detail.version, finding: form });
      const queued = await markAnomalyDraftPending(await saveOfflineDraft(local));
      draftRef.current = queued; setDraft(queued); setSyncState('pending_sync');
      return;
    }
    setState('saving');
    try {
      await saveAnomalyFinding(detail.id, findingPayload(form, detail.version));
      if (scope) await discardOfflineDraft(scope, detail.id);
      draftRef.current = null; setDraft(null); setSyncState('synchronized');
      await onRefresh();
    } catch (requestError) { setError(apiError(requestError)); }
    finally { setState('idle'); }
  }

  async function saveLocal() {
    try {
      const local = createAnomalyWorkflowDraft({ scope, inspectionId: detail.id, baseVersion: detail.version, finding: form });
      const saved = await saveOfflineDraft(local);
      draftRef.current = saved; setDraft(saved); setSyncState('local_draft'); setError('');
    } catch { setError('Локальное сохранение недоступно в этом браузере.'); }
  }

  function captureGps() {
    if (!navigator.geolocation) { setError('Геолокация недоступна в этом браузере.'); return; }
    setState('gps');
    navigator.geolocation.getCurrentPosition((position) => {
      update('longitude', String(position.coords.longitude));
      update('latitude', String(position.coords.latitude));
      update('gps_accuracy_m', String(Math.round(position.coords.accuracy)));
      setState('idle');
    }, () => { setError('Не удалось получить геопозицию. Введите координаты вручную.'); setState('idle'); }, {
      enableHighAccuracy: true, timeout: 12000, maximumAge: 30000,
    });
  }

  return (
    <section aria-labelledby="finding-heading" className="border-b border-slate-200 py-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 id="finding-heading" className="text-base font-bold text-slate-950">Результаты выезда</h2><p className="mt-1 text-sm text-slate-600">Структурированные наблюдения агронома.</p></div>
        {syncState && <p role="status" aria-live="polite" className={`rounded-full px-3 py-1 text-xs font-bold ${syncState === 'conflict' ? 'bg-red-100 text-red-900' : syncState === 'pending_sync' ? 'bg-amber-100 text-amber-950' : 'bg-emerald-100 text-emerald-900'}`}>{SYNC_COPY[syncState]}</p>}
      </div>
      <form onSubmit={save} className="mt-4 space-y-4">
        <fieldset disabled={!canEdit || state === 'saving'} className="space-y-4 disabled:opacity-70">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <label className="text-sm font-semibold text-slate-900">Время осмотра<input type="datetime-local" required value={form.inspected_at} onChange={(event) => update('inspected_at', event.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 font-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-700" /></label>
            <label className="text-sm font-semibold text-slate-900">Причина<select value={form.cause} onChange={(event) => update('cause', event.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 font-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-700">{CAUSES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label className="text-sm font-semibold text-slate-900">Выраженность<select value={form.severity} onChange={(event) => update('severity', event.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 font-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-700">{SEVERITIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          </div>
          {form.cause === 'other' && <label className="block text-sm font-semibold text-slate-900">Уточните причину<input required minLength={3} maxLength={2000} value={form.other_explanation} onChange={(event) => update('other_explanation', event.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 font-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-700" /></label>}
          <div className="grid gap-4 sm:grid-cols-[1fr_1fr_auto]">
            <label className="text-sm font-semibold text-slate-900">Долгота<input type="number" min="-180" max="180" step="0.000001" value={form.longitude} onChange={(event) => update('longitude', event.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 font-normal" /></label>
            <label className="text-sm font-semibold text-slate-900">Широта<input type="number" min="-90" max="90" step="0.000001" value={form.latitude} onChange={(event) => update('latitude', event.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 font-normal" /></label>
            <button type="button" onClick={captureGps} className="min-h-11 self-end rounded-lg border border-slate-400 px-4 text-sm font-bold text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-700">{state === 'gps' ? 'Определяем…' : 'Моя геопозиция'}</button>
          </div>
          <div className="grid gap-4 sm:grid-cols-[10rem_1fr]">
            <label className="text-sm font-semibold text-slate-900">Единица площади<select value={form.affected_mode} onChange={(event) => update('affected_mode', event.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 font-normal"><option value="ha">Гектары</option><option value="pct">Проценты</option></select></label>
            <label className="text-sm font-semibold text-slate-900">Затронутая площадь<input type="number" required min="0" max={form.affected_mode === 'pct' ? '100' : '1000000'} step="0.01" value={form.affected_value} onChange={(event) => update('affected_value', event.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 font-normal" /></label>
          </div>
          <label className="block text-sm font-semibold text-slate-900">Наблюдения<textarea required minLength={3} maxLength={4000} rows={4} value={form.observations} onChange={(event) => update('observations', event.target.value)} className="mt-1.5 w-full rounded-lg border border-slate-300 px-3 py-2.5 font-normal" /></label>
          <label className="block text-sm font-semibold text-slate-900">Рекомендованное действие<textarea required minLength={3} maxLength={4000} rows={3} value={form.recommended_action} onChange={(event) => update('recommended_action', event.target.value)} className="mt-1.5 w-full rounded-lg border border-slate-300 px-3 py-2.5 font-normal" /></label>
        </fieldset>
        {error && <p role="alert" className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm font-medium text-red-900">{error}</p>}
        {canEdit && <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end"><button type="button" onClick={saveLocal} className="min-h-11 rounded-lg border border-slate-400 px-4 font-bold text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-700">Сохранить локально</button><button type="submit" disabled={state === 'saving'} className="min-h-11 rounded-lg bg-green-700 px-5 font-bold text-white hover:bg-green-800 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-800 focus-visible:ring-offset-2">{state === 'saving' ? 'Сохраняем…' : 'Сохранить на сервере'}</button></div>}
      </form>
    </section>
  );
}

function PhotoWorkspace({ detail, onRefresh }) {
  const { user } = useAuth();
  const [state, setState] = useState('idle');
  const [error, setError] = useState('');
  const [preview, setPreview] = useState(null);
  const previewRequestRef = useRef(null);
  const canUpload = user?.role === 'agronomist' && detail.status === 'in_progress';
  useEffect(() => () => {
    previewRequestRef.current?.abort();
    if (preview?.url) URL.revokeObjectURL(preview.url);
  }, [preview]);
  async function openPreview(photo) {
    previewRequestRef.current?.abort();
    if (preview?.url) URL.revokeObjectURL(preview.url);
    const controller = new AbortController();
    previewRequestRef.current = controller;
    setState('saving'); setError('');
    try {
      const blob = await downloadInspectionPhoto(detail.id, photo.id, controller.signal);
      setPreview({ photo, url: URL.createObjectURL(blob) });
    } catch (requestError) {
      if (requestError?.name !== 'CanceledError') setError(apiError(requestError));
    } finally { setState('idle'); }
  }
  function closePreview() {
    previewRequestRef.current?.abort(); previewRequestRef.current = null;
    if (preview?.url) URL.revokeObjectURL(preview.url);
    setPreview(null);
  }
  async function choose(event) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (!navigator.onLine) { setError('Фото не хранится офлайн. Подключитесь и выберите файл повторно.'); return; }
    setState('saving'); setError('');
    try { await uploadInspectionPhoto(detail.id, detail.version, file, new Date().toISOString()); await onRefresh(); }
    catch (requestError) { setError(apiError(requestError)); }
    finally { setState('idle'); }
  }
  return (
    <section aria-labelledby="photo-heading" className="border-b border-slate-200 py-5">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 id="photo-heading" className="text-base font-bold text-slate-950">Фотофиксация</h2><p className="mt-1 text-sm text-slate-600">JPEG, PNG или WebP, до 8 МБ. Приватные файлы доступны только участникам осмотра.</p></div>{canUpload && <label className="inline-flex min-h-11 cursor-pointer items-center rounded-lg bg-slate-900 px-4 text-sm font-bold text-white focus-within:ring-2 focus-within:ring-green-700 focus-within:ring-offset-2"><input type="file" accept="image/jpeg,image/png,image/webp" onChange={choose} className="sr-only" disabled={state === 'saving'} />{state === 'saving' ? 'Загружаем…' : 'Добавить фото'}</label>}</div>
      {error && <p role="alert" className="mt-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950">{error}</p>}
      {detail.photos?.length ? <ul className="mt-3 divide-y divide-slate-200 rounded-lg border border-slate-200">{detail.photos.map((photo) => <li key={photo.id} className="flex min-w-0 flex-wrap items-center justify-between gap-3 px-3 py-2.5 text-sm"><span className="min-w-0 flex-1 truncate font-medium text-slate-900">{photo.original_filename}</span><span className="shrink-0 font-mono text-xs text-slate-600">{Math.ceil(photo.byte_size / 1024)} КБ · SHA {photo.sha256.slice(0, 8)}</span><span className="flex gap-2"><button type="button" onClick={() => openPreview(photo)} className="min-h-11 rounded-lg border border-slate-300 px-3 font-bold">Просмотреть</button>{canUpload && <button type="button" onClick={async () => { setState('saving'); setError(''); try { await deleteInspectionPhoto(detail.id, photo.id, detail.version); await onRefresh(); } catch (requestError) { setError(apiError(requestError)); } finally { setState('idle'); } }} className="min-h-11 rounded-lg border border-red-300 px-3 font-bold text-red-900">Удалить</button>}</span></li>)}</ul> : <p className="mt-3 text-sm text-slate-600">Фото пока нет.</p>}
      {preview && <div role="dialog" aria-modal="true" aria-label={`Просмотр фото ${preview.photo.original_filename}`} className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/70 p-4" onMouseDown={(event) => { if (event.target === event.currentTarget) closePreview(); }}><div className="w-full max-w-3xl rounded-xl bg-white p-4 shadow-xl"><div className="flex items-center justify-between gap-3"><p className="truncate font-bold text-slate-950">{preview.photo.original_filename}</p><button type="button" autoFocus onClick={closePreview} className="min-h-11 rounded-lg border border-slate-300 px-4 font-bold">Закрыть</button></div><img src={preview.url} alt={`Фотофиксация: ${preview.photo.original_filename}`} className="mt-3 max-h-[70dvh] w-full rounded-lg object-contain" /></div></div>}
    </section>
  );
}

function ReviewAndActions({ detail, onRefresh }) {
  const { user } = useAuth();
  const isReviewer = ['admin', 'manager'].includes(user?.role);
  const [error, setError] = useState('');
  const [state, setState] = useState('idle');
  const [reviewReason, setReviewReason] = useState('');
  const [actionForm, setActionForm] = useState({ action_type: 'field_treatment', owner_id: String(detail.assigned_to_id || ''), instructions: '', due_at: localInput(new Date(Date.now() + 2 * 86400000)) });

  async function run(operation) {
    setState('saving'); setError('');
    try { await operation(); await onRefresh(); }
    catch (requestError) { setError(apiError(requestError)); }
    finally { setState('idle'); }
  }

  return (
    <section aria-labelledby="closure-heading" className="border-b border-slate-200 py-5">
      <h2 id="closure-heading" className="text-base font-bold text-slate-950">Решение и действия</h2>
      {isReviewer && detail.status === 'submitted' && <div className="mt-4 rounded-xl bg-slate-50 p-4"><label className="block text-sm font-semibold text-slate-900">Причина решения (обязательна для отклонения)<textarea rows={2} value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} className="mt-1.5 w-full rounded-lg border border-slate-300 px-3 py-2 font-normal" /></label><div className="mt-3 flex flex-col gap-2 sm:flex-row sm:justify-end"><button type="button" disabled={state === 'saving' || reviewReason.trim().length < 5} onClick={() => run(() => reviewAnomalyInspection(detail.id, { expected_version: detail.version, decision: 'rejected', reason: reviewReason }))} className="min-h-11 rounded-lg border border-red-400 px-4 font-bold text-red-900 disabled:opacity-50">Отклонить аномалию</button><button type="button" disabled={state === 'saving'} onClick={() => run(() => reviewAnomalyInspection(detail.id, { expected_version: detail.version, decision: 'confirmed', reason: reviewReason || null }))} className="min-h-11 rounded-lg bg-green-700 px-4 font-bold text-white disabled:opacity-50">Подтвердить аномалию</button></div></div>}
      {isReviewer && detail.status === 'confirmed' && !detail.actions?.length && <form className="mt-4 grid gap-4 rounded-xl bg-slate-50 p-4 sm:grid-cols-2" onSubmit={(event) => { event.preventDefault(); void run(() => createInspectionAction(detail.id, { expected_inspection_version: detail.version, action_type: actionForm.action_type, owner_id: Number(actionForm.owner_id), instructions: actionForm.instructions, planned_start_at: null, due_at: new Date(actionForm.due_at).toISOString() })); }}><label className="text-sm font-semibold">Тип действия<input value={actionForm.action_type} pattern="[a-z][a-z0-9_]{1,49}" onChange={(event) => setActionForm((current) => ({ ...current, action_type: event.target.value }))} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 font-normal" /></label><label className="text-sm font-semibold">Ответственный<input type="number" min="1" required value={actionForm.owner_id} onChange={(event) => setActionForm((current) => ({ ...current, owner_id: event.target.value }))} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 font-normal" /></label><label className="text-sm font-semibold sm:col-span-2">Инструкции<textarea required minLength={5} rows={3} value={actionForm.instructions} onChange={(event) => setActionForm((current) => ({ ...current, instructions: event.target.value }))} className="mt-1.5 w-full rounded-lg border border-slate-300 px-3 py-2 font-normal" /></label><label className="text-sm font-semibold">Срок<input type="datetime-local" required value={actionForm.due_at} onChange={(event) => setActionForm((current) => ({ ...current, due_at: event.target.value }))} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 font-normal" /></label><button type="submit" disabled={state === 'saving'} className="min-h-11 self-end rounded-lg bg-green-700 px-4 font-bold text-white disabled:opacity-50">Создать план действия</button></form>}
      {detail.actions?.length > 0 && <ul className="mt-4 space-y-3">{detail.actions.map((action) => <li key={action.id} className="rounded-xl border border-slate-200 p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><p className="font-bold text-slate-950">{action.action_type}</p><p className="mt-1 text-sm leading-6 text-slate-700">{action.description}</p></div><span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold text-slate-800">{action.status}</span></div><div className="mt-3 flex flex-wrap gap-2">{action.status === 'planned' && <button type="button" onClick={() => run(() => transitionInspectionAction(action.id, { expected_version: action.version, transition: 'start', note: null }))} className="min-h-11 rounded-lg border border-slate-400 px-4 font-bold">Начать</button>}{action.status === 'in_progress' && <button type="button" onClick={() => run(() => transitionInspectionAction(action.id, { expected_version: action.version, transition: 'complete', note: 'Работы выполнены по плану' }))} className="min-h-11 rounded-lg bg-slate-900 px-4 font-bold text-white">Завершить</button>}{isReviewer && action.status === 'completed' && <><button type="button" onClick={() => run(() => verifyInspectionAction(action.id, { expected_version: action.version, result: 'effective', notes: 'Результат подтверждён при контрольной проверке', index_name: null, sampled_value: null, create_follow_up: false, follow_up_assignee_id: null, follow_up_due_at: null }))} className="min-h-11 rounded-lg bg-green-700 px-4 font-bold text-white">Эффективно</button><button type="button" onClick={() => run(() => verifyInspectionAction(action.id, { expected_version: action.version, result: 'ineffective', notes: 'Требуется повторный цикл проверки', index_name: null, sampled_value: null, create_follow_up: false, follow_up_assignee_id: null, follow_up_due_at: null }))} className="min-h-11 rounded-lg border border-red-400 px-4 font-bold text-red-900">Неэффективно</button></>}</div></li>)}</ul>}
      {error && <p role="alert" className="mt-3 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">{error}</p>}
    </section>
  );
}

export default function AnomalyInspectionDetail({ detail, onBack, onRefresh }) {
  const { user } = useAuth();
  const [state, setState] = useState('idle');
  const [error, setError] = useState('');
  const role = user?.role;
  const canStart = role === 'agronomist' && detail.status === 'assigned' && detail.assigned_to_id === user?.id;
  const canSubmit = role === 'agronomist' && detail.status === 'in_progress' && Boolean(detail.finding);
  async function mutate(operation) { setState('saving'); setError(''); try { await operation(); await onRefresh(); } catch (requestError) { setError(apiError(requestError)); } finally { setState('idle'); } }
  return (
    <article className="h-full overflow-y-auto bg-white pt-16" aria-labelledby="inspection-detail-title">
      <header className="sticky top-16 z-20 border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur-sm sm:px-6"><div className="mx-auto flex max-w-6xl items-center gap-3"><button type="button" onClick={onBack} className="min-h-11 rounded-lg border border-slate-300 px-3 text-sm font-bold text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-700">← К очереди</button><div className="min-w-0 flex-1"><h1 id="inspection-detail-title" className="truncate text-lg font-bold text-slate-950">Осмотр #{detail.id} · {detail.field_name}</h1><p className="text-sm text-slate-600"><span>{STATUS[detail.status]}</span> · <span>{PRIORITY[detail.priority]}</span> · срок {new Date(detail.due_at).toLocaleString('ru-RU')}</p></div></div></header>
      <div className="mx-auto max-w-6xl px-4 py-5 sm:px-6"><SourceContext detail={detail} />
        {(canStart || canSubmit) && <div className="sticky bottom-3 z-10 my-4 flex justify-end rounded-xl border border-slate-200 bg-white p-3 shadow-md"><button type="button" disabled={state === 'saving'} onClick={() => mutate(() => canStart ? startAnomalyInspection(detail.id, detail.version) : submitAnomalyInspection(detail.id, detail.version))} className="min-h-12 w-full rounded-lg bg-green-700 px-5 font-bold text-white hover:bg-green-800 disabled:opacity-50 sm:w-auto">{canStart ? 'Начать осмотр' : 'Отправить на проверку'}</button></div>}
        {error && <p role="alert" className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">{error}</p>}
        <FindingWorkspace detail={detail} onRefresh={onRefresh} />
        <PhotoWorkspace detail={detail} onRefresh={onRefresh} />
        <ReviewAndActions detail={detail} onRefresh={onRefresh} />
        <section aria-labelledby="timeline-heading" className="py-5"><h2 id="timeline-heading" className="text-base font-bold text-slate-950">Хронология</h2>{detail.timeline?.length ? <ol className="mt-3 space-y-3">{detail.timeline.map((event) => <li key={event.id} className="grid gap-1 border-b border-slate-100 pb-3 text-sm sm:grid-cols-[11rem_1fr]"><time className="font-mono text-xs text-slate-600">{new Date(event.occurred_at).toLocaleString('ru-RU')}</time><div><p className="font-semibold text-slate-900">{event.event_type}</p><p className="text-slate-600">{event.actor_name} · версия {event.entity_version}</p>{event.event_metadata?.reason && <p className="mt-1 text-slate-700">{event.event_metadata.reason}</p>}</div></li>)}</ol> : <p className="mt-3 text-sm text-slate-600">Событий пока нет.</p>}</section>
      </div>
    </article>
  );
}
