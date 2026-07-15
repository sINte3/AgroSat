import { useEffect, useRef, useState } from 'react';

import { getFieldInspection } from '../../api/fieldInspections';
import InspectionCard from './InspectionCard';
import { formatDate, safeArray, safeString } from './inspectionPresentation';

function DetailContent({ item, user, onNavigate, onAction }) {
  return (
    <div className="space-y-4">
      <InspectionCard inspection={item} user={user} onNavigate={onNavigate} onAction={onAction} compact />
      <section className="card space-y-2 p-4 text-sm">
        <h3 className="font-semibold">Подробности</h3>
        <p><b>Инструкции:</b> {safeString(item.instructions)}</p>
        <p>
          <b>Сводка источника:</b> {item.source_priority || '—'} ·{' '}
          {Number.isFinite(item.source_attention_score) ? item.source_attention_score : '—'} ·{' '}
          {formatDate(item.source_observation_date)}
        </p>
        <p><b>Коды причин:</b> {safeArray(item.source_reason_codes).filter((value) => typeof value === 'string').join(', ') || '—'}</p>
        <p><b>Итог осмотра:</b> {safeString(item.completion_summary)}</p>
        <p><b>Причина отмены:</b> {safeString(item.cancellation_reason)}</p>
      </section>
    </div>
  );
}

export default function InspectionDetailDrawer({
  id,
  user,
  onNavigate,
  onClose,
  onAction,
  reloadToken,
  onRetry,
  onLoaded,
  suspendEscape = false,
}) {
  const [item, setItem] = useState(null);
  const [state, setState] = useState('loading');
  const [error, setError] = useState('');
  const generationRef = useRef(0);
  const mountedRef = useRef(false);
  const controllerRef = useRef(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      controllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const generation = ++generationRef.current;
    setState('loading');
    setError('');

    getFieldInspection(id, controller.signal)
      .then((value) => {
        if (!mountedRef.current || controller.signal.aborted || generation !== generationRef.current) return;
        setItem(value);
        setState('ready');
        onLoaded?.(value);
      })
      .catch((requestError) => {
        if (!mountedRef.current || controller.signal.aborted || generation !== generationRef.current || requestError?.code === 'ERR_CANCELED') return;
        const status = requestError?.response?.status;
        setState(status === 403 ? '403' : status === 404 ? '404' : 'error');
        setError('Не удалось загрузить осмотр.');
      });

    return () => controller.abort();
  }, [id, reloadToken, onLoaded]);

  useEffect(() => {
    const handleEscape = (event) => {
      if (event.key === 'Escape' && !suspendEscape) onClose();
    };
    document.addEventListener('keydown', handleEscape);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handleEscape);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose, suspendEscape]);

  return (
    <div className="fixed inset-0 z-40 bg-black/30" onMouseDown={(event) => event.target === event.currentTarget && !suspendEscape && onClose()}>
      <aside className="ml-auto flex h-full w-full max-w-2xl flex-col bg-white shadow-xl" role="dialog" aria-modal="true" aria-label={`Осмотр #${id}`}>
        <div className="flex items-center justify-between border-b border-agro-border p-4">
          <h2 className="font-bold">Осмотр #{id}</h2>
          <button type="button" autoFocus onClick={onClose} className="rounded p-2 focus:ring-2 focus:ring-agro-accent" aria-label="Закрыть осмотр">✕</button>
        </div>
        <div className="flex-1 overflow-y-auto overflow-x-hidden p-4" aria-live="polite">
          {state === 'loading' && <p>Загружаем осмотр…</p>}
          {state === '403' && <p role="alert">Недостаточно прав для просмотра осмотра.</p>}
          {state === '404' && <p role="alert">Осмотр не найден или недоступен.</p>}
          {state === 'error' && <div role="alert"><p>{error}</p><button type="button" onClick={onRetry} className="btn-primary mt-3 px-3 py-2">Повторить</button></div>}
          {state === 'ready' && item && <DetailContent item={item} user={user} onNavigate={onNavigate} onAction={onAction} />}
        </div>
      </aside>
    </div>
  );
}
