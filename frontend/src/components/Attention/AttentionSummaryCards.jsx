const CARD_FIELDS = [
  ['critical', 'Критические'],
  ['high', 'Высокий приоритет'],
  ['medium', 'Средний приоритет'],
  ['attention_fields', 'Требуют внимания'],
  ['fields_evaluated', 'Поля в выборке'],
];

function safeCount(value) {
  return Number.isFinite(value) && value >= 0 ? value : 0;
}

function formatDate(value, withTime = false) {
  if (typeof value !== 'string' || !value) return '—';
  const date = new Date(withTime ? value : `${value}T12:00:00`);
  if (Number.isNaN(date.getTime())) return '—';
  return withTime ? date.toLocaleString('ru-RU') : date.toLocaleDateString('ru-RU');
}

export default function AttentionSummaryCards({ summary, generatedAt, dateTo, lookbackDays }) {
  const safeSummary = summary && typeof summary === 'object' ? summary : {};
  const returned = safeCount(safeSummary.returned);
  const attentionFields = safeCount(safeSummary.attention_fields);

  return (
    <section aria-label="Сводка очереди" className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">
        {CARD_FIELDS.map(([field, label]) => (
          <div key={field} className="card p-4 min-w-0">
            <p className="text-sm font-medium text-agro-muted">{label}</p>
            <p className="mt-1 text-2xl font-bold text-agro-text tabular-nums">{safeCount(safeSummary[field])}</p>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-agro-muted">
        <span>Показано полей: <b className="text-agro-text">{returned}</b></span>
        <span>Сформировано: <b className="text-agro-text">{formatDate(generatedAt, true)}</b></span>
        <span>Снимки не позднее: <b className="text-agro-text">{formatDate(dateTo)}</b></span>
        <span>Период анализа: <b className="text-agro-text">{safeCount(lookbackDays)} дн.</b></span>
      </div>
      {attentionFields > returned && (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          Показана часть очереди. Уточните фильтры или уровень приоритета.
        </p>
      )}
    </section>
  );
}
