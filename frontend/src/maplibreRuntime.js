import maplibregl from 'maplibre-gl/dist/maplibre-gl-csp';
import mapLibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-csp-worker?url';


// The default MapLibre bundle creates a process-wide blob: worker URL during
// module evaluation and cannot revoke it safely on logout. The CSP build keeps
// the worker as a normal Vite asset, so map ownership remains deterministic.
maplibregl.setWorkerUrl(mapLibreWorkerUrl);

export default maplibregl;
