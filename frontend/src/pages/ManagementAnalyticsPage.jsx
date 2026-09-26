import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useAuth } from '../context/AuthContext';
import { buildManagementAnalyticsParams, getManagementAnalytics } from '../api/managementAnalytics';
import { getOperationalFilterOptions } from '../api/operationalCenter';
import { MANAGEMENT_ANALYTICS_DEFINITIONS_VERSION } from '../config/managementAnalytics.js';
import {
  DEFAULT_GRANULARITY,
  DEFAULT_PERIOD_DAYS,
  FIELD_PAGE_SIZE,
  describeRequestError,
  isCancelledRequest,
  positiveId,
  presetPeriod,
  scopeKeyOf,
  validatePeriod,
} from '../utils/managementAnalytics.js';
import { formatTashkentDateTime } from '../utils/tashkentTime.js';
import AnalyticsFilters from '../components/ManagementAnalytics/AnalyticsFilters.jsx';
import AnalyticsContext from '../components/ManagementAnalytics/AnalyticsContext.jsx';
import CurrentStateSection from '../components/ManagementAnalytics/CurrentStateSection.jsx';
import PeriodSection from '../components/ManagementAnalytics/PeriodSection.jsx';
import BreakdownSection from '../components/ManagementAnalytics/BreakdownSection.jsx';
import {
  AnalyticsErrorPanel,
  AnalyticsSkeleton,
  EmptyScopePanel,
  RefreshErrorBanner,
} from '../components/ManagementAnalytics/AnalyticsStates.jsx';

// H1 Management Analytics v1 (TASK_233). Every figure on this page is a value
// of GET /api/management-analytics (TASK_232): the page validates filters,
// labels and lays out the response, and never recomputes a metric from other
// endpoints. Operational Center filter options only list selectable fields.

function initialFilters() {
  return {
    preset: DEFAULT_PERIOD_DAYS,
    ...presetPeriod(DEFAULT_PERIOD_DAYS),
    enterpriseId: '',
    fieldId: '',
    currentCropTypeId: '',
    granularity: DEFAULT_GRANULARITY,
    fieldOffset: 0,
  };
}

const optionList = (items, idKey, labelKey) => (Array.isArray(items) ? items : [])
  .map((item) => ({ id: positiveId(item?.[idKey]), label: typeof item?.[labelKey] === 'string' ? item[labelKey].trim() : '' }))
  .filter((item) => item.id && item.label);

const byLabel = (left, right) => left.label.localeCompare(right.label, 'ru');

