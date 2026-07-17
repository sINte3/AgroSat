export function InterpretationLoading() {
  return (
    <div className="card min-h-64 space-y-3" role="status" aria-live="polite">
      <div className="h-5 w-64 rounded bg-agro-surface2 animate-pulse" />
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-3">
        {[0, 1, 2, 3, 4].map((item) => <div key={item} className="h-44 rounded-lg bg-agro-surface2 animate-pulse" />)}
      </div>
      <span className="sr-only">Загружается агрономическая интерпретация</span>
    </div>
  );
}

export function InterpretationError({ onRetry }) {
  return (
    <div className="card flex flex-wrap items-center justify-between gap-3" role="alert">
      <p className="text-sm text-agro-danger">Не удалось загрузить агрономическую интерпретацию.</p>
      <button type="button" onClick={onRetry} className="btn-primary px-3 py-1.5 text-sm rounded-lg">
        Повторить
      </button>
    </div>
  );
}
