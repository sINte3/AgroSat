import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  getField,
  getNDVIHistory,
  getSatelliteIndexLatest,
  getSatelliteIndexHistory,
  getSatelliteCoverage,
} from '../../api/client';
import INDEX_METADATA, {
  SATELLITE_INDEX_CODES,
  ALL_INDEX_CODES,
  getIndexMetadata,
  getIndexColor,
  COVERAGE_STATUS_CONFIG,
  FRESHNESS_STATUS_CONFIG,
} from '../../config/indexMetadata';
import FieldAnalyticsCharts from './FieldAnalyticsCharts';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function formatDate(dateStr) {
  if (!dateStr) return '—';
  const d = new Date(dateStr);
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' });
}

function daysSince(dateStr) {
  if (!dateStr) return null;
  const d = new Date(dateStr);
  const now = new Date();
  return Math.floor((now - d) / (1000 * 60 * 60 * 24));
}

function trendIndicator(history, valueKey) {
  if (!history || history.length < 3) return { label: '—', dir: 'unknown' };
  const vals = history.slice(-3).map(r => r[valueKey]).filter(v => v != null);
  if (vals.length < 2) return { label: '—', dir: 'unknown' };
  const first = vals[0];
  const last = vals[vals.length - 1];
  const diff = last - first;
  if (diff > 0.02) return { label: '↑ Рост', dir: 'up' };
  if (diff < -0.02) return { label: '↓ Снижение', dir: 'down' };
  return { label: '→ Стабильно', dir: 'stable' };
}

function getCoverageStatusConfig(status) {
  return COVERAGE_STATUS_CONFIG[status] || COVERAGE_STATUS_CONFIG.none;
}

function getFreshnessStatusConfig(status) {
  return FRESHNESS_STATUS_CONFIG[status] || FRESHNESS_STATUS_CONFIG.no_data;
}

function irrigationLabel(type) {
  const labels = {
    canal: 'Канальное',
    drip: 'Капельное',
    sprinkler: 'Дождевание',
    rainfed: 'Богара',
  };
  return labels[type] || type || '—';
}

// ─── Loading Skeleton ─────────────────────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <div className="p-6 space-y-6 animate-pulse">
      <div className="h-8 bg-agro-surface2 rounded w-64" />
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        {[1, 2, 3, 4, 5].map(i => (
          <div key={i} className="h-28 bg-agro-surface2 rounded-lg" />
        ))}
      </div>
      <div className="h-64 bg-agro-surface2 rounded-lg" />
      <div className="h-48 bg-agro-surface2 rounded-lg" />
    </div>
  );
}

// ─── Error State ──────────────────────────────────────────────────────────────

function ErrorState({ message, onRetry }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-4">
      <div className="text-4xl mb-4">⚠️</div>
      <p className="text-agro-danger font-medium mb-2">{message || 'Ошибка загрузки данных'}</p>
      <p className="text-sm text-agro-muted mb-6">Попробуйте обновить страницу или вернуться позже</p>
      {onRetry && (
        <button onClick={onRetry} className="btn-primary px-6 py-2 text-sm rounded-lg">
          Повторить
        </button>
      )}
    </div>
  );
}

// ─── Empty State ──────────────────────────────────────────────────────────────

function EmptyState({ message, subtext }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 px-4">
      <div className="text-4xl mb-3">📭</div>
      <p className="text-agro-text font-medium mb-1">{message || 'Нет данных'}</p>
      {subtext && <p className="text-sm text-agro-muted">{subtext}</p>}
    </div>
  );
}

// ─── Index Card (compact) ─────────────────────────────────────────────────────

