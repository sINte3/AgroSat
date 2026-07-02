export default function SummaryCards({ summary, loading }) {
  if (loading) {
    return (
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="card animate-pulse">
            <div className="h-4 bg-agro-surface2 rounded w-24 mb-3" />
            <div className="h-8 bg-agro-surface2 rounded w-16" />
          </div>
        ))}
      </div>
    );
  }

  if (!summary) return null;

  const cards = [
    {
      label: 'Всего полей',
      value: summary.total_fields,
      icon: FieldIcon,
      color: 'text-agro-accent',
    },
    {
      label: 'Всего гектаров',
      value: summary.total_area_ha != null
        ? Number(summary.total_area_ha).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' га'
        : '—',
      icon: AreaIcon,
      color: 'text-agro-accent',
    },
    {
      label: 'Активные алерты',
      value: summary.active_alerts,
      icon: AlertIcon,
      color: summary.critical_alerts > 0 ? 'text-agro-danger' : 'text-agro-warning',
    },
    {
      label: 'Критические',
      value: summary.critical_alerts,
      icon: CriticalIcon,
      color: 'text-agro-danger',
    },
    {
      label: 'Высокий риск',
      value: summary.warning_alerts,
      icon: WarningIcon,
      color: 'text-agro-warning',
    },
    {
      label: 'Полей без данных',
      value: summary.fields_no_data,
      icon: NoDataIcon,
      color: 'text-agro-muted',
    },
    {
      label: 'Средний NDVI',
      value: summary.avg_ndvi != null ? summary.avg_ndvi.toFixed(3) : '—',
      icon: NDVIIcon,
      color: getNDVIColor(summary.avg_ndvi),
    },
  ];

  return (
    <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
      {cards.map((card, idx) => (
        <div key={idx} className="card flex items-center gap-4">
          <div className={`p-2.5 rounded-lg bg-agro-surface2 ${card.color}`}>
            <card.icon className="w-6 h-6" />
          </div>
          <div>
            <p className="text-sm text-agro-muted">{card.label}</p>
            <p className={`text-2xl font-bold ${card.color}`}>{card.value}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

function FieldIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5z"
      />
    </svg>
  );
}

function AreaIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4"
      />
    </svg>
  );
}

function AlertIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
      />
    </svg>
  );
}

function CriticalIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
      />
    </svg>
  );
}

function WarningIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M12 9v2m0 4h.01M10.29 3.86l-8.1 14c-.6 1.04.15 2.14 1.21 2.14h17.2c1.06 0 1.81-1.1 1.21-2.14l-8.1-14c-.6-1.04-2.12-1.04-2.72 0z"
      />
    </svg>
  );
}

function NoDataIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M18.364 5.636a9 9 0 11-12.728 0m12.728 0a9 9 0 00-12.728 0"
      />
    </svg>
  );
}

function NDVIIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z"
      />
    </svg>
  );
}

function getNDVIColor(ndvi) {
  if (ndvi == null) return 'text-agro-muted';
  if (ndvi >= 0.6) return 'text-agro-accent';
  if (ndvi >= 0.3) return 'text-agro-warning';
  return 'text-agro-danger';
}
