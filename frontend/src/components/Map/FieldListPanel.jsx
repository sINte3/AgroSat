import { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import FieldDetailPanel from './FieldDetailPanel';

const API = 'http://localhost:8000/api';

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
  const [collapsed, setCollapsed] = useState(false);
  const [selectedField, setSelectedField] = useState(null);

  // Sync enterpriseId prop → enterpriseFilter
  useEffect(() => {
    if (enterpriseId) setEnterpriseFilter(enterpriseId);
  }, [enterpriseId]);

  // Load detail when selectedFieldId changes
  useEffect(() => {
    if (!selectedFieldId) {
      setSelectedField(null);
      return;
    }
    const basic = fields.find(f => f.id === selectedFieldId);
    setSelectedField(basic || null);

    axios.get(`${API}/fields/${selectedFieldId}`)
      .then(res => {
        const data = res.data;
        // API returns GeoJSON Feature — data in .properties
        const props = data.properties || data;
        setSelectedField(prev => ({ ...prev, ...props }));
      })
      .catch(err => console.error('Error loading field detail:', err));
  }, [selectedFieldId, fields]);

  // Filter by search + enterprise
  const filteredFields = useMemo(() => {
    return fields.filter(f => {
      if (searchQuery && !f.name.toLowerCase().includes(searchQuery.toLowerCase())) return false;
      if (enterpriseFilter !== null && f.enterprise_id !== enterpriseFilter) return false;
      return true;
    });
  }, [fields, searchQuery, enterpriseFilter]);

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
        {selectedField ? (
          <FieldDetailPanel
            field={selectedField}
            onBack={() => {
              setSelectedField(null);
              onFieldSelect(null);
            }}
            onNavigate={onNavigate}
          />
        ) : (
          /* ── List mode ─────────────────────────────────────────── */
          <>
            {/* Header */}
            <div className="p-4 border-b border-gray-100">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-lg font-semibold text-gray-900">Поля</h2>
                <span className="text-sm text-gray-500">{fields.length} полей</span>
              </div>

              {/* Search */}
              <div className="relative">
                <input
                  type="text"
                  placeholder="Поиск по названию..."
                  value={searchQuery}
                  onChange={e => setSearchQuery(e.target.value)}
                  className="w-full pl-9 pr-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-green-500/30 focus:border-green-500"
                />
                <svg className="absolute left-3 top-2.5 w-4 h-4 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
                </svg>
              </div>

              {/* Enterprise pills */}
              <div className="flex gap-2 mt-3 overflow-x-auto pb-1">
                <button
                  onClick={() => setEnterpriseFilter(null)}
                  className={`px-3 py-1 text-xs rounded-full border transition-colors flex-shrink-0 ${
                    enterpriseFilter === null
                      ? 'bg-green-600 text-white border-green-600'
                      : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                  }`}
                >
                  Все
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
            </div>

            {/* Field list */}
            <div className="flex-1 overflow-y-auto">
              {sortedFields.length === 0 && (
                <div className="p-6 text-center text-sm text-gray-400">
                  {searchQuery ? 'Ничего не найдено' : 'Нет полей'}
                </div>
              )}
              {sortedFields.map(field => (
                <div
                  key={field.id}
                  onClick={() => onFieldSelect(field.id)}
                  onMouseEnter={() => onFieldHover?.(field.id)}
                  onMouseLeave={() => onFieldHover?.(null)}
                  className={`px-4 py-3 border-b border-gray-100 cursor-pointer transition-colors hover:bg-gray-50 ${
                    highlightedFieldId === field.id ? 'bg-green-50' : ''
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                              style={{ backgroundColor: getCropColor(field.current_crop) }} />
                        <span className="text-sm font-medium text-gray-900 truncate">{field.name}</span>
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
              ))}
            </div>

            {/* Stats bar */}
            <div className="p-3 border-t border-gray-100 bg-gray-50">
              <div className="flex items-center justify-between text-xs text-gray-500">
                <span>Ср. NDVI: <strong className="text-gray-700">{avgNdvi.toFixed(3)}</strong></span>
                <span>Проблемных: <strong className="text-red-600">{alertCount}</strong></span>
                <span>Площадь: <strong className="text-gray-700">{totalArea.toFixed(0)} га</strong></span>
              </div>
            </div>
          </>
        )}
      </div>
    </>
  );
}
