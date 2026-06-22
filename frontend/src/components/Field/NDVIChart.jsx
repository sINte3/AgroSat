import React, { useState, useEffect, useMemo } from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts';
import apiClient from '../../api/client';

export default function NDVIChart({ fieldId }) {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!fieldId) return;
    setLoading(true);

    apiClient.get(`/api/ndvi/${fieldId}/history?days=90`)
      .then(res => {
        setRecords(res.data.records || []);
      })
      .catch(() => {
        console.error('Failed to load NDVI history');
        setRecords([]);
      })
      .finally(() => setLoading(false));
  }, [fieldId]);

  const chartData = useMemo(() => {
    if (!records || records.length === 0) return [];

    return [...records]
      .map((record) => {
        const timestamp = Date.parse(record.captured_date);
        const ndvi = Number(record.mean_ndvi);

        if (!Number.isFinite(timestamp) || !Number.isFinite(ndvi)) {
          return null;
        }

        return {
          date: record.captured_date,
          timestamp,
          ndvi: Number(ndvi.toFixed(4)),
        };
      })
      .filter(Boolean)
      .sort((a, b) => a.timestamp - b.timestamp);
  }, [records]);

  if (loading) {
    return (
      <div className="w-full h-64 bg-slate-950/20 border border-slate-900 rounded-lg animate-pulse" />
    );
  }

  if (chartData.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 bg-slate-950/20 border border-slate-900 rounded-lg text-slate-500 font-mono text-xs">
        Нет доступной истории вегетации NDVI
      </div>
    );
  }

  return (
    <div className="w-full h-64 bg-slate-950/40 border border-slate-900/50 rounded-lg p-4 shadow-inner">
      <ResponsiveContainer width="100%" height="100%" debounce={100}>
        <LineChart
          data={chartData}
          margin={{ top: 10, right: 10, left: -20, bottom: 0 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />

          <XAxis
            dataKey="date"
            stroke="#64748b"
            fontSize={10}
            tickLine={false}
            tickFormatter={(tick) => {
              try {
                const date = new Date(tick);
                return date.toLocaleDateString('ru-RU', {
                  month: 'short',
                  day: 'numeric',
                });
              } catch {
                return tick;
              }
            }}
          />

          <YAxis
            stroke="#64748b"
            fontSize={10}
            tickLine={false}
            domain={[0.0, 1.0]}
            ticks={[0.0, 0.2, 0.4, 0.6, 0.8, 1.0]}
          />

          <Tooltip
            contentStyle={{
              backgroundColor: '#020617',
              borderColor: '#10b981',
              color: '#f8fafc',
            }}
            labelStyle={{
              color: '#10b981',
              fontWeight: 'bold',
              fontSize: '11px',
            }}
            itemStyle={{
              color: '#34d399',
              fontSize: '11px',
            }}
          />

          <Line
            type="monotone"
            dataKey="ndvi"
            stroke="#10b981"
            strokeWidth={2}
            dot={{ r: 2, strokeWidth: 1 }}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
