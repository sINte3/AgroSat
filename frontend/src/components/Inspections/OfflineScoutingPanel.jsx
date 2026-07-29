import { useEffect, useMemo, useRef, useState } from 'react';

import {
  createOfflineDraft,
  discardOfflineDraft,
  enqueueOfflineDraft,
  getOfflineDraft,
  listOfflineQueue,
  offlineScope,
  saveOfflineDraft,
} from '../../offline/offlineScoutingStore.js';
import { syncOfflineQueueItem } from '../../offline/offlineScoutingSync.js';
import {
  CAUSE_LABELS,
  normalizeRole,
  todayTashkentDate,
} from './inspectionPresentation';

const MAX_PHOTO_BYTES = 25 * 1024 * 1024;
const QUEUE_STATUS_LABELS = {
  queued: 'ожидает синхронизации',
  syncing: 'синхронизируется',
  conflict: 'конфликт версии',
  failed: 'требует исправления',
};

const emptyForm = () => ({
  causeCode: 'unconfirmed',
  causeDetails: '',
  evidenceNote: '',
  includeResultLocation: false,
  resultLatitude: '',
  resultLongitude: '',
  evidenceType: 'none',
  evidenceLatitude: '',
  evidenceLongitude: '',
  photo: null,
  createAction: false,
  actionDescription: '',
  actionDueDate: '',
});

const pairValid = (latitude, longitude) => {
  if (latitude === '' && longitude === '') return true;
  const lat = Number(latitude);
  const lon = Number(longitude);
  return latitude !== '' && longitude !== ''
    && Number.isFinite(lat) && lat >= -90 && lat <= 90
    && Number.isFinite(lon) && lon >= -180 && lon <= 180;
};

function draftToForm(draft) {
  const evidence = draft?.evidence?.[0];
  return {
    causeCode: draft.result.cause_code,
    causeDetails: draft.result.cause_details || '',
    evidenceNote: draft.result.evidence_note || '',
    includeResultLocation: draft.result.latitude != null,
    resultLatitude: draft.result.latitude == null ? '' : String(draft.result.latitude),
    resultLongitude: draft.result.longitude == null ? '' : String(draft.result.longitude),
    evidenceType: evidence?.evidence_type || 'none',
    evidenceLatitude: evidence?.latitude == null ? '' : String(evidence.latitude),
    evidenceLongitude: evidence?.longitude == null ? '' : String(evidence.longitude),
    photo: evidence?.evidence_type === 'photo' ? {
      original_filename: evidence.original_filename,
      media_type: evidence.media_type,
      byte_size: evidence.byte_size,
      sha256: evidence.sha256,
      captured_at: evidence.captured_at,
    } : null,
    createAction: Boolean(draft.action),
    actionDescription: draft.action?.description || '',
    actionDueDate: draft.action?.due_date || '',
  };
}

async function photoMetadata(file) {
  if (!file || file.size > MAX_PHOTO_BYTES) throw new Error('Размер фотографии превышает 25 МиБ.');
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  const sha256 = Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
  return {
    original_filename: file.name.replace(/^.*[\\/]/, '').slice(0, 255),
    media_type: (file.type || 'application/octet-stream').slice(0, 100),
    byte_size: file.size,
    sha256,
    captured_at: new Date(file.lastModified || Date.now()).toISOString(),
  };
}

