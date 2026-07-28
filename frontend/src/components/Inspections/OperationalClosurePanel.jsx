import { useCallback, useEffect, useRef, useState } from 'react';

import { getInspectionTimeline, getOperationalClosure } from '../../api/fieldInspections';
import {
  ACTION_STATUS_LABELS,
  CAUSE_LABELS,
  TIMELINE_EVENT_LABELS,
  VERIFICATION_CONFIDENCE_LABELS,
  VERIFICATION_RESULT_LABELS,
  VERIFICATION_STATUS_LABELS,
  canWriteInspections,
  displayName,
  formatDate,
  normalizeRole,
  safeArray,
  safeString,
} from './inspectionPresentation';
import OperationalWorkflowModal from './OperationalWorkflowModal';

function StatusPill({ children, tone = 'neutral' }) {
  const tones = {
    neutral: 'bg-slate-100 text-slate-700',
    good: 'bg-emerald-100 text-emerald-800',
    warning: 'bg-amber-100 text-amber-900',
    danger: 'bg-red-100 text-red-800',
    info: 'bg-sky-100 text-sky-800',
  };
  return <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${tones[tone]}`}>{children}</span>;
}

function EvidenceList({ items }) {
  if (!items.length) return <p className="text-sm text-agro-muted">Дополнительные метаданные доказательств пока не добавлены.</p>;
  return (
    <ul className="space-y-2">
      {items.map((item) => (
        <li key={item.id} className="rounded-lg border border-agro-border p-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <strong>{item.evidence_type === 'photo' ? 'Фотография' : 'Геопозиция'}</strong>
            <span className="text-xs text-agro-muted">{formatDate(item.created_at, true)}</span>
          </div>
          {item.evidence_type === 'photo' && <p className="mt-1 break-words">{safeString(item.original_filename)} · {Number.isFinite(item.byte_size) ? `${item.byte_size} байт` : 'размер не указан'}</p>}
          {item.evidence_type === 'geolocation' && <p className="mt-1 font-mono text-xs">{item.latitude}, {item.longitude}</p>}
          <p className="mt-1 text-xs text-agro-muted">Провайдер: {safeString(item.provider)}</p>
        </li>
      ))}
    </ul>
  );
}

function VerificationSummary({ verification }) {
  if (!verification) return <p className="text-sm text-agro-muted">Спутниковая проверка ещё не запрашивалась.</p>;
  const tone = verification.status === 'awaiting_observation'
    ? 'warning'
    : verification.result === 'improved'
      ? 'good'
      : verification.result === 'worsened'
        ? 'danger'
        : 'neutral';
  return (
    <div className="mt-3 rounded-lg bg-slate-50 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill tone={tone}>{VERIFICATION_STATUS_LABELS[verification.status] || verification.status}</StatusPill>
        {verification.result && <strong>{VERIFICATION_RESULT_LABELS[verification.result] || verification.result}</strong>}
      </div>
      {verification.confidence && <p className="mt-2">Уверенность данных: {VERIFICATION_CONFIDENCE_LABELS[verification.confidence] || verification.confidence}</p>}
    </div>
  );
}

function ActionCard({ action, role, userId, onWorkflow }) {
  const management = role === 'admin' || role === 'manager';
  const isOwner = Number(action?.owner?.id) === Number(userId);
  const editable = action.status !== 'closed' && (management || isOwner);
  const awaiting = action.latest_verification?.status === 'awaiting_observation';
  return (
    <article className={`rounded-xl border p-4 ${action.is_overdue ? 'border-red-300 bg-red-50/40' : 'border-agro-border'}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-semibold">{safeString(action.description)}</p>
          <p className="mt-1 text-sm text-agro-muted">Исполнитель: {displayName(action.owner)} · срок {formatDate(action.due_date)}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill tone={action.status === 'closed' ? 'good' : action.status === 'blocked' ? 'warning' : 'info'}>{ACTION_STATUS_LABELS[action.status] || action.status}</StatusPill>
          {action.is_overdue && <StatusPill tone="danger">Просрочено</StatusPill>}
        </div>
      </div>
      {action.closure_reason && <p className="mt-3 text-sm"><strong>Причина закрытия:</strong> {safeString(action.closure_reason)}</p>}
      {action.reopen_reason && <p className="mt-2 text-sm"><strong>Причина переоткрытия:</strong> {safeString(action.reopen_reason)}</p>}
      <VerificationSummary verification={action.latest_verification} />
      {(editable || management) && <div className="mt-3 flex flex-wrap gap-2">
        {editable && <button type="button" onClick={() => onWorkflow('update', action)} className="btn-secondary px-3 py-2 text-sm">Изменить</button>}
        {editable && <button type="button" onClick={() => onWorkflow('close', action)} className="btn-primary px-3 py-2 text-sm">Закрыть</button>}
        {management && action.status === 'closed' && <button type="button" onClick={() => onWorkflow('reopen', action)} className="btn-secondary px-3 py-2 text-sm">Переоткрыть</button>}
        {management && action.status === 'closed' && !awaiting && <button type="button" onClick={() => onWorkflow('request', action)} className="btn-secondary px-3 py-2 text-sm">Запросить проверку</button>}
        {management && awaiting && <button type="button" onClick={() => onWorkflow('resolve', action)} className="btn-primary px-3 py-2 text-sm">Проверить наблюдение</button>}
      </div>}
    </article>
  );
}

