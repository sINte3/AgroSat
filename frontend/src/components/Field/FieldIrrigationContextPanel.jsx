import { useEffect, useMemo, useRef, useState } from 'react';

import { createFieldInspection } from '../../api/fieldInspections';
import {
  createIrrigationEvent,
  getFieldIrrigationContext,
} from '../../api/irrigationContext';
import { useAuth } from '../../context/AuthContext';
import {
  createIdempotencyKey,
  normalizeRole,
  SOURCE_LABELS,
  STATUS_LABELS,
  todayTashkentDate,
} from '../Inspections/inspectionPresentation';


const EVENT_LABELS = {
  irrigation_applied: 'Полив выполнен',
  irrigation_interrupted: 'Полив прерван',
  equipment_issue: 'Проблема оборудования',
  field_observation: 'Наблюдение в поле',
};
const METHOD_LABELS = {
  canal: 'Канал',
  drip: 'Капельный',
  sprinkler: 'Дождевание',
  furrow: 'По бороздам',
  manual: 'Ручной',
  unknown: 'Не указан',
};
const REASON_LABELS = {
  water_stress_suspicion: 'Подозрение на водный стресс',
  weather_water_deficit: 'Проверить дефицит влаги по погодному контексту',
  irrigation_interruption: 'Проверить прерывание полива',
  irrigation_delivery_check: 'Проверить доставку воды',
  irrigation_equipment_check: 'Проверить оборудование орошения',
};


function cancelled(error) {
  return error?.name === 'AbortError' || error?.code === 'ERR_CANCELED';
}


