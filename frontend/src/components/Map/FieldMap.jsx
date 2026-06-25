import React, { useEffect, useRef, useState } from 'react';
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
  attribution: 'Р вЂ™Р’В© OpenStreetMap contributors',
  maxzoom: 18,
};

const ESRI_SATELLITE_SOURCE = {
  type: 'raster',
  tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
  tileSize: 256,
  attribution: 'Р вЂ™Р’В© Esri, Maxar, Airbus',
  maxzoom: 18,
};

const ESRI_LABELS_SOURCE = {
  type: 'raster',
  tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'],
  tileSize: 256,
  attribution: 'Р вЂ™Р’В© Esri',
  maxzoom: 18,
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
const DEFAULT_ZOOM = 7;
const MAP_MIN_ZOOM = 3;
const MAP_MAX_ZOOM = 18;
const LAYERS = ['fields-fill']; // TODO TASK_042: add field labels with a verified glyph source.

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
  const [activeStyle, setActiveStyle] = useState('satellite');
  const [activeColorMode, setActiveColorMode] = useState('crop');
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

  // Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ Disable draw mode Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ
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

  // Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ Enable draw mode Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ
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

    // Patch MapboxDraw class constants for MapLibre GL compatibility.
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

    // Global draw-control lock; use per-account locks only if throughput requires it.
  };

  // Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ Draw mode effect Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ
  useEffect(() => {
    if (isDrawingMode && canDraw) {
      enableDrawMode();
    } else {
      disableDrawMode();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDrawingMode, canDraw]);

  // Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ Logout handler Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ
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

  // Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ Map initialization Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ
  useEffect(() => {
    if (mapRef.current) return;
    isMountedRef.current = true;
    sessionPurgedRef.current = false;

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: MAP_STYLES.satellite.style,
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

      // Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ GeoJSON load Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ
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

        // Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ Fit map to loaded field bounds (once) Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ
        const doFitBounds = !dataLoadedRef.current;
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
              'fill-color': CROP_COLOR_EXPR,
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
              'line-width': 2,
              'line-opacity': 1,
            },
          });
        }

        const features = res.data.features;
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
            setTimeout(() => {
              try { m.fitBounds(bounds, { padding: 60, duration: 0, maxZoom: 15 }); } catch (_) {}
            }, 100);
          }
        }

        // Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ Interaction handlers Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ
        LAYERS.forEach((layer) => {
          if (m.getLayer(layer)) {
            if (handlersRef.current.onMouseMove) {
              m.off('mousemove', layer, handlersRef.current.onMouseMove);
            }
            if (handlersRef.current.onMouseLeave) {
              m.off('mouseleave', layer, handlersRef.current.onMouseLeave);
            }
            if (handlersRef.current.onFieldClick) {
              m.off('click', layer, handlersRef.current.onFieldClick);
            }
          }
        });

        handlersRef.current.onMouseMove = (e) => {
          if (isDrawingRef.current) return;
          if (e.features?.length > 0) {
            m.getCanvas().style.cursor = 'pointer';
            if (m.getLayer('fields-border')) {
              m.setPaintProperty('fields-border', 'line-width', [
                'case',
                ['==', ['get', 'id'], e.features[0].properties.id],
                3,
                2,
              ]);
              m.setPaintProperty('fields-border', 'line-opacity', [
                'case',
                ['==', ['get', 'id'], e.features[0].properties.id],
                1,
                1,
              ]);
            }
          }
        };

        handlersRef.current.onMouseLeave = () => {
          if (isDrawingRef.current) return;
          m.getCanvas().style.cursor = '';
          if (m.getLayer('fields-border')) {
            m.setPaintProperty('fields-border', 'line-width', 2);
            m.setPaintProperty('fields-border', 'line-opacity', 1);
          }
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
            m.on('mousemove', layer, handlersRef.current.onMouseMove);
            m.on('mouseleave', layer, handlersRef.current.onMouseLeave);
            m.on('click', layer, handlersRef.current.onFieldClick);
          }
        });

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
        LAYERS.forEach((layer) => {
          if (mapRef.current.getLayer(layer)) {
            if (handlersRef.current.onMouseMove) {
              mapRef.current.off('mousemove', layer, handlersRef.current.onMouseMove);
            }
            if (handlersRef.current.onMouseLeave) {
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

  // Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ Selected / highlight filter updates Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !m.isStyleLoaded()) return;
    try {
      if (m.getLayer('fields-border')) {
        m.setPaintProperty('fields-border', 'line-width', [
          'case',
          ['==', ['get', 'id'], selectedFieldId || -1],
          3,
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

  // Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ Style switch Р Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљР Р†РІР‚СњР вЂљ
  const switchMapStyle = (styleKey) => {
    const m = mapRef.current;
    if (!m) return;
    if (styleKey === activeStyle) return; // Skip redundant style reload when the selected style is already active.
    setActiveStyle(styleKey);
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
          ['coalesce', ['get', 'last_ndvi'], -1],
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
          'fill-opacity': 0.4,
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
          'line-width': 2,
          'line-opacity': 1,
        },
      });
    }

    // Re-attach interaction handlers after style switch
    LAYERS.forEach((layer) => {
      if (m.getLayer(layer)) {
        if (handlersRef.current.onMouseMove) {
          m.off('mousemove', layer, handlersRef.current.onMouseMove);
          m.on('mousemove', layer, handlersRef.current.onMouseMove);
        }
        if (handlersRef.current.onMouseLeave) {
          m.off('mouseleave', layer, handlersRef.current.onMouseLeave);
          m.on('mouseleave', layer, handlersRef.current.onMouseLeave);
        }
        if (handlersRef.current.onFieldClick) {
          m.off('click', layer, handlersRef.current.onFieldClick);
          m.on('click', layer, handlersRef.current.onFieldClick);
        }
      }
    });
  };

  const switchColorMode = (mode) => {
    setActiveColorMode(mode);
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
            className={`px-2.5 py-1 text-xs rounded-md font-medium transition-colors shadow border ${activeStyle === key ? 'bg-blue-600 text-white border-blue-600' : 'bg-white/90 backdrop-blur-sm text-slate-600 hover:text-slate-900 border-slate-200'}`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* Color mode toggle */}
      <div className="absolute top-12 right-3 z-10 flex gap-1">
        <button
          onClick={() => switchColorMode('crop')}
          className={`px-2.5 py-1 text-xs rounded-md font-medium transition-colors shadow border ${activeColorMode === 'crop' ? 'bg-blue-600 text-white border-blue-600' : 'bg-white/90 backdrop-blur-sm text-slate-600 hover:text-slate-900 border-slate-200'}`}
        >
          Культуры
        </button>
        <button
          onClick={() => switchColorMode('ndvi')}
          className={`px-2.5 py-1 text-xs rounded-md font-medium transition-colors shadow border ${activeColorMode === 'ndvi' ? 'bg-blue-600 text-white border-blue-600' : 'bg-white/90 backdrop-blur-sm text-slate-600 hover:text-slate-900 border-slate-200'}`}
        >
          NDVI
        </button>
      </div>

      {/* NDVI legend */}
      {activeColorMode === 'ndvi' && (
        <div className="absolute bottom-20 right-3 z-10 bg-white/90 backdrop-blur-sm rounded-lg p-2.5 shadow-lg border border-slate-200 text-xs">
          <div className="font-medium text-slate-700 mb-1.5 text-center">NDVI</div>
          <div className="flex flex-col gap-1">
            {[
              { color: '#006400', label: '0.80+' },
              { color: '#228B22', label: '0.65' },
              { color: '#9ACD32', label: '0.50' },
              { color: '#FFD700', label: '0.35' },
              { color: '#FF4500', label: '0.20' },
              { color: '#8B0000', label: '0.00' },
              { color: '#4b5563', label: 'Р В ССљР В Р’ВµР РЋРІР‚С™' },
            ].map(({ color, label }) => (
              <div key={label} className="flex items-center gap-2">
                <div className="w-4 h-3 rounded-sm" style={{ backgroundColor: color }} />
                <span className="text-slate-600">{label}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Re-center button */}
      <button
        onClick={() => mapRef.current?.flyTo({ center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, duration: 1000 })}
        className="absolute bottom-8 right-3 z-10 bg-white/90 backdrop-blur-sm hover:bg-slate-100 rounded-lg p-2 shadow-lg transition-colors border border-slate-200"
        title="Р В РІР‚ВР РЋСвЂњР РЋРІР‚В¦Р В Р’В°Р РЋР вЂљР В Р’В°"
      >
        <svg className="w-5 h-5 text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      </button>
    </div>
  );
}
