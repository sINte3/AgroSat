import { useId, useState } from 'react';

const STATUS = {
  valid: 'Актуальные данные', stale: 'Данные устарели', insufficient_history: 'Недостаточно истории', no_data: 'Нет наблюдений',
};
const CONFIDENCE = { high: 'Высокая уверенность', medium: 'Средняя уверенность', low: 'Низкая уверенность', insufficient: 'Недостаточно данных' };
const CONTEXT_CONFIDENCE = { high: 'Высокая', medium: 'Средняя', low: 'Низкая', none: 'Не определена' };
const CHANGE = { up: 'вырос', down: 'снизился', stable: 'без заметного изменения', unknown: 'недостаточно данных' };
const TREND = { rising: 'Растущий тренд', falling: 'Снижающийся тренд', stable: 'Без заметного тренда', unknown: 'Тренд не определён' };
const TREND_STRENGTH = { weak: 'Слабая выраженность', moderate: 'Умеренная выраженность', strong: 'Сильная выраженность', unknown: 'Выраженность не определена' };
const POSITION = { within_field_range: 'В обычном диапазоне истории поля', above_field_range: 'Выше обычного диапазона поля', below_field_range: 'Ниже обычного диапазона поля', insufficient_history: 'Недостаточно истории поля' };
const HETEROGENEITY = { low: 'Низкая неоднородность', moderate: 'Умеренная неоднородность', high: 'Высокая неоднородность', unknown: 'Недостаточно данных', insufficient: 'Недостаточно данных' };
const SIGNAL = { strong: 'сильное отклонение от истории поля', notable: 'заметное отклонение от истории поля', normal: 'в обычном диапазоне истории поля', insufficient: 'недостаточно истории' };
const QUALITY_FLAG_LABELS = {
  no_data: 'Нет данных',
  single_observation: 'Только одно наблюдение',
  insufficient_history: 'Недостаточно истории',
  stale: 'Данные устарели',
  low_valid_pixels: 'Мало пригодных пикселей',
  high_cloud_cover: 'Высокая облачность',
  missing_dispersion: 'Нет данных о разбросе',
  duplicate_date: 'Повтор наблюдения за одну дату',
  future_observation_date: 'Дата наблюдения находится в будущем',
};

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
      <p className="text-sm text-agro-muted">{POSITION[baseline.position_status] || POSITION.insufficient_history}. Минимум {formatNumber(minimum)}, максимум {formatNumber(maximum)}, выборка {baseline.point_count}.</p>
      <div className="relative h-2 rounded-full bg-agro-surface2" aria-label={`Диапазон истории поля от ${formatNumber(minimum)} до ${formatNumber(maximum)}`}>
        <span className="absolute h-2 rounded-full bg-agro-accent/30" style={{ left: position(p25), width: `${Math.max(0, Number.parseFloat(position(p75)) - Number.parseFloat(position(p25)))}%` }} />
        <span className="absolute -top-1 h-4 w-0.5 bg-agro-text" style={{ left: position(median) }} title="Медиана" />
        <span className="absolute -top-1 h-4 w-0.5 bg-agro-accent" style={{ left: position(latest) }} title="Последнее значение" />
      </div>
      <p className="text-xs text-agro-muted">Полоса — p25–p75; тёмная метка — медиана, зелёная — последнее значение. Положение статистическое и не является оценкой здоровья растений.</p>
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
  const usefulFor = Array.isArray(item?.useful_for) ? item.useful_for : item?.useful_for ? [item.useful_for] : [];
  const qualityFlags = [...new Set(Array.isArray(item?.data_quality?.quality_flags) ? item.data_quality.quality_flags : [])];
  return (
    <article className="card p-4 space-y-3 min-w-0">
      <div className="flex items-start justify-between gap-2">
        <div><h4 className="text-lg font-semibold text-agro-text">{item?.display_name || item?.label || item?.code?.toUpperCase()}</h4><p className="text-sm text-agro-muted">{item?.plain_language_meaning || item?.meaning || STATUS[item?.data_status] || 'Статус не указан'}</p></div>
        <span className="text-[11px] rounded-full bg-agro-surface2 px-2 py-1 text-agro-text text-right">{CONFIDENCE[item?.confidence?.level] || 'Уверенность не указана'}</span>
      </div>
      {noData ? <p className="text-sm text-agro-muted">В выбранном диапазоне нет принятого наблюдения. Измените период и повторите проверку.</p> : <>
        <div><p className="text-xl font-bold text-agro-text">{formatNumber(latest.value)}</p><p className="text-xs text-agro-muted">{formatDate(latest.observed_at)} · {freshness}</p></div>
        <div className="grid grid-cols-2 gap-2 text-xs"><p><span className="text-agro-muted">Предыдущее: </span>{formatNumber(item.previous_valid_observation?.value)}</p><p><span className="text-agro-muted">Изменение: </span>{formatNumber(item.change?.absolute)} ({CHANGE[item.change?.direction] || CHANGE.unknown})</p></div>
        <p className="text-xs text-agro-muted">Сигнал: <span className="text-agro-text">{SIGNAL[item.change?.statistical_signal] || SIGNAL.insufficient}</span></p>
        <p className="text-sm text-agro-muted">Тренд: <span className="text-agro-text">{TREND[item.trend?.direction] || TREND.unknown} · {TREND_STRENGTH[item.trend?.strength] || TREND_STRENGTH.unknown}{Number.isFinite(item.trend?.duration_days) ? ` · ${item.trend.duration_days} дн.` : ''}</span></p>
        <p className="text-sm text-agro-muted">Контекстная уверенность: <span className="text-agro-text">{CONTEXT_CONFIDENCE[item.confidence?.contextual_level] || CONTEXT_CONFIDENCE.none}</span></p>
        <p className="text-sm text-agro-muted">Неоднородность: <span className="text-agro-text">{HETEROGENEITY[item.heterogeneity?.classification || item.heterogeneity?.level] || HETEROGENEITY.unknown}</span>. Сигнал сам по себе не устанавливает причину.</p>
        <p className="text-sm text-agro-muted">Качество данных: {item.data_quality?.observation_count ?? 0} наблюдений{Number.isFinite(item.data_quality?.valid_pixels_pct) ? ` · пригодно ${item.data_quality.valid_pixels_pct}% пикселей` : ''}.</p>
        <Baseline baseline={item.baseline} latestValue={latest.value} />
      </>}
      <button type="button" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded} aria-controls={detailsId} className="text-sm text-agro-accent hover:underline focus:outline-none focus:ring-2 focus:ring-agro-accent rounded">
        {expanded ? 'Скрыть детали' : 'Показать детали'}
      </button>
      {expanded && <div id={detailsId} className="border-t border-agro-surface2 pt-3 space-y-3 text-xs text-agro-text">
        <div>
          <p className="font-medium">Качество данных</p>
          {qualityFlags.length > 0 ? (
            <ul className="mt-1 list-disc space-y-1 pl-4">
              {qualityFlags.map((flag, index) => <li key={`${String(flag)}-${index}`}>{QUALITY_FLAG_LABELS[flag] || 'Дополнительное ограничение качества данных'}</li>)}
            </ul>
          ) : <p className="mt-1">Дополнительных ограничений качества не отмечено.</p>}
        </div>
        <p><span className="font-medium">Что показывает индекс: </span>{item.plain_language_meaning || item.meaning || 'Описание не указано.'}</p>
        <div>
          <p className="font-medium">Для чего полезен:</p>
          {usefulFor.length ? <ul className="mt-1 list-disc space-y-1 pl-4">{usefulFor.map((value, index) => <li key={`${value}-${index}`}>{value}</li>)}</ul> : <p>Дополнительное назначение не указано.</p>}
        </div>
        <div><p className="font-medium mb-1">Уверенность</p><ul className="list-disc pl-4 space-y-1">{(item.confidence?.reasons || []).map((reason, index) => <li key={`${reason}-${index}`}>{confidenceReason(reason)}</li>)}</ul></div>
        <div><p className="mb-1 font-medium">Возможные причины для проверки</p>
          {item.hypotheses?.length ? item.hypotheses.map((hypothesis, index) => <div key={`${hypothesis.code}-${index}`} className="mb-2"><p>Возможная причина, требующая проверки: {hypothesis.label || hypothesis.title}. Требуется проверка в поле. {hypothesis.rationale || hypothesis.reason}</p></div>) : <p>Согласованного отклонения нескольких индексов не выявлено.</p>}
          <p className="mt-2 font-medium">Что проверить в поле</p>
          <ul className="mt-1 list-disc space-y-1 pl-4">{checks.map((check, index) => <li key={`${check}-${index}`}>{check}</li>)}</ul>
        </div>
        <div><p className="font-medium mb-1">Ограничения</p><ul className="list-disc pl-4 space-y-1">{(item.limitations || []).map((limitation, index) => <li key={`${limitation}-${index}`}>{limitation}</li>)}</ul></div>
      </div>}
    </article>
  );
}
