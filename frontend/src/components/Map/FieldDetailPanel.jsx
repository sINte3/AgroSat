import { useState, useEffect } from 'react';
import apiClient from '../../api/client';
import FieldIntelligence from '../Field/FieldIntelligence';

const severityColors = {
  critical: { bg: 'bg-red-50', text: 'text-red-700', dot: 'bg-red-500' },
  warning: { bg: 'bg-amber-50', text: 'text-amber-700', dot: 'bg-amber-500' },
  info: { bg: 'bg-blue-50', text: 'text-blue-700', dot: 'bg-blue-500' },
};

// ─── Main panel component ─────────────────────────────────────────────────────────
export default function FieldDetailPanel({ field, onBack, onNavigate }) {
  const [alerts, setAlerts] = useState([]);
  const [weather, setWeather] = useState(null);
  const [ndviLoading, setNdviLoading] = useState(false);

  // ─── Fetch alerts + weather ────────────────────────────────────────────────────
  useEffect(() => {
    if (!field?.id) return;
    setNdviLoading(true);

    Promise.allSettled([
      apiClient.get(`/api/alerts/?field_id=${field.id}`),
      apiClient.get(`/api/weather/field/${field.id}`),
    ]).then(([alertRes, weatherRes]) => {
      if (alertRes.status === 'fulfilled') {
        const data = alertRes.value.data;
        setAlerts(Array.isArray(data) ? data.filter(a => a.is_active !== false) : []);
      }
      if (weatherRes.status === 'fulfilled') {
        setWeather(weatherRes.value.data);
      }
      setNdviLoading(false);
    });
  }, [field?.id]);

  if (!field) return null;

  return (
    <div className="flex flex-col h-full">
      {/* ── Header with back button ──────────────────────────────────────── */}
      <div className="flex items-center gap-2 p-3 border-b border-gray-200 bg-white sticky top-0 z-10">
        <button
          onClick={onBack}
          className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors"
          title="Назад к списку"
        >
          <svg className="w-5 h-5 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-gray-900 truncate">{field.name}</h3>
          <p className="text-xs text-gray-500">{field.enterprise_name || ''}</p>
        </div>
        {onNavigate && (
          <button
            onClick={() => onNavigate('field-detail', field.id)}
            className="text-xs text-green-600 hover:text-green-700 whitespace-nowrap font-medium"
            title="Открыть полную карточку"
          >
            Подробнее →
          </button>
        )}
      </div>

      {/* ── Scrollable content ───────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto">
        {/* ── Field Intelligence — multi-index cards, chart, health ────── */}
      <div className="p-3 border-b border-gray-100">
        <FieldIntelligence field={field} fieldId={field?.id} />
      </div>

        {/* ── Active Alerts ────────────────────────────────────────────── */}
        <div className="p-3 border-b border-gray-100">
          <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-2">
            Алерты ({alerts.length})
          </h4>
          {ndviLoading ? (
            <div className="text-xs text-gray-400">Загрузка...</div>
          ) : alerts.length === 0 ? (
            <div className="text-xs text-gray-400 py-2">Нет активных алертов ✓</div>
          ) : (
            <div className="space-y-1.5 max-h-40 overflow-y-auto">
              {alerts.slice(0, 5).map((alert, i) => {
                const sev = severityColors[alert.severity] || severityColors.info;
                return (
                  <div key={alert.id || i} className={`${sev.bg} rounded-lg p-2`}>
                    <div className="flex items-start gap-1.5">
                      <div className={`w-1.5 h-1.5 rounded-full mt-1 ${sev.dot}`} />
                      <div className="flex-1 min-w-0">
                        <div className={`text-xs font-medium ${sev.text} truncate`}>
                          {alert.title || alert.alert_type}
                        </div>
                        {alert.recommendation && (
                          <div className="text-xs text-gray-600 mt-0.5 line-clamp-2">
                            {alert.recommendation}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
              {alerts.length > 5 && (
                <div className="text-xs text-gray-500 text-center pt-1">
                  +{alerts.length - 5} ещё
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Weather ──────────────────────────────────────────────────────── */}
        <div className="p-3">
          <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-2">
            Погода
          </h4>
          {ndviLoading ? (
            <div className="text-xs text-gray-400">Загрузка...</div>
          ) : !weather ? (
            <div className="text-xs text-gray-400">Нет данных о погоде</div>
          ) : (
            <WeatherMini data={weather} />
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Mini weather widget for the panel ──────────────────────────────────────────── */
function WeatherMini({ data }) {
  const current = data.current || data;
  const daily = data.forecast || [];

  const weatherIcon = (code) => {
    if (code <= 1) return '☀️';
    if (code <= 3) return '⛅';
    if (code <= 48) return '🌫️';
    if (code <= 67) return '🌧️';
    if (code <= 77) return '❄️';
    if (code <= 82) return '🌧️';
    if (code <= 86) return '❄️';
    if (code >= 95) return '⛈️';
    return '🌤️';
  };

  return (
    <div>
      {/* Current temperature */}
      {current?.temperature != null && (
        <div className="flex items-center gap-2 mb-2">
          <span className="text-lg">{weatherIcon(current.weather_code || 0)}</span>
          <span className="text-lg font-semibold text-gray-900">
            {Math.round(current.temperature)}°C
          </span>
          {current.humidity != null && (
            <span className="text-xs text-gray-500">💧 {current.humidity}%</span>
          )}
          {current.wind_speed != null && (
            <span className="text-xs text-gray-500">
              💨 {Math.round(current.wind_speed)} км/ч
            </span>
          )}
        </div>
      )}

      {/* Daily forecast — up to 5 days */}
      {daily.length > 0 && (
        <div className="grid grid-cols-5 gap-1 mt-1">
          {daily.slice(0, 5).map((day, i) => (
            <div key={day.date || i} className="text-center">
              <div className="text-xs text-gray-500">
                {new Date(day.date).toLocaleDateString('ru', { weekday: 'short' })}
              </div>
              <div className="text-sm">{weatherIcon(day.weather_code || 0)}</div>
              <div className="text-xs text-gray-700 font-medium">
                {day.temp_max != null ? `${Math.round(day.temp_max)}°` : '—'}
              </div>
              <div className="text-xs text-gray-400">
                {day.temp_min != null ? `${Math.round(day.temp_min)}°` : ''}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Precipitation sum */}
      {daily.length > 0 && (
        <div className="text-xs text-gray-500 mt-2">
          Осадки за 5 дн: {daily.slice(0, 5).reduce((s, d) => s + (d.precipitation || 0), 0).toFixed(1)} мм
        </div>
      )}
    </div>
  );
}
