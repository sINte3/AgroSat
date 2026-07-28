import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

import {
  createInspectionFromPixelAnomaly,
  getPixelAnomaly,
  getPixelAnomalyGeometry,
  getPixelAnomalySummary,
  listPixelAnomalies,
} from '../../api/pixelAnomalies';
import { useAuth } from '../../context/AuthContext';
import {
  createIdempotencyKey,
  normalizeRole,
  safeErrorDetail,
  todayTashkentDate,
} from '../Inspections/inspectionPresentation';


const SOURCE_ID = 'pixel-anomaly-zone-source';
const FILL_LAYER_ID = 'pixel-anomaly-zone-fill';
const LINE_LAYER_ID = 'pixel-anomaly-zone-line';
const CLASSIFICATION_LABELS = {
  single_scene: 'Однократная',
  persistent: 'Устойчивая',
  recovering: 'Восстанавливается',
};
const STATUS_LABELS = {
  open: 'Требует проверки',
  inspection_created: 'Осмотр создан',
  dismissed: 'Отклонена',
  resolved: 'Закрыта',
};
const INDEX_LABELS = {
  ndvi: 'NDVI',
  savi: 'SAVI',
  evi: 'EVI',
  ndmi: 'NDMI',
  ndre: 'NDRE',
};


function cancelled(error) {
  return error?.name === 'AbortError' || error?.code === 'ERR_CANCELED';
}


