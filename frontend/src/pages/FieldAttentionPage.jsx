import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getFieldAttentionQueue } from '../api/fieldAttention';
import { useAuth } from '../context/AuthContext';
import AttentionFilters from '../components/Attention/AttentionFilters';
import AttentionSummaryCards from '../components/Attention/AttentionSummaryCards';
import AttentionFieldCard from '../components/Attention/AttentionFieldCard';
import InspectionCreateModal from '../components/Inspections/InspectionCreateModal';
import { canWriteInspections, normalizeRole } from '../components/Inspections/inspectionPresentation';

function localDate() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function defaultFilters() {
  return { enterpriseId: '', cropTypeId: '', dateTo: localDate(), lookbackDays: '180', minPriority: 'medium' };
}

function safeDetail(error) {
  const detail = error?.response?.data?.detail;
  return typeof detail === 'string' && detail.length <= 240 && !/[<>\n\r]/.test(detail) ? detail : null;
}

function classifyError(error) {
  const status = error?.response?.status;
  if (status === 403) return { kind: 'access', message: 'Недостаточно прав для просмотра этой очереди.' };
  if (status === 422) return { kind: 'scope', message: 'Выборка слишком большая. Выберите предприятие или культуру.', detail: safeDetail(error) };
  return { kind: 'service', message: 'Не удалось загрузить очередь полей.' };
}

