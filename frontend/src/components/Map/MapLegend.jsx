// ─── Map Legend Component ─────────────────────────────────────────────────────
// Explains the active color mode (crop / NDVI) and field status colors.
// Uses qualitative labels — no exact agronomic thresholds unless already in code.

const CROP_LEGEND = [
  { color: '#EAB308', label: 'Пшеница' },
  { color: '#60A5FA', label: 'Хлопок (капельн.)' },
  { color: '#F472B6', label: 'Хлопок (канальн.)' },
  { color: '#A78BFA', label: 'Люцерна' },
  { color: '#34D399', label: 'Рис' },
  { color: '#FB923C', label: 'Кукуруза' },
  { color: '#6B7280', label: 'Другое' },
];

const NDVI_LEGEND = [
  { color: '#16a34a', label: 'Высокое значение', note: '≥0.6' },
  { color: '#84cc16', label: 'Хорошее значение', note: '0.45–0.6' },
  { color: '#eab308', label: 'Среднее значение', note: '0.3–0.45' },
  { color: '#f97316', label: 'Низкое значение', note: '0.15–0.3' },
  { color: '#dc2626', label: 'Критическое значение', note: '<0.15' },
  { color: '#4b5563', label: 'Нет данных', note: 'спутник не зафиксирован' },
];

const STATUS_LEGEND = [
  { color: '#16a34a', label: 'Хороший' },
  { color: '#eab308', label: 'Умеренный' },
  { color: '#f97316', label: 'Риск / требуется внимание' },
  { color: '#9ca3af', label: 'Нет данных' },
];

export default function MapLegend({ activeColorMode, onClose }) {
  const legendItems = activeColorMode === 'crop' ? CROP_LEGEND : NDVI_LEGEND;
  const title = activeColorMode === 'crop' ? 'Легенда: Культуры' : 'Легенда: NDVI';

  return (
    <div className="bg-white/95 backdrop-blur-sm rounded-lg shadow-lg border border-slate-200 text-xs w-56 max-h-80 overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100">
        <span className="font-semibold text-gray-700">{title}</span>
        {onClose && (
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>

      {/* Legend items */}
      <div className="p-3 space-y-2">
        {legendItems.map(item => (
          <div key={item.label} className="flex items-center gap-2">
            <div
              className="w-5 h-3 rounded-sm flex-shrink-0"
              style={{ backgroundColor: item.color }}
            />
            <div className="flex-1 min-w-0">
              <div className="text-gray-700 font-medium">{item.label}</div>
              {item.note && (
                <div className="text-gray-400 text-[10px]">{item.note}</div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Status legend (always shown) */}
      <div className="border-t border-gray-100 px-3 py-2">
        <div className="text-xs font-semibold text-gray-700 mb-1.5">Статус поля</div>
        <div className="space-y-1.5">
          {STATUS_LEGEND.map(item => (
            <div key={item.label} className="flex items-center gap-2">
              <div
                className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                style={{ backgroundColor: item.color }}
              />
              <span className="text-gray-600">{item.label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Index note — shown always */}
      <div className="border-t border-gray-100 px-3 py-2 text-[10px] text-gray-400 leading-relaxed">
        Отрицательные значения индексов могут быть допустимыми.
      </div>
    </div>
  );
}
