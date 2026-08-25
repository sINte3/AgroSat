import { useEffect, useRef, useState } from 'react';

import {
  createAnomalyInspection,
  getInspectionAssignees,
  workflowKey,
} from '../../api/anomalyInspections';

const PRIORITIES = [
  ['low', 'Низкий'], ['normal', 'Обычный'], ['high', 'Высокий'], ['urgent', 'Срочный'],
];

function localDateTime(days = 1) {
  const date = new Date(Date.now() + days * 86400000);
  date.setHours(17, 0, 0, 0);
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}
function sourceLabel(kind) {
  return { pixel_ndvi: 'Пиксельный NDVI', alert: 'Предупреждение', manual: 'Ручной контекст' }[kind] || kind;
}

function messageFor(error) {
  const status = Number(error?.response?.status || 0);
  if (status === 409) return 'Контекст изменился. Выберите точку или предупреждение ещё раз.';
  if (status === 422) return 'Проверьте точку, исполнителя, срок и обязательные поля.';
  if (status === 403) return 'У вашей роли нет права создавать осмотр.';
  return 'Не удалось создать осмотр. Повторите попытку.';
}

export default function InspectionSourceDialog({ source, onClose, onCreated }) {
  const dialogRef = useRef(null);
  const closeRef = useRef(null);
  const requestRef = useRef(null);
  const keyRef = useRef(workflowKey('create-inspection'));
  const [assignees, setAssignees] = useState([]);
  const [reason, setReason] = useState(source?.reason || 'Проверить выявленное отклонение на поле');
  const [priority, setPriority] = useState(source?.priority || 'normal');
  const [assigneeId, setAssigneeId] = useState('');
  const [dueAt, setDueAt] = useState(localDateTime());
  const [state, setState] = useState('loading');
  const [error, setError] = useState('');

  useEffect(() => {
    const previous = document.activeElement;
    const controller = new AbortController();
    requestRef.current = controller;
    closeRef.current?.focus();
    getInspectionAssignees({ fieldId: source.field_id }, controller.signal)
      .then((items) => {
        setAssignees(Array.isArray(items) ? items : []);
        if (items?.[0]?.id) setAssigneeId(String(items[0].id));
        setState('ready');
      })
      .catch((requestError) => {
        if (requestError?.name !== 'CanceledError') {
          setState('error');
          setError('Не удалось загрузить список агрономов.');
        }
      });
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose();
      if (event.key !== 'Tab') return;
      const controls = Array.from(dialogRef.current?.querySelectorAll(
        'button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled])',
      ) || []);
      if (!controls.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      controller.abort();
      window.removeEventListener('keydown', onKeyDown);
      if (previous instanceof HTMLElement && document.contains(previous)) previous.focus();
    };
  }, [onClose, source.field_id]);

  async function submit(event) {
    event.preventDefault();
    if (!assigneeId) { setError('Выберите назначенного агронома.'); return; }
    setState('saving');
    setError('');
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      const result = await createAnomalyInspection({
        field_id: source.field_id,
        source_kind: source.kind,
        source_alert_id: source.alert_id ?? null,
        provider: source.provider ?? null,
        item_id: source.item_id ?? null,
        acquired_at: source.acquired_at ?? null,
        index_name: source.index_name ?? null,
        sampled_value: source.sampled_value ?? null,
        comparison_value: source.comparison_value ?? null,
        delta: source.delta ?? null,
        geometry_hash: source.geometry_hash ?? null,
        point: source.point ?? null,
        zone: source.zone ?? null,
        reason,
        priority,
        assigned_to_id: Number(assigneeId),
        due_at: new Date(dueAt).toISOString(),
      }, keyRef.current, controller.signal);
      onCreated(result.inspection);
    } catch (requestError) {
      setError(messageFor(requestError));
      setState('ready');
    }
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-end justify-center bg-slate-950/50 p-0 sm:items-center sm:p-5" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="inspection-source-title" aria-describedby="inspection-source-context" className="max-h-[96dvh] w-full overflow-y-auto rounded-t-2xl bg-white p-5 shadow-xl sm:max-w-2xl sm:rounded-xl sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="inspection-source-title" className="text-lg font-bold text-slate-950">Создать осмотр</h2>
            <p id="inspection-source-context" className="mt-1 text-sm text-slate-700">Зафиксируйте исполнителя, срок и причину — источник после создания изменить нельзя.</p>
          </div>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="Закрыть создание осмотра" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-xl text-slate-700 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-700">×</button>
        </div>

        <dl className="mt-5 grid gap-x-5 gap-y-3 rounded-xl bg-slate-50 p-4 text-sm sm:grid-cols-2">
          <div><dt className="text-slate-600">Источник</dt><dd className="font-semibold text-slate-950">{sourceLabel(source.kind)}</dd></div>
          <div><dt className="text-slate-600">Поле</dt><dd className="font-semibold text-slate-950">{source.field_name || `Поле #${source.field_id}`}</dd></div>
          {source.acquired_at && <div><dt className="text-slate-600">Снимок</dt><dd className="font-mono text-slate-950">{new Date(source.acquired_at).toLocaleString('ru-RU')}</dd></div>}
          {Number.isFinite(source.sampled_value) && <div><dt className="text-slate-600">{String(source.index_name || 'Индекс').toUpperCase()}</dt><dd className="font-mono font-bold text-slate-950">{source.sampled_value.toFixed(3)}</dd></div>}
          {source.point && <div className="sm:col-span-2"><dt className="text-slate-600">Точка</dt><dd className="font-mono text-slate-950">{source.point.longitude.toFixed(6)}, {source.point.latitude.toFixed(6)}</dd></div>}
        </dl>

        <form onSubmit={submit} className="mt-5 space-y-4">
          <label className="block text-sm font-semibold text-slate-900">Причина осмотра
            <textarea value={reason} onChange={(event) => setReason(event.target.value)} required minLength={5} maxLength={2000} rows={3} className="mt-1.5 w-full rounded-lg border border-slate-300 px-3 py-2.5 font-normal text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-700" />
          </label>
          <div className="grid gap-4 sm:grid-cols-3">
            <label className="text-sm font-semibold text-slate-900">Приоритет
              <select value={priority} onChange={(event) => setPriority(event.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 font-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-700">
                {PRIORITIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label className="text-sm font-semibold text-slate-900">Агроном
              <select value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)} required disabled={state === 'loading'} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 font-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-700">
                <option value="">Выберите</option>
                {assignees.map((item) => <option key={item.id} value={item.id}>{item.full_name || item.email}</option>)}
              </select>
            </label>
            <label className="text-sm font-semibold text-slate-900">Срок
              <input type="datetime-local" value={dueAt} onChange={(event) => setDueAt(event.target.value)} required className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 font-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-700" />
            </label>
          </div>
          {error && <p role="alert" className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm font-medium text-red-900">{error}</p>}
          <div className="sticky bottom-0 -mx-5 flex gap-3 border-t border-slate-200 bg-white px-5 pb-[max(0px,env(safe-area-inset-bottom))] pt-4 sm:static sm:mx-0 sm:justify-end sm:border-0 sm:p-0">
            <button type="button" onClick={onClose} className="min-h-11 flex-1 rounded-lg border border-slate-300 px-4 font-semibold text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-700 sm:flex-none">Отмена</button>
            <button type="submit" disabled={state !== 'ready' || !assignees.length} className="min-h-11 flex-1 rounded-lg bg-green-700 px-5 font-semibold text-white hover:bg-green-800 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-800 focus-visible:ring-offset-2 sm:flex-none">{state === 'saving' ? 'Создаём…' : 'Создать осмотр'}</button>
          </div>
        </form>
      </section>
    </div>
  );
}
