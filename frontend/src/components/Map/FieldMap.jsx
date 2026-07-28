import React, { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';
import { getSameOriginApiAuthorizationHeaders } from '../../api/client';
import {
  getFieldTileMetadata,
  resolveFieldTileTemplate,
  validFieldTileMetadata,
} from '../../api/fieldTiles';
import MapLegend from './MapLegend';
import MapHoverPopup from './MapHoverPopup';
import NDVIRasterControl from './NDVIRasterControl';
import { getIndexColor } from '../../config/indexMetadata';

// ─── Constants ──────────────────────────────────────────────────────────────────

const CROP_COLORS = {
  wheat:        '#EAB308',
  cotton_drip:  '#60A5FA',
  cotton_canal: '#F472B6',
  alfalfa:      '#A78BFA',
  rice:         '#34D399',
  maize:        '#FB923C',
  default:      '#6B7280',
};

const CROP_COLOR_EXPR = [
  'case',
  ['==', ['get', 'current_crop'], 'Пшеница озимая'],  CROP_COLORS.wheat,
  ['all', ['==', ['get', 'current_crop'], 'Хлопок'], ['==', ['get', 'irrigation_type'], 'drip']],   CROP_COLORS.cotton_drip,
  ['all', ['==', ['get', 'current_crop'], 'Хлопок'], ['==', ['get', 'irrigation_type'], 'canal']],  CROP_COLORS.cotton_canal,
  ['==', ['get', 'current_crop'], 'Люцерна'],  CROP_COLORS.alfalfa,
  ['==', ['get', 'current_crop'], 'Рис'],      CROP_COLORS.rice,
  ['==', ['get', 'current_crop'], 'Кукуруза'], CROP_COLORS.maize,
  CROP_COLORS.default,
];

const OSM_SOURCE = {
  type: 'raster',
  tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
  tileSize: 256,
  attribution: '© OpenStreetMap contributors',
  maxzoom: 18,
};

const ESRI_SATELLITE_SOURCE = {
  type: 'raster',
  tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
  tileSize: 256,
  attribution: '© Esri, Maxar, Airbus',
  maxzoom: 18,
};

const ESRI_LABELS_SOURCE = {
  type: 'raster',
  tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'],
  tileSize: 256,
  attribution: '© Esri',
  maxzoom: 18,
};

const GLYPHS = 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf';

const MAP_STYLES = {
  satellite: { label: 'Спутник' },
  osm: { label: 'Карта' },
  hybrid: { label: 'Гибрид' },
};

const BASE_MAP_SOURCE_IDS = {
  satellite: 'agrosat-base-satellite-source',
  osm: 'agrosat-base-osm-source',
  hybridLabels: 'agrosat-base-hybrid-labels-source',
};

const BASE_MAP_LAYER_IDS = {
  satellite: 'agrosat-base-satellite',
  osm: 'agrosat-base-osm',
  hybridLabels: 'agrosat-base-hybrid-labels',
};

const BASE_MAP_VISIBILITY = {
  satellite: { satellite: 'visible', osm: 'none', hybridLabels: 'none' },
  osm: { satellite: 'none', osm: 'visible', hybridLabels: 'none' },
  hybrid: { satellite: 'visible', osm: 'none', hybridLabels: 'visible' },
};

const DEFERRED_SATELLITE_PIPELINE_ENABLED =
  import.meta.env.VITE_DEFERRED_SATELLITE_PIPELINE !== 'false';
const SATELLITE_REVEAL_DELAY_MS = 220;
const HYBRID_LABELS_FALLBACK_MS = 1800;
const SATELLITE_SLOW_STATUS_MS = 5000;

const COMPOSITE_MAP_STYLE = {
  version: 8,
  glyphs: GLYPHS,
  sources: {
    [BASE_MAP_SOURCE_IDS.satellite]: ESRI_SATELLITE_SOURCE,
    [BASE_MAP_SOURCE_IDS.osm]: OSM_SOURCE,
    [BASE_MAP_SOURCE_IDS.hybridLabels]: ESRI_LABELS_SOURCE,
  },
  layers: [
    { id: BASE_MAP_LAYER_IDS.osm, type: 'raster', source: BASE_MAP_SOURCE_IDS.osm, layout: { visibility: DEFERRED_SATELLITE_PIPELINE_ENABLED ? 'visible' : 'none' }, paint: { 'raster-fade-duration': 0 } },
    { id: BASE_MAP_LAYER_IDS.satellite, type: 'raster', source: BASE_MAP_SOURCE_IDS.satellite, layout: { visibility: DEFERRED_SATELLITE_PIPELINE_ENABLED ? 'none' : 'visible' }, paint: { 'raster-fade-duration': 0 } },
    { id: BASE_MAP_LAYER_IDS.hybridLabels, type: 'raster', source: BASE_MAP_SOURCE_IDS.hybridLabels, layout: { visibility: 'none' }, paint: { 'raster-fade-duration': 0 } },
  ],
};

function getBaseMapVisibility(styleKey) {
  return BASE_MAP_VISIBILITY[styleKey] || null;
}

function applyBaseMapVisibility(map, styleKey) {
  const visibility = getBaseMapVisibility(styleKey);
  if (!map || !visibility) return false;
  const entries = Object.entries(BASE_MAP_LAYER_IDS);
  if (entries.some(([, layerId]) => !map.getLayer(layerId))) return false;
  if (entries.every(([key, layerId]) =>
    (map.getLayoutProperty(layerId, 'visibility') || 'visible') === visibility[key]
  )) return true;
  try {
    entries.forEach(([key, layerId]) => map.setLayoutProperty(layerId, 'visibility', visibility[key]));
    return true;
  } catch (_) {
    return false;
  }
}

function setBaseLayerVisibility(map, layerId, visibility) {
  if (!map || map._removed || !map.getLayer(layerId)) return false;
  try {
    if ((map.getLayoutProperty(layerId, 'visibility') || 'visible') !== visibility) {
      map.setLayoutProperty(layerId, 'visibility', visibility);
    }
    return true;
  } catch (_) {
    return false;
  }
}

const DEFAULT_CENTER = [64.4286, 39.7747];
const DEFAULT_ZOOM = 7;
const MAP_MIN_ZOOM = 3;
const MAP_MAX_ZOOM = 18;
const MAP_PIXEL_RATIO_CAP = 1.25;
const FAST_WHEEL_ZOOM_RATE = 1 / 240;
const FAST_TRACKPAD_ZOOM_RATE = 1 / 70;
const LAYERS = ['fields-fill', 'fields-label'];
const FIELD_SOURCE_ID = 'fields-source';
const FIELD_SOURCE_LAYER = 'fields';
const FIELD_LAYER_IDS = ['fields-label', 'fields-border', 'fields-fill'];

const NO_DATA_GRAY = '#4b5563';
const NO_DATA_GRAY_HEX = '#6B7280';

function getMapPixelRatio() {
  const devicePixelRatio = typeof window === 'undefined' ? 1 : window.devicePixelRatio;
  return Math.min(Math.max(Number.isFinite(devicePixelRatio) ? devicePixelRatio : 1, 1), MAP_PIXEL_RATIO_CAP);
}

function transformMapRequest(url, resourceType) {
  const headers = getSameOriginApiAuthorizationHeaders(url);
  if (['Tile', 'Glyphs', 'SpriteImage', 'SpriteJSON'].includes(resourceType)) {
    return { url, headers, cache: 'force-cache' };
  }
  return { url, headers };
}

function configureSupportedMapInteractions(map) {
  if (typeof map?.scrollZoom?.setWheelZoomRate === 'function') {
    map.scrollZoom.setWheelZoomRate(FAST_WHEEL_ZOOM_RATE);
  }
  if (typeof map?.scrollZoom?.setZoomRate === 'function') {
    map.scrollZoom.setZoomRate(FAST_TRACKPAD_ZOOM_RATE);
  }
  if (typeof map?.touchZoomRotate?.disableRotation === 'function') {
    map.touchZoomRotate.disableRotation();
  }
  if (typeof map?.dragRotate?.disable === 'function') map.dragRotate.disable();
  if (typeof map?.touchPitch?.disable === 'function') map.touchPitch.disable();
  if (typeof map?.keyboard?.disableRotation === 'function') map.keyboard.disableRotation();
}

// ─── Map mode config ────────────────────────────────────────────────────────────

const MAP_MODES = [
  { code: 'crop',     label: 'Культуры' },
  { code: 'ndvi',     label: 'NDVI' },
  { code: 'savi',     label: 'SAVI' },
  { code: 'evi',      label: 'EVI' },
  { code: 'ndmi',     label: 'NDMI' },
  { code: 'ndre',     label: 'NDRE' },
  { code: 'coverage', label: 'Покрытие' },
  { code: 'freshness',label: 'Актуальность' },
];

// ─── Helper: get color by index value ──────────────────────────────────────────

function getIndexModeColor(value, code) {
  if (value == null || isNaN(value)) return NO_DATA_GRAY;
  return getIndexColor(value, code);
}

// ─── Helper: get coverage status color ──────────────────────────────────────────

function getCoverageColor(status) {
  switch (status) {
    case 'complete': return '#16a34a';
    case 'partial':  return '#f59e0b';
    case 'none':     return NO_DATA_GRAY;
    default:         return NO_DATA_GRAY;
  }
}

// ─── Helper: get freshness status color ─────────────────────────────────────────

function getFreshnessColor(status) {
  switch (status) {
    case 'fresh':      return '#16a34a';
    case 'stale':      return '#dc2626';
    case 'future_date': return '#f59e0b';
    default:           return NO_DATA_GRAY;
  }
}

// ─── Vector-tile feature state and styling ────────────────────────────────────

const SATELLITE_INDEX_CODES = ['savi', 'evi', 'ndmi', 'ndre'];

function fieldFeatureTarget(id) {
  return {
    source: FIELD_SOURCE_ID,
    sourceLayer: FIELD_SOURCE_LAYER,
    id,
  };
}

function coverageFeatureState(coverage) {
  const state = {
    coverage_status: coverage?.coverage_status || 'none',
    freshness_status: coverage?.freshness_status || 'no_data',
    coverage_has_any_data: Boolean(coverage?.has_any_data),
    coverage_latest_date: coverage?.latest_captured_date || null,
    coverage_color: getCoverageColor(coverage?.coverage_status),
    freshness_color: getFreshnessColor(coverage?.freshness_status),
  };
  SATELLITE_INDEX_CODES.forEach((code) => {
    const item = coverage?.indices?.[code];
    const value = item?.has_data ? item.latest_mean_value ?? null : null;
    state[`${code}_value`] = value;
    state[`${code}_date`] = item?.has_data ? item.latest_captured_date ?? null : null;
    state[`${code}_has_data`] = Boolean(item && (item.has_data || item.record_count > 0));
    state[`${code}_color`] = getIndexModeColor(value, code);
  });
  return state;
}

function modeColorExpression(mode) {
  if (mode === 'crop') return CROP_COLOR_EXPR;
  if (mode === 'ndvi') {
    return [
      'case',
      ['has', 'last_ndvi'],
      [
        'step',
        ['to-number', ['get', 'last_ndvi']],
        '#dc2626',
        0.15, '#f97316',
        0.3, '#eab308',
        0.45, '#84cc16',
        0.6, '#16a34a',
      ],
      NO_DATA_GRAY,
    ];
  }
  if (SATELLITE_INDEX_CODES.includes(mode)) {
    return ['coalesce', ['feature-state', `${mode}_color`], NO_DATA_GRAY];
  }
  if (mode === 'coverage') {
    return ['coalesce', ['feature-state', 'coverage_color'], NO_DATA_GRAY];
  }
  if (mode === 'freshness') {
    return ['coalesce', ['feature-state', 'freshness_color'], NO_DATA_GRAY];
  }
  return NO_DATA_GRAY;
}

function syncCoverageFeatureStates(map, coverageMap, previousIds = new Set()) {
  if (!map?.getSource(FIELD_SOURCE_ID)) return previousIds;
  const entries = Object.entries(coverageMap || {})
    .map(([rawId, value]) => [Number(rawId), value])
    .filter(([id]) => Number.isInteger(id) && id > 0);
  const nextIds = new Set(entries.map(([id]) => id));
  previousIds.forEach((id) => {
    if (nextIds.has(id)) return;
    try {
      map.removeFeatureState(fieldFeatureTarget(id));
    } catch (_) {}
  });
  entries.forEach(([id, value]) => {
    try {
      map.setFeatureState(fieldFeatureTarget(id), coverageFeatureState(value));
    } catch (_) {}
  });
  return nextIds;
}

// ─── Get hover info from feature properties ────────────────────────────────────

function formatModeValue(properties, mode) {
  const formatted = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(4) : null;
  };
  switch (mode) {
    case 'crop':
      return properties.current_crop || '—';
    case 'ndvi':
      return formatted(properties.last_ndvi);
    case 'savi':
      return formatted(properties.savi_value);
    case 'evi':
      return formatted(properties.evi_value);
    case 'ndmi':
      return formatted(properties.ndmi_value);
    case 'ndre':
      return formatted(properties.ndre_value);
    case 'coverage':
      return getCoverageLabel(properties.coverage_status);
    case 'freshness':
      return getFreshnessLabel(properties.freshness_status);
    default:
      return null;
  }
}

