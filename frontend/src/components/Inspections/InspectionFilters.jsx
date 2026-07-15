const controlClass = 'input mt-1 w-full min-w-0 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-agro-accent';

export default function InspectionFilters({
  draft, onChange, onApply, onReset, onRefresh, enterprises, assignees, role, loading,
}) {
  const globalRole = role === 'admin' || role === 'manager';
  return (
    <form onSubmit={onApply} className="card space-y-3 p-4" aria-label="Фильтры осмотров">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-6">
        {globalRole ? (
          <label className="text-xs font-medium text-agro-muted">Предприятие
            <select className={controlClass} value={draft.enterpriseId} onChange={(event) => onChange('enterpriseId', event.target.value)}>
              <option value="">Все предприятия</option>
              {enterprises.map((enterprise) => enterprise?.id && enterprise?.name ? <option key={enterprise.id} value={enterprise.id}>{enterprise.name}</option> : null)}
            </select>
          </label>
        ) : <div className="self-end rounded-lg bg-agro-surface2 p-3 text-xs text-agro-muted">Осмотры ограничены вашим предприятием.</div>}
        <label className="text-xs font-medium text-agro-muted">Статус
          <select className={controlClass} value={draft.status} onChange={(event) => onChange('status', event.target.value)}>
            <option value="">Все статусы</option><option value="pending">Ожидает</option><option value="in_progress">В работе</option><option value="completed">Завершён</option><option value="cancelled">Отменён</option>
          </select>
        </label>
        <label className="text-xs font-medium text-agro-muted">Исполнитель
          <select className={controlClass} value={draft.assignedToId} onChange={(event) => onChange('assignedToId', event.target.value)}>
            <option value="">{role === 'agronomist' ? 'Мои задания' : 'Все исполнители'}</option>
            {assignees.map((assignee) => <option key={assignee.id} value={assignee.id}>{assignee.name}</option>)}
          </select>
          {!assignees.length && <span className="mt-1 block font-normal">Исполнители появятся после загрузки назначенных осмотров.</span>}
        </label>
        <label className="flex items-center gap-2 self-end py-2 text-sm text-agro-text"><input type="checkbox" checked={draft.overdueOnly} onChange={(event) => onChange('overdueOnly', event.target.checked)} />Только просроченные</label>
        <label className="text-xs font-medium text-agro-muted">Срок до<input type="date" className={controlClass} value={draft.dueBefore} onChange={(event) => onChange('dueBefore', event.target.value)} /></label>
        <label className="text-xs font-medium text-agro-muted">Создано после<input type="datetime-local" className={controlClass} value={draft.createdAfter} onChange={(event) => onChange('createdAfter', event.target.value)} /></label>
      </div>
      <div className="flex flex-wrap gap-2">
        <button disabled={loading} className="btn-primary rounded-lg px-4 py-2 text-sm">Применить</button>
        <button type="button" disabled={loading} onClick={onReset} className="btn-secondary rounded-lg px-4 py-2 text-sm">Сбросить</button>
        <button type="button" disabled={loading} onClick={onRefresh} className="btn-secondary rounded-lg px-4 py-2 text-sm">Обновить</button>
      </div>
    </form>
  );
}
