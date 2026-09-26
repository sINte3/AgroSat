import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import {
  getNavigationKeysForRole,
  getNavigationLabelForRole,
} from '../../config/roleAccess';
import { listOfflineDrafts, offlineScope } from '../../offline/offlineScoutingStore.js';

const NAV_ITEMS = [
  { key: 'dashboard', icon: DashboardIcon },
  { key: 'operational-center', icon: CommandCenterIcon },
  { key: 'management-analytics', icon: AnalyticsIcon },
  { key: 'fields', icon: MapIcon },
  { key: 'monitoring', icon: MonitoringIcon },
  { key: 'field-attention', icon: AttentionIcon },
  { key: 'field-inspections', icon: InspectionIcon },
  { key: 'agronomy-plans', icon: AgronomyIcon },
  { key: 'alerts', icon: BellIcon },
  { key: 'enterprises', icon: BuildingIcon },
  { key: 'reports', icon: ReportIcon },
];

const NAV_ITEMS_BY_KEY = new Map(NAV_ITEMS.map(item => [item.key, item]));

const ROLE_LABELS = { admin: 'Администратор', manager: 'Менеджер', agronomist: 'Агроном', viewer: 'Только просмотр' };

export default function Sidebar({ activeView, onNavigate, mobileOpen, onMobileClose }) {
  const { user, logout } = useAuth();
  const drawerRef = useRef(null);
  const closeButtonRef = useRef(null);
  const stayButtonRef = useRef(null);
  const mountedRef = useRef(true);
  const [logoutConfirmation, setLogoutConfirmation] = useState(null);
  const [loggingOut, setLoggingOut] = useState(false);
  const profileName = user?.name || user?.full_name || user?.username || 'Профиль';
  const roleLabel = ROLE_LABELS[user?.role] || 'Профиль';
  const navigationItems = getNavigationKeysForRole(user?.role)
    .map(key => NAV_ITEMS_BY_KEY.get(key))
    .filter(Boolean);

  useEffect(() => {
    if (!mobileOpen) return undefined;
    const previouslyFocused = document.activeElement;
    const focusFrame = requestAnimationFrame(() => closeButtonRef.current?.focus());
    const handleKeyDown = event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onMobileClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const drawer = drawerRef.current;
      if (!drawer) return;
      const focusable = Array.from(drawer.querySelectorAll(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ));
      if (focusable.length === 0) {
        event.preventDefault();
        drawer.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      cancelAnimationFrame(focusFrame);
      window.removeEventListener('keydown', handleKeyDown);
      if (previouslyFocused instanceof HTMLElement && document.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
    };
  }, [mobileOpen, onMobileClose]);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    if (logoutConfirmation) stayButtonRef.current?.focus();
  }, [logoutConfirmation]);

  const isActive = (key) => {
    if (activeView === 'field-detail') return key === 'fields';
    if (activeView === 'enterprise-detail') return key === 'enterprises';
    return activeView === key;
  };

  // An explicit logout removes this user's local offline partition (product
  // contract). Unsynchronized drafts are never removed without confirmation.
  const performLogout = async () => {
    setLoggingOut(true);
    try {
      await logout();
    } finally {
      if (mountedRef.current) {
        setLoggingOut(false);
        setLogoutConfirmation(null);
      }
    }
  };

  const requestLogout = async () => {
    if (loggingOut) return;
    const scope = offlineScope(user);
    let unsynchronized = 0;
    if (scope) {
      try {
        unsynchronized = (await listOfflineDrafts(scope)).length;
      } catch {
        unsynchronized = 0;
      }
    }
    if (!mountedRef.current) return;
    if (unsynchronized > 0) {
      setLogoutConfirmation({ count: unsynchronized });
      return;
    }
    await performLogout();
  };

  return (
    <>
      {mobileOpen && (
        <button type="button" className="fixed inset-0 z-30 bg-slate-950/40 md:hidden" aria-label="Закрыть навигацию" onClick={onMobileClose} />
      )}
    <aside
      ref={drawerRef}
      role={mobileOpen ? 'dialog' : undefined}
      aria-modal={mobileOpen ? 'true' : undefined}
      aria-label={mobileOpen ? 'Основная навигация' : undefined}
      tabIndex={mobileOpen ? -1 : undefined}
      className={`${mobileOpen ? 'visible translate-x-0' : 'invisible -translate-x-full md:visible'} fixed inset-y-0 left-0 z-40 flex w-72 flex-shrink-0 flex-col border-r border-agro-border bg-white transition-transform md:static md:h-screen md:w-64 md:translate-x-0`}
    >
      {/* Logo */}
      <div className="flex h-16 items-center gap-3 border-b border-agro-border px-5">
        <div className="w-7 h-7 rounded-full bg-agro-accent flex items-center justify-center">
          <span className="text-white font-bold text-xs">A</span>
        </div>
        <div className="min-w-0 flex-1"><p className="text-base font-bold text-agro-text">AgroSat</p><p className="text-xs text-agro-muted">Мониторинг полей</p></div>
        <button ref={closeButtonRef} type="button" onClick={onMobileClose} aria-label="Закрыть навигацию" className="inline-flex h-11 w-11 items-center justify-center rounded-lg text-agro-muted hover:bg-agro-hover focus:outline-none focus:ring-2 focus:ring-agro-accent md:hidden">×</button>
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
                <item.icon className="w-5 h-5 flex-none" />
                <span className="min-w-0 text-left leading-5">{getNavigationLabelForRole(user?.role, item.key)}</span>
              </button>
            </div>
          );
        })}
      </nav>

      {/* Bottom */}
      {logoutConfirmation && (
        <div role="alertdialog" aria-labelledby="logout-confirmation-title" aria-describedby="logout-confirmation-text" className="border-t border-amber-300 bg-amber-50 p-4 text-sm text-amber-950">
          <p id="logout-confirmation-title" className="font-semibold">Несинхронизированные черновики: {logoutConfirmation.count}</p>
          <p id="logout-confirmation-text" className="mt-1 leading-5">При выходе они будут удалены с этого устройства. Чтобы сохранить их, откройте «Осмотры» и отправьте черновики на сервер, затем выйдите.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button ref={stayButtonRef} type="button" onClick={() => setLogoutConfirmation(null)} disabled={loggingOut} className="btn-secondary min-h-11 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-agro-accent">Остаться</button>
            <button type="button" onClick={performLogout} disabled={loggingOut} className="min-h-11 rounded-lg border border-red-400 bg-white px-3 text-sm font-semibold text-red-900 hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-red-600 disabled:opacity-60">{loggingOut ? 'Выходим…' : 'Выйти и удалить'}</button>
          </div>
        </div>
      )}
      <div className="flex items-center gap-3 border-t border-agro-border p-4">
        <div className="relative group">
          <div className="w-7 h-7 rounded-full bg-agro-card flex items-center justify-center text-agro-muted text-xs">
            U
          </div>
        </div>
        <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium text-agro-text">{profileName}</p><p className="text-xs text-agro-muted">{roleLabel}</p></div>
        <button type="button" onClick={requestLogout} disabled={loggingOut} className="rounded-lg px-2 py-1 text-xs font-medium text-agro-muted hover:bg-agro-hover hover:text-agro-text focus:outline-none focus:ring-2 focus:ring-agro-accent disabled:opacity-60">Выйти</button>
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

function CommandCenterIcon({ className }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={className}>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 7v5l3 2M4 12h2m12 0h2M12 4v2m0 12v2" />
    </svg>
  );
}

function AnalyticsIcon({ className }) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={className}><path d="M4 20h16M6 16v-3m4 3V9m4 7v-5m4 5V6M5 9l4-3 4 3 6-5" /></svg>;
}

function BellIcon({ className }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={className}>
      <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 01-3.46 0"/>
    </svg>
  );
}

function MonitoringIcon({ className }) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={className}><path d="M4 17l4-5 4 3 5-8 3 2M4 21h16M6 5h12"/></svg>;
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

function AgronomyIcon({ className }) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={className}><path d="M4 5h16v14H4zM8 9h8M8 13h5M7 5V3m10 2V3M15 16l2 2 3-4"/></svg>;
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
