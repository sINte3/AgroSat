import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getPixelNDVIImage,
  getPixelNDVIScenes,
  getPixelNDVIWorkspace,
  samplePixelNDVI,
} from '../api/pixelNdvi';
import {
  chooseInitialScenes,
  isAbortError,
  safeErrorStatus,
  validWorkspace,
} from '../utils/pixelNdviState';

export const PIXEL_NDVI_SOURCE_A = 'agrosat-pixel-ndvi-a-source';
export const PIXEL_NDVI_LAYER_A = 'agrosat-pixel-ndvi-a-layer';
export const PIXEL_NDVI_SOURCE_B = 'agrosat-pixel-ndvi-b-source';
export const PIXEL_NDVI_LAYER_B = 'agrosat-pixel-ndvi-b-layer';

const EMPTY_RENDER = Object.freeze({
  workspaceA: null,
  workspaceB: null,
  urlA: null,
  urlB: null,
  clippedUrl: null,
  imageB: null,
  cacheA: null,
  cacheB: null,
});

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = 'async';
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('Invalid raster image'));
    image.src = url;
  });
}

function canvasBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error('Comparison image unavailable'));
    }, 'image/png');
  });
}

async function clippedImageUrl(image, divider) {
  const canvas = document.createElement('canvas');
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const context = canvas.getContext('2d', { alpha: true });
  if (!context) throw new Error('Comparison canvas unavailable');
  const start = Math.round(canvas.width * (divider / 100));
  const width = canvas.width - start;
  context.clearRect(0, 0, canvas.width, canvas.height);
  if (width > 0) {
    context.drawImage(image, start, 0, width, canvas.height, start, 0, width, canvas.height);
  }
  return URL.createObjectURL(await canvasBlob(canvas));
}

function beforeFieldLabels(map) {
  if (map.getLayer('fields-border')) return 'fields-border';
  if (map.getLayer('fields-label')) return 'fields-label';
  return undefined;
}