function tashkentDateTimeInput(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tashkent',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}`;
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


function numeric(value, digits = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : '—';
}


function providerLabel(value) {
  if (value === 'open_meteo') return 'Open-Meteo';
  return value || 'Open-Meteo';
}


function WeatherContext({ weather }) {
  if (weather?.status !== 'available') {
    const reason = weather?.reason === 'missing_coordinates'
      ? 'Для поля не заданы координаты.'
      : 'Провайдер погоды временно не вернул данные.';
    return (
      <section className="card" aria-labelledby="weather-context-title">
        <h3 id="weather-context-title" className="font-semibold text-agro-text">
          Погодный контекст недоступен
        </h3>
        <p className="mt-2 text-sm text-agro-muted">{reason}</p>
        <p className="mt-2 text-xs text-agro-muted">Источник: {providerLabel(weather?.provider)}</p>
      </section>
    );
  }

  const current = weather.current || {};
  const forecast = Array.isArray(weather.forecast) ? weather.forecast.slice(0, 5) : [];
  const provenance = weather.provenance || {};
  return (
    <section className="card space-y-4" aria-labelledby="weather-context-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="weather-context-title" className="font-semibold text-agro-text">
            Погода: наблюдение и прогноз
          </h3>
          <p className="mt-1 text-xs text-agro-muted">
            Источник: {providerLabel(weather.provider || provenance.provider)}
          </p>
        </div>
        <div className="text-right">
          <p className="text-3xl font-semibold text-agro-text">{numeric(current.temperature)}°C</p>
          <p className="text-xs text-agro-muted">
            Наблюдение: {formatDateTime(provenance.provider_observed_at)}
          </p>
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs text-agro-muted">Влажность</dt>
          <dd className="font-medium text-agro-text">{numeric(current.humidity)}%</dd>
        </div>
        <div>
          <dt className="text-xs text-agro-muted">Ветер</dt>
          <dd className="font-medium text-agro-text">{numeric(current.wind_speed)} км/ч</dd>
        </div>
        <div>
          <dt className="text-xs text-agro-muted">Осадки</dt>
          <dd className="font-medium text-agro-text">{numeric(current.precipitation, 1)} мм</dd>
        </div>
        <div>
          <dt className="text-xs text-agro-muted">Часовой пояс</dt>
          <dd className="font-medium text-agro-text">{provenance.timezone || 'Asia/Tashkent'}</dd>
        </div>
      </dl>

      {forecast.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-agro-muted">
            Прогноз на 5 дней
          </h4>
          <ul className="divide-y divide-agro-surface2 rounded-lg border border-agro-surface2">
            {forecast.map((day) => (
              <li className="grid grid-cols-3 gap-2 px-3 py-2 text-sm" key={day.date}>
                <span className="text-agro-text">
                  {new Date(`${day.date}T00:00:00`).toLocaleDateString('ru-RU', {
                    day: 'numeric',
                    month: 'short',
                  })}
                </span>
                <span className="text-center text-agro-muted">
                  {numeric(day.temp_min)}…{numeric(day.temp_max)}°C
                </span>
                <span className="text-right text-agro-muted">{numeric(day.precipitation, 1)} мм</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}


function EventList({ events }) {
  return (
    <section className="card" aria-labelledby="irrigation-event-list-title">
      <h3 id="irrigation-event-list-title" className="font-semibold text-agro-text">
        Подтверждённые события
      </h3>
      {events.length === 0 ? (
        <p className="mt-3 text-sm text-agro-muted">События орошения ещё не зафиксированы.</p>
      ) : (
        <ol className="mt-3 space-y-3">
          {events.map((event) => (
            <li className="border-l-2 border-agro-accent pl-3" key={event.id}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="text-sm font-medium text-agro-text">
                  {EVENT_LABELS[event.event_type] || event.event_type}
                </p>
                <time className="text-xs text-agro-muted" dateTime={event.occurred_at}>
                  {formatDateTime(event.occurred_at)}
                </time>
              </div>
              <p className="mt-1 text-xs text-agro-muted">
                Метод: {METHOD_LABELS[event.method_code] || event.method_code || '—'}
                {event.water_amount_mm != null ? ` · ${numeric(event.water_amount_mm, 1)} мм` : ''}
              </p>
              {event.note && <p className="mt-1 whitespace-pre-wrap text-sm text-agro-text">{event.note}</p>}
              <p className="mt-1 text-xs text-agro-muted">
                Зафиксировал: {event.recorded_by?.display_name || 'Пользователь'}
                {event.inspection_id ? ` · Осмотр #${event.inspection_id}` : ''}
              </p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}


function EventForm({ fieldId, activeInspection, onCreated }) {
  const [eventType, setEventType] = useState('field_observation');
  const [methodCode, setMethodCode] = useState('unknown');
  const [occurredAt, setOccurredAt] = useState(tashkentDateTimeInput);
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const keyRef = useRef(createIdempotencyKey());
  const frozenRef = useRef('');
  const controllerRef = useRef(null);
  const mountedRef = useRef(true);

  useEffect(() => () => {
    mountedRef.current = false;
    controllerRef.current?.abort();
  }, []);

  const payload = useMemo(() => ({
    inspection_id: activeInspection?.id || null,
    event_type: eventType,
    occurred_at: occurredAt ? `${occurredAt}:00+05:00` : '',
    method_code: methodCode,
    water_amount_mm: eventType === 'irrigation_applied' && amount ? Number(amount) : null,
    evidence_source: 'human_reported',
    note: note.trim() || null,
  }), [activeInspection?.id, amount, eventType, methodCode, note, occurredAt]);
  const serialized = JSON.stringify(payload);
  const valid = occurredAt
    && (!payload.note || payload.note.length >= 3)
    && (eventType !== 'irrigation_applied' || !amount || (Number(amount) > 0 && Number(amount) <= 1000));

  async function submit(event) {
    event.preventDefault();
    if (!valid || pending) return;
    if (frozenRef.current && frozenRef.current !== serialized) {
      keyRef.current = createIdempotencyKey();
    }
    frozenRef.current = serialized;
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    setPending(true);
    setMessage('');
    try {
      const result = await createIrrigationEvent(fieldId, payload, keyRef.current, controller.signal);
      if (!mountedRef.current || controller.signal.aborted) return;
      keyRef.current = createIdempotencyKey();
      frozenRef.current = '';
      setNote('');
      setAmount('');
      setMessage(result?.created ? 'Событие зафиксировано.' : 'Событие уже было зафиксировано.');
      onCreated();
    } catch (error) {
      if (!mountedRef.current || controller.signal.aborted || cancelled(error)) return;
      setMessage(error?.response?.status === 409
        ? 'Ключ запроса уже использован с другими данными. Измените форму и повторите.'
        : 'Не удалось подтвердить запись события. Повторите тот же запрос.');
    } finally {
      if (mountedRef.current && !controller.signal.aborted) setPending(false);
    }
  }

  return (
    <form className="card space-y-3" onSubmit={submit}>
      <div>
        <h3 className="font-semibold text-agro-text">Зафиксировать событие</h3>
        <p className="mt-1 text-xs text-agro-muted">Источник доказательства: сообщение пользователя</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm text-agro-text">
          Тип события
          <select className="input mt-1 min-h-11 w-full" value={eventType} onChange={(event) => setEventType(event.target.value)}>
            {Object.entries(EVENT_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="text-sm text-agro-text">
          Когда произошло
          <input className="input mt-1 min-h-11 w-full" max={tashkentDateTimeInput()} type="datetime-local" value={occurredAt} onChange={(event) => setOccurredAt(event.target.value)} />
        </label>
        <label className="text-sm text-agro-text">
          Метод
          <select className="input mt-1 min-h-11 w-full" value={methodCode} onChange={(event) => setMethodCode(event.target.value)}>
            {Object.entries(METHOD_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        {eventType === 'irrigation_applied' && (
          <label className="text-sm text-agro-text">
            Объём, мм (если измерен)
            <input className="input mt-1 min-h-11 w-full" max="1000" min="0.01" step="0.01" type="number" value={amount} onChange={(event) => setAmount(event.target.value)} />
          </label>
        )}
      </div>
      <label className="block text-sm text-agro-text">
        Наблюдение
        <textarea className="input mt-1 min-h-24 w-full" maxLength="4000" minLength="3" value={note} onChange={(event) => setNote(event.target.value)} />
      </label>
      {message && <p aria-live="polite" className="text-sm text-agro-muted">{message}</p>}
      <button className="btn-primary min-h-11 w-full sm:w-auto" disabled={!valid || pending} type="submit">
        {pending ? 'Сохранение…' : 'Зафиксировать событие'}
      </button>
    </form>
  );
}


function InspectionContext({ fieldId, activeInspection, onCreated }) {
  const [reason, setReason] = useState('water_stress_suspicion');
  const [priority, setPriority] = useState('medium');
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const controllerRef = useRef(null);
  const mountedRef = useRef(true);
  const keyRef = useRef(createIdempotencyKey());

  useEffect(() => () => {
    mountedRef.current = false;
    controllerRef.current?.abort();
  }, []);

  async function createInspection() {
    if (pending) return;
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    setPending(true);
    setMessage('');
    try {
      const result = await createFieldInspection({
        field_id: fieldId,
        source: 'irrigation_context',
        source_priority: priority,
        source_attention_score: null,
        source_observation_date: todayTashkentDate(),
        source_reason_codes: [reason],
        title: `Осмотр: ${REASON_LABELS[reason]}`,
        instructions: 'Проверить состояние в поле и зафиксировать фактические доказательства.',
        due_date: null,
      }, keyRef.current, controller.signal);
      if (!mountedRef.current || controller.signal.aborted) return;
      keyRef.current = createIdempotencyKey();
      setMessage(result?.created ? 'Осмотр создан.' : 'Этот осмотр уже был создан.');
      onCreated();
    } catch (error) {
      if (!mountedRef.current || controller.signal.aborted || cancelled(error)) return;
      setMessage(error?.response?.status === 409
        ? 'Для поля уже есть активный осмотр.'
        : 'Не удалось подтвердить создание осмотра. Повторите запрос.');
    } finally {
      if (mountedRef.current && !controller.signal.aborted) setPending(false);
    }
  }

  if (activeInspection) {
    return (
      <section className="card" aria-labelledby="irrigation-inspection-title">
        <h3 id="irrigation-inspection-title" className="font-semibold text-agro-text">
          Активный осмотр #{activeInspection.id}
        </h3>
        <p className="mt-2 text-sm text-agro-muted">
          Статус: {STATUS_LABELS[activeInspection.status] || activeInspection.status}
          {' · '}
          источник: {SOURCE_LABELS[activeInspection.source] || activeInspection.source}
        </p>
        <a className="btn-secondary mt-4 inline-flex min-h-11 items-center" href="/inspections">
          Открыть осмотры
        </a>
      </section>
    );
  }

  return (
    <section className="card space-y-3" aria-labelledby="irrigation-inspection-title">
      <div>
        <h3 id="irrigation-inspection-title" className="font-semibold text-agro-text">Назначить проверку поля</h3>
        <p className="mt-1 text-xs text-agro-muted">
          Контекст создаёт задачу на проверку, но не подтверждает причину.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm text-agro-text">
          Причина проверки
          <select className="input mt-1 min-h-11 w-full" value={reason} onChange={(event) => setReason(event.target.value)}>
            {Object.entries(REASON_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="text-sm text-agro-text">
          Приоритет
          <select className="input mt-1 min-h-11 w-full" value={priority} onChange={(event) => setPriority(event.target.value)}>
            <option value="low">Низкий</option>
            <option value="medium">Средний</option>
            <option value="high">Высокий</option>
            <option value="critical">Критический</option>
          </select>
        </label>
      </div>
      {message && <p aria-live="polite" className="text-sm text-agro-muted">{message}</p>}
      <button className="btn-primary min-h-11 w-full sm:w-auto" disabled={pending} onClick={createInspection} type="button">
        {pending ? 'Создание…' : 'Создать осмотр'}
      </button>
    </section>
  );
}


export default function FieldIrrigationContextPanel({ fieldId }) {
  const { user } = useAuth();
  const role = normalizeRole(user?.role);
  const readOnly = role === 'viewer';
  const [state, setState] = useState({ status: 'loading', data: null, message: '' });
  const [reload, setReload] = useState(0);
  const generationRef = useRef(0);

  useEffect(() => {
    if (!fieldId) {
      setState({ status: 'error', data: null, message: 'Поле не выбрано.' });
      return undefined;
    }
    const controller = new AbortController();
    const generation = ++generationRef.current;
    setState({ status: 'loading', data: null, message: '' });
    getFieldIrrigationContext(fieldId, controller.signal)
      .then((data) => {
        if (controller.signal.aborted || generation !== generationRef.current) return;
        setState({ status: 'ready', data, message: '' });
      })
      .catch((error) => {
        if (controller.signal.aborted || generation !== generationRef.current || cancelled(error)) return;
        setState({
          status: 'error',
          data: null,
          message: error?.response?.status === 404
            ? 'Поле не найдено или больше недоступно.'
            : 'Не удалось загрузить погодный и ирригационный контекст.',
        });
      });
    return () => {
      generationRef.current += 1;
      controller.abort();
    };
  }, [fieldId, reload]);

  if (state.status === 'loading') {
    return (
      <div aria-busy="true" aria-label="Загрузка погодного и ирригационного контекста" className="space-y-3">
        <div className="card h-36 animate-pulse bg-agro-surface2" />
        <div className="card h-24 animate-pulse bg-agro-surface2" />
      </div>
    );
  }

  if (state.status === 'error') {
    return (
      <section className="card text-center" aria-live="polite">
        <h3 className="font-semibold text-agro-text">Контекст недоступен</h3>
        <p className="mt-2 text-sm text-agro-muted">{state.message}</p>
        <button className="btn-secondary mt-4 min-h-11" onClick={() => setReload((value) => value + 1)} type="button">
          Повторить
        </button>
      </section>
    );
  }

  const data = state.data || {};
  const events = Array.isArray(data.events) ? data.events : [];
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-3 text-sm text-amber-950" role="note">
        Погода, события орошения и спутниковые индексы не подтверждают агрономическую
        причину без доказательств осмотра поля.
      </div>
      <WeatherContext weather={data.weather} />
      <EventList events={events} />
      {readOnly ? (
        <section className="card" aria-live="polite">
          <h3 className="font-semibold text-agro-text">Только просмотр</h3>
          <p className="mt-2 text-sm text-agro-muted">
            Viewer может видеть контекст, но не может фиксировать события или создавать осмотры.
          </p>
        </section>
      ) : (
        <>
          <EventForm key={`event-${fieldId}`} fieldId={fieldId} activeInspection={data.active_inspection} onCreated={() => setReload((value) => value + 1)} />
          <InspectionContext key={`inspection-${fieldId}`} fieldId={fieldId} activeInspection={data.active_inspection} onCreated={() => setReload((value) => value + 1)} />
        </>
      )}
    </div>
  );
}
