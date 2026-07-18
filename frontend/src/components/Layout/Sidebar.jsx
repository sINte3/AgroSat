import { useEffect } from 'react';
import { useAuth } from '../../context/AuthContext';
import {
  getNavigationKeysForRole,
  getNavigationLabelForRole,
} from '../../config/roleAccess';

const NAV_ITEMS = [
  { key: 'dashboard', icon: DashboardIcon },
  { key: 'fields', icon: MapIcon },
  { key: 'field-attention', icon: AttentionIcon },
  { key: 'field-inspections', icon: InspectionIcon },
  { key: 'alerts', icon: BellIcon },
  { key: 'enterprises', icon: BuildingIcon },
  { key: 'reports', icon: ReportIcon },
];

const NAV_ITEMS_BY_KEY = new Map(NAV_ITEMS.map(item => [item.key, item]));

const ROLE_LABELS = { admin: 'Администратор', manager: 'Менеджер', agronomist: 'Агроном', viewer: 'Только просмотр' };

export default function Sidebar({ activeView, onNavigate, mobileOpen, onMobileClose }) {
  const { user, logout } = useAuth();
  const profileName = user?.name || user?.full_name || user?.username || 'Профиль';
  const roleLabel = ROLE_LABELS[user?.role] || 'Профиль';
  const navigationItems = getNavigationKeysForRole(user?.role)
    .map(key => NAV_ITEMS_BY_KEY.get(key))
    .filter(Boolean);

  useEffect(() => {
    if (!mobileOpen) return undefined;
    const handleKeyDown = event => {
      if (event.key === 'Escape') onMobileClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [mobileOpen, onMobileClose]);
  const isActive = (key) => {
    if (activeView === 'field-detail') return key === 'fields';
    if (activeView === 'enterprise-detail') return key === 'enterprises';
    return activeView === key;
  };

  return (
    <>
      {mobileOpen && (
        <button type="button" className="fixed inset-0 z-30 bg-slate-950/40 md:hidden" aria-label="Закрыть навигацию" onClick={onMobileClose} />
      )}
    <aside className={`${mobileOpen ? 'translate-x-0' : '-translate-x-full'} fixed inset-y-0 left-0 z-40 flex w-72 flex-shrink-0 flex-col border-r border-agro-border bg-white transition-transform md:static md:h-screen md:w-64 md:translate-x-0`}>
      {/* Logo */}
      <div className="flex h-16 items-center gap-3 border-b border-agro-border px-5">
        <div className="w-7 h-7 rounded-full bg-agro-accent flex items-center justify-center">
          <span className="text-white font-bold text-xs">A</span>
        </div>
        <div className="min-w-0 flex-1"><p className="text-base font-bold text-agro-text">AgroSat</p><p className="text-xs text-agro-muted">Мониторинг полей</p></div>
        <button type="button" onClick={onMobileClose} aria-label="Закрыть навигацию" className="rounded-lg p-2 text-agro-muted hover:bg-agro-hover focus:outline-none focus:ring-2 focus:ring-agro-accent md:hidden">×</button>
      </div>

      {/* Navigation */}
      <nav className="flex flex-1 flex-col items-stretch gap-1 overflow-y-auto px-3 py-5" aria-label="Основная навигация">
        {navigationItems.map((item) => {
          const active = isActive(item.key);
          return (
            <div key={item.key} className="relative">
              <button
                onClick={() => onNavigate(item.key)}
                aria-current={active ? 'page' : undefined}
                className={`flex min-h-11 w-full items-center justify-start gap-3 rounded-xl px-3 text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-agro-accent
                  ${active ? 'bg-emerald-50 text-agro-accent' : 'text-slate-600 hover:text-agro-text hover:bg-agro-hover'}`}
              >
                <item.icon className="w-5 h-5" />
                <span className="truncate">{getNavigationLabelForRole(user?.role, item.key)}</span>
              </button>
            </div>
          );
        })}
      </nav>

      {/* Bottom */}
      <div className="flex items-center gap-3 border-t border-agro-border p-4">
        <div className="relative group">
          <div className="w-7 h-7 rounded-full bg-agro-card flex items-center justify-center text-agro-muted text-xs">
            U
          </div>
        </div>
        <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium text-agro-text">{profileName}</p><p className="text-xs text-agro-muted">{roleLabel}</p></div>
        <button type="button" onClick={logout} className="rounded-lg px-2 py-1 text-xs font-medium text-agro-muted hover:bg-agro-hover hover:text-agro-text focus:outline-none focus:ring-2 focus:ring-agro-accent">Выйти</button>
      </div>
    </aside>
    </>
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
