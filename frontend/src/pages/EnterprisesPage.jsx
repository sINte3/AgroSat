import React, { useState, useEffect, useMemo } from 'react';
import apiClient from '../api/client';

// ─── NDVI text colour ──────────────────────────────────────────────────────────
function ndviColor(v) {
  if (!v || v < 0.2) return '#ef4444';
  if (v < 0.35) return '#f97316';
  if (v < 0.5) return '#eab308';
  if (v < 0.65) return '#84cc16';
  return '#4ade80';
}

// ─── Skeleton loader ───────────────────────────────────────────────────────────
function CardSkeleton() {
  return (
    <div className="card animate-pulse p-5 rounded-xl">
      <div className="h-5 bg-agro-surface2 rounded w-3/4 mb-3" />
      <div className="h-3 bg-agro-surface2 rounded w-1/2 mb-5" />
      <div className="grid grid-cols-2 gap-2">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-12 bg-agro-surface2 rounded-lg" />
        ))}
      </div>
    </div>
  );
}

function TableSkeleton({ rows = 6 }) {
  return (
    <div className="animate-pulse space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex gap-3 p-3 rounded-lg bg-agro-card">
          <div className="h-4 bg-agro-surface2 rounded flex-1" />
          <div className="h-4 bg-agro-surface2 rounded w-20" />
          <div className="h-4 bg-agro-surface2 rounded w-16" />
          <div className="h-4 bg-agro-surface2 rounded w-16" />
          <div className="h-4 bg-agro-surface2 rounded w-20" />
          <div className="h-4 bg-agro-surface2 rounded w-24" />
        </div>
      ))}
    </div>
  );
}

// ─── Sort icon ─────────────────────────────────────────────────────────────────
function SortIcon({ active, dir }) {
  return (
    <span className={`ml-1 text-[11px] ${active ? 'opacity-100' : 'opacity-30'}`}>
      {active ? (dir === 'asc' ? '▲' : '▼') : '⇅'}
    </span>
  );
}

// ─── Enterprise card (migrated from inline styles to Tailwind) ──────────────────
function EnterpriseCard({ enterprise, onSelect, onNavigate }) {
  const ndvi = enterprise.avg_ndvi;
  const hasCritical = enterprise.critical_alerts > 0;
  const problems = enterprise.fields_with_problems || 0;

  return (
    <div
      onClick={() => onSelect(enterprise)}
      className={`card rounded-xl p-5 cursor-pointer transition-all duration-150 relative overflow-hidden
        ${hasCritical ? 'border-red-800' : ''} hover:border-agro-accent hover:bg-agro-surface2`}
    >
      {hasCritical && (
        <span className="absolute top-3 right-3 bg-agro-danger text-white text-[11px] font-bold px-2 py-0.5 rounded-full">
          ⚠ {enterprise.critical_alerts} крит.
        </span>
      )}

      <h3 className="text-[15px] font-semibold text-agro-text mb-1 pr-20">
        {enterprise.name}
      </h3>
      <p className="text-xs text-agro-muted mb-4">
        {enterprise.code} • {enterprise.region}
        {enterprise.total_area_ha != null && ` • ${enterprise.total_area_ha.toFixed(1)} га`}
      </p>

      <div className="grid grid-cols-2 gap-2">
        <div className="bg-agro-surface2 rounded-lg p-2.5">
          <div className="text-[22px] font-bold text-agro-text">{enterprise.total_fields ?? '—'}</div>
          <div className="text-[11px] text-agro-muted">Полей</div>
        </div>
        <div className="bg-agro-surface2 rounded-lg p-2.5">
          <div className="text-[22px] font-bold" style={{ color: ndviColor(ndvi) }}>
            {ndvi != null ? ndvi.toFixed(3) : '—'}
          </div>
          <div className="text-[11px] text-agro-muted">Средний NDVI</div>
        </div>
        <div className="bg-agro-surface2 rounded-lg p-2.5">
          <div className={`text-[22px] font-bold ${enterprise.active_alerts > 0 ? 'text-agro-warning' : 'text-agro-accent'}`}>
            {enterprise.active_alerts ?? '—'}
          </div>
          <div className="text-[11px] text-agro-muted">Алертов</div>
        </div>
        <div className="bg-agro-surface2 rounded-lg p-2.5">
          <div className={`text-[22px] font-bold ${problems > 0 ? 'text-agro-danger' : 'text-agro-accent'}`}>
            {problems}
          </div>
          <div className="text-[11px] text-agro-muted">С проблемами</div>
        </div>
      </div>

      <div className="mt-3.5 flex items-center gap-3">
        <span
          className="text-xs text-agro-accent flex items-center gap-1 cursor-pointer hover:underline"
          onClick={e => { e.stopPropagation(); onSelect(enterprise); }}
        >
          Смотреть на карте →
        </span>
        <button
          onClick={e => {
            e.stopPropagation();
            if (onNavigate) onNavigate('enterprise-detail', enterprise.id);
          }}
          className="ml-auto bg-transparent border border-agro-border rounded-md px-3 py-1 text-[11px] text-agro-accent cursor-pointer transition-all hover:bg-agro-surface2 hover:border-agro-accent"
        >
          📊 Подробнее
        </button>
      </div>
    </div>
  );
}

