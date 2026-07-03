import { useState, useEffect, useCallback } from 'react';
import { getManagementReportSummary } from '../api/client';

// ─── Severity count extraction helper ─────────────────────────────────────

function getSeverityCounts(alerts) {
  if (!alerts || !Array.isArray(alerts.latest_items) || !alerts.latest_items.length) {
    return { total: 0, critical: 0, warning: 0, info: 0 };
  }
  const counts = { total: 0, critical: 0, warning: 0, info: 0 };
  alerts.latest_items.forEach((a) => {
    counts.total++;
    if (a.severity === 'critical') counts.critical++;
    else if (a.severity === 'warning') counts.warning++;
    else if (a.severity === 'info') counts.info++;
  });
  return counts;
}

// ─── NDVI color ──────────────────────────────────────────────────────────

function getNDVIColor(ndvi) {
  if (ndvi == null) return 'text-agro-muted';
  if (ndvi >= 0.6) return 'text-agro-accent';
  if (ndvi >= 0.3) return 'text-agro-warning';
  return 'text-agro-danger';
}

// ─── Severity badge color ────────────────────────────────────────────────

function getSeverityColor(severity) {
  switch (severity) {
    case 'critical': return 'bg-red-100 text-red-700 border-red-200';
    case 'warning':  return 'bg-amber-100 text-amber-700 border-amber-200';
    case 'info':     return 'bg-blue-100 text-blue-700 border-blue-200';
    default:         return 'bg-gray-100 text-gray-600 border-gray-200';
  }
}

// ─── Severity label ──────────────────────────────────────────────────────

function getSeverityLabel(severity) {
  switch (severity) {
    case 'critical': return 'Критический';
    case 'warning':  return 'Важный';
    case 'info':     return 'Информационный';
    default:         return severity;
  }
}

// ─── Icons ───────────────────────────────────────────────────────────────

function ReportIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
      />
    </svg>
  );
}

function FieldIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5z"
      />
    </svg>
  );
}

function AreaIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4"
      />
    </svg>
  );
}

function NoDataIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M18.364 5.636a9 9 0 11-12.728 0m12.728 0a9 9 0 00-12.728 0"
      />
    </svg>
  );
}

function NDVIIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z"
      />
    </svg>
  );
}

function AlertIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
      />
    </svg>
  );
}

function FreshnessIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
      />
    </svg>
  );
}

function EnterpriseIcon({ className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M3 21h18M5 21V7l8-4v18M19 21V11l-6-4M9 9h1M9 13h1M9 17h1"
      />
    </svg>
  );
}

// ─── ReportPage component ────────────────────────────────────────────────

