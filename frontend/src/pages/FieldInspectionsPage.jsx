import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useAuth } from '../context/AuthContext';
import { listFieldInspections } from '../api/fieldInspections';
import InspectionSummaryCards from '../components/Inspections/InspectionSummaryCards';
import InspectionFilters from '../components/Inspections/InspectionFilters';
import InspectionCard from '../components/Inspections/InspectionCard';
import InspectionCreateModal from '../components/Inspections/InspectionCreateModal';
import InspectionActionModal from '../components/Inspections/InspectionActionModal';
import InspectionDetailDrawer from '../components/Inspections/InspectionDetailDrawer';
import {
  canWriteInspections,
  displayName,
  normalizeRole,
  positiveId,
} from '../components/Inspections/inspectionPresentation';

function defaultFilters(user) {
  const role = normalizeRole(user?.role);
  return {
    enterpriseId: '',
    assignedToId: role === 'agronomist' && positiveId(user?.id) ? String(user.id) : '',
    status: '',
    overdueOnly: false,
    dueBefore: '',
    createdAfter: '',
    limit: 50,
    offset: 0,
  };
}

function listError(error) {
  const status = error?.response?.status;
  if (status === 403) return { state: '403', message: 'Недостаточно прав для просмотра осмотров.' };
  if (status === 422) return { state: '422', message: 'Проверьте параметры запроса.' };
  return { state: 'error', message: 'Не удалось загрузить осмотры.' };
}

