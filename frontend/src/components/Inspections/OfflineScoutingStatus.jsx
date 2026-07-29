import { useCallback, useEffect, useRef, useState } from 'react';

import { listOfflineQueue, offlineScope } from '../../offline/offlineScoutingStore.js';
import { syncOfflineQueue } from '../../offline/offlineScoutingSync.js';
import { normalizeRole } from './inspectionPresentation';

const LABELS = {
  queued: 'ожидает синхронизации',
  syncing: 'синхронизируется',
  conflict: 'конфликт',
  failed: 'требует исправления',
};

export default function OfflineScoutingStatus({ user, onSynchronized }) {
  const scope = offlineScope(user);
  const enabled = normalizeRole(user?.role) === 'agronomist' && Boolean(scope);
  const [items, setItems] = useState([]);
  const [online, setOnline] = useState(() => navigator.onLine);
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState('');
  const controllerRef = useRef(null);
  const mountedRef = useRef(false);

  const reload = useCallback(async () => {
    if (!enabled) return;
    try {
      const value = await listOfflineQueue(scope);
      if (mountedRef.current) setItems(value);
    } catch {
      if (mountedRef.current) setMessage('Локальное хранилище недоступно.');
    }
  }, [enabled, scope]);

  useEffect(() => {
    mountedRef.current = true;
    void reload();
    const handleOnline = () => setOnline(true);
    const handleOffline = () => setOnline(false);
    const handleChanged = () => void reload();
    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);
    window.addEventListener('agrosat:offline-queue-changed', handleChanged);
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
      window.removeEventListener('agrosat:offline-queue-changed', handleChanged);
    };
  }, [reload]);

  if (!enabled) return null;

  const conflicts = items.filter((item) => item.status === 'conflict').length;
  const failures = items.filter((item) => item.status === 'failed').length;

  async function synchronize() {
    if (!online || syncing || !items.length) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setSyncing(true);
    setMessage('Синхронизируем сохранённые изменения…');
    try {
      const results = await syncOfflineQueue(scope, controller.signal);
      if (controller.signal.aborted || !mountedRef.current) return;
      const synchronized = results.filter((item) => item.status === 'synchronized').length;
      const conflictCount = results.filter((item) => item.status === 'conflict').length;
      setMessage(
        conflictCount
          ? `Синхронизировано: ${synchronized}. Конфликтов: ${conflictCount}.`
          : `Синхронизировано: ${synchronized}.`,
      );
      await reload();
      window.dispatchEvent(new Event('agrosat:offline-queue-changed'));
      if (synchronized) onSynchronized?.();
    } catch (error) {
      if (error?.name !== 'AbortError' && error?.code !== 'ERR_CANCELED' && mountedRef.current) {
        setMessage('Синхронизация прервана. Черновики сохранены для повторной попытки.');
      }
    } finally {
      if (!controller.signal.aborted && mountedRef.current) setSyncing(false);
    }
  }

  return (
    <section
      className={`rounded-xl border p-4 ${
        online ? 'border-sky-200 bg-sky-50' : 'border-amber-300 bg-amber-50'
      }`}
      aria-labelledby="offline-scouting-status-title"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="offline-scouting-status-title" className="font-semibold text-agro-text">
            Полевой режим · {online ? 'сеть доступна' : 'без сети'}
          </h2>
          <p className="mt-1 text-sm text-agro-muted">
            Несинхронизировано: {items.length}
            {conflicts ? ` · конфликтов: ${conflicts}` : ''}
            {failures ? ` · ошибок: ${failures}` : ''}
          </p>
        </div>
        <button
          type="button"
          onClick={synchronize}
          disabled={!online || syncing || !items.length}
          className="btn-primary min-h-11 px-4 py-2 disabled:opacity-50"
        >
          {syncing ? 'Синхронизируем…' : 'Синхронизировать'}
        </button>
      </div>
      {!online && (
        <p className="mt-2 text-sm text-amber-950">
          Отправка не начнётся автоматически. Подключитесь к сети и запустите её вручную.
        </p>
      )}
      {items.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-agro-muted">
          {items.slice(0, 5).map((item) => (
            <li key={item.key}>
              Осмотр #{item.inspectionId}: {LABELS[item.status] || item.status}
            </li>
          ))}
        </ul>
      )}
      {message && <p className="mt-2 text-sm" aria-live="polite">{message}</p>}
    </section>
  );
}