export default function ManagementAnalyticsPage({ enterprises = [] }) {
  const { user } = useAuth();
  const isAdmin = String(user?.role || '').trim().toLowerCase() === 'admin';
  const [filters, setFilters] = useState(initialFilters);
  const [customPeriod, setCustomPeriod] = useState(() => ({ dateFrom: filters.dateFrom, dateTo: filters.dateTo }));
  const [periodError, setPeriodError] = useState('');
  const [reloadToken, setReloadToken] = useState(0);
  const [view, setView] = useState({ status: 'loading', data: null, error: null, scopeKey: '', optionScope: '' });
  const [fieldOptionsState, setFieldOptionsState] = useState({ status: 'idle', fields: [], enterprises: [] });
  const [cropOptionsState, setCropOptionsState] = useState({ scope: null, items: [] });
  const [online, setOnline] = useState(() => (typeof navigator === 'undefined' ? true : navigator.onLine));
  const generationRef = useRef(0);

  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener('online', up);
    window.addEventListener('offline', down);
    return () => {
      window.removeEventListener('online', up);
      window.removeEventListener('offline', down);
    };
  }, []);

  // One request key per distinct query; the effect below depends on the key
  // string, so re-renders with equal filters never issue a second request.
  // Filters are validated where they are set, so a refused filter here is a
  // defect: it is reported and nothing is sent.
  const request = useMemo(() => {
    try {
      const params = buildManagementAnalyticsParams({ ...filters, fieldLimit: FIELD_PAGE_SIZE });
      return {
        params,
        key: JSON.stringify(params),
        scopeKey: scopeKeyOf(params),
        optionScope: `${params.enterprise_id ?? ''}|${params.field_id ?? ''}`,
      };
    } catch (error) {
      return { params: null, key: `invalid:${error.message}`, scopeKey: '', optionScope: '', error: error.message };
    }
  }, [filters]);

  useEffect(() => {
    const { params, scopeKey, optionScope } = request;
    if (!params) {
      setView({
        status: 'error',
        data: null,
        error: { kind: 'invalid', retryable: false, resettable: true, title: 'Фильтр не принят', message: request.error },
        scopeKey,
        optionScope,
      });
      return undefined;
    }
    const controller = new AbortController();
    const generation = ++generationRef.current;
    // The same scope may keep its numbers while it refreshes (another page of
    // fields, another granularity); a different scope never shows old numbers.
    setView((current) => (current.data && current.scopeKey === scopeKey
      ? { ...current, status: 'refreshing', error: null }
      : { status: 'loading', data: null, error: null, scopeKey, optionScope }));
    getManagementAnalytics(params, controller.signal)
      .then((data) => {
        if (controller.signal.aborted || generation !== generationRef.current) return;
        setView({ status: 'ready', data, error: null, scopeKey, optionScope });
      })
      .catch((error) => {
        if (controller.signal.aborted || generation !== generationRef.current || isCancelledRequest(error)) return;
        const described = describeRequestError(error, { online: navigator.onLine });
        if (!described) return;
        setView((current) => ({
          status: 'error',
          data: current.scopeKey === scopeKey ? current.data : null,
          error: described,
          scopeKey,
          optionScope,
        }));
      });
    return () => controller.abort();
    // `request` is derived from `filters`; its key identifies the query.
  }, [request.key, reloadToken]);

  // Selectable fields: the server-scoped Operational Center option list of one
  // enterprise (the manager's own; an admin chooses the enterprise first).
  const optionsEnterpriseId = isAdmin ? filters.enterpriseId : '';
  const needsEnterpriseFirst = isAdmin && !filters.enterpriseId;
  useEffect(() => {
    if (needsEnterpriseFirst) {
      setFieldOptionsState({ status: 'idle', fields: [], enterprises: [] });
      return undefined;
    }
    const controller = new AbortController();
    setFieldOptionsState((current) => ({ ...current, status: 'loading', fields: [] }));
    getOperationalFilterOptions(optionsEnterpriseId || undefined, controller.signal)
      .then((options) => {
        if (controller.signal.aborted) return;
        const expected = positiveId(optionsEnterpriseId);
        const fields = (Array.isArray(options?.fields) ? options.fields : [])
          .filter((item) => !expected || positiveId(item?.enterprise_id) === expected);
        setFieldOptionsState({
          status: 'ready',
          fields: optionList(fields, 'id', 'label').sort(byLabel),
          enterprises: optionList(options?.enterprises, 'id', 'label'),
        });
      })
      .catch((error) => {
        if (controller.signal.aborted || isCancelledRequest(error)) return;
        setFieldOptionsState({ status: 'error', fields: [], enterprises: [] });
      });
    return () => controller.abort();
  }, [needsEnterpriseFirst, optionsEnterpriseId]);

  // Crop options are the TASK_232 current-crop classes of the same enterprise
  // and field scope, taken from a response without a crop filter.
  useEffect(() => {
    const data = view.data;
    if (view.status !== 'ready' || !data) return;
    if (data.scope?.current_crop_type_id !== null && data.scope?.current_crop_type_id !== undefined) return;
    setCropOptionsState({
      scope: view.optionScope,
      items: optionList(data.breakdowns?.current_crops, 'current_crop_type_id', 'current_crop_name'),
    });
  }, [view]);

  const data = view.data;
  const optionScope = request.optionScope;

  const enterpriseOptions = useMemo(() => {
    const known = new Map();
    for (const item of optionList(enterprises, 'id', 'name')) known.set(item.id, item.label);
    if (data?.scope && (data.scope.enterprise_id === null || data.scope.enterprise_id === undefined)) {
      for (const item of optionList(data.breakdowns?.enterprises, 'enterprise_id', 'enterprise_name')) known.set(item.id, item.label);
    }
    return Array.from(known, ([id, label]) => ({ id, label })).sort(byLabel);
  }, [data, enterprises]);

  const fieldOptions = useMemo(() => {
    const items = [...fieldOptionsState.fields];
    const selected = positiveId(filters.fieldId);
    if (selected && !items.some((item) => item.id === selected)) {
      // A field chosen from a server breakdown row stays visible as the selection.
      const row = (data?.breakdowns?.fields?.items || []).find((item) => positiveId(item?.field_id) === selected);
      items.unshift({ id: selected, label: row?.field_name || `Поле #${selected}` });
    }
    return items;
  }, [data, fieldOptionsState.fields, filters.fieldId]);

  const cropOptions = useMemo(() => {
    const items = cropOptionsState.scope === optionScope ? [...cropOptionsState.items] : [];
    const selected = positiveId(filters.currentCropTypeId);
    if (selected && !items.some((item) => item.id === selected)) {
      const row = (data?.breakdowns?.current_crops || []).find((item) => positiveId(item?.current_crop_type_id) === selected);
      items.push({ id: selected, label: row?.current_crop_name || `Культура #${selected}` });
    }
    return items.sort(byLabel);
  }, [cropOptionsState, data, filters.currentCropTypeId, optionScope]);

  const managerEnterpriseLabel = useMemo(() => {
    const id = positiveId(data?.scope?.enterprise_id ?? user?.enterprise_id);
    const fromOptions = fieldOptionsState.enterprises.find((item) => item.id === id)?.label;
    const fromRows = (data?.breakdowns?.enterprises || []).find((item) => positiveId(item?.enterprise_id) === id)?.enterprise_name;
    const fromList = optionList(enterprises, 'id', 'name').find((item) => item.id === id)?.label;
    return fromOptions || fromRows || fromList || 'Ваше предприятие';
  }, [data, enterprises, fieldOptionsState.enterprises, user?.enterprise_id]);

  const scopeLabel = useMemo(() => {
    const scope = data?.scope;
    if (!scope) return '';
    const parts = [];
    if (scope.enterprise_id === null || scope.enterprise_id === undefined) parts.push('Все предприятия');
    else {
      const name = scope.authorization === 'tenant'
        ? managerEnterpriseLabel
        : enterpriseOptions.find((item) => item.id === scope.enterprise_id)?.label;
      parts.push(`Предприятие: ${name || `#${scope.enterprise_id}`}`);
    }
    if (scope.field_id) {
      parts.push(`Поле: ${fieldOptions.find((item) => item.id === scope.field_id)?.label || `#${scope.field_id}`}`);
    }
    if (scope.current_crop_type_id) {
      parts.push(`Текущая культура: ${cropOptions.find((item) => item.id === scope.current_crop_type_id)?.label || `#${scope.current_crop_type_id}`}`);
    }
    return parts.join(' · ');
  }, [cropOptions, data, enterpriseOptions, fieldOptions, managerEnterpriseLabel]);

  const applyPreset = useCallback((days) => {
    const period = presetPeriod(days);
    setCustomPeriod(period);
    setPeriodError('');
    setFilters((current) => ({ ...current, preset: days, ...period, fieldOffset: 0 }));
  }, []);

  const changeCustomPeriod = useCallback((patch) => {
    setCustomPeriod((current) => ({ ...current, ...patch }));
    setPeriodError('');
  }, []);

  const applyCustomPeriod = useCallback((event) => {
    event.preventDefault();
    const check = validatePeriod(customPeriod.dateFrom, customPeriod.dateTo);
    if (!check.valid) {
      setPeriodError(check.error);
      return;
    }
    setPeriodError('');
    setFilters((current) => ({ ...current, preset: null, ...customPeriod, fieldOffset: 0 }));
  }, [customPeriod]);

  const changeEnterprise = useCallback((value) => {
    setFilters((current) => ({ ...current, enterpriseId: value, fieldId: '', currentCropTypeId: '', fieldOffset: 0 }));
  }, []);
  const changeField = useCallback((value) => {
    setFilters((current) => ({ ...current, fieldId: value, currentCropTypeId: '', fieldOffset: 0 }));
  }, []);
  const changeCrop = useCallback((value) => {
    setFilters((current) => ({ ...current, currentCropTypeId: value, fieldOffset: 0 }));
  }, []);
  const changeGranularity = useCallback((value) => {
    setFilters((current) => ({ ...current, granularity: value }));
  }, []);
  const changeFieldPage = useCallback((offset) => {
    setFilters((current) => ({ ...current, fieldOffset: offset }));
  }, []);
  const drillEnterprise = useCallback((enterpriseId) => {
    if (isAdmin) changeEnterprise(String(enterpriseId));
  }, [changeEnterprise, isAdmin]);
  const drillCrop = useCallback((cropId) => changeCrop(String(cropId)), [changeCrop]);
  const drillField = useCallback((row) => {
    setFilters((current) => ({
      ...current,
      enterpriseId: isAdmin ? String(row.enterprise_id) : current.enterpriseId,
      fieldId: String(row.field_id),
      currentCropTypeId: '',
      fieldOffset: 0,
    }));
  }, [isAdmin]);
  const resetFilters = useCallback(() => {
    const next = initialFilters();
    setCustomPeriod({ dateFrom: next.dateFrom, dateTo: next.dateTo });
    setPeriodError('');
    setFilters(next);
  }, []);
  // A preset period ends today: a reload after midnight (Asia/Tashkent) moves
  // it forward instead of silently repeating yesterday's window.
  const reload = useCallback(() => {
    if (filters.preset) {
      const period = presetPeriod(filters.preset);
      if (period.dateFrom !== filters.dateFrom || period.dateTo !== filters.dateTo) {
        setCustomPeriod(period);
        setFilters((current) => ({ ...current, ...period }));
      }
    }
    setReloadToken((value) => value + 1);
  }, [filters.dateFrom, filters.dateTo, filters.preset]);

  const generatedAt = formatTashkentDateTime(data?.generated_at, 'время не указано');
  const empty = data && data.coverage?.fields_in_scope === 0;
  const statusText = view.status === 'loading' ? 'Загружаем показатели…'
    : view.status === 'refreshing' ? 'Обновляем показатели…'
      : view.status === 'ready' ? `Показатели сформированы ${generatedAt}.`
        : view.error ? `Ошибка: ${view.error.title}.` : '';

  return (
    <section className="h-full overflow-y-auto px-3 pb-24 pt-20 sm:px-4 md:px-6" aria-label="Управленческая аналитика" data-testid="management-analytics-page">
      <div className="mx-auto max-w-[1560px] space-y-4">
        <AnalyticsFilters
          isAdmin={isAdmin}
          filters={filters}
          customPeriod={customPeriod}
          periodError={periodError}
          enterpriseOptions={enterpriseOptions}
          managerEnterpriseLabel={managerEnterpriseLabel}
          fieldOptions={fieldOptions}
          fieldOptionsState={needsEnterpriseFirst ? 'idle' : fieldOptionsState.status}
          cropOptions={cropOptions}
          cropOptionsState={cropOptionsState.scope === optionScope ? 'ready' : 'loading'}
          busy={view.status === 'loading' || view.status === 'refreshing'}
          onPreset={applyPreset}
          onCustomPeriodChange={changeCustomPeriod}
          onCustomPeriodApply={applyCustomPeriod}
          onEnterpriseChange={changeEnterprise}
          onFieldChange={changeField}
          onCropChange={changeCrop}
          onReset={resetFilters}
          onReload={reload}
        />

        <p role="status" aria-live="polite" className="text-xs text-agro-muted" data-testid="management-analytics-status" data-state={view.status}>{statusText}</p>

        {!online && (
          <div role="status" className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-950">
            Нет сети. {data ? `Показаны данные, сформированные ${generatedAt}; обновить их сейчас нельзя.` : 'Показатели загрузятся после восстановления связи.'}
          </div>
        )}

        {view.status === 'loading' && <AnalyticsSkeleton />}
        {view.error && !data && <AnalyticsErrorPanel error={view.error} onRetry={reload} onReset={resetFilters} />}
        {view.error && data && <RefreshErrorBanner error={view.error} generatedAt={generatedAt} onRetry={reload} />}

        {data && (
          <>
            {data.definitions_version !== MANAGEMENT_ANALYTICS_DEFINITIONS_VERSION && (
              <p role="status" className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
                Сервер вернул другую версию определений показателей ({String(data.definitions_version || 'не указана')}).
                Подписи на странице рассчитаны на {MANAGEMENT_ANALYTICS_DEFINITIONS_VERSION} и могут не соответствовать данным.
              </p>
            )}
            <AnalyticsContext data={data} scopeLabel={scopeLabel} />
            {empty ? (
              <EmptyScopePanel onReset={resetFilters} />
            ) : (
              <div className={`space-y-8 transition-opacity ${view.status === 'refreshing' ? 'opacity-60' : ''}`} aria-busy={view.status === 'refreshing'}>
                <CurrentStateSection data={data} />
                <PeriodSection data={data} requestedGranularity={filters.granularity} onGranularityChange={changeGranularity} />
                <BreakdownSection
                  data={data}
                  isAdmin={isAdmin}
                  paging={view.status === 'refreshing'}
                  onDrillEnterprise={drillEnterprise}
                  onDrillCrop={drillCrop}
                  onDrillField={drillField}
                  onFieldPage={changeFieldPage}
                />
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
