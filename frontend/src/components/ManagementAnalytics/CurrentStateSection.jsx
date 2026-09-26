import {
  LIFECYCLE_PHASES,
  NDVI_FRESHNESS_LABELS,
  PRIORITY_LABELS,
  SOURCE_LABELS,
} from '../../config/managementAnalytics.js';
import { formatCount } from '../../utils/managementAnalytics.js';
import { formatTashkentDateTime } from '../../utils/tashkentTime.js';

// Magnitude bars use one hue (a single series); status meaning is never
// carried by this colour.
const MAGNITUDE = '#2a78d6';

const count = (value) => (typeof value === 'number' ? value : value?.total);

function Fact({ label, value, testId }) {
  // A no-break space keeps each number on the line of its label.
  return <span>{label}:{'\u00a0'}<strong className="font-semibold text-agro-text" data-testid={testId}>{formatCount(value)}</strong></span>;
}

function KpiTile({ id, label, value, attention = false, children, footnote }) {
  return (
    <div className={`min-w-0 rounded-xl border bg-white p-4 ${attention ? 'border-rose-300 shadow-[inset_0_3px_0_0_#e11d48]' : 'border-agro-border'}`} data-kpi={id}>
      <dt className="text-sm font-medium leading-5 text-agro-muted">{label}</dt>
      <dd className="mt-1 text-3xl font-semibold text-agro-text" data-testid={`management-analytics-kpi-${id}`}>{formatCount(value)}</dd>
      {children && <dd className="mt-2 flex flex-col gap-0.5 text-xs leading-5 text-agro-muted">{children}</dd>}
      {footnote && <dd className="mt-2 border-t border-agro-border pt-2 text-xs leading-5 text-agro-muted">{footnote}</dd>}
    </div>
  );
}

function KpiRow({ coverage, current }) {
  const problems = current.active_problems || {};
  const overdue = current.overdue_cases || {};
  const work = current.work_items || {};
  const states = current.by_remediation_status || {};
  return (
    <dl className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5" aria-label="Ключевые показатели сейчас">
      <KpiTile id="monitored_fields" label="Поля под мониторингом" value={coverage.monitored_fields}>
        <Fact label="Всего полей в области" value={coverage.fields_in_scope} />
        {coverage.inactive_fields > 0 && <Fact label="Не отслеживаются" value={coverage.inactive_fields} />}
        <Fact label="Из них с активными проблемами" value={coverage.monitored_fields_with_active_problems} />
      </KpiTile>
      <KpiTile
        id="active_problems"
        label="Активные проблемы"
        value={problems.total}
        footnote="Без ситуаций «Данные недоступны» — они показаны отдельно."
      >
        <Fact label="Затронуто полей" value={problems.fields_affected} />
        <Fact label="Критический приоритет" value={problems.by_priority?.critical} />
        <Fact label="Высокий приоритет" value={problems.by_priority?.high} />
      </KpiTile>
      <KpiTile
        id="overdue_cases"
        label="Просроченные ситуации"
        value={overdue.total}
        attention={overdue.total > 0}
        footnote="Как «Просрочено» в Операционном центре."
      >
        <Fact label="На этапе осмотра" value={overdue.inspection_stage} />
        <Fact label="На этапе работ" value={overdue.work_stage} />
      </KpiTile>
      <KpiTile
        id="work_items"
        label="Незавершённые работы по планам мер"
        value={work.active}
        footnote="Считаются отдельные работы, а не проблемы. Истёкший срок — срок работы уже прошёл."
      >
        <Fact label="Выполняются" value={work.in_progress} />
        <Fact label="Запланированы" value={work.planned} />
        <Fact label="Без исполнителя" value={work.unassigned} />
        <Fact label="Работы с истёкшим сроком" value={work.overdue_work_items} testId="management-analytics-overdue-work-items" />
      </KpiTile>
      <KpiTile
        id="plans_pending_verification"
        label="Планы на спутниковой проверке"
        value={current.plans_pending_verification}
        footnote="Как «Ждут снимка» в Операционном центре."
      >
        <Fact label="Проверка заблокирована" value={count(states.verification_blocked)} />
        <span>Все состояния проверки — в этапе 6 ниже.</span>
      </KpiTile>
    </dl>
  );
}

function StateRow({ definition, value, max }) {
  const total = count(value);
  const width = max > 0 && total > 0 ? Math.max(2, (total / max) * 100) : 0;
  const parts = (definition.parts || []).filter(([key]) => (value?.[key] || 0) > 0);
  const note = definition.note && value?.[definition.note[0]] > 0 ? definition.note : null;
  return (
    <li className="min-w-0" data-state={definition.key}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 text-sm leading-5 text-agro-text">{definition.label}</span>
        <span className="text-xl font-semibold text-agro-text" data-testid={`management-analytics-state-${definition.key}`}>{formatCount(total)}</span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-blue-50" aria-hidden="true">
        <div className="h-full rounded-full" style={{ width: `${width}%`, backgroundColor: MAGNITUDE }} />
      </div>
      {parts.length > 0 && (
        <p className="mt-1 text-xs leading-5 text-agro-muted">
          {parts.map(([key, label], index) => <span key={key}>{index ? ' · ' : ''}{label}: {formatCount(value[key])}</span>)}
        </p>
      )}
      {note && <p className="mt-0.5 text-xs font-medium leading-5 text-amber-900">{note[1]}: {formatCount(value[note[0]])}</p>}
    </li>
  );
}

