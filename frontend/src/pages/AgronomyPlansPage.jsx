import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { addAgronomyWork, createAgronomyDraft, getAgronomyPlan, getAgronomySummary, listAgronomyPlans, reevaluateAgronomyPlan, transitionAgronomyPlan, transitionAgronomyWork } from '../api/closedLoopAgronomy';
import { getAgronomyDraft, saveAgronomyDraft, removeAgronomyDraft } from '../offline/offlineScoutingStore';
import AgronomyCaseMap from '../components/Agronomy/AgronomyCaseMap';
import {
  PLAN_STATUS_LABELS as STATUS,
  planStatusLabel,
  planStatusTone,
  TONE_CLASSES,
  VERIFICATION_STATUS,
  WORK_STATUS_LABELS,
} from '../config/canonicalLifecycle';
import { formatTashkentDateTime } from '../utils/tashkentTime';
import { validateWorkSchedule, workDraftInputs } from '../utils/workSchedule';

// Raw datetime-local text is kept as typed; it is validated and converted to a
// timezone-aware value (Asia/Tashkent) only when the form is submitted.
const blank = { category: 'reinspection', instruction: '', assigned_to_id: '', dueInput: '', plannedStartInput: '', geometry: null };
const message = error => error?.response?.status === 409 ? 'Данные изменились. Ваш черновик сохранён; обновите карточку и сравните версии.' : error?.response?.data?.detail || 'Не удалось выполнить действие.';
const isCancel = error => error?.name === 'CanceledError' || error?.name === 'AbortError' || error?.code === 'ERR_CANCELED';
const verificationLabel = value => VERIFICATION_STATUS[value]?.label || 'Проверка ещё не выполнялась';

function restoreWork(saved) {
  if (!saved) return blank;
  const { dueInput, plannedStartInput } = workDraftInputs(saved);
  return {
    ...blank,
    category: saved.category || blank.category,
    instruction: saved.instruction || '',
    assigned_to_id: saved.assigned_to_id == null ? '' : String(saved.assigned_to_id),
    geometry: saved.geometry || null,
    dueInput,
    plannedStartInput,
  };
}

