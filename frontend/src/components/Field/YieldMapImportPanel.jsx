import { useEffect, useMemo, useRef, useState } from 'react';

import {
  acceptYieldMapImport,
  listYieldMapImports,
  previewYieldMapImport,
} from '../../api/yieldMapImports';
import { useAuth } from '../../context/AuthContext';
import {
  createIdempotencyKey,
  normalizeRole,
  todayTashkentDate,
} from '../Inspections/inspectionPresentation';


const MAX_FILE_BYTES = 5 * 1024 * 1024;
const MAX_ROWS = 5000;
const REQUIRED_COLUMNS = ['longitude', 'latitude', 'yield_value', 'observed_at'];
const REJECTION_LABELS = {
  invalid_coordinate: 'Координаты вне EPSG:4326',
  yield_out_of_range: 'Урожайность вне диапазона',
  invalid_observed_at: 'Время без часового пояса или неверный формат',
  speed_out_of_range: 'Скорость вне диапазона',
  moisture_out_of_range: 'Влажность вне диапазона',
  duplicate_machine_point_id: 'Дублирующий идентификатор точки',
  yield_statistical_outlier: 'Статистический выброс',
  outside_field: 'Точка вне границы поля',
};


function cancelled(error) {
  return error?.name === 'AbortError' || error?.code === 'ERR_CANCELED';
}


function csvLine(value) {
  const cells = [];
  let current = '';
  let quoted = false;
  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];
    if (character === '"') {
      if (quoted && value[index + 1] === '"') {
        current += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (character === ',' && !quoted) {
      cells.push(current.trim());
      current = '';
    } else {
      current += character;
    }
  }
  if (quoted) throw new Error('Незакрытая кавычка в CSV.');
  cells.push(current.trim());
  return cells;
}


export function parseYieldPointCsv(text) {
  const lines = String(text || '').replace(/^\uFEFF/, '').split(/\r?\n/).filter((line) => line.trim());
  if (lines.length < 2) throw new Error('CSV должен содержать заголовок и хотя бы одну строку.');
  const headers = csvLine(lines[0]).map((value) => value.trim().toLowerCase());
  const missing = REQUIRED_COLUMNS.filter((column) => !headers.includes(column));
  if (missing.length) throw new Error(`Отсутствуют колонки: ${missing.join(', ')}.`);
  if (lines.length - 1 > MAX_ROWS) throw new Error(`Поддерживается не более ${MAX_ROWS} строк.`);
  return lines.slice(1).map((line, index) => {
    const values = csvLine(line);
    const record = Object.fromEntries(headers.map((header, position) => [header, values[position] ?? '']));
    const numeric = (name) => (record[name] === '' ? null : Number(record[name]));
    const row = {
      longitude: numeric('longitude'),
      latitude: numeric('latitude'),
      yield_value: numeric('yield_value'),
      observed_at: record.observed_at,
      machine_point_id: record.machine_point_id || null,
      speed_kph: numeric('speed_kph'),
      moisture_pct: numeric('moisture_pct'),
    };
    if (![row.longitude, row.latitude, row.yield_value].every(Number.isFinite)) {
      throw new Error(`Строка ${index + 2}: числовые значения некорректны.`);
    }
    return row;
  });
}


async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('');
}


function formatDateTime(value) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? '—'
    : parsed.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
}


