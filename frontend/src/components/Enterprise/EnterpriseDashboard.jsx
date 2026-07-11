/**
 * EnterpriseDashboard.jsx — KPI Dashboard карточки для предприятия
 *
 * Props:
 *   enterprise — объект предприятия (id, name, code, region, total_area_ha, field_count)
 *   fields — массив полей
 *   alerts — массив алертов
 *
 * 6 карточек (2 ряда × 3 колонки):
 *   1. Всего полей
 *   2. Средний NDVI
 *   3. Критических
 *   4. Обновлено
 *   5. Площадь (га)
 *   6. Норма NDVI
 */

import { useMemo } from 'react';

// NDVI цвет для текста
function ndviTextColor(v) {
  if (v == null) return '#64748b';
  if (v < 0.2) return '#ef4444';
  if (v < 0.35) return '#f97316';
  if (v < 0.5) return '#eab308';
  if (v < 0.65) return '#84cc16';
  return '#16a34a';
}

function KpiCard({ icon, label, value, color, subtitle }) {
  return (
    <div
      style={{
        background: '#f1f5f3',
        borderRadius: 10,
        padding: '14px 16px',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        transition: 'background 0.15s, border-color 0.15s',
        border: '1px solid #e0e7e3',
      }}
      onMouseEnter={e => {
        e.currentTarget.style.background = '#e8eeea';
        e.currentTarget.style.borderColor = '#16a34a';
      }}
      onMouseLeave={e => {
        e.currentTarget.style.background = '#f1f5f3';
        e.currentTarget.style.borderColor = '#e0e7e3';
      }}
    >
      {/* Иконка + большое число */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 20, lineHeight: 1, flexShrink: 0 }}>{icon}</span>
        <span style={{
          fontSize: 26,
          fontWeight: 700,
          color: color || '#ffffff',
          lineHeight: 1,
        }}>
          {value ?? '—'}
        </span>
      </div>
      {/* Подпись — всегда в одну строку */}
      <div style={{
        fontSize: 12,
        color: '#6b8578',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
      }}>
        {label}
      </div>
      {subtitle && (
        <div style={{ fontSize: 11, color: '#16a34a', marginTop: 2, opacity: 0.8 }}>
          {subtitle}
        </div>
      )}
    </div>
  );
}

function SkeletonCard() {
  return (
    <div
      style={{
        background: '#f1f5f3',
        borderRadius: 10,
        padding: '14px 16px',
        border: '1px solid #e0e7e3',
        animation: 'pulse 1.5s ease-in-out infinite',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ width: 20, height: 20, background: '#d1d9d3', borderRadius: 4, flexShrink: 0 }} />
        <div style={{ width: '50%', height: 26, background: '#d1d9d3', borderRadius: 4 }} />
      </div>
      <div style={{ width: '60%', height: 12, background: '#d1d9d3', borderRadius: 4, marginTop: 8 }} />
    </div>
  );
}

export default function EnterpriseDashboard({ enterprise, fields, alerts, loading }) {
  const computed = useMemo(() => {
    if (!fields || fields.length === 0) {
      return {
        avgNdvi: null,
        totalFields: 0,
        criticalAlerts: 0,
        normalCount: 0,
        lastUpdate: null,
        avgArea: null,
      };
    }

    const ndviValues = fields
      .map(f => f.current_ndvi)
      .filter(v => v != null && !isNaN(v));

    const avgNdvi = ndviValues.length > 0
      ? (ndviValues.reduce((a, b) => a + b, 0) / ndviValues.length)
      : null;

    const criticalAlerts = alerts
      ? alerts.filter(a => a.severity === 'critical').length
      : 0;

    const fieldsWithAlerts = alerts
      ? new Set(alerts.filter(a => a.severity === 'critical').map(a => a.field_id))
      : new Set();

    const normalCount = fields.filter(f => !fieldsWithAlerts.has(f.id)).length;

    const dates = fields
      .map(f => f.last_ndvi_date)
      .filter(Boolean)
      .sort()
      .reverse();

    const lastUpdate = dates.length > 0 ? dates[0] : null;

    const areas = fields
      .map(f => f.area_ha)
      .filter(v => v != null && !isNaN(v));
    const avgArea = areas.length > 0
      ? areas.reduce((a, b) => a + b, 0) / areas.length
      : null;

    return { avgNdvi, totalFields: fields.length, criticalAlerts, normalCount, lastUpdate, avgArea };
  }, [fields, alerts]);

  const formatDate = (iso) => {
    if (!iso) return null;
    try {
      const d = new Date(iso);
      return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
    } catch { return iso; }
  };

  if (loading) {
    return (
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
        gap: 10,
      }}>
        {[1, 2, 3, 4, 5, 6].map(i => <SkeletonCard key={i} />)}
        <style>{`@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }`}</style>
      </div>
    );
  }

  const cards = [
    // Строка 1
    {
      key: 'fields',
      icon: '🌾',
      label: 'Всего полей',
      value: computed.totalFields,
      color: '#1a2e23',
    },
    {
      key: 'ndvi',
      icon: '📊',
      label: 'Средний NDVI',
      value: computed.avgNdvi != null ? computed.avgNdvi.toFixed(3) : '—',
      color: ndviTextColor(computed.avgNdvi),
      subtitle: computed.avgNdvi != null
        ? (computed.avgNdvi < 0.3 ? 'Требует внимания' : 'В норме')
        : null,
    },
    {
      key: 'alerts',
      icon: '⚠️',
      label: 'Критических',
      value: computed.criticalAlerts,
      color: computed.criticalAlerts > 0 ? '#ef4444' : '#16a34a',
    },
    // Строка 2
    {
      key: 'updated',
      icon: '⏱',
      label: 'Обновлено',
      value: formatDate(computed.lastUpdate) || '—',
      color: '#1a2e23',
    },
    {
      key: 'area',
      icon: '📐',
      label: 'Площадь (га)',
      value: computed.avgArea != null ? computed.avgArea.toFixed(1) : '—',
      color: '#1a2e23',
      subtitle: computed.avgArea != null
        ? `Средняя по ${computed.totalFields} полям`
        : null,
    },
    {
      key: 'normal',
      icon: '✅',
      label: 'Норма NDVI',
      value: computed.normalCount,
      color: '#16a34a',
    },
  ];

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(3, 1fr)',
      gap: 10,
      marginBottom: 20,
    }}>
      {cards.map(({ key, ...props }) => (
        <KpiCard key={key} {...props} />
      ))}
    </div>
  );
}
