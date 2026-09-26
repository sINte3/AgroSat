import { useId, useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import {
  GRANULARITY_LABELS,
  INSPECTION_OPENING_PARTS,
  PERIOD_ACTIVITY,
  PERIOD_SERIES,
} from '../../config/managementAnalytics.js';
import { bucketLabel, formatCount, formatPeriod } from '../../utils/managementAnalytics.js';

// One series at a time, in one hue; the Y axis always starts at zero.
const SERIES_COLOR = '#2a78d6';
const AXIS_INK = '#52675c';
const GRID = '#e0e7e3';

function BucketTooltip({ active, payload, seriesLabel }) {
  const row = active ? payload?.[0]?.payload : null;
  if (!row) return null;
  return (
    <div className="rounded-lg border border-agro-border bg-white px-3 py-2 text-xs shadow-sm">
      <p className="text-base font-semibold text-agro-text">{formatCount(row.value)}</p>
      <p className="text-agro-muted">{seriesLabel}</p>
      <p className="text-agro-muted">{formatPeriod(row.start, row.end)}</p>
    </div>
  );
}

function ActivitySummary({ activity }) {
  const opened = activity.inspections_opened || {};
  return (
    <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3 lg:grid-cols-5" data-testid="management-analytics-activity">
      {PERIOD_ACTIVITY.map(([key, label]) => {
        const value = key === 'inspections_opened' ? opened.total : activity[key];
        return (
          <div key={key} className="min-w-0 border-b border-agro-border/60 pb-1">
            <dt className="text-xs leading-5 text-agro-muted">{label}</dt>
            <dd className="text-lg font-semibold text-agro-text">{formatCount(value)}</dd>
            {key === 'inspections_opened' && (
              <dd className="text-xs leading-5 text-agro-muted">
                {INSPECTION_OPENING_PARTS.map(([part, partLabel], index) => <span key={part}>{index ? ' · ' : ''}{partLabel}: {formatCount(opened[part])}</span>)}
              </dd>
            )}
          </div>
        );
      })}
    </dl>
  );
}

// `granularity` is the one the displayed buckets were built with (the server's
// effective value); `requestedGranularity` is the pending choice.
export default function PeriodDynamics({ activity, periods, granularity, requestedGranularity, onGranularityChange }) {
  const selectId = useId();
  const [seriesKey, setSeriesKey] = useState(PERIOD_SERIES[0].key);
  const series = PERIOD_SERIES.find((item) => item.key === seriesKey) || PERIOD_SERIES[0];
  const rows = useMemo(() => (Array.isArray(periods) ? periods : []).map((bucket) => ({
    label: bucketLabel(bucket, granularity),
    start: bucket.bucket_start,
    end: bucket.bucket_end,
    value: typeof bucket[series.key] === 'number' ? bucket[series.key] : 0,
  })), [granularity, periods, series.key]);
  const hasValues = rows.some((row) => row.value > 0);
  const groups = Array.from(new Set(PERIOD_SERIES.map((item) => item.group)));

  return (
    <article className="min-w-0 rounded-xl border border-agro-border bg-white p-4 xl:col-span-3" aria-labelledby="management-analytics-dynamics-title">
      <h3 id="management-analytics-dynamics-title" className="font-semibold text-agro-text">Поток работ и динамика за период</h3>
      <ActivitySummary activity={activity} />

      <div className="mt-4 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <label htmlFor={selectId} className="block text-sm font-medium text-agro-text">Показатель на графике</label>
          <select id={selectId} data-testid="management-analytics-series" className="input mt-1 min-h-11 w-full max-w-sm" value={series.key} onChange={(event) => setSeriesKey(event.target.value)}>
            {groups.map((group) => (
              <optgroup key={group} label={group}>
                {PERIOD_SERIES.filter((item) => item.group === group).map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
              </optgroup>
            ))}
          </select>
        </div>
        <div role="group" aria-label="Шаг динамики" className="flex overflow-hidden rounded-lg border border-agro-border">
          {GRANULARITY_LABELS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={requestedGranularity === value}
              onClick={() => onGranularityChange(value)}
              className={`min-h-11 px-3 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-inset focus:ring-agro-accent ${requestedGranularity === value ? 'bg-emerald-50 text-agro-accent' : 'bg-white text-agro-text hover:bg-agro-hover'}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <figure className="mt-3">
        <figcaption className="text-xs leading-5 text-agro-muted">
          {series.label} — по интервалам ({GRANULARITY_LABELS.find(([value]) => value === granularity)?.[1].toLowerCase() || granularity}); крайние интервалы обрезаны границами периода.
        </figcaption>
        {rows.length > 0 && hasValues ? (
          <div className="mt-2 h-60 w-full" aria-hidden="true" data-testid="management-analytics-chart">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid vertical={false} stroke={GRID} />
                <XAxis dataKey="label" tick={{ fontSize: 11, fill: AXIS_INK }} tickLine={false} axisLine={{ stroke: GRID }} interval="preserveStartEnd" minTickGap={12} />
                <YAxis allowDecimals={false} domain={[0, 'auto']} tick={{ fontSize: 11, fill: AXIS_INK }} tickLine={false} axisLine={false} width={40} />
                <Tooltip cursor={{ fill: 'rgba(42, 120, 214, 0.08)' }} content={<BucketTooltip seriesLabel={series.label} />} />
                <Bar dataKey="value" name={series.label} fill={SERIES_COLOR} radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="mt-2 rounded-lg bg-slate-50 p-3 text-sm text-agro-muted" data-testid="management-analytics-chart-empty">
            За период значений нет: во всех интервалах — 0.
          </p>
        )}
        <details className="mt-2">
          <summary className="flex min-h-11 cursor-pointer items-center text-sm font-medium text-agro-accent focus:outline-none focus:ring-2 focus:ring-agro-accent">Таблица значений по интервалам</summary>
          <div className="max-h-80 overflow-auto focus:outline-none focus:ring-2 focus:ring-agro-accent" role="region" tabIndex={0} aria-label="Значения по интервалам">
            <table className="w-full text-sm" data-testid="management-analytics-period-table">
              <caption className="sr-only">{series.label} по интервалам периода</caption>
              <thead>
                <tr className="border-b border-agro-border text-left text-xs text-agro-muted">
                  <th scope="col" className="py-2 pr-3 font-medium">Интервал</th>
                  <th scope="col" className="py-2 pl-3 text-right font-medium">{series.label}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.start} className="border-b border-agro-border/60">
                    <th scope="row" className="py-1.5 pr-3 text-left font-normal">{formatPeriod(row.start, row.end)}</th>
                    <td className="py-1.5 pl-3 text-right tabular-nums">{formatCount(row.value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </figure>
    </article>
  );
}
