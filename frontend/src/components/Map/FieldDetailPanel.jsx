import { useState, useEffect } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine
} from 'recharts';
import apiClient, { getSatelliteIndexLatest, getSatelliteIndexHistory, getLatestNDVI } from '../../api/client';

// ─── Index definitions ────────────────────────────────────────────────────────────
const INDEX_DEFS = [
  { code: 'NDVI', desc: 'Вегетация (NDVI)',    help: 'Индекс вегетации — общее состояние растительности' },
  { code: 'SAVI', desc: 'SAVI',                  help: 'SAVI — вегетация с коррекцией на почву' },
  { code: 'EVI',  desc: 'EVI',                   help: 'EVI — усиленный сигнал вегетации' },
  { code: 'NDMI', desc: 'Влага (NDMI)',           help: 'NDMI — влажность / водный стресс' },
  { code: 'NDRE', desc: 'NDRE',                  help: 'NDRE — хлорофилл / красная граница' },
];

// ─── Helpers ──────────────────────────────────────────────────────────────────────
const getNdviColor = (v) => {
  if (v == null) return '#9ca3af';
  if (v < 0.15) return '#dc2626';
  if (v < 0.3) return '#f97316';
  if (v < 0.45) return '#eab308';
  if (v < 0.6) return '#84cc16';
  return '#16a34a';
};

const getNdviLabel = (v) => {
  if (v == null) return 'Нет данных';
  if (v < 0.15) return 'Критический';
  if (v < 0.3) return 'Слабый';
  if (v < 0.45) return 'Умеренный';
  if (v < 0.6) return 'Хороший';
  return 'Отличный';
};

const severityColors = {
  critical: { bg: 'bg-red-50', text: 'text-red-700', dot: 'bg-red-500' },
  warning: { bg: 'bg-amber-50', text: 'text-amber-700', dot: 'bg-amber-500' },
  info: { bg: 'bg-blue-50', text: 'text-blue-700', dot: 'bg-blue-500' },
};

// ─── Single index card component ──────────────────────────────────────────────────
function IndexCard({ code, desc, help, latest, loading, error }) {
  const value = latest?.mean_value ?? latest?.mean_ndvi ?? null;
  const date = latest?.captured_date || '—';
  const validPixels = latest?.valid_pixels_pct ?? null;
  // NDVI uses cloud_cover_pct; satellite-index uses valid_pixels_pct
  const displayValidPixels = validPixels != null
    ? `${validPixels.toFixed(1)}%`
    : '—';

  return (
    <div className="bg-white border border-gray-100 rounded-lg p-2.5 text-xs space-y-1.5 hover:border-gray-200 transition-colors">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div className="font-semibold text-gray-800 flex items-center gap-1" title={help}>
          {desc}
          <span className="text-gray-300 cursor-help" title={help}>ⓘ</span>
        </div>
      </div>

      {/* Value + metadata */}
      {loading ? (
        <div className="flex items-center gap-2">
          <div className="w-16 h-5 bg-gray-100 rounded animate-pulse" />
          <div className="w-20 h-4 bg-gray-50 rounded animate-pulse" />
        </div>
      ) : error ? (
        <div className="text-red-500 text-[11px]">Ошибка загрузки</div>
      ) : value != null ? (
        <>
          <div className="flex items-baseline gap-2">
            <span className="text-sm font-bold text-gray-900">
              {code === 'NDVI' ? (
                <span style={{ color: getNdviColor(value) }}>{value.toFixed(4)}</span>
              ) : (
                <span className={value < 0 ? 'text-amber-600' : 'text-gray-900'}>
                  {value.toFixed(4)}
                </span>
              )}
            </span>
          </div>
          <div className="text-[11px] text-gray-400 space-y-0.5">
            <div>Дата снимка: {date}</div>
            <div>Валидные пиксели: {displayValidPixels}</div>
          </div>
        </>
      ) : (
        <div className="text-gray-400 text-[11px]">Нет данных</div>
      )}
    </div>
  );
}

