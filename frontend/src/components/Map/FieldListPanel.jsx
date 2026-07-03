import { useState, useEffect, useMemo } from 'react';

// ─── Helper functions ─────────────────────────────────────────────────────────
const getCropColor = (crop) => {
  const colors = {
    'Пшеница': '#eab308',
    'Хлопок (капельн.)': '#3b82f6',
    'Хлопок': '#ec4899',
    'Хлопок (открыт.)': '#ec4899',
    'Люцерна': '#22c55e',
    'Овощи': '#f97316',
    'Кукуруза': '#8b5cf6',
    'Рис': '#06b6d4',
  };
  if (!crop) return '#9ca3af';
  for (const [key, color] of Object.entries(colors)) {
    if (crop.includes(key) || key.includes(crop)) return color;
  }
  return '#9ca3af';
};

const getNdviColor = (ndvi) => {
  if (ndvi < 0.15) return '#dc2626';
  if (ndvi < 0.3) return '#f97316';
  if (ndvi < 0.45) return '#eab308';
  if (ndvi < 0.6) return '#84cc16';
  return '#16a34a';
};

const getNdviTextColor = (ndvi) => {
  if (ndvi < 0.15) return 'text-red-600';
  if (ndvi < 0.3) return 'text-orange-600';
  if (ndvi < 0.45) return 'text-yellow-600';
  if (ndvi < 0.6) return 'text-lime-600';
  return 'text-green-600';
};

// Derive a human-readable status label and color from a field record.
// Uses only data already available in frontend (last_ndvi, active_alerts).
function getFieldStatus(field) {
  if (field.last_ndvi == null) return { label: 'Без данных', color: '#9ca3af' };
  if (field.last_ndvi < 0.3 || (field.active_alerts || 0) > 0) return { label: 'Риск', color: '#f97316' };
  if (field.last_ndvi < 0.45) return { label: 'Умеренный', color: '#eab308' };
  return { label: 'Хороший', color: '#16a34a' };
}

