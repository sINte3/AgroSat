import { useState, useEffect } from 'react';
import { getField, getFieldAlerts, acknowledgeAlert } from '../../api/client';
import NDVIChart from './NDVIChart';
import WeatherWidget from './WeatherWidget';

export default function FieldDetail({ fieldId, onBack }) {
  const [field, setField] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('info');

  useEffect(() => {
    if (fieldId) loadField();
  }, [fieldId]);

  async function loadField() {
    setLoading(true);
    setError(null);
    try {
      const [fieldData, alertsData] = await Promise.all([
        getField(fieldId),
        getFieldAlerts(fieldId, { limit: 50 }),
      ]);
      setField(fieldData);
      setAlerts(Array.isArray(alertsData) ? alertsData : []);
    } catch (err) {
      setError('Ошибка загрузки данных поля');
      console.error(err);
    } finally {
      setLoading(false);
    }
  }

  async function handleAcknowledge(alertId) {
    try {
      await acknowledgeAlert(alertId);
      setAlerts((prev) => prev.filter((a) => a.id !== alertId));
    } catch (err) {
      console.error(err);
    }
  }

  if (loading) {
    return (
      <div className="p-4 space-y-4">
        <div className="animate-pulse space-y-4">
          <div className="h-8 bg-agro-surface2 rounded w-48" />
          <div className="h-24 bg-agro-surface2 rounded" />
          <div className="h-48 bg-agro-surface2 rounded" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 text-center">
        <p className="text-agro-danger mb-4">{error}</p>
        <button onClick={loadField} className="btn-primary">Повторить</button>
      </div>
    );
  }

  if (!field) return null;

  // API возвращает GeoJSON Feature — данные в properties
  const p = field.properties || field;

  const tabs = [
    { key: 'info', label: 'Инфо' },
    { key: 'ndvi', label: 'NDVI' },
    { key: 'weather', label: 'Погода' },
    { key: 'alerts', label: `Алерты (${alerts.length})` },
  ];

  return (
    <div className="flex flex-col h-full">
      {/* Шапка */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-agro-surface2">
        <button onClick={onBack} className="text-agro-muted hover:text-agro-text transition-colors">
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <div>
          <h2 className="font-semibold text-agro-text">{p.name || '—'}</h2>
          <p className="text-xs text-agro-muted">{p.enterprise_name || '—'}</p>
        </div>
      </div>

      {/* Табы */}
      <div className="flex border-b border-agro-surface2 px-4">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2.5 text-sm border-b-2 transition-colors ${
              activeTab === tab.key
                ? 'border-agro-accent text-agro-accent'
                : 'border-transparent text-agro-muted hover:text-agro-text'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Контент */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {activeTab === 'info' && (
          <div className="space-y-4">
            {/* NDVI карточка вверху */}
            {p.last_ndvi != null && (
              <div className="card flex items-center justify-between">
                <div>
                  <p className="text-xs text-agro-muted">Последний NDVI</p>
                  <p className={`text-2xl font-bold ${
                    p.last_ndvi >= 0.6 ? 'text-agro-accent'
                    : p.last_ndvi >= 0.3 ? 'text-yellow-400'
                    : 'text-red-400'
                  }`}>
                    {p.last_ndvi.toFixed(4)}
                  </p>
                  <p className="text-xs text-agro-muted">{p.last_ndvi_date || '—'}</p>
                </div>
                {p.ndvi_change_pct != null && (
                  <div className={`text-right ${p.ndvi_change_pct >= 0 ? 'text-agro-accent' : 'text-red-400'}`}>
                    <p className="text-sm font-semibold">
                      {p.ndvi_change_pct >= 0 ? '+' : ''}{p.ndvi_change_pct.toFixed(1)}%
                    </p>
                    <p className="text-xs text-agro-muted">vs предыдущий</p>
                  </div>
                )}
              </div>
            )}

            <div className="card">
              <h3 className="font-semibold text-sm mb-3">Основная информация</h3>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <InfoRow label="Код" value={p.code || '—'} />
                <InfoRow label="Площадь" value={p.area_ha ? `${p.area_ha.toFixed(1)} га` : '—'} />
                <InfoRow label="Тип орошения" value={irrigationLabel(p.irrigation_type)} />
                <InfoRow label="Тип почвы" value={p.soil_type || '—'} />
                <InfoRow label="Координаты" value={
                  p.centroid_lat && p.centroid_lon
                    ? `${p.centroid_lat.toFixed(4)}, ${p.centroid_lon.toFixed(4)}`
                    : '—'
                } />
                <InfoRow label="Культура" value={p.current_crop || '—'} />
              </div>
            </div>

            {/* Сезон */}
            {(p.season_year || p.planting_date) && (
              <div className="card">
                <h3 className="font-semibold text-sm mb-3">Сезон {p.season_year}</h3>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <InfoRow label="Культура" value={p.current_crop || '—'} />
                  <InfoRow label="Сорт" value={p.variety || '—'} />
                  <InfoRow label="Посев" value={
                    p.planting_date
                      ? new Date(p.planting_date).toLocaleDateString('ru-RU')
                      : '—'
                  } />
                  <InfoRow label="Год" value={String(p.season_year || '—')} />
                </div>
              </div>
            )}

            {p.notes && (
              <div className="card">
                <h3 className="font-semibold text-sm mb-2">Заметки</h3>
                <p className="text-sm text-agro-muted whitespace-pre-wrap">{p.notes}</p>
              </div>
            )}
          </div>
        )}

        {activeTab === 'ndvi' && (
          <div style={{ padding: '12px 0' }}>
            <NDVIChart fieldId={typeof fieldId === 'string' ? parseInt(fieldId) : fieldId} />
          </div>
        )}

        {activeTab === 'weather' && (
          <div style={{ padding: '12px 0' }}>
            <WeatherWidget fieldId={typeof fieldId === 'string' ? parseInt(fieldId) : fieldId} />
          </div>
        )}

        {activeTab === 'alerts' && (
          <div className="space-y-2">
            {alerts.length === 0 ? (
              <div className="card text-center py-6">
                <p className="text-agro-muted">Нет алертов для этого поля</p>
              </div>
            ) : (
              alerts.map((alert) => (
                <div
                  key={alert.id}
                  className={`p-3 rounded-lg border ${
                    alert.severity === 'critical'
                      ? 'bg-red-50 border-red-200'
                      : alert.severity === 'warning'
                      ? 'bg-amber-50 border-amber-200'
                      : 'bg-blue-50 border-blue-200'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1">
                      <p className="text-xs font-medium text-agro-text mb-1">{alert.title}</p>
                      <p className="text-xs text-agro-muted">{alert.description}</p>
                      {alert.recommendation && (
                        <p className="text-xs text-agro-accent mt-1 italic">{alert.recommendation}</p>
                      )}
                      <p className="text-xs text-agro-muted mt-1">
                        {new Date(alert.triggered_at).toLocaleString('ru-RU')}
                      </p>
                    </div>
                    {!alert.acknowledged_at && (
                      <button
                        onClick={() => handleAcknowledge(alert.id)}
                        className="text-xs text-agro-accent hover:underline flex-shrink-0"
                      >
                        Подтвердить
                      </button>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function InfoRow({ label, value }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-agro-muted">{label}</span>
      <span className="text-agro-text">{value}</span>
    </div>
  );
}

function irrigationLabel(type) {
  const labels = {
    canal: 'Канальное',
    drip: 'Капельное',
    sprinkler: 'Дождевание',
    rainfed: 'Богара',
  };
  return labels[type] || type || '—';
}
