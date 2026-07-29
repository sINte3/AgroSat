import { useEffect, useRef, useState } from 'react';

import { getFieldTelematics } from '../../api/telematics';


const STATUS_COPY = {
  unsupported: {
    title: 'Телематика не подключена',
    description: 'Для этого поля нет подтверждённого сопоставления с техникой.',
  },
  unavailable: {
    title: 'Телематика временно недоступна',
    description: 'Провайдер не вернул подтверждённые данные. Попробуйте позже.',
  },
};


function requestWasCancelled(error) {
  return error?.name === 'AbortError' || error?.code === 'ERR_CANCELED';
}


function formatDateTime(value) {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  return parsed.toLocaleString('ru-RU', {
    dateStyle: 'short',
    timeStyle: 'short',
  });
}


function formatAge(seconds) {
  const safe = Math.max(0, Number(seconds) || 0);
  if (safe < 60) return `${Math.round(safe)} сек.`;
  if (safe < 3600) return `${Math.round(safe / 60)} мин.`;
  if (safe < 86400) return `${Math.round(safe / 3600)} ч.`;
  return `${Math.round(safe / 86400)} дн.`;
}


function stateLabel(value, trueLabel, falseLabel) {
  if (value === true) return trueLabel;
  if (value === false) return falseLabel;
  return 'Нет данных';
}


