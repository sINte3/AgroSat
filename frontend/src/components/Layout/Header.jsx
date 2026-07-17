export default function Header({ title, subtitle, currentView, alertCount, mobileNavigationOpen, onMobileNavigationToggle }) {
  // Hidden entirely on map page
  if (currentView === 'fields' || currentView === 'field-detail') return null;

  return (
    <header className="absolute inset-x-0 top-0 z-20 pointer-events-none">
      <div className="mx-3 mt-2 flex min-h-12 items-center justify-between rounded-xl border border-agro-border bg-white/95 px-4 py-2 shadow-sm backdrop-blur-sm pointer-events-auto">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            className="inline-flex h-10 w-10 flex-none items-center justify-center rounded-lg border border-agro-border text-agro-text hover:bg-agro-hover focus:outline-none focus:ring-2 focus:ring-agro-accent md:hidden"
            aria-label="Открыть основную навигацию"
            aria-expanded={mobileNavigationOpen}
            onClick={onMobileNavigationToggle}
          >
            <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d="M4 7h16M4 12h16M4 17h16" />
            </svg>
          </button>
          <div className="min-w-0">
          <h1 className="text-xl font-bold tracking-tight text-agro-text md:text-2xl">{title}</h1>
          {subtitle && <p className="truncate text-xs text-agro-muted">{subtitle}</p>}
          </div>
        </div>
        {alertCount > 0 && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-700 font-medium">
            {alertCount} 🔔
          </span>
        )}
      </div>
    </header>
  );
}