export default function FieldAttentionPage({ onNavigate, enterprises }) {
  const { user } = useAuth();
  const role = normalizeRole(user?.role);
  const isGlobalRole = role === 'admin' || role === 'manager';
  const canCreateInspection = canWriteInspections(role);
  const [draft, setDraft] = useState(defaultFilters);
  const [applied, setApplied] = useState(defaultFilters);
  const [data, setData] = useState(null);
  const [state, setState] = useState('loading');
  const [error, setError] = useState(null);
  const [cropOptions, setCropOptions] = useState([]);
  const [refreshGeneration, setRefreshGeneration] = useState(0);
  const [selectedAttention, setSelectedAttention] = useState(null);
  const controllerRef = useRef(null);
  const requestGenerationRef = useRef(0);
  const mountedRef = useRef(false);

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
    const requestGeneration = ++requestGenerationRef.current;
    setState('loading');
    setError(null);

    getFieldAttentionQueue({
      enterpriseId: isGlobalRole ? applied.enterpriseId : '',
      cropTypeId: applied.cropTypeId,
      dateTo: applied.dateTo,
      lookbackDays: Number(applied.lookbackDays),
      minPriority: applied.minPriority,
      limit: 100,
      signal: controller.signal,
    }).then((result) => {
      if (!mountedRef.current || controller.signal.aborted || requestGeneration !== requestGenerationRef.current) return;
      const normalized = result && typeof result === 'object' ? result : {};
      const items = Array.isArray(normalized.items) ? normalized.items : [];
      setData({ ...normalized, items, summary: normalized.summary && typeof normalized.summary === 'object' ? normalized.summary : {} });
      if (!applied.cropTypeId) {
        const options = new Map();
        items.forEach((item) => {
          const id = Number(item?.field?.crop_type_id);
          const name = typeof item?.field?.crop_name === 'string' ? item.field.crop_name.trim() : '';
          if (Number.isSafeInteger(id) && id > 0 && name && !options.has(id)) options.set(id, name);
        });
        setCropOptions(Array.from(options, ([id, name]) => ({ id, name })));
      }
      setState('ready');
    }).catch((requestError) => {
      if (!mountedRef.current || controller.signal.aborted || requestGeneration !== requestGenerationRef.current || requestError?.code === 'ERR_CANCELED') return;
      setError(classifyError(requestError));
      setState('error');
    });

    return () => controller.abort();
  }, [applied, isGlobalRole, refreshGeneration]);

  const items = useMemo(() => Array.isArray(data?.items) ? data.items : [], [data]);
  const handleChange = useCallback((field, value) => setDraft((current) => ({ ...current, [field]: value })), []);
  const handleApply = useCallback((event) => {
    event.preventDefault();
    setApplied({ ...draft, enterpriseId: isGlobalRole ? draft.enterpriseId : '' });
  }, [draft, isGlobalRole]);
  const handleReset = useCallback(() => {
    const defaults = defaultFilters();
    setDraft(defaults);
    setApplied(defaults);
  }, []);
  const handleRefresh = useCallback(() => setRefreshGeneration((value) => value + 1), []);
  const createSource = useMemo(() => {
    if (!selectedAttention) return null;
    const field = selectedAttention.field || {};
    const spectral = selectedAttention.spectral_summary || {};
    const reasons = Array.isArray(selectedAttention.reasons) ? selectedAttention.reasons : [];
    const codes = Array.from(new Set(
      reasons
        .map((reason) => typeof reason?.code === 'string' ? reason.code : '')
        .filter((code) => /^[a-z][a-z0-9_]{1,63}$/.test(code)),
    ));
    return {
      source: 'attention_queue',
      field_id: field.id,
      field_name: field.name,
      enterprise_id: field.enterprise_id,
      enterprise_name: field.enterprise_name,
      priority: selectedAttention.priority,
      attention_score: selectedAttention.attention_score,
      observation_date: spectral.latest_observation_date,
      reason_codes: codes,
      recommended_checks: Array.isArray(selectedAttention.recommended_checks)
        ? selectedAttention.recommended_checks
        : [],
    };
  }, [selectedAttention]);

  return (
    <main className="h-full overflow-y-auto overflow-x-hidden p-4 pt-16 md:p-8 md:pt-20" aria-label="Очередь внимания">
      <div className="mx-auto max-w-7xl space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <p className="text-sm text-agro-muted">Поля, требующие проверки, в порядке, сформированном сервером.</p>
          <button type="button" onClick={handleRefresh} disabled={state === 'loading'} className="btn-secondary rounded-lg px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-agro-accent disabled:opacity-60">Обновить</button>
        </div>

        <AttentionFilters draft={draft} onChange={handleChange} onApply={handleApply} onReset={handleReset} enterprises={enterprises} cropOptions={cropOptions} isGlobalRole={isGlobalRole} loading={state === 'loading'} />

        {data && <AttentionSummaryCards summary={data.summary} generatedAt={data.generated_at} dateTo={data.date_to} lookbackDays={data.lookback_days} />}

        <div aria-live="polite">
          {state === 'loading' && <div className="card p-5" role="status"><p className="text-sm font-medium text-agro-text">Формируем очередь полей…</p><div className="mt-3 space-y-2 animate-pulse"><div className="h-4 w-2/3 rounded bg-agro-surface2" /><div className="h-20 rounded bg-agro-surface2" /></div></div>}

          {state === 'error' && <div className="card border border-red-200 p-5" role="alert"><p className="font-medium text-agro-danger">{error?.message}</p>{error?.detail && <p className="mt-1 text-xs text-agro-muted">{error.detail}</p>}<button type="button" onClick={handleRefresh} className="btn-primary mt-4 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-agro-accent">Повторить</button></div>}

          {state === 'ready' && items.length === 0 && <div className="card p-8 text-center"><p className="font-medium text-agro-text">По выбранным условиям полей, требующих внимания, нет.</p><button type="button" onClick={handleReset} className="btn-secondary mt-4 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-agro-accent">Сбросить фильтры</button></div>}

          {state === 'ready' && items.length > 0 && <div className="space-y-4">{items.map((item) => {
            const id = Number(item?.field?.id);
            return Number.isSafeInteger(id) && id > 0 ? <AttentionFieldCard key={id} item={item} onNavigate={onNavigate} canCreateInspection={canCreateInspection} onCreateInspection={setSelectedAttention} /> : null;
          })}</div>}
        </div>
      </div>
      {createSource && <InspectionCreateModal source={createSource} user={user} assignees={[]} onClose={() => setSelectedAttention(null)} onSuccess={(inspection) => { setSelectedAttention(null); if (inspection?.id) onNavigate('field-inspection-detail', inspection.id); }} />}
    </main>
  );
}
