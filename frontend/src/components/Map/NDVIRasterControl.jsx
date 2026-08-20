import { Fragment, useEffect, useRef, useState } from 'react';
import usePixelNDVIWorkspace from '../../hooks/usePixelNDVIWorkspace';
import { clampDivider } from '../../utils/pixelNdviState';

const SERVICE_ERRORS = new Set([502, 503, 504]);

function formatDate(value) {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? value
    : new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short', year: 'numeric' }).format(parsed);
}

function qualityText(scene) {
  const cloud = Number.isFinite(scene?.cloud_cover_pct) ? `${Math.round(scene.cloud_cover_pct)}% облаков` : 'облачность неизвестна';
  const valid = Number.isFinite(scene?.valid_pixel_pct) ? `${Math.round(scene.valid_pixel_pct)}% валидных` : 'валидность неизвестна';
  return `${cloud} · ${valid}`;
}

function DateChip({ scene, selected, onSelect, prefix }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(scene.scene_id)}
      aria-pressed={selected}
      aria-label={`${prefix}: ${formatDate(scene.acquired_at)}, ${qualityText(scene)}`}
      className={`min-w-[132px] rounded-lg border px-2.5 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-600 focus-visible:ring-offset-2 ${
        selected
          ? 'border-green-700 bg-green-50 text-green-950'
          : 'border-slate-200 bg-white text-slate-700 hover:border-slate-400'
      }`}
    >
      <span className="block text-xs font-semibold tabular-nums">{formatDate(scene.acquired_at)}</span>
      <span className="mt-0.5 block text-[11px] leading-4 text-slate-600">{qualityText(scene)}</span>
      {scene.freshness === 'stale' && <span className="mt-1 block text-[11px] font-medium text-amber-800">Снимок устарел</span>}
    </button>
  );
}

function DateStrip({ label, scenes, selected, onSelect }) {
  return (
    <fieldset>
      <legend className="mb-1.5 text-xs font-semibold text-slate-800">{label}</legend>
      <div className="flex gap-2 overflow-x-auto pb-1" role="list" aria-label={label}>
        {scenes.map((scene) => (
          <div role="listitem" key={scene.scene_id}>
            <DateChip scene={scene} selected={scene.scene_id === selected} onSelect={onSelect} prefix={label} />
          </div>
        ))}
      </div>
    </fieldset>
  );
}

