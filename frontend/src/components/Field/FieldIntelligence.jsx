import { useState, useEffect, useRef, useMemo } from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
} from 'recharts';
import {
  getSatelliteIndexLatest,
  getSatelliteIndexHistory,
  getLatestNDVI,
} from '../../api/client';
import apiClient from '../../api/client';
import INDEX_METADATA, {
  SATELLITE_INDEX_CODES,
  ALL_INDEX_CODES,
  getIndexMetadata,
  getIndexColor,
} from '../../config/indexMetadata';

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

function getFreshnessBadge(days) {
  if (days === null) return { label: 'Нет данных', className: 'bg-gray-100 text-gray-500' };
  if (days <= 3) return { label: 'Актуально', className: 'bg-green-100 text-green-700' };
  if (days <= 10) return { label: 'Частично', className: 'bg-amber-100 text-amber-700' };
  return { label: 'Устарело', className: 'bg-red-100 text-red-700' };
}

function trendDirection(history) {
  if (!history || history.length < 3) return 'unknown';
  const recent = history.slice(-3).map((r) => r.value ?? r.mean_value ?? r.mean_ndvi);
  if (recent.filter((v) => v != null).length < 2) return 'unknown';
  const first = recent.find((v) => v != null);
  const last = [...recent].reverse().find((v) => v != null);
  if (first == null || last == null) return 'unknown';
  const diff = last - first;
  if (diff > 0.02) return 'increased';
  if (diff < -0.02) return 'decreased';
  return 'stable';
}

const trendLabels = {
  increased: { text: '↑ Рост', className: 'text-green-600' },
  decreased: { text: '↓ Снижение', className: 'text-red-500' },
  stable: { text: '→ Стабильно', className: 'text-gray-500' },
  unknown: { text: '—', className: 'text-gray-400' },
};

// ─── Per-index card ──────────────────────────────────────────────────────────

function IndexCard({ indexCode, latest, loading, error, history }) {
  const meta = getIndexMetadata(indexCode);
  if (!meta) return null;

  const value = latest?.mean_value ?? latest?.mean_ndvi ?? null;
  const date = latest?.captured_date || null;
  const color = getIndexColor(value, indexCode);
  const trend = trendDirection(history);

  return (
    <div className="card p-3 text-xs space-y-1.5 hover:border-agro-accent/30 transition-colors">
      {/* Label + trend */}
      <div className="flex items-center justify-between">
        <div className="font-semibold text-agro-text">{meta.label}</div>
        {!loading && !error && value != null && (
          <div className={`text-[10px] font-medium ${trendLabels[trend].className}`}>
            {trendLabels[trend].text}
          </div>
        )}
      </div>

      {/* Value */}
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
          <div className="text-[11px] text-agro-muted space-y-0.5">
            <div>{formatDate(date)}</div>
            {latest?.valid_pixels_pct != null && (
              <div>Пиксели: {latest.valid_pixels_pct.toFixed(1)}%</div>
            )}
          </div>
        </>
      ) : (
        <div className="text-agro-muted text-[11px]">Нет данных</div>
      )}

      {/* Meaning */}
      <div className="text-[10px] text-agro-muted leading-tight pt-0.5 border-t border-agro-surface2/50 mt-1">
        {meta.shortMeaning}
      </div>
    </div>
  );
}

// ─── Index selector tabs ────────────────────────────────────────────────────

