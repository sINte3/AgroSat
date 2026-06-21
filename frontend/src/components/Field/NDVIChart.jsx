import React, { useState, useEffect } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
  Area, AreaChart, Legend
} from 'recharts';
import apiClient from '../../api/client';

// Получить цвет по значению NDVI
function getNdviColor(value) {
  if (value < 0.20) return '#ef4444';
  if (value < 0.35) return '#f97316';
  if (value < 0.50) return '#eab308';
  if (value < 0.65) return '#84cc16';
  return '#22c55e';
}

// Метка NDVI зоны
function getNdviLabel(value) {
  if (value < 0.20) return 'Критически низкий';
  if (value < 0.35) return 'Слабый';
  if (value < 0.50) return 'Умеренный';
  if (value < 0.65) return 'Хороший';
  return 'Отличный';
}

// Форматировать дату для оси X
function formatDate(dateStr) {
  const d = new Date(dateStr);
  return `${d.getDate().toString().padStart(2,'0')}.${(d.getMonth()+1).toString().padStart(2,'0')}`;
}

// Кастомный тултип
function CustomTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  const ndvi = payload[0]?.value;
  if (ndvi === undefined) return null;
  const d = new Date(label);
  const dateStr = d.toLocaleDateString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' });
  return (
    <div style={{
      background: '#ffffff',
      border: '1px solid #e0e7e3',
      borderRadius: '8px',
      padding: '10px 14px',
      fontSize: '13px',
      color: '#1a2e23',
      boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
    }}>
      <div style={{ color: '#6b8578', marginBottom: 4 }}>{dateStr}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{
          display: 'inline-block', width: 10, height: 10,
          borderRadius: '50%', background: getNdviColor(ndvi)
        }} />
        <span style={{ color: '#1a2e23' }}>NDVI: </span>
        <span style={{ color: getNdviColor(ndvi), fontWeight: 700, fontSize: 15 }}>
          {ndvi.toFixed(4)}
        </span>
      </div>
      <div style={{ color: '#6b8578', marginTop: 2, fontSize: 12 }}>
        {getNdviLabel(ndvi)}
      </div>
    </div>
  );
}

// Скелетон загрузки
function LoadingSkeleton() {
  return (
    <div style={{ padding: '16px 0' }}>
      <div style={{ background: '#e0e7e3', borderRadius: 6, height: 200, animation: 'pulse 1.5s infinite' }} />
    </div>
  );
}

