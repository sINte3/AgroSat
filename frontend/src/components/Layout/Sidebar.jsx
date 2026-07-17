const NAV_ITEMS = [
  { key: 'dashboard',   label: 'Сегодня',        icon: DashboardIcon },
  { key: 'fields',      label: 'Поля',           icon: MapIcon },
  { key: 'field-attention', label: 'Внимание', icon: AttentionIcon },
  { key: 'field-inspections', label: 'Осмотры', icon: InspectionIcon },
  { key: 'alerts',      label: 'Предупреждения', icon: BellIcon },
  { key: 'enterprises', label: 'Предприятия',    icon: BuildingIcon },
  { key: 'reports',     label: 'Отчёты',         icon: ReportIcon },
];

export default function Sidebar({ activeView, onNavigate, enterprises = [] }) {
  const isActive = (key) => {
    if (activeView === 'field-detail') return key === 'fields';
    if (activeView === 'enterprise-detail') return key === 'enterprises';
    return activeView === key;
  };

  return (
    <aside className="fixed inset-x-0 bottom-0 z-30 flex h-16 flex-shrink-0 bg-white border-t border-agro-border md:static md:h-screen md:w-64 md:flex-col md:border-r md:border-t-0">
      {/* Logo */}
      <div className="hidden h-16 items-center gap-3 border-b border-agro-border px-5 md:flex">
        <div className="w-7 h-7 rounded-full bg-agro-accent flex items-center justify-center">
          <span className="text-white font-bold text-xs">A</span>
        </div>
        <div><p className="text-base font-bold text-agro-text">AgroSat</p><p className="text-xs text-agro-muted">Мониторинг полей</p></div>
      </div>

      {/* Navigation */}
      <nav className="flex flex-1 items-center gap-1 overflow-x-auto px-2 py-2 md:flex-col md:items-stretch md:overflow-visible md:px-3 md:py-5" aria-label="Основная навигация">
        {NAV_ITEMS.map((item) => {
          const active = isActive(item.key);
          return (
            <div key={item.key} className="relative min-w-14 flex-1 md:min-w-0 md:flex-none">
              <button
                onClick={() => onNavigate(item.key)}
                aria-current={active ? 'page' : undefined}
                className={`flex min-h-11 w-full flex-col items-center justify-center gap-1 rounded-xl px-2 text-[11px] font-medium transition-colors md:flex-row md:justify-start md:gap-3 md:px-3 md:text-sm
                  ${active ? 'bg-emerald-50 text-agro-accent' : 'text-slate-600 hover:text-agro-text hover:bg-agro-hover'}`}
              >
                <item.icon className="w-5 h-5" />
                <span className="truncate">{item.label}</span>
              </button>
            </div>
          );
        })}
      </nav>

      {/* Bottom */}
      <div className="hidden items-center gap-3 border-t border-agro-border p-4 md:flex">
        <div className="relative group">
          <div className="w-7 h-7 rounded-full bg-agro-card flex items-center justify-center text-agro-muted text-xs">
            U
          </div>
        </div>
        <div><p className="text-sm font-medium text-agro-text">Пользователь</p><p className="text-xs text-agro-muted">Рабочий профиль</p></div>
      </div>
    </aside>
  );
}

// ─── Inline SVG icons ─────────────────────────────────────────────────────

function MapIcon({ className }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={className}>
      <path d="M9 2L15 2M9 22L15 22M2 9L2 15M22 9L22 15M6 2H4a2 2 0 00-2 2v2M18 2h2a2 2 0 012 2v2M6 22H4a2 2 0 01-2-2v-2M18 22h2a2 2 0 002-2v-2"/>
    </svg>
  );
}

function DashboardIcon({ className }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={className}>
      <path d="M3 3v18h18M7 16v-4M12 16V8M17 16v-6"/>
    </svg>
  );
}

function BellIcon({ className }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={className}>
      <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 01-3.46 0"/>
    </svg>
  );
}

function AttentionIcon({ className }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={className}>
      <path d="M12 3l9 16H3L12 3zM12 9v4m0 3h.01" />
    </svg>
  );
}

function InspectionIcon({ className }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={className}><path d="M9 5h6m-6 4h6m-6 4h3M7 3h10a2 2 0 012 2v14H5V5a2 2 0 012-2zM9 19v2m6-2v2"/></svg>;
}

function ReportIcon({ className }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={className}>
      <path d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
    </svg>
  );
}

function BuildingIcon({ className }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={className}>
      <path d="M3 21h18M5 21V7l8-4v18M19 21V11l-6-4M9 9h1M9 13h1M9 17h1"/>
    </svg>
  );
}
