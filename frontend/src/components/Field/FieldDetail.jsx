import { useState, useEffect, useRef, useCallback } from 'react';
import { getField, getFieldAlerts, acknowledgeAlert, getSatelliteIndexLatest, getSatelliteIndexHistory } from '../../api/client';
import NDVIChart from './NDVIChart';
import WeatherWidget from './WeatherWidget';
import FieldTelematicsPanel from './FieldTelematicsPanel';
import YieldMapImportPanel from './YieldMapImportPanel';
import ProductivityZonePanel from './ProductivityZonePanel';
import VariableRateRecommendationPanel from './VariableRateRecommendationPanel';
import { FIRST_PILOT_FEATURES } from '../../config/pilotFeatures';

export default function FieldDetail({ fieldId, onBack }) {
  const [field, setField] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('info');

  // Multi-index state (SAVI, EVI, NDMI, NDRE)
  const MULTI_INDICES = ['savi', 'evi', 'ndmi', 'ndre'];
  const INDEX_LABELS = { savi: 'SAVI', evi: 'EVI', ndmi: 'NDMI', ndre: 'NDRE' };
  const [activeIndex, setActiveIndex] = useState('ndvi');
  const [latestIndex, setLatestIndex] = useState(null);
  const [indexHistory, setIndexHistory] = useState([]);
  const [indexLoading, setIndexLoading] = useState(false);
  const [indexError, setIndexError] = useState(null);
  const indexMounted = useRef(true);

  const fetchMultiIndex = useCallback(async (fid, code) => {
    if (!fid || code === 'ndvi') { setLatestIndex(null); setIndexHistory([]); return; }
    setIndexLoading(true);
    setIndexError(null);
    try {
      const [latestRes, historyRes] = await Promise.all([
        getSatelliteIndexLatest(fid, code),
        getSatelliteIndexHistory(fid, code, { days: 30 }),
      ]);
      if (!indexMounted.current) return;
      setLatestIndex(latestRes);
      setIndexHistory(Array.isArray(historyRes?.records) ? historyRes.records : []);
    } catch (err) {
      if (!indexMounted.current) return;
      setIndexError('Ошибка загрузки индекса');
      setLatestIndex(null);
      setIndexHistory([]);
      console.error(err);
    } finally {
      if (indexMounted.current) setIndexLoading(false);
    }
  }, []);

  useEffect(() => {
    indexMounted.current = true;
    return () => { indexMounted.current = false; };
  }, []);

  useEffect(() => {
    if (fieldId && activeTab === 'indices') {
      fetchMultiIndex(typeof fieldId === 'string' ? parseInt(fieldId) : fieldId, activeIndex);
    }
  }, [fieldId, activeTab, activeIndex, fetchMultiIndex]);

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
    { key: 'indices', label: 'Индексы' },
    { key: 'yield', label: 'Урожай' },
    { key: 'weather', label: 'Погода' },
    ...(FIRST_PILOT_FEATURES.wialon
      ? [{ key: 'telematics', label: 'Техника' }]
      : []),
    { key: 'alerts', label: `Алерты (${alerts.length})` },
  ];

  return (
    <div className="flex flex-col h-full">
      {/* Шапка */}
      <div className="flex items-center gap-3 py-3 pl-16 pr-4 border-b border-agro-surface2 md:px-4">
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
      <div className="flex overflow-x-auto border-b border-agro-surface2 px-4">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`min-h-11 flex-none px-4 py-2.5 text-sm border-b-2 transition-colors ${
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

        {activeTab === 'indices' && (
          <div style={{ padding: '12px 0' }} className="space-y-4">
            {/* Sub-selector: NDVI + multi-indices */}
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => setActiveIndex('ndvi')}
                className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                  activeIndex === 'ndvi'
                    ? 'bg-agro-accent text-white'
                    : 'bg-agro-surface2 text-agro-muted hover:text-agro-text'
                }`}
              >
                NDVI
              </button>
              {MULTI_INDICES.map(code => (
                <button
                  key={code}
                  onClick={() => setActiveIndex(code)}
                  className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                    activeIndex === code
                      ? 'bg-agro-accent text-white'
                      : 'bg-agro-surface2 text-agro-muted hover:text-agro-text'
                  }`}
                >
                  {INDEX_LABELS[code]}
                </button>
              ))}
            </div>

            {activeIndex === 'ndvi' ? (
              <NDVIChart fieldId={typeof fieldId === 'string' ? parseInt(fieldId) : fieldId} />
            ) : (
              <div className="space-y-3">
                {/* Latest value card */}
                {indexLoading && (
                  <div className="card animate-pulse h-20 bg-agro-surface2 rounded" />
                )}
                {!indexLoading && indexError && (
                  <div className="card text-sm text-red-400">{indexError}</div>
                )}
                {!indexLoading && !indexError && latestIndex && latestIndex.record && (
                  <div className="card flex items-center justify-between">
                    <div>
                      <p className="text-xs text-agro-muted">
                        Последний {INDEX_LABELS[latestIndex.index_code]} ({latestIndex.index_code.toUpperCase()})
                      </p>
                      <p className="text-2xl font-bold text-agro-accent">
                        {latestIndex.record.mean_value?.toFixed(4) ?? '—'}
                      </p>
                      <p className="text-xs text-agro-muted">{latestIndex.record.captured_date || '—'}</p>
                    </div>
                    <div className="text-right text-xs text-agro-muted space-y-1">
                      {latestIndex.record.valid_pixels_pct != null && (
                        <p>Пиксели: {latestIndex.record.valid_pixels_pct.toFixed(1)}%</p>
                      )}
                      {latestIndex.record.cloud_cover_pct != null && (
                        <p>Облачность: {latestIndex.record.cloud_cover_pct.toFixed(1)}%</p>
                      )}
                    </div>
                  </div>
                )}
                {!indexLoading && !indexError && (!latestIndex || !latestIndex.record) && (
                  <div className="card text-sm text-agro-muted text-center py-6">
                    Нет данных для {INDEX_LABELS[activeIndex]}
                  </div>
                )}

                {/* History table */}
                {!indexLoading && indexHistory.length > 0 && (
                  <div className="card">
                    <h4 className="text-xs font-semibold text-agro-muted uppercase tracking-wide mb-2">
                      История {INDEX_LABELS[activeIndex]} ({indexHistory.length} записей)
                    </h4>
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs text-left">
                        <thead>
                          <tr className="border-b border-agro-surface2">
                            <th className="py-1.5 pr-3 text-agro-muted">Дата</th>
                            <th className="py-1.5 pr-3 text-agro-muted">Mean</th>
                            <th className="py-1.5 pr-3 text-agro-muted">Min</th>
                            <th className="py-1.5 pr-3 text-agro-muted">Max</th>
                            <th className="py-1.5 text-agro-muted">Облачность</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[...indexHistory].reverse().map((r, i) => (
                            <tr key={r.id || i} className="border-b border-agro-surface2/50">
                              <td className="py-1.5 pr-3 text-agro-text">{r.captured_date}</td>
                              <td className="py-1.5 pr-3 text-agro-accent font-medium">
                                {r.mean_value?.toFixed(4) ?? '—'}
                              </td>
                              <td className="py-1.5 pr-3 text-agro-muted">
                                {r.min_value?.toFixed(4) ?? '—'}
                              </td>
                              <td className="py-1.5 pr-3 text-agro-muted">
                                {r.max_value?.toFixed(4) ?? '—'}
                              </td>
                              <td className="py-1.5 text-agro-muted">
                                {r.cloud_cover_pct != null ? `${r.cloud_cover_pct.toFixed(1)}%` : '—'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
                {!indexLoading && !indexError && activeIndex !== 'ndvi' && indexHistory.length === 0 && (
                  <div className="card text-sm text-agro-muted text-center py-6">
                    Нет исторических данных для {INDEX_LABELS[activeIndex]}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {activeTab === 'weather' && (
          <div style={{ padding: '12px 0' }}>
            <WeatherWidget fieldId={typeof fieldId === 'string' ? parseInt(fieldId) : fieldId} />
          </div>
        )}

        {activeTab === 'yield' && (
          <>
            <ProductivityZonePanel
              fieldId={typeof fieldId === 'string' ? parseInt(fieldId) : fieldId}
            />
            <VariableRateRecommendationPanel
              fieldId={typeof fieldId === 'string' ? parseInt(fieldId) : fieldId}
            />
            <YieldMapImportPanel
              fieldId={typeof fieldId === 'string' ? parseInt(fieldId) : fieldId}
            />
          </>
        )}

        {FIRST_PILOT_FEATURES.wialon && activeTab === 'telematics' && (
          <FieldTelematicsPanel
            fieldId={typeof fieldId === 'string' ? parseInt(fieldId) : fieldId}
          />
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
