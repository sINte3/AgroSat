// ─── Map Legend Component ─────────────────────────────────────────────────────
// Explains the active color mode (crop / index / coverage / freshness).

import { COVERAGE_STATUS_CONFIG, FRESHNESS_STATUS_CONFIG } from '../../config/indexMetadata';

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
  { color: '#16a34a', label: 'Высокое значение', note: '≥ 0.6' },
  { color: '#84cc16', label: 'Хорошее значение', note: '0.45–0.6' },
  { color: '#eab308', label: 'Среднее значение', note: '0.3–0.45' },
  { color: '#f97316', label: 'Низкое значение', note: '0.15–0.3' },
  { color: '#dc2626', label: 'Критическое значение', note: '< 0.15' },
  { color: '#4b5563', label: 'Нет данных', note: 'спутник не зафиксирован' },
];

const SAVI_LEGEND = [
  { color: '#16a34a', label: 'Высокое значение', note: '≥ 0.4' },
  { color: '#84cc16', label: 'Хорошее значение', note: '0.3–0.4' },
  { color: '#eab308', label: 'Среднее значение', note: '0.2–0.3' },
  { color: '#f97316', label: 'Низкое значение', note: '0.1–0.2' },
  { color: '#dc2626', label: 'Критическое значение', note: '< 0.1' },
  { color: '#4b5563', label: 'Нет данных', note: 'спутник не зафиксирован' },
];

const EVI_LEGEND = [
  { color: '#16a34a', label: 'Высокое значение', note: '≥ 0.4' },
  { color: '#84cc16', label: 'Хорошее значение', note: '0.3–0.4' },
  { color: '#eab308', label: 'Среднее значение', note: '0.2–0.3' },
  { color: '#f97316', label: 'Низкое значение', note: '0.1–0.2' },
  { color: '#dc2626', label: 'Критическое значение', note: '< 0.1' },
  { color: '#4b5563', label: 'Нет данных', note: 'спутник не зафиксирован' },
];

const NDMI_LEGEND = [
  { color: '#2563eb', label: 'Очень сухо', note: '< -0.2' },
  { color: '#60a5fa', label: 'Сухо', note: '-0.2 – 0.0' },
  { color: '#f59e0b', label: 'Умеренная влажность', note: '0.0 – 0.1' },
  { color: '#84cc16', label: 'Хорошая влажность', note: '0.1 – 0.3' },
  { color: '#16a34a', label: 'Высокая влажность', note: '≥ 0.3' },
  { color: '#4b5563', label: 'Нет данных', note: 'спутник не зафиксирован' },
];

const NDRE_LEGEND = [
  { color: '#16a34a', label: 'Высокий сигнал', note: '≥ 0.35' },
  { color: '#84cc16', label: 'Хороший сигнал', note: '0.2 – 0.35' },
  { color: '#eab308', label: 'Умеренный сигнал', note: '0.1 – 0.2' },
  { color: '#f97316', label: 'Низкий сигнал', note: '< 0.1' },
  { color: '#4b5563', label: 'Нет данных', note: 'спутник не зафиксирован' },
];

const COVERAGE_LEGEND = [
  { color: COVERAGE_STATUS_CONFIG.complete.color, label: COVERAGE_STATUS_CONFIG.complete.label },
  { color: COVERAGE_STATUS_CONFIG.partial.color, label: COVERAGE_STATUS_CONFIG.partial.label },
  { color: COVERAGE_STATUS_CONFIG.none.color, label: COVERAGE_STATUS_CONFIG.none.label },
];

const FRESHNESS_LEGEND = [
  { color: FRESHNESS_STATUS_CONFIG.fresh.color, label: FRESHNESS_STATUS_CONFIG.fresh.label },
  { color: FRESHNESS_STATUS_CONFIG.stale.color, label: FRESHNESS_STATUS_CONFIG.stale.label },
  { color: FRESHNESS_STATUS_CONFIG.future_date.color, label: FRESHNESS_STATUS_CONFIG.future_date.label },
  { color: FRESHNESS_STATUS_CONFIG.no_data.color, label: FRESHNESS_STATUS_CONFIG.no_data.label },
];

const STATUS_LEGEND = [
  { color: '#16a34a', label: 'Хороший' },
  { color: '#eab308', label: 'Умеренный' },
  { color: '#f97316', label: 'Риск / требуется внимание' },
  { color: '#9ca3af', label: 'Нет данных' },
];

const MODE_LEGENDS = {
  crop: { items: CROP_LEGEND, title: 'Легенда: Культуры' },
  ndvi: { items: NDVI_LEGEND, title: 'Легенда: NDVI' },
  savi: { items: SAVI_LEGEND, title: 'Легенда: SAVI' },
  evi: { items: EVI_LEGEND, title: 'Легенда: EVI' },
  ndmi: { items: NDMI_LEGEND, title: 'Легенда: NDMI' },
  ndre: { items: NDRE_LEGEND, title: 'Легенда: NDRE' },
  coverage: { items: COVERAGE_LEGEND, title: 'Легенда: Покрытие' },
  freshness: { items: FRESHNESS_LEGEND, title: 'Легенда: Актуальность' },
};

const LEGEND_DISCLAIMERS = {
  ndvi: 'Цвет показывает относительное значение NDVI, не является диагнозом урожайности.',
  savi: 'Цвет показывает относительное значение SAVI, не является диагнозом урожайности.',
  evi: 'Цвет показывает относительное значение EVI, не является диагнозом урожайности.',
  ndmi: 'Отрицательные значения NDMI допустимы и указывают на сухую поверхность.',
  ndre: 'Цвет показывает относительное значение NDRE, не является диагнозом урожайности.',
};

export default function MapLegend({ activeColorMode, rasterMetadata, onClose }) {
  const legend = MODE_LEGENDS[activeColorMode] || MODE_LEGENDS.crop;
  const disclaimer = LEGEND_DISCLAIMERS[activeColorMode];

  return (
    <div className="bg-white/95 backdrop-blur-sm rounded-lg shadow-lg border border-slate-200 text-xs w-56 max-h-96 overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100">
        <span className="font-semibold text-gray-700">{legend.title}</span>
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
        {legend.items.map(item => (
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

      {/* Status legend — shown always */}
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

      {/* Mode-specific disclaimer */}
      {disclaimer && (
        <div className="border-t border-gray-100 px-3 py-2 text-[10px] text-gray-400 leading-relaxed">
          {disclaimer}
        </div>
      )}

      {rasterMetadata?.legend?.length > 0 && (
        <div className="border-t border-gray-100 px-3 py-2">
          <div className="mb-1.5 text-xs font-semibold text-gray-700">Пиксельный NDVI</div>
          <div className="space-y-1.5">
            {rasterMetadata.legend.map((item, index) => (
              <div key={`${item.label}-${index}`} className="flex items-center gap-2">
                <span className="h-3 w-5 flex-shrink-0 rounded-sm" style={{ backgroundColor: item.color }} />
                <span className="min-w-0 text-gray-600">{item.from ?? '−∞'}–{item.to}: {item.label}</span>
              </div>
            ))}
          </div>
          {rasterMetadata.limitations?.map((limitation, index) => <p key={index} className="mt-1 text-[10px] leading-relaxed text-gray-400">{limitation}</p>)}
        </div>
      )}
    </div>
  );
}
