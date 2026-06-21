import React, { useState, useEffect, useMemo } from 'react';
import apiClient from '../api/client';

// Цвет по среднему NDVI
function ndviColor(v) {
  if (!v || v < 0.2) return '#ef4444';
  if (v < 0.35) return '#f97316';
  if (v < 0.5) return '#eab308';
  if (v < 0.65) return '#84cc16';
  return '#4ade80';
}

// Скелетон для карточки
function CardSkeleton() {
  return (
    <div style={{
      background: '#ffffff', border: '1px solid #e0e7e3',
      borderRadius: 12, padding: 20,
      animation: 'pulse 1.5s ease-in-out infinite'
    }}>
      <div style={{ height: 20, background: '#e0e7e3', borderRadius: 4, marginBottom: 12, width: '70%' }} />
      <div style={{ height: 14, background: '#e0e7e3', borderRadius: 4, marginBottom: 20, width: '40%' }} />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        {[...Array(4)].map((_, i) => (
          <div key={i} style={{ height: 52, background: '#e0e7e3', borderRadius: 8 }} />
        ))}
      </div>
    </div>
  );
}

// Карточка предприятия — использует только данные из списка (без доп. запросов)
function EnterpriseCard({ enterprise, onSelect, onNavigate }) {
  const ndvi = enterprise.avg_ndvi;
  const hasCritical = enterprise.critical_alerts > 0;
  const problems = enterprise.fields_with_problems || 0;

  return (
    <div
      onClick={() => onSelect(enterprise)}
      style={{
        background: '#ffffff',
        border: `1px solid ${hasCritical ? '#7f1d1d' : '#e0e7e3'}`,
        borderRadius: 12,
        padding: 20,
        cursor: 'pointer',
        transition: 'all 0.15s',
        position: 'relative',
        overflow: 'hidden',
      }}
      onMouseEnter={e => {
        e.currentTarget.style.border = '1px solid #16a34a';
        e.currentTarget.style.background = '#f1f5f3';
      }}
      onMouseLeave={e => {
        e.currentTarget.style.border = `1px solid ${hasCritical ? '#7f1d1d' : '#e0e7e3'}`;
        e.currentTarget.style.background = '#ffffff';
      }}
    >
      {/* Бейдж критических алертов */}
      {hasCritical && (
        <div style={{
          position: 'absolute', top: 12, right: 12,
          background: '#ef4444', color: '#fff',
          fontSize: 11, fontWeight: 700,
          padding: '2px 8px', borderRadius: 20,
        }}>
          ⚠ {enterprise.critical_alerts} крит.
        </div>
      )}

      {/* Название */}
      <div style={{ fontSize: 15, fontWeight: 600, color: '#1a2e23', marginBottom: 4, paddingRight: hasCritical ? 80 : 0 }}>
        {enterprise.name}
      </div>
      <div style={{ fontSize: 12, color: '#6b8578', marginBottom: 16 }}>
        {enterprise.code} • {enterprise.region}{' '}
        {enterprise.total_area_ha != null && `• ${enterprise.total_area_ha.toFixed(1)} га`}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        {/* Поля */}
        <div style={{ background: '#f1f5f3', borderRadius: 8, padding: '10px 12px' }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: '#1a2e23' }}>
            {enterprise.total_fields ?? '—'}
          </div>
          <div style={{ fontSize: 11, color: '#6b8578' }}>Полей</div>
        </div>

        {/* NDVI */}
        <div style={{ background: '#f1f5f3', borderRadius: 8, padding: '10px 12px' }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: ndviColor(ndvi) }}>
            {ndvi != null ? ndvi.toFixed(3) : '—'}
          </div>
          <div style={{ fontSize: 11, color: '#6b8578' }}>Средний NDVI</div>
        </div>

        {/* Алерты */}
        <div style={{ background: '#f1f5f3', borderRadius: 8, padding: '10px 12px' }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: enterprise.active_alerts > 0 ? '#f97316' : '#16a34a' }}>
            {enterprise.active_alerts ?? '—'}
          </div>
          <div style={{ fontSize: 11, color: '#6b8578' }}>Алертов</div>
        </div>

        {/* Проблемные поля */}
        <div style={{ background: '#f1f5f3', borderRadius: 8, padding: '10px 12px' }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: problems > 0 ? '#ef4444' : '#16a34a' }}>
            {problems}
          </div>
          <div style={{ fontSize: 11, color: '#6b8578' }}>С проблемами</div>
        </div>
      </div>

      <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ fontSize: 12, color: '#16a34a', display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}
          onClick={e => { e.stopPropagation(); onSelect(enterprise); }}>
          Смотреть на карте →
        </span>
        <button
          onClick={e => {
            e.stopPropagation();
            if (onNavigate) onNavigate('enterprise-detail', enterprise.id);
          }}
          style={{
            background: 'transparent',
            border: '1px solid #e0e7e3',
            borderRadius: 6,
            padding: '4px 12px',
            color: '#16a34a',
            fontSize: 11,
            cursor: 'pointer',
            transition: 'all 0.15s',
            marginLeft: 'auto',
          }}
          onMouseEnter={e => { e.currentTarget.style.background = '#f1f5f3'; e.currentTarget.style.borderColor = '#16a34a'; }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.borderColor = '#e0e7e3'; }}
        >
          📊 Подробнее
        </button>
      </div>
    </div>
  );
}

