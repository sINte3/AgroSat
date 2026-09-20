import * as maplibregl from 'maplibre-gl';
import mapLibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';


// MapLibre v6 is ESM-only. Vite's worker pipeline emits a self-contained,
// same-origin worker asset, so no process-wide blob URL is created and the
// installed worker always matches the map runtime.
maplibregl.setWorkerUrl(mapLibreWorkerUrl);

export default maplibregl;
