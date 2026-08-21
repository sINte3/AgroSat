import { useState, useEffect, useMemo } from 'react';
import { getAlerts } from '../api/client';
import AlertsList from '../components/Dashboard/AlertsList';

const FILTERS = [
  { key: null, label: 'Все' },
  { key: 'critical', label: '🔴 Критические' },
  { key: 'warning', label: '🟡 Важные' },
  { key: 'info', label: 'ℹ️ Информационные' },
];

export default function AlertsPage({ onFieldClick, onFieldHighlight, onNavigate }) {
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [severityFilter, setSeverityFilter] = useState(null);
  const [searchText, setSearchText] = useState('');

  useEffect(() => {
    loadAlerts();
  }, []);

  async function loadAlerts() {
    setLoading(true);
    setError(null);
    try {
      const data = await getAlerts({ limit: 200, is_active: true });
      setAlerts(data);
    } catch (err) {
      setError('Ошибка загрузки предупреждений');
      console.error(err);
    } finally {
      setLoading(false);
    }
  }

  const handleAlertAcknowledged = (alertId) => {
    setAlerts(prev => prev.filter(a => a.id !== alertId));
  };

  const counts = useMemo(() => {
    const total = alerts.length;
    const bySeverity = { critical: 0, warning: 0, info: 0 };
    alerts.forEach((a) => {
      if (bySeverity[a.severity] !== undefined) bySeverity[a.severity]++;
    });
    return { total, ...bySeverity };
  }, [alerts]);

  if (error) {
    return (
      <div className="p-6 pt-16 h-full flex flex-col">
        <div className="text-center py-12">
          <p className="text-agro-danger mb-4">{error}</p>
          <button onClick={loadAlerts} className="btn-primary">Повторить</button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 pt-16 h-full flex flex-col">
      {/* Заголовок */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-bold text-agro-text">Предупреждения</h2>
          {!loading && (
            <span className="bg-agro-accent/20 text-agro-accent text-sm font-medium px-2.5 py-0.5 rounded-full">
              {counts.total}
            </span>
          )}
        </div>
      </div>

      {/* Сводка */}
      {!loading && counts.total > 0 && (
        <div className="flex items-center gap-3 mb-4 flex-wrap text-sm">
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-50 border border-agro-border">
            <span className="font-medium text-agro-text">{counts.total}</span>
            <span className="text-agro-muted">всего</span>
          </div>
          {counts.critical > 0 && (
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-50 border border-red-200">
              <span className="w-2 h-2 rounded-full bg-red-500" />
              <span className="font-medium text-red-700">{counts.critical}</span>
              <span className="text-red-500">критичных</span>
            </div>
          )}
          {counts.warning > 0 && (
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-50 border border-amber-200">
              <span className="w-2 h-2 rounded-full bg-amber-500" />
              <span className="font-medium text-amber-700">{counts.warning}</span>
              <span className="text-amber-500">важных</span>
            </div>
          )}
          {counts.info > 0 && (
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-50 border border-blue-200">
              <span className="w-2 h-2 rounded-full bg-blue-500" />
              <span className="font-medium text-blue-700">{counts.info}</span>
              <span className="text-blue-500">информационных</span>
            </div>
          )}
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-green-50 border border-green-200">
            <span className="w-2 h-2 rounded-full bg-green-500" />
            <span className="font-medium text-green-700">Активные</span>
          </div>
        </div>
      )}

      {/* Фильтры */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        {FILTERS.map((f) => {
          const isActive = severityFilter === f.key;
          const count = f.key ? counts[f.key] : counts.total;
          return (
            <button
              key={f.key ?? 'all'}
              onClick={() => setSeverityFilter(f.key)}
              className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                isActive
                  ? 'bg-agro-accent/20 text-agro-accent border border-agro-accent/30'
                  : 'bg-white text-agro-muted hover:text-agro-text border border-agro-border'
              }`}
            >
              {f.label}{' '}
              <span className="ml-1 text-xs opacity-70">({count})</span>
            </button>
          );
        })}

        {/* Поиск по полю */}
        <div className="ml-auto relative">
          <input
            type="text"
            placeholder="Поиск по полю..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            className="input text-sm pl-8 pr-3 py-1.5 w-48"
          />
          <svg
            className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-agro-muted"
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
        </div>
      </div>

      {/* Список */}
      <div className="flex-1 overflow-y-auto min-h-0">
        <AlertsList
          alerts={alerts}
          loading={loading}
          error={error}
          severityFilter={severityFilter}
          searchQuery={searchText}
          limit={200}
          onFieldClick={onFieldClick}
          onFieldHighlight={onFieldHighlight}
          onAlertAcknowledged={handleAlertAcknowledged}
          onInspectionCreated={(inspection) => onNavigate?.('field-inspection-detail', inspection.id)}
        />
      </div>
    </div>
  );
}
