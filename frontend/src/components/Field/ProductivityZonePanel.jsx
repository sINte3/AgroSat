import { useEffect, useMemo, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

import {
  getLatestProductivityZones,
  listProductivityZones,
} from '../../api/productivityZones';


const SOURCE_ID = 'productivity-zone-source';
const FILL_LAYER_ID = 'productivity-zone-fill';
const LINE_LAYER_ID = 'productivity-zone-line';
const ZONE_LABELS = {
  low: 'Низкая',
  medium: 'Средняя',
  high: 'Высокая',
};
const ZONE_COLORS = {
  low: '#b91c1c',
  medium: '#ca8a04',
  high: '#15803d',
};
const REASON_LABELS = {
  minimum_seasons_not_met: 'Нужно не менее трёх принятых сезонов.',
  minimum_points_per_season_not_met: 'В одном из сезонов меньше 20 измеренных точек.',
  minimum_total_points_not_met: 'Всего доступно меньше 60 измеренных точек.',
  no_multi_season_cells: 'Нет ячеек с измерениями минимум за два сезона.',
  no_cells_intersect_field: 'Расчётные ячейки не пересекают границу поля.',
};


function cancelled(error) {
  return error?.name === 'AbortError' || error?.code === 'ERR_CANCELED';
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


function removeMapOwnership(map) {
  if (!map || map._removed) return;
  if (map.getLayer(LINE_LAYER_ID)) map.removeLayer(LINE_LAYER_ID);
  if (map.getLayer(FILL_LAYER_ID)) map.removeLayer(FILL_LAYER_ID);
  if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
}


function renderZones(map, collection) {
  removeMapOwnership(map);
  if (!collection?.features?.length) return;
  map.addSource(SOURCE_ID, { type: 'geojson', data: collection });
  map.addLayer({
    id: FILL_LAYER_ID,
    type: 'fill',
    source: SOURCE_ID,
    paint: {
      'fill-color': [
        'match',
        ['get', 'zone_class'],
        'low', ZONE_COLORS.low,
        'medium', ZONE_COLORS.medium,
        'high', ZONE_COLORS.high,
        '#64748b',
      ],
      'fill-opacity': 0.42,
    },
  });
  map.addLayer({
    id: LINE_LAYER_ID,
    type: 'line',
    source: SOURCE_ID,
    paint: {
      'line-color': [
        'match',
        ['get', 'zone_class'],
        'low', '#7f1d1d',
        'medium', '#854d0e',
        'high', '#14532d',
        '#334155',
      ],
      'line-width': 2,
    },
  });
  const coordinates = collectCoordinates(
    collection.features.map((feature) => feature.geometry?.coordinates),
  );
  if (!coordinates.length) return;
  const bounds = coordinates.reduce(
    (value, coordinate) => value.extend(coordinate),
    new maplibregl.LngLatBounds(coordinates[0], coordinates[0]),
  );
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  map.fitBounds(bounds, {
    padding: 36,
    maxZoom: 17,
    duration: reducedMotion ? 0 : 400,
  });
}


export default function ProductivityZonePanel({ fieldId }) {
  const [state, setState] = useState('loading');
  const [run, setRun] = useState(null);
  const [zones, setZones] = useState([]);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const generationRef = useRef(0);
  const controllerRef = useRef(null);
  const mapContainerRef = useRef(null);
  const mapRef = useRef(null);
  const controlRef = useRef(null);
  const [mapReady, setMapReady] = useState(false);

  const collection = useMemo(() => ({
    type: 'FeatureCollection',
    features: zones
      .filter((zone) => zone?.geometry)
      .map((zone) => ({
        type: 'Feature',
        id: zone.id,
        properties: {
          zone_class: zone.zone_class,
          area_ha: Number(zone.area_ha),
          mean_score: Number(zone.mean_score),
          confidence: Number(zone.confidence),
        },
        geometry: zone.geometry,
      })),
  }), [zones]);

  useEffect(() => {
    if (!fieldId) return undefined;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const generation = ++generationRef.current;
    setState('loading');
    setRun(null);
    setZones([]);
    setError('');
    getLatestProductivityZones(fieldId, controller.signal)
      .then(async (payload) => {
        if (controller.signal.aborted || generation !== generationRef.current) return;
        const latest = payload?.latest_run || null;
        setRun(latest);
        if (!latest) {
          setState('empty');
          return;
        }
        if (latest.result_status !== 'ready') {
          setState('insufficient');
          return;
        }
        const zonePayload = await listProductivityZones(latest.id, controller.signal);
        if (controller.signal.aborted || generation !== generationRef.current) return;
        const items = Array.isArray(zonePayload?.items) ? zonePayload.items : [];
        setZones(items);
        setState(items.length ? 'ready' : 'empty');
      })
      .catch((requestError) => {
        if (
          controller.signal.aborted
          || generation !== generationRef.current
          || cancelled(requestError)
        ) return;
        setState('error');
        setError('Не удалось загрузить зоны продуктивности. Повторите запрос.');
      });
    return () => controller.abort();
  }, [fieldId, reload]);

  useEffect(() => () => {
    generationRef.current += 1;
    controllerRef.current?.abort();
  }, []);

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
        layers: [{
          id: 'productivity-background',
          type: 'background',
          paint: { 'background-color': '#f1f5f2' },
        }],
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
      removeMapOwnership(map);
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
    renderZones(map, collection);
    return () => removeMapOwnership(map);
  }, [collection, mapReady]);

  const confidence = Math.max(0, Math.min(1, Number(run?.confidence || 0)));

  return (
    <section className="card overflow-hidden p-0" aria-labelledby="productivity-zone-title">
      <div className="border-b border-agro-border bg-gradient-to-r from-emerald-50 to-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-emerald-800">
              Историческая устойчивость урожайности
            </p>
            <h3 id="productivity-zone-title" className="mt-1 text-lg font-bold text-agro-text">
              Зоны продуктивности
            </h3>
            <p className="mt-1 max-w-3xl text-sm text-agro-muted">
              Зоны отражают повторяющиеся измеренные значения урожайности. Это не агрономическое предписание
              и не расчёт только по спутниковым индексам.
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
      </div>

      <div className="space-y-4 p-4">
        {state === 'loading' && (
          <div role="status" aria-live="polite" className="space-y-2">
            <div className="h-20 animate-pulse rounded-lg bg-agro-surface2" />
            <span className="sr-only">Зоны продуктивности загружаются</span>
          </div>
        )}

        {state === 'error' && (
          <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
            {error}
          </div>
        )}

        {state === 'empty' && (
          <div className="rounded-lg border border-agro-border bg-agro-surface2/40 p-4 text-sm text-agro-muted">
            Готовых расчётов для поля пока нет. Расчёт запускается отдельной операционной командой после
            приёма измеренных карт урожайности.
          </div>
        )}

        {state === 'insufficient' && run && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-amber-950">
            <p className="font-semibold">Недостаточно надёжной истории</p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
              {(run.reason_codes || []).map((reason) => (
                <li key={reason}>{REASON_LABELS[reason] || reason}</li>
              ))}
            </ul>
            <p className="mt-3 text-xs">
              Сезоны: {(run.selected_seasons || []).join(', ') || '—'} · Алгоритм: {run.algorithm_version}
            </p>
          </div>
        )}

        {state === 'ready' && run && (
          <>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Metric label="Сезоны" value={(run.selected_seasons || []).join(', ')} />
              <Metric label="Уверенность" value={`${Math.round(confidence * 100)}%`} />
              <Metric label="Покрыто" value={`${finite(run.zoned_area_ha)} га`} />
              <Metric label="Без зоны" value={`${finite(run.area_delta_ha)} га`} />
            </div>

            <div
              ref={mapContainerRef}
              role="img"
              aria-label="Карта низкой, средней и высокой исторической продуктивности"
              className="h-72 min-h-64 overflow-hidden rounded-xl border border-agro-border"
            />

            <div className="grid gap-2 sm:grid-cols-3" aria-label="Легенда зон продуктивности">
              {['low', 'medium', 'high'].map((zoneClass) => {
                const zone = zones.find((item) => item.zone_class === zoneClass);
                return (
                  <div key={zoneClass} className="rounded-lg border border-agro-border p-3 text-sm">
                    <div className="flex items-center gap-2 font-semibold text-agro-text">
                      <span
                        aria-hidden="true"
                        className="h-3 w-3 rounded-full"
                        style={{ backgroundColor: ZONE_COLORS[zoneClass] }}
                      />
                      {ZONE_LABELS[zoneClass]}
                    </div>
                    <p className="mt-1 text-xs text-agro-muted">
                      {zone ? `${finite(zone.area_ha)} га · score ${finite(zone.mean_score)}` : 'Нет площади'}
                    </p>
                  </div>
                );
              })}
            </div>

            <div className="rounded-lg bg-agro-surface2/50 p-3 text-xs text-agro-muted">
              Алгоритм {run.algorithm_version} · {run.point_count} измерений · {run.eligible_cell_count} ячеек.
              Непокрытая площадь показана отдельно и не назначается зоне автоматически.
            </div>
          </>
        )}
      </div>
    </section>
  );
}


function Metric({ label, value }) {
  return (
    <div className="rounded-lg border border-agro-border bg-white p-3">
      <p className="text-xs text-agro-muted">{label}</p>
      <p className="mt-1 font-semibold text-agro-text">{value || '—'}</p>
    </div>
  );
}