function LifecycleStrip({ states, total, asOf }) {
  const values = LIFECYCLE_PHASES.flatMap((phase) => phase.states.map((item) => count(states[item.key]) || 0));
  const max = Math.max(0, ...values);
  return (
    <section className="rounded-xl border border-agro-border bg-white p-4" aria-labelledby="management-analytics-lifecycle-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="management-analytics-lifecycle-title" className="font-semibold text-agro-text">Где сейчас находятся активные проблемы</h3>
        <p className="text-sm text-agro-muted">Всего активных проблем: <strong className="text-agro-text">{formatCount(total)}</strong></p>
      </div>
      <p className="mt-1 text-xs leading-5 text-agro-muted">
        Снимок на {formatTashkentDateTime(asOf, 'момент формирования')}: каждая активная проблема учитывается ровно в одном состоянии.
        Это не воронка конверсии — числа не показывают, сколько проблем перешло с этапа на этап.
      </p>
      {total === 0 && <p className="mt-3 rounded-lg bg-slate-50 p-3 text-sm text-agro-muted">Активных проблем в выбранной области сейчас нет.</p>}
      <ol className="mt-4 grid gap-3 md:grid-cols-3 xl:grid-cols-7" aria-label="Этапы канонического цикла">
        {LIFECYCLE_PHASES.map((phase, index) => (
          <li key={phase.id} className={`relative min-w-0 rounded-lg border border-agro-border bg-agro-card/50 p-3 ${phase.states.length > 2 ? 'xl:col-span-2' : ''}`} data-phase={phase.id}>
            <h4 className="text-xs font-semibold tracking-wide text-agro-muted">
              <span className="mr-1 inline-flex h-5 w-5 items-center justify-center rounded-full bg-white text-[11px] text-agro-text ring-1 ring-agro-border">{index + 1}</span>
              {phase.title}
            </h4>
            <ul className={`mt-2 grid gap-3 ${phase.states.length > 2 ? 'xl:grid-cols-2 xl:gap-x-4' : ''}`}>
              {phase.states.map((definition) => (
                <StateRow key={definition.key} definition={definition} value={states[definition.key]} max={max} />
              ))}
            </ul>
            {index < LIFECYCLE_PHASES.length - 1 && (
              <span aria-hidden="true" className="absolute -right-3 top-6 z-10 hidden h-6 w-6 items-center justify-center rounded-full bg-white text-sm text-agro-muted ring-1 ring-agro-border xl:flex">→</span>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

function CountList({ items, source, testPrefix }) {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
      {items.map(([key, label]) => (
        <div key={key} className="flex items-baseline justify-between gap-2 border-b border-agro-border/60 py-1">
          <dt className="text-agro-muted">{label}</dt>
          <dd className="font-semibold tabular-nums text-agro-text" data-testid={testPrefix ? `${testPrefix}-${key}` : undefined}>{formatCount(source?.[key])}</dd>
        </div>
      ))}
    </dl>
  );
}

function CompositionCard({ problems }) {
  return (
    <section className="rounded-xl border border-agro-border bg-white p-4" aria-labelledby="management-analytics-composition-title">
      <h3 id="management-analytics-composition-title" className="font-semibold text-agro-text">Состав активных проблем</h3>
      <h4 className="mt-3 text-xs font-semibold text-agro-muted">По приоритету</h4>
      <div className="mt-1"><CountList items={PRIORITY_LABELS} source={problems.by_priority} /></div>
      <h4 className="mt-3 text-xs font-semibold text-agro-muted">По источнику</h4>
      <div className="mt-1"><CountList items={SOURCE_LABELS} source={problems.by_source} /></div>
      {problems.legacy_open_inspections > 0 && (
        <p className="mt-3 text-xs leading-5 text-agro-muted">
          В том числе открытых осмотров прежнего контура: <strong className="text-agro-text">{formatCount(problems.legacy_open_inspections)}</strong>. Они остаются в работе до закрытия.
        </p>
      )}
    </section>
  );
}

function DataAvailabilityCard({ coverage, dataUnavailable }) {
  return (
    <section className="rounded-xl border border-agro-border bg-white p-4" aria-labelledby="management-analytics-data-title">
      <h3 id="management-analytics-data-title" className="font-semibold text-agro-text">Данные спутникового мониторинга</h3>
      <p className="mt-1 text-xs leading-5 text-agro-muted">Свежесть NDVI по полям под мониторингом ({formatCount(coverage.monitored_fields)}).</p>
      <div className="mt-2"><CountList items={NDVI_FRESHNESS_LABELS} source={coverage.ndvi_freshness} testPrefix="management-analytics-freshness" /></div>
      <h4 className="mt-3 text-xs font-semibold text-agro-muted">Ситуации «Данные недоступны»</h4>
      <p className="mt-1 text-sm text-agro-text">
        <Fact label="Нет свежих данных по полю" value={dataUnavailable.freshness_cases} />
        {' · '}
        <Fact label="Сбой внешнего сбора" value={dataUnavailable.external_cases} />
      </p>
      <p className="mt-1 text-xs leading-5 text-agro-muted">Это состояние данных, а не проблемы на полях; в число активных проблем не входит.</p>
    </section>
  );
}

export default function CurrentStateSection({ data }) {
  const current = data.current || {};
  const coverage = data.coverage || {};
  return (
    <section aria-labelledby="management-analytics-current-title" className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="management-analytics-current-title" className="text-lg font-semibold text-agro-text">Сейчас</h2>
        <p className="text-xs text-agro-muted">Состояние на {formatTashkentDateTime(current.as_of, 'момент формирования')} · период не влияет</p>
      </div>
      <KpiRow coverage={coverage} current={current} />
      <LifecycleStrip states={current.by_remediation_status || {}} total={current.active_problems?.total} asOf={current.as_of} />
      <div className="grid gap-4 lg:grid-cols-2">
        <CompositionCard problems={current.active_problems || {}} />
        <DataAvailabilityCard coverage={coverage} dataUnavailable={current.data_unavailable || {}} />
      </div>
    </section>
  );
}
