import { useEffect, useRef, useState, useCallback } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { fetchFieldsGeoJson } from '../../api/client';

// ─── Цвета по культурам ───────────────────────────────────────────────────────
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

// ─── NDVI цветовая шкала ──────────────────────────────────────────────────────
const NDVI_COLORS = [
  { stop: 0.0,  color: '#8B0000' },
  { stop: 0.2,  color: '#FF4500' },
  { stop: 0.35, color: '#FFD700' },
  { stop: 0.5,  color: '#9ACD32' },
  { stop: 0.65, color: '#228B22' },
  { stop: 0.8,  color: '#006400' },
];

const NDVI_COLOR_EXPR = [
  'interpolate', ['linear'],
  ['coalesce', ['get', 'last_ndvi'], 0],
  ...NDVI_COLORS.flatMap(c => [c.stop, c.color]),
];

// ─── Источники карт ───────────────────────────────────────────────────────────
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

// ─── Легенда культур ─────────────────────────────────────────────────────────
const CROP_LEGEND = [
  { color: CROP_COLORS.wheat,        label: 'Пшеница' },
  { color: CROP_COLORS.cotton_drip,  label: 'Пахта томчи' },
  { color: CROP_COLORS.cotton_canal, label: 'Пахта очик' },
  { color: CROP_COLORS.alfalfa,      label: 'Люцерна' },
  { color: CROP_COLORS.default,      label: 'Прочее' },
];

