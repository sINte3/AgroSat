import { useCallback, useState, useEffect, useMemo } from 'react';
import { getAlerts, acknowledgeAlert } from '../../api/client';
import { useAuth } from '../../context/AuthContext';
import InspectionSourceDialog from '../Inspections/InspectionSourceDialog';

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

/* ───── SeverityBadge ───────────────────────────────────────────── */
function SeverityBadge({ severity }) {
  if (severity === 'critical') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-red-100 text-red-700 text-xs font-semibold">
        <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
        Критично
      </span>
    );
  }
  if (severity === 'warning') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-amber-100 text-amber-700 text-xs font-semibold">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
        Высокий риск
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-blue-100 text-blue-700 text-xs font-semibold">
      <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
      Инфо
    </span>
  );
}

/* ───── StatusBadge ──────────────────────────────────────────────── */
function StatusBadge({ isAcknowledged }) {
  if (isAcknowledged) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-gray-100 text-gray-600 text-xs font-medium">
        <span className="w-1.5 h-1.5 rounded-full bg-gray-400" />
        Принято
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-green-100 text-green-700 text-xs font-medium">
      <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
      Новое
    </span>
  );
}

/* ───── NDVIBadge ────────────────────────────────────────────────── */
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

/* ───── AlertCard ────────────────────────────────────────────────── */
function AlertCard({
  alert,
  isExpanded,
  onToggle,
  acknowledgingIds,
  onAcknowledge,
  onFieldClick,
  onFieldHighlight,
  onCreateInspection,
}) {
  const severityKey = alert.severity || 'info';

  const borderColor =
    severityKey === 'critical' ? 'border-l-red-500 bg-red-50/60 hover:bg-red-50' :
    severityKey === 'warning'  ? 'border-l-amber-500 bg-amber-50/60 hover:bg-amber-50' :
                                 'border-l-blue-500 bg-blue-50/60 hover:bg-blue-50';

  const isAcknowledging = acknowledgingIds.has(alert.id);

  // Sentinels for data presence
  const hasDescription = !!alert.description;
  const hasRecommendation = !!alert.recommendation;
  const hasFieldClick = !!onFieldClick;

  return (
    <div
      data-testid={`alert-card-${alert.id}`}
      className={`rounded-lg border border-l-4 ${borderColor} transition-colors cursor-pointer`}
      onClick={() => {
        onToggle(alert.id);
        if (onFieldHighlight) onFieldHighlight(alert.field_id);
      }}
    >
      {/* ── Compact row ── */}
      <div className="p-3">
        <div className="flex items-start gap-3">
          {/* Left: severity + title + meta */}
          <div className="flex-1 min-w-0 space-y-1.5">
            {/* Severity + Status row */}
            <div className="flex items-center gap-2 flex-wrap">
              <SeverityBadge severity={severityKey} />
              <StatusBadge isAcknowledged={false} />
            </div>

            {/* Title / type */}
            <p className="text-sm font-semibold text-agro-text leading-tight">
              {ALERT_TYPE_LABELS[alert.alert_type] || alert.alert_type || alert.title}
            </p>

            {/* Field + Date row */}
            <div className="flex items-center gap-3 flex-wrap text-xs text-agro-muted">
              <span className="inline-flex items-center gap-1 text-agro-accent font-medium">
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"
                  />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"
                  />
                </svg>
                {alert.field_name || `Поле #${alert.field_id}`}
              </span>
              <span className="inline-flex items-center gap-1">
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
                  />
                </svg>
                {new Date(alert.triggered_at).toLocaleDateString('ru-RU')}
              </span>
            </div>

            {/* NDVI value if present */}
            <NDVIBadge value={alert.triggered_value} threshold={alert.threshold_value} />
          </div>

          {/* Right: actions */}
          <div className="flex flex-col items-center gap-2 flex-shrink-0 pt-1">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onAcknowledge(e, alert.id);
              }}
              disabled={isAcknowledging}
              className={`w-8 h-8 flex items-center justify-center rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                isAcknowledging
                  ? 'bg-gray-100 text-gray-300'
                  : 'bg-green-100 text-green-600 hover:bg-green-200 hover:text-green-700'
              }`}
              title="Подтвердить"
            >
              {isAcknowledging ? (
                <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                  />
                </svg>
              ) : (
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              )}
            </button>
            <span className="text-agro-muted text-xs">
              {isExpanded ? '▲' : '▼'}
            </span>
          </div>
        </div>
      </div>

      {/* ── Expanded details ── */}
      {isExpanded && (
        <div
          className="px-3 pb-3 pt-2 border-t border-agro-border space-y-2"
          onClick={e => e.stopPropagation()}
        >
          {hasDescription && (
            <div>
              <p className="text-xs font-semibold text-agro-muted uppercase tracking-wide mb-1">
                📊 Ситуация
              </p>
              <p className="text-xs text-agro-text leading-relaxed">
                {alert.description}
              </p>
            </div>
          )}

          {hasRecommendation && (
            <div className={`rounded p-2.5 ${
              severityKey === 'critical'
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

          {hasFieldClick && (
            <button
              onClick={() => onFieldClick(alert.field_id)}
              className="w-full py-2 rounded text-xs font-medium bg-agro-accent/20 text-agro-accent hover:bg-agro-accent/30 transition-colors"
            >
              🗺 Открыть детальную карточку поля →
            </button>
          )}
          {onCreateInspection && (
            <button
              type="button"
              onClick={() => onCreateInspection(alert)}
              className="min-h-11 w-full rounded-lg bg-green-700 px-3 py-2 text-sm font-bold text-white hover:bg-green-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-800 focus-visible:ring-offset-2"
            >
              Создать осмотр
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────── */
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
  onAlertAcknowledged = null,
  onInspectionCreated = null,
}) {
  const { user } = useAuth();
  const [internalAlerts, setInternalAlerts] = useState([]);
  const [internalLoading, setInternalLoading] = useState(true);
  const [internalError, setInternalError] = useState(null);
  const [expanded, setExpanded] = useState(new Set());
  const [acknowledgingIds, setAcknowledgingIds] = useState(new Set());
  const [ackError, setAckError] = useState(null);
  const [inspectionAlert, setInspectionAlert] = useState(null);
  const closeInspectionDialog = useCallback(() => setInspectionAlert(null), []);
  const canCreateInspection = ['admin', 'manager'].includes(String(user?.role || '').toLowerCase());

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
    if (acknowledgingIds.has(alertId)) return;
    setAckError(null);
    setAcknowledgingIds(prev => { const n = new Set(prev); n.add(alertId); return n; });
    try {
      await acknowledgeAlert(alertId);
      if (useExternal) {
        onAlertAcknowledged?.(alertId);
      } else {
        setInternalAlerts(prev => prev.filter(a => a.id !== alertId));
      }
      setExpanded(prev => { const n = new Set(prev); n.delete(alertId); return n; });
    } catch (err) {
      console.error(err);
      setAckError('Не удалось подтвердить предупреждение');
    } finally {
      setAcknowledgingIds(prev => { const n = new Set(prev); n.delete(alertId); return n; });
    }
  }

  // Клик по карточке: раскрыть + подсветить на карте
  function handleToggle(alertId) {
    setExpanded(prev => {
      const n = new Set(prev);
      n.has(alertId) ? n.delete(alertId) : n.add(alertId);
      return n;
    });
  }

  /* ── Loading skeleton ─────────────────────────────────────────── */
  if (isPending) {
    return (
      <div className="space-y-2">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="h-24 bg-agro-card rounded-lg animate-pulse border border-agro-border" />
        ))}
      </div>
    );
  }

  /* ── Error state ──────────────────────────────────────────────── */
  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <div className="w-12 h-12 rounded-full bg-red-100 flex items-center justify-center mb-3">
          <svg className="w-6 h-6 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
        </div>
        <p className="text-sm text-agro-danger font-medium mb-2">{isError}</p>
        {!useExternal && (
          <button onClick={loadAlerts} className="text-xs text-agro-accent underline mt-1">Повторить</button>
        )}
      </div>
    );
  }

  /* ── Empty state ──────────────────────────────────────────────── */
  if (filteredAlerts.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <div className="w-16 h-16 rounded-full bg-green-100 flex items-center justify-center mb-4">
          <svg className="w-8 h-8 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <p className="text-agro-text font-semibold text-lg">Все поля в норме</p>
        <p className="text-sm text-agro-muted mt-1">Активных предупреждений нет</p>
      </div>
    );
  }

  /* ── Alert list ───────────────────────────────────────────────── */
  return (
    <div className="space-y-2">
      {ackError && (
        <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
          <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
          <span>{ackError}</span>
        </div>
      )}

      {/* Severity-sorted alert cards */}
      {filteredAlerts.map((alert) => (
        <AlertCard
          key={alert.id}
          alert={alert}
          isExpanded={expanded.has(alert.id)}
          onToggle={handleToggle}
          acknowledgingIds={acknowledgingIds}
          onAcknowledge={handleAcknowledge}
          onFieldClick={onFieldClick}
          onFieldHighlight={onFieldHighlight}
          onCreateInspection={canCreateInspection ? setInspectionAlert : null}
        />
      ))}
      {inspectionAlert && (
        <InspectionSourceDialog
          source={{
            kind: 'alert',
            field_id: inspectionAlert.field_id,
            field_name: inspectionAlert.field_name,
            alert_id: inspectionAlert.id,
            reason: inspectionAlert.description || inspectionAlert.title || 'Проверить предупреждение на поле',
            priority: inspectionAlert.severity === 'critical' ? 'urgent' : inspectionAlert.severity === 'warning' ? 'high' : 'normal',
          }}
          onClose={closeInspectionDialog}
          onCreated={(inspection) => {
            setInspectionAlert(null);
            onInspectionCreated?.(inspection);
          }}
        />
      )}
    </div>
  );
}
