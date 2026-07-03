const NAV_ITEMS = [
  { key: 'fields',      label: 'Карта',         icon: MapIcon },
  { key: 'dashboard',   label: 'Обзор',          icon: DashboardIcon },
  { key: 'alerts',      label: 'Алерты',         icon: BellIcon },
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
    <aside className="w-14 flex flex-col bg-white border-r border-agro-border flex-shrink-0 z-30">
      {/* Logo */}
      <div className="flex items-center justify-center h-12 border-b border-agro-border">
        <div className="w-7 h-7 rounded-full bg-agro-accent flex items-center justify-center">
          <span className="text-white font-bold text-xs">A</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 flex flex-col items-center gap-1 py-3">
        {NAV_ITEMS.map((item) => {
          const active = isActive(item.key);
          return (
            <div key={item.key} className="relative group">
              <button
                onClick={() => onNavigate(item.key)}
                className={`w-10 h-10 flex items-center justify-center rounded-lg transition-colors
                  ${active ? 'text-agro-accent' : 'text-agro-muted hover:text-agro-text hover:bg-agro-hover'}`}
              >
                <item.icon className="w-5 h-5" />
              </button>
              {/* Active indicator bar */}
              {active && <div className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 bg-agro-accent rounded-r" />}
              {/* Tooltip */}
              <div className="absolute left-full ml-2 px-2 py-1 bg-agro-text text-white text-xs rounded opacity-0 group-hover:opacity-100 pointer-events-none whitespace-nowrap z-50 shadow-lg">
                {item.label}
              </div>
            </div>
          );
        })}
      </nav>

      {/* Bottom */}
      <div className="flex items-center justify-center h-12 border-t border-agro-border">
        <div className="relative group">
          <div className="w-7 h-7 rounded-full bg-agro-card flex items-center justify-center text-agro-muted text-xs">
            U
          </div>
          <div className="absolute left-full ml-2 px-2 py-1 bg-agro-text text-white text-xs rounded opacity-0 group-hover:opacity-100 pointer-events-none whitespace-nowrap z-50 shadow-lg">
            Пользователь
          </div>
        </div>
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
