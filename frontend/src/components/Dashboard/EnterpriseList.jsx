export default function EnterpriseList({ enterprises, onEnterpriseClick }) {
  if (!enterprises || enterprises.length === 0) {
    return (
      <div className="card text-center py-6">
        <p className="text-agro-muted">Нет данных о предприятиях</p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3 className="font-semibold text-sm mb-3">Предприятия (сводка)</h3>
      <div className="space-y-2 max-h-64 overflow-y-auto">
        {enterprises.map((ent) => (
          <button
            key={ent.id}
            onClick={() => onEnterpriseClick?.(ent.id)}
            className="w-full flex items-center justify-between p-2.5 rounded-lg hover:bg-agro-surface2 transition-colors text-left"
          >
            <div className="min-w-0">
              <p className="text-sm font-medium text-agro-text truncate">{ent.name}</p>
              <p className="text-xs text-agro-muted">
                {ent.fields_count} полей · NDVI {ent.avg_ndvi?.toFixed(3) || '—'}
              </p>
            </div>
            <div className="flex items-center gap-3 flex-shrink-0 ml-3">
              {ent.active_alerts > 0 && (
                <span className={`text-xs font-medium ${
                  ent.critical_alerts > 0 ? 'text-agro-danger' : 'text-agro-warning'
                }`}>
                  {ent.active_alerts} ал.
                </span>
              )}
              <svg className="w-4 h-4 text-agro-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
