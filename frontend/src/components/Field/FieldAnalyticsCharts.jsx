import { useMemo } from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts';
import { ALL_INDEX_CODES, getIndexMetadata, getIndexColor } from '../../config/indexMetadata';

// ─── Chart color palette ──────────────────────────────────────────────────────
const CHART_COLORS = {
  ndvi: '#16a34a',
  savi: '#8b5cf6',
  evi: '#f59e0b',
  ndmi: '#2563eb',
  ndre: '#ec4899',
};

// ─── Tooltip ──────────────────────────────────────────────────────────────────

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="bg-white rounded-lg shadow-lg border border-gray-200 p-3 text-xs">
      <p className="font-medium text-gray-700 mb-1.5">Дата: {label}</p>
      <div className="space-y-1">
        {payload.map((entry) => {
          const meta = getIndexMetadata(entry.name);
          return (
            <div key={entry.name} className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: entry.color }} />
              <span className="text-gray-600">{meta?.label || entry.name}:</span>
              <span className="font-medium text-gray-900">
                {entry.value != null ? Number(entry.value).toFixed(4) : '—'}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Comparison Chart ─────────────────────────────────────────────────────────

function ComparisonChart({ chartDataByIndex, visibleIndices }) {
  // Merge data from multiple indices into a single time series
  const mergedData = useMemo(() => {
    if (!chartDataByIndex) return [];

    // Collect all unique dates across all visible indices
    const dateMap = {};

    ALL_INDEX_CODES.forEach(code => {
      if (!visibleIndices.has(code)) return;
      const records = chartDataByIndex[code] || [];
      records.forEach(r => {
        if (!r.date) return;
        if (!dateMap[r.date]) {
          dateMap[r.date] = { date: r.date };
        }
        dateMap[r.date][code] = r.value;
      });
    });

    return Object.values(dateMap).sort((a, b) => a.date.localeCompare(b.date));
  }, [chartDataByIndex, visibleIndices]);

  if (mergedData.length === 0) {
    return (
      <div className="card text-center py-10">
        <div className="text-2xl mb-2">📊</div>
        <p className="text-sm text-agro-muted mb-1">Нет данных для сравнения</p>
        <p className="text-xs text-agro-muted">Выберите хотя бы один индекс для отображения</p>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={mergedData} margin={{ top: 10, right: 15, bottom: 5, left: -10 }}>
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
              domain={['auto', 'auto']}
              tick={{ fontSize: 10, fill: '#9ca3af' }}
              tickCount={6}
            />
            <Tooltip content={<CustomTooltip />} />
            <Legend
              wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
            />
            {ALL_INDEX_CODES.map(code => {
              if (!visibleIndices.has(code)) return null;
              const meta = getIndexMetadata(code);
              return (
                <Line
                  key={code}
                  type="monotone"
                  dataKey={code}
                  name={code}
                  stroke={CHART_COLORS[code] || '#6b7280'}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 3, strokeWidth: 1 }}
                  connectNulls={false}
                />
              );
            })}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ─── Deep Dive Chart (single index) ───────────────────────────────────────────

function DeepDiveChart({ data, indexCode }) {
  const meta = getIndexMetadata(indexCode);
  const color = CHART_COLORS[indexCode] || '#8b5cf6';

  if (!data || data.length === 0) {
    return (
      <div className="card text-center py-8">
        <p className="text-sm text-agro-muted">Нет исторических данных</p>
      </div>
    );
  }

  const isNDVI = indexCode === 'ndvi';
  const yDomain = isNDVI ? [0, 1] : ['auto', 'auto'];

  return (
    <div className="card">
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 10, right: 15, bottom: 5, left: -10 }}>
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
              formatter={(value) => [Number(value).toFixed(4), meta?.label]}
              labelFormatter={(d) => `Дата: ${d}`}
            />
            <Line
              type="monotone"
              dataKey="value"
              name={indexCode}
              stroke={color}
              strokeWidth={2}
              dot={{ r: 2.5, fill: color }}
              activeDot={{ r: 4, strokeWidth: 2 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ─── Index Toggle Chips ───────────────────────────────────────────────────────

function IndexToggleChips({ visibleIndices, onToggle }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-agro-muted mr-1">Индексы:</span>
      {ALL_INDEX_CODES.map(code => {
        const meta = getIndexMetadata(code);
        const isVisible = visibleIndices.has(code);
        const color = CHART_COLORS[code] || '#6b7280';
        return (
          <button
            key={code}
            onClick={() => onToggle(code)}
            className={`inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-full transition-colors ${
              isVisible
                ? 'text-white'
                : 'bg-agro-surface2 text-agro-muted hover:text-agro-text'
            }`}
            style={isVisible ? { backgroundColor: color } : {}}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${isVisible ? 'bg-white/60' : ''}`}
              style={!isVisible ? { backgroundColor: color } : {}}
            />
            {meta?.label || code.toUpperCase()}
          </button>
        );
      })}
    </div>
  );
}

// ─── Day Range Selector ───────────────────────────────────────────────────────

function DayRangeSelector({ dayRange, onDayRangeChange }) {
  const ranges = [30, 60, 90, 180];
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs text-agro-muted mr-1">Период:</span>
      {ranges.map(d => (
        <button
          key={d}
          onClick={() => onDayRangeChange(d)}
          className={`px-2 py-1 text-xs font-medium rounded-md transition-colors ${
            dayRange === d
              ? 'bg-agro-accent text-white'
              : 'bg-agro-surface2 text-agro-muted hover:text-agro-text'
          }`}
        >
          {d} дн.
        </button>
      ))}
    </div>
  );
}

// ─── Deep Dive Selector ───────────────────────────────────────────────────────

function DeepDiveSelector({ activeIndex, onSelect, perIndexState }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-agro-muted mr-1">Детальный индекс:</span>
      {ALL_INDEX_CODES.map(code => {
        const meta = getIndexMetadata(code);
        const isActive = activeIndex === code;
        const hasData = perIndexState?.[code]?.record != null;
        return (
          <button
            key={code}
            onClick={() => onSelect(code)}
            className={`px-2.5 py-1 text-xs font-medium rounded-lg transition-colors ${
              isActive
                ? 'bg-agro-accent text-white shadow-sm'
                : hasData
                  ? 'bg-agro-surface2 text-agro-text hover:bg-agro-surface2/80'
                  : 'bg-agro-surface2/50 text-agro-muted'
            }`}
          >
            {meta?.label || code.toUpperCase()}
            {hasData && <span className="ml-1 opacity-60">✓</span>}
          </button>
        );
      })}
    </div>
  );
}

// ─── Main Export ──────────────────────────────────────────────────────────────

export default function FieldAnalyticsCharts({
  chartDataByIndex,
  visibleIndices,
  onToggleIndex,
  dayRange,
  onDayRangeChange,
  deepDiveIndex,
  onDeepDiveIndexChange,
  perIndexState,
  loading,
  hasAnyData,
}) {
  if (loading) {
    return (
      <div className="space-y-3">
        <div className="h-5 bg-agro-surface2 rounded w-48 animate-pulse" />
        <div className="h-72 bg-agro-surface2 rounded-lg animate-pulse" />
      </div>
    );
  }

  if (!hasAnyData) {
    return (
      <div className="card text-center py-10">
        <div className="text-2xl mb-2">📊</div>
        <p className="text-sm text-agro-muted">Нет данных для построения графиков</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Section title */}
      <h3 className="text-xs font-semibold text-agro-muted uppercase tracking-wide">
        Сравнение индексов
      </h3>

      {/* Controls row */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <IndexToggleChips visibleIndices={visibleIndices} onToggle={onToggleIndex} />
        <DayRangeSelector dayRange={dayRange} onDayRangeChange={onDayRangeChange} />
      </div>

      {/* Comparison chart */}
      <ComparisonChart chartDataByIndex={chartDataByIndex} visibleIndices={visibleIndices} />

      {/* Deep dive section */}
      <div className="space-y-2 pt-2">
        <h3 className="text-xs font-semibold text-agro-muted uppercase tracking-wide">
          Детальный график
        </h3>
        <DeepDiveSelector
          activeIndex={deepDiveIndex}
          onSelect={onDeepDiveIndexChange}
          perIndexState={perIndexState}
        />
        <DeepDiveChart
          data={chartDataByIndex?.[deepDiveIndex] || []}
          indexCode={deepDiveIndex}
        />
      </div>
    </div>
  );
}