function ImportHistory({ items, loading }) {
  return (
    <section className="card" aria-labelledby="yield-import-history-title">
      <h3 id="yield-import-history-title" className="font-semibold text-agro-text">История импортов</h3>
      {loading ? (
        <p aria-busy="true" className="mt-3 text-sm text-agro-muted">Загрузка…</p>
      ) : items.length === 0 ? (
        <p className="mt-3 text-sm text-agro-muted">Для поля ещё нет принятых карт урожайности.</p>
      ) : (
        <ol className="mt-3 space-y-3">
          {items.map((item) => (
            <li className="rounded-lg border border-agro-surface2 px-3 py-3" key={item.id}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-medium text-agro-text">{item.source_filename}</p>
                  <p className="text-xs text-agro-muted">
                    Сезон {item.season_year} · {item.crop_code} · {item.accepted_rows} точек
                  </p>
                </div>
                <span className="rounded-full border border-emerald-300 bg-emerald-50 px-2.5 py-1 text-xs text-emerald-800">
                  Принято
                </span>
              </div>
              <p className="mt-2 text-xs text-agro-muted">
                Средняя: {Number(item.yield_mean_t_ha).toFixed(2)} т/га · {formatDateTime(item.created_at)}
              </p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}


export default function YieldMapImportPanel({ fieldId }) {
  const { user } = useAuth();
  const role = normalizeRole(user?.role);
  const readOnly = role === 'viewer';
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [seasonYear, setSeasonYear] = useState(Number(todayTashkentDate().slice(0, 4)));
  const [cropCode, setCropCode] = useState('cotton');
  const [sourceProvider, setSourceProvider] = useState('machine_export');
  const [yieldUnit, setYieldUnit] = useState('t_ha');
  const [fileState, setFileState] = useState(null);
  const [preview, setPreview] = useState(null);
  const [pending, setPending] = useState('');
  const [message, setMessage] = useState('');
  const actionGenerationRef = useRef(0);
  const actionControllerRef = useRef(null);
  const historyGenerationRef = useRef(0);
  const historyControllerRef = useRef(null);
  const metadataReadyRef = useRef(false);
  const keyRef = useRef(createIdempotencyKey());

  function cancelActionRequest() {
    actionGenerationRef.current += 1;
    actionControllerRef.current?.abort();
    actionControllerRef.current = null;
  }

  function refreshHistory() {
    historyGenerationRef.current += 1;
    historyControllerRef.current?.abort();
    const controller = new AbortController();
    const generation = historyGenerationRef.current;
    historyControllerRef.current = controller;
    setHistoryLoading(true);
    listYieldMapImports(fieldId, controller.signal)
      .then((result) => {
        if (controller.signal.aborted || generation !== historyGenerationRef.current) return;
        setHistory(Array.isArray(result?.items) ? result.items : []);
      })
      .catch((error) => {
        if (controller.signal.aborted || generation !== historyGenerationRef.current || cancelled(error)) return;
        setMessage('Не удалось загрузить историю карт урожайности.');
      })
      .finally(() => {
        if (!controller.signal.aborted && generation === historyGenerationRef.current) setHistoryLoading(false);
      });
  }

  useEffect(() => {
    if (fieldId) refreshHistory();
    return () => {
      cancelActionRequest();
      historyGenerationRef.current += 1;
      historyControllerRef.current?.abort();
    };
  }, [fieldId]);

  const requestPayload = useMemo(() => {
    if (!fileState) return null;
    return {
      field_id: fieldId,
      season_year: Number(seasonYear),
      crop_code: cropCode.trim().toLowerCase(),
      schema_code: 'yield_point_csv_v1',
      source_filename: fileState.name,
      source_sha256: fileState.sha256,
      source_provider: sourceProvider.trim(),
      machine_id: null,
      machine_model: null,
      yield_unit: yieldUnit,
      rows: fileState.rows,
    };
  }, [cropCode, fieldId, fileState, seasonYear, sourceProvider, yieldUnit]);

  useEffect(() => {
    if (!metadataReadyRef.current) {
      metadataReadyRef.current = true;
      return;
    }
    setPreview(null);
    setMessage('');
    keyRef.current = createIdempotencyKey();
    cancelActionRequest();
  }, [cropCode, seasonYear, sourceProvider, yieldUnit, fileState]);

  async function chooseFile(event) {
    cancelActionRequest();
    const file = event.target.files?.[0];
    setPreview(null);
    setMessage('');
    if (!file) {
      setFileState(null);
      return;
    }
    if (!file.name.toLowerCase().endsWith('.csv')) {
      setFileState(null);
      setMessage('Поддерживается только файл .csv.');
      return;
    }
    if (file.size <= 0 || file.size > MAX_FILE_BYTES) {
      setFileState(null);
      setMessage('Размер CSV должен быть от 1 байта до 5 МБ.');
      return;
    }
    const generation = ++actionGenerationRef.current;
    setPending('file');
    try {
      const bytes = await file.arrayBuffer();
      const rows = parseYieldPointCsv(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
      const sha256 = await sha256Hex(bytes);
      if (generation !== actionGenerationRef.current) return;
      setFileState({ name: file.name.split(/[\\/]/).pop(), size: file.size, sha256, rows });
    } catch (error) {
      if (generation !== actionGenerationRef.current) return;
      setFileState(null);
      setMessage(error?.message || 'Не удалось прочитать CSV.');
    } finally {
      if (generation === actionGenerationRef.current) setPending('');
    }
  }

  async function runPreview() {
    if (!requestPayload || pending) return;
    cancelActionRequest();
    const controller = new AbortController();
    const generation = actionGenerationRef.current;
    actionControllerRef.current = controller;
    setPending('preview');
    setMessage('');
    try {
      const result = await previewYieldMapImport(requestPayload, controller.signal);
      if (controller.signal.aborted || generation !== actionGenerationRef.current) return;
      setPreview(result);
      setMessage(result.rejected_rows
        ? 'Предпросмотр содержит отклонённые строки. Исправьте исходный файл.'
        : 'Предпросмотр прошёл. Проверьте итог перед принятием.');
    } catch (error) {
      if (controller.signal.aborted || generation !== actionGenerationRef.current || cancelled(error)) return;
      setMessage('Не удалось выполнить подтверждённый предпросмотр.');
    } finally {
      if (!controller.signal.aborted && generation === actionGenerationRef.current) setPending('');
    }
  }

  async function acceptPreview() {
    if (!requestPayload || !preview || preview.rejected_rows || pending) return;
    cancelActionRequest();
    const controller = new AbortController();
    const generation = actionGenerationRef.current;
    actionControllerRef.current = controller;
    setPending('accept');
    setMessage('');
    try {
      const result = await acceptYieldMapImport({
        ...requestPayload,
        preview_fingerprint: preview.preview_fingerprint,
        confirm: true,
      }, keyRef.current, controller.signal);
      if (controller.signal.aborted || generation !== actionGenerationRef.current) return;
      keyRef.current = createIdempotencyKey();
      setMessage(result?.created ? 'Карта урожайности принята.' : 'Этот импорт уже был принят.');
      setPreview(null);
      setFileState(null);
      setPending('');
      refreshHistory();
    } catch (error) {
      if (controller.signal.aborted || generation !== actionGenerationRef.current || cancelled(error)) return;
      setMessage(error?.response?.status === 409
        ? 'Файл, fingerprint или ключ запроса конфликтует с уже принятым импортом.'
        : 'Результат принятия не подтверждён. Повторите тот же запрос.');
    } finally {
      if (!controller.signal.aborted && generation === actionGenerationRef.current) setPending('');
    }
  }

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-3 text-sm text-blue-950" role="note">
        Импортируются измеренные точки урожайности. Спутниковые индексы не используются
        для вычисления урожайности.
      </div>
      <ImportHistory items={history} loading={historyLoading} />
      {readOnly ? (
        <section className="card" aria-live="polite">
          <h3 className="font-semibold text-agro-text">Только просмотр</h3>
          <p className="mt-2 text-sm text-agro-muted">Viewer не может загружать или принимать карты урожайности.</p>
        </section>
      ) : (
        <section className="card space-y-4" aria-labelledby="yield-import-title">
          <div>
            <h3 id="yield-import-title" className="font-semibold text-agro-text">Импорт карты урожайности</h3>
            <p className="mt-1 text-xs text-agro-muted">Схема: yield_point_csv_v1 · максимум 5 000 строк</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm text-agro-text">
              Сезон
              <input className="input mt-1 min-h-11 w-full" max={Number(todayTashkentDate().slice(0, 4)) + 1} min="2000" type="number" value={seasonYear} onChange={(event) => setSeasonYear(event.target.value)} />
            </label>
            <label className="text-sm text-agro-text">
              Код культуры
              <input className="input mt-1 min-h-11 w-full" maxLength="64" value={cropCode} onChange={(event) => setCropCode(event.target.value)} />
            </label>
            <label className="text-sm text-agro-text">
              Единица исходных данных
              <select className="input mt-1 min-h-11 w-full" value={yieldUnit} onChange={(event) => setYieldUnit(event.target.value)}>
                <option value="t_ha">т/га</option>
                <option value="kg_ha">кг/га</option>
              </select>
            </label>
            <label className="text-sm text-agro-text">
              Источник
              <input className="input mt-1 min-h-11 w-full" maxLength="100" value={sourceProvider} onChange={(event) => setSourceProvider(event.target.value)} />
            </label>
          </div>
          <label className="block text-sm text-agro-text">
            CSV-файл
            <input accept=".csv,text/csv" className="mt-1 block min-h-11 w-full text-sm" onChange={chooseFile} type="file" />
          </label>
          {fileState && (
            <div className="rounded-lg border border-agro-surface2 px-3 py-3 text-sm">
              <p className="font-medium text-agro-text">{fileState.name}</p>
              <p className="mt-1 text-xs text-agro-muted">
                {fileState.rows.length} строк · SHA-256 {fileState.sha256.slice(0, 12)}…
              </p>
            </div>
          )}
          <button className="btn-secondary min-h-11 w-full sm:w-auto" disabled={!requestPayload || Boolean(pending)} onClick={runPreview} type="button">
            {pending === 'preview' ? 'Проверка…' : 'Проверить файл'}
          </button>
          {preview && (
            <div className="space-y-3" aria-live="polite">
              <dl className="grid grid-cols-2 gap-3 rounded-lg border border-agro-surface2 px-3 py-3 text-sm sm:grid-cols-4">
                <div><dt className="text-xs text-agro-muted">Всего</dt><dd className="font-semibold">{preview.total_rows}</dd></div>
                <div><dt className="text-xs text-agro-muted">Принято</dt><dd className="font-semibold text-emerald-700">{preview.accepted_rows}</dd></div>
                <div><dt className="text-xs text-agro-muted">Отклонено</dt><dd className="font-semibold text-red-700">{preview.rejected_rows}</dd></div>
                <div><dt className="text-xs text-agro-muted">Средняя</dt><dd className="font-semibold">{preview.summary?.yield_mean_t_ha ?? '—'} т/га</dd></div>
              </dl>
              {preview.rejected_rows > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[420px] text-left text-sm">
                    <thead><tr className="text-xs text-agro-muted"><th className="p-2">Строка</th><th className="p-2">Причина</th></tr></thead>
                    <tbody>{preview.rejected.slice(0, 100).map((item) => (
                      <tr className="border-t border-agro-surface2" key={`${item.source_row}-${item.reason_code}`}>
                        <td className="p-2">{item.source_row + 1}</td>
                        <td className="p-2">{REJECTION_LABELS[item.reason_code] || item.reason_code}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              )}
              <button className="btn-primary min-h-11 w-full sm:w-auto" disabled={preview.rejected_rows > 0 || Boolean(pending)} onClick={acceptPreview} type="button">
                {pending === 'accept' ? 'Принятие…' : 'Подтвердить импорт'}
              </button>
            </div>
          )}
          {message && <p aria-live="polite" className="text-sm text-agro-muted">{message}</p>}
        </section>
      )}
    </div>
  );
}
