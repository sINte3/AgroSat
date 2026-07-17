const LOOKBACK_OPTIONS = [30, 90, 180, 365];
const PRIORITY_OPTIONS = [
  ['medium', 'Средний и выше'],
  ['high', 'Высокий и критический'],
  ['critical', 'Только критический'],
  ['low', 'Все уровни'],
];

const controlClass = 'input w-full min-w-0 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-agro-accent';

export default function AttentionFilters({
  draft,
  onChange,
  onApply,
  onReset,
  enterprises,
  cropOptions,
  isGlobalRole,
  loading,
}) {
  const safeEnterprises = Array.isArray(enterprises) ? enterprises : [];
  const safeCrops = Array.isArray(cropOptions) ? cropOptions : [];

  return (
    <form onSubmit={onApply} className="card space-y-4 p-4" aria-label="Фильтры очереди внимания">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
        {isGlobalRole ? (
          <label className="block min-w-0 text-xs font-medium text-agro-muted">
            Предприятие
            <select value={draft.enterpriseId} onChange={(event) => onChange('enterpriseId', event.target.value)} className={`${controlClass} mt-1`}>
              <option value="">Все предприятия</option>
              {safeEnterprises.map((enterprise) => {
                const id = Number(enterprise?.id);
                const name = typeof enterprise?.name === 'string' && enterprise.name.trim() ? enterprise.name : null;
                return Number.isSafeInteger(id) && id > 0 && name ? <option key={id} value={id}>{name}</option> : null;
              })}
            </select>
          </label>
        ) : (
          <div className="self-end rounded-lg bg-agro-surface2 px-3 py-2 text-xs text-agro-muted">
            Очередь ограничена вашим предприятием.
          </div>
        )}

        <label className="block min-w-0 text-xs font-medium text-agro-muted">
          Культура
          <select disabled={!safeCrops.length} value={draft.cropTypeId} onChange={(event) => onChange('cropTypeId', event.target.value)} className={`${controlClass} mt-1 disabled:cursor-not-allowed disabled:opacity-60`}>
            <option value="">Все культуры</option>
            {safeCrops.map((crop) => <option key={crop.id} value={crop.id}>{crop.name}</option>)}
          </select>
          {!safeCrops.length && <span className="mt-1 block font-normal">Культуры появятся после загрузки очереди.</span>}
        </label>

        <label className="block min-w-0 text-xs font-medium text-agro-muted">
          Снимки не позднее
          <input type="date" required value={draft.dateTo} onChange={(event) => onChange('dateTo', event.target.value)} className={`${controlClass} mt-1`} />
        </label>

        <label className="block min-w-0 text-xs font-medium text-agro-muted">
          Период анализа
          <select value={draft.lookbackDays} onChange={(event) => onChange('lookbackDays', event.target.value)} className={`${controlClass} mt-1`}>
            {LOOKBACK_OPTIONS.map((days) => <option key={days} value={days}>{days} дней</option>)}
          </select>
        </label>

        <label className="block min-w-0 text-xs font-medium text-agro-muted">
          Минимальный приоритет
          <select value={draft.minPriority} onChange={(event) => onChange('minPriority', event.target.value)} className={`${controlClass} mt-1`}>
            {PRIORITY_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={loading} className="btn-primary rounded-lg px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-agro-accent disabled:opacity-60">Применить</button>
        <button type="button" onClick={onReset} disabled={loading} className="btn-secondary rounded-lg px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-agro-accent disabled:opacity-60">Сбросить</button>
      </div>
    </form>
  );
}
