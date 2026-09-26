import {
  COMPLETION_METRICS,
  CYCLE_TIME_METRICS,
  UNSUPPORTED_CYCLE_TIMES,
  UNVERIFIED_RESOLUTIONS,
  VERIFIED_OUTCOMES,
} from '../../config/managementAnalytics.js';
import {
  formatCount,
  formatExactHours,
  formatHours,
  formatPeriod,
  formatRate,
  formatShare,
} from '../../utils/managementAnalytics.js';
import PeriodDynamics from './PeriodDynamics.jsx';

const METER = '#2a78d6';

function Card({ id, title, children, className = '' }) {
  return (
    <article className={`min-w-0 rounded-xl border border-agro-border bg-white p-4 ${className}`} aria-labelledby={id}>
      <h3 id={id} className="font-semibold text-agro-text">{title}</h3>
      {children}
    </article>
  );
}

// Part-to-whole of the verified outcomes only. The widths are geometry of the
// three server counts; the numbers themselves are in the tiles next to it.
function OutcomeBar({ verified }) {
  if (!(verified.total > 0)) return null;
  return (
    <div className="mt-3 flex h-3 w-full gap-0.5 overflow-hidden rounded" aria-hidden="true">
      {VERIFIED_OUTCOMES.filter((outcome) => verified[outcome.key] > 0).map((outcome) => (
        <div key={outcome.key} className="h-full first:rounded-l last:rounded-r" style={{ width: `${(verified[outcome.key] / verified.total) * 100}%`, backgroundColor: outcome.color }} />
      ))}
    </div>
  );
}