export default function NDVIChart({ fieldId }) {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [days, setDays] = useState(90);

  useEffect(() => {
    if (!fieldId) return;
    setLoading(true);
    setError(null);
    apiClient.get(`/api/ndvi/${fieldId}/history?days=${days}`)
      .then(res => {
        const records = (res.data.records || []).map(r => ({
          date: r.captured_date,
          ndvi: parseFloat(r.mean_ndvi),
          min: parseFloat(r.min_ndvi),
          max: parseFloat(r.max_ndvi),
          change: r.change_pct,
        }));
        // Сортируем по дате
        records.sort((a, b) => new Date(a.date) - new Date(b.date));
        setData(records);
      })
      .catch(err => {
        const status = err.response?.status;
        if (status === 404 || status === 422) {
          // Поле не найдено или нет данных — показываем пустое состояние
          setData([]);
        } else {
          console.error('Ошибка загрузки NDVI:', err);
          setError('Не удалось загрузить историю NDVI');
        }
      })
      .finally(() => setLoading(false));
  }, [fieldId, days]);

  if (loading) return <LoadingSkeleton />;

  if (error) return (
    <div style={{
      textAlign: 'center', padding: '32px 16px',
      color: '#ef4444', fontSize: 14
    }}>
      {error}
    </div>
  );

  if (!data.length) return (
    <div style={{
      textAlign: 'center', padding: '32px 16px',
      color: '#6b8578', fontSize: 14
    }}>
      <div style={{ fontSize: 32, marginBottom: 8 }}>🛰️</div>
      Нет данных NDVI за выбранный период
    </div>
  );

  // Текущее NDVI (последняя запись)
  const latest = data[data.length - 1];
  const latestColor = getNdviColor(latest.ndvi);

  return (
    <div style={{ color: '#1a2e23' }}>
      {/* Текущее значение NDVI */}
      <div style={{
        display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', marginBottom: 16
      }}>
        <div>
          <div style={{ fontSize: 12, color: '#6b8578', marginBottom: 2 }}>Текущий NDVI</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ fontSize: 28, fontWeight: 700, color: latestColor }}>
              {latest.ndvi.toFixed(3)}
            </span>
            <span style={{
              fontSize: 12, color: latestColor,
              background: latestColor + '22',
              padding: '2px 8px', borderRadius: 12
            }}>
              {getNdviLabel(latest.ndvi)}
            </span>
          </div>
          <div style={{ fontSize: 11, color: '#6b8578', marginTop: 2 }}>
            {new Date(latest.date).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })}
          </div>
        </div>

        {/* Изменение */}
        {latest.change !== null && latest.change !== undefined && (
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 12, color: '#6b8578' }}>Изменение</div>
            <div style={{
              fontSize: 18, fontWeight: 600,
              color: latest.change >= 0 ? '#4ade80' : '#ef4444'
            }}>
              {latest.change >= 0 ? '+' : ''}{latest.change?.toFixed(1)}%
            </div>
          </div>
        )}
      </div>

      {/* Селектор периода */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {[30, 60, 90].map(d => (
          <button
            key={d}
            onClick={() => setDays(d)}
            style={{
              padding: '4px 14px',
              borderRadius: 20,
              border: 'none',
              cursor: 'pointer',
              fontSize: 13,
              fontWeight: days === d ? 600 : 400,
              background: days === d ? '#16a34a' : '#e0e7e3',
              color: days === d ? '#ffffff' : '#6b8578',
              transition: 'all 0.15s'
            }}
          >
            {d} дней
          </button>
        ))}
      </div>

      {/* График */}
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={data} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
          <defs>
            <linearGradient id="ndviGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#4ade80" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#4ade80" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#e0e7e3" vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={formatDate}
            tick={{ fill: '#6b8578', fontSize: 11 }}
            axisLine={{ stroke: '#e0e7e3' }}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            domain={[0, 1]}
            ticks={[0, 0.2, 0.35, 0.5, 0.65, 0.8, 1.0]}
            tick={{ fill: '#6b8578', fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={v => v.toFixed(1)}
          />
          <Tooltip content={<CustomTooltip />} />

          {/* Зональные линии */}
          <ReferenceLine y={0.20} stroke="#ef4444" strokeDasharray="4 3" strokeWidth={1} strokeOpacity={0.5} />
          <ReferenceLine y={0.35} stroke="#f97316" strokeDasharray="4 3" strokeWidth={1} strokeOpacity={0.5} />
          <ReferenceLine y={0.50} stroke="#eab308" strokeDasharray="4 3" strokeWidth={1} strokeOpacity={0.5} />
          <ReferenceLine y={0.65} stroke="#84cc16" strokeDasharray="4 3" strokeWidth={1} strokeOpacity={0.5} />

          <Area
            type="monotone"
            dataKey="ndvi"
            stroke="#4ade80"
            strokeWidth={2}
            fill="url(#ndviGradient)"
            dot={false}
            activeDot={{ r: 4, fill: '#4ade80', stroke: '#ffffff', strokeWidth: 2 }}
          />
        </AreaChart>
      </ResponsiveContainer>

      {/* Легенда зон */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: '6px 12px',
        marginTop: 12, paddingTop: 12,
        borderTop: '1px solid #e0e7e3'
      }}>
        {[
          { color: '#ef4444', label: 'Критический (<0.2)' },
          { color: '#f97316', label: 'Слабый (0.2–0.35)' },
          { color: '#eab308', label: 'Умеренный (0.35–0.5)' },
          { color: '#22c55e', label: 'Хороший (>0.5)' },
        ].map(z => (
          <div key={z.label} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <div style={{ width: 24, height: 2, background: z.color, borderRadius: 1 }} />
            <span style={{ fontSize: 11, color: '#6b8578' }}>{z.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
