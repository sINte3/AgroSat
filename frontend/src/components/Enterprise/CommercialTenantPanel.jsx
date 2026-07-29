import { useCallback, useEffect, useRef, useState } from 'react';
import {
  createTenantLifecycleRequest,
  decideTenantLifecycleRequest,
  getCommercialBoundary,
  getCommercialMemberships,
  getTenantLifecycleRequests,
} from '../../api/commercialTenant';

const statusLabels = {
  requested: 'Ожидает решения',
  approved: 'Одобрено',
  rejected: 'Отклонено',
  executing: 'Выполняется отдельной операцией',
  completed: 'Завершено',
  failed: 'Ошибка выполнения',
};

function DataCard({ title, children }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="mb-3 text-sm font-semibold text-slate-900">{title}</h3>
      {children}
    </section>
  );
}

function KeyValues({ values, empty = 'Не настроено' }) {
  const entries = Object.entries(values || {});
  if (!entries.length) return <p className="text-sm text-slate-500">{empty}</p>;
  return (
    <dl className="grid gap-2 sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="rounded-lg bg-slate-50 px-3 py-2">
          <dt className="break-all text-xs text-slate-500">{key}</dt>
          <dd className="mt-1 break-all text-sm font-medium text-slate-900">
            {typeof value === 'boolean' ? (value ? 'Включено' : 'Выключено') : value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export default function CommercialTenantPanel({ enterpriseId, role }) {
  const [boundary, setBoundary] = useState(null);
  const [memberships, setMemberships] = useState([]);
  const [requests, setRequests] = useState([]);
  const [state, setState] = useState('loading');
  const [message, setMessage] = useState('');
  const [requestType, setRequestType] = useState('export');
  const [reason, setReason] = useState('');
  const [decisionNotes, setDecisionNotes] = useState({});
  const generationRef = useRef(0);
  const abortRef = useRef(null);
  const canManage = role === 'admin' || role === 'manager';
  const isAdmin = role === 'admin';

  const load = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const generation = ++generationRef.current;
    setState('loading');
    setMessage('');
    try {
      const [boundaryValue, membershipValue, requestValue] = await Promise.all([
        getCommercialBoundary(enterpriseId, controller.signal),
        getCommercialMemberships(enterpriseId, controller.signal),
        getTenantLifecycleRequests(enterpriseId, controller.signal),
      ]);
      if (generation !== generationRef.current) return;
      setBoundary(boundaryValue);
      setMemberships(membershipValue.items || []);
      setRequests(requestValue.items || []);
      setState('ready');
    } catch (error) {
      if (error?.code !== 'ERR_CANCELED' && generation === generationRef.current) {
        setState('error');
        setMessage(error?.response?.data?.detail || 'Не удалось загрузить коммерческий контур');
      }
    }
  }, [enterpriseId]);

  useEffect(() => {
    void load();
    return () => {
      generationRef.current += 1;
      abortRef.current?.abort();
    };
  }, [load]);

  async function submitRequest(event) {
    event.preventDefault();
    if (!canManage || reason.trim().length < 10) return;
    setMessage('');
    try {
      await createTenantLifecycleRequest(
        enterpriseId,
        { request_type: requestType, reason: reason.trim() },
        crypto.randomUUID(),
      );
      setReason('');
      setMessage('Запрос зарегистрирован. Данные ещё не экспортированы и не удалены.');
      await load();
    } catch (error) {
      setMessage(error?.response?.data?.detail || 'Не удалось зарегистрировать запрос');
    }
  }

  async function decide(item, decision) {
    const note = (decisionNotes[item.id] || '').trim();
    if (!isAdmin || note.length < 3) return;
    setMessage('');
    try {
      await decideTenantLifecycleRequest(enterpriseId, item.id, decision, note);
      setMessage('Решение сохранено. Исполнение остаётся отдельной операцией.');
      await load();
    } catch (error) {
      setMessage(error?.response?.data?.detail || 'Не удалось сохранить решение');
    }
  }

  if (state === 'loading') {
    return <p className="py-10 text-center text-sm text-slate-500" aria-live="polite">Загрузка коммерческого контура…</p>;
  }
  if (state === 'error') {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-4" role="alert">
        <p className="text-sm text-red-800">{message}</p>
        <button type="button" className="mt-3 min-h-11 rounded-lg border border-red-300 px-4 text-sm" onClick={load}>Повторить</button>
      </div>
    );
  }

  const profile = boundary?.profile || {};
  return (
    <div className="space-y-4" data-testid="commercial-tenant-panel">
      <div className="rounded-xl border border-blue-200 bg-blue-50 p-4">
        <h2 className="text-base font-semibold text-blue-950">Коммерческий контур предприятия</h2>
        <p className="mt-1 text-sm text-blue-900">
          Оплата не обрабатывается. Экспорт и удаление создают только проверяемый
          запрос, а не запускают внешнее или необратимое действие.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <DataCard title="План и статус">
          <KeyValues values={{
            plan: profile.plan_code,
            subscription: profile.subscription_state,
            payment_processing: boundary?.billing?.payment_processing ? 'enabled' : 'disabled',
          }} />
        </DataCard>
        <DataCard title="Изоляция ресурсов"><KeyValues values={boundary?.namespaces} /></DataCard>
        <DataCard title="Функции"><KeyValues values={profile.feature_flags} /></DataCard>
        <DataCard title="Квоты и хранение">
          <KeyValues values={{ ...(profile.quota_limits || {}), ...(profile.retention_policy || {}) }} />
        </DataCard>
      </div>

      <DataCard title="Провайдеры">
        {!boundary?.providers?.length ? (
          <p className="text-sm text-slate-500">Провайдеры не настроены.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {boundary.providers.map((provider) => (
              <li key={provider.provider_code} className="flex min-h-11 items-center justify-between gap-3 py-2">
                <span className="text-sm font-medium text-slate-900">{provider.provider_code}</span>
                <span className="text-xs text-slate-600">{provider.status}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-xs text-slate-500">
          Секреты и их внутренние ссылки никогда не передаются в браузер.
        </p>
      </DataCard>

      <DataCard title={`Участники (${memberships.length})`}>
        {!memberships.length ? (
          <p className="text-sm text-slate-500">Активные memberships не найдены.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {memberships.map((membership) => (
              <li key={membership.id} className="flex min-h-11 items-center justify-between gap-3 py-2">
                <span className="text-sm text-slate-900">{membership.full_name || `User #${membership.user_id}`}</span>
                <span className="text-xs text-slate-600">{membership.membership_role} · {membership.status}</span>
              </li>
            ))}
          </ul>
        )}
      </DataCard>

      {canManage && (
        <DataCard title="Запросить переносимость или удаление">
          <form className="grid gap-3" onSubmit={submitRequest}>
            <label className="grid gap-1 text-sm text-slate-700">
              Тип запроса
              <select className="min-h-11 rounded-lg border border-slate-300 bg-white px-3" value={requestType} onChange={(event) => setRequestType(event.target.value)}>
                <option value="export">Экспорт данных</option>
                {isAdmin && <option value="deletion">Удаление tenant</option>}
              </select>
            </label>
            <label className="grid gap-1 text-sm text-slate-700">
              Основание
              <textarea className="min-h-24 rounded-lg border border-slate-300 p-3" value={reason} maxLength={2000} onChange={(event) => setReason(event.target.value)} />
            </label>
            <button type="submit" className="min-h-11 justify-self-start rounded-lg bg-slate-900 px-4 text-sm font-medium text-white disabled:opacity-50" disabled={reason.trim().length < 10}>
              Зарегистрировать запрос
            </button>
          </form>
        </DataCard>
      )}

      <DataCard title="Журнал запросов">
        {!requests.length ? (
          <p className="text-sm text-slate-500">Запросов пока нет.</p>
        ) : (
          <div className="space-y-3">
            {requests.map((item) => (
              <article key={item.id} className="rounded-lg border border-slate-200 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <strong className="text-sm text-slate-900">#{item.id} · {item.request_type === 'export' ? 'Экспорт' : 'Удаление'}</strong>
                  <span className="text-xs text-slate-600">{statusLabels[item.status] || item.status}</span>
                </div>
                <p className="mt-2 text-sm text-slate-700">{item.reason}</p>
                {isAdmin && item.status === 'requested' && (
                  <div className="mt-3 grid gap-2">
                    <label className="grid gap-1 text-sm text-slate-700">
                      Обоснование решения
                      <textarea
                        className="min-h-20 rounded-lg border border-slate-300 p-3"
                        value={decisionNotes[item.id] || ''}
                        onChange={(event) => setDecisionNotes((value) => ({ ...value, [item.id]: event.target.value }))}
                      />
                    </label>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" className="min-h-11 rounded-lg bg-emerald-700 px-4 text-sm text-white" onClick={() => decide(item, 'approved')}>Одобрить</button>
                      <button type="button" className="min-h-11 rounded-lg border border-red-300 px-4 text-sm text-red-800" onClick={() => decide(item, 'rejected')}>Отклонить</button>
                    </div>
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
      </DataCard>
      <p className="text-sm text-slate-700" aria-live="polite">{message}</p>
    </div>
  );
}