export default function ReportsPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sortKey, setSortKey] = useState('name');
  const [sortDir, setSortDir] = useState('asc');
  const [searchQuery, setSearchQuery] = useState('');

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getManagementReportSummary();
      setData(result);
    } catch (err) {
      if (err?.response?.status === 404) {
        setError('Отчёты пока недоступны (эндпоинт не найден).');
      } else if (err?.response?.status === 403) {
        setError('Нет доступа к разделу отчётов.');
      } else {
        setError('Ошибка загрузки отчёта. Сервер временно недоступен.');
      }
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // ─── Enterprise sort/search ────────────────────────────────────────────

  const getSortedFilteredEnterprises = () => {
    if (!data?.enterprises || !Array.isArray(data.enterprises)) return [];
    let list = [...data.enterprises];
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase();
      list = list.filter((e) =>
        (e.name || '').toLowerCase().includes(q) ||
        (e.farm_name || '').toLowerCase().includes(q)
      );
    }
    list.sort((a, b) => {
      let aVal, bVal;
      switch (sortKey) {
        case 'name':          aVal = (a.name || a.farm_name || ''); bVal = (b.name || b.farm_name || ''); break;
        case 'fields':        aVal = a.field_count ?? 0;            bVal = b.field_count ?? 0;            break;
        case 'hectares':      aVal = a.total_hectares ?? 0;         bVal = b.total_hectares ?? 0;         break;
        case 'alerts':        aVal = a.active_alerts ?? 0;          bVal = b.active_alerts ?? 0;          break;
        case 'problemFields': aVal = a.problem_fields ?? 0;         bVal = b.problem_fields ?? 0;         break;
        case 'noData':        aVal = a.fields_no_data ?? 0;         bVal = b.fields_no_data ?? 0;         break;
        case 'avgNdvi':       aVal = a.avg_ndvi ?? -999;           bVal = b.avg_ndvi ?? -999;            break;
        default:              aVal = a.name || '';                  bVal = b.name || '';                  break;
      }
      if (typeof aVal === 'string') {
        return sortDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      }
      return sortDir === 'asc' ? aVal - bVal : bVal - aVal;
    });
    return list;
  };

  const toggleSort = (key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

  const SortArrow = ({ columnKey }) => {
    if (sortKey !== columnKey) return <span className="ml-1 text-agro-muted opacity-30">↕</span>;
    return <span className="ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>;
  };

  // ─── Error state ───────────────────────────────────────────────────────

  if (error && !loading) {
    return (
      <div className="p-6 pt-16 h-full flex flex-col">
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <div className="w-14 h-14 rounded-full bg-red-100 flex items-center justify-center mb-4">
            <svg className="w-7 h-7 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
          </div>
          <p className="text-sm text-red-500 font-medium mb-2">{error}</p>
          <button onClick={loadData} className="text-sm text-agro-accent underline">
            Повторить
          </button>
        </div>
      </div>
    );
  }

  // ─── Loading state ─────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="p-6 pt-16 h-full flex flex-col overflow-y-auto">
        {/* Header skeleton */}
        <div className="mb-6">
          <div className="h-7 bg-agro-surface2 rounded w-32 mb-2 animate-pulse" />
          <div className="h-4 bg-agro-surface2 rounded w-64 animate-pulse" />
        </div>

        {/* Summary cards skeleton */}
        <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 mb-8">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="card animate-pulse">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 rounded-lg bg-agro-surface2" />
                <div>
                  <div className="h-3 bg-agro-surface2 rounded w-20 mb-2" />
                  <div className="h-6 bg-agro-surface2 rounded w-12" />
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Table skeleton */}
        <div className="card p-4 mb-8">
          <div className="h-5 bg-agro-surface2 rounded w-48 mb-4 animate-pulse" />
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-10 bg-agro-surface2 rounded mb-2 animate-pulse" />
          ))}
        </div>

        {/* Alerts skeleton */}
        <div className="card p-4 mb-8">
          <div className="h-5 bg-agro-surface2 rounded w-32 mb-4 animate-pulse" />
          <div className="h-10 bg-agro-surface2 rounded mb-2 animate-pulse" />
        </div>
      </div>
    );
  }

  // ─── Empty state ───────────────────────────────────────────────────────

  if (!data) {
    return (
      <div className="p-6 pt-16 h-full flex flex-col">
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <div className="w-16 h-16 rounded-full bg-green-100 flex items-center justify-center mb-4">
            <svg className="w-8 h-8 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
          </div>
          <p className="font-semibold text-lg text-agro-text">Нет данных отчёта</p>
          <p className="text-sm text-agro-muted mt-1">Управленческая сводка пока не сформирована.</p>
          <button onClick={loadData} className="mt-4 btn-secondary text-sm">Обновить</button>
        </div>
      </div>
    );
  }

  // ─── Destructure response ──────────────────────────────────────────────

  const { generated_at, date_range, summary, enterprises, alerts, data_freshness, limitations } = data;
  const processedAlerts = alerts ? getSeverityCounts(alerts) : { total: 0, critical: 0, warning: 0, info: 0 };
  const sortedEnterprises = getSortedFilteredEnterprises();

  return (
    <div className="p-6 pt-16 h-full flex flex-col overflow-y-auto">

      {/* ═══════════════════════════════════════════════════════════
          Section 4.1: Page Header
         ═══════════════════════════════════════════════════════════ */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-agro-text">Отчёты</h1>
        <p className="text-sm text-agro-muted mt-1">Управленческая сводка по состоянию полей</p>
        <div className="flex flex-wrap gap-4 mt-2 text-xs text-agro-muted">
          {generated_at && (
            <span>
              Сформировано: <span className="font-medium text-agro-text">{new Date(generated_at).toLocaleString('ru-RU')}</span>
            </span>
          )}
          {date_range?.from && date_range?.to && (
            <span>
              Период: <span className="font-medium text-agro-text">
                {new Date(date_range.from).toLocaleDateString('ru-RU')} — {new Date(date_range.to).toLocaleDateString('ru-RU')}
              </span>
            </span>
          )}
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════════════
          Section 4.2: Summary Cards
         ═══════════════════════════════════════════════════════════ */}
      {summary && (
        <section className="mb-8">
          <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {/* Всего полей */}
            <div className="card flex items-center gap-4">
              <div className="p-2.5 rounded-lg bg-agro-surface2 text-agro-accent">
                <FieldIcon className="w-6 h-6" />
              </div>
              <div>
                <p className="text-sm text-agro-muted">Всего полей</p>
                <p className="text-2xl font-bold text-agro-text">
                  {summary.total_fields != null ? summary.total_fields : '—'}
                </p>
              </div>
            </div>

            {/* Всего гектаров */}
            <div className="card flex items-center gap-4">
              <div className="p-2.5 rounded-lg bg-agro-surface2 text-agro-accent">
                <AreaIcon className="w-6 h-6" />
              </div>
              <div>
                <p className="text-sm text-agro-muted">Всего гектаров</p>
                <p className="text-2xl font-bold text-agro-text">
                  {summary.total_hectares != null
                    ? Number(summary.total_hectares).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' га'
                    : '—'}
                </p>
              </div>
            </div>

            {/* С данными */}
            <div className="card flex items-center gap-4">
              <div className="p-2.5 rounded-lg bg-agro-surface2 text-agro-accent">
                <NDVIIcon className="w-6 h-6" />
              </div>
              <div>
                <p className="text-sm text-agro-muted">С данными</p>
                <p className="text-2xl font-bold text-agro-text">
                  {summary.fields_with_data != null ? summary.fields_with_data : '—'}
                </p>
              </div>
            </div>

            {/* Без данных */}
            <div className="card flex items-center gap-4">
              <div className="p-2.5 rounded-lg bg-agro-surface2 text-agro-muted">
                <NoDataIcon className="w-6 h-6" />
              </div>
              <div>
                <p className="text-sm text-agro-muted">Без данных</p>
                <p className="text-2xl font-bold text-agro-text">
                  {summary.fields_without_data != null ? summary.fields_without_data : '—'}
                </p>
              </div>
            </div>

            {/* Средний NDVI */}
            <div className="card flex items-center gap-4">
              <div className={`p-2.5 rounded-lg bg-agro-surface2 ${getNDVIColor(summary.avg_ndvi)}`}>
                <NDVIIcon className="w-6 h-6" />
              </div>
              <div>
                <p className="text-sm text-agro-muted">Средний NDVI</p>
                <p className={`text-2xl font-bold ${getNDVIColor(summary.avg_ndvi)}`}>
                  {summary.avg_ndvi != null ? summary.avg_ndvi.toFixed(3) : '—'}
                </p>
              </div>
            </div>

            {/* Активные предупреждения */}
            <div className="card flex items-center gap-4">
              <div className={`p-2.5 rounded-lg bg-agro-surface2 ${
                processedAlerts.critical > 0 ? 'text-agro-danger' : 'text-agro-warning'
              }`}>
                <AlertIcon className="w-6 h-6" />
              </div>
              <div>
                <p className="text-sm text-agro-muted">Активные предупреждения</p>
                <p className={`text-2xl font-bold ${
                  processedAlerts.critical > 0 ? 'text-agro-danger' : 'text-agro-text'
                }`}>
                  {processedAlerts.total}
                </p>
              </div>
            </div>
          </div>
        </section>
      )}

      {/* ═══════════════════════════════════════════════════════════
          Section 4.3: Enterprise Comparison
         ═══════════════════════════════════════════════════════════ */}
      <section className="mb-8">
        <div className="card p-5">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <EnterpriseIcon className="w-5 h-5 text-agro-accent" />
              <h2 className="text-lg font-semibold text-agro-text">Сравнение предприятий</h2>
              {Array.isArray(enterprises) && (
                <span className="bg-agro-accent/20 text-agro-accent text-xs font-medium px-2 py-0.5 rounded-full">
                  {enterprises.length}
                </span>
              )}
            </div>
            {/* Search */}
            <div className="relative">
              <input
                type="text"
                placeholder="Поиск предприятия..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="input text-sm pl-8 pr-3 py-1.5 w-48"
              />
              <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-agro-muted"
                fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                />
              </svg>
            </div>
          </div>

          {!Array.isArray(enterprises) || enterprises.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 text-center">
              <div className="w-12 h-12 rounded-full bg-gray-100 flex items-center justify-center mb-3">
                <EnterpriseIcon className="w-6 h-6 text-agro-muted" />
              </div>
              <p className="text-agro-text font-medium">Нет данных по предприятиям</p>
              <p className="text-sm text-agro-muted mt-1">
                Информация по предприятиям будет доступна после настройки.
              </p>
            </div>
          ) : sortedEnterprises.length === 0 ? (
            <div className="text-center py-8 text-sm text-agro-muted">
              Ничего не найдено по запросу «{searchQuery}»
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-agro-border">
                    <th
                      className="text-left py-2.5 pr-4 text-agro-muted font-medium cursor-pointer hover:text-agro-text whitespace-nowrap"
                      onClick={() => toggleSort('name')}
                    >
                      Предприятие <SortArrow columnKey="name" />
                    </th>
                    <th
                      className="text-right py-2.5 px-3 text-agro-muted font-medium cursor-pointer hover:text-agro-text whitespace-nowrap"
                      onClick={() => toggleSort('fields')}
                    >
                      Поля <SortArrow columnKey="fields" />
                    </th>
                    <th
                      className="text-right py-2.5 px-3 text-agro-muted font-medium cursor-pointer hover:text-agro-text whitespace-nowrap"
                      onClick={() => toggleSort('hectares')}
                    >
                      га <SortArrow columnKey="hectares" />
                    </th>
                    <th
                      className="text-right py-2.5 px-3 text-agro-muted font-medium cursor-pointer hover:text-agro-text whitespace-nowrap"
                      onClick={() => toggleSort('alerts')}
                    >
                      Алерты <SortArrow columnKey="alerts" />
                    </th>
                    <th
                      className="text-right py-2.5 px-3 text-agro-muted font-medium cursor-pointer hover:text-agro-text whitespace-nowrap"
                      onClick={() => toggleSort('problemFields')}
                    >
                      Проблемные <SortArrow columnKey="problemFields" />
                    </th>
                    <th
                      className="text-right py-2.5 px-3 text-agro-muted font-medium cursor-pointer hover:text-agro-text whitespace-nowrap"
                      onClick={() => toggleSort('noData')}
                    >
                      Без данных <SortArrow columnKey="noData" />
                    </th>
                    <th
                      className="text-right py-2.5 pl-3 text-agro-muted font-medium cursor-pointer hover:text-agro-text whitespace-nowrap"
                      onClick={() => toggleSort('avgNdvi')}
                    >
                      Средн. NDVI <SortArrow columnKey="avgNdvi" />
                    </th>
                    <th className="text-right py-2.5 pl-3 text-agro-muted font-medium whitespace-nowrap">
                      Данные на
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sortedEnterprises.map((ent, idx) => (
                    <tr
                      key={ent.id || ent.name || idx}
                      className={`border-b border-agro-border/50 ${
                        idx % 2 === 0 ? 'bg-transparent' : 'bg-agro-surface2/30'
                      } hover:bg-agro-hover/50 transition-colors`}
                    >
                      <td className="py-2.5 pr-4 font-medium text-agro-text">
                        {ent.name || ent.farm_name || '—'}
                      </td>
                      <td className="py-2.5 px-3 text-right text-agro-text">
                        {ent.field_count ?? '—'}
                      </td>
                      <td className="py-2.5 px-3 text-right text-agro-text">
                        {ent.total_hectares != null
                          ? Number(ent.total_hectares).toLocaleString('ru-RU', { maximumFractionDigits: 1 })
                          : '—'}
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        <span className={ent.active_alerts > 0 ? 'text-agro-danger font-medium' : 'text-agro-text'}>
                          {ent.active_alerts ?? '—'}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        <span className={ent.problem_fields > 0 ? 'text-agro-danger font-medium' : 'text-agro-text'}>
                          {ent.problem_fields ?? '—'}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        <span className={ent.fields_no_data > 0 ? 'text-agro-warning font-medium' : 'text-agro-text'}>
                          {ent.fields_no_data ?? '—'}
                        </span>
                      </td>
                      <td className={`py-2.5 px-3 text-right font-medium ${getNDVIColor(ent.avg_ndvi)}`}>
                        {ent.avg_ndvi != null ? ent.avg_ndvi.toFixed(3) : '—'}
                      </td>
                      <td className="py-2.5 pl-3 text-right text-agro-muted text-xs">
                        {ent.latest_data_date
                          ? new Date(ent.latest_data_date).toLocaleDateString('ru-RU')
                          : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════
          Section 4.4: Alerts
         ═══════════════════════════════════════════════════════════ */}
      <section className="mb-8">
        <div className="card p-5">
          <div className="flex items-center gap-2 mb-4">
            <AlertIcon className="w-5 h-5 text-agro-accent" />
            <h2 className="text-lg font-semibold text-agro-text">Сводка предупреждений</h2>
          </div>

          {!alerts || !Array.isArray(alerts.latest_items) || alerts.latest_items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 text-center">
              <div className="w-12 h-12 rounded-full bg-green-100 flex items-center justify-center mb-3">
                <svg className="w-6 h-6 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
                  />
                </svg>
              </div>
              <p className="text-agro-text font-medium">Нет активных предупреждений</p>
              <p className="text-sm text-agro-muted mt-1">
                {alerts?.active_total != null
                  ? `Всего: ${alerts.active_total}`
                  : 'Все предупреждения обработаны.'}
              </p>
            </div>
          ) : (
            <>
              {/* Severity breakdown */}
              <div className="flex items-center gap-3 mb-4 flex-wrap text-sm">
                <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-50 border border-agro-border">
                  <span className="font-medium text-agro-text">{processedAlerts.total}</span>
                  <span className="text-agro-muted">всего</span>
                </div>
                {processedAlerts.critical > 0 && (
                  <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-50 border border-red-200">
                    <span className="w-2 h-2 rounded-full bg-red-500" />
                    <span className="font-medium text-red-700">{processedAlerts.critical}</span>
                    <span className="text-red-500">критичных</span>
                  </div>
                )}
                {processedAlerts.warning > 0 && (
                  <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-50 border border-amber-200">
                    <span className="w-2 h-2 rounded-full bg-amber-500" />
                    <span className="font-medium text-amber-700">{processedAlerts.warning}</span>
                    <span className="text-amber-500">важных</span>
                  </div>
                )}
                {processedAlerts.info > 0 && (
                  <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-50 border border-blue-200">
                    <span className="w-2 h-2 rounded-full bg-blue-500" />
                    <span className="font-medium text-blue-700">{processedAlerts.info}</span>
                    <span className="text-blue-500">информационных</span>
                  </div>
                )}
                <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-green-50 border border-green-200">
                  <span className="w-2 h-2 rounded-full bg-green-500" />
                  <span className="font-medium text-green-700">Только активные</span>
                </div>
              </div>

              {/* Latest items */}
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-agro-border">
                      <th className="text-left py-2 pr-4 text-agro-muted font-medium">Уровень</th>
                      <th className="text-left py-2 pr-4 text-agro-muted font-medium">Сообщение</th>
                      <th className="text-right py-2 pl-4 text-agro-muted font-medium">Дата</th>
                    </tr>
                  </thead>
                  <tbody>
                    {alerts.latest_items.slice(0, 10).map((alert, idx) => (
                      <tr key={alert.id || idx}
                        className="border-b border-agro-border/50 hover:bg-agro-hover/50 transition-colors"
                      >
                        <td className="py-2 pr-4">
                          <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium border ${getSeverityColor(alert.severity)}`}>
                            {getSeverityLabel(alert.severity)}
                          </span>
                        </td>
                        <td className="py-2 pr-4 text-agro-text">
                          {alert.message || alert.description || '—'}
                        </td>
                        <td className="py-2 pl-4 text-right text-agro-muted text-xs whitespace-nowrap">
                          {alert.created_at
                            ? new Date(alert.created_at).toLocaleString('ru-RU')
                            : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {alerts.latest_items.length > 10 && (
                <p className="text-xs text-agro-muted mt-3 text-center">
                  Показаны последние 10 из {alerts.latest_items.length}
                </p>
              )}
            </>
          )}
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════
          Section 4.5: Data Freshness
         ═══════════════════════════════════════════════════════════ */}
      {data_freshness && (
        <section className="mb-8">
          <div className="card p-5">
            <div className="flex items-center gap-2 mb-4">
              <FreshnessIcon className="w-5 h-5 text-agro-accent" />
              <h2 className="text-lg font-semibold text-agro-text">Свежесть данных</h2>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              <div className="p-4 rounded-lg bg-agro-surface2">
                <p className="text-xs text-agro-muted mb-1">Последний NDVI</p>
                <p className="text-lg font-semibold text-agro-text">
                  {data_freshness.latest_ndvi_date
                    ? new Date(data_freshness.latest_ndvi_date).toLocaleDateString('ru-RU')
                    : '—'}
                </p>
              </div>
              <div className="p-4 rounded-lg bg-agro-surface2">
                <p className="text-xs text-agro-muted mb-1">Последний спутниковый индекс</p>
                <p className="text-lg font-semibold text-agro-text">
                  {data_freshness.latest_index_date
                    ? new Date(data_freshness.latest_index_date).toLocaleDateString('ru-RU')
                    : '—'}
                </p>
              </div>
              <div className="p-4 rounded-lg bg-agro-surface2">
                <p className="text-xs text-agro-muted mb-1">Полей без данных</p>
                <p className={`text-lg font-semibold ${
                  data_freshness.fields_without_data > 0 ? 'text-agro-warning' : 'text-agro-text'
                }`}>
                  {data_freshness.fields_without_data != null ? data_freshness.fields_without_data : '—'}
                </p>
              </div>
            </div>
            {data_freshness.note && (
              <p className="mt-3 text-xs text-agro-muted italic">{data_freshness.note}</p>
            )}
          </div>
        </section>
      )}

      {/* ═══════════════════════════════════════════════════════════
          Section 4.6: Limitations
         ═══════════════════════════════════════════════════════════ */}
      {limitations && Array.isArray(limitations) && limitations.length > 0 && (
        <section className="mb-8">
          <div className="card p-5 border border-amber-200 bg-amber-50/30">
            <h2 className="text-base font-semibold text-agro-text mb-3">Ограничения отчёта</h2>
            <ul className="space-y-2">
              {limitations.map((lim, idx) => (
                <li key={idx} className="flex items-start gap-2 text-sm text-agro-muted">
                  <svg className="w-4 h-4 mt-0.5 flex-shrink-0 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                    />
                  </svg>
                  <span>{lim.limitation || lim.message || lim}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>
      )}

      {/* ═══════════════════════════════════════════════════════════
          Section 4.7: Export Placeholder
         ═══════════════════════════════════════════════════════════ */}
      <section className="mb-8">
        <div className="card p-5 border border-dashed border-agro-border bg-agro-surface2/50">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold text-agro-text">Экспорт</h2>
              <p className="text-xs text-agro-muted mt-1">
                Экспорт PDF/Excel будет добавлен отдельной задачей.
              </p>
            </div>
            <div className="flex gap-2">
              <button disabled className="px-4 py-2 rounded-lg text-sm bg-gray-200 text-gray-400 cursor-not-allowed">
                PDF
              </button>
              <button disabled className="px-4 py-2 rounded-lg text-sm bg-gray-200 text-gray-400 cursor-not-allowed">
                Excel
              </button>
            </div>
          </div>
        </div>
      </section>

    </div>
  );
}
