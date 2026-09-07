import { useEffect, useRef } from 'react';
import maplibregl from '../../maplibreRuntime';
import 'maplibre-gl/dist/maplibre-gl.css';

const SOURCE = 'agronomy-case-field';
export default function AgronomyCaseMap({ geometry, workItems = [] }) {
  const host = useRef(null);
  useEffect(() => {
    if (!host.current || !geometry) return undefined;
    const controller = new AbortController();
    const map = new maplibregl.Map({ container: host.current, style: { version: 8, sources: {}, layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#eef4ef' } }] }, center: [64.4, 39.8], zoom: 10, attributionControl: false });
    const control = new maplibregl.NavigationControl({ showCompass: false });
    map.addControl(control, 'top-right');
    const onLoad = () => {
      if (controller.signal.aborted) return;
      const features = [{ type: 'Feature', properties: { kind: 'field' }, geometry }, ...workItems.filter(item => item.work_geometry).map(item => ({ type: 'Feature', properties: { kind: 'work' }, geometry: item.work_geometry }))];
      map.addSource(SOURCE, { type: 'geojson', data: { type: 'FeatureCollection', features } });
      map.addLayer({ id: `${SOURCE}-fill`, type: 'fill', source: SOURCE, filter: ['==', ['geometry-type'], 'Polygon'], paint: { 'fill-color': ['match', ['get', 'kind'], 'work', '#d97706', '#15803d'], 'fill-opacity': 0.22 } });
      map.addLayer({ id: `${SOURCE}-line`, type: 'line', source: SOURCE, paint: { 'line-color': ['match', ['get', 'kind'], 'work', '#b45309', '#166534'], 'line-width': 2 } });
      map.addLayer({ id: `${SOURCE}-point`, type: 'circle', source: SOURCE, filter: ['==', ['geometry-type'], 'Point'], paint: { 'circle-radius': 7, 'circle-color': '#b45309', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } });
      const bounds = new maplibregl.LngLatBounds();
      const visit = coordinates => typeof coordinates?.[0] === 'number' ? bounds.extend(coordinates) : coordinates?.forEach(visit);
      features.forEach(feature => visit(feature.geometry.coordinates));
      if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 28, maxZoom: 15, duration: 0 });
    };
    map.on('load', onLoad);
    return () => {
      controller.abort();
      map.off('load', onLoad);
      for (const layer of [`${SOURCE}-point`, `${SOURCE}-line`, `${SOURCE}-fill`]) if (map.getLayer(layer)) map.removeLayer(layer);
      if (map.getSource(SOURCE)) map.removeSource(SOURCE);
      try { map.removeControl(control); } catch (_) { /* already removed */ }
      map.remove();
    };
  }, [geometry, workItems]);
  return <div ref={host} className="h-64 w-full overflow-hidden rounded-lg border border-agro-border" aria-label="Карта поля и зоны работ" role="region" />;
}
