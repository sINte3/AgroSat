import { Component } from 'react';

/**
 * Catches rendering exceptions of its subtree so one failing page cannot blank
 * the application shell. It is not an error channel for API failures: network
 * and data errors stay explicit state inside the page that owns the request.
 *
 * The boundary resets when `resetKey` changes (for example the route) or when
 * the fallback calls `reset()`. A child that throws again simply shows the
 * fallback again; nothing re-renders automatically, so there is no loop.
 */
export default class RenderErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
    this.reset = this.reset.bind(this);
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error) {
    // Diagnostics without user or business data.
    console.error('[AgroSat] render failure', error?.name || 'Error', String(error?.message || '').slice(0, 200));
  }

  componentDidUpdate(previousProps) {
    if (this.state.error && previousProps.resetKey !== this.props.resetKey) this.reset();
  }

  reset() {
    this.setState({ error: null });
  }

  render() {
    if (this.state.error) return this.props.fallback({ error: this.state.error, reset: this.reset });
    return this.props.children;
  }
}

export function RouteErrorFallback({ onRetry, onNavigateHome, homeLabel = 'На главную' }) {
  return (
    <section role="alert" aria-labelledby="route-error-title" className="flex h-full items-center justify-center overflow-y-auto p-6 pt-20" data-testid="route-error-boundary">
      <div className="max-w-lg rounded-xl border border-red-200 bg-white p-6 text-center shadow-sm">
        <h2 id="route-error-title" className="text-lg font-semibold text-red-900">Раздел не удалось отобразить</h2>
        <p className="mt-2 text-sm leading-6 text-agro-muted">Произошла ошибка интерфейса этого раздела. Данные на сервере и локальные черновики не изменены. Повторите попытку или перейдите в другой раздел.</p>
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          <button type="button" onClick={onRetry} className="btn-primary min-h-11 px-4 focus:outline-none focus:ring-2 focus:ring-agro-accent">Повторить</button>
          <button type="button" onClick={onNavigateHome} className="btn-secondary min-h-11 px-4 focus:outline-none focus:ring-2 focus:ring-agro-accent">{homeLabel}</button>
        </div>
      </div>
    </section>
  );
}

export function AppErrorFallback() {
  return (
    <main role="alert" aria-labelledby="app-error-title" className="flex min-h-screen items-center justify-center bg-slate-950 px-4" data-testid="app-error-boundary">
      <div className="w-full max-w-sm rounded-xl border border-slate-800 bg-slate-900 p-8 text-center text-white shadow-2xl">
        <h1 id="app-error-title" className="text-lg font-semibold">AgroSat не удалось отобразить</h1>
        <p className="mt-2 text-sm text-slate-400">Произошла ошибка интерфейса. Локальные черновики осмотров сохранены на устройстве.</p>
        <div className="mt-5 flex flex-col gap-2">
          <button type="button" onClick={() => window.location.reload()} className="min-h-11 rounded-lg bg-emerald-600 px-4 text-sm font-medium text-white hover:bg-emerald-500">Перезагрузить</button>
          <button type="button" onClick={() => window.location.assign('/login')} className="min-h-11 rounded-lg border border-slate-700 px-4 text-sm font-medium text-slate-200 hover:bg-slate-800">Перейти ко входу</button>
        </div>
      </div>
    </main>
  );
}