export default function FieldListPanel({
  fields,
  enterprises,
  selectedFieldId,
  highlightedFieldId,
  onFieldSelect,
  onFieldHover,
  onOpenFullDetail,
  onNavigate,
  enterpriseId,
}) {
  const [searchQuery, setSearchQuery] = useState('');
  const [enterpriseFilter, setEnterpriseFilter] = useState(enterpriseId || null);
  const [cropFilter, setCropFilter] = useState(null);
  const [statusFilter, setStatusFilter] = useState(null); // 'has_data' | 'no_data' | null
  const [collapsed, setCollapsed] = useState(false);

  // Sync enterpriseId prop → enterpriseFilter
  useEffect(() => {
    if (enterpriseId) setEnterpriseFilter(enterpriseId);
  }, [enterpriseId]);

  // Derive unique crop list from loaded fields
  const cropOptions = useMemo(() => {
    const crops = new Set();
    fields.forEach(f => {
      if (f.current_crop) crops.add(f.current_crop);
    });
    return ['Все культуры', ...Array.from(crops).sort()];
  }, [fields]);

  // Filter by search + enterprise + crop + status
  const filteredFields = useMemo(() => {
    return fields.filter(f => {
      if (searchQuery && !f.name.toLowerCase().includes(searchQuery.toLowerCase())) return false;
      if (enterpriseFilter !== null && f.enterprise_id !== enterpriseFilter) return false;
      if (cropFilter && f.current_crop !== cropFilter) return false;
      if (statusFilter === 'has_data' && f.last_ndvi == null) return false;
      if (statusFilter === 'no_data' && f.last_ndvi != null) return false;
      return true;
    });
  }, [fields, searchQuery, enterpriseFilter, cropFilter, statusFilter]);

  // Sort: alerts first, then NDVI ascending
  const sortedFields = useMemo(() => {
    return [...filteredFields].sort((a, b) => {
      const aAlerts = a.active_alerts || 0;
      const bAlerts = b.active_alerts || 0;
      if (aAlerts > 0 && bAlerts === 0) return -1;
      if (aAlerts === 0 && bAlerts > 0) return 1;
      const aNdvi = a.last_ndvi ?? 999;
      const bNdvi = b.last_ndvi ?? 999;
      return aNdvi - bNdvi;
    });
  }, [filteredFields]);

  // Stats
  const avgNdvi = useMemo(() => {
    const vals = sortedFields.filter(f => f.last_ndvi != null).map(f => f.last_ndvi);
    if (!vals.length) return 0;
    return vals.reduce((a, b) => a + b, 0) / vals.length;
  }, [sortedFields]);

  const alertCount = useMemo(() => {
    return sortedFields.filter(f => (f.active_alerts || 0) > 0).length;
  }, [sortedFields]);

  const totalArea = useMemo(() => {
    return sortedFields.reduce((s, f) => s + (f.area_ha || 0), 0);
  }, [sortedFields]);

  const noDataCount = useMemo(() => {
    return sortedFields.filter(f => f.last_ndvi == null).length;
  }, [sortedFields]);

  // Check if any filter is active (excluding enterprise which is a view-level filter)
  const hasActiveFilters = searchQuery || cropFilter || statusFilter;

  function resetFilters() {
    setSearchQuery('');
    setCropFilter(null);
    setStatusFilter(null);
  }

  // Check selected field is still in the filtered list
  const selectedInFilter = selectedFieldId
    ? sortedFields.some(f => f.id === selectedFieldId)
    : true;

  return (
    <>
      {/* Collapse toggle — always visible */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="absolute left-[380px] top-1/2 -translate-y-1/2 w-6 h-12 bg-white rounded-r-lg shadow-md border border-l-0 border-gray-200 flex items-center justify-center text-gray-400 hover:text-gray-600 z-40"
        style={{ left: collapsed ? '0px' : '380px' }}
      >
        <svg className={`w-4 h-4 transition-transform ${collapsed ? 'rotate-180' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M15 18l-6-6 6-6" />
        </svg>
      </button>

      {/* Panel */}
      <div
        className={`absolute top-3 bottom-3 z-30 w-[380px] bg-white rounded-2xl shadow-xl border border-gray-200 flex flex-col overflow-hidden transition-transform duration-300 ${
          collapsed ? '-translate-x-[calc(100%+12px)]' : 'left-3'
        }`}
      >
        {/* ── Header + Filters ──────────────────────────────────── */}
        <div className="p-4 border-b border-gray-100">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold text-gray-900">Поля</h2>
            <span className="text-sm text-gray-500">
              {filteredFields.length} / {fields.length}
            </span>
          </div>

          {/* Search */}
          <div className="relative mb-2">
            <input
              type="text"
              placeholder="Поиск поля"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-green-500/30 focus:border-green-500"
            />
            <svg className="absolute left-3 top-2.5 w-4 h-4 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
            </svg>
          </div>

          {/* Enterprise pills */}
          <div className="flex gap-2 mb-2 overflow-x-auto pb-1">
            <button
              onClick={() => setEnterpriseFilter(null)}
              className={`px-3 py-1 text-xs rounded-full border transition-colors flex-shrink-0 ${
                enterpriseFilter === null
                  ? 'bg-green-600 text-white border-green-600'
                  : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
              }`}
            >
              Все предприятия
            </button>
            {enterprises.map(e => (
              <button
                key={e.id}
                onClick={() => setEnterpriseFilter(e.id)}
                className={`px-3 py-1 text-xs rounded-full border transition-colors truncate max-w-[140px] flex-shrink-0 ${
                  enterpriseFilter === e.id
                    ? 'bg-green-600 text-white border-green-600'
                    : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                }`}
              >
                {e.name.replace(' Агрокластер', '')}
              </button>
            ))}
          </div>

          {/* Crop filter dropdown */}
          <div className="flex gap-2 mb-2">
            <select
              value={cropFilter || ''}
              onChange={e => setCropFilter(e.target.value || null)}
              className="flex-1 px-2 py-1.5 text-xs border border-gray-200 rounded-lg bg-gray-50 text-gray-700 focus:outline-none focus:ring-2 focus:ring-green-500/30 focus:border-green-500"
            >
              {cropOptions.map(crop => (
                <option key={crop} value={crop === 'Все культуры' ? '' : crop}>
                  {crop}
                </option>
              ))}
            </select>
          </div>

          {/* Status / data availability filter */}
          <div className="flex gap-2">
            <button
              onClick={() => setStatusFilter(null)}
              className={`flex-1 px-2 py-1.5 text-xs rounded-lg border font-medium transition-colors ${
                statusFilter === null
                  ? 'bg-green-600 text-white border-green-600'
                  : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
              }`}
            >
              Все статусы
            </button>
            <button
              onClick={() => setStatusFilter(statusFilter === 'has_data' ? null : 'has_data')}
              className={`flex-1 px-2 py-1.5 text-xs rounded-lg border font-medium transition-colors ${
                statusFilter === 'has_data'
                  ? 'bg-green-600 text-white border-green-600'
                  : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
              }`}
            >
              С данными
            </button>
            <button
              onClick={() => setStatusFilter(statusFilter === 'no_data' ? null : 'no_data')}
              className={`flex-1 px-2 py-1.5 text-xs rounded-lg border font-medium transition-colors ${
                statusFilter === 'no_data'
                  ? 'bg-green-600 text-white border-green-600'
                  : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
              }`}
            >
              Без данных
            </button>
          </div>

          {/* Reset filters link */}
          {hasActiveFilters && (
            <button
              onClick={resetFilters}
              className="mt-2 text-xs text-green-600 hover:text-green-700 font-medium"
            >
              Сбросить фильтры
            </button>
          )}
        </div>

        {/* ── Field list ──────────────────────────────────────── */}
        <div className="flex-1 overflow-y-auto">
          {sortedFields.length === 0 && (
            <div className="p-6 text-center">
              <div className="text-sm text-gray-400 mb-1">
                {searchQuery || cropFilter || statusFilter
                  ? 'Ничего не найдено'
                  : 'Нет полей'}
              </div>
              <div className="text-xs text-gray-400">
                Найдено полей: 0
              </div>
            </div>
          )}

          {/* Banner when selected field is filtered out */}
          {selectedFieldId && !selectedInFilter && (
            <div className="mx-3 mt-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700">
              Выбранное поле отфильтровано. Измените фильтры или выберите другое поле.
            </div>
          )}

          {sortedFields.map(field => {
            const status = getFieldStatus(field);
            return (
              <div
                key={field.id}
                onClick={() => onFieldSelect(field.id)}
                onMouseEnter={() => onFieldHover?.(field.id)}
                onMouseLeave={() => onFieldHover?.(null)}
                className={`px-4 py-3 border-b border-gray-100 cursor-pointer transition-colors hover:bg-gray-50 ${
                  highlightedFieldId === field.id ? 'bg-green-50' : ''
                } ${selectedFieldId === field.id ? 'bg-green-50 border-l-4 border-l-green-500' : ''}`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                            style={{ backgroundColor: getCropColor(field.current_crop) }} />
                      <span className="text-sm font-medium text-gray-900 truncate">{field.name}</span>
                      {/* Status badge */}
                      <span
                        className="text-[11px] font-medium px-1.5 py-0.5 rounded-full text-white flex-shrink-0"
                        style={{ backgroundColor: status.color }}
                      >
                        {status.label}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-1 ml-[18px]">
                      <span className="text-xs text-gray-500">{field.area_ha?.toFixed(1)} га</span>
                      <span className="text-xs text-gray-400">{field.current_crop || '—'}</span>
                      {(field.active_alerts || 0) > 0 && (
                        <span className="text-xs text-red-500">⚠ {field.active_alerts}</span>
                      )}
                    </div>
                  </div>

                  {/* NDVI indicator */}
                  <div className="text-right flex-shrink-0 ml-2">
                    {field.last_ndvi != null ? (
                      <div className="flex items-center gap-1.5">
                        <span className={`text-sm font-mono font-semibold ${getNdviTextColor(field.last_ndvi)}`}>
                          {field.last_ndvi.toFixed(2)}
                        </span>
                        <span className="w-3 h-3 rounded-full" style={{ backgroundColor: getNdviColor(field.last_ndvi) }} />
                      </div>
                    ) : (
                      <span className="text-xs text-gray-400">—</span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* ── Stats bar ───────────────────────────────────────── */}
        <div className="p-3 border-t border-gray-100 bg-gray-50">
          <div className="flex items-center justify-between text-xs text-gray-500">
            <span>Ср. NDVI: <strong className="text-gray-700">{avgNdvi.toFixed(3)}</strong></span>
            <span>Риск: <strong className="text-red-600">{alertCount}</strong></span>
            <span>Нет данных: <strong className="text-gray-400">{noDataCount}</strong></span>
            <span>{totalArea.toFixed(0)} га</span>
          </div>
        </div>
      </div>
    </>
  );
}