function getCoverageLabel(status) {
  switch (status) {
    case 'complete': return 'Полное покрытие';
    case 'partial':  return 'Частично';
    case 'none':     return 'Нет данных';
    default:         return 'Нет данных';
  }
}

function getFreshnessLabel(status) {
  switch (status) {
    case 'fresh':      return 'Актуально';
    case 'stale':      return 'Устарело';
    case 'future_date': return 'Дата из будущего';
    default:           return 'Нет данных';
  }
}

// ─── Component ──────────────────────────────────────────────────────────────────

export default function FieldMap({
  onFieldSelect,
  onDrawComplete,
  isDrawingMode,
  setIsDrawingMode,
  canDraw,
  selectedFieldId,
  enterpriseId,
  highlightedFieldId,
  onMapReady,
  coverageMap,
  coverageLoading,
  selectedMapMode,
  onMapModeChange,
}) {
  const [activeStyle, setActiveStyle] = useState('satellite');
  const [satellitePipelineStatus, setSatellitePipelineStatus] = useState('idle');
  const [activeColorMode, setActiveColorMode] = useState('crop');
  const [showLegend, setShowLegend] = useState(false);
  const [hoveredFeature, setHoveredFeature] = useState(null);
  const [hoverPosition, setHoverPosition] = useState(null);
  const [mapInstance, setMapInstance] = useState(null);
  const [rasterMetadata, setRasterMetadata] = useState(null);
  const [spatialStatus, setSpatialStatus] = useState('loading');
  const mapContainerRef = useRef(null);
  const mapRef = useRef(null);
  const drawRef = useRef(null);
  const isDrawingRef = useRef(false);
  const abortControllerRef = useRef(null);
  const isMountedRef = useRef(true);
  const sessionPurgedRef = useRef(false);
  const styleSwitchColorModeRef = useRef('crop');
  const coverageMapRef = useRef(null);
  const coverageFeatureIdsRef = useRef(new Set());
  const selectedMapModeRef = useRef('crop');
  const selectedFieldIdRef = useRef(selectedFieldId);
  const hoverFrameRef = useRef(null);
  const hoveredFeatureIdRef = useRef(null);
  const cameraMovingRef = useRef(false);
  const labelVisibilityBeforeMoveRef = useRef(null);
  const fitBoundsTimeoutRef = useRef(null);
  const activeBaseModeRef = useRef('satellite');
  const satelliteRevealTimeoutRef = useRef(null);
  const hybridLabelsFallbackTimeoutRef = useRef(null);
  const satelliteSlowStatusTimeoutRef = useRef(null);
  const satellitePipelineGenerationRef = useRef(0);
  const satelliteSourceLoadedRef = useRef(false);
  const hybridLabelsSourceLoadedRef = useRef(false);
  const satellitePipelineStatusRef = useRef('idle');

  const callbacksRef = useRef({
    onFieldSelect,
    onDrawComplete,
    setIsDrawingMode,
    onMapReady,
  });

  const handlersRef = useRef({
    onLoad: null,
    onMouseMove: null,
    onMouseLeave: null,
    onFieldClick: null,
    onDrawCreate: null,
    onMoveStart: null,
    onMoveEnd: null,
    onSourceData: null,
    onError: null,
  });

  const updateSatellitePipelineStatus = (status) => {
    if (satellitePipelineStatusRef.current === status) return;
    satellitePipelineStatusRef.current = status;
    if (isMountedRef.current) setSatellitePipelineStatus(status);
  };

  const clearPipelineTimers = () => {
    if (satelliteRevealTimeoutRef.current) clearTimeout(satelliteRevealTimeoutRef.current);
    if (hybridLabelsFallbackTimeoutRef.current) clearTimeout(hybridLabelsFallbackTimeoutRef.current);
    if (satelliteSlowStatusTimeoutRef.current) clearTimeout(satelliteSlowStatusTimeoutRef.current);
    satelliteRevealTimeoutRef.current = null;
    hybridLabelsFallbackTimeoutRef.current = null;
    satelliteSlowStatusTimeoutRef.current = null;
  };

  const cancelDeferredBasePipeline = ({ updateStatus = true } = {}) => {
    clearPipelineTimers();
    satellitePipelineGenerationRef.current += 1;
    satelliteSourceLoadedRef.current = false;
    hybridLabelsSourceLoadedRef.current = false;
    if (updateStatus) updateSatellitePipelineStatus('idle');
    return satellitePipelineGenerationRef.current;
  };

  const applyImmediateBaseMode = (map, mode) => {
    const applied = applyBaseMapVisibility(map, mode);
    if (applied) updateSatellitePipelineStatus('idle');
    return applied;
  };

  const applyNavigationFallback = (map, mode = activeBaseModeRef.current) => {
    if (!DEFERRED_SATELLITE_PIPELINE_ENABLED || mode === 'osm') {
      return applyImmediateBaseMode(map, mode);
    }
    const applied = [
      setBaseLayerVisibility(map, BASE_MAP_LAYER_IDS.osm, 'visible'),
      setBaseLayerVisibility(map, BASE_MAP_LAYER_IDS.satellite, 'none'),
      setBaseLayerVisibility(map, BASE_MAP_LAYER_IDS.hybridLabels, 'none'),
    ].every(Boolean);
    if (applied) updateSatellitePipelineStatus('navigation');
    return applied;
  };

  const completeHybridLabelsLoad = (map, generation) => {
    if (
      generation !== satellitePipelineGenerationRef.current ||
      activeBaseModeRef.current !== 'hybrid' ||
      !satelliteSourceLoadedRef.current
    ) return false;
    hybridLabelsSourceLoadedRef.current = true;
    if (hybridLabelsFallbackTimeoutRef.current) clearTimeout(hybridLabelsFallbackTimeoutRef.current);
    hybridLabelsFallbackTimeoutRef.current = null;
    updateSatellitePipelineStatus('idle');
    return setBaseLayerVisibility(map, BASE_MAP_LAYER_IDS.hybridLabels, 'visible');
  };

  const completeSatelliteLoad = (map, generation) => {
    if (
      generation !== satellitePipelineGenerationRef.current ||
      !['satellite', 'hybrid'].includes(activeBaseModeRef.current) ||
      !satelliteRevealTimeoutRef.current && satellitePipelineStatusRef.current !== 'loading-satellite' && satellitePipelineStatusRef.current !== 'slow'
    ) return false;
    satelliteSourceLoadedRef.current = true;
    if (satelliteSlowStatusTimeoutRef.current) clearTimeout(satelliteSlowStatusTimeoutRef.current);
    satelliteSlowStatusTimeoutRef.current = null;
    setBaseLayerVisibility(map, BASE_MAP_LAYER_IDS.osm, 'none');
    setBaseLayerVisibility(map, BASE_MAP_LAYER_IDS.satellite, 'visible');
    if (activeBaseModeRef.current === 'hybrid') {
      setBaseLayerVisibility(map, BASE_MAP_LAYER_IDS.hybridLabels, 'visible');
      updateSatellitePipelineStatus(
        hybridLabelsSourceLoadedRef.current ? 'idle' : 'loading-labels'
      );
    } else {
      setBaseLayerVisibility(map, BASE_MAP_LAYER_IDS.hybridLabels, 'none');
      updateSatellitePipelineStatus('idle');
    }
    return true;
  };

  const beginDeferredSatelliteReveal = (map, mode = activeBaseModeRef.current) => {
    if (!DEFERRED_SATELLITE_PIPELINE_ENABLED || mode === 'osm') {
      return applyImmediateBaseMode(map, mode);
    }
    clearPipelineTimers();
    satelliteSourceLoadedRef.current = false;
    hybridLabelsSourceLoadedRef.current = false;
    const generation = ++satellitePipelineGenerationRef.current;
    applyNavigationFallback(map, mode);
    satelliteRevealTimeoutRef.current = setTimeout(() => {
      satelliteRevealTimeoutRef.current = null;
      if (generation !== satellitePipelineGenerationRef.current || activeBaseModeRef.current !== mode) return;
      setBaseLayerVisibility(map, BASE_MAP_LAYER_IDS.osm, 'visible');
      setBaseLayerVisibility(map, BASE_MAP_LAYER_IDS.satellite, 'visible');
      setBaseLayerVisibility(map, BASE_MAP_LAYER_IDS.hybridLabels, 'none');
      updateSatellitePipelineStatus('loading-satellite');
      satelliteSlowStatusTimeoutRef.current = setTimeout(() => {
        satelliteSlowStatusTimeoutRef.current = null;
        if (generation === satellitePipelineGenerationRef.current && !satelliteSourceLoadedRef.current) {
          updateSatellitePipelineStatus('slow');
        }
      }, SATELLITE_SLOW_STATUS_MS);
      if (mode === 'hybrid') {
        hybridLabelsFallbackTimeoutRef.current = setTimeout(() => {
          hybridLabelsFallbackTimeoutRef.current = null;
          if (generation !== satellitePipelineGenerationRef.current || activeBaseModeRef.current !== 'hybrid') return;
          setBaseLayerVisibility(map, BASE_MAP_LAYER_IDS.hybridLabels, 'visible');
        }, HYBRID_LABELS_FALLBACK_MS);
      }
    }, SATELLITE_REVEAL_DELAY_MS);
    return true;
  };

  const clearHoverState = (map, clearTooltip = true) => {
    const hoveredId = hoveredFeatureIdRef.current;
    if (hoveredId !== null && map?.getSource(FIELD_SOURCE_ID)) {
      try {
        map.setFeatureState(fieldFeatureTarget(hoveredId), { agrosatHover: false });
      } catch (_) {}
    }
    hoveredFeatureIdRef.current = null;
    if (clearTooltip && isMountedRef.current) {
      setHoveredFeature(null);
      setHoverPosition(null);
    }
  };

  const suspendLabelsForMove = (map) => {
    if (map?.getLayer('fields-label')) {
      try {
        labelVisibilityBeforeMoveRef.current = map.getLayoutProperty('fields-label', 'visibility') || 'visible';
        map.setLayoutProperty('fields-label', 'visibility', 'none');
      } catch (_) { labelVisibilityBeforeMoveRef.current = null; }
    }
  };

  const restoreLabelsAfterMove = (map) => {
    const labelVisibility = labelVisibilityBeforeMoveRef.current;
    labelVisibilityBeforeMoveRef.current = null;
    if (labelVisibility !== null && map?.getLayer('fields-label')) {
      try { map.setLayoutProperty('fields-label', 'visibility', labelVisibility); } catch (_) {}
    }
  };

  useEffect(() => {
    callbacksRef.current = {
      onFieldSelect,
      onDrawComplete,
      setIsDrawingMode,
      onMapReady,
    };
  }, [onFieldSelect, onDrawComplete, setIsDrawingMode, onMapReady]);

  useEffect(() => {
    isDrawingRef.current = isDrawingMode;
  }, [isDrawingMode]);

  // Track coverageMap in ref
  useEffect(() => {
    coverageMapRef.current = coverageMap;
  }, [coverageMap]);

  // Track selectedMapMode in ref
  useEffect(() => {
    selectedMapModeRef.current = selectedMapMode || 'crop';
  }, [selectedMapMode]);

  useEffect(() => {
    selectedFieldIdRef.current = selectedFieldId;
  }, [selectedFieldId]);

  // ─── Disable draw mode ──────────────────────────────────────────────────────
  const disableDrawMode = () => {
    const m = mapRef.current;
    if (!m) return;

    if (handlersRef.current.onDrawCreate) {
      m.off('draw.create', handlersRef.current.onDrawCreate);
      handlersRef.current.onDrawCreate = null;
    }

    if (drawRef.current) {
      drawRef.current.deleteAll();
      m.removeControl(drawRef.current);
      drawRef.current = null;
    }

    isDrawingRef.current = false;
    m.getCanvas().style.cursor = '';
  };

  // ─── Enable draw mode ────────────────────────────────────────────────────────
  const enableDrawMode = () => {
    const m = mapRef.current;
    if (!m || !m.isStyleLoaded()) {
      console.error('Map style is not ready for drawing mode');
      callbacksRef.current.setIsDrawingMode?.(false);
      return;
    }

    if (!MapboxDraw.constants?.classes) {
      console.error('MapboxDraw compatibility check failed');
      callbacksRef.current.setIsDrawingMode?.(false);
      return;
    }

    MapboxDraw.constants.classes.CANVAS = 'maplibregl-canvas';
    MapboxDraw.constants.classes.CONTROL_BASE = 'maplibregl-ctrl';
    MapboxDraw.constants.classes.CONTROL_PREFIX = 'maplibregl-ctrl-';
    MapboxDraw.constants.classes.CONTROL_GROUP = 'maplibregl-ctrl-group';
    MapboxDraw.constants.classes.ATTRIBUTION = 'maplibregl-ctrl-attrib';

    const draw = new MapboxDraw({
      displayControlsDefault: false,
      controls: {
        polygon: true,
        trash: true,
      },
      defaultMode: 'draw_polygon',
    });

    drawRef.current = draw;
    m.addControl(draw);

    handlersRef.current.onDrawCreate = (e) => {
      const feature = e.features?.[0];
      if (feature?.geometry?.type !== 'Polygon') return;

      callbacksRef.current.onDrawComplete?.(feature.geometry);
      callbacksRef.current.setIsDrawingMode?.(false);
    };

    m.on('draw.create', handlersRef.current.onDrawCreate);
  };

  // ─── Draw mode effect ────────────────────────────────────────────────────────
  useEffect(() => {
    if (isDrawingMode && canDraw) {
      enableDrawMode();
    } else {
      disableDrawMode();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDrawingMode, canDraw]);

  // ─── Logout handler ──────────────────────────────────────────────────────────
  const handleLogout = () => {
    sessionPurgedRef.current = true;

    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }

    disableDrawMode();

    callbacksRef.current.setIsDrawingMode?.(false);
    callbacksRef.current.onFieldSelect?.(null);

    const m = mapRef.current;
    if (m) {
      try {
        LAYERS.forEach((layer) => {
          if (layer === 'fields-fill' && handlersRef.current.onMouseMove) {
            m.off('mousemove', layer, handlersRef.current.onMouseMove);
          }
          if (layer === 'fields-fill' && handlersRef.current.onMouseLeave) {
            m.off('mouseleave', layer, handlersRef.current.onMouseLeave);
          }
          if (handlersRef.current.onFieldClick) {
            m.off('click', layer, handlersRef.current.onFieldClick);
          }
        });
        handlersRef.current.onMouseMove = null;
        handlersRef.current.onMouseLeave = null;
        handlersRef.current.onFieldClick = null;
        FIELD_LAYER_IDS.forEach((layerId) => {
          if (m.getLayer(layerId)) m.removeLayer(layerId);
        });
        if (m.getSource(FIELD_SOURCE_ID)) m.removeSource(FIELD_SOURCE_ID);
        coverageFeatureIdsRef.current = new Set();
      } catch (_) {}
    }
  };

  // ─── Map initialization ──────────────────────────────────────────────────────
  useEffect(() => {
    if (mapRef.current) return;
    isMountedRef.current = true;
    sessionPurgedRef.current = false;

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: COMPOSITE_MAP_STYLE,
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      minZoom: MAP_MIN_ZOOM,
      maxZoom: MAP_MAX_ZOOM,
      cancelPendingTileRequestsWhileZooming: true,
      refreshExpiredTiles: false,
      renderWorldCopies: false,
      fadeDuration: 0,
      pixelRatio: getMapPixelRatio(),
      validateStyle: import.meta.env.PROD ? false : true,
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      transformRequest: transformMapRequest,
    });
    configureSupportedMapInteractions(map);
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl(), 'top-right');

    handlersRef.current.onSourceData = (event) => {
      if (!DEFERRED_SATELLITE_PIPELINE_ENABLED || mapRef.current !== map || !isMountedRef.current) return;
      const generation = satellitePipelineGenerationRef.current;
      const mode = activeBaseModeRef.current;
      if (event.isSourceLoaded !== true) return;
      if (event.sourceId === BASE_MAP_SOURCE_IDS.satellite && ['satellite', 'hybrid'].includes(mode)) {
        completeSatelliteLoad(map, generation);
      } else if (event.sourceId === BASE_MAP_SOURCE_IDS.hybridLabels && mode === 'hybrid') {
        completeHybridLabelsLoad(map, generation);
      }
    };
    map.on('sourcedata', handlersRef.current.onSourceData);
    handlersRef.current.onError = (event) => {
      if (
        event?.sourceId === FIELD_SOURCE_ID
        && mapRef.current === map
        && isMountedRef.current
      ) setSpatialStatus('error');
    };
    map.on('error', handlersRef.current.onError);

    handlersRef.current.onLoad = async () => {
      sessionPurgedRef.current = false;

      const m = mapRef.current;
      if (!m || !isMountedRef.current) return;
      setMapInstance(m);
      beginDeferredSatelliteReveal(m, activeBaseModeRef.current);

      abortControllerRef.current = new AbortController();

      try {
        const metadata = await getFieldTileMetadata({
          enterpriseId,
          signal: abortControllerRef.current.signal,
        });

        if (
          sessionPurgedRef.current ||
          !isMountedRef.current ||
          mapRef.current !== m
        ) {
          return;
        }
        if (!validFieldTileMetadata(metadata)) {
          throw new Error('Invalid field tile metadata');
        }
        m.addSource(FIELD_SOURCE_ID, {
          type: 'vector',
          tiles: [resolveFieldTileTemplate(metadata)],
          minzoom: metadata.min_zoom,
          maxzoom: metadata.max_zoom,
          promoteId: 'id',
        });
        if (!m.getLayer('fields-fill')) {
          m.addLayer({
            id: 'fields-fill',
            type: 'fill',
            source: FIELD_SOURCE_ID,
            'source-layer': FIELD_SOURCE_LAYER,
            paint: {
              'fill-color': modeColorExpression(selectedMapModeRef.current),
              'fill-opacity': 0.4,
            },
          });
        }

        if (!m.getLayer('fields-border')) {
          m.addLayer({
            id: 'fields-border',
            type: 'line',
            source: FIELD_SOURCE_ID,
            'source-layer': FIELD_SOURCE_LAYER,
            paint: {
              'line-color': '#ffffff',
              'line-width': [
                'case',
                ['==', ['get', 'id'], selectedFieldIdRef.current || -1], 3,
                ['boolean', ['feature-state', 'agrosatHover'], false], 3,
                2,
              ],
              'line-opacity': 1,
            },
          });
        }

        if (!m.getLayer('fields-label')) {
          m.addLayer({
            id: 'fields-label',
            type: 'symbol',
            source: FIELD_SOURCE_ID,
            'source-layer': FIELD_SOURCE_LAYER,
            minzoom: 12,
            layout: {
              'text-field': ['to-string', ['coalesce', ['get', 'name'], ['get', 'code'], ['get', 'id']]],
              'text-font': ['Noto Sans Regular'],
              'text-size': ['interpolate', ['linear'], ['zoom'], 12, 10, 16, 14, 22, 18],
              'text-offset': [0, -0.5],
              'text-anchor': 'center',
            },
            paint: {
              'text-color': '#ffffff',
              'text-halo-color': '#000000',
              'text-halo-width': 1.5,
              'text-halo-blur': 1,
            },
          });
        }
        coverageFeatureIdsRef.current = syncCoverageFeatureStates(
          m,
          coverageMapRef.current,
          coverageFeatureIdsRef.current,
        );
        if (mapContainerRef.current) {
          mapContainerRef.current.dataset.fieldSourceType = 'vector';
          mapContainerRef.current.dataset.fieldLayerCount = String(
            FIELD_LAYER_IDS.filter((layerId) => m.getLayer(layerId)).length,
          );
        }
        if (metadata.bounds) {
          if (fitBoundsTimeoutRef.current) clearTimeout(fitBoundsTimeoutRef.current);
          fitBoundsTimeoutRef.current = setTimeout(() => {
            fitBoundsTimeoutRef.current = null;
            if (mapRef.current !== m || !isMountedRef.current || m._removed) return;
            try {
              m.fitBounds(metadata.bounds, { padding: 60, duration: 0, maxZoom: 15 });
            } catch (_) {}
          }, 100);
        }
        setSpatialStatus(metadata.field_count === 0 ? 'empty' : 'ready');

        // Interaction handlers
        LAYERS.forEach((layer) => {
          if (m.getLayer(layer)) {
            if (layer === 'fields-fill' && handlersRef.current.onMouseMove) {
              m.off('mousemove', layer, handlersRef.current.onMouseMove);
            }
            if (layer === 'fields-fill' && handlersRef.current.onMouseLeave) {
              m.off('mouseleave', layer, handlersRef.current.onMouseLeave);
            }
            if (handlersRef.current.onFieldClick) {
              m.off('click', layer, handlersRef.current.onFieldClick);
            }
          }
        });

        handlersRef.current.onMouseMove = (e) => {
          if (isDrawingRef.current || cameraMovingRef.current || !e.features?.length) return;
          const feat = e.features[0];
          const featureId = Number(feat.id ?? feat.properties?.id);
          const props = feat.properties || {};
          const point = { x: e.point.x, y: e.point.y };
          if (hoverFrameRef.current) cancelAnimationFrame(hoverFrameRef.current);
          hoverFrameRef.current = requestAnimationFrame(() => {
            hoverFrameRef.current = null;
            if (cameraMovingRef.current || !isMountedRef.current || mapRef.current !== m) return;
            if (!m.getSource(FIELD_SOURCE_ID) || hoveredFeatureIdRef.current === featureId) return;
            clearHoverState(m, false);
            try {
              m.setFeatureState(fieldFeatureTarget(featureId), { agrosatHover: true });
            } catch (_) { return; }
            hoveredFeatureIdRef.current = featureId;
            m.getCanvas().style.cursor = 'pointer';
            setHoveredFeature({ ...props, ...(feat.state || {}) });
            setHoverPosition(point);
          });
        };

        handlersRef.current.onMouseLeave = () => {
          if (isDrawingRef.current) return;
          m.getCanvas().style.cursor = '';
          if (hoverFrameRef.current) cancelAnimationFrame(hoverFrameRef.current);
          hoverFrameRef.current = null;
          clearHoverState(m);
        };

        handlersRef.current.onFieldClick = (e) => {
          if (isDrawingRef.current) return;
          const featureId = Number(e.features?.[0]?.properties?.id);
          if (Number.isInteger(featureId) && featureId > 0) {
            callbacksRef.current.onFieldSelect?.(featureId);
          }
        };

        LAYERS.forEach((layer) => {
          if (m.getLayer(layer)) {
            if (layer === 'fields-fill') {
              m.on('mousemove', layer, handlersRef.current.onMouseMove);
              m.on('mouseleave', layer, handlersRef.current.onMouseLeave);
            }
            m.on('click', layer, handlersRef.current.onFieldClick);
          }
        });

        handlersRef.current.onMoveStart = () => {
          if (cameraMovingRef.current) return;
          cameraMovingRef.current = true;
          if (hoverFrameRef.current) cancelAnimationFrame(hoverFrameRef.current);
          hoverFrameRef.current = null;
          clearHoverState(m);
          m.getCanvas().style.cursor = '';
          suspendLabelsForMove(m);
          if (['satellite', 'hybrid'].includes(activeBaseModeRef.current)) {
            cancelDeferredBasePipeline({ updateStatus: false });
            applyNavigationFallback(m, activeBaseModeRef.current);
          }
        };
        handlersRef.current.onMoveEnd = () => {
          cameraMovingRef.current = false;
          restoreLabelsAfterMove(m);
          if (['satellite', 'hybrid'].includes(activeBaseModeRef.current)) {
            beginDeferredSatelliteReveal(m, activeBaseModeRef.current);
          }
        };
        m.on('movestart', handlersRef.current.onMoveStart);
        m.on('moveend', handlersRef.current.onMoveEnd);

        callbacksRef.current.onMapReady?.(m);
      } catch (err) {
        if (
          err?.name !== 'CanceledError'
          && err?.name !== 'AbortError'
          && err?.code !== 'ERR_CANCELED'
        ) {
          if (isMountedRef.current) setSpatialStatus('error');
          console.error('Failed to load spatial layers');
        }
      }
    };

    window.addEventListener('agrosat:logout', handleLogout);

    map.on('style.load', handlersRef.current.onLoad);

    return () => {
      isMountedRef.current = false;
      sessionPurgedRef.current = true;
      cancelDeferredBasePipeline({ updateStatus: false });
      satellitePipelineStatusRef.current = 'idle';
      window.removeEventListener('agrosat:logout', handleLogout);
      if (handlersRef.current.onLoad) {
        map.off('style.load', handlersRef.current.onLoad);
        handlersRef.current.onLoad = null;
      }
      if (hoverFrameRef.current) cancelAnimationFrame(hoverFrameRef.current);
      hoverFrameRef.current = null;
      if (fitBoundsTimeoutRef.current) clearTimeout(fitBoundsTimeoutRef.current);
      fitBoundsTimeoutRef.current = null;

      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }

      disableDrawMode();

      if (mapRef.current) {
        if (handlersRef.current.onSourceData) {
          mapRef.current.off('sourcedata', handlersRef.current.onSourceData);
          handlersRef.current.onSourceData = null;
        }
        if (handlersRef.current.onError) {
          mapRef.current.off('error', handlersRef.current.onError);
          handlersRef.current.onError = null;
        }
        if (handlersRef.current.onMoveStart) {
          mapRef.current.off('movestart', handlersRef.current.onMoveStart);
        }
        if (handlersRef.current.onMoveEnd) {
          mapRef.current.off('moveend', handlersRef.current.onMoveEnd);
        }
        clearHoverState(mapRef.current, false);
        cameraMovingRef.current = false;
        labelVisibilityBeforeMoveRef.current = null;
        LAYERS.forEach((layer) => {
          try {
            if (layer === 'fields-fill' && handlersRef.current.onMouseMove) {
              mapRef.current.off('mousemove', layer, handlersRef.current.onMouseMove);
            }
            if (layer === 'fields-fill' && handlersRef.current.onMouseLeave) {
              mapRef.current.off('mouseleave', layer, handlersRef.current.onMouseLeave);
            }
            if (handlersRef.current.onFieldClick) {
              mapRef.current.off('click', layer, handlersRef.current.onFieldClick);
            }
          } catch (_) {}
        });
        handlersRef.current.onMouseMove = null;
        handlersRef.current.onMouseLeave = null;
        handlersRef.current.onFieldClick = null;
        mapRef.current.remove();
        mapRef.current = null;
        coverageFeatureIdsRef.current = new Set();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ─── Selected / highlight filter updates ────────────────────────────────────
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !m.isStyleLoaded()) return;
    try {
      if (m.getLayer('fields-border')) {
        m.setPaintProperty('fields-border', 'line-width', [
          'case',
          ['==', ['get', 'id'], selectedFieldId || -1], 3,
          ['boolean', ['feature-state', 'agrosatHover'], false], 3,
          2,
        ]);
        m.setPaintProperty('fields-border', 'line-opacity', [
          'case',
          ['==', ['get', 'id'], selectedFieldId || -1],
          1,
          1,
        ]);
      }
    } catch (_) {}
  }, [selectedFieldId]);

  // ─── Style switch ──────────────────────────────────────────────────────────
  const switchMapStyle = (styleKey) => {
    const m = mapRef.current;
    if (!m) return;
    if (styleKey === activeBaseModeRef.current) {
      if (satellitePipelineStatusRef.current === 'slow') beginDeferredSatelliteReveal(m, styleKey);
      return;
    }
    cancelDeferredBasePipeline({ updateStatus: false });
    activeBaseModeRef.current = styleKey;
    let applied = false;
    if (!DEFERRED_SATELLITE_PIPELINE_ENABLED || styleKey === 'osm') {
      applied = applyImmediateBaseMode(m, styleKey);
    } else {
      applied = applyNavigationFallback(m, styleKey);
      if (applied && !cameraMovingRef.current) beginDeferredSatelliteReveal(m, styleKey);
    }
    if (applied) setActiveStyle(styleKey);
  };

  // ─── Switch color mode ─────────────────────────────────────────────────────
  const switchColorMode = (mode) => {
    setActiveColorMode(mode);
    styleSwitchColorModeRef.current = mode;
    if (onMapModeChange) onMapModeChange(mode);

    const m = mapRef.current;
    if (!m || !m.isStyleLoaded()) return;
    try {
      if (m.getLayer('fields-fill')) {
        m.setPaintProperty('fields-fill', 'fill-color', modeColorExpression(mode));
      }
    } catch (_) {}
  };

  // ─── Respond to coverageMap changes without replacing vector geometry ──────
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded() || !map.getSource(FIELD_SOURCE_ID)) return;
    coverageFeatureIdsRef.current = syncCoverageFeatureStates(
      map,
      coverageMap,
      coverageFeatureIdsRef.current,
    );
  }, [coverageMap]);

  // ─── Respond to selectedMapMode changes: recolor ───────────────────────────
  useEffect(() => {
    if (!mapRef.current || !mapRef.current.isStyleLoaded()) return;
    const mode = selectedMapMode || 'crop';
    setActiveColorMode(mode);
    styleSwitchColorModeRef.current = mode;

    try {
      if (mapRef.current.getLayer('fields-fill')) {
        mapRef.current.setPaintProperty('fields-fill', 'fill-color', modeColorExpression(mode));
        mapRef.current.setPaintProperty('fields-fill', 'fill-opacity', 0.4);
      }
    } catch (_) {}
  }, [selectedMapMode]);

  return (
    <div className="relative w-full h-full">
      <div
        ref={mapContainerRef}
        className="w-full h-full"
        role="region"
        aria-label="Интерактивная карта полей"
        data-spatial-status={spatialStatus}
      />

      {spatialStatus !== 'ready' && (
        <div
          className="pointer-events-none absolute bottom-3 left-1/2 z-10 max-w-[calc(100%-1.5rem)] -translate-x-1/2 rounded-md border border-slate-200 bg-white/95 px-3 py-2 text-center text-xs font-medium text-slate-700 shadow"
          role={spatialStatus === 'error' ? 'alert' : 'status'}
          aria-live="polite"
        >
          {spatialStatus === 'loading' && 'Загрузка полей в текущей области…'}
          {spatialStatus === 'empty' && 'В выбранной области нет доступных полей.'}
          {spatialStatus === 'error' && 'Не удалось загрузить пространственный слой полей.'}
        </div>
      )}

      <NDVIRasterControl
        map={mapInstance}
        fieldId={selectedFieldId}
        onMetadataChange={setRasterMetadata}
      />

      {/* Style switcher */}
      <div className="absolute top-3 right-3 z-10 flex gap-1 max-sm:left-3 max-sm:right-auto max-sm:top-[60px]">
        {Object.entries(MAP_STYLES).map(([key, s]) => (
          <button
            type="button"
            key={key}
            onClick={() => switchMapStyle(key)}
            aria-pressed={activeStyle === key}
            className={`px-2.5 py-1 text-xs rounded-md font-medium transition-colors shadow border ${activeStyle === key ? 'bg-blue-600 text-white border-blue-600' : 'bg-white/90 backdrop-blur-sm text-slate-600 hover:text-slate-900 border-slate-200'}`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* Map mode selector — compact row */}
      {activeStyle !== 'osm' && satellitePipelineStatus !== 'idle' && (
        <div className="pointer-events-none absolute top-12 left-3 z-10 max-w-[min(320px,calc(100%-24px))] rounded-md border border-slate-200 bg-white/90 px-3 py-1.5 text-[13px] font-medium text-slate-700 shadow backdrop-blur-sm max-sm:bottom-20 max-sm:top-auto">
          {{
            navigation: 'Навигация по карте · спутник после остановки',
            'loading-satellite': 'Загрузка спутникового слоя…',
            'loading-labels': 'Загрузка подписей…',
            slow: 'Спутниковый слой загружается медленно',
          }[satellitePipelineStatus]}
        </div>
      )}

      <div className="absolute top-12 right-3 z-10 flex max-w-[260px] flex-wrap justify-end gap-1 max-sm:left-3 max-sm:right-3 max-sm:top-[100px] max-sm:max-w-none max-sm:flex-nowrap max-sm:justify-start max-sm:overflow-x-auto max-sm:pb-1">
        {MAP_MODES.map(mode => (
          <button
            type="button"
            key={mode.code}
            onClick={() => switchColorMode(mode.code)}
            aria-pressed={activeColorMode === mode.code}
            className={`flex-shrink-0 px-2 py-1 text-xs rounded-md font-medium transition-colors shadow border ${
              activeColorMode === mode.code
                ? 'bg-blue-600 text-white border-blue-600'
                : 'bg-white/90 backdrop-blur-sm text-slate-600 hover:text-slate-900 border-slate-200'
            }`}
          >
            {mode.label}
          </button>
        ))}
      </div>

      {/* Legend toggle */}
      <div className="absolute bottom-20 right-3 z-10 flex flex-col items-end gap-2">
        <button
          type="button"
          onClick={() => setShowLegend(!showLegend)}
          aria-expanded={showLegend}
          className={`px-2.5 py-1.5 text-xs rounded-lg font-medium shadow border transition-colors ${
            showLegend
              ? 'bg-blue-600 text-white border-blue-600'
              : 'bg-white/90 backdrop-blur-sm text-slate-600 hover:text-slate-900 border-slate-200'
          }`}
          title="Легенда карты"
        >
          {showLegend ? '✕ Легенда' : '✓ Легенда'}
        </button>

        {showLegend && (
          <MapLegend
            activeColorMode={activeColorMode}
            rasterMetadata={rasterMetadata}
            onClose={() => setShowLegend(false)}
          />
        )}
      </div>

      {/* Hover popup */}
      {hoveredFeature && hoverPosition && !isDrawingMode && (
        <MapHoverPopup
          feature={hoveredFeature}
          position={hoverPosition}
          mapMode={activeColorMode}
          onClose={() => { setHoveredFeature(null); setHoverPosition(null); }}
        />
      )}

      {/* Re-center button */}
      <button
        type="button"
        onClick={() => mapRef.current?.flyTo({ center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, duration: 1000 })}
        className="absolute bottom-8 right-3 z-10 bg-white/90 backdrop-blur-sm hover:bg-slate-100 rounded-lg p-2 shadow-lg transition-colors border border-slate-200"
        title="Сбросить вид"
      >
        <svg className="w-5 h-5 text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      </button>
    </div>
  );
}
