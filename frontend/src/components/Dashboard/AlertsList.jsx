import { useState, useEffect } from 'react';
import { getAlerts, acknowledgeAlert } from '../../api/client';

const SEVERITY_ORDER = { critical: 0, warning: 1, info: 2 };

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

function SeverityBadge({ severity }) {
  if (severity === 'critical') return (
    <span className="text-xs font-bold text-red-600">🔴 Критическое</span>
  );
  if (severity === 'warning') return (
    <span className="text-xs font-bold text-amber-600">🟡 Важное</span>
  );
  return <span className="text-xs font-bold text-blue-600">ℹ️ Информационное</span>;
}

function NDVIBadge({ value, threshold }) {
  // ponytail: |value| > 1.0 is a percentage stored as NDVI — skip display
  if (value == null || Math.abs(value) > 1.0) return null;
  return (
    <div className="flex items-center gap-2 mt-1.5 flex-wrap">
      <span className={`px-2 py-0.5 rounded text-xs font-mono font-bold ${
        value < 0.15 ? 'bg-red-100 text-red-700' :
        value < 0.30 ? 'bg-orange-100 text-orange-700' :
        value < 0.45 ? 'bg-yellow-100 text-yellow-700' :
        'bg-green-100 text-green-700'
      }`}>
        NDVI: {value.toFixed(4)}
      </span>
      {threshold != null && (
        <span className="text-xs text-agro-muted">
          порог: {threshold.toFixed(2)}
          {' '}({Math.round((value / threshold) * 100)}% от нормы)
        </span>
      )}
    </div>
  );
}

