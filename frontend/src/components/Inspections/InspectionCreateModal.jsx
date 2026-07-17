import { useEffect, useMemo, useRef, useState } from 'react';

import { createFieldInspection, listInspectionFields } from '../../api/fieldInspections';
import {
  createIdempotencyKey,
  filterAssigneesForEnterprise,
  normalizeRole,
  positiveId,
  safeArray,
  safeErrorDetail,
  todayTashkentDate,
} from './inspectionPresentation';

function isCancelled(error) {
  return error?.name === 'AbortError' || error?.code === 'ERR_CANCELED';
}

export default function InspectionCreateModal({ source, user, assignees, onClose, onSuccess }) {
  const attentionSource = source?.source === 'attention_queue';
  const role = normalizeRole(user?.role);
  const globalRole = role === 'admin' || role === 'manager';
  const [fields, setFields] = useState([]);
  const [fieldId, setFieldId] = useState(attentionSource ? String(source.field_id || '') : '');
  const [title, setTitle] = useState(attentionSource ? `Осмотр: ${source.field_name || 'поле'}` : '');
  const [instructions, setInstructions] = useState(attentionSource ? safeArray(source.recommended_checks).join('\n').slice(0, 2000) : '');
  const [dueDate, setDueDate] = useState('');
  const [assignedToId, setAssignedToId] = useState('');
  const [pending, setPending] = useState(false);
  const [loadingFields, setLoadingFields] = useState(!attentionSource);
  const [error, setError] = useState('');
  const [conflictId, setConflictId] = useState(null);
  const idempotencyKeyRef = useRef(createIdempotencyKey());
  const frozenPayloadRef = useRef(null);
  const controllerRef = useRef(null);
  const mountedRef = useRef(false);
  const requestGenerationRef = useRef(0);
  const initialFocusRef = useRef(null);

  useEffect(() => {
    mountedRef.current = true;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    initialFocusRef.current?.focus();
    return () => {
      mountedRef.current = false;
      requestGenerationRef.current += 1;
      controllerRef.current?.abort();
      document.body.style.overflow = previousOverflow;
      idempotencyKeyRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (attentionSource) return undefined;
    const controller = new AbortController();
    controllerRef.current = controller;
    const generation = ++requestGenerationRef.current;
    listInspectionFields(controller.signal)
      .then((result) => {
        if (!mountedRef.current || controller.signal.aborted || generation !== requestGenerationRef.current) return;
        const discovered = Array.isArray(result) ? result : safeArray(result?.items);
        const unique = new Map();
        discovered.forEach((field) => {
          const id = positiveId(field?.id);
          if (id && !unique.has(id)) unique.set(id, field);
        });
        setFields(Array.from(unique.values()));
      })
      .catch((requestError) => {
        if (!mountedRef.current || controller.signal.aborted || generation !== requestGenerationRef.current || isCancelled(requestError)) return;
        setError('Не удалось загрузить поля.');
      })
      .finally(() => {
        if (mountedRef.current && !controller.signal.aborted && generation === requestGenerationRef.current) setLoadingFields(false);
      });
    return () => controller.abort();
  }, [attentionSource]);

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

  const selectedField = fields.find((field) => positiveId(field?.id) === positiveId(fieldId));
  const enterpriseId = attentionSource ? positiveId(source?.enterprise_id) : positiveId(selectedField?.enterprise_id);
  const eligibleAssignees = useMemo(
    () => filterAssigneesForEnterprise(assignees, enterpriseId),
    [assignees, enterpriseId],
  );

  useEffect(() => {
    if (assignedToId && !eligibleAssignees.some((option) => String(option.id) === assignedToId)) setAssignedToId('');
  }, [assignedToId, eligibleAssignees]);

  const payload = useMemo(() => {
    const result = {
      field_id: Number(fieldId), source: attentionSource ? 'attention_queue' : 'manual', title: title.trim(),
      instructions: instructions.trim() || null, due_date: dueDate || null,
    };
    if (globalRole && assignedToId) result.assigned_to_id = Number(assignedToId);
    if (attentionSource) {
      result.source_priority = source.priority;
      result.source_attention_score = source.attention_score;
      result.source_observation_date = source.observation_date || null;
      result.source_reason_codes = safeArray(source.reason_codes);
    }
    return result;
  }, [assignedToId, attentionSource, dueDate, fieldId, globalRole, instructions, source, title]);

  const serializedPayload = JSON.stringify(payload);
  const valid = positiveId(payload.field_id) && payload.title.length >= 3 && payload.title.length <= 255
    && (!payload.instructions || (payload.instructions.length >= 3 && payload.instructions.length <= 2000))
    && (!dueDate || dueDate >= todayTashkentDate())
    && (!attentionSource || payload.source_reason_codes.length > 0);

  async function submit(event) {
    event.preventDefault();
    if (!valid || pending) return;
    if (frozenPayloadRef.current && frozenPayloadRef.current !== serializedPayload) {
      idempotencyKeyRef.current = createIdempotencyKey();
      frozenPayloadRef.current = null;
    }
    frozenPayloadRef.current = serializedPayload;
    const controller = new AbortController();
    controllerRef.current = controller;
    const generation = ++requestGenerationRef.current;
    setPending(true);
    setError('');
    setConflictId(null);
    try {
      const result = await createFieldInspection(payload, idempotencyKeyRef.current, controller.signal);
      if (!mountedRef.current || controller.signal.aborted || generation !== requestGenerationRef.current) return;
      idempotencyKeyRef.current = null;
      onSuccess(result?.inspection);
    } catch (requestError) {
      if (!mountedRef.current || controller.signal.aborted || generation !== requestGenerationRef.current || isCancelled(requestError)) return;
      const status = requestError?.response?.status;
      if (status === 409) {
        setError(safeErrorDetail(requestError) || 'Для поля уже существует активный осмотр.');
        const match = String(requestError?.response?.data?.detail || '').match(/(?:inspection|осмотр)[^0-9]{0,20}(\d+)/i);
        setConflictId(positiveId(match?.[1]));
      } else if (status === 422) setError('Проверьте параметры запроса.');
      else setError('Результат запроса неизвестен. Можно повторить тот же запрос.');
    } finally {
      if (mountedRef.current && !controller.signal.aborted && generation === requestGenerationRef.current) setPending(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-3" role="dialog" aria-modal="true" aria-labelledby="create-title">
      <form onSubmit={submit} className="card max-h-[calc(100vh-1.5rem)] w-full max-w-xl overflow-y-auto p-5">
        <div className="flex justify-between">
<h2 id="create-title" className="text-lg font-bold">Создать осмотр</h2>
<button type="button" onClick={onClose} disabled={pending} aria-label="Закрыть" className="rounded-lg p-2 focus:outline-none focus:ring-2 focus:ring-agro-accent">✕</button>
</div>
        <p className="mt-2 text-sm text-agro-muted">Укажите поле, задачу и срок полевой проверки.</p>
        <div className="mt-4 space-y-3">
          {attentionSource ? <div className="rounded-lg bg-agro-surface2 p-3 text-sm">
<p>Поле: {source.field_name || '—'}</p>
<p>Приоритет: {source.priority || '—'} · Индекс внимания: {Number.isFinite(source.attention_score) ? source.attention_score : '—'}</p>
<p>Дата наблюдения: {source.observation_date || '—'}</p>
<p>Причины: {safeArray(source.reason_codes).join(', ') || '—'}</p>
</div> : <label className="block text-sm">Поле<select ref={initialFocusRef} required disabled={loadingFields} className="input mt-1 w-full p-2" value={fieldId} onChange={(event) => setFieldId(event.target.value)}>
<option value="">{loadingFields ? 'Загружаем поля…' : 'Выберите поле'}</option>{fields.map((field) => positiveId(field?.id) && field?.name ? <option key={field.id} value={field.id}>{field.name}</option> : null)}</select>
</label>}
          <label className="block text-sm">Заголовок<input ref={attentionSource ? initialFocusRef : undefined} required minLength={3} maxLength={255} className="input mt-1 w-full p-2" value={title} onChange={(event) => setTitle(event.target.value)} />
</label>
          <label className="block text-sm">Инструкции<textarea minLength={3} maxLength={2000} className="input mt-1 w-full p-2" value={instructions} onChange={(event) => setInstructions(event.target.value)} />
</label>
          <label className="block text-sm">Срок<input type="date" min={todayTashkentDate()} className="input mt-1 w-full p-2" value={dueDate} onChange={(event) => setDueDate(event.target.value)} />
</label>
          {role === 'agronomist' ? <p className="text-sm text-agro-muted">Осмотр будет назначен вам.</p> : globalRole && <label className="block text-sm">Исполнитель<select className="input mt-1 w-full p-2" value={assignedToId} onChange={(event) => setAssignedToId(event.target.value)}>
<option value="">Без исполнителя</option>{eligibleAssignees.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}</select>{!eligibleAssignees.length && <span className="mt-1 block text-xs text-agro-muted">Для выбранного предприятия подходящие исполнители пока не обнаружены. Осмотр можно оставить без исполнителя.</span>}</label>}
        </div>
        {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
        {conflictId && <button type="button" onClick={() => onSuccess({ id: conflictId }, true)} className="mt-2 text-sm text-agro-accent underline">Открыть существующий осмотр</button>}
        <div className="mt-5 flex justify-end gap-2">
<button type="button" onClick={onClose} disabled={pending} className="btn-secondary px-4 py-2">Закрыть</button>
<button disabled={!valid || pending} className="btn-primary px-4 py-2 disabled:opacity-50">{pending ? 'Создаём…' : frozenPayloadRef.current ? 'Повторить тот же запрос' : 'Создать осмотр'}</button>
</div>
      </form>
    </div>
  );
}