function UnitCard({ unit }) {
  const position = unit?.position || {};
  const sensors = Array.isArray(unit?.sensors) ? unit.sensors : [];
  return (
    <li className="card space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 className="font-semibold text-agro-text">{unit?.label || unit?.unit_id || 'Техника'}</h4>
          <p className="text-xs text-agro-muted">Наблюдение: {formatDateTime(position.observed_at)}</p>
        </div>
        <span
          className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
            unit?.stale
              ? 'border-amber-300 bg-amber-50 text-amber-900'
              : 'border-emerald-300 bg-emerald-50 text-emerald-800'
          }`}
        >
          {unit?.stale ? 'Данные устарели' : 'Актуально'}
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
        <div>
          <dt className="text-xs text-agro-muted">Давность</dt>
          <dd className="text-agro-text">{formatAge(unit?.age_seconds)}</dd>
        </div>
        <div>
          <dt className="text-xs text-agro-muted">Положение</dt>
          <dd className="text-agro-text">
            {Number.isFinite(Number(position.latitude)) && Number.isFinite(Number(position.longitude))
              ? `${Number(position.latitude).toFixed(5)}, ${Number(position.longitude).toFixed(5)}`
              : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-agro-muted">Движение</dt>
          <dd className="text-agro-text">
            {stateLabel(unit?.movement, 'В движении', 'Стоит')}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-agro-muted">Зажигание</dt>
          <dd className="text-agro-text">
            {stateLabel(unit?.ignition, 'Включено', 'Выключено')}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-agro-muted">В границах поля</dt>
          <dd className="text-agro-text">
            {stateLabel(unit?.field_intersection, 'Да', 'Нет')}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-agro-muted">В геозоне</dt>
          <dd className="text-agro-text">
            {stateLabel(unit?.geofence_intersection, 'Да', 'Нет')}
          </dd>
        </div>
      </dl>

      {sensors.length > 0 && (
        <div>
          <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-agro-muted">
            Доступные датчики
          </h5>
          <dl className="grid grid-cols-2 gap-2 text-sm">
            {sensors.map((sensor) => (
              <div
                className="rounded-lg border border-agro-surface2 px-3 py-2"
                key={`${unit?.unit_id || 'unit'}-${sensor.code}`}
              >
                <dt className="truncate text-xs text-agro-muted">{sensor.code}</dt>
                <dd className="font-medium text-agro-text">
                  {Number.isFinite(Number(sensor.value)) ? Number(sensor.value).toLocaleString('ru-RU') : '—'}
                  {sensor.unit ? ` ${sensor.unit}` : ''}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </li>
  );
}


export default function FieldTelematicsPanel({ fieldId }) {
  const [state, setState] = useState({ status: 'loading', data: null, message: '' });
  const [reload, setReload] = useState(0);
  const generationRef = useRef(0);

  useEffect(() => {
    if (!fieldId) {
      setState({ status: 'unsupported', data: null, message: '' });
      return undefined;
    }

    const controller = new AbortController();
    const generation = ++generationRef.current;
    setState({ status: 'loading', data: null, message: '' });

    getFieldTelematics(fieldId, { hours: 24, limit: 50 }, controller.signal)
      .then((data) => {
        if (controller.signal.aborted || generation !== generationRef.current) return;
        const status = ['available', 'stale', 'unsupported', 'unavailable'].includes(data?.status)
          ? data.status
          : 'unavailable';
        setState({ status, data, message: '' });
      })
      .catch((error) => {
        if (
          controller.signal.aborted
          || generation !== generationRef.current
          || requestWasCancelled(error)
        ) return;
        setState({
          status: 'unavailable',
          data: null,
          message: error?.response?.status === 404
            ? 'Поле не найдено или больше недоступно.'
            : 'Не удалось получить подтверждённые данные телематики.',
        });
      });

    return () => {
      generationRef.current += 1;
      controller.abort();
    };
  }, [fieldId, reload]);

  if (state.status === 'loading') {
    return (
      <div aria-busy="true" aria-label="Загрузка телематики" className="space-y-3">
        <div className="card h-24 animate-pulse bg-agro-surface2" />
        <div className="card h-40 animate-pulse bg-agro-surface2" />
      </div>
    );
  }

  if (state.status === 'unsupported' || state.status === 'unavailable') {
    const copy = STATUS_COPY[state.status];
    return (
      <section className="card text-center" aria-live="polite">
        <h3 className="font-semibold text-agro-text">{copy.title}</h3>
        <p className="mt-2 text-sm text-agro-muted">
          {state.message || copy.description}
        </p>
        <p className="mt-2 text-xs text-agro-muted">
          Источник: {state.data?.provider || 'Wialon'} · только чтение
        </p>
        {state.status === 'unavailable' && (
          <button
            className="btn-secondary mt-4 min-h-11"
            onClick={() => setReload((value) => value + 1)}
            type="button"
          >
            Повторить
          </button>
        )}
      </section>
    );
  }

  const units = Array.isArray(state.data?.units) ? state.data.units : [];
  const requestedRange = state.data?.requested_range || {};
  return (
    <section className="space-y-3" aria-labelledby="field-telematics-title">
      <div className="card">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 id="field-telematics-title" className="font-semibold text-agro-text">
              Контекст техники
            </h3>
            <p className="mt-1 text-xs text-agro-muted">
              Источник: {state.data?.provider || 'Wialon'} · только чтение
            </p>
          </div>
          <span
            className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
              state.status === 'stale'
                ? 'border-amber-300 bg-amber-50 text-amber-900'
                : 'border-emerald-300 bg-emerald-50 text-emerald-800'
            }`}
          >
            {state.status === 'stale' ? 'Все данные устарели' : 'Данные доступны'}
          </span>
        </div>
        <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs text-agro-muted">Период</dt>
            <dd className="text-agro-text">
              {formatDateTime(requestedRange.started_at)} — {formatDateTime(requestedRange.ended_at)}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-agro-muted">Происхождение сопоставления</dt>
            <dd className="text-agro-text">{state.data?.mapping_provenance || '—'}</dd>
          </div>
        </dl>
        <p className="mt-3 text-xs text-agro-muted">
          Положение техники является контекстом осмотра и не подтверждает выполненную операцию само по себе.
        </p>
      </div>

      {units.length > 0 ? (
        <ul className="space-y-3" aria-label="Сопоставленная техника">
          {units.map((unit) => <UnitCard key={unit.unit_id} unit={unit} />)}
        </ul>
      ) : (
        <div className="card text-center text-sm text-agro-muted" aria-live="polite">
          За выбранный период подтверждённых позиций техники нет.
        </div>
      )}
    </section>
  );
}