export default function FieldInspectionsPage({ onNavigate, enterprises = [], selectedInspectionId }) {
  const { user } = useAuth();
  const role = normalizeRole(user?.role);
  const canWrite = canWriteInspections(role);
  const [draft, setDraft] = useState(() => defaultFilters(user));
  const [applied, setApplied] = useState(() => defaultFilters(user));
  const [data, setData] = useState(null);
  const [state, setState] = useState('loading');
  const [error, setError] = useState('');
  const [listReloadToken, setListReloadToken] = useState(0);
  const [detailReloadToken, setDetailReloadToken] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [action, setAction] = useState(null);
  const knownAssigneesRef = useRef(new Map());
  const mountedRef = useRef(false);
  const requestGenerationRef = useRef(0);
  const controllerRef = useRef(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestGenerationRef.current += 1;
      controllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const generation = ++requestGenerationRef.current;
    setState('loading');
    setError('');

    listFieldInspections(applied, controller.signal)
      .then((result) => {
        if (!mountedRef.current || controller.signal.aborted || generation !== requestGenerationRef.current) return;
        const normalized = { ...result, items: Array.isArray(result?.items) ? result.items : [] };
        normalized.items.forEach((item) => {
          const id = positiveId(item?.assigned_to?.id);
          const name = displayName(item?.assigned_to, '');
          if (id && name) {
            knownAssigneesRef.current.set(id, {
              id,
              name,
              enterpriseId: positiveId(item?.field?.enterprise_id),
            });
          }
        });
        if (role === 'agronomist' && positiveId(user?.id) && user?.display_name) {
          knownAssigneesRef.current.set(Number(user.id), {
            id: Number(user.id),
            name: user.display_name,
            enterpriseId: positiveId(user.enterprise_id),
          });
        }
        setData(normalized);
        setState('ready');
      })
      .catch((requestError) => {
        if (!mountedRef.current || controller.signal.aborted || generation !== requestGenerationRef.current || requestError?.code === 'ERR_CANCELED') return;
        const failure = listError(requestError);
        setState(failure.state);
        setError(failure.message);
      });
    return () => controller.abort();
  }, [applied, listReloadToken, role, user]);

  const assignees = useMemo(() => Array.from(knownAssigneesRef.current.values()), [data, user]);
  const changeFilter = useCallback((key, value) => setDraft((current) => ({ ...current, [key]: value })), []);
  const applyFilters = useCallback((event) => {
    event.preventDefault();
    setApplied({ ...draft, offset: 0 });
  }, [draft]);
  const resetFilters = useCallback(() => {
    const defaults = defaultFilters(user);
    setDraft(defaults);
    setApplied(defaults);
  }, [user]);
  const reloadList = useCallback(() => setListReloadToken((value) => value + 1), []);
  const reloadDetail = useCallback(() => setDetailReloadToken((value) => value + 1), []);

  const handleMutationSuccess = useCallback((inspection) => {
    setAction(null);
    reloadDetail();
    reloadList();
    if (inspection?.id) onNavigate('field-inspection-detail', inspection.id);
  }, [onNavigate, reloadDetail, reloadList]);

  const handleConflictReload = useCallback(() => {
    setAction(null);
    reloadDetail();
    reloadList();
  }, [reloadDetail, reloadList]);

  const handleCreateSuccess = useCallback((inspection) => {
    setCreateOpen(false);
    reloadList();
    if (inspection?.id) onNavigate('field-inspection-detail', inspection.id);
  }, [onNavigate, reloadList]);

  const items = Array.isArray(data?.items) ? data.items : [];
  return (
    <div className="h-full overflow-y-auto overflow-x-hidden p-4 pt-16 md:p-8 md:pt-20">
      <div className="mx-auto max-w-7xl space-y-6">
        <div className="flex flex-wrap justify-between gap-3">
          <p className="text-sm text-agro-muted">Планирование, выполнение и результаты полевых проверок.</p>
          {canWrite && <button type="button" onClick={() => setCreateOpen(true)} className="btn-primary rounded-lg px-4 py-2">Создать осмотр</button>}
        </div>
        <InspectionFilters draft={draft} onChange={changeFilter} onApply={applyFilters} onReset={resetFilters} onRefresh={reloadList} enterprises={enterprises} assignees={assignees} role={role} loading={state === 'loading'} />
        {data && <InspectionSummaryCards data={data} />}
        <div aria-live="polite">
          {state === 'loading' && <div className="card p-5">Загружаем осмотры полей…</div>}
          {['403', '422', 'error'].includes(state) && <div className="card p-5" role="alert">
<p>{error}</p>
<button type="button" onClick={reloadList} className="btn-primary mt-3 px-3 py-2">Повторить</button>
</div>}
          {state === 'ready' && items.length === 0 && <div className="card p-8 text-center">
<p>По выбранным условиям осмотров нет.</p>
<div className="mt-3 flex justify-center gap-2">
<button type="button" onClick={resetFilters} className="btn-secondary px-3 py-2">Сбросить фильтры</button>{canWrite && <button type="button" onClick={() => setCreateOpen(true)} className="btn-primary px-3 py-2">Создать осмотр</button>}</div>
</div>}
          {state === 'ready' && <div className="space-y-3">{items.map((item) => <InspectionCard key={item.id} inspection={item} user={user} onNavigate={onNavigate} onAction={(mode, value) => setAction({ mode, item: value })} />)}</div>}
        </div>
        {state === 'ready' && <div className="flex justify-between">
<button type="button" disabled={applied.offset === 0} onClick={() => setApplied((current) => ({ ...current, offset: Math.max(0, current.offset - current.limit) }))} className="btn-secondary px-4 py-2 disabled:opacity-50">Назад</button>
<button type="button" disabled={applied.offset + items.length >= Number(data?.summary?.total || 0)} onClick={() => setApplied((current) => ({ ...current, offset: current.offset + current.limit }))} className="btn-secondary px-4 py-2 disabled:opacity-50">Вперёд</button>
</div>}
      </div>
      {createOpen && <InspectionCreateModal user={user} assignees={assignees} onClose={() => setCreateOpen(false)} onSuccess={handleCreateSuccess} />}
      {action && <InspectionActionModal {...action} user={user} assignees={assignees} onClose={() => setAction(null)} onSuccess={handleMutationSuccess} onReload={handleConflictReload} />}
      {selectedInspectionId && <InspectionDetailDrawer id={selectedInspectionId} user={user} onNavigate={onNavigate} onClose={() => onNavigate('field-inspections')} onAction={(mode, item) => setAction({ mode, item })} reloadToken={detailReloadToken} onRetry={reloadDetail} suspendEscape={Boolean(action)} />}
    </div>
  );
}
