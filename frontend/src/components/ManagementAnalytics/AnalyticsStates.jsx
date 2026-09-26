// Loading, error and empty states of the Management Analytics page. An error
// is never rendered as an empty result, and an empty scope is not an error.

function SkeletonBlock({ className }) {
  return <div className={`animate-pulse rounded-xl bg-slate-200/70 motion-reduce:animate-none ${className}`} />;
}

export function AnalyticsSkeleton() {
  return (
    <div aria-busy="true" data-testid="management-analytics-loading">
      <p role="status" className="mb-3 text-sm text-agro-muted">Загружаем управленческую аналитику…</p>
      <div aria-hidden="true" className="space-y-4">
        <div className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {Array.from({ length: 5 }).map((_, index) => <SkeletonBlock key={index} className="h-32" />)}
        </div>
        <SkeletonBlock className="h-56" />
        <div className="grid gap-4 xl:grid-cols-2">
          <SkeletonBlock className="h-64" />
          <SkeletonBlock className="h-64" />
        </div>
      </div>
    </div>
  );
}

export function AnalyticsErrorPanel({ error, onRetry, onReset }) {
  return (
    <section role="alert" aria-labelledby="management-analytics-error-title" data-testid="management-analytics-error" data-error-kind={error.kind} className="rounded-xl border border-red-200 bg-white p-5">
      <h2 id="management-analytics-error-title" className="text-base font-semibold text-red-900">{error.title}</h2>
      <p className="mt-2 max-w-2xl text-sm leading-6 text-red-900">{error.message}</p>
      <p className="mt-1 text-xs text-agro-muted">Показатели не отображаются: ошибка загрузки не означает, что данных нет.</p>
      <div className="mt-4 flex flex-wrap gap-2">
        {error.retryable && <button type="button" onClick={onRetry} className="btn-primary min-h-11 px-4 focus:outline-none focus:ring-2 focus:ring-agro-accent focus:ring-offset-2">Повторить</button>}
        {error.resettable && <button type="button" onClick={onReset} className="btn-secondary min-h-11 px-4 focus:outline-none focus:ring-2 focus:ring-agro-accent">Сбросить фильтры</button>}
      </div>
    </section>
  );
}

export function RefreshErrorBanner({ error, generatedAt, onRetry }) {
  return (
    <div role="alert" data-testid="management-analytics-refresh-error" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950">
      <p className="min-w-0 leading-6"><strong>{error.title}.</strong> {error.message} Ниже — данные, сформированные {generatedAt}, для той же области и периода.</p>
      {error.retryable && <button type="button" onClick={onRetry} className="btn-secondary min-h-11 flex-none px-3 text-sm focus:outline-none focus:ring-2 focus:ring-agro-accent">Повторить</button>}
    </div>
  );
}

export function EmptyScopePanel({ onReset }) {
  return (
    <section aria-labelledby="management-analytics-empty-title" data-testid="management-analytics-empty" className="rounded-xl border border-agro-border bg-white p-6">
      <h2 id="management-analytics-empty-title" className="text-base font-semibold text-agro-text">В выбранной области нет полей</h2>
      <p className="mt-2 max-w-2xl text-sm leading-6 text-agro-muted">
        Сервер не нашёл ни одного поля для выбранного предприятия, поля или текущей культуры,
        поэтому показателей нет. Это не ошибка загрузки. Измените фильтры или сбросьте их.
      </p>
      <button type="button" onClick={onReset} className="btn-secondary mt-4 min-h-11 px-4 focus:outline-none focus:ring-2 focus:ring-agro-accent">Сбросить фильтры</button>
    </section>
  );
}