export default function EnterprisesPage({ onNavigate }) {
  const [enterprises, setEnterprises] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');

  useEffect(() => {
    apiClient.get('/api/enterprises/')
      .then(r => setEnterprises(r.data))
      .catch(err => {
        console.error('Enterprises API error:', err.response?.status, err.response?.data, err.message);
        setError('Не удалось загрузить список предприятий');
      })
      .finally(() => setLoading(false));
  }, []);

  // При клике → переходим к полям с фильтром по предприятию
  function handleSelect(enterprise) {
    if (onNavigate) {
      onNavigate('enterprise', enterprise.id);
    }
  }

  const filtered = enterprises.filter(e =>
    e.name?.toLowerCase().includes(search.toLowerCase()) ||
    e.code?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div style={{ padding: '0 24px 24px', color: '#1a2e23', height: '100%', overflowY: 'auto' }}>
      {/* Заголовок */}
      <div style={{ paddingTop: 24, marginBottom: 24 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, color: '#1a2e23', margin: 0 }}>
          Предприятия
        </h1>
        <p style={{ fontSize: 14, color: '#6b8578', marginTop: 4 }}>
          Дочерние предприятия Бухоро Агрокластер
        </p>
      </div>

      {/* Поиск */}
      <div style={{ marginBottom: 20 }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="🔍  Поиск предприятия..."
          style={{
            width: '100%', maxWidth: 400,
            background: '#ffffff', border: '1px solid #e0e7e3',
            borderRadius: 8, padding: '10px 14px',
            color: '#1a2e23', fontSize: 14,
            outline: 'none', boxSizing: 'border-box'
          }}
        />
      </div>

      {error && (
        <div style={{ color: '#ef4444', padding: '20px 0', fontSize: 14 }}>{error}</div>
      )}

      {loading ? (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
          gap: 16
        }}>
          {[...Array(6)].map((_, i) => <CardSkeleton key={i} />)}
        </div>
      ) : (
        <>
          <div style={{ fontSize: 13, color: '#6b8578', marginBottom: 12 }}>
            {filtered.length} из {enterprises.length} предприятий
          </div>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
            gap: 16
          }}>
            {filtered.map(e => (
              <EnterpriseCard
                key={e.id}
                enterprise={e}
                onSelect={handleSelect}
                onNavigate={onNavigate}
              />
            ))}
            {filtered.length === 0 && (
              <div style={{ color: '#6b8578', padding: '40px 0', fontSize: 14, gridColumn: '1/-1', textAlign: 'center' }}>
                Предприятия не найдены
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