// ─── Sortable table row ────────────────────────────────────────────────────────
function EnterpriseTableRow({ enterprise, index, onSelect, onNavigate }) {
  const ndvi = enterprise.avg_ndvi;
  const hasCritical = enterprise.critical_alerts > 0;

  return (
    <tr
      onClick={() => onSelect(enterprise)}
      className={`cursor-pointer transition-colors hover:bg-agro-surface2
        ${index % 2 === 0 ? 'bg-agro-card' : 'bg-agro-surface'}`}
    >
      <td className="px-3 py-3 text-sm text-agro-text font-medium max-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
        {enterprise.name}
      </td>
      <td className="px-3 py-3 text-sm text-agro-text font-mono tabular-nums text-center">
        {enterprise.total_fields ?? '—'}
      </td>
      <td className="px-3 py-3 text-sm text-agro-text font-mono tabular-nums text-center">
        {enterprise.total_area_ha != null ? enterprise.total_area_ha.toFixed(1) : '—'}
      </td>
      <td className="px-3 py-3 text-sm text-center font-mono tabular-nums">
        <span className="font-bold" style={{ color: ndviColor(ndvi) }}>
          {ndvi != null ? ndvi.toFixed(3) : '—'}
        </span>
      </td>
      <td className="px-3 py-3 text-sm text-center">
        {(() => {
          const a = enterprise.active_alerts ?? 0;
          const c = enterprise.critical_alerts ?? 0;
          if (a === 0) return <span className="text-agro-muted">—</span>;
          return (
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold
              ${c > 0 ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'}`}>
              {c > 0 && <span className="w-1.5 h-1.5 rounded-full bg-red-500" />}
              {a}
            </span>
          );
        })()}
      </td>
      <td className="px-3 py-3 text-sm text-center">
        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold
          ${hasCritical ? 'bg-red-100 text-red-700' : 'text-agro-accent'}`}>
          {enterprise.fields_with_problems ?? 0}
        </span>
      </td>
    </tr>
  );
}

// ─── Main page ─────────────────────────────────────────────────────────────────
export default function EnterprisesPage({ onNavigate }) {
  const [enterprises, setEnterprises] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [viewMode, setViewMode] = useState('cards'); // 'cards' | 'table'
  const [sortKey, setSortKey] = useState('name');
  const [sortDir, setSortDir] = useState('asc');

  useEffect(() => {
    apiClient.get('/api/enterprises/')
      .then(r => setEnterprises(r.data))
      .catch(err => {
        console.error('Enterprises API error:', err.response?.status, err.response?.data, err.message);
        setError('Не удалось загрузить список предприятий');
      })
      .finally(() => setLoading(false));
  }, []);

  function handleSelect(enterprise) {
    if (onNavigate) {
      onNavigate('enterprise', enterprise.id);
    }
  }

  const filtered = useMemo(() => {
    let list = enterprises.filter(e =>
      e.name?.toLowerCase().includes(search.toLowerCase()) ||
      e.code?.toLowerCase().includes(search.toLowerCase())
    );

    list.sort((a, b) => {
      let va, vb;
      switch (sortKey) {
        case 'name':
          va = (a.name || '').toLowerCase();
          vb = (b.name || '').toLowerCase();
          return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        case 'total_fields':
          return sortDir === 'asc'
            ? (a.total_fields ?? 0) - (b.total_fields ?? 0)
            : (b.total_fields ?? 0) - (a.total_fields ?? 0);
        case 'total_area_ha':
          return sortDir === 'asc'
            ? (a.total_area_ha ?? 0) - (b.total_area_ha ?? 0)
            : (b.total_area_ha ?? 0) - (a.total_area_ha ?? 0);
        case 'avg_ndvi':
          return sortDir === 'asc'
            ? (a.avg_ndvi ?? -999) - (b.avg_ndvi ?? -999)
            : (b.avg_ndvi ?? -999) - (a.avg_ndvi ?? -999);
        case 'active_alerts':
          return sortDir === 'asc'
            ? (a.active_alerts ?? 0) - (b.active_alerts ?? 0)
            : (b.active_alerts ?? 0) - (a.active_alerts ?? 0);
        case 'fields_with_problems':
          return sortDir === 'asc'
            ? (a.fields_with_problems ?? 0) - (b.fields_with_problems ?? 0)
            : (b.fields_with_problems ?? 0) - (a.fields_with_problems ?? 0);
        default:
          return 0;
      }
    });

    return list;
  }, [enterprises, search, sortKey, sortDir]);

  // Aggregate totals from the loaded enterprise list
  const totals = useMemo(() => {
    if (!enterprises || enterprises.length === 0) return null;
    return {
      totalEnterprises: enterprises.length,
      totalFields: enterprises.reduce((s, e) => s + (e.total_fields || 0), 0),
      totalArea: enterprises.reduce((s, e) => s + (e.total_area_ha || 0), 0),
      totalAlerts: enterprises.reduce((s, e) => s + (e.active_alerts || 0), 0),
      avgNdvi: (() => {
        const vals = enterprises.map(e => e.avg_ndvi).filter(v => v != null);
        return vals.length > 0 ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
      })(),
    };
  }, [enterprises]);

  function handleSort(key) {
    setSortDir(prev => sortKey === key ? (prev === 'asc' ? 'desc' : 'asc') : 'asc');
    setSortKey(key);
  }

  const TABLE_COLUMNS = [
    { key: 'name',              label: 'Предприятие',           align: 'left',   width: 'auto' },
    { key: 'total_fields',      label: 'Полей',                align: 'center', width: '80px' },
    { key: 'total_area_ha',     label: 'Площадь, га',          align: 'center', width: '90px' },
    { key: 'avg_ndvi',          label: 'Средний NDVI',          align: 'center', width: '100px' },
    { key: 'active_alerts',     label: 'Активные алерты',       align: 'center', width: '110px' },
    { key: 'fields_with_problems', label: 'Проблемные поля',    align: 'center', width: '105px' },
  ];

  return (
    <div className="p-6 text-agro-text h-full overflow-y-auto">
      {/* ── Management header ─────────────────────────────────────────── */}
      <div className="mb-6">
        <h1 className="text-xl font-bold text-agro-text m-0">Сравнение предприятий</h1>
        <p className="text-sm text-agro-muted mt-1">Дочерние предприятия Бухоро Агрокластер</p>
      </div>

      {/* ── Summary bar ────────────────────────────────────────────────── */}
      {totals && !loading && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5">
          <div className="card p-3 rounded-lg text-center">
            <p className="text-2xl font-bold text-agro-text">{totals.totalEnterprises}</p>
            <p className="text-xs text-agro-muted">Предприятий</p>
          </div>
          <div className="card p-3 rounded-lg text-center">
            <p className="text-2xl font-bold text-agro-text">{totals.totalFields}</p>
            <p className="text-xs text-agro-muted">Всего полей</p>
          </div>
          <div className="card p-3 rounded-lg text-center">
            <p className="text-2xl font-bold text-agro-text">
              {totals.totalArea > 0 ? `${totals.totalArea.toFixed(0)} га` : '—'}
            </p>
            <p className="text-xs text-agro-muted">Всего гектаров</p>
          </div>
          <div className="card p-3 rounded-lg text-center">
            <p className="text-2xl font-bold text-agro-text">
              {totals.avgNdvi != null ? totals.avgNdvi.toFixed(3) : '—'}
            </p>
            <p className="text-xs text-agro-muted">Средний NDVI</p>
          </div>
          <div className="card p-3 rounded-lg text-center">
            <p className={`text-2xl font-bold ${totals.totalAlerts > 0 ? 'text-agro-warning' : 'text-agro-accent'}`}>
              {totals.totalAlerts}
            </p>
            <p className="text-xs text-agro-muted">Активных алертов</p>
          </div>
        </div>
      )}

      {/* ── Search + view toggle row ────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="🔍  Поиск предприятия..."
          className="input flex-1 min-w-[200px] max-w-sm p-2.5 text-sm"
        />
        <div className="flex items-center gap-1 bg-agro-card rounded-lg p-0.5 border border-agro-border">
          <button
            onClick={() => setViewMode('cards')}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors
              ${viewMode === 'cards' ? 'bg-agro-accent text-white' : 'text-agro-muted hover:text-agro-text'}`}
          >
            Карточки
          </button>
          <button
            onClick={() => setViewMode('table')}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors
              ${viewMode === 'table' ? 'bg-agro-accent text-white' : 'text-agro-muted hover:text-agro-text'}`}
          >
            Таблица
          </button>
        </div>
      </div>

      {/* ── Error state ────────────────────────────────────────────────── */}
      {error && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <div className="w-14 h-14 rounded-full bg-red-100 flex items-center justify-center mb-4">
            <svg className="w-7 h-7 text-agro-danger" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <p className="text-sm text-agro-danger font-medium mb-2">{error}</p>
          <button
            onClick={() => window.location.reload()}
            className="text-sm text-agro-accent underline hover:no-underline"
          >
            Повторить загрузку
          </button>
        </div>
      )}

      {/* ── Loading state ──────────────────────────────────────────────── */}
      {loading && (
        viewMode === 'table' ? <TableSkeleton /> : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {[...Array(6)].map((_, i) => <CardSkeleton key={i} />)}
          </div>
        )
      )}

      {/* ── Data ───────────────────────────────────────────────────────── */}
      {!loading && !error && (
        <>
          <div className="text-xs text-agro-muted mb-3">
            {filtered.length} из {enterprises.length} предприятий
            {search && ' (отфильтровано)'}
          </div>

          {/* ═══ TABLE VIEW ═══ */}
          {viewMode === 'table' && (
            <div className="overflow-x-auto rounded-lg border border-agro-border">
              <table className="w-full border-collapse text-sm" style={{ tableLayout: 'fixed' }}>
                <colgroup>
                  <col style={{ width: 'auto' }} />
                  <col style={{ width: '80px' }} />
                  <col style={{ width: '90px' }} />
                  <col style={{ width: '100px' }} />
                  <col style={{ width: '110px' }} />
                  <col style={{ width: '105px' }} />
                </colgroup>
                <thead>
                  <tr className="bg-agro-surface2">
                    {TABLE_COLUMNS.map(col => (
                      <th
                        key={col.key}
                        onClick={() => handleSort(col.key)}
                        className={`px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-agro-muted
                          select-none cursor-pointer whitespace-nowrap`}
                        style={{ textAlign: col.align || 'left' }}
                      >
                        {col.label}
                        <SortIcon active={sortKey === col.key} dir={sortDir} />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.length === 0 ? (
                    <tr>
                      <td colSpan={TABLE_COLUMNS.length} className="text-center py-12 text-sm text-agro-muted">
                        <div className="flex flex-col items-center gap-2">
                          <span className="text-2xl">🔍</span>
                          <span>Предприятия не найдены</span>
                          {search && (
                            <button
                              onClick={() => setSearch('')}
                              className="text-agro-accent underline text-xs"
                            >
                              Сбросить поиск
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ) : (
                    filtered.map((e, i) => (
                      <EnterpriseTableRow
                        key={e.id}
                        enterprise={e}
                        index={i}
                        onSelect={handleSelect}
                        onNavigate={onNavigate}
                      />
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}

          {/* ═══ CARD VIEW ═══ */}
          {viewMode === 'cards' && (
            <>
              {filtered.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-center text-agro-muted">
                  <span className="text-2xl mb-2">🔍</span>
                  <p className="text-sm">Предприятия не найдены</p>
                  {search && (
                    <button
                      onClick={() => setSearch('')}
                      className="text-agro-accent underline text-xs mt-2"
                    >
                      Сбросить поиск
                    </button>
                  )}
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {filtered.map(e => (
                    <EnterpriseCard
                      key={e.id}
                      enterprise={e}
                      onSelect={handleSelect}
                      onNavigate={onNavigate}
                    />
                  ))}
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
