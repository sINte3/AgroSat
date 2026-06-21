/**
 * NDVIHistoryModal.jsx — Мини-модал с 30-дневной историей NDVI поля
 *
 * Props:
 *   field — объект поля (id, name)
 *   onClose — callback закрытия
 *
 * NDVI данные загружаются самостоятельно при открытии (GET /api/ndvi/{id}/history?days=30)
 * — не требует pre-loaded данных от родителя.
 *
 * Содержит:
 *   - Recharts AreaChart с зелёной линией и gradient area
 *   - Таблица данных внизу
 *   - Адаптив: на мобиле fullscreen, на десктопе — centered modal 640px
 */

import { useState, useEffect, useMemo } from 'react';
import apiClient from '../../api/client';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Area, AreaChart,
} from 'recharts';

// ─── Форматирование ─────────────────────────────────────────────────────────
function formatDateRu(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
  } catch { return iso; }
}

function formatShortDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
  } catch { return iso; }
}

// ─── NDVI статус ────────────────────────────────────────────────────────────
function ndviStatus(value) {
  if (value == null) return { label: 'Нет данных', color: '#6b8578' };
  if (value < 0.2) return { label: 'Критический', color: '#ef4444' };
  if (value < 0.35) return { label: 'Плохой', color: '#f97316' };
  if (value < 0.5) return { label: 'Удовлетворительный', color: '#eab308' };
  if (value < 0.65) return { label: 'Хороший', color: '#84cc16' };
  return { label: 'Отличный', color: '#16a34a' };
}

// ─── Custom Tooltip ─────────────────────────────────────────────────────────
function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const data = payload[0].payload;
  return (
    <div
      style={{
        background: '#ffffff',
        border: '1px solid #e0e7e3',
        borderRadius: 8,
        padding: '10px 14px',
        boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
      }}
    >
      <div style={{ fontSize: 12, color: '#6b8578', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 700, color: '#16a34a' }}>
        NDVI: {data.mean_ndvi?.toFixed(4) ?? '—'}
      </div>
      {data.min_ndvi != null && (
        <div style={{ fontSize: 11, color: '#f97316', marginTop: 2 }}>Мин: {data.min_ndvi.toFixed(4)}</div>
      )}
      {data.max_ndvi != null && (
        <div style={{ fontSize: 11, color: '#84cc16' }}>Макс: {data.max_ndvi.toFixed(4)}</div>
      )}
    </div>
  );
}

