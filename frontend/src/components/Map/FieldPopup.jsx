export default function FieldPopup({ field, onClose, onViewDetails }) {
  if (!field) return null;

  return (
    <div className="bg-agro-surface border border-agro-surface2 rounded-lg shadow-xl p-4 max-w-xs">
      {/* Заголовок */}
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-agro-text text-sm truncate">{field.name}</h3>
        <button onClick={onClose} className="text-agro-muted hover:text-agro-text ml-2">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Информация */}
      <div className="space-y-2 text-xs">
        <div className="flex justify-between">
          <span className="text-agro-muted">Предприятие</span>
          <span className="text-agro-text">{field.properties.enterprise_name || '—'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-agro-muted">Площадь</span>
          <span className="text-agro-text">
            {field.properties.area_ha ? `${field.properties.area_ha.toFixed(1)} га` : '—'}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-agro-muted">Культура</span>
          <span className="text-agro-text">{field.properties.current_crop || '—'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-agro-muted">NDVI</span>
          <span className={`font-medium ${
            field.properties.current_ndvi >= 0.6 ? 'text-agro-accent'
            : field.properties.current_ndvi >= 0.3 ? 'text-agro-warning'
            : 'text-agro-danger'
          }`}>
            {field.properties.current_ndvi != null ? field.properties.current_ndvi.toFixed(4) : '—'}
          </span>
        </div>
        {field.properties.active_alerts > 0 && (
          <div className="flex justify-between">
            <span className="text-agro-muted">Алерты</span>
            <span className={`font-medium ${
              field.properties.max_severity === 'critical' ? 'text-agro-danger'
              : field.properties.max_severity === 'warning' ? 'text-agro-warning'
              : 'text-agro-info'
            }`}>
              {field.properties.active_alerts}
            </span>
          </div>
        )}
      </div>

      {/* Кнопка */}
      {onViewDetails && (
        <button
          onClick={() => onViewDetails(field.properties.id)}
          className="w-full mt-3 btn-primary text-xs py-1.5"
        >
          Подробнее о поле
        </button>
      )}
    </div>
  );
}
