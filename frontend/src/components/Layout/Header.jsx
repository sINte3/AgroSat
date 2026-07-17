export default function Header({ title, subtitle, currentView, alertCount }) {
  // Hidden entirely on map page
  if (currentView === 'fields' || currentView === 'field-detail') return null;

  return (
    <header className="absolute top-0 left-0 right-0 z-20 pointer-events-none md:left-64">
      <div className="mx-3 mt-2 flex min-h-12 items-center justify-between rounded-xl border border-agro-border bg-white/95 px-4 py-2 shadow-sm backdrop-blur-sm pointer-events-auto">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-agro-text md:text-2xl">{title}</h1>
          {subtitle && <p className="text-xs text-agro-muted">{subtitle}</p>}
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