export default function OfflineScoutingPanel({
  inspection,
  user,
  externalNotice = '',
  onSynchronized,
}) {
  const scope = offlineScope(user);
  const role = normalizeRole(user?.role);
  const assigned = Number(inspection?.assigned_to?.id) === Number(user?.id);
  const enabled = role === 'agronomist' && assigned && inspection?.status === 'in_progress' && scope;
  const [form, setForm] = useState(emptyForm);
  const [queueItem, setQueueItem] = useState(null);
  const [state, setState] = useState('loading');
  const [online, setOnline] = useState(() => navigator.onLine);
  const [message, setMessage] = useState('');
  const [processingPhoto, setProcessingPhoto] = useState(false);
  const generationRef = useRef(0);
  const controllerRef = useRef(null);

  useEffect(() => {
    const handleOnline = () => setOnline(true);
    const handleOffline = () => setOnline(false);
    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);
    return () => {
      controllerRef.current?.abort();
      generationRef.current += 1;
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  useEffect(() => {
    if (!enabled) {
      setState('unavailable');
      return undefined;
    }
    const generation = ++generationRef.current;
    setState('loading');
    Promise.all([
      getOfflineDraft(scope, inspection.id),
      listOfflineQueue(scope),
    ])
      .then(([draft, queue]) => {
        if (generation !== generationRef.current) return;
        if (draft) setForm(draftToForm(draft));
        setQueueItem(queue.find((item) => item.inspectionId === inspection.id) || null);
        setState('ready');
      })
      .catch(() => {
        if (generation !== generationRef.current) return;
        setState('error');
        setMessage('Локальное хранилище недоступно.');
      });
    return () => {
      generationRef.current += 1;
    };
  }, [enabled, inspection?.id, scope]);

  const valid = useMemo(() => {
    if (!enabled || processingPhoto) return false;
    const detailsValid = !form.causeDetails.trim()
      || (form.causeDetails.trim().length >= 3 && form.causeDetails.trim().length <= 2000);
    const noteValid = !form.evidenceNote.trim()
      || (form.evidenceNote.trim().length >= 3 && form.evidenceNote.trim().length <= 4000);
    const causeValid = form.causeCode !== 'other' || form.causeDetails.trim().length >= 3;
    const resultLocationValid = !form.includeResultLocation
      || pairValid(form.resultLatitude, form.resultLongitude);
    const evidenceValid = form.evidenceType === 'none'
      || (form.evidenceType === 'geolocation'
        ? pairValid(form.evidenceLatitude, form.evidenceLongitude)
          && form.evidenceLatitude !== ''
        : Boolean(form.photo));
    const actionValid = !form.createAction
      || (
        form.actionDescription.trim().length >= 5
        && form.actionDescription.trim().length <= 4000
        && form.actionDueDate >= todayTashkentDate()
      );
    return detailsValid && noteValid && causeValid
      && resultLocationValid && evidenceValid && actionValid;
  }, [enabled, form, processingPhoto]);

  if (!enabled) {
    return role === 'agronomist' ? (
      <section className="card p-4" aria-labelledby="offline-draft-title">
        <h3 id="offline-draft-title" className="font-semibold">Офлайн-черновик</h3>
        <p className="mt-2 text-sm text-agro-muted">
          Черновик доступен назначенному агроному для осмотра в работе.
        </p>
      </section>
    ) : null;
  }

  function setValue(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function buildDraft() {
    const result = {
      cause_code: form.causeCode,
      cause_details: form.causeDetails.trim() || null,
      evidence_note: form.evidenceNote.trim() || null,
    };
    if (form.includeResultLocation) {
      result.latitude = Number(form.resultLatitude);
      result.longitude = Number(form.resultLongitude);
    }
    const evidence = [];
    if (form.evidenceType === 'geolocation') {
      evidence.push({
        evidence_type: 'geolocation',
        provider: 'metadata_only',
        provider_metadata: { source: 'offline_scouting' },
        latitude: Number(form.evidenceLatitude),
        longitude: Number(form.evidenceLongitude),
      });
    } else if (form.evidenceType === 'photo' && form.photo) {
      evidence.push({
        evidence_type: 'photo',
        provider: 'metadata_only',
        provider_metadata: { binary_stored: false, source: 'offline_scouting' },
        ...form.photo,
      });
    }
    const action = form.createAction ? {
      owner_id: Number(user.id),
      description: form.actionDescription.trim(),
      due_date: form.actionDueDate,
    } : null;
    const existingIdempotency = queueItem?.idempotency;
    const draft = createOfflineDraft({ scope, inspection, result, evidence, action });
    if (existingIdempotency) draft.idempotency = existingIdempotency;
    return draft;
  }

  async function save(queue = false) {
    if (!valid) return;
    setMessage('');
    try {
      const draft = buildDraft();
      await saveOfflineDraft(draft);
      const queued = queue ? await enqueueOfflineDraft(draft) : null;
      setQueueItem(queued);
      setMessage(queue
        ? 'Изменения сохранены локально и ожидают ручной синхронизации.'
        : 'Черновик сохранён только на этом устройстве.');
      window.dispatchEvent(new Event('agrosat:offline-queue-changed'));
    } catch {
      setMessage('Не удалось сохранить локальный черновик.');
    }
  }

  async function synchronize() {
    if (!queueItem || !online || state === 'syncing') return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState('syncing');
    setMessage('Синхронизируем осмотр…');
    try {
      const result = await syncOfflineQueueItem(queueItem, controller.signal);
      if (controller.signal.aborted) return;
      if (result.status === 'synchronized') {
        setQueueItem(null);
        setForm(emptyForm());
        setMessage('Все изменения подтверждены сервером.');
        onSynchronized?.();
      } else if (result.status === 'conflict') {
        const refreshed = (await listOfflineQueue(scope))
          .find((item) => item.inspectionId === inspection.id);
        setQueueItem(refreshed || queueItem);
        setMessage('Конфликт: осмотр изменён на сервере. Обновите данные; черновик не перезаписан.');
      } else if (result.retryable) {
        setMessage('Сеть или сервер недоступны. Очередь сохранена для повторной попытки.');
      } else {
        setMessage('Сервер отклонил черновик. Проверьте права и поля перед повтором.');
      }
      window.dispatchEvent(new Event('agrosat:offline-queue-changed'));
    } catch (error) {
      if (error?.name !== 'AbortError' && error?.code !== 'ERR_CANCELED') {
        setMessage('Синхронизация прервана. Очередь сохранена.');
      }
    } finally {
      if (!controller.signal.aborted) setState('ready');
    }
  }

  async function discard() {
    await discardOfflineDraft(scope, inspection.id);
    setQueueItem(null);
    setForm(emptyForm());
    setMessage('Локальный черновик удалён.');
    window.dispatchEvent(new Event('agrosat:offline-queue-changed'));
  }

  async function selectPhoto(event) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setProcessingPhoto(true);
    setMessage('');
    try {
      setValue('photo', await photoMetadata(file));
    } catch (error) {
      setValue('photo', null);
      setMessage(error?.message || 'Не удалось прочитать метаданные фотографии.');
    } finally {
      setProcessingPhoto(false);
    }
  }

  return (
    <section className="card p-4" aria-labelledby="offline-draft-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="offline-draft-title" className="font-semibold">Офлайн-черновик осмотра</h3>
          <p className="mt-1 text-sm text-agro-muted">
            {queueItem
              ? `Статус: ${QUEUE_STATUS_LABELS[queueItem.status] || 'неизвестен'}. Изменения ещё не являются серверными данными.`
              : 'Сохраните черновик или поставьте его в очередь. Отправка всегда запускается вручную.'}
          </p>
        </div>
        <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${
          online ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-900'
        }`}>
          {online ? 'Сеть доступна' : 'Без сети'}
        </span>
      </div>

      {state === 'loading' ? <p className="mt-3 text-sm" role="status">Загружаем локальный черновик…</p> : (
        <form className="mt-4 space-y-4" onSubmit={(event) => { event.preventDefault(); void save(true); }}>
          <fieldset
            disabled={Boolean(queueItem) || state === 'syncing'}
            className="space-y-4 disabled:opacity-75"
          >
          <label className="block text-sm">
            Подтверждённая причина
            <select
              className="input mt-1 min-h-11 w-full p-2"
              value={form.causeCode}
              onChange={(event) => setValue('causeCode', event.target.value)}
            >
              {Object.entries(CAUSE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            Пояснение причины
            <textarea
              className="input mt-1 min-h-24 w-full p-2"
              value={form.causeDetails}
              onChange={(event) => setValue('causeDetails', event.target.value)}
              minLength={3}
              maxLength={2000}
              required={form.causeCode === 'other'}
            />
          </label>
          <label className="block text-sm">
            Заметка о доказательствах
            <textarea
              className="input mt-1 min-h-24 w-full p-2"
              value={form.evidenceNote}
              onChange={(event) => setValue('evidenceNote', event.target.value)}
              minLength={3}
              maxLength={4000}
            />
          </label>

          <fieldset className="rounded-lg border border-agro-border p-3">
            <legend className="px-1 text-sm font-medium">Геопозиция результата</legend>
            <label className="flex min-h-11 items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.includeResultLocation}
                onChange={(event) => setValue('includeResultLocation', event.target.checked)}
              />
              Добавить координаты
            </label>
            {form.includeResultLocation && (
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <label className="text-xs">Широта<input type="number" step="any" min="-90" max="90" required className="input mt-1 min-h-11 w-full p-2 text-sm" value={form.resultLatitude} onChange={(event) => setValue('resultLatitude', event.target.value)} /></label>
                <label className="text-xs">Долгота<input type="number" step="any" min="-180" max="180" required className="input mt-1 min-h-11 w-full p-2 text-sm" value={form.resultLongitude} onChange={(event) => setValue('resultLongitude', event.target.value)} /></label>
              </div>
            )}
          </fieldset>

          <fieldset className="rounded-lg border border-agro-border p-3">
            <legend className="px-1 text-sm font-medium">Дополнительное доказательство</legend>
            <div className="grid gap-2 sm:grid-cols-3">
              {[
                ['none', 'Не добавлять'],
                ['geolocation', 'Геопозиция'],
                ['photo', 'Фото-метаданные'],
              ].map(([value, label]) => (
                <label key={value} className="flex min-h-11 items-center gap-2 text-sm">
                  <input type="radio" name={`offline-evidence-${inspection.id}`} checked={form.evidenceType === value} onChange={() => setValue('evidenceType', value)} />
                  {label}
                </label>
              ))}
            </div>
            {form.evidenceType === 'geolocation' && (
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <label className="text-xs">Широта<input type="number" step="any" min="-90" max="90" required className="input mt-1 min-h-11 w-full p-2 text-sm" value={form.evidenceLatitude} onChange={(event) => setValue('evidenceLatitude', event.target.value)} /></label>
                <label className="text-xs">Долгота<input type="number" step="any" min="-180" max="180" required className="input mt-1 min-h-11 w-full p-2 text-sm" value={form.evidenceLongitude} onChange={(event) => setValue('evidenceLongitude', event.target.value)} /></label>
              </div>
            )}
            {form.evidenceType === 'photo' && (
              <div className="mt-2">
                <label className="block text-sm">
                  Выбрать фотографию для вычисления метаданных
                  <input type="file" accept="image/*" onChange={selectPhoto} className="mt-1 block min-h-11 w-full text-sm" />
                </label>
                <p className="mt-1 text-xs text-agro-muted">
                  Файл не сохраняется офлайн и не загружается. Сохраняются только имя, тип, размер и SHA-256; максимум 25 МиБ.
                </p>
                {processingPhoto && <p className="mt-2 text-sm" role="status">Вычисляем SHA-256…</p>}
                {form.photo && <p className="mt-2 break-all text-xs">Подготовлено: {form.photo.original_filename} · {form.photo.byte_size} байт · {form.photo.sha256.slice(0, 12)}…</p>}
              </div>
            )}
          </fieldset>

          <fieldset className="rounded-lg border border-agro-border p-3">
            <legend className="px-1 text-sm font-medium">Корректирующее действие</legend>
            <label className="flex min-h-11 items-center gap-2 text-sm">
              <input type="checkbox" checked={form.createAction} onChange={(event) => setValue('createAction', event.target.checked)} />
              Создать действие на себя после результата
            </label>
            {form.createAction && (
              <div className="mt-2 space-y-2">
                <label className="block text-sm">Описание<textarea required minLength={5} maxLength={4000} className="input mt-1 min-h-24 w-full p-2" value={form.actionDescription} onChange={(event) => setValue('actionDescription', event.target.value)} /></label>
                <label className="block text-sm">Срок<input type="date" required min={todayTashkentDate()} className="input mt-1 min-h-11 w-full p-2" value={form.actionDueDate} onChange={(event) => setValue('actionDueDate', event.target.value)} /></label>
              </div>
            )}
          </fieldset>
          </fieldset>

          {queueItem?.status === 'conflict' && (
            <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">
              Конфликт версии. Серверные данные не перезаписаны. Обновите осмотр и создайте новый черновик после проверки.
            </p>
          )}
          {(message || externalNotice) && (
            <p aria-live="polite" className="text-sm">{message || externalNotice}</p>
          )}
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={!valid || state === 'syncing' || Boolean(queueItem)} onClick={() => void save(false)} className="btn-secondary min-h-11 px-4 py-2 disabled:opacity-50">Сохранить черновик</button>
            <button disabled={!valid || state === 'syncing' || Boolean(queueItem)} className="btn-primary min-h-11 px-4 py-2 disabled:opacity-50">В очередь</button>
            <button type="button" disabled={!queueItem || !online || state === 'syncing'} onClick={synchronize} className="btn-primary min-h-11 px-4 py-2 disabled:opacity-50">{state === 'syncing' ? 'Синхронизируем…' : 'Синхронизировать'}</button>
            <button type="button" disabled={state === 'syncing'} onClick={discard} className="min-h-11 rounded-lg px-4 py-2 text-sm text-red-700 hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-red-600">Удалить локально</button>
          </div>
        </form>
      )}
    </section>
  );
}
