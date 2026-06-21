export default function Header({ title, subtitle, currentView, alertCount }) {
  // Hidden entirely on map page
  if (currentView === 'fields' || currentView === 'field-detail') return null;

  return (
    <header className="absolute top-0 left-14 right-0 z-20 pointer-events-none">
      <div className="mx-3 mt-2 px-4 py-2 bg-white/80 backdrop-blur-sm rounded-b-lg border-x border-b border-agro-border flex items-center justify-between pointer-events-auto">
        <div>
          <h1 className="text-sm font-semibold text-agro-text">{title}</h1>
          {subtitle && <p className="text-[11px] text-agro-muted">{subtitle}</p>}
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
