import { useEffect, useMemo, useRef, useState } from 'react';

import { getLatestProductivityZones } from '../../api/productivityZones';
import {
  createVariableRateRecommendation,
  decideVariableRateRecommendation,
  exportVariableRateRecommendation,
  listVariableRateRecommendations,
} from '../../api/variableRateRecommendations';
import { useAuth } from '../../context/AuthContext';
import {
  createIdempotencyKey,
  normalizeRole,
  todayTashkentDate,
} from '../Inspections/inspectionPresentation';


const KIND_UNITS = {
  fertilizer: 'kg_ha',
  seed: 'seeds_ha',
  pesticide: 'l_ha',
  irrigation: 'mm',
};
const KIND_LABELS = {
  fertilizer: 'Удобрение',
  seed: 'Посев',
  pesticide: 'Средство защиты',
  irrigation: 'Орошение',
};
const STATUS_LABELS = {
  draft: 'Черновик',
  approved: 'Утверждено',
  rejected: 'Отклонено',
  superseded: 'Заменено новой версией',
};


function cancelled(error) {
  return error?.name === 'AbortError' || error?.code === 'ERR_CANCELED';
}


function initialForm() {
  return {
    cropCode: 'cotton',
    seasonYear: Number(todayTashkentDate().slice(0, 4)),
    kind: 'fertilizer',
    minimumRate: '80',
    maximumRate: '160',
    lowRate: '90',
    mediumRate: '120',
    highRate: '150',
    equipmentName: '',
    equipmentMinimum: '50',
    equipmentMaximum: '200',
    compatibilityNote: '',
    notes: '',
    safetyAcknowledged: false,
  };
}