// ─── Main panel component ─────────────────────────────────────────────────────────
export default function FieldDetailPanel({ field, onBack, onNavigate }) {
  const [ndviHistory, setNdviHistory] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [weather, setWeather] = useState(null);
  const [ndviLoading, setNdviLoading] = useState(false);  // for legacy chart
  const [dayRange, setDayRange] = useState(90);
  const [activeIndex, setActiveIndex] = useState('NDVI');
  const [multiLatest, setMultiLatest] = useState(null);
  const [multiHistory, setMultiHistory] = useState([]);
  const [multiLoading, setMultiLoading] = useState(false);
  const [multiError, setMultiError] = useState(null);

  // Per-index card state — NDVI + 4 satellite-indices
  const [ndviCard, setNdviCard] = useState({ latest: null, loading: true, error: null });
  const [saviCard, setSaviCard] = useState({ latest: null, loading: true, error: null });
  const [eviCard, setEviCard] = useState({ latest: null, loading: true, error: null });
  const [ndmiCard, setNdmiCard] = useState({ latest: null, loading: true, error: null });
  const [ndreCard, setNdreCard] = useState({ latest: null, loading: true, error: null });

  const INDEX_CODES = ['NDVI', 'SAVI', 'EVI', 'NDMI', 'NDRE'];

  // Reset active index when field changes
  useEffect(() => {
    setActiveIndex('NDVI');
  }, [field?.id]);

  // ─── Fetch index card data ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!field?.id) return;

    // ---- NDVI card (legacy /api/ndvi) ----
    setNdviCard({ latest: null, loading: true, error: null });
    getLatestNDVI(field.id)
      .then(res => {
        const rec = res?.record ?? null;
        setNdviCard({ latest: rec, loading: false, error: null });
      })
      .catch(() => {
        setNdviCard({ latest: null, loading: false, error: true });
      });

    // ---- SAVI / EVI / NDMI / NDRE cards (satellite-indices) ----
    const codes = ['savi', 'evi', 'ndmi', 'ndre'];
    const setters = [setSaviCard, setEviCard, setNdmiCard, setNdreCard];

    codes.forEach((code, i) => {
      setters[i]({ latest: null, loading: true, error: null });
      getSatelliteIndexLatest(field.id, code, { includeCloudy: false })
        .then(res => {
          const rec = res?.record ?? null;
          setters[i]({ latest: rec, loading: false, error: null });
        })
        .catch(() => {
          setters[i]({ latest: null, loading: false, error: true });
        });
    });
  }, [field?.id]);

  // ─── Fetch NDVI history + alerts + weather (legacy, always runs) ───────────────
  useEffect(() => {
    if (!field?.id) return;
    setNdviLoading(true);

    Promise.allSettled([
      apiClient.get(`/api/ndvi/${field.id}/history?days=${dayRange}`),
      apiClient.get(`/api/alerts/?field_id=${field.id}`),
      apiClient.get(`/api/weather/field/${field.id}`),
    ]).then(([ndviRes, alertRes, weatherRes]) => {
      if (ndviRes.status === 'fulfilled') {
        const records = ndviRes.value.data?.records || [];
        setNdviHistory(
          records
            .map(r => ({
              date: r.captured_date,
              ndvi: r.mean_ndvi,
              min: r.min_ndvi,
              max: r.max_ndvi,
              cloud: r.cloud_cover_pct,
            }))
            .sort((a, b) => a.date.localeCompare(b.date))
        );
      }
      if (alertRes.status === 'fulfilled') {
        const data = alertRes.value.data;
        setAlerts(Array.isArray(data) ? data.filter(a => a.is_active !== false) : []);
      }
      if (weatherRes.status === 'fulfilled') {
        setWeather(weatherRes.value.data);
      }
      setNdviLoading(false);
    });
  }, [field?.id, dayRange]);

  // ─── Fetch multi-index history when a non-NDVI index is active ─────────────────
  useEffect(() => {
    if (!field?.id || activeIndex === 'NDVI') {
      setMultiLatest(null);
      setMultiHistory([]);
      setMultiError(null);
      setMultiLoading(false);
      return;
    }
    let cancelled = false;
    const code = activeIndex.toLowerCase();
    setMultiLoading(true);
    setMultiError(null);

    Promise.allSettled([
      getSatelliteIndexLatest(field.id, code, { includeCloudy: false }),
      getSatelliteIndexHistory(field.id, code, { days: dayRange, includeCloudy: false }),
    ]).then(([latestRes, histRes]) => {
      if (cancelled) return;
      if (latestRes.status === 'fulfilled') {
        setMultiLatest(latestRes.value?.record ?? null);
      } else {
        setMultiError('Ошибка загрузки последних данных');
      }
      if (histRes.status === 'fulfilled') {
        const records = histRes.value?.records || [];
        setMultiHistory(
          records
            .map(r => ({
              date: r.captured_date,
              value: r.mean_value,
              min: r.min_value,
              max: r.max_value,
              cloud: r.cloud_cover_pct,
            }))
            .sort((a, b) => a.date.localeCompare(b.date))
        );
      }
      setMultiLoading(false);
    });
    return () => { cancelled = true; };
  }, [field?.id, activeIndex, dayRange]);

  if (!field) return null;

  const ndvi = field.last_ndvi ?? field.mean_ndvi ?? null;
  const ndviColor = getNdviColor(ndvi);
  const crop = field.current_crop || field.crop_name || 'Не указана';

  // Gather card states for the history section
  const cardStates = { NDVI: ndviCard, SAVI: saviCard, EVI: eviCard, NDMI: ndmiCard, NDRE: ndreCard };
  const activeCard = cardStates[activeIndex];

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
        {/* ── Field summary section ─────────────────────────────────────── */}
        <div className="p-3 border-b border-gray-100 bg-gray-50/40">
          <div className="flex items-center gap-3">
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center text-white text-sm font-bold shadow-sm"
              style={{ backgroundColor: ndviColor }}
            >
              {ndvi != null ? ndvi.toFixed(2) : '—'}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold text-gray-900 truncate">{field.name}</div>
              <div className="text-xs text-gray-500 mt-0.5 space-y-0.5">
                {field.enterprise_name && (
                  <div>Предприятие: <span className="text-gray-700">{field.enterprise_name}</span></div>
                )}
                {crop && crop !== 'Не указана' && (
                  <div>Культура: <span className="text-gray-700">{crop}</span></div>
                )}
                {field.area_ha && (
                  <div>Площадь: <span className="text-gray-700">{Number(field.area_ha).toFixed(1)} га</span></div>
                )}
                <div>
                  NDVI: <span style={{ color: ndviColor }} className="font-medium">{getNdviLabel(ndvi)}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* ── Index cards grid ──────────────────────────────────────────── */}
        <div className="p-3 border-b border-gray-100">
          <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-2">
            Индексы
          </h4>
          <div className="grid grid-cols-1 gap-2">
            {INDEX_DEFS.map(def => {
              const cardState = cardStates[def.code];
              return (
                <IndexCard
                  key={def.code}
                  code={def.code}
                  desc={def.desc}
                  help={def.help}
                  latest={cardState.latest}
                  loading={cardState.loading}
                  error={cardState.error}
                />
              );
            })}
          </div>
        </div>

        {/* ── History chart ──────────────────────────────────────────────── */}
        <div className="p-3 border-b border-gray-100">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
              История
            </h4>
            <div className="flex gap-1">
              {[30, 60, 90].map(d => (
                <button
                  key={d}
                  onClick={() => setDayRange(d)}
                  className={`text-xs px-2 py-0.5 rounded ${
                    dayRange === d
                      ? 'bg-green-600 text-white'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  {d}д
                </button>
              ))}
            </div>
          </div>

          {/* Index selector tabs */}
          <div className="flex gap-1 flex-wrap mb-2">
            {INDEX_CODES.map(code => (
              <button
                key={code}
                onClick={() => setActiveIndex(code)}
                className={`text-xs px-2 py-0.5 rounded font-medium ${
                  activeIndex === code
                    ? 'bg-green-600 text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {code}
              </button>
            ))}
          </div>

          {/* Latest value summary for chart */}
          <div className="flex items-center justify-between mb-1 text-xs text-gray-500">
            <span>{INDEX_DEFS.find(d => d.code === activeIndex)?.desc || activeIndex}</span>
            <span>
              {activeCard?.loading
                ? 'Загрузка...'
                : activeCard?.latest
                  ? `last: ${(activeCard.latest.mean_value ?? activeCard.latest.mean_ndvi)?.toFixed(4) ?? '—'}`
                  : 'Нет данных'}
            </span>
          </div>

          {activeIndex === 'NDVI' ? (
            /* NDVI chart */
            ndviLoading ? (
              <div className="h-32 flex items-center justify-center text-xs text-gray-400">
                Загрузка графика...
              </div>
            ) : ndviHistory.length === 0 ? (
              <div className="h-32 flex items-center justify-center text-xs text-gray-400">
                Нет данных за период
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={140}>
                <LineChart data={ndviHistory} margin={{ top: 5, right: 5, bottom: 5, left: -15 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 9, fill: '#9ca3af' }}
                    tickFormatter={d => {
                      const parts = d.split('-');
                      return `${parts[2]}/${parts[1]}`;
                    }}
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    domain={[0, 1]}
                    tick={{ fontSize: 9, fill: '#9ca3af' }}
                    tickCount={5}
                  />
                  <Tooltip
                    contentStyle={{
                      fontSize: 11,
                      borderRadius: 8,
                      border: '1px solid #e5e7eb',
                      boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
                    }}
                    formatter={(value, name) => {
                      if (name === 'ndvi') return [value?.toFixed(4), 'NDVI'];
                      return [value, name];
                    }}
                    labelFormatter={d => `Дата: ${d}`}
                  />
                  <ReferenceLine y={0.2} stroke="#f97316" strokeDasharray="3 3" strokeOpacity={0.5} />
                  <ReferenceLine y={0.5} stroke="#84cc16" strokeDasharray="3 3" strokeOpacity={0.5} />
                  <Line
                    type="monotone"
                    dataKey="ndvi"
                    stroke="#16a34a"
                    strokeWidth={2}
                    dot={{ r: 2.5, fill: '#16a34a' }}
                    activeDot={{ r: 4, strokeWidth: 2 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            )
          ) : (
            /* SAVI / EVI / NDMI / NDRE chart */
            <>
              {multiLoading ? (
                <div className="h-32 flex items-center justify-center text-xs text-gray-400">
                  Загрузка графика...
                </div>
              ) : multiHistory.length === 0 ? (
                <div className="h-32 flex items-center justify-center text-xs text-gray-400">
                  Нет исторических данных
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={140}>
                  <LineChart data={multiHistory} margin={{ top: 5, right: 5, bottom: 5, left: -15 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 9, fill: '#9ca3af' }}
                      tickFormatter={d => {
                        const parts = d.split('-');
                        return `${parts[2]}/${parts[1]}`;
                      }}
                      interval="preserveStartEnd"
                    />
                    <YAxis
                      type="number"
                      domain={['auto', 'auto']}
                      tick={{ fontSize: 9, fill: '#9ca3af' }}
                      tickCount={5}
                    />
                    <Tooltip
                      contentStyle={{
                        fontSize: 11,
                        borderRadius: 8,
                        border: '1px solid #e5e7eb',
                        boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
                      }}
                      formatter={(value, name) => {
                        if (name === 'value') return [value?.toFixed(4), activeIndex];
                        return [value, name];
                      }}
                      labelFormatter={d => `Дата: ${d}`}
                    />
                    <Line
                      type="monotone"
                      dataKey="value"
                      stroke="#8b5cf6"
                      strokeWidth={2}
                      dot={{ r: 2.5, fill: '#8b5cf6' }}
                      activeDot={{ r: 4, strokeWidth: 2 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </>
          )}
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