export default function OperationalClosurePanel({
  inspectionId,
  user,
  assignees,
  reloadToken,
  onInspectionReload,
  onModalState,
}) {
  const [closure, setClosure] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [state, setState] = useState('loading');
  const [error, setError] = useState('');
  const [refreshToken, setRefreshToken] = useState(0);
  const [workflow, setWorkflow] = useState(null);
  const generationRef = useRef(0);
  const mountedRef = useRef(false);
  const controllerRef = useRef(null);
  const role = normalizeRole(user?.role);
  const canWrite = canWriteInspections(role);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      controllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    onModalState(Boolean(workflow));
    return () => onModalState(false);
  }, [onModalState, workflow]);

  useEffect(() => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const generation = ++generationRef.current;
    setState('loading');
    setError('');
    Promise.all([
      getOperationalClosure(inspectionId, controller.signal),
      getInspectionTimeline(inspectionId, controller.signal),
    ])
      .then(([closureValue, timelineValue]) => {
        if (!mountedRef.current || controller.signal.aborted || generation !== generationRef.current) return;
        setClosure(closureValue);
        setTimeline(safeArray(timelineValue?.items));
        setState('ready');
      })
      .catch((requestError) => {
        if (!mountedRef.current || controller.signal.aborted || generation !== generationRef.current || requestError?.code === 'ERR_CANCELED') return;
        const status = requestError?.response?.status;
        setState(status === 403 ? '403' : status === 404 ? '404' : 'error');
        setError(status === 403
          ? 'Недостаточно прав для просмотра операционного цикла.'
          : status === 404
            ? 'Операционный цикл осмотра не найден.'
            : 'Не удалось загрузить операционный цикл.');
      });
    return () => controller.abort();
  }, [inspectionId, refreshToken, reloadToken]);

  const openWorkflow = useCallback((mode, action = null) => setWorkflow({ mode, action }), []);
  const closeWorkflow = useCallback(() => setWorkflow(null), []);
  const handleWorkflowSuccess = useCallback(() => {
    setWorkflow(null);
    setRefreshToken((value) => value + 1);
    onInspectionReload();
  }, [onInspectionReload]);

  if (state === 'loading') return <section className="card p-4" aria-labelledby="closure-heading"><h3 id="closure-heading" className="font-semibold">Операционный цикл</h3><p className="mt-2 text-sm" role="status">Загружаем результат, действия и проверку…</p></section>;
  if (state !== 'ready') return <section className="card p-4" aria-labelledby="closure-heading"><h3 id="closure-heading" className="font-semibold">Операционный цикл</h3><div className="mt-2 text-sm" role="alert"><p>{error}</p>{state === 'error' && <button type="button" onClick={() => setRefreshToken((value) => value + 1)} className="btn-primary mt-3 px-3 py-2">Повторить</button>}</div></section>;

  const result = closure?.result;
  const evidence = safeArray(closure?.evidence);
  const actions = safeArray(closure?.actions);
  const inspection = closure?.inspection;
  const canOperateInspection = canWrite
    && (role !== 'agronomist' || Number(inspection?.assigned_to_id) === Number(user?.id));

  return (
    <section className="card p-4" aria-labelledby="closure-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="closure-heading" className="font-semibold">Операционный цикл</h3>
          <p className="mt-1 text-sm text-agro-muted">Подтверждённая причина → действие → закрытие → следующее наблюдение.</p>
        </div>
        {!canWrite && <StatusPill>Только чтение</StatusPill>}
      </div>

      <div className="mt-4 border-t border-agro-border pt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="font-medium">Результат осмотра</h4>
          {canOperateInspection && result && <button type="button" onClick={() => openWorkflow('evidence')} className="btn-secondary px-3 py-2 text-sm">Добавить доказательство</button>}
        </div>
        {!result && <p className="mt-2 text-sm text-agro-muted">Структурированный результат появится после завершения осмотра.</p>}
        {result && <div className="mt-3 rounded-xl bg-emerald-50/60 p-4 text-sm">
          <StatusPill tone={result.cause_code === 'no_issue' ? 'good' : result.cause_code === 'unconfirmed' ? 'warning' : 'info'}>{CAUSE_LABELS[result.cause_code] || result.cause_code}</StatusPill>
          <p className="mt-3"><strong>Пояснение:</strong> {safeString(result.cause_details)}</p>
          <p className="mt-2"><strong>Заметка о доказательствах:</strong> {safeString(result.evidence_note)}</p>
          {result.latitude != null && result.longitude != null && <p className="mt-2 font-mono text-xs">{result.latitude}, {result.longitude}</p>}
          <p className="mt-2 text-xs text-agro-muted">Записано {formatDate(result.created_at, true)}</p>
        </div>}
      </div>

      <div className="mt-4 border-t border-agro-border pt-4">
        <h4 className="font-medium">Доказательства</h4>
        <div className="mt-3"><EvidenceList items={evidence} /></div>
      </div>

      <div className="mt-4 border-t border-agro-border pt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="font-medium">Корректирующие действия</h4>
          {canOperateInspection && result && <button type="button" onClick={() => openWorkflow('create')} className="btn-primary px-3 py-2 text-sm">Создать действие</button>}
        </div>
        {!actions.length && <p className="mt-3 text-sm text-agro-muted">Действия пока не назначены.</p>}
        <div className="mt-3 space-y-3">
          {actions.map((action) => <ActionCard key={action.id} action={action} role={role} userId={user?.id} onWorkflow={openWorkflow} />)}
        </div>
        <p className="mt-3 rounded-lg bg-sky-50 p-3 text-xs text-sky-900">Изменение спутникового индекса — это наблюдение, а не доказательство агрономической причинности. Решение подтверждает специалист с учётом полевых данных.</p>
      </div>

      <div className="mt-4 border-t border-agro-border pt-4">
        <h4 className="font-medium">Хронология</h4>
        {!timeline.length && <p className="mt-3 text-sm text-agro-muted">Событий пока нет.</p>}
        <ol className="mt-3 space-y-3 border-l border-agro-border pl-4">
          {timeline.map((event) => (
            <li key={`${event.id}-${event.occurred_at}-${event.event_type}`} className="relative text-sm before:absolute before:-left-[1.17rem] before:top-1.5 before:h-2 before:w-2 before:rounded-full before:bg-agro-accent">
              <p className="font-medium">{TIMELINE_EVENT_LABELS[event.event_type] || safeString(event.event_type)}</p>
              <p className="text-xs text-agro-muted">{formatDate(event.occurred_at, true)}</p>
            </li>
          ))}
        </ol>
      </div>

      {workflow && <OperationalWorkflowModal
        mode={workflow.mode}
        inspection={inspection}
        result={result}
        action={workflow.action}
        user={user}
        assignees={assignees}
        onClose={closeWorkflow}
        onSuccess={handleWorkflowSuccess}
      />}
    </section>
  );
}