export default function AlertsList({
  enterpriseId,
  limit = 200,
  onFieldClick,
  onFieldHighlight,
  severityFilter = null,
  alerts: externalAlerts = null,
  loading: externalLoading = null,
  error: externalError = null,
  searchQuery = '',
}) {
  const [internalAlerts, setInternalAlerts] = useState([]);
  const [internalLoading, setInternalLoading] = useState(true);
  const [internalError, setInternalError] = useState(null);
  const [expanded, setExpanded] = useState(new Set());

  const useExternal = externalAlerts !== null;

  useEffect(() => {
    if (!useExternal) loadAlerts();
  }, [enterpriseId, useExternal]);

  async function loadAlerts() {
    setInternalLoading(true);
    setInternalError(null);
    try {
      const params = { limit, is_active: true };
      if (enterpriseId) params.enterprise_id = enterpriseId;
      const data = await getAlerts(params);
      setInternalAlerts(data.sort((a, b) => {
        const sa = SEVERITY_ORDER[a.severity] ?? 3;
        const sb = SEVERITY_ORDER[b.severity] ?? 3;
        if (sa !== sb) return sa - sb;
        return new Date(b.triggered_at) - new Date(a.triggered_at);
      }));
    } catch (err) {
      setInternalError('Ошибка загрузки предупреждений');
      console.error(err);
    } finally {
      setInternalLoading(false);
    }
  }

  const rawAlerts = useExternal ? externalAlerts : internalAlerts;
  const isPending = useExternal ? (externalLoading ?? false) : internalLoading;
  const isError = useExternal ? externalError : internalError;

  // Apply severityFilter
  let filteredBySeverity = severityFilter
    ? rawAlerts.filter(a => a.severity === severityFilter)
    : rawAlerts;

  // Apply searchQuery (case-insensitive match on field_name or title)
  const filteredAlerts = searchQuery?.trim()
    ? filteredBySeverity.filter(a => {
        const q = searchQuery.toLowerCase();
        return (a.field_name || '').toLowerCase().includes(q)
            || (a.title || '').toLowerCase().includes(q);
      })
    : filteredBySeverity;

  async function handleAcknowledge(e, alertId) {
    e.stopPropagation();
    try {
      await acknowledgeAlert(alertId);
      setAlerts(prev => prev.filter(a => a.id !== alertId));
      setExpanded(prev => { const n = new Set(prev); n.delete(alertId); return n; });
    } catch (err) {
      console.error(err);
    }
  }

  // Клик по карточке: раскрыть + подсветить на карте (без смены view)
  function handleCardClick(alert) {
    // Переключаем раскрытие
    setExpanded(prev => {
      const n = new Set(prev);
      n.has(alert.id) ? n.delete(alert.id) : n.add(alert.id);
      return n;
    });
    // Подсвечиваем поле на карте (zoom без navigation)
    if (onFieldHighlight) onFieldHighlight(alert.field_id);
  }

  if (isPending) {
    return (
      <div className="space-y-2">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="h-20 bg-agro-card rounded-lg animate-pulse" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div className="text-center py-6">
        <p className="text-agro-danger text-sm">{isError}</p>
        {!useExternal && (
          <button onClick={loadAlerts} className="text-xs text-agro-accent underline mt-2">Повторить</button>
        )}
      </div>
    );
  }

  if (filteredAlerts.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <div className="text-4xl mb-3">✅</div>
        <p className="text-agro-text font-medium">Все поля в норме</p>
        <p className="text-xs text-agro-muted mt-1">Активных предупреждений нет</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {filteredAlerts.map((alert) => {
        const isExpanded = expanded.has(alert.id);
        const borderColor =
          alert.severity === 'critical' ? 'border-red-200 bg-red-50 hover:bg-red-100' :
          alert.severity === 'warning'  ? 'border-amber-200 bg-amber-50 hover:bg-amber-100' :
                                          'border-blue-200 bg-blue-50 hover:bg-blue-100';

        return (
          <div key={alert.id} className={`rounded-lg border transition-all ${borderColor}`}>

            {/* Основная строка — всегда видна, кликабельна */}
            <div
              className="p-3 cursor-pointer select-none"
              onClick={() => handleCardClick(alert)}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex-1 min-w-0">

                  {/* Severity + дата */}
                  <div className="flex items-center justify-between mb-1">
                    <SeverityBadge severity={alert.severity} />
                    <span className="text-xs text-agro-muted">
                      {new Date(alert.triggered_at).toLocaleDateString('ru-RU')}
                    </span>
                  </div>

                  {/* Тип */}
                  <p className="text-xs text-agro-muted mb-0.5">
                    {ALERT_TYPE_LABELS[alert.alert_type] || alert.alert_type}
                  </p>

                  {/* Поле */}
                  <p className="text-sm font-semibold text-agro-accent truncate">
                    {alert.field_name || `Поле #${alert.field_id}`}
                  </p>

                  {/* NDVI */}
                  <NDVIBadge
                    value={alert.triggered_value}
                    threshold={alert.threshold_value}
                  />
                </div>

                {/* Кнопки справа */}
                <div className="flex flex-col items-end gap-2 flex-shrink-0">
                  <button
                    onClick={(e) => handleAcknowledge(e, alert.id)}
                    className="text-xs text-agro-muted hover:text-green-400 transition-colors"
                    title="Отметить просмотренным"
                  >
                    ✓
                  </button>
                  <span className="text-agro-muted text-xs mt-1">
                    {isExpanded ? '▲' : '▼'}
                  </span>
                </div>
              </div>
            </div>

            {/* Раскрытые детали */}
            {isExpanded && (
              <div
                className="px-3 pb-3 pt-2 border-t border-agro-border space-y-2"
                onClick={e => e.stopPropagation()}
              >
                {/* Ситуация */}
                {alert.description && (
                  <div>
                    <p className="text-xs font-semibold text-agro-muted uppercase tracking-wide mb-1">
                      📊 Ситуация
                    </p>
                    <p className="text-xs text-agro-text leading-relaxed">
                      {alert.description}
                    </p>
                  </div>
                )}

                {/* Рекомендация */}
                {alert.recommendation && (
                  <div className={`rounded p-2.5 ${
                    alert.severity === 'critical'
                      ? 'bg-red-50 border border-red-200'
                      : 'bg-amber-50 border border-amber-200'
                  }`}>
                    <p className="text-xs font-semibold text-agro-muted uppercase tracking-wide mb-1">
                      💡 Рекомендация
                    </p>
                    <p className="text-xs text-agro-text leading-relaxed">
                      {alert.recommendation}
                    </p>
                  </div>
                )}

                {/* Кнопка перехода к полю */}
                {onFieldClick && (
                  <button
                    onClick={() => onFieldClick(alert.field_id)}
                    className="w-full py-2 rounded text-xs font-medium bg-agro-accent/20 text-agro-accent hover:bg-agro-accent/30 transition-colors"
                  >
                    🗺 Открыть детальную карточку поля →
                  </button>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
