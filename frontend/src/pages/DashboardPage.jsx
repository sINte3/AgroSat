import { useState, useEffect, useCallback, useMemo } from 'react';
import { getCachedDashboardSummary, getAlerts, getSatelliteCoverage } from '../api/client';
import SummaryCards from '../components/Dashboard/SummaryCards';
import { COVERAGE_STATUS_CONFIG, FRESHNESS_STATUS_CONFIG, COVERAGE_PRIORITY_LABELS } from '../config/indexMetadata';
import INDEX_METADATA from '../config/indexMetadata';

const ALERT_TYPE_LABELS = {
  ndvi_low:    'NDVI ниже нормы для фазы роста',
  ndvi_drop:   'Резкое снижение NDVI',
  ndvi_uneven: 'Неравномерное состояние посевов',
  ndvi_high:   'NDVI выше нормы',
  drought_risk:'Риск засухи',
  frost_risk:  'Риск заморозков',
  heavy_rain:  'Сильные осадки',
  no_data:     'Нет данных (облачность)',
  manual:      'Заметка агронома',
};

export default function DashboardPage({ onNavigate, onFieldClick, onFieldHighlight }) {
  const [summary, setSummary] = useState(null);
  const [allAlerts, setAllAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [coverage, setCoverage] = useState(null);
  const [coverageLoading, setCoverageLoading] = useState(true);
  const [coverageError, setCoverageError] = useState(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    setCoverageError(null);
    setCoverage(null);
    try {
      const [summaryData, alertsData, coverageData] = await Promise.all([
        getCachedDashboardSummary(),
        getAlerts({ is_active: true, limit: 20 }),
        getSatelliteCoverage({ include_empty: true, active_only: true }).catch(e => {
          setCoverageError(e?.message || 'Не удалось загрузить данные покрытия');
          return null;
        }),
      ]);
      setSummary(summaryData);
      setAllAlerts(Array.isArray(alertsData) ? alertsData : []);
      if (coverageData) setCoverage(coverageData);
    } catch (err) {
      setError('Не удалось загрузить данные дашборда');
      console.error(err);
    } finally {
      setLoading(false);
      setCoverageLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Alert severity breakdown
  const criticalAlerts = useMemo(
    () => allAlerts.filter(a => a.severity === 'critical'),
    [allAlerts]
  );
  const warningAlerts = useMemo(
    () => allAlerts.filter(a => a.severity === 'warning'),
    [allAlerts]
  );
  const infoAlerts = useMemo(
    () => allAlerts.filter(a => a.severity === 'info'),
    [allAlerts]
  );

  // Top recent alerts (sorted by triggered_at desc, up to 5)
  const recentAlerts = useMemo(
    () => [...allAlerts]
      .sort((a, b) => new Date(b.triggered_at) - new Date(a.triggered_at))
      .slice(0, 5),
    [allAlerts]
  );

  const lastUpdated = summary?.last_updated
    ? new Date(summary.last_updated).toLocaleString('ru-RU')
    : null;

  /* ── Alert row component ───────────────────────────────────── */
  function AlertRow({ alert }) {
    const severityColors = {
      critical: { dot: 'bg-red-500', bg: 'bg-red-50/40 border-red-200', text: 'text-red-700' },
      warning:  { dot: 'bg-amber-500', bg: 'bg-amber-50/40 border-amber-200', text: 'text-amber-700' },
      info:     { dot: 'bg-blue-500', bg: 'bg-blue-50/40 border-blue-200', text: 'text-blue-700' },
    };
    const colors = severityColors[alert.severity] || severityColors.info;

    return (
      <button
        key={alert.id}
        onClick={() => onFieldHighlight?.(alert.field_id)}
        className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg border ${colors.bg} hover:opacity-80 transition-opacity text-left`}
      >
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${colors.dot}`} />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-agro-text truncate">
            {ALERT_TYPE_LABELS[alert.alert_type] || alert.title}
          </p>
          <p className="text-xs text-agro-muted truncate">
            {alert.field_name || `Поле #${alert.field_id}`}
            {' · '}
            {new Date(alert.triggered_at).toLocaleDateString('ru-RU')}
          </p>
        </div>
        <span className={`text-xs font-semibold flex-shrink-0 ${colors.text}`}>
          {alert.severity === 'critical' ? 'Критично'
            : alert.severity === 'warning' ? 'Риск'
            : 'Инфо'}
        </span>
      </button>
    );
  }

  /* ── Loading state ──────────────────────────────────────────── */
  if (loading) {
    return (
      <div className="flex flex-col h-full">
        <div className="p-4 lg:p-6 pt-16 space-y-4">
          <SummaryCards summary={null} loading={true} />
          <div className="space-y-2">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-14 bg-agro-card rounded animate-pulse" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  /* ── Error state ────────────────────────────────────────────── */
  if (error) {
    return (
      <div className="flex flex-col h-full">
        <div className="p-4 lg:p-6 pt-16 space-y-4">
          <SummaryCards summary={null} loading={false} />
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="w-14 h-14 rounded-full bg-red-100 flex items-center justify-center mb-4">
              <svg className="w-7 h-7 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
            </div>
            <p className="text-sm text-agro-danger font-medium mb-2">{error}</p>
            <button
              onClick={loadData}
              className="text-sm text-agro-accent underline hover:no-underline"
            >
              Повторить загрузку
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="p-4 lg:p-6 pt-16 space-y-4">
        {/* Sub-header with cluster info and last update */}
        <div>
          <p className="text-sm text-agro-muted">
            Бухоро Агрокластер
            {lastUpdated && ` · Данные от ${lastUpdated}`}
          </p>
        </div>

        {/* Summary cards */}
        <SummaryCards summary={summary} loading={false} />

        {/* Two-column management overview */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
          {/* Left — Problem fields and recent alerts */}
          <div className="lg:col-span-3 space-y-4">
            {/* Alerts summary card */}
            <div className="card">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-agro-text">
                  Сводка предупреждений
                </h2>
                {allAlerts.length > 0 && (
                  <button
                    onClick={() => onNavigate('alerts')}
                    className="text-xs text-agro-accent hover:underline font-medium"
                  >
                    Перейти к предупреждениям →
                  </button>
                )}
              </div>

              {allAlerts.length === 0 ? (
                <div className="flex items-center gap-3 py-4">
                  <div className="w-10 h-10 rounded-full bg-green-100 flex items-center justify-center">
                    <svg className="w-5 h-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  </div>
                  <div>
                    <p className="text-sm font-medium text-agro-text">Все поля в норме</p>
                    <p className="text-xs text-agro-muted">Активных предупреждений нет</p>
                  </div>
                </div>
              ) : (
                <>
                  {/* Severity summary chips */}
                  <div className="flex flex-wrap gap-2 mb-3">
                    {criticalAlerts.length > 0 && (
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-red-100 text-red-700 text-xs font-semibold">
                        <span className="w-2 h-2 rounded-full bg-red-500" />
                        Критично: {criticalAlerts.length}
                      </span>
                    )}
                    {warningAlerts.length > 0 && (
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-amber-100 text-amber-700 text-xs font-semibold">
                        <span className="w-2 h-2 rounded-full bg-amber-500" />
                        Высокий риск: {warningAlerts.length}
                      </span>
                    )}
                    {infoAlerts.length > 0 && (
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-blue-100 text-blue-700 text-xs font-semibold">
                        <span className="w-2 h-2 rounded-full bg-blue-500" />
                        Инфо: {infoAlerts.length}
                      </span>
                    )}
                  </div>

                  {/* Recent top alerts list */}
                  <div className="space-y-2">
                    <p className="text-xs font-semibold text-agro-muted uppercase tracking-wide">
                      Последние предупреждения
                    </p>
                    {recentAlerts.map(alert => (
                      <AlertRow key={alert.id} alert={alert} />
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* Field summary info (derived from summary) */}
            {summary && (
              <div className="card">
                <h2 className="text-sm font-semibold text-agro-text mb-3">
                  Состояние полей
                </h2>
                <div className="grid grid-cols-3 gap-3">
                  <div className="p-3 rounded-lg bg-green-50 border border-green-200">
                    <p className="text-xs text-green-600 font-medium">С данными</p>
                    <p className="text-lg font-bold text-green-700">
                      {summary.total_fields - (summary.fields_no_data || 0)}
                    </p>
                  </div>
                  {summary.fields_no_data > 0 && (
                    <div className="p-3 rounded-lg bg-gray-50 border border-gray-200">
                      <p className="text-xs text-gray-500 font-medium">Без данных</p>
                      <p className="text-lg font-bold text-gray-600">{summary.fields_no_data}</p>
                    </div>
                  )}
                  {summary.fields_with_problems > 0 && (
                    <div className="p-3 rounded-lg bg-amber-50 border border-amber-200">
                      <p className="text-xs text-amber-600 font-medium">Проблемные</p>
                      <p className="text-lg font-bold text-amber-700">{summary.fields_with_problems}</p>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Right — Additional metrics */}
          <div className="lg:col-span-2 space-y-4">
            {/* Enterprise quick count */}
            {summary?.total_enterprises > 0 && (
              <div className="card">
                <h2 className="text-sm font-semibold text-agro-text mb-3">
                  Предприятия
                </h2>
                <p className="text-2xl font-bold text-agro-accent">
                  {summary.total_enterprises}
                </p>
                <p className="text-xs text-agro-muted mt-1">всего предприятий в кластере</p>
              </div>
            )}

            {/* Satellite data freshness */}
            {lastUpdated && (
              <div className="card">
                <h2 className="text-sm font-semibold text-agro-text mb-3">
                  Свежесть данных
                </h2>
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-agro-surface2 text-agro-accent">
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                        d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
                      />
                    </svg>
                  </div>
                  <div>
                    <p className="text-sm font-medium text-agro-text">
                      {new Date(summary.last_updated).toLocaleDateString('ru-RU', {
                        day: 'numeric',
                        month: 'long',
                        year: 'numeric',
                      })}
                    </p>
                    <p className="text-xs text-agro-muted">последнее обновление спутниковых данных</p>
                  </div>
                </div>
              </div>
            )}

            {/* Average NDVI */}
            {summary?.avg_ndvi != null && (
              <div className="card">
                <h2 className="text-sm font-semibold text-agro-text mb-3">
                  Средний NDVI по кластеру
                </h2>
                <div className="flex items-center gap-3">
                  <div className={`w-3 h-3 rounded-full ${
                    summary.avg_ndvi >= 0.6 ? 'bg-green-500'
                      : summary.avg_ndvi >= 0.3 ? 'bg-amber-500'
                      : 'bg-red-500'
                  }`} />
                  <span className="text-2xl font-bold text-agro-text">
                    {summary.avg_ndvi.toFixed(3)}
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ── Satellite coverage block ───────────────────────────────── */}

        {/* Loading */}
        {coverageLoading && (
          <div className="card">
            <div className="h-4 bg-gray-200 rounded w-48 mb-3 animate-pulse" />
            <div className="grid grid-cols-3 gap-3">
              {[...Array(6)].map((_, i) => (
                <div key={i} className="h-12 bg-gray-100 rounded animate-pulse" />
              ))}
            </div>
          </div>
        )}

        {/* Auth error */}
        {coverageError && coverageError.includes('401') && (
          <div className="card border border-red-200 bg-red-50">
            <p className="text-sm text-red-700 font-medium">Нет доступа к спутниковым данным</p>
            <p className="text-xs text-red-500 mt-1">Проверьте права учётной записи.</p>
          </div>
        )}

        {/* Config error (422) */}
        {coverageError && coverageError.includes('конфигурации') && (
          <div className="card border border-amber-200 bg-amber-50">
            <p className="text-sm text-amber-700 font-medium">{coverageError}</p>
          </div>
        )}

        {/* Coverage data */}
        {coverage && coverage.summary && !coverageLoading && (
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-agro-text">
                Покрытие спутниковыми индексами
              </h2>
              {coverage.summary.latest_captured_date && (
                <span className="text-xs text-agro-muted">
                  Данные от {new Date(coverage.summary.latest_captured_date).toLocaleDateString('ru-RU')}
                </span>
              )}
            </div>

            {/* Summary grid */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
              <CoverageStat label="Всего полей" value={coverage.summary.fields_total} color="text-agro-text" />
              <CoverageStat label="С данными" value={coverage.summary.fields_with_any_data} color="text-green-600" />
              <CoverageStat label="Без данных" value={coverage.summary.fields_without_data} color="text-gray-500" />
              <CoverageStat label="Полное покрытие" value={coverage.summary.fields_with_all_requested_indices} color="text-green-600" />
              <CoverageStat label="Частичное" value={coverage.summary.fields_with_partial_indices} color="text-amber-600" />
              <CoverageStat label="Устарело" value={coverage.summary.fields_stale} color="text-red-600" />
              <CoverageStat label="Всего записей" value={coverage.summary.record_count_total} color="text-agro-text" />
            </div>

            {/* Per-index mini summary */}
            {coverage.summary.index_summary && Object.keys(coverage.summary.index_summary).length > 0 && (
              <div>
                <p className="text-xs font-semibold text-agro-muted uppercase tracking-wide mb-2">
                  По индексам
                </p>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
                  {Object.entries(coverage.summary.index_summary).map(([code, idxData]) => {
                    const meta = INDEX_METADATA[code];
                    return (
                      <div key={code} className="p-2 rounded-lg bg-agro-surface2">
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs font-bold text-agro-text">{meta?.label || code.toUpperCase()}</span>
                          <span className="text-[10px] text-agro-muted">{meta?.shortMeaning || ''}</span>
                        </div>
                        <p className="text-sm font-semibold text-agro-text mt-0.5">
                          {idxData.fields_with_data ?? 0} полей
                        </p>
                        {idxData.latest_date && (
                          <p className="text-[10px] text-agro-muted">
                            до {new Date(idxData.latest_date).toLocaleDateString('ru-RU')}
                          </p>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Empty coverage: endpoint returned no summary */}
        {!coverageLoading && !coverageError && coverage && !coverage.summary && (
          <div className="card">
            <div className="flex items-center gap-3 py-4">
              <div className="w-10 h-10 rounded-full bg-gray-100 flex items-center justify-center">
                <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
                  />
                </svg>
              </div>
              <div>
                <p className="text-sm font-medium text-agro-text">Нет данных покрытия</p>
                <p className="text-xs text-agro-muted">Спутниковые индексы ещё не загружены.</p>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Coverage stat inline component ────────────────────────── */
function CoverageStat({ label, value, color }) {
  return (
    <div className="p-2 rounded-lg bg-agro-surface2">
      <p className="text-[11px] text-agro-muted">{label}</p>
      <p className={`text-lg font-bold ${color}`}>{value ?? '—'}</p>
    </div>
  );
}
