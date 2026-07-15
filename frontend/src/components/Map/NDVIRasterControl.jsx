import { useEffect, useState } from 'react';
import useNDVIRasterLayer from '../../hooks/useNDVIRasterLayer';

const CACHE_LABELS = { HIT: 'Кэш', MISS: 'Загружено', BYPASS: 'Без кэша' };
const SERVICE_ERRORS = new Set([502, 503, 504]);

function tashkentToday() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tashkent', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date());
  const value = Object.fromEntries(parts.map(({ type, value: part }) => [type, part]));
  return `${value.year}-${value.month}-${value.day}`;
}

export default function NDVIRasterControl({ map, fieldId, onMetadataChange }) {
  const [enabled, setEnabled] = useState(false);
  const [dateTo, setDateTo] = useState(tashkentToday);
  const [opacity, setOpacity] = useState(0.72);
  const raster = useNDVIRasterLayer({ map, fieldId, enabled, dateTo, opacity });
  const metadata = raster.metadata;

  useEffect(() => {
    onMetadataChange?.(enabled ? metadata : null);
  }, [enabled, metadata, onMetadataChange]);

  const errorMessage = raster.errorStatus === 404
    ? 'Для выбранного поля нет принятого NDVI-снимка до указанной даты.'
    : SERVICE_ERRORS.has(raster.errorStatus)
      ? 'Пиксельный NDVI сейчас недоступен.'
      : raster.status === 'error' ? 'Не удалось загрузить пиксельный NDVI.' : null;

  return (
    <section className="absolute top-[104px] right-3 z-10 w-[min(18rem,calc(100vw-1.5rem))] rounded-lg border border-slate-200 bg-white/95 p-3 text-xs shadow-lg backdrop-blur-sm">
      <label className="flex cursor-pointer items-center justify-between gap-3 font-semibold text-slate-800">
        <span>Пиксельный NDVI</span>
        <input
          type="checkbox"
          checked={enabled}
          disabled={!fieldId}
          onChange={(event) => setEnabled(event.target.checked)}
          className="h-4 w-4 accent-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:cursor-not-allowed"
          aria-describedby={!fieldId ? 'ndvi-raster-selection-hint' : undefined}
        />
      </label>
      {!fieldId && <p id="ndvi-raster-selection-hint" className="mt-1 text-slate-500">Выберите поле на карте или в списке.</p>}

      {enabled && fieldId && (
        <div className="mt-3 space-y-3 border-t border-slate-100 pt-3">
          <label className="block text-slate-700">
            <span className="mb-1 block font-medium">Снимок не позднее</span>
            <input type="date" value={dateTo} max={tashkentToday()} onChange={(event) => setDateTo(event.target.value)} className="w-full rounded border border-slate-300 px-2 py-1.5 text-slate-800 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200" />
          </label>
          <label className="block text-slate-700">
            <span className="mb-1 flex justify-between font-medium"><span>Прозрачность слоя</span><span>{Math.round(opacity * 100)}%</span></span>
            <input type="range" min="0" max="1" step="0.01" value={opacity} onChange={(event) => setOpacity(Number(event.target.value))} className="w-full accent-blue-600" />
          </label>

          <div aria-live="polite" className="text-slate-600">
            {raster.status === 'loading' && 'Загрузка пиксельного NDVI…'}
            {errorMessage && <div className="space-y-2 text-amber-800"><p>{errorMessage}</p>{raster.errorStatus !== 404 && <button type="button" onClick={raster.retry} className="rounded border border-amber-400 px-2 py-1 font-medium focus:outline-none focus:ring-2 focus:ring-amber-500">Повторить</button>}</div>}
            {raster.status === 'ready' && metadata && <div className="space-y-1"><p>Фактическая дата снимка: {metadata.observation_date}</p><p>{metadata.satellite || 'Sentinel-2'} · {CACHE_LABELS[raster.cacheState] || 'Без кэша'}</p><p className="pt-1 text-slate-500">Растр показывает спектральный NDVI-сигнал, а не агрономический диагноз.</p></div>}
          </div>
        </div>
      )}
    </section>
  );
}
