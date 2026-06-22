import React, { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';
import apiClient from '../../api/client';

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
  maxzoom: 19,
};

const ESRI_SATELLITE_SOURCE = {
  type: 'raster',
  tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
  tileSize: 256,
  attribution: '© Esri, Maxar, Airbus',
  maxzoom: 19,
};

const ESRI_LABELS_SOURCE = {
  type: 'raster',
  tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'],
  tileSize: 256,
  attribution: '© Esri',
  maxzoom: 19,
};

const GLYPHS = 'https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf';

const MAP_STYLES = {
  satellite: {
    label: 'Спутник',
    style: {
      version: 8,
      glyphs: GLYPHS,
      sources: { satellite: ESRI_SATELLITE_SOURCE },
      layers: [{ id: 'satellite', type: 'raster', source: 'satellite' }],
    },
  },
  osm: {
    label: 'Карта',
    style: {
      version: 8,
      glyphs: GLYPHS,
      sources: { osm: OSM_SOURCE },
      layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
    },
  },
  hybrid: {
    label: 'Гибрид',
    style: {
      version: 8,
      glyphs: GLYPHS,
      sources: {
        satellite: ESRI_SATELLITE_SOURCE,
        labels: ESRI_LABELS_SOURCE,
      },
      layers: [
        { id: 'satellite', type: 'raster', source: 'satellite' },
        { id: 'labels', type: 'raster', source: 'labels' },
      ],
    },
  },
};

