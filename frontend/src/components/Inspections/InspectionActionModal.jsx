import { useEffect, useMemo, useRef, useState } from 'react';

import {
  cancelFieldInspection, completeFieldInspection, startFieldInspection, updateFieldInspection,
} from '../../api/fieldInspections';
import {
  displayName, filterAssigneesForEnterprise, inspectionError, normalizeRole, todayTashkentDate,
} from './inspectionPresentation';

const LABELS = {
  edit: 'Редактировать осмотр', start: 'Начать осмотр?', complete: 'Завершить осмотр', cancel: 'Отменить осмотр',
};

export default function InspectionActionModal({ mode, item, user, assignees, onClose, onSuccess, onReload }) {
  const [title, setTitle] = useState(item?.title || '');
  const [instructions, setInstructions] = useState(item?.instructions || '');
  const [dueDate, setDueDate] = useState(item?.due_date || '');
  const [assignedToId, setAssignedToId] = useState(item?.assigned_to?.id ? String(item.assigned_to.id) : '');
  const [text, setText] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const controllerRef = useRef(null);
  const initialFocusRef = useRef(null);
  const role = normalizeRole(user?.role);
  const globalRole = role === 'admin' || role === 'manager';
  const eligibleAssignees = useMemo(() => {
    const currentAssignee = item?.assigned_to?.id ? [{
      id: item.assigned_to.id,
      name: displayName(item.assigned_to, 'Исполнитель'),
      enterpriseId: item?.field?.enterprise_id,
    }] : [];
    return filterAssigneesForEnterprise(
      [...currentAssignee, ...assignees],
      item?.field?.enterprise_id,
    );
  }, [assignees, item?.assigned_to, item?.field?.enterprise_id]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    initialFocusRef.current?.focus();
    return () => {
      controllerRef.current?.abort();
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  useEffect(() => {
    const handleEscape = (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        if (!pending) onClose();
      }
    };
    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [onClose, pending]);

  const valid = useMemo(() => {
    if (mode === 'complete') return text.trim().length >= 10 && text.trim().length <= 4000;
    if (mode === 'cancel') return text.trim().length >= 5 && text.trim().length <= 2000;
    if (mode === 'edit') return title.trim().length >= 3 && title.trim().length <= 255 && (!dueDate || dueDate >= todayTashkentDate());
    return true;
  }, [dueDate, mode, text, title]);

  async function submit(event) {
    event.preventDefault();
    if (!valid || pending) return;
    setPending(true);
    setError('');
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      let result;
      if (mode === 'edit') {
        const payload = { expected_version: item.version };
        if (title.trim() !== item.title) payload.title = title.trim();
        if ((instructions.trim() || null) !== (item.instructions || null)) payload.instructions = instructions.trim() || null;
        if ((dueDate || null) !== (item.due_date || null)) payload.due_date = dueDate || null;
        if (globalRole && (assignedToId || null) !== String(item.assigned_to?.id || '')) payload.assigned_to_id = assignedToId ? Number(assignedToId) : null;
        if (Object.keys(payload).length === 1) {
          setError('Измените хотя бы одно поле.');
          setPending(false);
          return;
        }
        result = await updateFieldInspection(item.id, payload, controller.signal);
      } else if (mode === 'start') result = await startFieldInspection(item.id, item.version, controller.signal);
      else if (mode === 'complete') result = await completeFieldInspection(item.id, item.version, text.trim(), controller.signal);
      else result = await cancelFieldInspection(item.id, item.version, text.trim(), controller.signal);
      onSuccess(result);
    } catch (requestError) {
      if (requestError?.name !== 'AbortError' && requestError?.code !== 'ERR_CANCELED') setError(inspectionError(requestError));
    } finally {
      if (!controller.signal.aborted) setPending(false);
    }
  }

  const label = LABELS[mode];
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-3" role="dialog" aria-modal="true" aria-labelledby="action-title">
      <form onSubmit={submit} className="card max-h-[calc(100vh-1.5rem)] w-full max-w-lg overflow-y-auto p-5">
        <div className="flex justify-between gap-3">
<h2 id="action-title" className="text-lg font-bold">{label}</h2>
<button type="button" onClick={onClose} disabled={pending} aria-label="Закрыть" className="rounded-lg p-2 focus:outline-none focus:ring-2 focus:ring-agro-accent">✕</button>
</div>
        <p className="mt-2 text-sm text-agro-muted">Осмотр: {item?.title || `#${item?.id}`}. Изменение будет подтверждено сервером.</p>
        {mode === 'edit' && <div className="mt-4 space-y-3">
<label className="block text-sm">Заголовок<input ref={initialFocusRef} className="input mt-1 w-full p-2" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={255} />
</label>
<label className="block text-sm">Инструкции<textarea className="input mt-1 w-full p-2" value={instructions} onChange={(event) => setInstructions(event.target.value)} maxLength={2000} />
</label>
<label className="block text-sm">Срок<input type="date" min={todayTashkentDate()} className="input mt-1 w-full p-2" value={dueDate} onChange={(event) => setDueDate(event.target.value)} />
</label>{globalRole && <label className="block text-sm">Исполнитель<select className="input mt-1 w-full p-2" value={assignedToId} onChange={(event) => setAssignedToId(event.target.value)}>
<option value="">Без исполнителя</option>{eligibleAssignees.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}</select>{!eligibleAssignees.length && <span className="mt-1 block text-xs text-agro-muted">Для предприятия подходящие исполнители пока не обнаружены. Осмотр можно оставить без исполнителя.</span>}</label>}</div>}
        {mode === 'complete' && <label className="mt-4 block text-sm">Итог осмотра<textarea ref={initialFocusRef} className="input mt-1 w-full p-2" value={text} onChange={(event) => setText(event.target.value)} minLength={10} maxLength={4000} required />
</label>}
        {mode === 'cancel' && <label className="mt-4 block text-sm">Причина отмены<textarea ref={initialFocusRef} className="input mt-1 w-full p-2" value={text} onChange={(event) => setText(event.target.value)} minLength={5} maxLength={2000} required />
</label>}
        {error && <div role="alert" className="mt-3 text-sm text-red-700">{error}{error.includes('изменён') && <button type="button" onClick={onReload} className="ml-2 underline">Обновить данные</button>}</div>}
        <div className="mt-5 flex justify-end gap-2">
<button type="button" disabled={pending} onClick={onClose} className="btn-secondary px-4 py-2">Закрыть</button>
<button ref={mode === 'start' ? initialFocusRef : undefined} disabled={!valid || pending} className="btn-primary px-4 py-2 disabled:opacity-50">{pending ? 'Выполняем…' : label}</button>
</div>
      </form>
    </div>
  );
}