export default function FieldMap({ onFieldSelect, selectedFieldId, enterpriseId, highlightedFieldId, onMapReady }) {
  const mapContainer = useRef(null);
  const map = useRef(null);
  const [mapLoaded, setMapLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [mapStyle, setMapStyle] = useState('satellite');
  const [colorMode, setColorMode] = useState('crop');
  const geojsonRef = useRef(null);
  const dataLoadedRef = useRef(false);

  // ─── Инициализация карты ───────────────────────────────────────────────────
  useEffect(() => {
    if (map.current) return;

    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: MAP_STYLES.satellite.style,
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
    });

    map.current.addControl(new maplibregl.NavigationControl(), 'top-right');

    map.current.on('load', () => {
      setMapLoaded(true);
    });

    map.current.on('click', 'fields-fill', (e) => {
      if (e.features?.length > 0 && onFieldSelect) {
        onFieldSelect(e.features[0].properties.id);
      }
    });

    map.current.on('mouseenter', 'fields-fill', () => {
      map.current.getCanvas().style.cursor = 'pointer';
    });
    map.current.on('mouseleave', 'fields-fill', () => {
      map.current.getCanvas().style.cursor = '';
    });

    return () => { map.current?.remove(); map.current = null; };
  }, []);

  // ─── Добавление слоёв полей ───────────────────────────────────────────────
  const addFieldLayers = useCallback((geojson, mode) => {
    const m = map.current;
    if (!m) return;

    try {
      if (m.getLayer('field-highlight')) m.removeLayer('field-highlight');
      if (m.getLayer('field-selected')) m.removeLayer('field-selected');
      if (m.getLayer('fields-outline')) m.removeLayer('fields-outline');
      if (m.getLayer('fields-fill')) m.removeLayer('fields-fill');
      if (m.getLayer('fields-labels')) m.removeLayer('fields-labels');
      if (m.getSource('field-centroids')) m.removeSource('field-centroids');
      if (m.getSource('fields')) m.removeSource('fields');
    } catch (_) {}

    m.addSource('fields', { type: 'geojson', data: geojson });

    const fillColor = mode === 'ndvi' ? NDVI_COLOR_EXPR : CROP_COLOR_EXPR;
    const fillOpacity = mode === 'ndvi' ? 0.3 : 0.25;

    m.addLayer({
      id: 'fields-fill',
      type: 'fill',
      source: 'fields',
      paint: {
        'fill-color': fillColor,
        'fill-opacity': fillOpacity,
      },
    });

    const outlineColor = mode === 'ndvi'
      ? '#ffffff'
      : CROP_COLOR_EXPR;

    m.addLayer({
      id: 'fields-outline',
      type: 'line',
      source: 'fields',
      paint: {
        'line-color': outlineColor,
        'line-width': [
          'case',
          ['==', ['get', 'alert_severity'], 'critical'], 2.5,
          ['==', ['get', 'alert_severity'], 'warning'],  2,
          ['==', ['get', 'id'], selectedFieldId || -1], 3,
          1.5,
        ],
        'line-opacity': [
          'case',
          ['==', ['get', 'alert_severity'], 'critical'], 1,
          ['==', ['get', 'alert_severity'], 'warning'],  0.95,
          0.9,
        ],
      },
    });

    // Highlight layer (hover)
    m.addLayer({
      id: 'field-highlight',
      type: 'line',
      source: 'fields',
      paint: {
        'line-color': '#16a34a',
        'line-width': 3,
        'line-opacity': 0.9,
      },
      filter: ['==', ['get', 'id'], ''],
    });

    // Selected layer — blue dashed
    m.addLayer({
      id: 'field-selected',
      type: 'line',
      source: 'fields',
      paint: {
        'line-color': '#2563eb',
        'line-width': 3,
        'line-dasharray': [2, 1],
        'line-opacity': 1,
      },
      filter: ['==', ['get', 'id'], ''],
    });

    // Build centroid labels
    const centroidFeatures = (geojson.features || [])
      .filter(f => f.properties?.centroid_lat && f.properties?.centroid_lon)
      .map(f => ({
        type: 'Feature',
        geometry: {
          type: 'Point',
          coordinates: [f.properties.centroid_lon, f.properties.centroid_lat],
        },
        properties: {
          id: f.properties.id,
          name: f.properties.name,
        },
      }));

    m.addSource('field-centroids', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: centroidFeatures },
    });

    m.addLayer({
      id: 'fields-labels',
      type: 'symbol',
      source: 'field-centroids',
      minzoom: 11,
      layout: {
        'text-field': ['get', 'name'],
        'text-font': ['Noto Sans Regular'],
        'text-size': [
          'interpolate', ['linear'], ['zoom'],
          11, 9,
          13, 11,
          15, 13,
          17, 15,
        ],
        'text-anchor': 'center',
        'text-max-width': 10,
        'text-allow-overlap': false,
        'text-ignore-placement': false,
      },
      paint: {
        'text-color': '#1a2e23',
        'text-halo-color': '#ffffff',
        'text-halo-width': 1.5,
        'text-halo-blur': 0,
      },
    });
  }, [selectedFieldId]);

  // ─── Expose map to parent ──────────────────────────────────────────────────
  useEffect(() => {
    if (mapLoaded && map.current && onMapReady) {
      onMapReady(map.current);
    }
  }, [mapLoaded, onMapReady]);

  // ─── Загрузка полей ───────────────────────────────────────────────────────
  const loadFields = useCallback(async () => {
    if (dataLoadedRef.current && geojsonRef.current) {
      if (map.current?.isStyleLoaded()) {
        addFieldLayers(geojsonRef.current, colorMode);
      }
      return;
    }

    setLoading(true);
    try {
      const params = {};
      if (enterpriseId) params.enterprise_id = enterpriseId;
      const geojson = await fetchFieldsGeoJson(params);
      geojsonRef.current = geojson;
      dataLoadedRef.current = true;
      if (map.current?.isStyleLoaded()) {
        addFieldLayers(geojson, colorMode);
      }
    } catch (err) {
      console.error('Ошибка загрузки полей:', err);
    } finally {
      setLoading(false);
    }
  }, [enterpriseId, colorMode, addFieldLayers]);

  useEffect(() => {
    if (mapLoaded) loadFields();
  }, [mapLoaded, loadFields]);

  const prevEnterpriseRef = useRef(enterpriseId);
  useEffect(() => {
    if (prevEnterpriseRef.current !== enterpriseId) {
      dataLoadedRef.current = false;
      prevEnterpriseRef.current = enterpriseId;
    }
  }, [enterpriseId]);

  // ─── Смена стиля карты ────────────────────────────────────────────────────
  const switchMapStyle = useCallback((styleKey) => {
    if (!map.current) return;
    setMapStyle(styleKey);
    map.current.setStyle(MAP_STYLES[styleKey].style);
    map.current.once('style.load', () => {
      if (geojsonRef.current) {
        addFieldLayers(geojsonRef.current, colorMode);
      }
    });
  }, [addFieldLayers, colorMode]);

  // ─── Смена режима окраски ─────────────────────────────────────────────────
  const switchColorMode = useCallback((mode) => {
    setColorMode(mode);
    if (geojsonRef.current && map.current?.isStyleLoaded()) {
      addFieldLayers(geojsonRef.current, mode);
    }
  }, [addFieldLayers]);

  // ─── Обновление выделения поля ────────────────────────────────────────────
  useEffect(() => {
    if (!map.current || !mapLoaded) return;
    try {
      if (map.current.getLayer('fields-outline')) {
        map.current.setPaintProperty('fields-outline', 'line-width', [
          'case',
          ['==', ['get', 'alert_severity'], 'critical'], 2.5,
          ['==', ['get', 'alert_severity'], 'warning'],  2,
          ['==', ['get', 'id'], selectedFieldId || -1], 3,
          1.5,
        ]);
      }
    } catch (_) {}
  }, [selectedFieldId, mapLoaded]);

  // ─── Fly to selected field ─────────────────────────────────────────────────
  useEffect(() => {
    if (!selectedFieldId || !map.current || !mapLoaded || !geojsonRef.current) return;
    const feature = geojsonRef.current.features?.find(
      f => f.properties?.id === selectedFieldId
    );
    if (!feature) return;

    // Use fitBounds to account for panel overlap (left: 420px padding)
    const coords = feature.geometry?.coordinates;
    if (coords && coords[0]) {
      const bounds = coords[0].reduce(
        (b, c) => [
          [Math.min(b[0][0], c[0]), Math.min(b[0][1], c[1])],
          [Math.max(b[1][0], c[0]), Math.max(b[1][1], c[1])],
        ],
        [[Infinity, Infinity], [-Infinity, -Infinity]]
      );
      map.current.fitBounds(bounds, {
        padding: { top: 80, bottom: 80, left: 420, right: 80 },
        maxZoom: 16,
        duration: 1000,
      });
    } else {
      // Fallback to centroid flyTo
      const lat = feature.properties?.centroid_lat;
      const lon = feature.properties?.centroid_lon;
      if (lat && lon) {
        map.current.flyTo({
          center: [lon, lat],
          zoom: Math.max(map.current.getZoom(), 14),
          duration: 1000,
          essential: true,
        });
      }
    }
  }, [selectedFieldId, mapLoaded]);

  // ─── Highlight hovered field ──────────────────────────────────────────────
  useEffect(() => {
    if (!map.current || !mapLoaded) return;
    try {
      if (map.current.getLayer('field-highlight')) {
        map.current.setFilter('field-highlight',
          highlightedFieldId
            ? ['any', ['==', ['get', 'id'], highlightedFieldId], ['==', ['get', 'id'], String(highlightedFieldId)]]
            : ['==', ['get', 'id'], '']
        );
      }
    } catch (_) {}
  }, [highlightedFieldId, mapLoaded]);

  // ─── Update selected field filter ───────────────────────────────────────
  useEffect(() => {
    if (!map.current || !mapLoaded) return;
    try {
      if (map.current.getLayer('field-selected')) {
        map.current.setFilter('field-selected',
          selectedFieldId
            ? ['any', ['==', ['get', 'id'], selectedFieldId], ['==', ['get', 'id'], String(selectedFieldId)]]
            : ['==', ['get', 'id'], '']
        );
      }
    } catch (_) {}
  }, [selectedFieldId, mapLoaded]);

  // ─── Map resize when container changes ────────────────────────────────────
  useEffect(() => {
    if (!map.current || !mapLoaded) return;
    const timer = setTimeout(() => map.current.resize(), 100);
    return () => clearTimeout(timer);
  }, [mapLoaded]);

  return (
    <div className="relative w-full h-full">
      <div ref={mapContainer} className="w-full h-full" />

      {/* Style switcher — top-right, below nav controls */}
      <div className="absolute top-3 right-3 z-10 flex gap-1">
        {Object.entries(MAP_STYLES).map(([key, s]) => (
          <button
            key={key}
            onClick={() => switchMapStyle(key)}
            className={`px-2.5 py-1 text-xs rounded-md font-medium transition-colors shadow ${
              mapStyle === key
                ? 'bg-agro-accent text-white'
                : 'bg-white/90 backdrop-blur-sm text-agro-muted hover:text-agro-text border border-agro-border'
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* Color mode toggle — above legend */}
      <div className="absolute top-12 right-3 z-10 flex gap-1">
        <button
          onClick={() => switchColorMode('crop')}
          className={`px-2.5 py-1 text-xs rounded-md font-medium transition-colors shadow ${
            colorMode === 'crop'
              ? 'bg-agro-accent text-white'
              : 'bg-white/90 backdrop-blur-sm text-agro-muted hover:text-agro-text border border-agro-border shadow-sm'
          }`}
        >
          Культуры
        </button>
        <button
          onClick={() => switchColorMode('ndvi')}
          className={`px-2.5 py-1 text-xs rounded-md font-medium transition-colors shadow ${
            colorMode === 'ndvi'
              ? 'bg-agro-accent text-white'
              : 'bg-white/90 backdrop-blur-sm text-agro-muted hover:text-agro-text border border-agro-border shadow-sm'
          }`}
        >
          NDVI
        </button>
      </div>

      {/* Loading indicator */}
      {loading && (
        <div className="absolute top-24 left-3 z-10 bg-white/90 backdrop-blur-sm rounded-lg px-3 py-2 text-xs text-agro-muted flex items-center gap-2 shadow-sm border border-agro-border">
          <svg className="animate-spin h-4 w-4 text-agro-accent" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
          </svg>
          Загрузка полей...
        </div>
      )}

      {/* Legend */}
      <div className="absolute bottom-8 left-3 z-10 bg-white/90 backdrop-blur-sm rounded-lg px-3 py-2 text-xs border border-agro-border shadow-sm">
        {colorMode === 'crop' ? (
          <>
            <p className="text-agro-muted mb-1.5 font-medium">Культуры</p>
            <div className="space-y-1">
              {CROP_LEGEND.map(item => (
                <div key={item.label} className="flex items-center gap-2">
                  <div className="w-3 h-3 rounded-sm flex-shrink-0" style={{ backgroundColor: item.color }} />
                  <span className="text-agro-text">{item.label}</span>
                </div>
              ))}
            </div>
          </>
        ) : (
          <>
            <p className="text-agro-muted mb-1 font-medium">NDVI</p>
            <div className="flex gap-0.5 h-3 rounded overflow-hidden">
              {NDVI_COLORS.slice(0, -1).map((c, i) => (
                <div key={i} className="flex-1" style={{ backgroundColor: c.color }} />
              ))}
            </div>
            <div className="flex justify-between text-agro-muted mt-0.5">
              <span>0.0</span><span>0.8</span>
            </div>
          </>
        )}
      </div>

      {/* Re-center button */}
      <button
        onClick={() => map.current?.flyTo({ center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, duration: 1000 })}
        className="absolute bottom-8 right-3 z-10 bg-white/90 backdrop-blur-sm hover:bg-agro-card rounded-lg p-2 shadow-lg transition-colors border border-agro-border"
        title="Бухара"
      >
        <svg className="w-5 h-5 text-agro-text" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
      </button>
    </div>
  );
}
