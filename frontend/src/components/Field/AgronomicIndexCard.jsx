import { useId, useState } from 'react';

const STATUS = {
  valid: 'Актуальные данные', stale: 'Данные устарели', insufficient_history: 'Недостаточно истории', no_data: 'Нет наблюдений',
};
const CONFIDENCE = { high: 'Высокая уверенность', medium: 'Средняя уверенность', low: 'Низкая уверенность', insufficient: 'Недостаточно данных' };
const CHANGE = { up: 'вырос', down: 'снизился', stable: 'без заметного изменения', unknown: 'недостаточно данных' };
const TREND = { rising: 'растущий тренд', falling: 'снижающийся тренд', stable: 'стабильный тренд', unknown: 'тренд не определён' };
const SIGNAL = { strong: 'сильное отклонение от истории поля', notable: 'заметное отклонение от истории поля', normal: 'в обычном диапазоне истории поля', insufficient: 'недостаточно истории' };

function formatNumber(value) {
  return value != null && Number.isFinite(Number(value)) ? Number(value).toFixed(4) : '—';
}

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(`${value}T12:00:00`);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' });
}

function confidenceReason(reason) {
  if (reason === 'quality_metrics_unavailable_for_this_index') return 'Для этого индекса в принятой записи нет отдельных метрик качества сцены.';
  if (typeof reason === 'string' && /^[a-z0-9_]+$/i.test(reason)) return 'Условия принятия наблюдения ограничивают уверенность.';
  return reason || 'Причина не указана.';
}

function Baseline({ baseline, latestValue }) {
  const source = [baseline?.minimum, baseline?.maximum, baseline?.p25, baseline?.p75, baseline?.median, latestValue];
  if (baseline?.status !== 'available' || source.some((value) => value == null || !Number.isFinite(Number(value)))) {
    return <p className="text-xs text-agro-muted">Базовая линия: недостаточно истории ({baseline?.point_count ?? 0} точек).</p>;
  }
  const values = source.map(Number);
  const [minimum, maximum, p25, p75, median, latest] = values;
  const span = maximum - minimum;
  const position = (value) => `${Math.min(100, Math.max(0, span === 0 ? 50 : ((value - minimum) / span) * 100))}%`;
  return (
    <div className="space-y-1.5">
      <p className="text-xs text-agro-muted">История поля: {formatNumber(minimum)}–{formatNumber(maximum)}, {baseline.point_count} точек</p>
      <div className="relative h-2 rounded-full bg-agro-surface2" aria-label={`Диапазон истории поля от ${formatNumber(minimum)} до ${formatNumber(maximum)}`}>
        <span className="absolute h-2 rounded-full bg-agro-accent/30" style={{ left: position(p25), width: `${Math.max(0, Number.parseFloat(position(p75)) - Number.parseFloat(position(p25)))}%` }} />
        <span className="absolute -top-1 h-4 w-0.5 bg-agro-text" style={{ left: position(median) }} title="Медиана" />
        <span className="absolute -top-1 h-4 w-0.5 bg-agro-accent" style={{ left: position(latest) }} title="Последнее значение" />
      </div>
      <p className="text-[11px] text-agro-muted">Полоса p25–p75; тёмная метка — медиана, зелёная — последнее значение.</p>
    </div>
  );
}

export default function AgronomicIndexCard({ item }) {
  const [expanded, setExpanded] = useState(false);
  const detailsId = useId();
  const latest = item?.latest_observation;
  const noData = !latest;
  const freshness = Number.isFinite(Number(latest?.freshness_days)) ? `${latest.freshness_days} дн. назад` : 'Возраст снимка не указан';
  const checks = item?.recommended_checks?.length ? item.recommended_checks : ['Сравните сигнал с фактическим состоянием растений.'];
  return (
    <article className="card p-4 space-y-3 min-w-0">
      <div className="flex items-start justify-between gap-2">
        <div><h4 className="font-semibold text-agro-text">{item?.label || item?.code?.toUpperCase()}</h4><p className="text-xs text-agro-muted">{STATUS[item?.data_status] || 'Статус не указан'}</p></div>
        <span className="text-[11px] rounded-full bg-agro-surface2 px-2 py-1 text-agro-text text-right">{CONFIDENCE[item?.confidence?.level] || 'Уверенность не указана'}</span>
      </div>
      {noData ? <p className="text-sm text-agro-muted">В выбранном диапазоне нет принятого наблюдения. Измените период и повторите проверку.</p> : <>
        <div><p className="text-xl font-bold text-agro-text">{formatNumber(latest.value)}</p><p className="text-xs text-agro-muted">{formatDate(latest.observed_at)} · {freshness}</p></div>
        <div className="grid grid-cols-2 gap-2 text-xs"><p><span className="text-agro-muted">Предыдущее: </span>{formatNumber(item.previous_valid_observation?.value)}</p><p><span className="text-agro-muted">Изменение: </span>{formatNumber(item.change?.absolute)} ({CHANGE[item.change?.direction] || CHANGE.unknown})</p></div>
        <p className="text-xs text-agro-muted">Сигнал: <span className="text-agro-text">{SIGNAL[item.change?.statistical_signal] || SIGNAL.insufficient}</span></p>
        <p className="text-xs text-agro-muted">Тренд: <span className="text-agro-text">{TREND[item.trend?.direction] || TREND.unknown}</span></p>
        <Baseline baseline={item.baseline} latestValue={latest.value} />
      </>}
      <button type="button" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded} aria-controls={detailsId} className="text-sm text-agro-accent hover:underline focus:outline-none focus:ring-2 focus:ring-agro-accent rounded">
        {expanded ? 'Скрыть детали' : 'Показать детали'}
      </button>
      {expanded && <div id={detailsId} className="border-t border-agro-surface2 pt-3 space-y-3 text-xs text-agro-text">
        <p><span className="font-medium">Значение: </span>{item.meaning || 'Описание не указано.'}</p>
        <div><p className="font-medium mb-1">Уверенность</p><ul className="list-disc pl-4 space-y-1">{(item.confidence?.reasons || []).map((reason, index) => <li key={`${reason}-${index}`}>{confidenceReason(reason)}</li>)}</ul></div>
        <div><p className="font-medium mb-1">Что стоит проверить</p>{item.hypotheses?.length ? item.hypotheses.map((hypothesis, index) => <div key={`${hypothesis.code}-${index}`} className="mb-2"><p>Возможная причина, требующая проверки: {hypothesis.label}. {hypothesis.rationale}</p></div>) : <p>Согласованного отклонения нескольких индексов не выявлено.</p>}<ul className="list-disc pl-4 mt-1 space-y-1">{checks.map((check, index) => <li key={`${check}-${index}`}>{check}</li>)}</ul></div>
        <div><p className="font-medium mb-1">Ограничения</p><ul className="list-disc pl-4 space-y-1">{(item.limitations || []).map((limitation, index) => <li key={`${limitation}-${index}`}>{limitation}</li>)}</ul></div>
      </div>}
    </article>
  );
}
