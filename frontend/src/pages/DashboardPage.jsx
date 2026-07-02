import { useState, useEffect, useCallback, useMemo } from 'react';
import { getCachedDashboardSummary, getAlerts } from '../api/client';
import SummaryCards from '../components/Dashboard/SummaryCards';
import EnterpriseList from '../components/Dashboard/EnterpriseList';

export default function DashboardPage({ onNavigate, onFieldClick, onFieldHighlight }) {
  const [summary, setSummary] = useState(null);
  const [allAlerts, setAllAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [summaryData, alertsData] = await Promise.all([
        getCachedDashboardSummary(),
        getAlerts({ is_active: true, limit: 20 }),
      ]);
      setSummary(summaryData);
      setAllAlerts(Array.isArray(alertsData) ? alertsData : []);
    } catch (err) {
      setError('Не удалось загрузить данные дашборда');
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Разделяем на critical и warning
  const criticalAlerts = useMemo(
    () => allAlerts.filter(a => a.severity === 'critical'),
    [allAlerts]
  );
  const warningAlerts = useMemo(
    () => allAlerts.filter(a => a.severity === 'warning'),
    [allAlerts]
  );

  const lastUpdated = summary?.last_updated
    ? new Date(summary.last_updated).toLocaleString('ru-RU')
    : null;

  function AlertRow({ alert }) {
    return (
      <button
        key={alert.id}
        onClick={() => onFieldHighlight?.(alert.field_id)}
        className="w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors text-left"
      >
        {/* Иконка severity */}
        <span className="text-sm flex-shrink-0">
          {alert.severity === 'critical' ? '🔴' : '🟡'}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-agro-text truncate">
            {alert.field_name || `Поле #${alert.field_id}`}
          </p>
          <p className="text-xs text-agro-muted truncate">{alert.title}</p>
        </div>
        <div className="text-right flex-shrink-0">
          {alert.triggered_value != null && Math.abs(alert.triggered_value) <= 1.0 && (
            <span className={`text-xs font-mono ${
              alert.severity === 'critical' ? 'text-red-600' : 'text-amber-600'
            }`}>
              NDVI: {alert.triggered_value.toFixed(4)}
            </span>
          )}
          <p className="text-[10px] text-agro-muted mt-0.5">
            {new Date(alert.triggered_at).toLocaleDateString('ru-RU')}
          </p>
        </div>
      </button>
    );
  }

  function AlertsSection({ title, alerts, bgClass, textClass }) {
    if (alerts.length === 0) return null;
    return (
      <div className={`rounded-lg border border-agro-border ${bgClass}`}>
        <div className="flex items-center gap-2 px-3 py-2 border-b border-agro-border">
          <h3 className={`text-sm font-semibold ${textClass}`}>{title}</h3>
          <span className={`text-xs px-1.5 py-0.5 rounded-full ${textClass}/20 ${textClass}`}>
            {alerts.length}
          </span>
        </div>
        <div className="divide-y divide-agro-border">
          {alerts.map(alert => <AlertRow key={alert.id} alert={alert} />)}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Верхняя фиксированная часть */}
      <div className="p-4 lg:p-6 pt-16 pb-0 space-y-4">
        <div>
          <p className="text-sm text-agro-muted">
            Бухоро Агрокластер
            {lastUpdated && ` · Данные от ${lastUpdated}`}
          </p>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
            <p>{error}</p>
            <button onClick={loadData} className="underline mt-1 font-medium">Повторить</button>
          </div>
        )}

        <SummaryCards summary={summary} loading={loading} />

        {/* Кнопка "Показать все предупреждения" */}
        {allAlerts.length > 0 && (
          <button
            onClick={() => onNavigate('alerts')}
            className="w-full py-2.5 rounded-lg text-sm font-medium
              bg-agro-card text-agro-text
              hover:bg-agro-accent hover:text-white
              transition-colors"
          >
            Показать все предупреждения → ({summary?.active_alerts ?? allAlerts.length})
          </button>
        )}
      </div>

      {/* Скроллируемая нижняя часть — алерты */}
      <div className="flex-1 overflow-y-auto min-h-0 p-4 lg:p-6 pt-4 space-y-4">
        {loading ? (
          <>
            <div className="space-y-2">
              {[...Array(4)].map((_, i) => (
                <div key={i} className="h-14 bg-agro-card rounded animate-pulse" />
              ))}
            </div>
          </>
        ) : allAlerts.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <div className="text-4xl mb-3">✅</div>
            <p className="text-agro-text font-medium">Все поля в норме</p>
            <p className="text-xs text-agro-muted mt-1">Активных предупреждений нет</p>
          </div>
        ) : (
          <>
            <AlertsSection
              title="🔴 Критические"
              alerts={criticalAlerts}
              bgClass="bg-red-50"
              textClass="text-red-700"
            />
            <AlertsSection
              title="🟡 Важные"
              alerts={warningAlerts}
              bgClass="bg-amber-50"
              textClass="text-amber-700"
            />
          </>
        )}
      </div>
    </div>
  );
}
