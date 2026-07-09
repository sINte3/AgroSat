import { COVERAGE_STATUS_CONFIG, FRESHNESS_STATUS_CONFIG, SATELLITE_INDEX_CODES } from '../../config/indexMetadata';

const MODE_INFO_LABELS = {
  crop: { label: 'Культура', format: (p) => p.current_crop || '—' },
  ndvi: { label: 'NDVI', format: (p) => p.last_ndvi != null ? p.last_ndvi.toFixed(4) : 'Нет данных' },
  savi: { label: 'SAVI', format: (p) => p.savi_value != null ? p.savi_value.toFixed(4) : 'Нет данных' },
  evi: { label: 'EVI', format: (p) => p.evi_value != null ? p.evi_value.toFixed(4) : 'Нет данных' },
  ndmi: { label: 'NDMI', format: (p) => p.ndmi_value != null ? p.ndmi_value.toFixed(4) : 'Нет данных' },
  ndre: { label: 'NDRE', format: (p) => p.ndre_value != null ? p.ndre_value.toFixed(4) : 'Нет данных' },
  coverage: { label: 'Покрытие', format: (p) => {
    const cfg = COVERAGE_STATUS_CONFIG[p.coverage_status];
    return cfg ? cfg.label : 'Нет данных';
  }},
  freshness: { label: 'Актуальность', format: (p) => {
    const cfg = FRESHNESS_STATUS_CONFIG[p.freshness_status];
    return cfg ? cfg.label : 'Нет данных';
  }},
};

export default function MapHoverPopup({ feature, position, mapMode, onClose }) {
  if (!feature || !position) return null;

  const modeInfo = MODE_INFO_LABELS[mapMode] || MODE_INFO_LABELS.crop;
  const modeValue = modeInfo.format(feature);

  // Get date for current mode
  let dateStr = null;
  switch (mapMode) {
    case 'ndvi':
      dateStr = feature.last_ndvi_date || null;
      break;
    case 'savi':
      dateStr = feature.savi_date || null;
      break;
    case 'evi':
      dateStr = feature.evi_date || null;
      break;
    case 'ndmi':
      dateStr = feature.ndmi_date || null;
      break;
    case 'ndre':
      dateStr = feature.ndre_date || null;
      break;
    case 'coverage':
    case 'freshness':
      dateStr = feature.coverage_latest_date || null;
      break;
    default:
      dateStr = null;
  }

  const covCfg = feature.coverage_status ? COVERAGE_STATUS_CONFIG[feature.coverage_status] : null;
  const freshCfg = feature.freshness_status ? FRESHNESS_STATUS_CONFIG[feature.freshness_status] : null;

  const popupLeft = Math.min(position.x + 12, window.innerWidth - 240);
  const popupTop = Math.min(position.y - 10, window.innerHeight - 300);

  return (
    <div
      className="fixed z-50 bg-white/95 backdrop-blur-sm rounded-lg shadow-xl border border-slate-200 text-xs w-56 overflow-hidden pointer-events-auto"
      style={{ left: popupLeft, top: popupTop }}
      onMouseEnter={() => {}}
    >
      {/* Header */}
      <div className="px-3 py-2 bg-gray-50 border-b border-gray-100">
        <div className="text-sm font-semibold text-gray-900 truncate">{feature.name || `Поле #${feature.id}`}</div>
        <div className="text-[10px] text-gray-400 mt-0.5">
          {feature.area_ha != null ? `${Number(feature.area_ha).toFixed(1)} га` : ''}
          {feature.current_crop ? ` · ${feature.current_crop}` : ''}
        </div>
      </div>

      {/* Mode value */}
      <div className="px-3 py-2">
        <div className="flex items-center justify-between">
          <span className="text-gray-500 font-medium">{modeInfo.label}:</span>
          <span className="font-mono font-semibold text-gray-800">{modeValue}</span>
        </div>
        {dateStr && (
          <div className="flex items-center justify-between mt-1">
            <span className="text-gray-500">Дата:</span>
            <span className="text-gray-600">{new Date(dateStr).toLocaleDateString('ru-RU')}</span>
          </div>
        )}
      </div>

      {/* Coverage & freshness */}
      <div className="px-3 py-1.5 border-t border-gray-100 bg-gray-50/50">
        <div className="flex items-center gap-2">
          {covCfg && (
            <span
              className="text-[10px] font-medium px-1.5 py-0.5 rounded-full"
              style={{ backgroundColor: covCfg.bgColor, color: covCfg.textColor }}
            >
              {covCfg.shortLabel}
            </span>
          )}
          {freshCfg && feature.freshness_status !== 'no_data' && feature.freshness_status !== 'future_date' && (
            <span
              className="text-[10px] font-medium px-1.5 py-0.5 rounded-full"
              style={{ backgroundColor: freshCfg.bgColor, color: freshCfg.textColor }}
            >
              {freshCfg.shortLabel}
            </span>
          )}
        </div>
      </div>

      {/* Index chips */}
      <div className="px-3 py-1.5 border-t border-gray-100 bg-gray-50/50">
        <div className="flex flex-wrap gap-1">
          {SATELLITE_INDEX_CODES.map(code => {
            const hasData = feature[`${code}_has_data`];
            return (
              <span
                key={code}
                className={`text-[9px] font-mono font-semibold px-1 py-0.5 rounded ${
                  hasData
                    ? 'bg-green-50 text-green-700 border border-green-200'
                    : 'bg-gray-50 text-gray-400 border border-gray-200'
                }`}
              >
                {code.toUpperCase()}
              </span>
            );
          })}
        </div>
      </div>

      {/* Footer */}
      <div className="px-3 py-1 border-t border-gray-100 text-[9px] text-gray-400 italic">
        Цвет показывает относительное значение индекса, не является диагнозом урожайности.
      </div>
    </div>
  );
}