function OutcomesCard({ outcomes, policy }) {
  const verified = outcomes.verified || {};
  const unverified = outcomes.unverified || {};
  const closed = outcomes.closed || {};
  return (
    <Card id="management-analytics-outcomes-title" title="Проверенные результаты мер" className="xl:col-span-2">
      <p className="mt-1 text-xs leading-5 text-agro-muted">
        Циклы планов, решённые за период (закрыты или возвращены после проверки): <strong className="text-agro-text" data-testid="management-analytics-resolved-cycles">{formatCount(outcomes.resolved_cycles)}</strong>.
        Результат — статус спутниковой проверки цикла на момент решения.
      </p>
      <OutcomeBar verified={verified} />
      <dl className="mt-3 grid gap-2 sm:grid-cols-3" aria-label="Проверенные результаты" data-testid="management-analytics-verified-outcomes">
        {VERIFIED_OUTCOMES.map((outcome) => (
          <div key={outcome.key} className="flex flex-col justify-between gap-1 rounded-lg border border-agro-border p-3" data-outcome={outcome.key}>
            <dt className="flex items-center gap-2 text-sm text-agro-muted">
              <span aria-hidden="true" className="h-2.5 w-2.5 flex-none rounded-sm" style={{ backgroundColor: outcome.color }} />
              {outcome.label}
            </dt>
            <dd className="mt-1 text-2xl font-semibold text-agro-text" data-testid={`management-analytics-outcome-${outcome.key}`}>{formatCount(verified[outcome.key])}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-sm text-agro-text">
        Решено с проверенным результатом: <strong data-testid="management-analytics-outcome-total">{formatCount(verified.total)}</strong>
        {verified.total === 0 && <span className="text-agro-muted"> — за период нет циклов с завершённой проверкой.</span>}
      </p>

      <section className="mt-4 rounded-lg bg-slate-50 p-3" aria-labelledby="management-analytics-unverified-title" data-testid="management-analytics-unverified">
        <h4 id="management-analytics-unverified-title" className="text-sm font-semibold text-agro-text">
          Решено без проверенного результата: <span data-testid="management-analytics-unverified-total">{formatCount(unverified.total)}</span>
        </h4>
        <p className="mt-1 text-xs leading-5 text-agro-muted">Не результат мер: проверка не состоялась или не дала вывода. Эти циклы не входят ни в «улучшение», ни в «без изменений».</p>
        <dl className="mt-2 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
          {UNVERIFIED_RESOLUTIONS.map(([key, label]) => (
            <div key={key} className="flex items-baseline justify-between gap-2 border-b border-slate-200 py-1" data-unverified={key}>
              <dt className="text-agro-muted">{label}</dt>
              <dd className="font-semibold tabular-nums text-agro-text">{formatCount(unverified[key])}</dd>
            </div>
          ))}
        </dl>
      </section>

      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-agro-muted">Планов закрыто за период</dt>
          <dd className="text-xl font-semibold text-agro-text">{formatCount(closed.total)}</dd>
          <dd className="text-xs text-agro-muted">с улучшением: {formatCount(closed.improved)} · без улучшения: {formatCount(closed.without_improvement)}</dd>
        </div>
        <div>
          <dt className="text-agro-muted">Возвратов на доработку после проверки</dt>
          <dd className="text-xl font-semibold text-agro-text">{formatCount(outcomes.returned_for_rework)}</dd>
          <dd className="text-xs text-agro-muted">события периода; цикл продолжается новым циклом плана</dd>
        </div>
      </dl>
      <p className="mt-3 text-xs leading-5 text-agro-muted">
        «Улучшение» — изменение NDVI на новом спутниковом снимке по правилу проверки {policy || 'сервера'}.
        Это не доказательство агрономической причины, урожайности или экономического эффекта.
      </p>
    </Card>
  );
}

function ReopenCard({ reopenEvents, reopenedNow }) {
  return (
    <Card id="management-analytics-reopen-title" title="Переоткрытия и доработка">
      <dl className="mt-3 space-y-4 text-sm">
        <div>
          <dt className="text-agro-muted">Переоткрытий за период (события)</dt>
          <dd className="text-2xl font-semibold text-agro-text" data-testid="management-analytics-reopen-events">{formatCount(reopenEvents.total)}</dd>
          <dd className="text-xs text-agro-muted">после закрытия: {formatCount(reopenEvents.after_closure)} · после проверки: {formatCount(reopenEvents.after_verification)}</dd>
        </div>
        <div>
          <dt className="text-agro-muted">Сейчас на доработке (проблемы)</dt>
          <dd className="text-2xl font-semibold text-agro-text" data-testid="management-analytics-reopened-now">{formatCount(reopenedNow)}</dd>
          <dd className="text-xs text-agro-muted">текущее состояние, а не события периода</dd>
        </div>
      </dl>
      <p className="mt-3 text-xs leading-5 text-agro-muted">Одна проблема может переоткрываться несколько раз, поэтому события и текущее состояние не совпадают.</p>
    </Card>
  );
}

function Meter({ rate }) {
  if (typeof rate !== 'number') return null;
  return (
    <div className="mt-2 h-2 overflow-hidden rounded-full bg-blue-100" aria-hidden="true">
      <div className="h-full rounded-full" style={{ width: `${Math.min(100, Math.max(0, rate * 100))}%`, backgroundColor: METER }} />
    </div>
  );
}

function CompletionCard({ completion }) {
  return (
    <Card id="management-analytics-completion-title" title="Завершённость за период" className="xl:col-span-3">
      <p className="mt-1 text-xs leading-5 text-agro-muted">
        Каждая строка — своя выборка: объекты, начавшие этап в периоде. Числитель — их состояние сейчас.
        Завершённые работы — не успех: успех определяется только результатом проверки.
      </p>
      <div className="mt-3 grid gap-4 lg:grid-cols-3">
        {COMPLETION_METRICS.map((definition) => {
          const metric = completion?.[definition.key] || {};
          const extraRate = definition.extraRate ? formatRate(metric[definition.extraRate[0]]) : null;
          return (
            <section key={definition.key} className="min-w-0 rounded-lg border border-agro-border p-3" aria-labelledby={`management-analytics-${definition.key}`} data-completion={definition.key}>
              <h4 id={`management-analytics-${definition.key}`} className="text-sm font-semibold text-agro-text">{definition.title}</h4>
              <p className="mt-1 text-lg font-semibold text-agro-text" data-testid={`management-analytics-completion-${definition.key}`}>{formatShare(metric)}</p>
              <Meter rate={metric.denominator > 0 ? metric.rate : null} />
              <p className="mt-2 text-xs leading-5 text-agro-muted">{definition.population}</p>
              <p className="mt-1 text-xs leading-5 text-agro-muted">
                {definition.parts.map(([key, label], index) => <span key={key}>{index ? ' · ' : ''}{label}: {formatCount(metric[key])}</span>)}
              </p>
              {extraRate && <p className="mt-1 text-xs leading-5 text-agro-muted">{definition.extraRate[1]}: {extraRate}</p>}
            </section>
          );
        })}
      </div>
    </Card>
  );
}

function CycleTimesCard({ cycleTimes }) {
  const metrics = cycleTimes?.metrics || {};
  const minimum = cycleTimes?.p90_minimum_samples;
  const known = new Set(CYCLE_TIME_METRICS.map((item) => item.key));
  const rows = [
    ...CYCLE_TIME_METRICS.filter((item) => metrics[item.key]),
    // A metric this page has no Russian label for is still shown, never hidden.
    ...Object.keys(metrics).filter((key) => !known.has(key)).map((key) => ({
      key, label: `${metrics[key].start_event} → ${metrics[key].end_event}`, population: metrics[key].population,
    })),
  ];
  const unsupported = Array.isArray(cycleTimes?.unsupported) ? cycleTimes.unsupported : [];
  return (
    <Card id="management-analytics-cycle-times-title" title="Время прохождения этапов" className="xl:col-span-3">
      <p className="mt-1 text-xs leading-5 text-agro-muted">
        Только завершённые интервалы, закончившиеся в периоде. Медиана — от одного наблюдения,
        P90 — от {formatCount(minimum)} наблюдений. Малое число наблюдений делает значение ненадёжным.
      </p>
      <div className="mt-3 overflow-x-auto focus:outline-none focus:ring-2 focus:ring-agro-accent" role="region" tabIndex={0} aria-label="Таблица времени этапов">
        <table className="w-full text-sm" data-testid="management-analytics-cycle-times">
          <caption className="sr-only">Время прохождения этапов за период: медиана, P90 и число наблюдений</caption>
          <thead>
            <tr className="border-b border-agro-border text-left text-xs text-agro-muted">
              <th scope="col" className="py-2 pr-3 font-medium">Этап</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Медиана</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">P90</th>
              <th scope="col" className="py-2 pl-3 text-right font-medium">Наблюдений</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const metric = metrics[row.key];
              const measured = metric.status === 'measured' && metric.sample_count > 0;
              return (
                <tr key={row.key} className="border-b border-agro-border/60 align-top" data-cycle-time={row.key}>
                  <th scope="row" className="py-2 pr-3 text-left font-normal">
                    <span className="block font-medium text-agro-text">{row.label}</span>
                    <span className="block text-xs leading-5 text-agro-muted">{row.population}</span>
                  </th>
                  <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                    {measured ? <>{formatHours(metric.median_hours)}<span className="block text-xs text-agro-muted">{formatExactHours(metric.median_hours)}</span></> : <span className="text-agro-muted">нет наблюдений</span>}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                    {metric.p90_hours !== null && metric.p90_hours !== undefined
                      ? <>{formatHours(metric.p90_hours)}<span className="block text-xs text-agro-muted">{formatExactHours(metric.p90_hours)}</span></>
                      : <span className="text-xs text-agro-muted">{measured ? `нужно ≥ ${formatCount(minimum)}` : '—'}</span>}
                  </td>
                  <td className="py-2 pl-3 text-right font-semibold tabular-nums" data-testid={`management-analytics-samples-${row.key}`}>{formatCount(metric.sample_count)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {unsupported.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs leading-5 text-agro-muted" aria-label="Не измеряемые интервалы">
          {unsupported.map((item) => {
            const described = UNSUPPORTED_CYCLE_TIMES[item?.metric];
            return <li key={item?.metric}><strong className="text-agro-text">{described?.label || item?.metric}</strong> — {described?.reason || item?.reason}</li>;
          })}
        </ul>
      )}
    </Card>
  );
}

export default function PeriodSection({ data, requestedGranularity, onGranularityChange }) {
  const effective = data.period?.effective || {};
  const outcomes = data.outcomes || {};
  return (
    <section aria-labelledby="management-analytics-period-title" className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="management-analytics-period-title" className="text-lg font-semibold text-agro-text">За период</h2>
        <p className="text-xs text-agro-muted">{formatPeriod(effective.date_from, effective.date_to)} · события, время которых попало в период</p>
      </div>
      <div className="grid gap-4 xl:grid-cols-3">
        <OutcomesCard outcomes={outcomes} policy={data.provenance?.verification_policy_version} />
        <ReopenCard reopenEvents={outcomes.reopen_events || {}} reopenedNow={data.current?.by_remediation_status?.reopened} />
        <CompletionCard completion={data.completion} />
        <CycleTimesCard cycleTimes={data.cycle_times} />
        <PeriodDynamics
          activity={data.period_activity || {}}
          periods={data.breakdowns?.periods}
          granularity={effective.granularity || requestedGranularity}
          requestedGranularity={requestedGranularity}
          onGranularityChange={onGranularityChange}
        />
      </div>
    </section>
  );
}
