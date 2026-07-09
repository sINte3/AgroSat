import { getIndexMetadata, ALL_INDEX_CODES } from '../../config/indexMetadata';

/**
 * Compact legend explaining what each vegetation index means.
 * Designed to fit in a map sidebar or field detail area.
 */
export default function IndexLegend() {
  return (
    <div className="card text-xs space-y-3">
      <h4 className="text-xs font-semibold text-agro-muted uppercase tracking-wide">
        Что такое спутниковые индексы
      </h4>
      <div className="space-y-2">
        {ALL_INDEX_CODES.map((code) => {
          const meta = getIndexMetadata(code);
          if (!meta) return null;
          return (
            <div key={code} className="space-y-0.5">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-agro-text font-mono text-sm">
                  {meta.label}
                </span>
                <span className="text-agro-muted text-[10px]">
                  {meta.fullLabel}
                </span>
              </div>
              <p className="text-[11px] text-agro-muted leading-relaxed">
                {meta.description}
              </p>
              <div className="flex items-center gap-2 text-[10px] text-agro-muted">
                <span>Диапазон: {meta.valueRange.min} – {meta.valueRange.max}</span>
                {meta.dataSource === 'legacy' && (
                  <span className="inline-block px-1.5 py-0.5 rounded bg-agro-surface2/50">
                    Исторический NDVI
                  </span>
                )}
                {meta.dataSource === 'satellite' && (
                  <span className="inline-block px-1.5 py-0.5 rounded bg-blue-50 text-blue-600">
                    Multi-index API
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