function dateFromRange(days) {
  const safeDays = Math.max(1, Math.min(Number(days) || 90, 366));
  const value = new Date(`${todayTashkentDate()}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() - safeDays);
  return value.toISOString().slice(0, 10);
}


function formatDate(value) {
  if (!value) return '—';
  const parsed = new Date(`${value}T12:00:00`);
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleDateString('ru-RU');
}


function finite(value, digits = 2) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
}


function collectCoordinates(value, output = []) {
  if (!Array.isArray(value)) return output;
  if (
    value.length >= 2
    && Number.isFinite(Number(value[0]))
    && Number.isFinite(Number(value[1]))
  ) {
    output.push([Number(value[0]), Number(value[1])]);
    return output;
  }
  value.forEach((child) => collectCoordinates(child, output));
  return output;
}


function fitFeature(map, feature) {
  const coordinates = collectCoordinates(feature?.geometry?.coordinates);
  if (!coordinates.length) return;
  const bounds = coordinates.reduce(
    (result, coordinate) => result.extend(coordinate),
    new maplibregl.LngLatBounds(coordinates[0], coordinates[0]),
  );
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  map.fitBounds(bounds, {
    padding: 44,
    maxZoom: 16,
    duration: reducedMotion ? 0 : 450,
  });
}


function removeZoneOwnership(map) {
  if (!map || map._removed) return;
  if (map.getLayer(LINE_LAYER_ID)) map.removeLayer(LINE_LAYER_ID);
  if (map.getLayer(FILL_LAYER_ID)) map.removeLayer(FILL_LAYER_ID);
  if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
}


function renderZone(map, feature) {
  removeZoneOwnership(map);
  if (!feature?.geometry) return;
  map.addSource(SOURCE_ID, { type: 'geojson', data: feature });
  map.addLayer({
    id: FILL_LAYER_ID,
    type: 'fill',
    source: SOURCE_ID,
    paint: {
      'fill-color': '#dc2626',
      'fill-opacity': 0.28,
    },
  });
  map.addLayer({
    id: LINE_LAYER_ID,
    type: 'line',
    source: SOURCE_ID,
    paint: {
      'line-color': '#991b1b',
      'line-width': 3,
    },
  });
  fitFeature(map, feature);
}


function statusTone(status) {
  if (status === 'inspection_created') return 'border-blue-200 bg-blue-50 text-blue-800';
  if (status === 'resolved') return 'border-emerald-200 bg-emerald-50 text-emerald-800';
  if (status === 'dismissed') return 'border-slate-200 bg-slate-50 text-slate-700';
  return 'border-amber-200 bg-amber-50 text-amber-900';
}


function InspectionFromAnomalyDialog({ anomaly, onClose, onSuccess }) {
  const { user } = useAuth();
  const role = normalizeRole(user?.role);
  const [title, setTitle] = useState(`Осмотр аномальной зоны #${anomaly.id}`);
  const [instructions, setInstructions] = useState(
    'Проверить зону на месте, зафиксировать наблюдения и подтвердить либо отклонить причину.',
  );
  const [dueDate, setDueDate] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const dialogRef = useRef(null);
  const initialFocusRef = useRef(null);
  const controllerRef = useRef(null);
  const mountedRef = useRef(false);
  const generationRef = useRef(0);
  const idempotencyKeyRef = useRef(createIdempotencyKey());
  const frozenPayloadRef = useRef(null);

  useEffect(() => {
    mountedRef.current = true;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    initialFocusRef.current?.focus();
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      controllerRef.current?.abort();
      document.body.style.overflow = previousOverflow;
      idempotencyKeyRef.current = null;
    };
  }, []);

  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.key === 'Escape' && !pending) {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(
        dialogRef.current?.querySelectorAll(
          'button:not([disabled]), input:not([disabled]), textarea:not([disabled])',
        ) || [],
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose, pending]);

  const payload = useMemo(
    () => ({
      title: title.trim(),
      instructions: instructions.trim() || null,
      due_date: dueDate || null,
    }),
    [dueDate, instructions, title],
  );
  const serializedPayload = JSON.stringify(payload);
  const valid = payload.title.length >= 3
    && payload.title.length <= 255
    && (!payload.instructions || (
      payload.instructions.length >= 3 && payload.instructions.length <= 2000
    ))
    && (!dueDate || dueDate >= todayTashkentDate());

  async function submit(event) {
    event.preventDefault();
    if (!valid || pending) return;
    if (
      frozenPayloadRef.current
      && frozenPayloadRef.current !== serializedPayload
    ) {
      idempotencyKeyRef.current = createIdempotencyKey();
    }
    frozenPayloadRef.current = serializedPayload;
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    const generation = ++generationRef.current;
    setPending(true);
    setError('');
    try {
      const result = await createInspectionFromPixelAnomaly(
        anomaly.id,
        payload,
        idempotencyKeyRef.current,
        controller.signal,
      );
      if (
        !mountedRef.current
        || controller.signal.aborted
        || generation !== generationRef.current
      ) return;
      idempotencyKeyRef.current = null;
      onSuccess(result);
    } catch (requestError) {
      if (
        !mountedRef.current
        || controller.signal.aborted
        || generation !== generationRef.current
        || cancelled(requestError)
      ) return;
      const status = requestError?.response?.status;
      if (status === 403) setError('Недостаточно прав для создания осмотра.');
      else if (status === 404) setError('Зона не найдена или больше недоступна.');
      else if (status === 409) {
        setError(
          safeErrorDetail(requestError)
          || 'Состояние зоны изменилось. Обновите список перед повтором.',
        );
      } else if (status === 422) setError('Проверьте заголовок, инструкции и срок.');
      else setError('Результат запроса неизвестен. Можно повторить тот же запрос.');
    } finally {
      if (
        mountedRef.current
        && !controller.signal.aborted
        && generation === generationRef.current
      ) setPending(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-3">
      <form
        ref={dialogRef}
        onSubmit={submit}
        role="dialog"
        aria-modal="true"
        aria-labelledby="anomaly-inspection-title"
        aria-describedby="anomaly-inspection-description"
        className="card max-h-[calc(100vh-1.5rem)] w-full max-w-lg overflow-y-auto p-5 shadow-xl"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 id="anomaly-inspection-title" className="text-lg font-bold text-agro-text">
              Создать полевой осмотр
            </h3>
            <p id="anomaly-inspection-description" className="mt-1 text-sm text-agro-muted">
              Зона #{anomaly.id} — измерительный сигнал, а не подтверждённая причина.
            </p>
          </div>
          <button
            ref={initialFocusRef}
            type="button"
            onClick={onClose}
            disabled={pending}
            aria-label="Закрыть"
            className="min-h-11 min-w-11 rounded-lg p-2 focus:outline-none focus:ring-2 focus:ring-agro-accent"
          >
            ✕
          </button>
        </div>

        <div className="mt-5 space-y-4">
          <label className="block text-sm font-medium text-agro-text">
            Заголовок
            <input
              required
              minLength={3}
              maxLength={255}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              className="input mt-1 min-h-11 w-full"
            />
          </label>
          <label className="block text-sm font-medium text-agro-text">
            Инструкции
            <textarea
              required
              minLength={3}
              maxLength={2000}
              rows={4}
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              className="input mt-1 w-full"
            />
          </label>
          <label className="block text-sm font-medium text-agro-text">
            Срок
            <input
              type="date"
              min={todayTashkentDate()}
              value={dueDate}
              onChange={(event) => setDueDate(event.target.value)}
              className="input mt-1 min-h-11 w-full"
            />
          </label>
          {role === 'agronomist' && (
            <p className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-900">
              Осмотр будет назначен вам.
            </p>
          )}
        </div>

        <p aria-live="polite" className="mt-3 min-h-5 text-sm text-red-700">
          {error}
        </p>
        <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            onClick={onClose}
            disabled={pending}
            className="btn-secondary min-h-11 px-4 py-2"
          >
            Отмена
          </button>
          <button
            disabled={!valid || pending}
            className="btn-primary min-h-11 px-4 py-2 disabled:opacity-50"
          >
            {pending ? 'Создаём…' : frozenPayloadRef.current ? 'Повторить запрос' : 'Создать осмотр'}
          </button>
        </div>
      </form>
    </div>
  );
}


export default function PixelAnomalyPanel({ fieldId, indexCode = 'ndvi', dayRange = 90 }) {
  const { user } = useAuth();
  const role = normalizeRole(user?.role);
  const canWrite = ['admin', 'manager', 'agronomist'].includes(role);
  const [summary, setSummary] = useState(null);
  const [items, setItems] = useState([]);
  const [listTotal, setListTotal] = useState(0);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [feature, setFeature] = useState(null);
  const [state, setState] = useState('loading');
  const [detailState, setDetailState] = useState('idle');
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [dialogOpen, setDialogOpen] = useState(false);
  const mapContainerRef = useRef(null);
  const mapRef = useRef(null);
  const controlRef = useRef(null);
  const listGenerationRef = useRef(0);
  const detailGenerationRef = useRef(0);
  const listControllerRef = useRef(null);
  const detailControllerRef = useRef(null);
  const createButtonRef = useRef(null);
  const headingRef = useRef(null);
  const focusFrameRef = useRef(null);
  const [mapReady, setMapReady] = useState(false);

  useEffect(() => {
    if (state !== 'ready' || !mapContainerRef.current || mapRef.current) {
      return undefined;
    }
    setMapReady(false);
    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: {
        version: 8,
        sources: {},
        layers: [
          {
            id: 'anomaly-background',
            type: 'background',
            paint: { 'background-color': '#eef4ef' },
          },
        ],
      },
      center: [64.42, 39.77],
      zoom: 9,
      attributionControl: false,
    });
    const navigation = new maplibregl.NavigationControl({
      showCompass: false,
      visualizePitch: false,
    });
    const handleStyleLoad = () => setMapReady(true);
    map.addControl(navigation, 'top-right');
    map.on('style.load', handleStyleLoad);
    mapRef.current = map;
    controlRef.current = navigation;
    return () => {
      map.off('style.load', handleStyleLoad);
      removeZoneOwnership(map);
      if (controlRef.current) {
        try {
          map.removeControl(controlRef.current);
        } catch (_) {
          // MapLibre may already have detached the control.
        }
      }
      controlRef.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, [state]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || map._removed) return undefined;
    renderZone(map, feature);
    return () => removeZoneOwnership(map);
  }, [feature, mapReady]);

  useEffect(() => {
    if (!fieldId) return undefined;
    listControllerRef.current?.abort();
    detailControllerRef.current?.abort();
    const controller = new AbortController();
    listControllerRef.current = controller;
    const generation = ++listGenerationRef.current;
    detailGenerationRef.current += 1;
    setState('loading');
    setError('');
    setSelectedId(null);
    setDetail(null);
    setFeature(null);
    Promise.all([
      getPixelAnomalySummary(fieldId, controller.signal),
      listPixelAnomalies(
        {
          fieldId,
          indexCode,
          dateFrom: dateFromRange(dayRange),
          limit: 50,
          offset: 0,
        },
        controller.signal,
      ),
    ])
      .then(([summaryResult, listResult]) => {
        if (controller.signal.aborted || generation !== listGenerationRef.current) return;
        const nextItems = Array.isArray(listResult?.items) ? listResult.items : [];
        setSummary(summaryResult);
        setItems(nextItems);
        setListTotal(Number(listResult?.total || 0));
        setSelectedId(nextItems[0]?.id || null);
        setState(nextItems.length ? 'ready' : 'empty');
      })
      .catch((requestError) => {
        if (
          controller.signal.aborted
          || generation !== listGenerationRef.current
          || cancelled(requestError)
        ) return;
        setState('error');
        setError('Не удалось загрузить зоны. Повторите запрос.');
      });
    return () => controller.abort();
  }, [dayRange, fieldId, indexCode, reload]);

  useEffect(() => {
    detailControllerRef.current?.abort();
    if (!selectedId) {
      setDetail(null);
      setFeature(null);
      setDetailState('idle');
      return undefined;
    }
    const controller = new AbortController();
    detailControllerRef.current = controller;
    const generation = ++detailGenerationRef.current;
    setDetailState('loading');
    setDetail(null);
    setFeature(null);
    Promise.all([
      getPixelAnomaly(selectedId, controller.signal),
      getPixelAnomalyGeometry(selectedId, controller.signal),
    ])
      .then(([detailResult, geometryResult]) => {
        if (
          controller.signal.aborted
          || generation !== detailGenerationRef.current
        ) return;
        setDetail(detailResult);
        setFeature(geometryResult);
        setDetailState('ready');
      })
      .catch((requestError) => {
        if (
          controller.signal.aborted
          || generation !== detailGenerationRef.current
          || cancelled(requestError)
        ) return;
        setDetail(null);
        setFeature(null);
        setDetailState('error');
      });
    return () => controller.abort();
  }, [selectedId]);

  useEffect(
    () => () => {
      listGenerationRef.current += 1;
      detailGenerationRef.current += 1;
      listControllerRef.current?.abort();
      detailControllerRef.current?.abort();
      if (focusFrameRef.current !== null) {
        window.cancelAnimationFrame(focusFrameRef.current);
      }
    },
    [],
  );

  const selected = useMemo(
    () => items.find((item) => item.id === selectedId) || null,
    [items, selectedId],
  );

  const closeDialog = useCallback((returnToHeading = false) => {
    setDialogOpen(false);
    if (focusFrameRef.current !== null) {
      window.cancelAnimationFrame(focusFrameRef.current);
    }
    focusFrameRef.current = window.requestAnimationFrame(() => {
      focusFrameRef.current = null;
      const target = returnToHeading ? headingRef.current : createButtonRef.current;
      (target || headingRef.current)?.focus();
    });
  }, []);

  const handleCreated = useCallback((result) => {
    const inspection = result?.inspection || null;
    setItems((current) => current.map((item) => (
      item.id === result?.anomaly_id
        ? { ...item, status: 'inspection_created', inspection }
        : item
    )));
    setDetail((current) => (
      current && current.id === result?.anomaly_id
        ? { ...current, status: 'inspection_created', inspection }
        : current
    ));
    setSummary((current) => (
      current
        ? {
            ...current,
            open: Math.max(0, Number(current.open || 0) - 1),
            inspection_created: Number(current.inspection_created || 0) + 1,
          }
        : current
    ));
    closeDialog(true);
  }, [closeDialog]);

  return (
    <section className="card overflow-hidden p-0" aria-labelledby="pixel-anomaly-title">
      <div className="border-b border-agro-border bg-gradient-to-r from-red-50 to-white p-4 md:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-red-700">
              Внутриполевая проверка
            </p>
            <h2
              ref={headingRef}
              id="pixel-anomaly-title"
              tabIndex={-1}
              className="mt-1 text-lg font-bold text-agro-text focus:outline-none"
            >
              Пиксельные аномалии · {INDEX_LABELS[indexCode] || indexCode.toUpperCase()}
            </h2>
            <p className="mt-1 max-w-3xl text-sm text-agro-muted">
              Зоны показывают измеренное отличие сигнала. Это повод для осмотра, а не диагноз
              орошения, питания, болезни или урожайности.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setReload((value) => value + 1)}
            disabled={state === 'loading'}
            className="btn-secondary min-h-11 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-agro-accent"
          >
            Обновить
          </button>
        </div>

        {summary && (
          <dl className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              ['Открытые зоны', summary.open],
              ['Осмотры созданы', summary.inspection_created],
              ['Устойчивые', summary.persistent],
              ['Недостаточно данных', summary.insufficient_data_runs],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border border-white bg-white/80 p-3">
                <dt className="text-xs text-agro-muted">{label}</dt>
                <dd className="mt-1 text-xl font-bold text-agro-text">{Number(value || 0)}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      <div aria-live="polite">
        {state === 'loading' && (
          <div className="grid gap-4 p-4 md:grid-cols-[minmax(260px,0.8fr)_minmax(0,1.4fr)]">
            <div className="h-64 animate-pulse rounded-lg bg-agro-card" />
            <div className="h-80 animate-pulse rounded-lg bg-agro-card" />
          </div>
        )}
        {state === 'error' && (
          <div className="p-6 text-center">
            <p role="alert" className="text-sm font-medium text-red-700">{error}</p>
          </div>
        )}
        {state === 'empty' && (
          <div className="p-6 text-center">
            <p className="font-medium text-agro-text">Зоны за выбранный период не обнаружены</p>
            <p className="mt-1 text-sm text-agro-muted">
              Это не подтверждает отсутствие проблемы. Проверьте свежесть и качество наблюдений.
            </p>
            {Number(summary?.insufficient_data_runs || 0) > 0 && (
              <p className="mt-3 inline-flex rounded-full bg-amber-100 px-3 py-1 text-sm text-amber-900">
                Недостаточно данных: {summary.insufficient_data_runs}
              </p>
            )}
          </div>
        )}
      </div>

      {state === 'ready' && (
        <div className="grid min-w-0 md:grid-cols-[minmax(260px,0.82fr)_minmax(0,1.5fr)]">
          <div className="max-h-[34rem] overflow-y-auto border-b border-agro-border p-3 md:border-b-0 md:border-r">
            <p className="px-1 pb-2 text-xs text-agro-muted">
              Показано {items.length} из {listTotal} зон
            </p>
            <div className="space-y-2" role="list" aria-label="Список пиксельных аномалий">
              {items.map((item) => {
                const active = item.id === selectedId;
                return (
                  <div role="listitem" key={item.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(item.id)}
                      aria-pressed={active}
                      className={`min-h-11 w-full rounded-lg border p-3 text-left focus:outline-none focus:ring-2 focus:ring-agro-accent ${
                        active
                          ? 'border-red-300 bg-red-50'
                          : 'border-agro-border bg-white hover:bg-agro-card'
                      }`}
                    >
                      <span className="flex items-start justify-between gap-2">
                        <span className="font-semibold text-agro-text">Зона #{item.id}</span>
                        <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${statusTone(item.status)}`}>
                          {STATUS_LABELS[item.status] || item.status}
                        </span>
                      </span>
                      <span className="mt-2 grid grid-cols-2 gap-1 text-xs text-agro-muted">
                        <span>{CLASSIFICATION_LABELS[item.classification] || item.classification}</span>
                        <span>{finite(item.area_ha)} га</span>
                        <span>{formatDate(item.current_observation_date)}</span>
                        <span>Уверенность {finite(Number(item.confidence) * 100, 0)}%</span>
                      </span>
                    </button>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="min-w-0 p-3 md:p-4">
            <div
              ref={mapContainerRef}
              role="region"
              aria-label={
                selected
                  ? `Карта выбранной аномальной зоны ${selected.id}`
                  : 'Карта аномальных зон'
              }
              className="h-64 w-full overflow-hidden rounded-lg border border-agro-border bg-[#eef4ef] sm:h-72"
            />

            {detailState === 'loading' && (
              <p aria-live="polite" className="mt-3 text-sm text-agro-muted">
                Загружаем измерения зоны…
              </p>
            )}
            {detailState === 'error' && (
              <p role="alert" className="mt-3 text-sm text-red-700">
                Не удалось загрузить геометрию или измерения выбранной зоны.
              </p>
            )}
            {detailState === 'ready' && detail && (
              <div className="mt-4 space-y-4">
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {[
                    ['Текущий снимок', formatDate(detail.current_observation_date)],
                    ['Сравнение', formatDate(detail.comparison_observation_date)],
                    ['Площадь', `${finite(detail.area_ha)} га`],
                    ['Уверенность', `${finite(Number(detail.confidence) * 100, 0)}%`],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-lg bg-agro-card p-3">
                      <p className="text-xs text-agro-muted">{label}</p>
                      <p className="mt-1 text-sm font-semibold text-agro-text">{value}</p>
                    </div>
                  ))}
                </div>

                <div className="grid gap-3 text-sm sm:grid-cols-2">
                  <div className="rounded-lg border border-agro-border p-3">
                    <h3 className="font-semibold text-agro-text">Качество данных</h3>
                    <dl className="mt-2 space-y-1 text-agro-muted">
                      <div className="flex justify-between gap-3">
                        <dt>Валидные пиксели</dt>
                        <dd>{finite(detail.quality_summary?.current_valid_pixels_pct, 1)}%</dd>
                      </div>
                      <div className="flex justify-between gap-3">
                        <dt>Облачность</dt>
                        <dd>{finite(detail.quality_summary?.current_cloud_cover_pct, 1)}%</dd>
                      </div>
                      <div className="flex justify-between gap-3">
                        <dt>Алгоритм</dt>
                        <dd className="max-w-[12rem] truncate" title={detail.algorithm_version}>
                          {detail.algorithm_version}
                        </dd>
                      </div>
                      <div className="flex justify-between gap-3">
                        <dt>Провайдер данных</dt>
                        <dd className="max-w-[12rem] truncate">
                          {detail.provenance?.run?.provider
                            || detail.provenance?.provider
                            || '—'}
                        </dd>
                      </div>
                    </dl>
                  </div>
                  <div className="rounded-lg border border-agro-border p-3">
                    <h3 className="font-semibold text-agro-text">Наблюдаемое отличие</h3>
                    <dl className="mt-2 space-y-1 text-agro-muted">
                      <div className="flex justify-between gap-3">
                        <dt>Класс</dt>
                        <dd>{CLASSIFICATION_LABELS[detail.classification] || detail.classification}</dd>
                      </div>
                      <div className="flex justify-between gap-3">
                        <dt>Оценка</dt>
                        <dd>{finite(Number(detail.score) * 100, 0)}/100</dd>
                      </div>
                      <div className="flex justify-between gap-3">
                        <dt>Наблюдений подряд</dt>
                        <dd>{detail.persistence_count}</dd>
                      </div>
                    </dl>
                  </div>
                </div>

                <div className="flex flex-col gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-sm text-amber-950">
                    Причину необходимо подтвердить полевым осмотром. Изменение индекса само по
                    себе не доказывает агрономическую причинность.
                  </p>
                  {detail.inspection ? (
                    <span className="shrink-0 rounded-full bg-blue-100 px-3 py-1 text-sm font-medium text-blue-900">
                      Осмотр #{detail.inspection.id} · {detail.inspection.status}
                    </span>
                  ) : canWrite && detail.status === 'open' ? (
                    <button
                      ref={createButtonRef}
                      type="button"
                      onClick={() => setDialogOpen(true)}
                      className="btn-primary min-h-11 shrink-0 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-offset-2"
                    >
                      Создать осмотр
                    </button>
                  ) : (
                    <span className="shrink-0 text-sm font-medium text-amber-950">
                      Только чтение
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {dialogOpen && detail && (
        <InspectionFromAnomalyDialog
          anomaly={detail}
          onClose={closeDialog}
          onSuccess={handleCreated}
        />
      )}
    </section>
  );
}