function IndexSelector({ activeIndex, onSelect, perIndexState }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {ALL_INDEX_CODES.map((code) => {
        const meta = getIndexMetadata(code);
        const state = perIndexState[code];
        const hasData = state?.latest?.record?.mean_value != null || state?.latest?.record?.mean_ndvi != null;
        return (
          <button
            key={code}
            onClick={() => onSelect(code)}
            className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
              activeIndex === code
                ? 'bg-agro-accent text-white shadow-sm'
                : hasData
                  ? 'bg-agro-surface2 text-agro-text hover:bg-agro-surface2/80'
                  : 'bg-agro-surface2/50 text-agro-muted'
            }`}
          >
            {meta.label}
            {hasData && <span className="ml-1 opacity-60">✓</span>}
          </button>
        );
      })}
    </div>
  );
}

// ─── Trend chart ─────────────────────────────────────────────────────────────

function TrendChart({ activeIndex, chartData, loading, error, meta }) {
  if (loading) {
    return (
      <div className="card">
        <div className="h-48 flex items-center justify-center">
          <div className="space-y-2 w-full px-4">
            <div className="h-40 bg-agro-surface2 rounded animate-pulse" />
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="card text-center py-8">
        <p className="text-agro-danger text-sm mb-2">Ошибка загрузки истории</p>
        <p className="text-xs text-agro-muted">Не удалось получить данные графика</p>
      </div>
    );
  }

  if (!chartData || chartData.length === 0) {
    return (
      <div className="card text-center py-8">
        <div className="text-2xl mb-2">📊</div>
        <p className="text-sm text-agro-muted mb-1">Нет данных за выбранный период</p>
        <p className="text-xs text-agro-muted">
          Для индекса {meta?.label} пока нет исторических записей
        </p>
      </div>
    );
  }

  const isNDVI = activeIndex === 'ndvi';
  const yDomain = isNDVI ? [0, 1] : ['auto', 'auto'];
  const lineColor = isNDVI ? '#16a34a' : '#8b5cf6';
  const dataKey = isNDVI ? 'ndvi' : 'value';

  return (
    <div className="card">
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 10, right: 10, bottom: 5, left: -15 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: '#9ca3af' }}
              tickFormatter={(d) => {
                const parts = d.split('-');
                return `${parts[2]}/${parts[1]}`;
              }}
              interval="preserveStartEnd"
            />
            <YAxis
              type="number"
              domain={yDomain}
              tick={{ fontSize: 10, fill: '#9ca3af' }}
              tickCount={5}
            />
            <Tooltip
              contentStyle={{
                fontSize: 11,
                borderRadius: 8,
                border: '1px solid #e5e7eb',
                boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
                background: '#fff',
              }}
              formatter={(value) => [value?.toFixed(4), meta?.label]}
              labelFormatter={(d) => `Дата: ${d}`}
            />
            {isNDVI && <ReferenceLine y={0.2} stroke="#f97316" strokeDasharray="3 3" strokeOpacity={0.4} />}
            {isNDVI && <ReferenceLine y={0.5} stroke="#84cc16" strokeDasharray="3 3" strokeOpacity={0.4} />}
            <Line
              type="monotone"
              dataKey={dataKey}
              stroke={lineColor}
              strokeWidth={2}
              dot={{ r: 2.5, fill: lineColor }}
              activeDot={{ r: 4, strokeWidth: 2 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ─── Health summary ──────────────────────────────────────────────────────────

function HealthSummary({ perIndexState }) {
  const available = Object.entries(perIndexState)
    .filter(([code, s]) => s?.latest?.record?.mean_value != null || s?.latest?.record?.mean_ndvi != null)
    .map(([code, s]) => ({
      code,
      meta: getIndexMetadata(code),
      value: s.latest.record.mean_value ?? s.latest.record.mean_ndvi,
    }));

  const coverage =
    available.length === 0
      ? 'no-data'
      : available.length >= 4
        ? 'good'
        : 'partial';

  const statements = [];

  // Vegetation summary
  const vegIndices = available.filter((a) => ['ndvi', 'savi', 'evi'].includes(a.code));
  if (vegIndices.length > 0) {
    const maxVeg = Math.max(...vegIndices.map((a) => a.value));
    if (maxVeg > 0.5) {
      statements.push(
        'Высокие значения вегетационных индексов — активная биомасса. Не является диагнозом урожайности.'
      );
    } else if (maxVeg > 0.2) {
      statements.push(
        'Умеренные значения вегетационных индексов. Рекомендуется сверить с фазой развития культуры.'
      );
    } else {
      statements.push(
        'Низкие значения вегетационных индексов. Возможен разреженный покров, стресс или ранняя стадия вегетации.'
      );
    }
  }

  // Moisture hint
  const ndmiEntry = available.find((a) => a.code === 'ndmi');
  if (ndmiEntry) {
    if (ndmiEntry.value < -0.05) {
      statements.push(
        'NDMI ниже нуля — возможен дефицит влаги или сухая поверхность. Сопоставить с поливом и погодными данными.'
      );
    } else if (ndmiEntry.value < 0.1) {
      statements.push(
        'NDMI около нуля — влажность поверхности пониженная. Требуется контроль влагообеспеченности.'
      );
    } else if (ndmiEntry.value > 0.3) {
      statements.push('NDMI высокий — достаточное увлажнение поверхности.');
    }
  }

  // Chlorophyll hint
  const ndreEntry = available.find((a) => a.code === 'ndre');
  if (ndreEntry) {
    if (ndreEntry.value > 0.3) {
      statements.push(
        'NDRE высокий — хорошее содержание хлорофилла, вероятно достаточное азотное питание.'
      );
    } else if (ndreEntry.value < 0.1) {
      statements.push(
        'NDRE пониженный — возможен дефицит азота или стресс. Рекомендуется проверить подкормки.'
      );
    }
  }

  return (
    <div className="card">
      <h4 className="text-xs font-semibold text-agro-muted uppercase tracking-wide mb-2">
        Сводка по полю
      </h4>

      {/* Coverage badge */}
      <div className="flex flex-wrap gap-1.5 mb-2">
        {coverage === 'no-data' && (
          <span className="inline-block px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 text-[10px] font-medium">
            Данных недостаточно
          </span>
        )}
        {coverage === 'partial' && (
          <span className="inline-block px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 text-[10px] font-medium">
            Есть данные по части индексов
          </span>
        )}
        {coverage === 'good' && (
          <span className="inline-block px-2 py-0.5 rounded-full bg-green-100 text-green-700 text-[10px] font-medium">
            Достаточно данных для оценки
          </span>
        )}
      </div>

      {/* Statements */}
      {statements.length === 0 ? (
        <p className="text-sm text-agro-muted">Данных недостаточно для вывода.</p>
      ) : (
        <ul className="space-y-1.5">
          {statements.map((s, i) => (
            <li key={i} className="text-xs text-agro-text leading-relaxed pl-3 relative">
              <span className="absolute left-0 top-1 text-agro-muted">•</span>
              {s}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ─── Operational hints ──────────────────────────────────────────────────────

function OperationalHints({ perIndexState, freshnessDays }) {
  const hints = [];

  if (freshnessDays === null) {
    hints.push('Дождаться первого спутникового снимка.');
  } else {
    if (freshnessDays > 7) {
      hints.push('Дождаться следующего спутникового снимка — данные устарели.');
    }

    const ndviState = perIndexState?.ndvi;
    const ndvi = ndviState?.latest?.record?.mean_ndvi ?? null;
    if (ndvi != null && ndvi < 0.2) {
      hints.push('Проверить проблемные зоны на карте.');
    }

    const ndmiEntry = Object.entries(perIndexState).find(
      ([code, s]) => code === 'ndmi' && s?.latest?.record?.mean_value != null
    );
    if (ndmiEntry && ndmiEntry[1].latest.record.mean_value < -0.05) {
      hints.push('Проверить полив.');
    }

    hints.push('Сравнить с последним осмотром агронома.');
  }

  if (hints.length === 0) return null;

  return (
    <div className="card bg-agro-surface2/50">
      <h4 className="text-xs font-semibold text-agro-muted uppercase tracking-wide mb-2">
        Рекомендации
      </h4>
      <ul className="space-y-1">
        {hints.slice(0, 4).map((hint, i) => (
          <li key={i} className="text-xs text-agro-text flex items-start gap-1.5">
            <span className="text-agro-muted mt-0.5">→</span>
            {hint}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ─── Data freshness badge ───────────────────────────────────────────────────

function FreshnessBadge({ days }) {
  const badge = getFreshnessBadge(days);
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium ${badge.className}`}>
      <span className="w-1.5 h-1.5 rounded-full currentColor opacity-60" />
      {badge.label}
    </span>
  );
}

// ─── Field info header ──────────────────────────────────────────────────────

function FieldInfoHeader({ field, freshnessDays }) {
  if (!field) return null;
  const badge = getFreshnessBadge(freshnessDays);

  return (
    <div className="space-y-2 mb-3">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-agro-text">
            {field.name || '—'}
          </h3>
          {field.code && (
            <p className="text-xs text-agro-muted">Код: {field.code}</p>
          )}
        </div>
        <FreshnessBadge days={freshnessDays} />
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-agro-muted">
        {field.enterprise_name && <span>Предприятие: {field.enterprise_name}</span>}
        {field.area_ha != null && <span>Площадь: {Number(field.area_ha).toFixed(1)} га</span>}
        {field.current_crop && <span>Культура: {field.current_crop}</span>}
      </div>
    </div>
  );
}

// ─── Main component ──────────────────────────────────────────────────────────

export default function FieldIntelligence({ field, fieldId }) {
  const [activeIndex, setActiveIndex] = useState('ndvi');
  const [dayRange, setDayRange] = useState(90);
  const [ndviLatest, setNdviLatest] = useState(null);
  const [ndviHistory, setNdviHistory] = useState([]);
  const [multiLatest, setMultiLatest] = useState({});
  const [multiHistory, setMultiHistory] = useState({});
  const [multiLoading, setMultiLoading] = useState({});
  const [multiError, setMultiError] = useState({});
  const [initialLoading, setInitialLoading] = useState(true);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Reset when field changes
  useEffect(() => {
    setActiveIndex('ndvi');
    setDayRange(90);
  }, [fieldId]);

  // Fetch NDVI latest
  useEffect(() => {
    if (!fieldId) return;
    getLatestNDVI(fieldId)
      .then((res) => {
        if (mountedRef.current) {
          setNdviLatest(res?.record ?? null);
        }
      })
      .catch(() => {
        if (mountedRef.current) setNdviLatest(null);
      });
  }, [fieldId]);

  // Fetch all satellite indices latest (savi, evi, ndmi, ndre)
  useEffect(() => {
    if (!fieldId) return;
    let cancelled = false;

    setInitialLoading(true);
    SATELLITE_INDEX_CODES.forEach((code) => {
      if (mountedRef.current) {
        setMultiLoading((prev) => ({ ...prev, [code]: true }));
        setMultiError((prev) => ({ ...prev, [code]: null }));
      }
    });

    Promise.allSettled(
      SATELLITE_INDEX_CODES.map((code) =>
        getSatelliteIndexLatest(fieldId, code, { includeCloudy: false })
      )
    ).then((results) => {
      if (cancelled) return;
      const latestMap = {};
      SATELLITE_INDEX_CODES.forEach((code, i) => {
        const res = results[i];
        if (res.status === 'fulfilled' && res.value?.record) {
          latestMap[code] = res.value;
        } else {
          latestMap[code] = null;
        }
        if (mountedRef.current) {
          setMultiLoading((prev) => ({ ...prev, [code]: false }));
          if (res.status === 'rejected') {
            setMultiError((prev) => ({ ...prev, [code]: 'Ошибка' }));
          }
        }
      });
      if (mountedRef.current) {
        setMultiLatest(latestMap);
        setInitialLoading(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [fieldId]);

  // Fetch history for active index
  useEffect(() => {
    if (!fieldId) return;
    let cancelled = false;

    if (activeIndex === 'ndvi') {
      // Legacy NDVI via API client
      apiClient
        .get(`/api/ndvi/${fieldId}/history?days=${dayRange}`)
        .then((res) => {
          if (cancelled) return;
          const records = res.data?.records || [];
          setNdviHistory(
            records
              .map((r) => ({
                date: r.captured_date,
                ndvi: r.mean_ndvi,
                min: r.min_ndvi,
                max: r.max_ndvi,
                cloud: r.cloud_cover_pct,
              }))
              .sort((a, b) => a.date.localeCompare(b.date))
          );
        })
        .catch(() => {
          if (!cancelled) setNdviHistory([]);
        });
    } else {
      // Satellite index history
      setMultiHistory((prev) => ({ ...prev, [activeIndex]: [] }));
      getSatelliteIndexHistory(fieldId, activeIndex, { days: dayRange, includeCloudy: false })
        .then((data) => {
          if (cancelled) return;
          const records = data?.records || [];
          setMultiHistory((prev) => ({
            ...prev,
            [activeIndex]: records
              .map((r) => ({
                date: r.captured_date,
                value: r.mean_value,
                min: r.min_value,
                max: r.max_value,
                cloud: r.cloud_cover_pct,
              }))
              .sort((a, b) => a.date.localeCompare(b.date)),
          }));
        })
        .catch(() => {
          if (!cancelled) setMultiHistory((prev) => ({ ...prev, [activeIndex]: [] }));
        });
    }

    return () => {
      cancelled = true;
    };
  }, [fieldId, activeIndex, dayRange]);

  // Compute per-index state for cards
  const perIndexState = useMemo(() => {
    const state = {};
    // NDVI
    state.ndvi = {
      latest: ndviLatest ? { record: ndviLatest } : null,
      loading: initialLoading,
      error: false,
    };
    // SAVI, EVI, NDMI, NDRE
    SATELLITE_INDEX_CODES.forEach((code) => {
      state[code] = {
        latest: multiLatest[code],
        loading: multiLoading[code] ?? false,
        error: multiError[code] ?? false,
      };
    });
    return state;
  }, [ndviLatest, multiLatest, multiLoading, multiError, initialLoading]);

  // Compute chart data for active index
  const chartData = useMemo(() => {
    if (activeIndex === 'ndvi') return ndviHistory;
    return multiHistory[activeIndex] || [];
  }, [activeIndex, ndviHistory, multiHistory]);

  // Compute latest available date across all indices
  const latestDate = useMemo(() => {
    const dates = [];
    Object.values(perIndexState).forEach((s) => {
      const d = s?.latest?.record?.captured_date;
      if (d) dates.push(d);
    });
    if (dates.length === 0) return null;
    dates.sort();
    return dates[dates.length - 1];
  }, [perIndexState]);

  const freshnessDays = latestDate ? daysSince(latestDate) : null;

  // Active index metadata
  const activeMeta = getIndexMetadata(activeIndex);
  const activeLatestValue =
    activeIndex === 'ndvi'
      ? ndviLatest?.mean_ndvi ?? null
      : multiLatest[activeIndex]?.record?.mean_value ?? null;
  const activeLatestDate =
    activeIndex === 'ndvi'
      ? ndviLatest?.captured_date ?? null
      : multiLatest[activeIndex]?.record?.captured_date ?? null;

  // Loading skeleton for initial load
  if (initialLoading) {
    return (
      <div className="space-y-4 p-1 animate-pulse">
        <div className="h-12 bg-agro-surface2 rounded" />
        <div className="grid grid-cols-2 gap-2">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="h-20 bg-agro-surface2 rounded" />
          ))}
        </div>
        <div className="h-8 bg-agro-surface2 rounded w-64" />
        <div className="h-48 bg-agro-surface2 rounded" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Field header + freshness */}
      <FieldInfoHeader field={field} freshnessDays={freshnessDays} />

      {/* Index cards grid */}
      <div>
        <h4 className="text-xs font-semibold text-agro-muted uppercase tracking-wide mb-2">
          Спутниковые индексы
        </h4>
        <div className="grid grid-cols-2 gap-2">
          {ALL_INDEX_CODES.map((code) => {
            const state = perIndexState[code];
            return (
              <IndexCard
                key={code}
                indexCode={code}
                latest={state?.latest?.record ?? null}
                loading={state?.loading ?? false}
                error={state?.error ?? false}
                history={code === 'ndvi' ? ndviHistory : multiHistory[code]}
              />
            );
          })}
        </div>
      </div>

      {/* Index selector */}
      <div>
        <h4 className="text-xs font-semibold text-agro-muted uppercase tracking-wide mb-2">
          График
        </h4>
        <IndexSelector
          activeIndex={activeIndex}
          onSelect={setActiveIndex}
          perIndexState={perIndexState}
        />
      </div>

      {/* Day range selector */}
      <div className="flex gap-1.5">
        {[30, 60, 90, 180].map((d) => (
          <button
            key={d}
            onClick={() => setDayRange(d)}
            className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
              dayRange === d
                ? 'bg-agro-accent text-white'
                : 'bg-agro-surface2 text-agro-muted hover:text-agro-text'
            }`}
          >
            {d} дн.
          </button>
        ))}
      </div>

      {/* Latest value + chart */}
      {activeLatestValue != null && (
        <div className="card flex items-center justify-between">
          <div>
            <p className="text-xs text-agro-muted">
              Последнее значение {activeMeta?.label}
            </p>
            <p
              className="text-2xl font-bold"
              style={{ color: getIndexColor(activeLatestValue, activeIndex) }}
            >
              {activeLatestValue.toFixed(activeMeta?.precision ?? 4)}
            </p>
            <p className="text-xs text-agro-muted">
              {formatDate(activeLatestDate)}
            </p>
          </div>
          <div className="text-right text-xs text-agro-muted">
            {activeMeta?.shortMeaning}
          </div>
        </div>
      )}
      {activeLatestValue == null && !initialLoading && (
        <div className="card text-center py-4">
          <p className="text-sm text-agro-muted">
            Нет данных для {activeMeta?.label}
          </p>
        </div>
      )}

      {/* Trend chart */}
      <TrendChart
        activeIndex={activeIndex}
        chartData={chartData}
        loading={false}
        error={false}
        meta={activeMeta}
      />

      {/* Health summary */}
      <HealthSummary perIndexState={perIndexState} />

      {/* Operational hints */}
      <OperationalHints perIndexState={perIndexState} freshnessDays={freshnessDays} />
    </div>
  );
}
