/**
 * EnterpriseFieldsTable.jsx — Таблица всех полей предприятия
 *
 * Props:
 *   fields — массив полей (из API enterprises/{id})
 *   alerts — массив алертов (для подсчёта на поле)
 *   onViewGraph — callback(field) открыть NDVI график (при клике на строку)
 *
 * Колонки: Контур, Культура, Площадь (га), NDVI, Предупреждения
 * Сортировка: по умолчанию NDVI asc (проблемные вверху)
 * Поиск: по коду/названию
 * Фильтры: культура, статус NDVI, наличие алертов
 * Пагинация: 50 строк
 * Скролл: фиксированная высота с вертикальным скроллом
 */

import { useState, useMemo, useCallback } from 'react';

// ─── NDVI Colorscale ────────────────────────────────────────────────────────
function getNDVIBackground(ndvi) {
  if (ndvi == null) return '#6b8578';
  if (ndvi < 0.2) return '#8B0000';
  if (ndvi < 0.35) return '#FF4500';
  if (ndvi < 0.5) return '#FFD700';
  if (ndvi < 0.65) return '#9ACD32';
  if (ndvi < 0.8) return '#228B22';
  return '#006400';
}

const getNDVIScore = (ndvi) => {
  if (ndvi == null) return 'none';
  if (ndvi < 0.3) return 'poor';
  if (ndvi < 0.5) return 'fair';
  return 'good';
};

// ─── Crop name mapping ──────────────────────────────────────────────────────
function formatCropName(cropName) {
  if (!cropName) return '—';
  const name = cropName.toLowerCase();
  if (name.includes('томчи') || name.includes('tomchi') || name.includes('капельн') || name.includes('drip')) {
    return 'Хлопок капля';
  }
  if (name.includes('очик') || name.includes('ochiq') || name.includes('полив') || name.includes('open')) {
    return 'Хлопок полив';
  }
  if (name.includes('галла') || name.includes('galla') || name.includes('пшениц') || name.includes('wheat')) {
    return 'Пшеница';
  }
  if (name.includes('люцерн') || name.includes('alfalfa')) {
    return 'Люцерна';
  }
  if (name.includes('кукуруз') || name.includes('maize') || name.includes('corn')) {
    return 'Кукуруза';
  }
  if (name.includes('рис') || name.includes('rice')) {
    return 'Рис';
  }
  // fallback: return as-is but capitalize
  return cropName.charAt(0).toUpperCase() + cropName.slice(1);
}

// ─── Sort Icon ──────────────────────────────────────────────────────────────
function SortIcon({ active, dir }) {
  return (
    <span style={{ marginLeft: 4, opacity: active ? 1 : 0.3, fontSize: 11 }}>
      {active ? (dir === 'asc' ? '▲' : '▼') : '⇅'}
    </span>
  );
}