// ─── Main Component ─────────────────────────────────────────────────────────
export default function NDVIHistoryModal({ field, onClose }) {
  const [ndviHistory, setNdviHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showAll, setShowAll] = useState(false);

  // Загрузка NDVI при открытии модала — только для одного поля
  useEffect(() => {
    if (!field?.id) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);

    apiClient.get(`/api/ndvi/${field.id}/history`, { params: { days: 30 } })
      .then(r => setNdviHistory(r.data || []))
      .catch(err => {
        console.error('NDVI history error:', err);
        setError('Не удалось загрузить историю NDVI');
        setNdviHistory([]);
      })
      .finally(() => setLoading(false));
  }, [field?.id]);

  // Подготовка данных для графика
  const chartData = useMemo(() => {
    if (!ndviHistory || ndviHistory.length === 0) return [];
    return ndviHistory
      .map(d => ({
        ...d,
        label: formatShortDate(d.captured_date),
        mean_ndvi: d.mean_ndvi != null ? Number(d.mean_ndvi.toFixed(4)) : null,
      }))
      .sort((a, b) => new Date(a.captured_date) - new Date(b.captured_date));
  }, [ndviHistory]);

  // Статистика
  const stats = useMemo(() => {
    if (!chartData.length) return null;
    const values = chartData.filter(d => d.mean_ndvi != null).map(d => d.mean_ndvi);
    if (!values.length) return null;
    return {
      min: Math.min(...values),
      max: Math.max(...values),
      avg: values.reduce((a, b) => a + b, 0) / values.length,
      first: values[0],
      last: values[values.length - 1],
      change: values[values.length - 1] - values[0],
    };
  }, [chartData]);

  // Показываемые строки таблицы
  const displayData = showAll ? chartData : chartData.slice(-14);

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(0,0,0,0.6)',
        backdropFilter: 'blur(2px)',
      }}
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: '#f8faf9',
          border: '1px solid #e0e7e3',
          borderRadius: 16,
          width: '90%',
          maxWidth: 640,
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: '0 8px 40px rgba(0,0,0,0.5)',
        }}
      >
        {/* Header */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '16px 20px',
            borderBottom: '1px solid #e0e7e3',
            flexShrink: 0,
          }}
        >
          <h2
            style={{
              margin: 0,
              fontSize: 16,
              fontWeight: 600,
              color: '#1a2e23',
            }}
          >
            NDVI История — {field?.name || `Поле #${field?.id}`}
          </h2>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: '#6b8578',
              fontSize: 22,
              cursor: 'pointer',
              width: 32,
              height: 32,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: 6,
              transition: 'all 0.15s',
            }}
            onMouseEnter={e => { e.currentTarget.style.background = '#e8eeea'; e.currentTarget.style.color = '#1a2e23'; }}
            onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = '#6b8578'; }}
          >
            ×
          </button>
        </div>

        {/* Content */}
        <div style={{ padding: '16px 20px', overflowY: 'auto', flex: 1 }}>
          {/* Loading spinner */}
          {loading && (
            <div style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '60px 20px',
              gap: 12,
            }}>
              <div className="modal-spinner" style={{
                width: 32, height: 32,
                border: '3px solid #e0e7e3',
                borderTop: '3px solid #4ade80',
                borderRadius: '50%',
                animation: 'spin 0.8s linear infinite',
              }} />
              <span style={{ fontSize: 13, color: '#6b8578' }}>Загрузка NDVI истории...</span>
            </div>
          )}

          {/* Error state */}
          {!loading && error && (
            <div style={{
              textAlign: 'center',
              padding: '40px 20px',
              color: '#ef4444',
              fontSize: 14,
            }}>
              ⚠️ {error}
            </div>
          )}

          {/* Empty state */}
          {!loading && !error && chartData.length === 0 && (
            <div style={{
              textAlign: 'center',
              padding: '40px 20px',
              color: '#6b8578',
              fontSize: 14,
            }}>
              Нет данных NDVI за последние 30 дней
            </div>
          )}

          {/* Данные загружены */}
          {!loading && !error && chartData.length > 0 && (
            <>
              {/* График */}
              <div style={{ height: 260, marginBottom: 16 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
                    <defs>
                      <linearGradient id="ndviGradient" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#4ade80" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#4ade80" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e0e7e3" />
                    <XAxis
                      dataKey="label"
                      tick={{ fill: '#6b8578', fontSize: 10 }}
                      tickLine={false}
                      axisLine={{ stroke: '#e0e7e3' }}
                      interval="preserveStartEnd"
                    />
                    <YAxis
                      domain={[0, 1]}
                      tick={{ fill: '#6b8578', fontSize: 10 }}
                      tickLine={false}
                      axisLine={{ stroke: '#e0e7e3' }}
                      tickFormatter={v => v.toFixed(1)}
                    />
                    <Tooltip content={<CustomTooltip />} />
                    <Area
                      type="monotone"
                      dataKey="mean_ndvi"
                      stroke="#4ade80"
                      strokeWidth={2.5}
                      fill="url(#ndviGradient)"
                      dot={{
                        r: 3,
                        fill: '#f8faf9',
                        stroke: '#16a34a',
                        strokeWidth: 2,
                      }}
                      activeDot={{
                        r: 5,
                        fill: '#16a34a',
                        stroke: '#f8faf9',
                        strokeWidth: 2,
                      }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              {/* Stats mini */}
              {stats && (
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
                    gap: 8,
                    marginBottom: 16,
                    padding: '10px 12px',
                    background: '#f1f5f3',
                    borderRadius: 8,
                    border: '1px solid #e0e7e3',
                  }}
                >
                  <StatBox label="Средний" value={stats.avg.toFixed(4)} color="#4ade80" />
                  <StatBox label="Максимум" value={stats.max.toFixed(4)} color="#84cc16" />
                  <StatBox label="Минимум" value={stats.min.toFixed(4)} color="#f97316" />
                  <StatBox
                    label="Изменение"
                    value={`${stats.change >= 0 ? '+' : ''}${stats.change.toFixed(4)}`}
                    color={stats.change >= 0 ? '#16a34a' : '#ef4444'}
                  />
                  <StatBox
                    label="Текущий статус"
                    value={ndviStatus(stats.last).label}
                    color={ndviStatus(stats.last).color}
                    small
                  />
                </div>
              )}

              {/* Таблица данных */}
              <div style={{ fontSize: 13, fontWeight: 600, color: '#1a2e23', marginBottom: 8 }}>
                Данные NDVI
                <span style={{ fontSize: 11, color: '#6b8578', fontWeight: 400, marginLeft: 8 }}>
                  (показано {displayData.length} из {chartData.length} записей)
                </span>
              </div>

              <div style={{ overflowX: 'auto', borderRadius: 6, border: '1px solid #e0e7e3' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr style={{ background: '#f1f5f3' }}>
                      <th style={thStyle}>Дата</th>
                      <th style={thStyle}>NDVI</th>
                      <th style={thStyle}>Изменение</th>
                      <th style={thStyle}>Статус</th>
                    </tr>
                  </thead>
                  <tbody>
                    {displayData.slice().reverse().map((d, idx) => {
                      const prev = displayData.slice().reverse()[idx + 1];
                      const change = prev && d.mean_ndvi != null && prev.mean_ndvi != null
                        ? d.mean_ndvi - prev.mean_ndvi
                        : null;
                      const st = ndviStatus(d.mean_ndvi);
                      return (
                        <tr
                          key={d.captured_date || idx}
                          style={{ background: idx % 2 === 0 ? '#f1f5f3' : '#f8faf9' }}
                        >
                          <td style={tdStyle}>{formatDateRu(d.captured_date)}</td>
                          <td style={{ ...tdStyle, fontWeight: 600, color: st.color }}>
                            {d.mean_ndvi?.toFixed(4) ?? '—'}
                          </td>
                          <td style={{
                            ...tdStyle,
                            color: change == null ? '#6b8578' : change >= 0 ? '#16a34a' : '#ef4444',
                          }}>
                            {change != null ? `${change >= 0 ? '+' : ''}${change.toFixed(4)}` : '—'}
                          </td>
                          <td style={{ ...tdStyle }}>
                            <span style={{
                              display: 'inline-block',
                              padding: '1px 6px',
                              borderRadius: 4,
                              fontSize: 11,
                              background: `${st.color}22`,
                              color: st.color,
                            }}>
                              {st.label}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Show more / less */}
              {chartData.length > 14 && (
                <button
                  onClick={() => setShowAll(!showAll)}
                  style={{
                    display: 'block',
                    margin: '12px auto 0',
                    background: 'transparent',
                    border: '1px solid #e0e7e3',
                    borderRadius: 6,
                    padding: '6px 16px',
                    color: '#16a34a',
                    cursor: 'pointer',
                    fontSize: 12,
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.background = '#e8eeea'; }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                >
                  {showAll ? '▲ Показать меньше' : '▼ Показать все'}
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {/* Spinner animation */}
      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

// ─── Small helpers ──────────────────────────────────────────────────────────
function StatBox({ label, value, color, small }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: '#6b8578', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: small ? 12 : 15, fontWeight: 700, color }}>
        {value}
      </div>
    </div>
  );
}

const thStyle = {
  padding: '8px 10px',
  fontSize: 11,
  fontWeight: 600,
  color: '#6b8578',
  textTransform: 'uppercase',
  letterSpacing: '0.5px',
  textAlign: 'left',
  borderBottom: '1px solid #e0e7e3',
};

const tdStyle = {
  padding: '6px 10px',
  color: '#1a2e23',
  borderBottom: '1px solid #e0e7e3',
  verticalAlign: 'middle',
};
