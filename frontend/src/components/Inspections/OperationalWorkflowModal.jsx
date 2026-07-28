import { useEffect, useMemo, useRef, useState } from 'react';

import {
  attachInspectionEvidence,
  closeCorrectiveAction,
  createCorrectiveAction,
  reopenCorrectiveAction,
  requestActionVerification,
  resolveActionVerification,
  updateCorrectiveAction,
} from '../../api/fieldInspections';
import {
  ACTION_STATUS_LABELS,
  createIdempotencyKey,
  displayName,
  filterAssigneesForEnterprise,
  inspectionError,
  normalizeRole,
  todayTashkentDate,
} from './inspectionPresentation';

const MODE_LABELS = {
  evidence: 'Добавить доказательство',
  create: 'Создать корректирующее действие',
  update: 'Изменить корректирующее действие',
  close: 'Закрыть корректирующее действие',
  reopen: 'Переоткрыть корректирующее действие',
  request: 'Запросить спутниковую проверку',
  resolve: 'Проверить по новому наблюдению',
};

const INDEX_OPTIONS = ['ndvi', 'savi', 'evi', 'ndmi', 'ndre'];

export default function OperationalWorkflowModal({
  mode,
  inspection,
  result,
  action,
  user,
  assignees,
  onClose,
  onSuccess,
}) {
  const role = normalizeRole(user?.role);
  const management = role === 'admin' || role === 'manager';
  const initialOwner = action?.owner?.id || (role === 'agronomist' ? user?.id : '');
  const [ownerId, setOwnerId] = useState(initialOwner ? String(initialOwner) : '');
  const [description, setDescription] = useState(action?.description || '');
  const [dueDate, setDueDate] = useState(action?.due_date || '');
  const [status, setStatus] = useState(action?.status || 'open');
  const [reason, setReason] = useState('');
  const [indexCode, setIndexCode] = useState('ndvi');
  const [minimumDays, setMinimumDays] = useState('3');
  const [notes, setNotes] = useState('');
  const [evidenceType, setEvidenceType] = useState('geolocation');
  const [latitude, setLatitude] = useState('');
  const [longitude, setLongitude] = useState('');
  const [filename, setFilename] = useState('');
  const [mediaType, setMediaType] = useState('image/jpeg');
  const [byteSize, setByteSize] = useState('');
  const [sha256, setSha256] = useState('');
  const [providerReference, setProviderReference] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const controllerRef = useRef(null);
  const dialogRef = useRef(null);
  const initialFocusRef = useRef(null);
  const returnFocusRef = useRef(null);
  const idempotencyKeyRef = useRef(createIdempotencyKey());

  const eligibleAssignees = useMemo(() => {
    const currentUser = user?.id ? [{
      id: user.id,
      name: displayName(user, 'Текущий пользователь'),
      enterpriseId: user?.enterprise_id,
    }] : [];
    const currentOwner = action?.owner?.id ? [{
      id: action.owner.id,
      name: displayName(action.owner, 'Исполнитель'),
      enterpriseId: inspection?.enterprise_id,
    }] : [];
    return filterAssigneesForEnterprise(
      [...currentOwner, ...currentUser, ...assignees],
      inspection?.enterprise_id,
    );
  }, [action?.owner, assignees, inspection?.enterprise_id, user]);

  useEffect(() => {
    returnFocusRef.current = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    initialFocusRef.current?.focus();
    return () => {
      controllerRef.current?.abort();
      document.body.style.overflow = previousOverflow;
      if (returnFocusRef.current?.isConnected) returnFocusRef.current.focus();
    };
  }, []);

  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        if (!pending) onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      ) || []).filter((element) => element.getAttribute('aria-hidden') !== 'true');
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose, pending]);

  const valid = useMemo(() => {
    if (mode === 'evidence') {
      if (evidenceType === 'geolocation') {
        const lat = Number(latitude);
        const lon = Number(longitude);
        return latitude !== '' && longitude !== ''
          && Number.isFinite(lat) && lat >= -90 && lat <= 90
          && Number.isFinite(lon) && lon >= -180 && lon <= 180;
      }
      return filename.trim().length > 0
        && filename.trim().length <= 255
        && !/[\\/\0]/.test(filename)
        && mediaType.trim().length >= 3
        && mediaType.trim().length <= 100
        && Number.isSafeInteger(Number(byteSize))
        && Number(byteSize) >= 0
        && Number(byteSize) <= 25 * 1024 * 1024
        && /^[0-9a-f]{64}$/.test(sha256);
    }
    if (mode === 'create') {
      return Number(ownerId) > 0
        && description.trim().length >= 5
        && description.trim().length <= 4000
        && dueDate >= todayTashkentDate();
    }
    if (mode === 'update') {
      const changed = description.trim() !== action?.description
        || dueDate !== action?.due_date
        || status !== action?.status
        || (management && String(action?.owner?.id || '') !== ownerId);
      return changed
        && Number(ownerId) > 0
        && description.trim().length >= 5
        && description.trim().length <= 4000
        && dueDate >= todayTashkentDate()
        && ['open', 'in_progress', 'blocked'].includes(status);
    }
    if (mode === 'close' || mode === 'reopen') return reason.trim().length >= 5 && reason.trim().length <= 4000;
    if (mode === 'request') return INDEX_OPTIONS.includes(indexCode) && Number(minimumDays) >= 1 && Number(minimumDays) <= 30;
    if (mode === 'resolve') return !notes.trim() || (notes.trim().length >= 3 && notes.trim().length <= 2000);
    return false;
  }, [
    action,
    byteSize,
    description,
    dueDate,
    evidenceType,
    filename,
    indexCode,
    latitude,
    longitude,
    management,
    mediaType,
    minimumDays,
    mode,
    notes,
    ownerId,
    reason,
    sha256,
    status,
  ]);

  async function submit(event) {
    event.preventDefault();
    if (!valid || pending) return;
    setPending(true);
    setError('');
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const key = idempotencyKeyRef.current;
      let response;
      if (mode === 'evidence') {
        const payload = {
          expected_version: inspection.version,
          result_id: result?.id || null,
          evidence_type: evidenceType,
          provider: 'metadata_only',
          provider_metadata: {},
        };
        if (evidenceType === 'geolocation') {
          payload.latitude = Number(latitude);
          payload.longitude = Number(longitude);
        } else {
          payload.original_filename = filename.trim();
          payload.media_type = mediaType.trim();
          payload.byte_size = Number(byteSize);
          payload.sha256 = sha256;
          payload.provider_reference = providerReference.trim() || null;
        }
        response = await attachInspectionEvidence(inspection.id, payload, key, controller.signal);
      } else if (mode === 'create') {
        response = await createCorrectiveAction(inspection.id, {
          expected_inspection_version: inspection.version,
          result_id: result.id,
          owner_id: Number(ownerId),
          description: description.trim(),
          due_date: dueDate,
        }, key, controller.signal);
      } else if (mode === 'update') {
        const payload = {
          expected_version: action.version,
          description: description.trim(),
          due_date: dueDate,
          status,
        };
        if (management) payload.owner_id = Number(ownerId);
        response = await updateCorrectiveAction(action.id, payload, key, controller.signal);
      } else if (mode === 'close') {
        response = await closeCorrectiveAction(action.id, {
          expected_version: action.version,
          closure_reason: reason.trim(),
        }, key, controller.signal);
      } else if (mode === 'reopen') {
        response = await reopenCorrectiveAction(action.id, {
          expected_version: action.version,
          reopen_reason: reason.trim(),
        }, key, controller.signal);
      } else if (mode === 'request') {
        response = await requestActionVerification(action.id, {
          expected_action_version: action.version,
          index_code: indexCode,
          minimum_separation_days: Number(minimumDays),
        }, key, controller.signal);
      } else {
        response = await resolveActionVerification(action.latest_verification.id, {
          expected_version: action.latest_verification.version,
          notes: notes.trim() || null,
        }, key, controller.signal);
      }
      onSuccess(response);
    } catch (requestError) {
      if (requestError?.name !== 'AbortError' && requestError?.code !== 'ERR_CANCELED') {
        setError(inspectionError(requestError));
      }
    } finally {
      if (!controller.signal.aborted) setPending(false);
    }
  }

  const label = MODE_LABELS[mode];
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-3" role="dialog" aria-modal="true" aria-labelledby="workflow-title">
      <form ref={dialogRef} onSubmit={submit} className="card max-h-[calc(100vh-1.5rem)] w-full max-w-xl overflow-y-auto p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="workflow-title" className="text-lg font-bold">{label}</h2>
            <p className="mt-1 text-sm text-agro-muted">Осмотр #{inspection.id}. Изменение проверяется сервером по роли, предприятию и версии записи.</p>
          </div>
          <button type="button" onClick={onClose} disabled={pending} className="rounded-lg p-2 focus:outline-none focus:ring-2 focus:ring-agro-accent" aria-label="Закрыть">✕</button>
        </div>

        {mode === 'evidence' && <div className="mt-4 space-y-3">
          <fieldset>
            <legend className="text-sm font-medium">Тип метаданных</legend>
            <div className="mt-2 flex flex-wrap gap-4">
              <label className="flex min-h-11 items-center gap-2 text-sm"><input ref={initialFocusRef} type="radio" name="evidence-type" checked={evidenceType === 'geolocation'} onChange={() => setEvidenceType('geolocation')} /> Геопозиция</label>
              <label className="flex min-h-11 items-center gap-2 text-sm"><input type="radio" name="evidence-type" checked={evidenceType === 'photo'} onChange={() => setEvidenceType('photo')} /> Метаданные фотографии</label>
            </div>
          </fieldset>
          {evidenceType === 'geolocation' && <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="text-sm">Широта<input type="number" step="any" min="-90" max="90" required className="input mt-1 w-full p-2" value={latitude} onChange={(event) => setLatitude(event.target.value)} /></label>
            <label className="text-sm">Долгота<input type="number" step="any" min="-180" max="180" required className="input mt-1 w-full p-2" value={longitude} onChange={(event) => setLongitude(event.target.value)} /></label>
          </div>}
          {evidenceType === 'photo' && <div className="space-y-3">
            <p className="rounded-lg bg-amber-50 p-3 text-xs text-amber-900">Файл не загружается. Сохраняются только проверяемые метаданные существующего объекта у настроенного провайдера.</p>
            <label className="block text-sm">Имя файла<input required maxLength={255} className="input mt-1 w-full p-2" value={filename} onChange={(event) => setFilename(event.target.value)} /></label>
            <label className="block text-sm">MIME-тип<input required maxLength={100} className="input mt-1 w-full p-2" value={mediaType} onChange={(event) => setMediaType(event.target.value)} /></label>
            <label className="block text-sm">Размер, байт<input type="number" required min="0" max={25 * 1024 * 1024} className="input mt-1 w-full p-2" value={byteSize} onChange={(event) => setByteSize(event.target.value)} /></label>
            <label className="block text-sm">SHA-256<input required minLength={64} maxLength={64} pattern="[0-9a-f]{64}" className="input mt-1 w-full p-2 font-mono text-xs" value={sha256} onChange={(event) => setSha256(event.target.value.trim().toLowerCase())} /></label>
            <label className="block text-sm">Ссылка провайдера<input maxLength={255} className="input mt-1 w-full p-2" value={providerReference} onChange={(event) => setProviderReference(event.target.value)} /></label>
          </div>}
        </div>}

        {(mode === 'create' || mode === 'update') && <div className="mt-4 space-y-3">
          <label className="block text-sm">Исполнитель
            <select ref={management ? initialFocusRef : undefined} required disabled={!management && role === 'agronomist'} className="input mt-1 w-full p-2" value={ownerId} onChange={(event) => setOwnerId(event.target.value)}>
              <option value="">Выберите исполнителя</option>
              {eligibleAssignees.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
            </select>
          </label>
          {!eligibleAssignees.length && <p className="text-xs text-amber-800">Нет известного допустимого исполнителя для этого предприятия.</p>}
          <label className="block text-sm">Корректирующее действие<textarea ref={!management ? initialFocusRef : undefined} required minLength={5} maxLength={4000} className="input mt-1 w-full p-2" value={description} onChange={(event) => setDescription(event.target.value)} /></label>
          <label className="block text-sm">Срок<input type="date" required min={todayTashkentDate()} className="input mt-1 w-full p-2" value={dueDate} onChange={(event) => setDueDate(event.target.value)} /></label>
          {mode === 'update' && <label className="block text-sm">Статус
            <select className="input mt-1 w-full p-2" value={status} onChange={(event) => setStatus(event.target.value)}>
              {Object.entries(ACTION_STATUS_LABELS).filter(([value]) => value !== 'closed').map(([value, name]) => <option key={value} value={value}>{name}</option>)}
            </select>
          </label>}
        </div>}

        {(mode === 'close' || mode === 'reopen') && <label className="mt-4 block text-sm">
          {mode === 'close' ? 'Причина закрытия' : 'Причина переоткрытия'}
          <textarea ref={initialFocusRef} required minLength={5} maxLength={4000} className="input mt-1 w-full p-2" value={reason} onChange={(event) => setReason(event.target.value)} />
        </label>}

        {mode === 'request' && <div className="mt-4 space-y-3">
          <label className="block text-sm">Спутниковый индекс
            <select ref={initialFocusRef} className="input mt-1 w-full p-2" value={indexCode} onChange={(event) => setIndexCode(event.target.value)}>
              {INDEX_OPTIONS.map((code) => <option key={code} value={code}>{code.toUpperCase()}</option>)}
            </select>
          </label>
          <label className="block text-sm">Минимальный интервал, дней<input type="number" min="1" max="30" required className="input mt-1 w-full p-2" value={minimumDays} onChange={(event) => setMinimumDays(event.target.value)} /></label>
          <p className="rounded-lg bg-sky-50 p-3 text-xs text-sky-900">Проверка покажет наблюдаемое изменение индекса. Она не подтверждает агрономическую причинность сама по себе.</p>
        </div>}

        {mode === 'resolve' && <div className="mt-4 space-y-3">
          <p className="rounded-lg bg-sky-50 p-3 text-sm text-sky-900">Будет выбрано первое более позднее наблюдение того же поля и индекса, которое проходит правила качества и минимального интервала.</p>
          <label className="block text-sm">Примечание к проверке<textarea ref={initialFocusRef} minLength={3} maxLength={2000} className="input mt-1 w-full p-2" value={notes} onChange={(event) => setNotes(event.target.value)} /></label>
        </div>}

        {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button type="button" disabled={pending} onClick={onClose} className="btn-secondary px-4 py-2">Отмена</button>
          <button disabled={!valid || pending} className="btn-primary px-4 py-2 disabled:opacity-50">{pending ? 'Сохраняем…' : label}</button>
        </div>
      </form>
    </div>
  );
}