export default function AgronomyPlansPage({ onNavigate, enterprises, selectedPlanId }) {
  const { user } = useAuth(); const editable = user?.role !== 'viewer';
  const [filters, setFilters] = useState({ status: '', priority: '', verification_status: '', enterprise_id: '' });
  const [data, setData] = useState({ items: [], total: 0 }); const [summary, setSummary] = useState(null);
  const [queueState, setQueueState] = useState('loading');
  const [queueReload, setQueueReload] = useState(0);
  const [selectedId, setSelectedId] = useState(selectedPlanId || null); const [detail, setDetail] = useState(null);
  const [detailState, setDetailState] = useState('idle');
  const [busy, setBusy] = useState(false); const [notice, setNotice] = useState('');
  const [reason, setReason] = useState('Проверено агрономом'); const [inspectionId, setInspectionId] = useState(''); const [work, setWork] = useState(blank);
  const [workErrors, setWorkErrors] = useState({});
  const selectedIdRef = useRef(selectedId);
  const detailGenerationRef = useRef(0);
  selectedIdRef.current = selectedId;
  const query = useMemo(() => Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== '')), [filters]);

  useEffect(() => {
    const controller = new AbortController();
    setQueueState('loading'); setNotice('');
    Promise.all([listAgronomyPlans(query, controller.signal), getAgronomySummary(query, controller.signal)])
      .then(([queue, totals]) => {
        if (controller.signal.aborted) return;
        setData({ items: Array.isArray(queue?.items) ? queue.items : [], total: Number(queue?.total || 0) });
        setSummary(totals || null);
        setQueueState('ready');
        if (!selectedIdRef.current && queue?.items?.[0]) setSelectedId(queue.items[0].id);
      })
      .catch(error => {
        if (controller.signal.aborted || isCancel(error)) return;
        setQueueState('error'); setSummary(null);
        setNotice(navigator.onLine ? message(error) : 'Нет сети. Незавершённые заметки остаются на устройстве.');
      });
    return () => controller.abort();
  }, [query, queueReload]);

  useEffect(() => { if (selectedPlanId) setSelectedId(selectedPlanId); }, [selectedPlanId]);

  const loadDetail = useCallback(async (id, signal) => {
    const generation = ++detailGenerationRef.current;
    setDetailState('loading');
    try {
      const value = await getAgronomyPlan(id, signal);
      if (signal?.aborted || generation !== detailGenerationRef.current) return;
      setDetail(value); setDetailState('ready');
    } catch (error) {
      if (signal?.aborted || generation !== detailGenerationRef.current || isCancel(error)) return;
      setDetail(null); setDetailState('error'); setNotice(message(error));
    }
  }, []);

  useEffect(() => {
    if (!selectedId) { setDetail(null); setDetailState('idle'); return undefined; }
    const controller = new AbortController();
    void loadDetail(selectedId, controller.signal);
    return () => controller.abort();
  }, [loadDetail, selectedId]);

  useEffect(() => {
    if (!selectedId) return undefined;
    let active = true;
    setWork(blank); setWorkErrors({});
    getAgronomyDraft(user, selectedId).then(draft => {
      if (!active || !draft) return;
      if (draft.reason) setReason(draft.reason);
      setWork(restoreWork(draft.work));
      setNotice('Восстановлен локальный черновик.');
    }).catch(() => {});
    return () => { active = false; };
  }, [selectedId, user]);

  const refresh = async () => {
    setQueueReload(value => value + 1);
    if (selectedIdRef.current) await loadDetail(selectedIdRef.current);
  };
  const saveLocalDraft = () => saveAgronomyDraft(user, detail.id, { reason, work, baseVersion: detail.version }).catch(() => {});
  const act = async operation => { if (!detail || reason.trim().length < 5) return; setBusy(true); try { await transitionAgronomyPlan(detail.id, operation, detail.version, reason); await removeAgronomyDraft(user, detail.id); await refresh(); setNotice('Изменение сохранено в журнале.'); } catch (error) { if (error?.response?.status === 409) await saveLocalDraft(); setNotice(message(error)); } finally { setBusy(false); } };
  const addWork = async event => {
    event.preventDefault();
    if (!detail) return;
    const schedule = validateWorkSchedule(work);
    setWorkErrors(schedule.errors);
    if (Object.keys(schedule.errors).length) return;
    setBusy(true);
    try {
      await addAgronomyWork(detail.id, {
        category: work.category,
        instruction: work.instruction,
        geometry: work.geometry,
        assigned_to_id: Number(work.assigned_to_id) || null,
        planned_start_at: schedule.values.planned_start_at,
        due_at: schedule.values.due_at,
        expected_version: detail.version,
        reason,
      });
      setWork(blank); setWorkErrors({}); await removeAgronomyDraft(user, detail.id); await refresh();
    } catch (error) { await saveLocalDraft(); setNotice(message(error)); } finally { setBusy(false); }
  };
  const workAct = async (item, operation) => { setBusy(true); try { await transitionAgronomyWork(detail.id, item.id, { operation, expected_plan_version: detail.version, expected_version: item.version, reason, assigned_to_id: item.assigned_to_id, due_at: item.due_at, planned_start_at: item.planned_start_at, result_note: operation === 'complete' ? reason : null }); await refresh(); } catch (error) { setNotice(message(error)); } finally { setBusy(false); } };
  const makeDraft = async event => { event.preventDefault(); setBusy(true); try { const result = await createAgronomyDraft(Number(inspectionId), reason); setInspectionId(''); setSelectedId(result.plan_id); setQueueReload(value => value + 1); } catch (error) { setNotice(message(error)); } finally { setBusy(false); } };
  const setWorkField = (key, value) => setWork(current => ({ ...current, [key]: value }));
  const summaryValue = key => (queueState === 'ready' && summary ? summary[key] ?? '—' : '—');

  return <section className="h-full overflow-y-auto p-4 md:p-6" aria-labelledby="agronomy-title">
    <div className="mx-auto max-w-[1500px] space-y-4 pb-24"><div className="flex flex-wrap items-end justify-between gap-3"><div><h1 id="agronomy-title" className="text-xl font-semibold">Меры и контроль</h1><p className="mt-1 text-sm text-agro-muted">От решения по осмотру до подтверждения результата новым спутниковым наблюдением.</p></div>{editable && <form onSubmit={makeDraft} className="flex flex-wrap items-end gap-2"><label className="text-sm">Осмотр №<input className="input ml-2 w-28" type="number" min="1" required value={inspectionId} onChange={e => setInspectionId(e.target.value)} /></label><button className="btn-primary min-h-11" disabled={busy || !inspectionId}>Создать черновик</button></form>}</div>
    {notice && <div role="status" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">{notice}</div>}
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">{[['open','Открыто'],['overdue','Просрочено'],['pending_verification','Ожидает проверки'],['improved','Улучшено'],['ineffective','Без эффекта / доработка']].map(([key,label]) => <div className="card" key={key}><div className="text-2xl font-semibold tabular-nums">{summaryValue(key)}</div><div className="mt-1 text-sm text-agro-muted">{label}</div></div>)}</div>
    <div className="flex flex-wrap gap-3 rounded-lg border border-agro-border bg-white p-3">{user?.role === 'admin' && <label className="text-sm">Предприятие<select className="input ml-2" value={filters.enterprise_id} onChange={e => setFilters(v => ({...v, enterprise_id:e.target.value}))}><option value="">Все</option>{enterprises.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>}<label className="text-sm">Статус<select className="input ml-2" value={filters.status} onChange={e => setFilters(v => ({...v,status:e.target.value}))}><option value="">Все</option>{Object.entries(STATUS).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label><label className="text-sm">Приоритет<select className="input ml-2" value={filters.priority} onChange={e => setFilters(v => ({...v,priority:e.target.value}))}><option value="">Все</option><option value="urgent">Срочный</option><option value="high">Высокий</option><option value="normal">Обычный</option><option value="low">Низкий</option></select></label></div>
    <div className="grid min-h-[620px] overflow-hidden rounded-lg border border-agro-border bg-white lg:grid-cols-[minmax(300px,.72fr)_minmax(520px,1.28fr)]"><div className="border-b border-agro-border lg:border-b-0 lg:border-r"><div className="border-b border-agro-border p-4 text-sm text-agro-muted">{queueState === 'loading' ? 'Загрузка…' : queueState === 'error' ? 'Очередь не загружена' : `${data.total} планов`}</div><div className="max-h-[620px] overflow-y-auto" role="list">
      {queueState === 'error' && <div role="alert" className="p-5 text-sm text-red-900"><p>Не удалось загрузить планы. Отсутствие строк здесь не означает отсутствие планов.</p><button type="button" className="btn-secondary mt-3 min-h-11" onClick={() => setQueueReload(value => value + 1)}>Повторить</button></div>}
      {queueState !== 'error' && data.items.map(item => <button type="button" role="listitem" key={item.id} onClick={() => setSelectedId(item.id)} className={`block min-h-20 w-full border-b border-agro-border p-4 text-left focus:outline-none focus:ring-2 focus:ring-inset focus:ring-agro-accent ${selectedId===item.id?'bg-emerald-50':'hover:bg-agro-hover'}`}><span className="flex justify-between gap-2"><strong className="truncate">{item.field_name}</strong><span className={`badge border ${TONE_CLASSES[planStatusTone(item)]}`}>{planStatusLabel(item)}</span></span><span className="mt-1 block text-sm text-agro-muted">План #{item.id} · осмотр #{item.inspection_id} · {verificationLabel(item.verification_status)}</span></button>)}
      {queueState === 'ready' && !data.items.length && <p className="p-5 text-sm text-agro-muted">Планов по выбранным фильтрам нет. Черновик создаётся только из отправленного осмотра.</p>}
    </div></div>
    <div className="min-w-0 overflow-y-auto p-4">{detail ? <div className="space-y-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">{detail.field_name} · план #{detail.id}</h2><p className="text-sm text-agro-muted">{detail.enterprise_name} · правило {detail.policy_version} · версия {detail.version}</p></div><span className={`badge border ${TONE_CLASSES[planStatusTone(detail)]}`}>{planStatusLabel(detail)}</span></div><AgronomyCaseMap geometry={detail.field_geometry} workItems={detail.work_items} /><div><h3 className="font-semibold">Решение и неопределённость</h3><p className="mt-2 text-sm leading-6">{detail.decision}</p><p className="mt-2 text-sm text-agro-muted">{detail.recommendation?.explanation}</p><div className="mt-2 rounded-lg bg-slate-100 p-3 text-sm"><strong>Требуется согласование агронома.</strong> Уверенность: {detail.recommendation?.confidence || 'не определена'}. {detail.recommendation?.limitations?.join(' ')}</div></div>
    <div><h3 className="font-semibold">Проверка результата</h3><p className={`mt-2 inline-flex rounded-full border px-3 py-1 text-sm font-medium ${TONE_CLASSES[VERIFICATION_STATUS[detail.verification_status]?.tone || 'neutral']}`}>{verificationLabel(detail.verification_status)}</p><p className="mt-2 text-xs text-agro-muted">Новые спутниковые снимки собирает автоматический сборщик; повторная проверка использует уже сохранённые наблюдения.</p></div>
    <div><h3 className="font-semibold">Работы</h3><div className="mt-2 space-y-2">{detail.work_items.map(item => <div key={item.id} className="rounded-lg border border-agro-border p-3"><div className="flex flex-wrap justify-between gap-2"><strong>{item.instruction}</strong><span>{WORK_STATUS_LABELS[item.status] || item.status}</span></div><p className="mt-1 text-sm text-agro-muted">{item.assignee_name || 'Не назначено'} · срок {formatTashkentDateTime(item.due_at)}</p>{editable && <div className="mt-2 flex gap-2">{item.status==='planned' && detail.status==='approved' && <button className="btn-secondary min-h-11" disabled={busy} onClick={() => workAct(item,'start')}>Начать</button>}{item.status==='in_progress' && <button className="btn-primary min-h-11" disabled={busy || reason.trim().length<5} onClick={() => workAct(item,'complete')}>Завершить с заметкой</button>}</div>}</div>)}</div></div>
    {editable && ['draft','rework'].includes(detail.status) && <form onSubmit={addWork} noValidate className="space-y-3 rounded-lg border border-agro-border p-3" aria-describedby="work-schedule-help"><h3 className="font-semibold">Добавить работу</h3><label className="block text-sm">Инструкция<textarea className="input mt-1 min-h-20 w-full" required minLength="5" value={work.instruction} onChange={e => setWorkField('instruction', e.target.value)} /></label><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><label className="text-sm">Категория<select className="input mt-1 min-h-11 w-full" value={work.category} onChange={e=>setWorkField('category', e.target.value)}><option value="reinspection">Повторный осмотр</option><option value="sampling">Отбор проб</option><option value="irrigation">Полив</option><option value="nutrition">Питание</option><option value="crop_protection">Защита растений</option><option value="drainage">Дренаж</option><option value="cultivation">Обработка</option><option value="other">Другое</option></select></label><label className="text-sm">Исполнитель<select className="input mt-1 min-h-11 w-full" required value={work.assigned_to_id} onChange={e=>setWorkField('assigned_to_id', e.target.value)}><option value="">Выберите</option>{detail.assignees.map(a=><option key={a.id} value={a.id}>{a.full_name}</option>)}</select></label><label className="text-sm">Плановое начало<input className="input mt-1 min-h-11 w-full" type="datetime-local" value={work.plannedStartInput} onChange={e=>setWorkField('plannedStartInput', e.target.value)} aria-invalid={Boolean(workErrors.planned_start_at)} aria-describedby="work-start-error" /><span id="work-start-error" className="mt-1 block text-xs text-red-700">{workErrors.planned_start_at || ''}</span></label><label className="text-sm">Срок<span aria-hidden="true"> *</span><input className="input mt-1 min-h-11 w-full" type="datetime-local" required value={work.dueInput} onChange={e=>setWorkField('dueInput', e.target.value)} aria-invalid={Boolean(workErrors.due_at)} aria-describedby="work-due-error" /><span id="work-due-error" className="mt-1 block text-xs text-red-700">{workErrors.due_at || ''}</span></label></div><p id="work-schedule-help" className="text-xs text-agro-muted">Время указывается по Ташкенту (UTC+05:00).</p><button className="btn-secondary min-h-11" disabled={busy}>Добавить</button></form>}
    {editable && <div className="space-y-2 border-t border-agro-border pt-4"><label className="block text-sm font-medium">Основание действия<textarea className="input mt-1 min-h-20 w-full" minLength="5" maxLength="2000" value={reason} onChange={e=>setReason(e.target.value)} /></label><div className="flex flex-wrap gap-2">{['draft','rework'].includes(detail.status) && <button className="btn-primary min-h-11" disabled={busy || !detail.work_items.length || reason.trim().length<5} onClick={()=>act('approve')}>Согласовать</button>}{detail.status==='pending_verification' && <><button className="btn-secondary min-h-11" disabled={busy} onClick={async()=>{setBusy(true);try{await reevaluateAgronomyPlan(detail.id,detail.version,reason);await refresh();}catch(e){setNotice(message(e));}finally{setBusy(false)}}}>Перепроверить по сохранённым снимкам</button>{detail.verification_status==='IMPROVED' && <button className="btn-primary min-h-11" disabled={busy} onClick={()=>act('close')}>Закрыть</button>}<button className="btn-secondary min-h-11" disabled={busy} onClick={()=>act('rework')}>На доработку</button></>}</div></div>}
    <div><h3 className="font-semibold">Хронология</h3><ol className="mt-2 border-l border-agro-border pl-4">{detail.timeline.map(event=><li className="mb-3 text-sm" key={event.id}><strong>{event.event_type}</strong><div className="text-agro-muted">{formatTashkentDateTime(event.occurred_at)} · {event.actor_name || 'Система'}</div></li>)}</ol></div><div className="flex flex-wrap gap-2 text-sm"><button className="btn-secondary min-h-11" onClick={()=>onNavigate('field-inspection-detail',detail.inspection_id)}>Осмотр</button><button className="btn-secondary min-h-11" onClick={()=>onNavigate('field-detail',detail.field_id)}>Поле</button><button className="btn-secondary min-h-11" onClick={()=>onNavigate('monitoring')}>Мониторинг</button></div></div> : detailState === 'loading' ? <p role="status" className="text-sm text-agro-muted">Загружаем план…</p> : detailState === 'error' ? <div role="alert" className="text-sm text-red-900"><p>План не загружен.</p><button type="button" className="btn-secondary mt-3 min-h-11" onClick={() => selectedId && loadDetail(selectedId)}>Повторить</button></div> : <p className="text-sm text-agro-muted">Выберите план в очереди.</p>}</div></div></div>
  </section>;
}