const DEFAULT_CENTER = [64.4286, 39.7747];
const DEFAULT_ZOOM = 9;

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
}) {
  const mapContainerRef = useRef(null);
  const mapRef = useRef(null);
  const drawRef = useRef(null);
  const isDrawingRef = useRef(false);
  const abortControllerRef = useRef(null);
  const isMountedRef = useRef(true);
  const sessionPurgedRef = useRef(false);
  const dataLoadedRef = useRef(false);
  const geojsonRef = useRef(null);
  const styleSwitchColorModeRef = useRef('crop');

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
  });

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

  // ─── Disable draw mode ───────────────────────────────────────────────────────
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

    // ponytail: patch MapboxDraw class constants for MapLibre GL compat
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

    // ponytail: global lock, per-account locks if throughput matters
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

  // ─── Map initialization ──────────────────────────────────────────────────────
  useEffect(() => {
    if (mapRef.current) return;
    isMountedRef.current = true;
    sessionPurgedRef.current = false;

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: MAP_STYLES.satellite.style,
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl(), 'top-right');

    handlersRef.current.onLoad = async () => {
      sessionPurgedRef.current = false;

      const m = mapRef.current;
      if (!m || !isMountedRef.current) return;

      // ─── GeoJSON load ────────────────────────────────────────────────────
      abortControllerRef.current = new AbortController();

      try {
        const params = {};
        if (enterpriseId) params.enterprise_id = enterpriseId;
        const res = await apiClient.get('/api/fields/geojson/all', {
          params,
          signal: abortControllerRef.current.signal,
        });

        // Mandatory check after request resolves
        if (
          sessionPurgedRef.current ||
          !isMountedRef.current ||
          !mapRef.current
        ) {
          return;
        }

        geojsonRef.current = res.data;
        dataLoadedRef.current = true;

        if (!m.getSource('fields-source')) {
          m.addSource('fields-source', {
            type: 'geojson',
            data: res.data,
            promoteId: 'id',
          });
        }

        // Add layers only if they don't exist
        if (!m.getLayer('fields-fill')) {
          m.addLayer({
            id: 'fields-fill',
            type: 'fill',
            source: 'fields-source',
            paint: {
              'fill-color': [
                'interpolate',
                ['linear'],
                ['coalesce', ['get', 'current_ndvi'], -1],
                -1, '#4b5563',
                0.0, '#8B0000',
                0.2, '#FF4500',
                0.35, '#FFD700',
                0.5, '#9ACD32',
                0.65, '#228B22',
                0.8, '#006400',
              ],
              'fill-opacity': 0.25,
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
              'line-width': 1.5,
              'line-opacity': 0.9,
            },
          });
        }

        // ─── Interaction handlers ──────────────────────────────────────────
        if (handlersRef.current.onMouseMove) {
          m.off('mousemove', 'fields-fill', handlersRef.current.onMouseMove);
        }
        if (handlersRef.current.onMouseLeave) {
          m.off('mouseleave', 'fields-fill', handlersRef.current.onMouseLeave);
        }
        if (handlersRef.current.onFieldClick) {
          m.off('click', 'fields-fill', handlersRef.current.onFieldClick);
        }

        handlersRef.current.onMouseMove = (e) => {
          if (isDrawingRef.current) return;
          if (e.features?.length > 0) {
            m.getCanvas().style.cursor = 'pointer';
            if (m.getLayer('fields-border')) {
              m.setPaintProperty('fields-border', 'line-width', [
                'case',
                ['==', ['get', 'id'], e.features[0].properties.id],
                3,
                1.5,
              ]);
              m.setPaintProperty('fields-border', 'line-opacity', [
                'case',
                ['==', ['get', 'id'], e.features[0].properties.id],
                1,
                0.9,
              ]);
            }
          }
        };

        handlersRef.current.onMouseLeave = () => {
          if (isDrawingRef.current) return;
          m.getCanvas().style.cursor = '';
          if (m.getLayer('fields-border')) {
            m.setPaintProperty('fields-border', 'line-width', 1.5);
            m.setPaintProperty('fields-border', 'line-opacity', 0.9);
          }
        };

        handlersRef.current.onFieldClick = (e) => {
          if (isDrawingRef.current) return;
          const featureId = e.features?.[0]?.properties?.id;
          if (featureId) {
            callbacksRef.current.onFieldSelect?.(featureId);
          }
        };

        m.on('mousemove', 'fields-fill', handlersRef.current.onMouseMove);
        m.on('mouseleave', 'fields-fill', handlersRef.current.onMouseLeave);
        m.on('click', 'fields-fill', handlersRef.current.onFieldClick);

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

      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }

      disableDrawMode();

      if (mapRef.current) {
        if (handlersRef.current.onMouseMove) {
          mapRef.current.off('mousemove', 'fields-fill', handlersRef.current.onMouseMove);
        }
        if (handlersRef.current.onMouseLeave) {
          mapRef.current.off('mouseleave', 'fields-fill', handlersRef.current.onMouseLeave);
        }
        if (handlersRef.current.onFieldClick) {
          mapRef.current.off('click', 'fields-fill', handlersRef.current.onFieldClick);
        }
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ─── Selected / highlight filter updates ─────────────────────────────────────
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !m.isStyleLoaded()) return;
    try {
      if (m.getLayer('fields-border')) {
        m.setPaintProperty('fields-border', 'line-width', [
          'case',
          ['==', ['get', 'id'], selectedFieldId || -1],
          3,
          1.5,
        ]);
        m.setPaintProperty('fields-border', 'line-opacity', [
          'case',
          ['==', ['get', 'id'], selectedFieldId || -1],
          1,
          0.9,
        ]);
      }
    } catch (_) {}
  }, [selectedFieldId]);

  // ─── Style switch ────────────────────────────────────────────────────────────
  const switchMapStyle = (styleKey) => {
    const m = mapRef.current;
    if (!m) return;
    styleSwitchColorModeRef.current = 'crop';
    m.setStyle(MAP_STYLES[styleKey].style);
    m.once('style.load', () => {
      if (geojsonRef.current) {
        rehydrateLayers(geojsonRef.current);
      }
    });
  };

  const rehydrateLayers = (data) => {
    const m = mapRef.current;
    if (!m || !isMountedRef.current) return;

    const mode = styleSwitchColorModeRef.current;
    const fillColor = mode === 'ndvi'
      ? [
          'interpolate',
          ['linear'],
          ['coalesce', ['get', 'current_ndvi'], -1],
          -1, '#4b5563',
          0.0, '#8B0000',
          0.2, '#FF4500',
          0.35, '#FFD700',
          0.5, '#9ACD32',
          0.65, '#228B22',
          0.8, '#006400',
        ]
      : CROP_COLOR_EXPR;

    if (!m.getSource('fields-source')) {
      m.addSource('fields-source', {
        type: 'geojson',
        data,
        promoteId: 'id',
      });
    } else {
      m.getSource('fields-source').setData(data);
    }

    if (!m.getLayer('fields-fill')) {
      m.addLayer({
        id: 'fields-fill',
        type: 'fill',
        source: 'fields-source',
        paint: {
          'fill-color': fillColor,
          'fill-opacity': 0.25,
        },
      });
    } else {
      m.setPaintProperty('fields-fill', 'fill-color', fillColor);
    }

    if (!m.getLayer('fields-border')) {
      m.addLayer({
        id: 'fields-border',
        type: 'line',
        source: 'fields-source',
        paint: {
          'line-color': '#ffffff',
          'line-width': 1.5,
          'line-opacity': 0.9,
        },
      });
    }
  };

  const switchColorMode = (mode) => {
    styleSwitchColorModeRef.current = mode;
    if (geojsonRef.current && mapRef.current?.isStyleLoaded()) {
      rehydrateLayers(geojsonRef.current);
    }
  };

  return (
    <div className="relative w-full h-full">
      <div ref={mapContainerRef} className="w-full h-full" />

      {/* Style switcher */}
      <div className="absolute top-3 right-3 z-10 flex gap-1">
        {Object.entries(MAP_STYLES).map(([key, s]) => (
          <button
            key={key}
            onClick={() => switchMapStyle(key)}
            className="px-2.5 py-1 text-xs rounded-md font-medium transition-colors shadow bg-white/90 backdrop-blur-sm text-slate-600 hover:text-slate-900 border border-slate-200"
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* Color mode toggle */}
      <div className="absolute top-12 right-3 z-10 flex gap-1">
        <button
          onClick={() => switchColorMode('crop')}
          className="px-2.5 py-1 text-xs rounded-md font-medium transition-colors shadow bg-white/90 backdrop-blur-sm text-slate-600 hover:text-slate-900 border border-slate-200"
        >
          Культуры
        </button>
        <button
          onClick={() => switchColorMode('ndvi')}
          className="px-2.5 py-1 text-xs rounded-md font-medium transition-colors shadow bg-white/90 backdrop-blur-sm text-slate-600 hover:text-slate-900 border border-slate-200"
        >
          NDVI
        </button>
      </div>

      {/* Re-center button */}
      <button
        onClick={() => mapRef.current?.flyTo({ center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, duration: 1000 })}
        className="absolute bottom-8 right-3 z-10 bg-white/90 backdrop-blur-sm hover:bg-slate-100 rounded-lg p-2 shadow-lg transition-colors border border-slate-200"
        title="Бухара"
      >
        <svg className="w-5 h-5 text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      </button>
    </div>
  );
}
