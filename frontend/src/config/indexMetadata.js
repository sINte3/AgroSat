/**
 * Multi-index metadata configuration.
 *
 * Provides display labels, agronomic meaning, formatting rules, and
 * UI descriptions for all supported vegetation indices.
 *
 * NDVI is included for UI display only — its data comes from the legacy
 * /api/ndvi/* route, not from /api/satellite-indices/*.
 */
const INDEX_METADATA = {
  ndvi: {
    code: 'ndvi',
    label: 'NDVI',
    fullLabel: 'Normalized Difference Vegetation Index',
    shortMeaning: 'Вегетация',
    description:
      'Стандартный индекс вегетации. Отражает общее состояние и зеленую биомассу посевов. 0–1, высокие значения = густая растительность.',
    precision: 4,
    negativeAllowed: false,
    valueRange: { min: 0, max: 1 },
    dataSource: 'legacy', // fetched from /api/ndvi/*
    satelliteRoute: null,
  },
  savi: {
    code: 'savi',
    label: 'SAVI',
    fullLabel: 'Soil-Adjusted Vegetation Index',
    shortMeaning: 'Вегетация (почв. коррекция)',
    description:
      'Индекс вегетации с коррекцией на яркость почвы. Полезен на полях с разреженным покровом, где NDVI может завышать сигнал почвы.',
    precision: 4,
    negativeAllowed: false,
    valueRange: { min: -1, max: 1 },
    dataSource: 'satellite',
    satelliteRoute: true,
  },
  evi: {
    code: 'evi',
    label: 'EVI',
    fullLabel: 'Enhanced Vegetation Index',
    shortMeaning: 'Усиленная вегетация',
    description:
      'Улучшенный индекс вегетации с коррекцией атмосферных искажений и фона почвы. Менее насыщается в густых посевах, чувствительнее к структуре полога.',
    precision: 4,
    negativeAllowed: false,
    valueRange: { min: -1, max: 1 },
    dataSource: 'satellite',
    satelliteRoute: true,
  },
  ndmi: {
    code: 'ndmi',
    label: 'NDMI',
    fullLabel: 'Normalized Difference Moisture Index',
    shortMeaning: 'Влажность / водный стресс',
    description:
      'Индекс влажности посевов. Отражает содержание воды в листьях и почве. Отрицательные значения указывают на сухую поверхность или дефицит влаги.',
    precision: 4,
    negativeAllowed: true,
    valueRange: { min: -1, max: 1 },
    dataSource: 'satellite',
    satelliteRoute: true,
  },
  ndre: {
    code: 'ndre',
    label: 'NDRE',
    fullLabel: 'Red-Edge Normalized Difference Vegetation Index',
    shortMeaning: 'Хлорофилл / красная граница',
    description:
      'Индекс по красной границе спектра. Чувствителен к содержанию хлорофилла и азотному статусу. Полезен для оценки продуктивности на поздних стадиях вегетации.',
    precision: 4,
    negativeAllowed: true,
    valueRange: { min: -1, max: 1 },
    dataSource: 'satellite',
    satelliteRoute: true,
  },
};

// (SATELLITE_INDEX_CODES and ALL_INDEX_CODES are now defined after INDEX_METADATA)

/** Get metadata for a single index */
export function getIndexMetadata(code) {
  return INDEX_METADATA[code?.toLowerCase()] || null;
}

/** Get display label for a color indicator based on index rules */
export function getIndexColor(value, code) {
  const meta = INDEX_METADATA[code?.toLowerCase()];
  if (value == null) return '#9ca3af';

  // For NDVI — use standard vegetation color scale
  if (code === 'ndvi') {
    if (value < 0.15) return '#dc2626';
    if (value < 0.3) return '#f97316';
    if (value < 0.45) return '#eab308';
    if (value < 0.6) return '#84cc16';
    return '#16a34a';
  }

  // For indices where negative is allowed (NDMI, NDRE)
  if (meta?.negativeAllowed) {
    if (value < -0.1) return '#2563eb';
    if (value < 0) return '#60a5fa';
    if (value < 0.1) return '#f59e0b';
    if (value < 0.3) return '#84cc16';
    return '#16a34a';
  }

  // For SAVI, EVI
  if (value < 0.1) return '#dc2626';
  if (value < 0.2) return '#f97316';
  if (value < 0.3) return '#eab308';
  if (value < 0.4) return '#84cc16';
  return '#16a34a';
}

// ─── Satellite coverage status config ────────────────────────────────────

/**
 * Labels and styling for coverage_status values returned by
 * /api/satellite-indices/coverage per field.
 */
export const COVERAGE_STATUS_CONFIG = {
  none: {
    label: 'Нет данных',
    shortLabel: 'Нет данных',
    color: '#9ca3af',       // gray-400
    bgColor: '#f3f4f6',     // gray-100
    textColor: '#6b7280',    // gray-500
    icon: '○',
    priority: 'high',
  },
  partial: {
    label: 'Частично',
    shortLabel: 'Частично',
    color: '#f59e0b',       // amber-500
    bgColor: '#fffbeb',     // amber-50
    textColor: '#b45309',   // amber-700
    icon: '◐',
    priority: 'medium',
  },
  complete: {
    label: 'Полное покрытие',
    shortLabel: 'Полное',
    color: '#16a34a',       // green-600
    bgColor: '#f0fdf4',     // green-50
    textColor: '#15803d',   // green-700
    icon: '●',
    priority: 'normal',
  },
};

/**
 * Labels and styling for freshness_status values.
 */
export const FRESHNESS_STATUS_CONFIG = {
  no_data: {
    label: 'Нет данных',
    shortLabel: 'Нет данных',
    color: '#9ca3af',
    bgColor: '#f3f4f6',
    textColor: '#6b7280',
  },
  fresh: {
    label: 'Актуально',
    shortLabel: 'Актуально',
    color: '#16a34a',
    bgColor: '#f0fdf4',
    textColor: '#15803d',
  },
  stale: {
    label: 'Устарело',
    shortLabel: 'Устарело',
    color: '#dc2626',       // red-600
    bgColor: '#fef2f2',     // red-50
    textColor: '#b91c1c',   // red-700
  },
  future_date: {
    label: 'Дата из будущего',
    shortLabel: 'Будущая дата',
    color: '#f59e0b',
    bgColor: '#fffbeb',
    textColor: '#b45309',
  },
};

/**
 * Coverage priority labels for operational hints.
 */
export const COVERAGE_PRIORITY_LABELS = {
  high: {
    label: 'Требует сбора данных',
    detail: 'Поле без данных или с устаревшими спутниковыми данными. Приоритет для проверки.',
  },
  medium: {
    label: 'Частичное покрытие',
    detail: 'Некоторые спутниковые индексы отсутствуют. Требуется дообследование.',
  },
  normal: {
    label: 'Данные актуальны',
    detail: 'Все спутниковые индексы доступны и актуальны.',
  },
};

/**
 * Supported satellite index codes (excludes NDVI).
 */
export const SATELLITE_INDEX_CODES = ['savi', 'evi', 'ndmi', 'ndre'];

/**
 * All index codes including legacy NDVI.
 */
export const ALL_INDEX_CODES = ['ndvi', 'savi', 'evi', 'ndmi', 'ndre'];

export default INDEX_METADATA;
