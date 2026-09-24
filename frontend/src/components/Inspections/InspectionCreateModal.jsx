import { useEffect, useMemo, useRef, useState } from 'react';

import { getFields } from '../../api/client';
import {
  createAnomalyInspection,
  getInspectionAssignees,
  workflowKey,
} from '../../api/anomalyInspections';
import {
  canonicalInspectionPriority,
  INSPECTION_PRIORITY_OPTIONS,
} from '../../config/canonicalLifecycle';
import {
  attentionInspectionReason,
  buildManualInspectionRequest,
  INSPECTION_REASON_MAX as REASON_MAX,
  INSPECTION_REASON_MIN as REASON_MIN,
  inspectionDueError,
} from '../../utils/inspectionRequests';
import { toTashkentDateTimeInput } from '../../utils/tashkentTime';
import InspectionFieldCombobox from './InspectionFieldCombobox';
import {
  positiveId,
  safeArray,
  safeErrorDetail,
} from './inspectionPresentation';

function isCancelled(error) {
  return error?.name === 'AbortError' || error?.name === 'CanceledError' || error?.code === 'ERR_CANCELED';
}

export default function InspectionCreateModal({ source, user, onClose, onSuccess }) {
  const attentionSource = source?.source === 'attention_queue';
  const [fields, setFields] = useState([]);
  const [fieldId, setFieldId] = useState(attentionSource ? String(source.field_id || '') : '');
  // The attention queue is a server ranking, not a backend-recognized inspection
  // source: the inspection is canonical `manual` and the queue context travels
  // in the visible reason text.
  const [reason, setReason] = useState(attentionSource ? attentionInspectionReason(source) : '');
  const [priority, setPriority] = useState(attentionSource ? canonicalInspectionPriority(source.priority) : 'normal');
  const [dueAt, setDueAt] = useState('');
  const [dueTouched, setDueTouched] = useState(false);
  const [assignedToId, setAssignedToId] = useState('');
  const [assignees, setAssignees] = useState({ state: 'idle', items: [] });
  const [pending, setPending] = useState(false);
  const [loadingFields, setLoadingFields] = useState(!attentionSource);
  const [error, setError] = useState('');
  const idempotencyKeyRef = useRef(workflowKey('create-inspection'));
  const frozenPayloadRef = useRef(null);
  const controllerRef = useRef(null);
  const fieldsControllerRef = useRef(null);
  const assigneesControllerRef = useRef(null);
  const mountedRef = useRef(false);
  const requestGenerationRef = useRef(0);
  const initialFocusRef = useRef(null);
  const canCreate = ['admin', 'manager'].includes(String(user?.role || '').toLowerCase());

  useEffect(() => {
    mountedRef.current = true;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    initialFocusRef.current?.focus();
    return () => {
      mountedRef.current = false;
      requestGenerationRef.current += 1;
      controllerRef.current?.abort();
      fieldsControllerRef.current?.abort();
      assigneesControllerRef.current?.abort();
      document.body.style.overflow = previousOverflow;
      idempotencyKeyRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (attentionSource) return undefined;
    const controller = new AbortController();
    fieldsControllerRef.current = controller;
    getFields({}, controller.signal)
      .then((result) => {
        if (!mountedRef.current || controller.signal.aborted) return;
        const discovered = Array.isArray(result) ? result : safeArray(result?.items);
        const unique = new Map();
        discovered.forEach((field) => {
          const id = positiveId(field?.id);
          if (id && !unique.has(id)) unique.set(id, field);
        });
        setFields(Array.from(unique.values()));
      })
      .catch((requestError) => {
        if (!mountedRef.current || controller.signal.aborted || isCancelled(requestError)) return;
        setError('Не удалось загрузить поля.');
      })
      .finally(() => {
        if (mountedRef.current && !controller.signal.aborted) setLoadingFields(false);
      });
    return () => controller.abort();
  }, [attentionSource]);

  // Canonical assignees are the active agronomists of the field's enterprise.
  useEffect(() => {
    assigneesControllerRef.current?.abort();
    setAssignedToId('');
    const id = positiveId(fieldId);
    if (!id || !canCreate) {
      setAssignees({ state: 'idle', items: [] });
      return undefined;
    }
    const controller = new AbortController();
    assigneesControllerRef.current = controller;
    setAssignees({ state: 'loading', items: [] });
    getInspectionAssignees({ fieldId: id }, controller.signal)
      .then((items) => {
        if (!mountedRef.current || controller.signal.aborted) return;
        setAssignees({ state: 'ready', items: safeArray(items).filter((item) => positiveId(item?.id)) });
      })
      .catch((requestError) => {
        if (!mountedRef.current || controller.signal.aborted || isCancelled(requestError)) return;
        setAssignees({ state: 'error', items: [] });
      });
    return () => controller.abort();
  }, [canCreate, fieldId]);

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

  const trimmedReason = reason.trim();
  const dueError = inspectionDueError(dueAt);
  const request = useMemo(
    () => buildManualInspectionRequest({ fieldId, reason, priority, dueInput: dueAt, assignedToId }),
    [assignedToId, dueAt, fieldId, priority, reason],
  );
  const payload = request.payload;
  const serializedPayload = JSON.stringify(payload || null);
  const reasonValid = trimmedReason.length >= REASON_MIN && trimmedReason.length <= REASON_MAX;
  const valid = canCreate && Boolean(payload);

  async function submit(event) {
    event.preventDefault();
    setDueTouched(true);
    if (!valid || !payload || pending) return;
    if (!idempotencyKeyRef.current) {
      idempotencyKeyRef.current = workflowKey('create-inspection');
      frozenPayloadRef.current = null;
    }
    if (frozenPayloadRef.current && frozenPayloadRef.current !== serializedPayload) {
      idempotencyKeyRef.current = workflowKey('create-inspection');
      frozenPayloadRef.current = null;
    }
    frozenPayloadRef.current = serializedPayload;
    const controller = new AbortController();
    controllerRef.current = controller;
    const generation = ++requestGenerationRef.current;
    setPending(true);
    setError('');
    try {
      const result = await createAnomalyInspection(payload, idempotencyKeyRef.current, controller.signal);
      if (!mountedRef.current || controller.signal.aborted || generation !== requestGenerationRef.current) return;
      idempotencyKeyRef.current = null;
      onSuccess(result?.inspection);
    } catch (requestError) {
      if (!mountedRef.current || controller.signal.aborted || generation !== requestGenerationRef.current || isCancelled(requestError)) return;
      const status = requestError?.response?.status;
      if (status === 403) setError('У вашей роли нет права создавать осмотр.');
      else if (status === 404) setError('Поле не найдено или недоступно.');
      else if (status === 409) setError(safeErrorDetail(requestError) || 'Запрос конфликтует с уже выполненным. Обновите очередь осмотров.');
      else if (status === 422) setError('Проверьте поле, причину, срок и исполнителя.');
      else setError('Результат запроса неизвестен. Можно повторить тот же запрос.');
    } finally {
      if (mountedRef.current && !controller.signal.aborted && generation === requestGenerationRef.current) setPending(false);
    }
  }

  const showDueError = (dueTouched || dueAt) && dueError;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-3" role="dialog" aria-modal="true" aria-labelledby="create-title">
      <form onSubmit={submit} noValidate className="card max-h-[calc(100vh-1.5rem)] w-full max-w-xl overflow-y-auto p-5">
        <div className="flex justify-between">
          <h2 id="create-title" className="text-lg font-bold">Создать осмотр</h2>
          <button type="button" onClick={onClose} disabled={pending} aria-label="Закрыть" className="rounded-lg p-2 focus:outline-none focus:ring-2 focus:ring-agro-accent">✕</button>
        </div>
        <p className="mt-2 text-sm text-agro-muted">Осмотр создаётся в каноническом процессе: поле, причина, приоритет и срок обязательны.</p>
        {!canCreate && <p role="alert" className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">Создавать осмотры могут менеджер или администратор.</p>}
        <div className="mt-4 space-y-3">
          {attentionSource ? (
            <div className="rounded-lg bg-agro-surface2 p-3 text-sm">
              <p>Поле: {source.field_name || '—'}</p>
              <p>Источник: очередь внимания (сохраняется в причине осмотра)</p>
            </div>
          ) : <InspectionFieldCombobox fields={fields} value={fieldId} onChange={setFieldId} loading={loadingFields} disabled={pending} inputRef={initialFocusRef} />}
          <label className="block text-sm">Причина осмотра<span aria-hidden="true"> *</span>
            <textarea ref={attentionSource ? initialFocusRef : undefined} required minLength={REASON_MIN} maxLength={REASON_MAX} rows={4} className="input mt-1 w-full p-2" value={reason} onChange={(event) => setReason(event.target.value)} aria-invalid={Boolean(reason) && !reasonValid} />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">Приоритет
              <select className="input mt-1 min-h-11 w-full p-2" value={priority} onChange={(event) => setPriority(event.target.value)}>
                {INSPECTION_PRIORITY_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label className="block text-sm">Срок (Ташкент)<span aria-hidden="true"> *</span>
              <input type="datetime-local" required min={toTashkentDateTimeInput()} className="input mt-1 min-h-11 w-full p-2" value={dueAt} onChange={(event) => setDueAt(event.target.value)} onBlur={() => setDueTouched(true)} aria-invalid={Boolean(showDueError)} aria-describedby="create-due-help" />
              <span id="create-due-help" className={`mt-1 block text-xs ${showDueError ? 'text-red-700' : 'text-agro-muted'}`}>{showDueError || 'Обязательно. Время Asia/Tashkent (UTC+05:00).'}</span>
            </label>
          </div>
          {canCreate && (
            <label className="block text-sm">Агроном (необязательно)
              <select className="input mt-1 min-h-11 w-full p-2" value={assignedToId} onChange={(event) => setAssignedToId(event.target.value)} disabled={assignees.state === 'loading' || !positiveId(fieldId)}>
                <option value="">Без исполнителя</option>
                {assignees.items.map((option) => <option key={option.id} value={option.id}>{option.full_name || option.email || `Агроном #${option.id}`}</option>)}
              </select>
              {assignees.state === 'error' && <span className="mt-1 block text-xs text-red-700">Не удалось загрузить агрономов. Осмотр можно создать без исполнителя.</span>}
              {assignees.state === 'ready' && !assignees.items.length && <span className="mt-1 block text-xs text-agro-muted">В предприятии поля нет активных агрономов. Осмотр можно создать без исполнителя.</span>}
            </label>
          )}
        </div>
        {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={pending} className="btn-secondary min-h-11 px-4 py-2">Закрыть</button>
          <button type="submit" disabled={!valid || pending} className="btn-primary min-h-11 px-4 py-2 disabled:opacity-50">{pending ? 'Создаём…' : frozenPayloadRef.current ? 'Повторить тот же запрос' : 'Создать осмотр'}</button>
        </div>
      </form>
    </div>
  );
}