// ─── Скелетон ───────────────────────────────────────────────────────────────
function TableSkeleton({ rows = 10 }) {
  return (
    <div style={{ animation: 'pulse 1.5s ease-in-out infinite' }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          style={{
            display: 'flex',
            gap: 12,
            padding: '10px 14px',
            background: i % 2 === 0 ? '#f1f5f3' : '#f8faf9',
          }}
        >
          {Array.from({ length: 5 }).map((_, j) => (
            <div
              key={j}
              style={{
                flex: j === 0 ? 1.8 : j === 1 ? 1.5 : 1,
                height: 16,
                background: '#e0e7e3',
                borderRadius: 4,
              }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

// ─── Row — клик по строке открывает график ──────────────────────────────────
function FieldRow({ field, onViewGraph, style }) {
  const alertCount = field.active_alerts ?? 0;
  const hasCritical = field.alert_severity === 'critical';

  const handleRowClick = () => {
    onViewGraph?.(field);
  };

  return (
    <tr
      style={{
        ...style,
        transition: 'background 0.1s',
        cursor: 'pointer',
      }}
      onMouseEnter={e => { e.currentTarget.style.background = '#e8eeea'; }}
      onMouseLeave={e => { e.currentTarget.style.background = ''; }}
      onClick={handleRowClick}
    >
      {/* Контур */}
      <td style={{ ...tdStyle, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 0 }}>
        {field.name || '—'}
      </td>
      {/* Культура */}
      <td style={{ ...tdStyle, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {formatCropName(field.current_crop)}
      </td>
      {/* Площадь (га) */}
      <td style={{ ...tdStyle, textAlign: 'right', fontFamily: 'monospace', fontVariantNumeric: 'tabular-nums' }}>
        {field.area_ha != null ? field.area_ha.toFixed(1) : '—'}
      </td>
      {/* NDVI */}
      <td style={{ ...tdStyle, textAlign: 'center' }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
          <span style={{
            display: 'inline-block',
            padding: '2px 7px',
            borderRadius: 4,
            fontSize: 11,
            fontWeight: 700,
            fontFamily: "'Roboto Mono', 'Courier New', monospace",
            fontVariantNumeric: 'tabular-nums',
            minWidth: 52,
            textAlign: 'center',
            background: getNDVIBackground(field.current_ndvi),
            color: '#fff',
          }}>
            {field.current_ndvi != null ? field.current_ndvi.toFixed(3) : '—'}
          </span>
          {field.last_ndvi_date && (
            <span style={{ fontSize: 9, color: '#6b8578', lineHeight: 1.2 }}>
              {new Date(field.last_ndvi_date).toLocaleDateString('ru-RU')}
            </span>
          )}
        </div>
      </td>
      {/* Предупреждения */}
      <td style={{ ...tdStyle, textAlign: 'center' }}>
        {alertCount > 0 ? (
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 3,
            background: hasCritical ? '#7f1d1d' : '#78350f',
            color: '#fff',
            borderRadius: 10,
            padding: '2px 8px',
            fontSize: 11,
            fontWeight: 600,
            minWidth: 28,
          }}>
            {hasCritical ? '🔴' : '🟡'} {alertCount}
          </span>
        ) : (
          <span style={{ color: '#a3b5a9', fontSize: 12 }}>—</span>
        )}
      </td>
    </tr>
  );
}

const tdStyle = {
  padding: '9px 8px',
  fontSize: 12,
  lineHeight: '1.3',
  color: '#1a2e23',
  borderBottom: '1px solid #f1f5f3',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  verticalAlign: 'middle',
};

const thStyle = {
  padding: '10px 8px',
  fontSize: 11,
  fontWeight: 600,
  letterSpacing: '0.04em',
  textTransform: 'uppercase',
  color: '#6b8578',
  background: '#f1f5f3',
  textAlign: 'left',
  borderBottom: '1px solid #e0e7e3',
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  userSelect: 'none',
  cursor: 'pointer',
};

// ─── Dropdown фильтр ────────────────────────────────────────────────────────
function FilterSelect({ value, onChange, options, label, allLabel = 'Все' }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <label style={{ fontSize: 12, color: '#6b8578', whiteSpace: 'nowrap' }}>{label}:</label>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        style={{
          background: '#f1f5f3',
          border: '1px solid #e0e7e3',
          borderRadius: 6,
          padding: '6px 10px',
          color: '#1a2e23',
          fontSize: 12,
          outline: 'none',
          cursor: 'pointer',
          minWidth: 100,
        }}
      >
        <option value="">{allLabel}</option>
        {options.map(opt => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>
    </div>
  );
}

// ─── Main Component ─────────────────────────────────────────────────────────
export default function EnterpriseFieldsTable({
  fields,
  alerts,
  onViewGraph,
  loading,
}) {
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState('current_ndvi');
  const [sortDir, setSortDir] = useState('asc');
  const [filterCrop, setFilterCrop] = useState('');
  const [filterNdvi, setFilterNdvi] = useState('');
  const [filterAlerts, setFilterAlerts] = useState('');
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 50;

  // Вычисляемые уникальные культуры для фильтра
  const cropOptions = useMemo(() => {
    const set = new Set();
    (fields || []).forEach(f => {
      if (f.current_crop) set.add(f.current_crop);
    });
    return Array.from(set).sort().map(c => ({ value: c, label: c }));
  }, [fields]);

  // Сортировка + фильтры + поиск
  const processed = useMemo(() => {
    if (!fields) return [];

    let list = [...fields];

    // Поиск
    if (search) {
      const q = search.toLowerCase();
      list = list.filter(f =>
        (f.code && f.code.toLowerCase().includes(q)) ||
        (f.name && f.name.toLowerCase().includes(q))
      );
    }

    // Фильтр по культуре
    if (filterCrop) {
      list = list.filter(f => f.current_crop === filterCrop);
    }

    // Фильтр по NDVI статусу
    if (filterNdvi) {
      list = list.filter(f => {
        const score = getNDVIScore(f.current_ndvi);
        return score === filterNdvi;
      });
    }

    // Фильтр по алертам
    if (filterAlerts === 'with') {
      list = list.filter(f => f.active_alerts > 0);
    }

    // Сортировка
    list.sort((a, b) => {
      let va, vb;
      switch (sortKey) {
        case 'name':
          va = (a.name || '').toLowerCase();
          vb = (b.name || '').toLowerCase();
          return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        case 'crop':
          va = formatCropName(a.current_crop).toLowerCase();
          vb = formatCropName(b.current_crop).toLowerCase();
          return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        case 'area':
          va = a.area_ha ?? -1;
          vb = b.area_ha ?? -1;
          return sortDir === 'asc' ? va - vb : vb - va;
        case 'current_ndvi':
          va = a.current_ndvi ?? -999;
          vb = b.current_ndvi ?? -999;
          return sortDir === 'asc' ? va - vb : vb - va;
        case 'alerts':
          va = a.active_alerts ?? 0;
          vb = b.active_alerts ?? 0;
          return sortDir === 'asc' ? va - vb : vb - va;
        default:
          return 0;
      }
    });

    return list;
  }, [fields, search, sortKey, sortDir, filterCrop, filterNdvi, filterAlerts]);

  const totalPages = Math.max(1, Math.ceil(processed.length / PAGE_SIZE));
  const clampedPage = Math.min(page, totalPages - 1);
  const pageItems = processed.slice(clampedPage * PAGE_SIZE, (clampedPage + 1) * PAGE_SIZE);

  const handleSort = useCallback((key) => {
    setSortDir(prev => sortKey === key ? (prev === 'asc' ? 'desc' : 'asc') : 'asc');
    setSortKey(key);
    setPage(0);
  }, [sortKey]);

  // Колонки: 5 колонок
  const columnHeaders = [
    { key: 'name', label: 'Контур', width: '30%', align: 'left' },
    { key: 'crop', label: 'Культура', width: '22%', align: 'left' },
    { key: 'area', label: 'Площадь (га)', width: '16%', align: 'right' },
    { key: 'current_ndvi', label: 'NDVI', width: '16%', align: 'center' },
    { key: 'alerts', label: 'Предупреждения', width: '16%', align: 'center' },
  ];

  return (
    <div>
      {/* Фильтры и поиск */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 12,
          alignItems: 'center',
          marginBottom: 14,
        }}
      >
        <input
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(0); }}
          placeholder="🔍  Поиск по контуру..."
          className="input"
          style={{ flex: '1 1 200px', maxWidth: 320, padding: '8px 12px', fontSize: 13 }}
        />
        <FilterSelect
          label="Культура"
          value={filterCrop}
          onChange={v => { setFilterCrop(v); setPage(0); }}
          options={cropOptions}
          allLabel="Все культуры"
        />
        <FilterSelect
          label="NDVI"
          value={filterNdvi}
          onChange={v => { setFilterNdvi(v); setPage(0); }}
          options={[
            { value: 'poor', label: 'Плохо (&lt;0.3)' },
            { value: 'fair', label: 'Средне (0.3-0.5)' },
            { value: 'good', label: 'Хорошо (&gt;0.5)' },
          ]}
          allLabel="Любой"
        />
        <FilterSelect
          label="Алерты"
          value={filterAlerts}
          onChange={v => { setFilterAlerts(v); setPage(0); }}
          options={[{ value: 'with', label: 'Только с алертами' }]}
          allLabel="Все"
        />
      </div>

      {/* Счётчик */}
      <div style={{ fontSize: 12, color: '#6b8578', marginBottom: 8 }}>
        {processed.length} из {fields?.length || 0} полей
        {processed.length !== (fields?.length || 0) && ` (отфильтровано)`}
      </div>

      {/* Таблица с фиксированной высотой и скроллом */}
      {loading ? (
        <TableSkeleton />
      ) : pageItems.length === 0 ? (
        <div style={{
          textAlign: 'center',
          padding: '40px 20px',
          color: '#6b8578',
          fontSize: 14,
        }}>
          Поля не найдены
        </div>
      ) : (
        <div style={{
          width: '100%',
          overflowX: 'auto',
          overflowY: 'auto',
          maxHeight: 'calc(100vh - 400px)',
          borderRadius: 8,
          border: '1px solid #e0e7e3',
        }}>
          <table style={{
            width: '100%',
            borderCollapse: 'collapse',
            tableLayout: 'fixed',
            fontSize: 12,
            fontFamily: "'Inter', 'Segoe UI', system-ui, sans-serif",
          }}>
            <colgroup>
              <col style={{ width: '30%' }} />   {/* Контур */}
              <col style={{ width: '22%' }} />   {/* Культура */}
              <col style={{ width: '16%' }} />   {/* Площадь (га) */}
              <col style={{ width: '16%' }} />   {/* NDVI */}
              <col style={{ width: '16%' }} />   {/* Предупреждения */}
            </colgroup>
            <thead style={{
              position: 'sticky',
              top: 0,
              zIndex: 1,
            }}>
              <tr>
                {columnHeaders.map(ch => (
                  <th
                    key={ch.key}
                    onClick={() => handleSort(ch.key)}
                    style={{
                      ...thStyle,
                      textAlign: ch.align || 'left',
                      width: ch.width || 'auto',
                    }}
                  >
                    {ch.label}
                    <SortIcon active={sortKey === ch.key} dir={sortDir} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pageItems.map((field, idx) => (
                <FieldRow
                  key={field.id}
                  field={field}
                  onViewGraph={onViewGraph}
                  style={{
                    background: idx % 2 === 0 ? '#f1f5f3' : '#f8faf9',
                  }}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Пагинация */}
      {totalPages > 1 && (
        <div style={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          gap: 8,
          marginTop: 14,
          fontSize: 13,
          color: '#6b8578',
        }}>
          <button
            disabled={clampedPage === 0}
            onClick={() => setPage(p => Math.max(0, p - 1))}
            style={{
              background: clampedPage === 0 ? 'transparent' : '#f1f5f3',
              border: '1px solid #e0e7e3',
              borderRadius: 6,
              padding: '4px 12px',
              color: clampedPage === 0 ? '#a3b5a9' : '#1a2e23',
              cursor: clampedPage === 0 ? 'default' : 'pointer',
              fontSize: 12,
            }}
          >
            ← Назад
          </button>
          <span>
            {clampedPage + 1} из {totalPages}
          </span>
          <button
            disabled={clampedPage >= totalPages - 1}
            onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
            style={{
              background: clampedPage >= totalPages - 1 ? 'transparent' : '#f1f5f3',
              border: '1px solid #e0e7e3',
              borderRadius: 6,
              padding: '4px 12px',
              color: clampedPage >= totalPages - 1 ? '#a3b5a9' : '#1a2e23',
              cursor: clampedPage >= totalPages - 1 ? 'default' : 'pointer',
              fontSize: 12,
            }}
          >
            Вперёд →
          </button>
        </div>
      )}
    </div>
  );
}
