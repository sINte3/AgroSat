import { useEffect, useMemo, useRef, useState } from 'react';
import { getAgronomicInterpretation } from '../../api/agronomicInterpretation';
import AgronomicIndexCard from './AgronomicIndexCard';
import { InterpretationError, InterpretationLoading } from './AgronomicInterpretationStates';

const INDEX_ORDER = ['ndvi', 'savi', 'evi', 'ndmi', 'ndre'];
const RANGE_OPTIONS = [{ value: 30, label: '30 дней' }, { value: 90, label: '90 дней' }, { value: 180, label: '180 дней' }, { value: 'season', label: 'Сезон' }];
const CONFIDENCE = { high: 'Высокая уверенность', medium: 'Средняя уверенность', low: 'Низкая уверенность', insufficient: 'Недостаточно данных' };

function tashkentDateParts(date = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tashkent', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(date);
  return Object.fromEntries(parts.filter((part) => part.type !== 'literal').map((part) => [part.type, part.value]));
}

function getRange(value) {
  const now = tashkentDateParts();
  const dateTo = `${now.year}-${now.month}-${now.day}`;
  if (value === 'season') return { date_from: `${now.year}-01-01`, date_to: dateTo };
  const local = new Date(Number(now.year), Number(now.month) - 1, Number(now.day));
  local.setDate(local.getDate() - Number(value));
  const from = tashkentDateParts(local);
  return { date_from: `${from.year}-${from.month}-${from.day}`, date_to: dateTo };
}

function displayDate(value) { return value ? new Date(`${value}T12:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' }) : '—'; }

export default function AgronomicInterpretationPanel({ fieldId }) {
  const [rangeChoice, setRangeChoice] = useState(180);
  const [data, setData] = useState(null);
  const [state, setState] = useState('loading');
  const [retry, setRetry] = useState(0);
  const requestRef = useRef(0);
  const range = useMemo(() => getRange(rangeChoice), [rangeChoice]);

  useEffect(() => {
    if (!fieldId) return undefined;
    const controller = new AbortController();
    const requestId = ++requestRef.current;
    setState('loading');
    getAgronomicInterpretation(fieldId, range, controller.signal)
      .then((result) => { if (!controller.signal.aborted && requestId === requestRef.current) { setData(result); setState('ready'); } })
      .catch((error) => { if (!controller.signal.aborted && requestId === requestRef.current) { setState('error'); } });
    return () => controller.abort();
  }, [fieldId, range.date_from, range.date_to, retry]);

  if (state === 'loading') return <InterpretationLoading />;
  if (state === 'error') return <InterpretationError onRetry={() => setRetry((value) => value + 1)} />;
  const byCode = new Map((data?.indices || []).map((item) => [item.code?.toLowerCase(), item]));
  const generated = data?.generated_at ? new Date(data.generated_at).toLocaleString('ru-RU') : '—';
  return <section className="space-y-3" aria-labelledby="agronomic-interpretation-title">
    <div className="card p-4 space-y-3"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 id="agronomic-interpretation-title" className="text-lg font-bold text-agro-text">Агрономическая интерпретация</h3><p className="text-xs text-agro-muted mt-1">Спутниковые индексы — косвенные сигналы. Выводы нужно подтвердить осмотром поля.</p></div><span className="rounded-full bg-agro-surface2 px-2 py-1 text-xs text-agro-text">{CONFIDENCE[data?.overall_confidence] || 'Недостаточно данных'}</span></div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-agro-muted"><span>Поле: <b className="text-agro-text">{data?.field?.name || '—'}</b></span>{data?.field?.crop_name && <span>Культура: {data.field.crop_name}</span>}{data?.field?.season_year && <span>Сезон: {data.field.season_year}</span>}<span>Период: {displayDate(data?.range?.date_from)} — {displayDate(data?.range?.date_to)}</span><span>Сформировано: {generated}</span></div>
      <div className="flex flex-wrap items-center gap-2" aria-label="Период интерпретации">{RANGE_OPTIONS.map((option) => <button type="button" key={option.value} onClick={() => setRangeChoice(option.value)} className={`px-2.5 py-1 text-xs font-medium rounded-md focus:outline-none focus:ring-2 focus:ring-agro-accent ${rangeChoice === option.value ? 'bg-agro-accent text-white' : 'bg-agro-surface2 text-agro-muted hover:text-agro-text'}`}>{option.label}</button>)}<button type="button" onClick={() => setRetry((value) => value + 1)} className="ml-auto px-2.5 py-1 text-xs font-medium rounded-md bg-agro-surface2 text-agro-muted hover:text-agro-text focus:outline-none focus:ring-2 focus:ring-agro-accent">Обновить</button></div></div>
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-3">{INDEX_ORDER.map((code) => <AgronomicIndexCard key={code} item={byCode.get(code) || { code, label: code.toUpperCase(), data_status: 'no_data', confidence: { level: 'insufficient', reasons: ['Нет принятого наблюдения.'] }, baseline: { point_count: 0 }, change: {}, trend: {}, hypotheses: [], recommended_checks: [], limitations: [] }} />)}</div>
  </section>;
}
