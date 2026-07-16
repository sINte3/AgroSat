import React, { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';
import apiClient from '../../api/client';
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

const COMPOSITE_MAP_STYLE = {
  version: 8,
  glyphs: GLYPHS,
  sources: {
    [BASE_MAP_SOURCE_IDS.satellite]: ESRI_SATELLITE_SOURCE,
    [BASE_MAP_SOURCE_IDS.osm]: OSM_SOURCE,
    [BASE_MAP_SOURCE_IDS.hybridLabels]: ESRI_LABELS_SOURCE,
  },
  layers: [
    { id: BASE_MAP_LAYER_IDS.satellite, type: 'raster', source: BASE_MAP_SOURCE_IDS.satellite, layout: { visibility: 'visible' }, paint: { 'raster-fade-duration': 0 } },
    { id: BASE_MAP_LAYER_IDS.osm, type: 'raster', source: BASE_MAP_SOURCE_IDS.osm, layout: { visibility: 'none' }, paint: { 'raster-fade-duration': 0 } },
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

const DEFAULT_CENTER = [64.4286, 39.7747];
const DEFAULT_ZOOM = 7;
const MAP_MIN_ZOOM = 3;
const MAP_MAX_ZOOM = 18;
const LAYERS = ['fields-fill', 'fields-label'];

const NO_DATA_GRAY = '#4b5563';
const NO_DATA_GRAY_HEX = '#6B7280';

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

// ─── Helper: enrich feature properties with coverage data ──────────────────────

const SATELLITE_INDEX_CODES = ['savi', 'evi', 'ndmi', 'ndre'];

function enrichGeoJsonFeatures(geojson, coverageMap) {
  if (!geojson?.features) return geojson;
  return {
    ...geojson,
    features: geojson.features.map(f => {
      const props = f.properties || {};
      const fieldId = props.id;
      const cov = coverageMap?.[fieldId];
      const enriched = { ...props };

      if (cov) {
        enriched.coverage_status = cov.coverage_status || 'none';
        enriched.freshness_status = cov.freshness_status || 'no_data';
        enriched.coverage_has_any_data = cov.has_any_data || false;
        enriched.coverage_latest_date = cov.latest_captured_date || null;

        // Per-index values
        SATELLITE_INDEX_CODES.forEach(code => {
          const idx = cov.indices?.[code];
          if (idx && idx.has_data) {
            enriched[`${code}_value`] = idx.latest_mean_value ?? null;
            enriched[`${code}_date`] = idx.latest_captured_date ?? null;
          } else {
            enriched[`${code}_value`] = null;
            enriched[`${code}_date`] = null;
          }
          enriched[`${code}_has_data`] = !!(idx && (idx.has_data || idx.record_count > 0));
        });
      } else {
        // No coverage data available
        enriched.coverage_status = 'none';
        enriched.freshness_status = 'no_data';
        enriched.coverage_has_any_data = false;
        enriched.coverage_latest_date = null;
        SATELLITE_INDEX_CODES.forEach(code => {
          enriched[`${code}_value`] = null;
          enriched[`${code}_date`] = null;
          enriched[`${code}_has_data`] = false;
        });
      }

      // Compute color for current mode (will be recomputed on mode change)
      return { ...f, properties: enriched };
    }),
  };
}

function computeModeColor(properties, mode) {
  if (!properties) return NO_DATA_GRAY;

  switch (mode) {
    case 'crop':
      return getCropColorFromProps(properties);
    case 'ndvi':
      return getIndexModeColor(properties.last_ndvi ?? properties.ndvi_value ?? null, 'ndvi');
    case 'savi':
      return getIndexModeColor(properties.savi_value ?? null, 'savi');
    case 'evi':
      return getIndexModeColor(properties.evi_value ?? null, 'evi');
    case 'ndmi':
      return getIndexModeColor(properties.ndmi_value ?? null, 'ndmi');
    case 'ndre':
      return getIndexModeColor(properties.ndre_value ?? null, 'ndre');
    case 'coverage':
      return getCoverageColor(properties.coverage_status);
    case 'freshness':
      return getFreshnessColor(properties.freshness_status);
    default:
      return NO_DATA_GRAY;
  }
}

function getCropColorFromProps(props) {
  const crop = props.current_crop;
  const irr = props.irrigation_type;
  if (crop === 'Пшеница озимая') return CROP_COLORS.wheat;
  if (crop === 'Хлопок' && irr === 'drip') return CROP_COLORS.cotton_drip;
  if (crop === 'Хлопок' && irr === 'canal') return CROP_COLORS.cotton_canal;
  if (crop === 'Люцерна') return CROP_COLORS.alfalfa;
  if (crop === 'Рис') return CROP_COLORS.rice;
  if (crop === 'Кукуруза') return CROP_COLORS.maize;
  return CROP_COLORS.default;
}

function addModeColorToFeatures(geojson, mode) {
  if (!geojson?.features) return geojson;
  return {
    ...geojson,
    features: geojson.features.map(f => {
      const props = f.properties || {};
      return {
        ...f,
        id: Number(props.id),
        properties: {
          ...props,
          map_mode_color: computeModeColor(props, mode),
          map_mode_value: props.last_ndvi ?? props.savi_value ?? props.evi_value ?? props.ndmi_value ?? props.ndre_value ?? null,
          map_mode_label: getModeLabel(mode),
        },
      };
    }),
  };
}

function getModeLabel(mode) {
  const m = MAP_MODES.find(mm => mm.code === mode);
  return m ? m.label : mode;
}

// ─── Get hover info from feature properties ────────────────────────────────────

function formatModeValue(properties, mode) {
  switch (mode) {
    case 'crop':
      return properties.current_crop || '—';
    case 'ndvi':
      if (properties.last_ndvi != null) return properties.last_ndvi.toFixed(4);
      return null;
    case 'savi':
      return properties.savi_value != null ? properties.savi_value.toFixed(4) : null;
    case 'evi':
      return properties.evi_value != null ? properties.evi_value.toFixed(4) : null;
    case 'ndmi':
      return properties.ndmi_value != null ? properties.ndmi_value.toFixed(4) : null;
    case 'ndre':
      return properties.ndre_value != null ? properties.ndre_value.toFixed(4) : null;
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
  const [activeColorMode, setActiveColorMode] = useState('crop');
  const [showLegend, setShowLegend] = useState(false);
  const [hoveredFeature, setHoveredFeature] = useState(null);
  const [hoverPosition, setHoverPosition] = useState(null);
  const [mapInstance, setMapInstance] = useState(null);
  const [rasterMetadata, setRasterMetadata] = useState(null);
  const mapContainerRef = useRef(null);
  const mapRef = useRef(null);
  const drawRef = useRef(null);
  const isDrawingRef = useRef(false);
  const abortControllerRef = useRef(null);
  const isMountedRef = useRef(true);
  const sessionPurgedRef = useRef(false);
  const dataLoadedRef = useRef(false);
  const geojsonRef = useRef(null);
  const enrichedGeoJsonRef = useRef(null);
  const styleSwitchColorModeRef = useRef('crop');
  const coverageMapRef = useRef(null);
  const selectedMapModeRef = useRef('crop');
  const selectedFieldIdRef = useRef(selectedFieldId);
  const hoverFrameRef = useRef(null);
  const hoveredFeatureIdRef = useRef(null);
  const cameraMovingRef = useRef(false);
  const labelVisibilityBeforeMoveRef = useRef(null);
  const fieldFillVisibilityBeforeMoveRef = useRef(null);
  const fieldBorderWidthBeforeMoveRef = useRef(null);
  const fieldBorderOpacityBeforeMoveRef = useRef(null);
  const fitBoundsTimeoutRef = useRef(null);

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
  });

  const clearHoverState = (map, clearTooltip = true) => {
    const hoveredId = hoveredFeatureIdRef.current;
    if (hoveredId !== null && map?.getSource('fields-source')) {
      try {
        map.setFeatureState(
          { source: 'fields-source', id: hoveredId },
          { agrosatHover: false }
        );
      } catch (_) {}
    }
    hoveredFeatureIdRef.current = null;
    if (clearTooltip && isMountedRef.current) {
      setHoveredFeature(null);
      setHoverPosition(null);
    }
  };

  const suspendFieldRenderingForMove = (map) => {
    if (map?.getLayer('fields-fill')) {
      try {
        fieldFillVisibilityBeforeMoveRef.current = map.getLayoutProperty('fields-fill', 'visibility') || 'visible';
        map.setLayoutProperty('fields-fill', 'visibility', 'none');
      } catch (_) { fieldFillVisibilityBeforeMoveRef.current = null; }
    }
    if (map?.getLayer('fields-label')) {
      try {
        labelVisibilityBeforeMoveRef.current = map.getLayoutProperty('fields-label', 'visibility') || 'visible';
        map.setLayoutProperty('fields-label', 'visibility', 'none');
      } catch (_) { labelVisibilityBeforeMoveRef.current = null; }
    }
    if (map?.getLayer('fields-border')) {
      try {
        fieldBorderWidthBeforeMoveRef.current = map.getPaintProperty('fields-border', 'line-width');
        fieldBorderOpacityBeforeMoveRef.current = map.getPaintProperty('fields-border', 'line-opacity');
        map.setPaintProperty('fields-border', 'line-width', 1);
        map.setPaintProperty('fields-border', 'line-opacity', 0.75);
      } catch (_) {
        fieldBorderWidthBeforeMoveRef.current = null;
        fieldBorderOpacityBeforeMoveRef.current = null;
      }
    }
  };

  const restoreFieldRenderingAfterMove = (map) => {
    const fillVisibility = fieldFillVisibilityBeforeMoveRef.current;
    const labelVisibility = labelVisibilityBeforeMoveRef.current;
    const borderWidth = fieldBorderWidthBeforeMoveRef.current;
    const borderOpacity = fieldBorderOpacityBeforeMoveRef.current;
    fieldFillVisibilityBeforeMoveRef.current = null;
    labelVisibilityBeforeMoveRef.current = null;
    fieldBorderWidthBeforeMoveRef.current = null;
    fieldBorderOpacityBeforeMoveRef.current = null;
    if (fillVisibility !== null && map?.getLayer('fields-fill')) {
      try { map.setLayoutProperty('fields-fill', 'visibility', fillVisibility); } catch (_) {}
    }
    if (labelVisibility !== null && map?.getLayer('fields-label')) {
      try { map.setLayoutProperty('fields-label', 'visibility', labelVisibility); } catch (_) {}
    }
    if (borderWidth !== null && map?.getLayer('fields-border')) {
      try { map.setPaintProperty('fields-border', 'line-width', borderWidth); } catch (_) {}
    }
    if (borderOpacity !== null && map?.getLayer('fields-border')) {
      try { map.setPaintProperty('fields-border', 'line-opacity', borderOpacity); } catch (_) {}
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
        const src = m.getSource('fields-source');
        if (src) {
          src.setData({ type: 'FeatureCollection', features: [] });
        }
      } catch (_) {}
    }
  };

  // ─── Refresh map colors from enriched GeoJSON + current mode ────────────────
  const applyModeColors = useRef(null);

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
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl(), 'top-right');

    handlersRef.current.onLoad = async () => {
      sessionPurgedRef.current = false;

      const m = mapRef.current;
      if (!m || !isMountedRef.current) return;
      setMapInstance(m);

      abortControllerRef.current = new AbortController();

      try {
        const params = {};
        if (enterpriseId) params.enterprise_id = enterpriseId;
        const res = await apiClient.get('/api/fields/geojson/all', {
          params,
          signal: abortControllerRef.current.signal,
        });

        if (
          sessionPurgedRef.current ||
          !isMountedRef.current ||
          !mapRef.current
        ) {
          return;
        }

        const rawGeoJson = res.data;
        geojsonRef.current = rawGeoJson;

        // Enrich with coverage data
        const enriched = enrichGeoJsonFeatures(rawGeoJson, coverageMapRef.current);
        enrichedGeoJsonRef.current = enriched;

        // Apply current mode colors
        const colored = addModeColorToFeatures(enriched, selectedMapModeRef.current);

        const doFitBounds = !dataLoadedRef.current;
        dataLoadedRef.current = true;

        if (!m.getSource('fields-source')) {
          m.addSource('fields-source', {
            type: 'geojson',
            data: colored,
            promoteId: 'id',
          });
        } else {
          m.getSource('fields-source').setData(colored);
        }

        // Add layers only if they don't exist
        if (!m.getLayer('fields-fill')) {
          m.addLayer({
            id: 'fields-fill',
            type: 'fill',
            source: 'fields-source',
            paint: {
              'fill-color': ['get', 'map_mode_color'],
              'fill-opacity': 0.4,
            },
          });
        }

        if (!m.getLayer('fields-border')) {
          m.addLayer({
            id: 'fields-border',
            type: 'line',
            source: 'fields-source',
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
            source: 'fields-source',
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

        const features = colored.features;
        if (features?.length && doFitBounds) {
          const bounds = new maplibregl.LngLatBounds();
          let hasCoords = false;
          for (const f of features) {
            if (!f?.geometry?.coordinates) continue;
            if (f.geometry.type === 'Polygon') {
              for (const ring of f.geometry.coordinates) {
                for (const [lng, lat] of ring) {
                  bounds.extend([lng, lat]);
                  hasCoords = true;
                }
              }
            } else if (f.geometry.type === 'MultiPolygon') {
              for (const poly of f.geometry.coordinates) {
                for (const ring of poly) {
                  for (const [lng, lat] of ring) {
                    bounds.extend([lng, lat]);
                    hasCoords = true;
                  }
                }
              }
            }
          }
          if (hasCoords && !bounds.isEmpty()) {
            if (fitBoundsTimeoutRef.current) clearTimeout(fitBoundsTimeoutRef.current);
            fitBoundsTimeoutRef.current = setTimeout(() => {
              fitBoundsTimeoutRef.current = null;
              if (mapRef.current !== m || !isMountedRef.current || m._removed) return;
              try { m.fitBounds(bounds, { padding: 60, duration: 0, maxZoom: 15 }); } catch (_) {}
            }, 100);
          }
        }

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
            if (!m.getSource('fields-source') || hoveredFeatureIdRef.current === featureId) return;
            clearHoverState(m, false);
            try {
              m.setFeatureState(
                { source: 'fields-source', id: featureId },
                { agrosatHover: true }
              );
            } catch (_) { return; }
            hoveredFeatureIdRef.current = featureId;
            m.getCanvas().style.cursor = 'pointer';
            setHoveredFeature(props);
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
          const featureId = e.features?.[0]?.properties?.id;
          if (featureId) {
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
          suspendFieldRenderingForMove(m);
        };
        handlersRef.current.onMoveEnd = () => {
          cameraMovingRef.current = false;
          restoreFieldRenderingAfterMove(m);
        };
        m.on('movestart', handlersRef.current.onMoveStart);
        m.on('moveend', handlersRef.current.onMoveEnd);

        callbacksRef.current.onMapReady?.(m);
      } catch (err) {
        if (err.name !== 'CanceledError') {
          console.error('Failed to load spatial layers');
        }
      }
    };

    window.addEventListener('agrosat:logout', handleLogout);

    map.on('load', handlersRef.current.onLoad);

    return () => {
      isMountedRef.current = false;
      sessionPurgedRef.current = true;
      window.removeEventListener('agrosat:logout', handleLogout);
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
        if (handlersRef.current.onMoveStart) {
          mapRef.current.off('movestart', handlersRef.current.onMoveStart);
        }
        if (handlersRef.current.onMoveEnd) {
          mapRef.current.off('moveend', handlersRef.current.onMoveEnd);
        }
        clearHoverState(mapRef.current, false);
        cameraMovingRef.current = false;
        labelVisibilityBeforeMoveRef.current = null;
        fieldFillVisibilityBeforeMoveRef.current = null;
        fieldBorderWidthBeforeMoveRef.current = null;
        fieldBorderOpacityBeforeMoveRef.current = null;
        LAYERS.forEach((layer) => {
          if (mapRef.current.getLayer(layer)) {
            if (layer === 'fields-fill' && handlersRef.current.onMouseMove) {
              mapRef.current.off('mousemove', layer, handlersRef.current.onMouseMove);
            }
            if (layer === 'fields-fill' && handlersRef.current.onMouseLeave) {
              mapRef.current.off('mouseleave', layer, handlersRef.current.onMouseLeave);
            }
            if (handlersRef.current.onFieldClick) {
              mapRef.current.off('click', layer, handlersRef.current.onFieldClick);
            }
          }
        });
        mapRef.current.remove();
        mapRef.current = null;
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
    if (styleKey === activeStyle) return;
    if (applyBaseMapVisibility(m, styleKey)) setActiveStyle(styleKey);
  };

  // ─── Switch color mode ─────────────────────────────────────────────────────
  const switchColorMode = (mode) => {
    setActiveColorMode(mode);
    styleSwitchColorModeRef.current = mode;
    if (onMapModeChange) onMapModeChange(mode);

    const m = mapRef.current;
    if (!m || !m.isStyleLoaded()) return;

    // Recolor features with new mode
    const enriched = enrichGeoJsonFeatures(geojsonRef.current, coverageMapRef.current);
    enrichedGeoJsonRef.current = enriched;
    const colored = addModeColorToFeatures(enriched, mode);

    try {
      const src = m.getSource('fields-source');
      if (src) {
        src.setData(colored);
      }
    } catch (_) {}

    // Update fill-color paint property
    try {
      if (m.getLayer('fields-fill')) {
        m.setPaintProperty('fields-fill', 'fill-color', ['get', 'map_mode_color']);
      }
    } catch (_) {}
  };

  // ─── Respond to coverageMap changes: re-enrich and recolor ─────────────────
  useEffect(() => {
    if (!coverageMap || !mapRef.current || !mapRef.current.isStyleLoaded()) return;
    const mode = selectedMapMode || 'crop';
    const enriched = enrichGeoJsonFeatures(geojsonRef.current, coverageMap);
    enrichedGeoJsonRef.current = enriched;
    const colored = addModeColorToFeatures(enriched, mode);
    try {
      const src = mapRef.current.getSource('fields-source');
      if (src) {
        src.setData(colored);
      }
    } catch (_) {}
  }, [coverageMap]);

  // ─── Respond to selectedMapMode changes: recolor ───────────────────────────
  useEffect(() => {
    if (!mapRef.current || !mapRef.current.isStyleLoaded()) return;
    const mode = selectedMapMode || 'crop';
    setActiveColorMode(mode);
    styleSwitchColorModeRef.current = mode;

    const enriched = enrichGeoJsonFeatures(geojsonRef.current, coverageMapRef.current);
    enrichedGeoJsonRef.current = enriched;
    const colored = addModeColorToFeatures(enriched, mode);

    try {
      const src = mapRef.current.getSource('fields-source');
      if (src) {
        src.setData(colored);
      }
    } catch (_) {}

    try {
      if (mapRef.current.getLayer('fields-fill')) {
        mapRef.current.setPaintProperty('fields-fill', 'fill-color', ['get', 'map_mode_color']);
        mapRef.current.setPaintProperty('fields-fill', 'fill-opacity', 0.4);
      }
    } catch (_) {}
  }, [selectedMapMode]);

  return (
    <div className="relative w-full h-full">
      <div ref={mapContainerRef} className="w-full h-full" />

      <NDVIRasterControl
        map={mapInstance}
        fieldId={selectedFieldId}
        onMetadataChange={setRasterMetadata}
      />

      {/* Style switcher */}
      <div className="absolute top-3 right-3 z-10 flex gap-1">
        {Object.entries(MAP_STYLES).map(([key, s]) => (
          <button
            key={key}
            onClick={() => switchMapStyle(key)}
            className={`px-2.5 py-1 text-xs rounded-md font-medium transition-colors shadow border ${activeStyle === key ? 'bg-blue-600 text-white border-blue-600' : 'bg-white/90 backdrop-blur-sm text-slate-600 hover:text-slate-900 border-slate-200'}`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* Map mode selector — compact row */}
      <div className="absolute top-12 right-3 z-10 flex flex-wrap gap-1 max-w-[260px] justify-end">
        {MAP_MODES.map(mode => (
          <button
            key={mode.code}
            onClick={() => switchColorMode(mode.code)}
            className={`px-2 py-1 text-xs rounded-md font-medium transition-colors shadow border ${
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
          onClick={() => setShowLegend(!showLegend)}
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