export default function VariableRateRecommendationPanel({ fieldId }) {
  const { user } = useAuth();
  const role = normalizeRole(user?.role);
  const canCreate = ['admin', 'manager', 'agronomist'].includes(role);
  const canApprove = ['admin', 'manager'].includes(role);
  const [items, setItems] = useState([]);
  const [sourceRun, setSourceRun] = useState(null);
  const [state, setState] = useState('loading');
  const [message, setMessage] = useState('');
  const [form, setForm] = useState(initialForm);
  const [decisionNotes, setDecisionNotes] = useState({});
  const [pending, setPending] = useState('');
  const [reload, setReload] = useState(0);
  const loadControllerRef = useRef(null);
  const actionControllerRef = useRef(null);
  const generationRef = useRef(0);
  const keyRef = useRef(createIdempotencyKey());

  useEffect(() => {
    if (!fieldId) return undefined;
    loadControllerRef.current?.abort();
    const controller = new AbortController();
    loadControllerRef.current = controller;
    const generation = ++generationRef.current;
    setState('loading');
    setMessage('');
    Promise.all([
      getLatestProductivityZones(fieldId, controller.signal),
      listVariableRateRecommendations(fieldId, controller.signal),
    ])
      .then(([source, list]) => {
        if (controller.signal.aborted || generation !== generationRef.current) return;
        setSourceRun(source?.latest_run?.result_status === 'ready' ? source.latest_run : null);
        setItems(Array.isArray(list?.items) ? list.items : []);
        setState('ready');
      })
      .catch((error) => {
        if (controller.signal.aborted || generation !== generationRef.current || cancelled(error)) return;
        setState('error');
        setMessage('Не удалось загрузить черновики рекомендаций.');
      });
    return () => controller.abort();
  }, [fieldId, reload]);

  useEffect(() => () => {
    generationRef.current += 1;
    loadControllerRef.current?.abort();
    actionControllerRef.current?.abort();
    keyRef.current = null;
  }, []);

  const payload = useMemo(() => {
    const unit = KIND_UNITS[form.kind];
    return {
      field_id: fieldId,
      productivity_run_id: sourceRun?.id,
      crop_code: form.cropCode.trim().toLowerCase(),
      season_year: Number(form.seasonYear),
      recommendation_kind: form.kind,
      rate_unit: unit,
      minimum_rate: Number(form.minimumRate),
      maximum_rate: Number(form.maximumRate),
      zone_rates: {
        low: Number(form.lowRate),
        medium: Number(form.mediumRate),
        high: Number(form.highRate),
      },
      equipment_capability: {
        equipment_name: form.equipmentName.trim(),
        supported_unit: unit,
        minimum_controllable_rate: Number(form.equipmentMinimum),
        maximum_controllable_rate: Number(form.equipmentMaximum),
        compatibility_note: form.compatibilityNote.trim(),
      },
      notes: form.notes.trim() || null,
      safety_acknowledged: form.safetyAcknowledged,
    };
  }, [fieldId, form, sourceRun]);

  const valid = Boolean(
    sourceRun
    && /^[a-z0-9][a-z0-9_-]{1,63}$/.test(payload.crop_code)
    && payload.equipment_capability.equipment_name.length >= 2
    && payload.equipment_capability.compatibility_note.length >= 3
    && payload.safety_acknowledged
    && Number.isFinite(payload.minimum_rate)
    && Number.isFinite(payload.maximum_rate)
    && payload.maximum_rate >= payload.minimum_rate
    && Object.values(payload.zone_rates).every(
      (value) => Number.isFinite(value)
        && value >= payload.minimum_rate
        && value <= payload.maximum_rate
        && value >= payload.equipment_capability.minimum_controllable_rate
        && value <= payload.equipment_capability.maximum_controllable_rate,
    )
  );

  function update(name, value) {
    setForm((current) => ({ ...current, [name]: value }));
  }

  async function submit(event) {
    event.preventDefault();
    if (!valid || pending) return;
    actionControllerRef.current?.abort();
    const controller = new AbortController();
    actionControllerRef.current = controller;
    const generation = generationRef.current;
    setPending('create');
    setMessage('');
    try {
      const result = await createVariableRateRecommendation(
        payload, keyRef.current, controller.signal,
      );
      if (controller.signal.aborted || generation !== generationRef.current) return;
      keyRef.current = createIdempotencyKey();
      setMessage(result?.created ? 'Черновик создан и ожидает проверки.' : 'Этот запрос уже был обработан.');
      setReload((value) => value + 1);
    } catch (error) {
      if (controller.signal.aborted || generation !== generationRef.current || cancelled(error)) return;
      setMessage(error?.response?.status === 409
        ? 'Версия или ключ запроса конфликтует с текущим состоянием.'
        : 'Черновик не подтверждён. Проверьте границы и оборудование.');
    } finally {
      if (!controller.signal.aborted && generation === generationRef.current) setPending('');
    }
  }

  async function decide(item, decision) {
    const note = (decisionNotes[item.id] || '').trim();
    if (note.length < 3 || pending) return;
    actionControllerRef.current?.abort();
    const controller = new AbortController();
    actionControllerRef.current = controller;
    const generation = generationRef.current;
    setPending(`${decision}-${item.id}`);
    setMessage('');
    try {
      await decideVariableRateRecommendation(
        item.id,
        decision,
        { expected_version: item.version, confirm: true, note },
        controller.signal,
      );
      if (controller.signal.aborted || generation !== generationRef.current) return;
      setMessage(decision === 'approve' ? 'Рекомендация утверждена человеком.' : 'Рекомендация отклонена.');
      setReload((value) => value + 1);
    } catch (error) {
      if (controller.signal.aborted || generation !== generationRef.current || cancelled(error)) return;
      setMessage(error?.response?.status === 409
        ? 'Состояние изменилось. Обновите список перед решением.'
        : 'Решение не подтверждено.');
    } finally {
      if (!controller.signal.aborted && generation === generationRef.current) setPending('');
    }
  }

  async function download(item) {
    actionControllerRef.current?.abort();
    const controller = new AbortController();
    actionControllerRef.current = controller;
    const generation = generationRef.current;
    setPending(`export-${item.id}`);
    try {
      const data = await exportVariableRateRecommendation(item.id, controller.signal);
      if (controller.signal.aborted || generation !== generationRef.current) return;
      const url = URL.createObjectURL(new Blob(
        [`${JSON.stringify(data, null, 2)}\n`],
        { type: 'application/geo+json' },
      ));
      try {
        const link = document.createElement('a');
        link.href = url;
        link.download = `variable-rate-${item.id}-v${item.version}.geojson`;
        link.click();
      } finally {
        URL.revokeObjectURL(url);
      }
    } catch (error) {
      if (!controller.signal.aborted && generation === generationRef.current && !cancelled(error)) {
        setMessage('Экспорт не сформирован.');
      }
    } finally {
      if (!controller.signal.aborted && generation === generationRef.current) setPending('');
    }
  }

  return (
    <section className="card p-4" aria-labelledby="variable-rate-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-indigo-700">
            Решение с обязательной проверкой
          </p>
          <h3 id="variable-rate-title" className="mt-1 text-lg font-bold text-agro-text">
            Черновики переменной нормы
          </h3>
          <p className="mt-1 text-sm text-agro-muted">
            Все нормы вводит человек. Спутниковые данные и зоны сами не определяют дозировку.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setReload((value) => value + 1)}
          className="btn-secondary min-h-11 px-4 py-2"
        >
          Обновить
        </button>
      </div>

      {state === 'loading' && <p role="status" className="mt-4 text-sm text-agro-muted">Загрузка…</p>}
      {state === 'error' && <p role="alert" className="mt-4 text-sm text-red-700">{message}</p>}

      {state === 'ready' && canCreate && (
        <form onSubmit={submit} className="mt-4 space-y-3 rounded-xl border border-agro-border p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <SelectField label="Вид рекомендации" value={form.kind} onChange={(value) => update('kind', value)}>
              {Object.entries(KIND_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </SelectField>
            <TextField label="Культура" value={form.cropCode} onChange={(value) => update('cropCode', value)} />
            <NumberField label="Сезон" value={form.seasonYear} onChange={(value) => update('seasonYear', value)} />
            <TextField label="Оборудование" value={form.equipmentName} onChange={(value) => update('equipmentName', value)} />
            <NumberField label="Минимум" value={form.minimumRate} onChange={(value) => update('minimumRate', value)} />
            <NumberField label="Максимум" value={form.maximumRate} onChange={(value) => update('maximumRate', value)} />
            <NumberField label="Низкая зона" value={form.lowRate} onChange={(value) => update('lowRate', value)} />
            <NumberField label="Средняя зона" value={form.mediumRate} onChange={(value) => update('mediumRate', value)} />
            <NumberField label="Высокая зона" value={form.highRate} onChange={(value) => update('highRate', value)} />
            <NumberField label="Минимум оборудования" value={form.equipmentMinimum} onChange={(value) => update('equipmentMinimum', value)} />
            <NumberField label="Максимум оборудования" value={form.equipmentMaximum} onChange={(value) => update('equipmentMaximum', value)} />
            <TextField label="Совместимость и калибровка" value={form.compatibilityNote} onChange={(value) => update('compatibilityNote', value)} />
          </div>
          <label className="flex min-h-11 items-start gap-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-950">
            <input
              type="checkbox"
              checked={form.safetyAcknowledged}
              onChange={(event) => update('safetyAcknowledged', event.target.checked)}
              className="mt-1 h-5 w-5"
            />
            Я подтверждаю, что нормы введены человеком и требуют проверки агрономом перед применением.
          </label>
          {!sourceRun && (
            <p className="text-sm text-amber-800">Нужен готовый расчёт зон продуктивности.</p>
          )}
          <button disabled={!valid || Boolean(pending)} className="btn-primary min-h-11 px-4 py-2 disabled:opacity-50">
            {pending === 'create' ? 'Создание…' : 'Создать черновик'}
          </button>
        </form>
      )}

      {state === 'ready' && !canCreate && (
        <p className="mt-4 rounded-lg bg-agro-surface2 p-3 text-sm text-agro-muted">
          Только просмотр. Создание и утверждение недоступны.
        </p>
      )}

      <p aria-live="polite" className="mt-3 min-h-5 text-sm text-agro-muted">{message}</p>

      <div className="mt-2 space-y-3">
        {items.length === 0 && state === 'ready' && (
          <p className="text-sm text-agro-muted">Черновиков пока нет.</p>
        )}
        {items.map((item) => (
          <article key={item.id} className="rounded-xl border border-agro-border p-4">
            <div className="flex flex-wrap justify-between gap-2">
              <div>
                <p className="font-semibold text-agro-text">
                  {KIND_LABELS[item.recommendation_kind]} · v{item.version}
                </p>
                <p className="text-xs text-agro-muted">
                  {STATUS_LABELS[item.status]} · {item.crop_code} · {item.season_year}
                </p>
              </div>
              <button
                type="button"
                onClick={() => download(item)}
                disabled={Boolean(pending)}
                className="btn-secondary min-h-11 px-3 py-2 text-sm"
              >
                GeoJSON
              </button>
            </div>
            <div className="mt-3 grid grid-cols-3 gap-2 text-sm">
              {['low', 'medium', 'high'].map((zone) => (
                <div key={zone} className="rounded-lg bg-agro-surface2 p-2">
                  <p className="text-xs text-agro-muted">{zone}</p>
                  <p className="font-semibold">{item.zone_rates[zone]} {item.rate_unit}</p>
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs text-amber-800">
              Требует проверки агрономом; не является автономным предписанием.
            </p>
            {canApprove && item.status === 'draft' && (
              <div className="mt-3 space-y-2">
                <label className="block text-sm">
                  Комментарий решения
                  <textarea
                    value={decisionNotes[item.id] || ''}
                    onChange={(event) => setDecisionNotes((current) => ({
                      ...current,
                      [item.id]: event.target.value,
                    }))}
                    rows={2}
                    className="input mt-1 w-full"
                  />
                </label>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => decide(item, 'approve')}
                    disabled={(decisionNotes[item.id] || '').trim().length < 3 || Boolean(pending)}
                    className="btn-primary min-h-11 px-4 py-2 disabled:opacity-50"
                  >
                    Утвердить
                  </button>
                  <button
                    type="button"
                    onClick={() => decide(item, 'reject')}
                    disabled={(decisionNotes[item.id] || '').trim().length < 3 || Boolean(pending)}
                    className="btn-secondary min-h-11 px-4 py-2 disabled:opacity-50"
                  >
                    Отклонить
                  </button>
                </div>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}


function TextField({ label, value, onChange }) {
  return (
    <label className="block text-sm">
      {label}
      <input value={value} onChange={(event) => onChange(event.target.value)} className="input mt-1 min-h-11 w-full" />
    </label>
  );
}


function NumberField({ label, value, onChange }) {
  return (
    <label className="block text-sm">
      {label}
      <input type="number" step="any" value={value} onChange={(event) => onChange(event.target.value)} className="input mt-1 min-h-11 w-full" />
    </label>
  );
}


function SelectField({ label, value, onChange, children }) {
  return (
    <label className="block text-sm">
      {label}
      <select value={value} onChange={(event) => onChange(event.target.value)} className="input mt-1 min-h-11 w-full">
        {children}
      </select>
    </label>
  );
}