function IndexCardCompact({ code, record, loading, error, historyRecords }) {
  const meta = getIndexMetadata(code);
  if (!meta) return null;

  const value = record?.mean_value ?? record?.mean_ndvi ?? null;
  const date = record?.captured_date || null;
  const color = value != null ? getIndexColor(value, code) : '#9ca3af';
  const trend = trendIndicator(historyRecords, code === 'ndvi' ? 'mean_ndvi' : 'mean_value');

  return (
    <div className="card p-3 text-xs space-y-1.5 transition-colors hover:border-agro-accent/30">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-agro-text">{meta.label}</span>
        {!loading && !error && value != null && (
          <span className={`text-[10px] font-medium ${
            trend.dir === 'up' ? 'text-green-600' : trend.dir === 'down' ? 'text-red-500' : 'text-gray-400'
          }`}>
            {trend.label}
          </span>
        )}
      </div>

      {loading ? (
        <div className="space-y-1">
          <div className="h-5 w-16 bg-agro-surface2 rounded animate-pulse" />
          <div className="h-3 w-20 bg-agro-surface2 rounded animate-pulse" />
        </div>
      ) : error ? (
        <div className="text-agro-danger text-[11px]">Ошибка загрузки</div>
      ) : value != null ? (
        <>
          <div className="flex items-baseline gap-1.5">
            <span className="text-lg font-bold" style={{ color }}>
              {value.toFixed(meta.precision)}
            </span>
          </div>
          <div className="text-[10px] text-agro-muted space-y-0.5">
            <div>{formatDate(date)}</div>
            {record?.valid_pixels_pct != null && (
              <div>Пиксели: {record.valid_pixels_pct.toFixed(1)}%</div>
            )}
          </div>
        </>
      ) : (
        <div className="text-agro-muted text-[11px]">Нет данных</div>
      )}

      {/* Meaning badge */}
      <div className="text-[10px] text-agro-muted pt-1 border-t border-agro-surface2/30">
        {meta.shortMeaning}
      </div>
    </div>
  );
}

// ─── Index Cards Row ──────────────────────────────────────────────────────────

function IndexCardsRow({ perIndexState, ndviHistory, multiHistory }) {
  return (
    <div>
      <h3 className="text-xs font-semibold text-agro-muted uppercase tracking-wide mb-2.5">
        Текущие значения индексов
      </h3>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-2.5">
        {ALL_INDEX_CODES.map(code => {
          const state = perIndexState[code];
          return (
            <IndexCardCompact
              key={code}
              code={code}
              record={state?.record ?? null}
              loading={state?.loading ?? false}
              error={state?.error ?? false}
              historyRecords={code === 'ndvi' ? ndviHistory : multiHistory[code]}
            />
          );
        })}
      </div>
    </div>
  );
}

// ─── Latest Values Table ──────────────────────────────────────────────────────