function ComparisonDivider({ value, onChange }) {
  const overlayRef = useRef(null);
  const draggingRef = useRef(false);

  function updateFromPointer(event) {
    const bounds = overlayRef.current?.getBoundingClientRect();
    if (!bounds?.width) return;
    onChange(clampDivider(((event.clientX - bounds.left) / bounds.width) * 100));
  }

  function handleKeyDown(event) {
    const step = event.shiftKey ? 10 : 2;
    if (event.key === 'ArrowLeft') onChange(clampDivider(value - step));
    else if (event.key === 'ArrowRight') onChange(clampDivider(value + step));
    else if (event.key === 'Home') onChange(5);
    else if (event.key === 'End') onChange(95);
    else return;
    event.preventDefault();
  }

  return (
    <div ref={overlayRef} className="pointer-events-none absolute inset-0 z-20" aria-hidden="false">
      <div className="pointer-events-none absolute inset-y-0 w-0.5 bg-white shadow-sm" style={{ left: `${value}%` }} />
      <button
        type="button"
        role="slider"
        aria-label="Разделитель сравнения снимков"
        aria-valuemin={5}
        aria-valuemax={95}
        aria-valuenow={value}
        className="pointer-events-auto absolute top-1/2 flex h-14 w-11 -translate-x-1/2 -translate-y-1/2 touch-none items-center justify-center rounded-lg border border-slate-300 bg-white text-slate-700 shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-600"
        style={{ left: `${value}%` }}
        onKeyDown={handleKeyDown}
        onPointerDown={(event) => {
          draggingRef.current = true;
          event.currentTarget.setPointerCapture(event.pointerId);
          updateFromPointer(event);
        }}
        onPointerMove={(event) => {
          if (draggingRef.current) updateFromPointer(event);
        }}
        onPointerUp={(event) => {
          draggingRef.current = false;
          event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={() => { draggingRef.current = false; }}
      >
        <span aria-hidden="true" className="text-lg leading-none">↔</span>
      </button>
    </div>
  );
}

function Legend({ workspace }) {
  if (!workspace) return null;
  return (
    <details className="rounded-lg border border-slate-200 bg-slate-50/80 px-3 py-2">
      <summary className="cursor-pointer text-xs font-semibold text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-600">
        Легенда NDVI
      </summary>
      <ul className="mt-2 space-y-1.5">
        {workspace.legend.map((item) => (
          <li key={`${item.from}-${item.to}`} className="flex items-start gap-2 text-[11px] leading-4 text-slate-700">
            <span className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded-sm" style={{ backgroundColor: item.color }} aria-hidden="true" />
            <span><span className="font-mono tabular-nums">{item.from.toFixed(2)}–{item.to.toFixed(2)}</span> · {item.label}</span>
          </li>
        ))}
        <li className="flex items-start gap-2 text-[11px] leading-4 text-slate-700">
          <span className="mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm bg-slate-200 text-[9px] text-slate-700" aria-hidden="true">×</span>
          <span>Нет данных / облако</span>
        </li>
      </ul>
      <p className="mt-2 text-[11px] leading-4 text-slate-600">{workspace.disclaimer}</p>
    </details>
  );
}

function PixelSample({ sample }) {
  if (sample.status === 'idle') {
    return <p className="text-[11px] leading-4 text-slate-600">Нажмите на поле, чтобы увидеть значение пикселя.</p>;
  }
  if (sample.status === 'loading') {
    return <p role="status" className="text-[11px] text-slate-600">Определяем значение пикселя…</p>;
  }
  if (sample.status === 'error') {
    return <p role="alert" className="text-[11px] text-amber-800">Не удалось определить значение в этой точке.</p>;
  }
  const value = sample.value;
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2" role="status" aria-label="Результат проверки пикселя">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs font-semibold text-slate-800">Пиксель NDVI</span>
        <span className="font-mono text-sm font-semibold tabular-nums text-slate-950">
          {value.status === 'value' ? value.ndvi.toFixed(3) : 'Нет данных'}
        </span>
      </div>
      {value.classification && <p className="mt-1 text-[11px] text-slate-700">{value.classification.label}</p>}
      <p className="mt-1 font-mono text-[10px] tabular-nums text-slate-600">
        {value.longitude.toFixed(5)}, {value.latitude.toFixed(5)} · {formatDate(value.acquired_at)}
      </p>
    </div>
  );
}

export default function NDVIRasterControl({ map, fieldId, onMetadataChange }) {
  const [enabled, setEnabled] = useState(false);
  const [opacity, setOpacity] = useState(0.76);
  const [comparisonEnabled, setComparisonEnabled] = useState(false);
  const [divider, setDivider] = useState(50);
  const workspace = usePixelNDVIWorkspace({
    map, fieldId, enabled, opacity, comparisonEnabled, divider,
  });
  const readyWorkspace = workspace.layer.workspaceA;

  useEffect(() => {
    setComparisonEnabled(false);
    setDivider(50);
  }, [fieldId]);

  useEffect(() => {
    onMetadataChange?.(enabled ? readyWorkspace : null);
  }, [enabled, onMetadataChange, readyWorkspace]);

  const errorStatus = workspace.layer.errorStatus || workspace.catalog.errorStatus;
  const errorMessage = errorStatus === 404
    ? 'Для этой даты нет пригодного снимка'
    : SERVICE_ERRORS.has(errorStatus)
      ? 'Спутниковый слой временно недоступен. Попробуйте позже.'
      : errorStatus ? 'Не удалось загрузить пиксельный NDVI.' : null;

  return (
    <Fragment>
      {enabled && comparisonEnabled && workspace.layer.status === 'ready' && (
        <ComparisonDivider value={divider} onChange={setDivider} />
      )}

      <section className="absolute right-3 top-[140px] z-30 w-[min(23rem,calc(100vw-1.5rem))] max-h-[calc(100%-152px)] overflow-y-auto rounded-xl border border-slate-200 bg-white/95 p-3 text-xs shadow-sm backdrop-blur-sm max-sm:bottom-3 max-sm:left-3 max-sm:right-3 max-sm:top-auto max-sm:w-auto max-sm:max-h-[46vh]" aria-label="Рабочее пространство пиксельного NDVI">
        <div className="flex min-h-11 items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">Пиксельный NDVI</h2>
            <p className="mt-0.5 text-[11px] text-slate-600">Пространственная неоднородность внутри поля</p>
          </div>
          <label className="relative inline-flex min-h-11 min-w-11 cursor-pointer items-center justify-end">
            <span className="sr-only">Пиксельный NDVI</span>
            <input
              type="checkbox"
              checked={enabled}
              disabled={!fieldId}
              onChange={(event) => setEnabled(event.target.checked)}
              aria-describedby={!fieldId ? 'pixel-ndvi-field-hint' : undefined}
              className="peer sr-only"
            />
            <span className="h-6 w-11 rounded-full bg-slate-300 transition-colors peer-checked:bg-green-700 peer-focus-visible:ring-2 peer-focus-visible:ring-green-600 peer-focus-visible:ring-offset-2 peer-disabled:cursor-not-allowed peer-disabled:opacity-50 after:absolute after:right-[22px] after:top-[13px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-transform peer-checked:after:translate-x-5" aria-hidden="true" />
          </label>
        </div>

        {!fieldId && <p id="pixel-ndvi-field-hint" className="mt-1 text-slate-600">Выберите поле на карте или в списке.</p>}

        {enabled && fieldId && (
          <div className="mt-3 space-y-3 border-t border-slate-200 pt-3" aria-busy={workspace.catalog.status === 'loading' || workspace.layer.status === 'loading'}>
            {workspace.catalog.status === 'loading' && <p role="status" aria-live="polite" className="text-slate-700">Загрузка доступных дат…</p>}
            {workspace.catalog.status === 'empty' && <p role="status" className="rounded-lg bg-slate-100 px-3 py-2 text-slate-700">Для этой даты нет пригодного снимка</p>}
            {workspace.catalog.scenes.length > 0 && (
              <DateStrip label="Дата снимка" scenes={workspace.catalog.scenes} selected={workspace.sceneA} onSelect={workspace.setSceneA} />
            )}

            {workspace.layer.status === 'loading' && <p role="status" aria-live="polite" className="rounded-lg bg-slate-100 px-3 py-2 text-slate-700">Загрузка спутникового слоя…</p>}
            {errorMessage && (
              <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-amber-950" role="alert">
                <p>{errorMessage}</p>
                <button type="button" onClick={workspace.retry} className="mt-2 min-h-11 rounded-lg border border-amber-500 px-3 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-700">Повторить</button>
              </div>
            )}

            {workspace.layer.status === 'ready' && readyWorkspace && (
              <>
                <div className="grid grid-cols-2 gap-2 rounded-lg bg-slate-50 px-3 py-2 text-[11px] text-slate-700">
                  <div><span className="block text-slate-600">Облачность</span><strong className="font-mono font-semibold tabular-nums text-slate-900">{Number.isFinite(readyWorkspace.scene.cloud_cover_pct) ? `${readyWorkspace.scene.cloud_cover_pct.toFixed(1)}%` : '—'}</strong></div>
                  <div><span className="block text-slate-600">Доля валидных пикселей</span><strong className="font-mono font-semibold tabular-nums text-slate-900">{readyWorkspace.summary.valid_pixel_pct.toFixed(1)}%</strong></div>
                </div>

                <label className="block text-slate-800">
                  <span className="mb-1 flex items-center justify-between font-semibold"><span>Прозрачность</span><span className="font-mono tabular-nums">{Math.round(opacity * 100)}%</span></span>
                  <input type="range" min="0.15" max="1" step="0.01" value={opacity} onChange={(event) => setOpacity(Number(event.target.value))} aria-label="Прозрачность слоя NDVI" className="h-11 w-full accent-green-700" />
                </label>

                <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2 font-semibold text-slate-800">
                  <span>Сравнить снимки</span>
                  <input type="checkbox" checked={comparisonEnabled} disabled={workspace.catalog.scenes.length < 2} onChange={(event) => setComparisonEnabled(event.target.checked)} className="h-5 w-5 accent-green-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-600" />
                </label>

                {comparisonEnabled && (
                  <div className="space-y-2">
                    <DateStrip label="Дата снимка B" scenes={workspace.catalog.scenes.filter((scene) => scene.scene_id !== workspace.sceneA)} selected={workspace.sceneB} onSelect={workspace.setSceneB} />
                    <label className="block text-slate-800">
                      <span className="mb-1 flex justify-between font-semibold"><span>Положение разделителя</span><span className="font-mono tabular-nums">{divider}%</span></span>
                      <input type="range" min="5" max="95" step="1" value={divider} onChange={(event) => setDivider(clampDivider(event.target.value))} aria-label="Положение разделителя сравнения" className="h-11 w-full accent-green-700" />
                    </label>
                  </div>
                )}

                <PixelSample sample={workspace.sample} />
                <Legend workspace={readyWorkspace} />
              </>
            )}
          </div>
        )}
      </section>
    </Fragment>
  );
}