export default function usePixelNDVIWorkspace({
  map,
  fieldId,
  enabled,
  opacity,
  comparisonEnabled,
  divider,
}) {
  const [catalog, setCatalog] = useState({ status: 'idle', scenes: [], errorStatus: null });
  const [layer, setLayer] = useState({ status: 'idle', workspaceA: null, workspaceB: null, cacheA: null, cacheB: null, errorStatus: null });
  const [sceneA, setSceneA] = useState(null);
  const [sceneB, setSceneB] = useState(null);
  const [sample, setSample] = useState({ status: 'idle', value: null, errorStatus: null });
  const [retryKey, setRetryKey] = useState(0);
  const mountedRef = useRef(true);
  const generationRef = useRef(0);
  const clipGenerationRef = useRef(0);
  const catalogControllerRef = useRef(null);
  const layerControllerRef = useRef(null);
  const sampleControllerRef = useRef(null);
  const renderRef = useRef({ ...EMPTY_RENDER });
  const enabledRef = useRef(enabled);
  const comparisonRef = useRef(comparisonEnabled);
  const opacityRef = useRef(opacity);

  const removeMapArtifacts = useCallback((targetMap = map) => {
    if (!targetMap || targetMap._removed) return;
    try {
      if (targetMap.getLayer(PIXEL_NDVI_LAYER_B)) targetMap.removeLayer(PIXEL_NDVI_LAYER_B);
      if (targetMap.getLayer(PIXEL_NDVI_LAYER_A)) targetMap.removeLayer(PIXEL_NDVI_LAYER_A);
      if (targetMap.getSource(PIXEL_NDVI_SOURCE_B)) targetMap.removeSource(PIXEL_NDVI_SOURCE_B);
      if (targetMap.getSource(PIXEL_NDVI_SOURCE_A)) targetMap.removeSource(PIXEL_NDVI_SOURCE_A);
    } catch (_) {
      // A style replacement may have already removed custom artifacts.
    }
  }, [map]);

  const disposeRender = useCallback(() => {
    const current = renderRef.current;
    [current.urlA, current.urlB, current.clippedUrl].filter(Boolean).forEach((url) => URL.revokeObjectURL(url));
    renderRef.current = { ...EMPTY_RENDER };
  }, []);

  const addMapArtifacts = useCallback((targetMap = map) => {
    const current = renderRef.current;
    if (!targetMap || targetMap._removed || !current.urlA || !validWorkspace(current.workspaceA)) return;
    removeMapArtifacts(targetMap);
    try {
      const beforeId = beforeFieldLabels(targetMap);
      targetMap.addSource(PIXEL_NDVI_SOURCE_A, {
        type: 'image', url: current.urlA, coordinates: current.workspaceA.corners,
      });
      targetMap.addLayer({
        id: PIXEL_NDVI_LAYER_A,
        type: 'raster',
        source: PIXEL_NDVI_SOURCE_A,
        paint: { 'raster-opacity': opacityRef.current, 'raster-fade-duration': 0 },
      }, beforeId);
      if (comparisonRef.current && current.clippedUrl && validWorkspace(current.workspaceB)) {
        targetMap.addSource(PIXEL_NDVI_SOURCE_B, {
          type: 'image', url: current.clippedUrl, coordinates: current.workspaceB.corners,
        });
        targetMap.addLayer({
          id: PIXEL_NDVI_LAYER_B,
          type: 'raster',
          source: PIXEL_NDVI_SOURCE_B,
          paint: { 'raster-opacity': opacityRef.current, 'raster-fade-duration': 0 },
        }, beforeId);
      }
    } catch (_) {
      removeMapArtifacts(targetMap);
    }
  }, [map, removeMapArtifacts]);

  useEffect(() => {
    enabledRef.current = enabled;
    comparisonRef.current = comparisonEnabled;
    opacityRef.current = opacity;
  }, [comparisonEnabled, enabled, opacity]);

  useEffect(() => {
    if (!map) return undefined;
    const handleStyleReady = () => {
      if (!mountedRef.current || !enabledRef.current || map._removed) return;
      const current = renderRef.current;
      const requiresB = comparisonRef.current && current.clippedUrl && validWorkspace(current.workspaceB);
      try {
        const hasA = map.getLayer(PIXEL_NDVI_LAYER_A) && map.getSource(PIXEL_NDVI_SOURCE_A);
        const hasB = !requiresB || (map.getLayer(PIXEL_NDVI_LAYER_B) && map.getSource(PIXEL_NDVI_SOURCE_B));
        if (hasA && hasB) return;
      } catch (_) {
        // A style replacement may be between its teardown and ready events.
      }
      addMapArtifacts(map);
    };
    map.on('style.load', handleStyleReady);
    map.on('idle', handleStyleReady);
    const restoreInterval = enabled ? window.setInterval(handleStyleReady, 250) : null;
    handleStyleReady();
    return () => {
      if (restoreInterval !== null) window.clearInterval(restoreInterval);
      map.off('style.load', handleStyleReady);
      map.off('idle', handleStyleReady);
    };
  }, [addMapArtifacts, enabled, map]);

  useEffect(() => {
    catalogControllerRef.current?.abort();
    setSceneA(null);
    setSceneB(null);
    setSample({ status: 'idle', value: null, errorStatus: null });
    if (!enabled || !fieldId) {
      setCatalog({ status: 'idle', scenes: [], errorStatus: null });
      return undefined;
    }
    const controller = new AbortController();
    catalogControllerRef.current = controller;
    setCatalog({ status: 'loading', scenes: [], errorStatus: null });
    getPixelNDVIScenes(fieldId, { signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted || !mountedRef.current) return;
        const scenes = Array.isArray(response?.scenes) ? response.scenes : [];
        const initial = chooseInitialScenes(scenes);
        setCatalog({ status: scenes.length ? 'ready' : 'empty', scenes, errorStatus: null });
        setSceneA(initial.sceneA);
        setSceneB(initial.sceneB);
      })
      .catch((error) => {
        if (!isAbortError(error) && mountedRef.current) {
          setCatalog({ status: 'error', scenes: [], errorStatus: safeErrorStatus(error) });
        }
      });
    return () => controller.abort();
  }, [enabled, fieldId, retryKey]);

  useEffect(() => {
    if (!comparisonEnabled || !sceneA || sceneB !== sceneA) return;
    const alternative = catalog.scenes.find((scene) => scene.raster_available && scene.scene_id !== sceneA);
    if (alternative) setSceneB(alternative.scene_id);
  }, [catalog.scenes, comparisonEnabled, sceneA, sceneB]);

  useEffect(() => {
    const generation = ++generationRef.current;
    ++clipGenerationRef.current;
    layerControllerRef.current?.abort();
    removeMapArtifacts(map);
    disposeRender();
    setSample({ status: 'idle', value: null, errorStatus: null });
    if (!enabled || !fieldId || !sceneA || !map) {
      setLayer({ status: 'idle', workspaceA: null, workspaceB: null, cacheA: null, cacheB: null, errorStatus: null });
      return undefined;
    }
    const controller = new AbortController();
    layerControllerRef.current = controller;
    setLayer((current) => ({ ...current, status: 'loading', errorStatus: null }));

    async function loadScene(sceneId) {
      const workspace = await getPixelNDVIWorkspace(fieldId, sceneId, controller.signal);
      if (!validWorkspace(workspace)) throw new Error('Invalid workspace metadata');
      const image = await getPixelNDVIImage(fieldId, sceneId, controller.signal);
      if (!(image.blob instanceof Blob) || image.blob.size === 0 || !/^image\/png(?:;|$)/i.test(image.blob.type)) {
        throw new Error('Invalid raster image');
      }
      return { workspace, url: URL.createObjectURL(image.blob), cacheState: image.cacheState };
    }

    async function load() {
      let loadedA = null;
      let loadedB = null;
      try {
        [loadedA, loadedB] = await Promise.all([
          loadScene(sceneA),
          comparisonEnabled && sceneB ? loadScene(sceneB) : Promise.resolve(null),
        ]);
        if (generation !== generationRef.current || controller.signal.aborted || !mountedRef.current) return;
        let imageB = null;
        let clippedUrl = null;
        if (loadedB) {
          imageB = await loadImage(loadedB.url);
          clippedUrl = await clippedImageUrl(imageB, divider);
        }
        if (generation !== generationRef.current || controller.signal.aborted || !mountedRef.current) {
          if (clippedUrl) URL.revokeObjectURL(clippedUrl);
          return;
        }
        renderRef.current = {
          workspaceA: loadedA.workspace,
          workspaceB: loadedB?.workspace || null,
          urlA: loadedA.url,
          urlB: loadedB?.url || null,
          clippedUrl,
          imageB,
          cacheA: loadedA.cacheState,
          cacheB: loadedB?.cacheState || null,
        };
        loadedA = null;
        loadedB = null;
        addMapArtifacts(map);
        setLayer({
          status: 'ready',
          workspaceA: renderRef.current.workspaceA,
          workspaceB: renderRef.current.workspaceB,
          cacheA: renderRef.current.cacheA,
          cacheB: renderRef.current.cacheB,
          errorStatus: null,
        });
      } catch (error) {
        if (isAbortError(error) || generation !== generationRef.current || !mountedRef.current) return;
        removeMapArtifacts(map);
        disposeRender();
        setLayer({ status: 'error', workspaceA: null, workspaceB: null, cacheA: null, cacheB: null, errorStatus: safeErrorStatus(error) });
      } finally {
        [loadedA?.url, loadedB?.url].filter(Boolean).forEach((url) => URL.revokeObjectURL(url));
      }
    }
    load();
    return () => controller.abort();
  }, [addMapArtifacts, comparisonEnabled, disposeRender, enabled, fieldId, map, removeMapArtifacts, retryKey, sceneA, sceneB]);

  useEffect(() => {
    const current = renderRef.current;
    if (!comparisonEnabled) {
      try {
        if (map?.getLayer(PIXEL_NDVI_LAYER_B)) map.removeLayer(PIXEL_NDVI_LAYER_B);
        if (map?.getSource(PIXEL_NDVI_SOURCE_B)) map.removeSource(PIXEL_NDVI_SOURCE_B);
      } catch (_) {}
      return undefined;
    }
    if (!current.imageB || !current.workspaceB) return undefined;
    const clipGeneration = ++clipGenerationRef.current;
    clippedImageUrl(current.imageB, divider).then((nextUrl) => {
      if (clipGeneration !== clipGenerationRef.current || !mountedRef.current) {
        URL.revokeObjectURL(nextUrl);
        return;
      }
      const previous = renderRef.current.clippedUrl;
      renderRef.current.clippedUrl = nextUrl;
      try {
        const source = map?.getSource(PIXEL_NDVI_SOURCE_B);
        if (source?.updateImage) {
          source.updateImage({ url: nextUrl, coordinates: current.workspaceB.corners });
        } else {
          addMapArtifacts(map);
        }
      } catch (_) {
        addMapArtifacts(map);
      }
      if (previous) URL.revokeObjectURL(previous);
    }).catch(() => {});
    return () => { ++clipGenerationRef.current; };
  }, [addMapArtifacts, comparisonEnabled, divider, map]);

  useEffect(() => {
    opacityRef.current = opacity;
    for (const layerId of [PIXEL_NDVI_LAYER_A, PIXEL_NDVI_LAYER_B]) {
      try {
        if (map?.getLayer(layerId)) map.setPaintProperty(layerId, 'raster-opacity', opacity);
      } catch (_) {}
    }
  }, [map, opacity]);

  useEffect(() => {
    if (!map || !enabled || layer.status !== 'ready' || !sceneA) return undefined;
    const handleClick = (event) => {
      const longitude = event?.lngLat?.lng;
      const latitude = event?.lngLat?.lat;
      if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return;
      sampleControllerRef.current?.abort();
      const controller = new AbortController();
      sampleControllerRef.current = controller;
      setSample({ status: 'loading', value: { longitude, latitude }, errorStatus: null });
      samplePixelNDVI(fieldId, sceneA, longitude, latitude, controller.signal)
        .then((value) => {
          if (!controller.signal.aborted && mountedRef.current) setSample({ status: 'ready', value, errorStatus: null });
        })
        .catch((error) => {
          if (!isAbortError(error) && mountedRef.current) {
            setSample({ status: 'error', value: { longitude, latitude }, errorStatus: safeErrorStatus(error) });
          }
        });
    };
    map.on('click', handleClick);
    return () => {
      map.off('click', handleClick);
      sampleControllerRef.current?.abort();
    };
  }, [enabled, fieldId, layer.status, map, sceneA]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      ++generationRef.current;
      ++clipGenerationRef.current;
      catalogControllerRef.current?.abort();
      layerControllerRef.current?.abort();
      sampleControllerRef.current?.abort();
      removeMapArtifacts(map);
      disposeRender();
    };
  }, [disposeRender, map, removeMapArtifacts]);

  return {
    catalog,
    layer,
    sample,
    sceneA,
    sceneB,
    setSceneA,
    setSceneB,
    retry: () => setRetryKey((value) => value + 1),
  };
}