function LatestValuesTable({ perIndexState }) {
  const rows = ALL_INDEX_CODES.map(code => {
    const meta = getIndexMetadata(code);
    const state = perIndexState[code];
    const record = state?.record;
    const value = record?.mean_value ?? record?.mean_ndvi ?? null;
    const date = record?.captured_date || null;
    const valueColor = value != null ? getIndexColor(value, code) : '#9ca3af';

    // Count records from various sources
    let recordCount = state?.recordCount ?? null;

    return { code, meta, value, date, valueColor, record, recordCount, loading: state?.loading, error: state?.error };
  });

  return (
    <div className="card overflow-hidden print:border print:border-gray-300">
      <h3 className="text-xs font-semibold text-agro-muted uppercase tracking-wide mb-3 px-4 pt-4">
        Сводная таблица индексов
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs text-left">
          <thead>
            <tr className="border-b border-agro-surface2">
              <th className="py-2 px-4 text-agro-muted font-medium">Индекс</th>
              <th className="py-2 px-3 text-agro-muted font-medium">Значение</th>
              <th className="py-2 px-3 text-agro-muted font-medium">Дата</th>
              <th className="py-2 px-3 text-agro-muted font-medium">Записей</th>
              <th className="py-2 px-3 text-agro-muted font-medium">Свежесть</th>
              <th className="py-2 px-3 text-agro-muted font-medium">Статус</th>
              <th className="py-2 px-3 text-agro-muted font-medium">Значение</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const days = r.date ? daysSince(r.date) : null;
              let freshnessLabel = 'Нет данных';
              let freshnessClass = 'text-gray-400';
              if (days !== null) {
                if (days <= 3) { freshnessLabel = 'Актуально'; freshnessClass = 'text-green-600'; }
                else if (days <= 10) { freshnessLabel = 'Частично'; freshnessClass = 'text-amber-600'; }
                else { freshnessLabel = 'Устарело'; freshnessClass = 'text-red-500'; }
              }

              return (
                <tr key={r.code} className="border-b border-agro-surface2/40 hover:bg-agro-surface2/20">
                  <td className="py-2.5 px-4 font-medium text-agro-text">
                    {r.meta?.label || r.code.toUpperCase()}
                  </td>
                  <td className="py-2.5 px-3">
                    {r.loading ? (
                      <span className="text-agro-muted">...</span>
                    ) : r.error ? (
                      <span className="text-red-400">Ошибка</span>
                    ) : r.value != null ? (
                      <span className="font-medium" style={{ color: r.valueColor }}>
                        {r.value.toFixed(r.meta?.precision ?? 4)}
                      </span>
                    ) : (
                      <span className="text-agro-muted">Нет данных</span>
                    )}
                  </td>
                  <td className="py-2.5 px-3 text-agro-muted">{formatDate(r.date)}</td>
                  <td className="py-2.5 px-3 text-agro-muted">
                    {r.recordCount != null ? r.recordCount : (r.loading ? '...' : '—')}
                  </td>
                  <td className={`py-2.5 px-3 font-medium ${freshnessClass}`}>
                    {freshnessLabel}
                  </td>
                  <td className="py-2.5 px-3">
                    {r.value != null ? (
                      <span className="inline-flex items-center gap-1 text-green-600">
                        <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                        Доступно
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-gray-400">
                        <span className="w-1.5 h-1.5 rounded-full bg-gray-300" />
                        Нет данных
                      </span>
                    )}
                  </td>
                  <td className="py-2.5 px-3 text-agro-muted text-[10px]">
                    {r.meta?.shortMeaning || '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Data Quality / Coverage Panel ────────────────────────────────────────────

function DataQualityPanel({ coverage, perIndexState }) {
  const coverageCfg = getCoverageStatusConfig(coverage?.coverage_status);
  const freshnessCfg = getFreshnessStatusConfig(coverage?.freshness_status);
  const latestDate = coverage?.latest_captured_date || null;
  const days = latestDate ? daysSince(latestDate) : null;

  // Per-index data presence
  const indexDataPresence = ALL_INDEX_CODES.map(code => {
    const meta = getIndexMetadata(code);
    const state = perIndexState[code];
    const hasData = state?.record != null;
    const recCount = state?.recordCount ?? null;
    return { code, label: meta?.label || code.toUpperCase(), hasData, recordCount: recCount };
  });

  return (
    <div className="card">
      <h3 className="text-xs font-semibold text-agro-muted uppercase tracking-wide mb-3">
        Качество и покрытие данных
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Status badges */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-xs text-agro-muted w-28">Покрытие:</span>
            <span
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium"
              style={{ backgroundColor: coverageCfg.bgColor, color: coverageCfg.textColor }}
            >
              {coverageCfg.icon} {coverageCfg.label}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-agro-muted w-28">Актуальность:</span>
            <span
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium"
              style={{ backgroundColor: freshnessCfg.bgColor, color: freshnessCfg.textColor }}
            >
              {freshnessCfg.label}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-agro-muted w-28">Последний снимок:</span>
            <span className="text-xs text-agro-text">
              {latestDate ? formatDate(latestDate) : '—'}
              {days !== null && ` (${days} дн. назад)`}
            </span>
          </div>
        </div>

        {/* Per-index presence */}
        <div>
          <p className="text-xs text-agro-muted mb-1.5">Доступность индексов:</p>
          <div className="space-y-1">
            {indexDataPresence.map((item) => (
              <div key={item.code} className="flex items-center gap-2 text-xs">
                <span className={`w-2 h-2 rounded-full ${item.hasData ? 'bg-green-500' : 'bg-gray-300'}`} />
                <span className="text-agro-text w-12">{item.label}</span>
                {item.hasData ? (
                  <span className="text-green-600">Доступно</span>
                ) : (
                  <span className="text-agro-muted">Нет данных</span>
                )}
                {item.recordCount != null && (
                  <span className="text-agro-muted">({item.recordCount} зап.)</span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Operational Agronomist Hints ─────────────────────────────────────────────

function OperationalHints({ perIndexState, coverage }) {
  const hints = [];

  const ndviRecord = perIndexState?.ndvi?.record;
  const ndmiRecord = perIndexState?.ndmi?.record;
  const ndreRecord = perIndexState?.ndre?.record;
  const saviRecord = perIndexState?.savi?.record;
  const eviRecord = perIndexState?.evi?.record;

  const ndviValue = ndviRecord?.mean_ndvi ?? null;
  const ndmiValue = ndmiRecord?.mean_value ?? null;
  const ndreValue = ndreRecord?.mean_value ?? null;
  const saviValue = saviRecord?.mean_value ?? null;
  const eviValue = eviRecord?.mean_value ?? null;

  const coverageStatus = coverage?.coverage_status;
  const allHaveData = ALL_INDEX_CODES.every(code => perIndexState[code]?.record != null);
  const noneHaveData = ALL_INDEX_CODES.every(code => perIndexState[code]?.record == null);

  // NDMI — moisture check
  if (ndmiValue != null) {
    if (ndmiValue < -0.1) {
      hints.push('NDMI значительно ниже нуля — возможен дефицит влаги. Проверить полив и влажность почвы.');
    } else if (ndmiValue < 0) {
      hints.push('NDMI отрицательный — поверхность сухая. Рекомендуется проверить влагообеспеченность.');
    }
  } else if (!noneHaveData) {
    hints.push('Нет данных NDMI — проверить полив и влажность почвы при осмотре.');
  }

  // Vegetation indices sharp change — cross-check with field visit
  const vegValues = [ndviValue, saviValue, eviValue].filter(v => v != null);
  if (vegValues.length >= 2) {
    const maxVeg = Math.max(...vegValues);
    const minVeg = Math.min(...vegValues);
    if (maxVeg - minVeg > 0.3) {
      hints.push('Значительный разброс между вегетационными индексами. Сравнить с последним осмотром поля.');
    }
  }

  // NDRE — chlorophyll check
  if (ndreValue != null && ndreValue < 0.1) {
    hints.push('NDRE пониженный — возможен дефицит азота. Рекомендуется проверить подкормки.');
  }

  // Coverage-based hints
  if (coverageStatus === 'none' || noneHaveData) {
    hints.push('Дождаться следующего спутникового снимка — данных пока нет.');
  } else if (coverageStatus === 'partial') {
    hints.push('Часть индексов отсутствует. Проверить проблемные зоны на карте.');
  }

  // Freshness hint
  const latestDate = coverage?.latest_captured_date || null;
  const days = latestDate ? daysSince(latestDate) : null;
  if (days !== null && days > 10) {
    hints.push('Данные устарели. Дождаться следующего спутникового снимка.');
  }

  // General cross-check hint
  if (!noneHaveData) {
    hints.push('Сравнить спутниковые данные с визуальным осмотром агронома.');
  }

  if (hints.length === 0) return null;

  return (
    <div className="card bg-agro-surface2/40 border-l-4 border-agro-accent">
      <h3 className="text-xs font-semibold text-agro-muted uppercase tracking-wide mb-2.5">
        Что проверить агроному
      </h3>
      <ul className="space-y-2">
        {hints.map((hint, i) => (
          <li key={i} className="text-xs text-agro-text flex items-start gap-2">
            <span className="text-agro-accent mt-0.5 flex-shrink-0">→</span>
            <span>{hint}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ─── Header Section ───────────────────────────────────────────────────────────

function AnalyticsHeader({ field, coverage, perIndexState }) {
  if (!field) return null;
  const p = field.properties || field;

  const latestDate = useMemo(() => {
    const dates = [];
    if (coverage?.latest_captured_date) dates.push(coverage.latest_captured_date);
    Object.values(perIndexState).forEach(s => {
      if (s?.record?.captured_date) dates.push(s.record.captured_date);
    });
    if (dates.length === 0) return null;
    dates.sort();
    return dates[dates.length - 1];
  }, [coverage, perIndexState]);

  const coverageCfg = getCoverageStatusConfig(coverage?.coverage_status);

  return (
    <div className="card !p-4 print:border print:border-gray-300">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-bold text-agro-text truncate">{p.name || '—'}</h2>
          <div className="flex flex-wrap gap-x-4 gap-y-1 mt-1 text-xs text-agro-muted">
            {p.code && <span>Код: {p.code}</span>}
            {p.enterprise_name && <span>Предприятие: {p.enterprise_name}</span>}
            {p.area_ha != null && <span>Площадь: {Number(p.area_ha).toFixed(1)} га</span>}
            {p.current_crop && <span>Культура: {p.current_crop}</span>}
            {p.irrigation_type && <span>Орошение: {irrigationLabel(p.irrigation_type)}</span>}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs">
          {latestDate && (
            <span className="text-agro-muted">
              Последний снимок: {formatDate(latestDate)}
            </span>
          )}
          <span
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium"
            style={{ backgroundColor: coverageCfg.bgColor, color: coverageCfg.textColor }}
          >
            {coverageCfg.icon} {coverageCfg.label}
          </span>
        </div>
      </div>
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function FieldAnalyticsWorkspace({ fieldId, onBack }) {
  const [field, setField] = useState(null);
  const [coverage, setCoverage] = useState(null);
  const [ndviLatest, setNdviLatest] = useState(null);
  const [ndviHistory, setNdviHistory] = useState([]);
  const [multiLatest, setMultiLatest] = useState({});
  const [multiHistory, setMultiHistory] = useState({});
  const [multiRecordCount, setMultiRecordCount] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [dataLoaded, setDataLoaded] = useState(false);

  // Chart state
  const [visibleIndices, setVisibleIndices] = useState(() => new Set(ALL_INDEX_CODES));
  const [dayRange, setDayRange] = useState(90);
  const [deepDiveIndex, setDeepDiveIndex] = useState('ndvi');

  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const loadData = useCallback(async () => {
    if (!fieldId) return;
    setLoading(true);
    setError(null);

    const fid = typeof fieldId === 'string' ? parseInt(fieldId) : fieldId;

    try {
      // Fetch field + NDVI latest from field properties + coverage in parallel
      const [fieldData, coverageRes] = await Promise.allSettled([
        getField(fid),
        getSatelliteCoverage({ field_ids: [fid] }),
      ]);

      if (!mountedRef.current) return;

      // Field
      if (fieldData.status === 'fulfilled') {
        setField(fieldData.value);
      } else {
        setError('Ошибка загрузки данных поля');
        setLoading(false);
        return;
      }

      // NDVI latest via legacy API
      try {
        const ndviData = await getNDVIHistory(fid, dayRange);
        if (mountedRef.current) {
          const records = Array.isArray(ndviData?.records) ? ndviData.records : [];
          setNdviHistory(
            records.map(r => ({
              captured_date: r.captured_date,
              mean_ndvi: r.mean_ndvi,
              min_ndvi: r.min_ndvi,
              max_ndvi: r.max_ndvi,
              cloud_cover_pct: r.cloud_cover_pct,
              valid_pixels_pct: r.valid_pixels_pct,
            })).sort((a, b) => a.captured_date.localeCompare(b.captured_date))
          );

          // Latest NDVI from history if available
          if (records.length > 0) {
            const sorted = [...records].sort((a, b) => b.captured_date.localeCompare(a.captured_date));
            setNdviLatest(sorted[0]);
          }
        }
      } catch {
        if (mountedRef.current) {
          setNdviHistory([]);
          setNdviLatest(null);
        }
      }

      // Coverage
      if (coverageRes.status === 'fulfilled' && coverageRes.value?.fields?.length > 0) {
        setCoverage(coverageRes.value.fields[0]);
      } else {
        setCoverage(null);
      }

      // Fetch satellite indices (savi, evi, ndmi, ndre)
      const satelliteCodes = SATELLITE_INDEX_CODES;
      const latestResults = await Promise.allSettled(
        satelliteCodes.map(code =>
          getSatelliteIndexLatest(fid, code, { includeCloudy: false })
        )
      );
      const historyResults = await Promise.allSettled(
        satelliteCodes.map(code =>
          getSatelliteIndexHistory(fid, code, { days: dayRange, includeCloudy: false })
        )
      );

      if (!mountedRef.current) return;

      const latestMap = {};
      const historyMap = {};
      const recordCountMap = {};

      satelliteCodes.forEach((code, i) => {
        const lr = latestResults[i];
        latestMap[code] = (lr.status === 'fulfilled' && lr.value?.record) ? lr.value.record : null;

        const hr = historyResults[i];
        if (hr.status === 'fulfilled' && Array.isArray(hr.value?.records)) {
          historyMap[code] = hr.value.records.map(r => ({
            captured_date: r.captured_date,
            mean_value: r.mean_value,
            min_value: r.min_value,
            max_value: r.max_value,
            cloud_cover_pct: r.cloud_cover_pct,
            valid_pixels_pct: r.valid_pixels_pct,
          })).sort((a, b) => a.captured_date.localeCompare(b.captured_date));
          recordCountMap[code] = hr.value.records.length;
        } else {
          historyMap[code] = [];
          recordCountMap[code] = 0;
        }
      });

      setMultiLatest(latestMap);
      setMultiHistory(historyMap);
      setMultiRecordCount(recordCountMap);
      setDataLoaded(true);
      setLoading(false);
    } catch (err) {
      if (mountedRef.current) {
        setError('Ошибка загрузки спутниковых данных');
        setLoading(false);
        console.error(err);
      }
    }
  }, [fieldId, dayRange]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Build per-index state for child components
  const perIndexState = useMemo(() => {
    const state = {};

    // NDVI
    state.ndvi = {
      record: ndviLatest || null,
      loading: loading && !dataLoaded,
      error: !!error,
      recordCount: ndviHistory.length > 0 ? ndviHistory.length : null,
    };

    // Satellite indices
    SATELLITE_INDEX_CODES.forEach(code => {
      state[code] = {
        record: multiLatest[code] || null,
        loading: loading && !dataLoaded,
        error: !!error,
        recordCount: multiRecordCount[code] ?? null,
      };
    });

    return state;
  }, [ndviLatest, multiLatest, multiRecordCount, loading, dataLoaded, error]);

  // Merge chart data: combine NDVI history + multi history
  const chartDataByIndex = useMemo(() => {
    const data = {};

    // NDVI
    data.ndvi = ndviHistory.map(r => ({
      date: r.captured_date,
      value: r.mean_ndvi,
      min: r.min_ndvi,
      max: r.max_ndvi,
    }));

    // Satellite indices
    SATELLITE_INDEX_CODES.forEach(code => {
      data[code] = (multiHistory[code] || []).map(r => ({
        date: r.captured_date,
        value: r.mean_value,
        min: r.min_value,
        max: r.max_value,
      }));
    });

    return data;
  }, [ndviHistory, multiHistory]);

  // Toggle visible indices for comparison chart
  const toggleIndex = useCallback((code) => {
    setVisibleIndices(prev => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }, []);

  // Print handler
  const handlePrint = useCallback(() => {
    window.print();
  }, []);

  // Copy text summary
  const handleCopyReport = useCallback(() => {
    if (!field) return;
    const p = field.properties || field;
    const lines = [
      `АгроСпутник — Аналитика поля: ${p.name || '—'}`,
      `Код: ${p.code || '—'}  Предприятие: ${p.enterprise_name || '—'}`,
      `Площадь: ${p.area_ha != null ? Number(p.area_ha).toFixed(1) + ' га' : '—'}  Культура: ${p.current_crop || '—'}`,
      '',
      'Текущие значения индексов:',
    ];

    ALL_INDEX_CODES.forEach(code => {
      const meta = getIndexMetadata(code);
      const state = perIndexState[code];
      const value = state?.record?.mean_value ?? state?.record?.mean_ndvi ?? null;
      const date = state?.record?.captured_date || '—';
      lines.push(`  ${meta?.label || code.toUpperCase()}: ${value != null ? value.toFixed(meta?.precision ?? 4) : 'Нет данных'} (${date})`);
    });

    lines.push('', 'Сформировано AgroSat');

    navigator.clipboard.writeText(lines.join('\n')).catch(() => {
      // Clipboard not available — silent
    });
  }, [field, perIndexState]);

  // ==================== RENDER ====================

  // No field selected
  if (!fieldId) {
    return <EmptyState message="Поле не выбрано" subtext="Выберите поле на карте или в списке" />;
  }

  // Loading
  if (loading && !dataLoaded) {
    return <LoadingSkeleton />;
  }

  // Error
  if (error && !dataLoaded) {
    return <ErrorState message={error} onRetry={loadData} />;
  }

  const hasAnyData = ALL_INDEX_CODES.some(code => perIndexState[code]?.record != null);

  return (
    <div className="h-full flex flex-col print:bg-white">
      {/* Sticky top bar */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-agro-surface2 bg-agro-surface print:bg-white print:border-gray-300 print:hidden">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-agro-muted hover:text-agro-text transition-colors text-sm"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          Назад
        </button>
        <div className="flex items-center gap-2">
          <button
            onClick={handleCopyReport}
            className="px-3 py-1.5 text-xs font-medium rounded-lg bg-agro-surface2 text-agro-muted hover:text-agro-text transition-colors"
            title="Копировать краткий отчёт"
          >
            📋 Копировать отчёт
          </button>
          <button
            onClick={handlePrint}
            className="px-3 py-1.5 text-xs font-medium rounded-lg bg-agro-surface2 text-agro-muted hover:text-agro-text transition-colors"
            title="Печать / PDF"
          >
            🖨️ Печать / PDF
          </button>
        </div>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-5 print:overflow-visible print:p-4">
        {/* A. Header */}
        <AnalyticsHeader field={field} coverage={coverage} perIndexState={perIndexState} />

        {/* B. Latest index overview */}
        <IndexCardsRow perIndexState={perIndexState} ndviHistory={ndviHistory} multiHistory={multiHistory} />

        {/* C. Multi-index comparison chart */}
        <FieldAnalyticsCharts
          chartDataByIndex={chartDataByIndex}
          visibleIndices={visibleIndices}
          onToggleIndex={toggleIndex}
          dayRange={dayRange}
          onDayRangeChange={setDayRange}
          deepDiveIndex={deepDiveIndex}
          onDeepDiveIndexChange={setDeepDiveIndex}
          loading={loading && !dataLoaded}
          hasAnyData={hasAnyData}
        />

        {/* D. Single-index deep dive */}
        {deepDiveIndex && (
          <SingleIndexDeepDive
            indexCode={deepDiveIndex}
            perIndexState={perIndexState}
            chartData={chartDataByIndex[deepDiveIndex] || []}
          />
        )}

        {/* E. Latest values table */}
        <LatestValuesTable perIndexState={perIndexState} />

        {/* F. Operational hints */}
        <OperationalHints perIndexState={perIndexState} coverage={coverage} />

        {/* G. Data quality / coverage */}
        <DataQualityPanel coverage={coverage} perIndexState={perIndexState} />

        {/* No data fallback */}
        {!hasAnyData && dataLoaded && (
          <EmptyState
            message="Нет спутниковых данных"
            subtext="Для этого поля пока нет записей спутниковых индексов"
          />
        )}
      </div>
    </div>
  );
}

// ─── Single Index Deep Dive ───────────────────────────────────────────────────

function SingleIndexDeepDive({ indexCode, perIndexState, chartData }) {
  const meta = getIndexMetadata(indexCode);
  const state = perIndexState[indexCode];
  const record = state?.record;
  const value = record?.mean_value ?? record?.mean_ndvi ?? null;
  const date = record?.captured_date || null;
  const valueColor = value != null ? getIndexColor(value, indexCode) : '#9ca3af';

  const recordCount = state?.recordCount ?? chartData.length;

  // Compute min/max from chart data
  let minVal = null, maxVal = null;
  if (chartData.length > 0) {
    const values = chartData.map(d => d.value).filter(v => v != null);
    if (values.length > 0) {
      minVal = Math.min(...values);
      maxVal = Math.max(...values);
    }
  }

  // Trend description
  let trendText = 'Недостаточно данных';
  if (chartData.length >= 3) {
    const recent = chartData.slice(-3).filter(d => d.value != null);
    if (recent.length >= 2) {
      const first = recent[0].value;
      const last = recent[recent.length - 1].value;
      const diff = last - first;
      if (diff > 0.03) trendText = 'Уверенный рост за последние 3 точки';
      else if (diff > 0.01) trendText = 'Небольшой рост';
      else if (diff < -0.03) trendText = 'Снижение за последние 3 точки';
      else if (diff < -0.01) trendText = 'Небольшое снижение';
      else trendText = 'Стабильно';
    }
  }

  // Interpretation
  let interpretation = '—';
  if (value != null && meta) {
    if (indexCode === 'ndvi') {
      if (value >= 0.6) interpretation = 'Густая зелёная биомасса. Хорошее состояние посевов.';
      else if (value >= 0.3) interpretation = 'Умеренная вегетация. Соответствует большинству культур в середине сезона.';
      else if (value >= 0.15) interpretation = 'Разреженный покров или стресс. Требуется проверка.';
      else interpretation = 'Открытая почва или отсутствие вегетации.';
    } else if (indexCode === 'ndmi') {
      if (value >= 0.3) interpretation = 'Высокая влажность. Достаточное увлажнение.';
      else if (value >= 0) interpretation = 'Умеренная влажность.';
      else if (value >= -0.1) interpretation = 'Пониженная влажность. Возможен дефицит влаги.';
      else interpretation = 'Дефицит влаги. Требуется проверка полива.';
    } else if (indexCode === 'ndre') {
      if (value >= 0.3) interpretation = 'Высокое содержание хлорофилла. Хорошее азотное питание.';
      else if (value >= 0.15) interpretation = 'Умеренный хлорофилл.';
      else interpretation = 'Пониженный хлорофилл. Возможен дефицит азота.';
    } else {
      if (value >= 0.5) interpretation = 'Высокое значение.';
      else if (value >= 0.2) interpretation = 'Умеренное значение.';
      else interpretation = 'Низкое значение.';
    }
  }

  return (
    <div className="card">
      <h3 className="text-xs font-semibold text-agro-muted uppercase tracking-wide mb-3">
        {meta?.label} — детальный анализ
      </h3>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
        <div>
          <p className="text-[10px] text-agro-muted">Текущее значение</p>
          <p className="text-lg font-bold" style={{ color: valueColor }}>
            {value != null ? value.toFixed(meta?.precision ?? 4) : '—'}
          </p>
          {date && <p className="text-[10px] text-agro-muted">{formatDate(date)}</p>}
        </div>
        <div>
          <p className="text-[10px] text-agro-muted">Мин / Макс (история)</p>
          <p className="text-sm font-medium text-agro-text">
            {minVal != null ? minVal.toFixed(4) : '—'} / {maxVal != null ? maxVal.toFixed(4) : '—'}
          </p>
        </div>
        <div>
          <p className="text-[10px] text-agro-muted">Записей / Дат</p>
          <p className="text-sm font-medium text-agro-text">
            {recordCount} / {chartData.length}
          </p>
        </div>
        <div>
          <p className="text-[10px] text-agro-muted">Тренд</p>
          <p className="text-sm font-medium text-agro-text">{trendText}</p>
        </div>
      </div>

      <div className="bg-agro-surface2/40 rounded-lg p-3 text-xs text-agro-text leading-relaxed">
        <p className="font-medium text-agro-muted mb-1">Интерпретация:</p>
        <p>{interpretation}</p>
      </div>

      {record && (
        <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          {record.cloud_cover_pct != null && (
            <div className="bg-agro-surface2/30 rounded p-2">
              <span className="text-agro-muted">Облачность:</span>{' '}
              <span className="text-agro-text">{record.cloud_cover_pct.toFixed(1)}%</span>
            </div>
          )}
          {record.valid_pixels_pct != null && (
            <div className="bg-agro-surface2/30 rounded p-2">
              <span className="text-agro-muted">Пиксели:</span>{' '}
              <span className="text-agro-text">{record.valid_pixels_pct.toFixed(1)}%</span>
            </div>
          )}
          {record.min_value != null && (
            <div className="bg-agro-surface2/30 rounded p-2">
              <span className="text-agro-muted">Min:</span>{' '}
              <span className="text-agro-text">{record.min_value.toFixed(4)}</span>
            </div>
          )}
          {record.max_value != null && (
            <div className="bg-agro-surface2/30 rounded p-2">
              <span className="text-agro-muted">Max:</span>{' '}
              <span className="text-agro-text">{record.max_value.toFixed(4)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
