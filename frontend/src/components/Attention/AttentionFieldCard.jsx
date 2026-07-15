const PRIORITY = {
  critical: { label: 'Критический', className: 'bg-red-100 text-red-700 border-red-200' },
  high: { label: 'Высокий', className: 'bg-orange-100 text-orange-700 border-orange-200' },
  medium: { label: 'Средний', className: 'bg-amber-100 text-amber-700 border-amber-200' },
  low: { label: 'Низкий', className: 'bg-blue-100 text-blue-700 border-blue-200' },
};
const FRESHNESS = { no_data: 'Нет данных', stale: 'Данные устарели', current: 'Данные актуальны' };
const CONFIDENCE = { high: 'Высокая', medium: 'Средняя', low: 'Низкая', insufficient: 'Недостаточно данных' };
const ALERT_LABEL = { critical: 'Критические', warning: 'Предупреждающие', info: 'Информационные' };
const ALERT_SEVERITY = { critical: 'Критический', warning: 'Предупреждающий', info: 'Информационный' };

function text(value, fallback = '—') {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function count(value) {
  return Number.isFinite(value) && value >= 0 ? value : 0;
}

function date(value, withTime = false) {
  if (typeof value !== 'string' || !value) return '—';
  const parsed = new Date(withTime ? value : `${value}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return '—';
  return withTime ? parsed.toLocaleString('ru-RU') : parsed.toLocaleDateString('ru-RU');
}

function stringList(value) {
  return Array.isArray(value) ? value.filter((item) => typeof item === 'string' && item.trim()).map((item) => item.trim()) : [];
}

export default function AttentionFieldCard({ item, onNavigate }) {
  const field = item?.field && typeof item.field === 'object' ? item.field : {};
  const fieldId = Number(field.id);
  const validFieldId = Number.isSafeInteger(fieldId) && fieldId > 0;
  const priority = PRIORITY[item?.priority] || { label: 'Не указан', className: 'bg-gray-100 text-gray-700 border-gray-200' };
  const score = Number.isFinite(item?.attention_score) && item.attention_score >= 0 && item.attention_score <= 100 ? item.attention_score : null;
  const alertSummary = item?.alert_summary && typeof item.alert_summary === 'object' ? item.alert_summary : {};
  const spectral = item?.spectral_summary && typeof item.spectral_summary === 'object' ? item.spectral_summary : {};
  const reasons = Array.isArray(item?.reasons) ? item.reasons.filter((reason) => reason && typeof reason === 'object') : [];
  const checks = stringList(item?.recommended_checks);
  const limitations = stringList(item?.limitations);
  const topAlerts = Array.isArray(alertSummary.top_alerts) ? alertSummary.top_alerts.filter((alert) => alert && typeof alert === 'object').slice(0, 3) : [];

  return (
    <article className="card p-4 md:p-5 space-y-4 min-w-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wide text-agro-muted">Место #{count(item?.rank)}</p>
          <h2 className="mt-1 break-words text-lg font-bold text-agro-text">{text(field.name, 'Поле без названия')}</h2>
          <p className="mt-1 text-xs text-agro-muted">ID поля: {validFieldId ? fieldId : '—'} · {text(field.enterprise_name)} · {text(field.crop_name)} · Сезон: {Number.isSafeInteger(field.season_year) ? field.season_year : '—'}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${priority.className}`}>{priority.label}</span>
          <span className="rounded-full bg-agro-surface2 px-2.5 py-1 text-sm font-bold text-agro-text">Индекс внимания: {score === null ? '—' : score}</span>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 text-sm">
        <div className="rounded-lg bg-agro-surface2 p-3"><p className="text-xs text-agro-muted">Последнее наблюдение</p><p className="mt-1 font-medium text-agro-text">{date(spectral.latest_observation_date)}</p></div>
        <div className="rounded-lg bg-agro-surface2 p-3"><p className="text-xs text-agro-muted">Статус NDVI</p><p className="mt-1 font-medium text-agro-text">{FRESHNESS[spectral.data_status] || 'Нет данных'}</p><p className="text-xs text-agro-muted">Свежесть NDVI: {Number.isFinite(spectral.freshness_days) && spectral.freshness_days >= 0 ? `${spectral.freshness_days} дн.` : 'нет данных'}</p></div>
        <div className="rounded-lg bg-agro-surface2 p-3"><p className="text-xs text-agro-muted">Общая уверенность</p><p className="mt-1 font-medium text-agro-text">{CONFIDENCE[spectral.overall_confidence] || 'Недостаточно данных'}</p></div>
        <div className="rounded-lg bg-agro-surface2 p-3"><p className="text-xs text-agro-muted">Активные алерты</p><p className="mt-1 font-medium text-agro-text">Всего: {count(alertSummary.active_total)}</p><p className="text-xs text-agro-muted">Последний: {date(alertSummary.latest_triggered_at, true)}</p></div>
      </div>

      <div className="flex flex-wrap gap-2 text-xs">
        {['critical', 'warning', 'info'].map((severity) => <span key={severity} className="rounded-full border border-agro-border px-2 py-1 text-agro-text">{ALERT_LABEL[severity]}: {count(alertSummary[severity])}</span>)}
      </div>

      {topAlerts.length > 0 && <section><h3 className="text-sm font-semibold text-agro-text">Основные алерты</h3><ul className="mt-2 space-y-1.5">{topAlerts.map((alert) => {
        const id = Number(alert.id);
        if (!Number.isSafeInteger(id) || id <= 0) return null;
        return <li key={id} className="rounded-lg border border-agro-border px-3 py-2 text-sm text-agro-text"><span className="font-medium">{text(alert.title, 'Алерт')}</span><span className="ml-2 text-xs text-agro-muted">{ALERT_SEVERITY[alert.severity] || 'Уровень не указан'} · {text(alert.type, 'Тип не указан')} · {date(alert.triggered_at, true)}</span></li>;
      })}</ul></section>}

      <section><h3 className="text-sm font-semibold text-agro-text">Сигналы ранжирования</h3>{reasons.length ? <ul className="mt-2 space-y-1.5">{reasons.map((reason) => {
        const indices = stringList(reason.indices);
        const code = text(reason.code, 'signal');
        const key = `${code}:${indices.join('-')}`;
        return <li key={key} className="flex flex-wrap justify-between gap-2 rounded-lg bg-agro-surface2 px-3 py-2 text-sm"><span className="text-agro-text">{text(reason.label, 'Сигнал')} {indices.length ? <span className="text-agro-muted">({indices.join(', ')})</span> : null} {count(reason.count) > 1 ? <span className="text-agro-muted">× {count(reason.count)}</span> : null}</span><span className="font-semibold text-agro-text">{Number.isFinite(reason.points) ? `${reason.points} балл.` : 'Баллы не указаны'}</span></li>;
      })}</ul> : <p className="mt-1 text-sm text-agro-muted">Сигналы не указаны.</p>}</section>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <section><h3 className="text-sm font-semibold text-agro-text">Рекомендуемые проверки</h3>{checks.length ? <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-agro-text">{checks.map((check) => <li key={check}>{check}</li>)}</ul> : <p className="mt-1 text-sm text-agro-muted">Проверки не указаны.</p>}</section>
        <section><h3 className="text-sm font-semibold text-agro-text">Ограничения</h3>{limitations.length ? <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-agro-muted">{limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul> : <p className="mt-1 text-sm text-agro-muted">Ограничения не указаны.</p>}</section>
      </div>

      <div className="flex flex-wrap gap-2 border-t border-agro-border pt-3">
        <button type="button" disabled={!validFieldId} onClick={() => validFieldId && onNavigate('field-detail', fieldId)} className="btn-primary rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-agro-accent disabled:opacity-50">Открыть поле</button>
        <button type="button" disabled={!validFieldId} onClick={() => validFieldId && onNavigate('field-analytics', fieldId)} className="btn-secondary rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-agro-accent disabled:opacity-50">Аналитика</button>
      </div>
    </article>
  );
}
