import { finiteInteger, formatDate } from './inspectionPresentation';

const cards = [
  ['total', 'Всего осмотров'],
  ['pending', 'Ожидают начала'],
  ['in_progress', 'В работе'],
  ['completed', 'Завершены'],
  ['cancelled', 'Отменены'],
  ['overdue', 'Просрочены'],
];

export default function InspectionSummaryCards({ data }) {
  const summary = data?.summary || {};
  const count = Array.isArray(data?.items) ? data.items.length : 0;
  const offset = finiteInteger(data?.offset, 0);
  const limit = finiteInteger(data?.limit, 0);
  return (
    <section className="space-y-3" aria-label="Сводка осмотров">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {cards.map(([key, label]) => (
          <div key={key} className="card p-4">
            <p className="text-sm text-agro-muted">{label}</p>
            <p className="mt-1 text-2xl font-bold tabular-nums text-agro-text">
              {Object.prototype.hasOwnProperty.call(summary, key) ? finiteInteger(summary[key], 0) : '—'}
            </p>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-agro-muted">
        <span>Сформировано: {formatDate(data?.generated_at, true)}</span>
        <span>Лимит: {limit}</span><span>Смещение: {offset}</span><span>Показано: {count}</span>
      </div>
      {finiteInteger(summary.total, 0) > offset + count && (
        <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">Показана только часть результата.</p>
      )}
    </section>
  );
}
