import { useCallback, useEffect, useRef, useState } from 'react';
import { getRasterImage, getRasterMetadata } from '../api/raster';

export const NDVI_RASTER_SOURCE_ID = 'agrosat-field-raster-source';
export const NDVI_RASTER_LAYER_ID = 'agrosat-field-raster-layer';

function isAbortError(error) {
  return error?.name === 'AbortError' || error?.name === 'CanceledError' || error?.code === 'ERR_CANCELED';
}

function validMetadata(metadata) {
  const [west, south, east, north] = metadata?.bbox || [];
  return [west, south, east, north].every(Number.isFinite) && west < east && south < north
    && Number.isFinite(metadata?.default_size) && metadata.default_size > 0
    && typeof metadata?.observation_date === 'string';
}

export default function useNDVIRasterLayer({
  map,
  fieldId,
  enabled,
  dateTo,
  opacity,
  indexCode = 'ndvi',
}) {
  const [state, setState] = useState({ status: 'idle', metadata: null, cacheState: null, errorStatus: null });
  const mountedRef = useRef(true);
  const generationRef = useRef(0);
  const controllerRef = useRef(null);
  const objectUrlRef = useRef(null);
  const metadataRef = useRef(null);
  const styleLoadHandlerRef = useRef(null);
  const enabledRef = useRef(enabled);
  const opacityRef = useRef(opacity);
  const retryRef = useRef(0);
  const [retryKey, setRetryKey] = useState(0);

  const clearRaster = useCallback((targetMap = map, { revokeUrl = true } = {}) => {
    if (targetMap && !targetMap._removed) {
      try {
        if (targetMap.getLayer(NDVI_RASTER_LAYER_ID)) targetMap.removeLayer(NDVI_RASTER_LAYER_ID);
        if (targetMap.getSource(NDVI_RASTER_SOURCE_ID)) targetMap.removeSource(NDVI_RASTER_SOURCE_ID);
      } catch (_) {
        // A concurrent style replacement may have already discarded the custom artifacts.
      }
    }
    if (revokeUrl && objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
    if (revokeUrl) metadataRef.current = null;
  }, [map]);

  const addRaster = useCallback((targetMap = map) => {
    const metadata = metadataRef.current;
    const url = objectUrlRef.current;
    if (!targetMap || targetMap._removed || !targetMap.isStyleLoaded?.() || !url || !validMetadata(metadata)) return;
    const [west, south, east, north] = metadata.bbox;
    try {
      if (targetMap.getLayer(NDVI_RASTER_LAYER_ID)) targetMap.removeLayer(NDVI_RASTER_LAYER_ID);
      if (targetMap.getSource(NDVI_RASTER_SOURCE_ID)) targetMap.removeSource(NDVI_RASTER_SOURCE_ID);
      targetMap.addSource(NDVI_RASTER_SOURCE_ID, {
        type: 'image',
        url,
        coordinates: [[west, north], [east, north], [east, south], [west, south]],
      });
      targetMap.addLayer({
        id: NDVI_RASTER_LAYER_ID,
        type: 'raster',
        source: NDVI_RASTER_SOURCE_ID,
        paint: { 'raster-opacity': opacityRef.current },
      }, targetMap.getLayer('fields-fill') ? 'fields-fill' : undefined);
    } catch (_) {
      clearRaster(targetMap);
    }
  }, [clearRaster, map]);

  useEffect(() => {
    enabledRef.current = enabled;
  }, [enabled]);

  useEffect(() => {
    opacityRef.current = opacity;
  }, [opacity]);

  useEffect(() => {
    if (!map) return undefined;
    const onStyleLoad = () => {
      if (mountedRef.current && enabledRef.current && objectUrlRef.current && metadataRef.current) addRaster(map);
    };
    styleLoadHandlerRef.current = onStyleLoad;
    map.on('style.load', onStyleLoad);
    return () => {
      map.off('style.load', onStyleLoad);
      if (styleLoadHandlerRef.current === onStyleLoad) styleLoadHandlerRef.current = null;
    };
  }, [addRaster, map]);

  useEffect(() => {
    mountedRef.current = true;
    const generation = ++generationRef.current;
    if (controllerRef.current) controllerRef.current.abort();
    controllerRef.current = null;
    clearRaster(map);

    if (!enabled || !map || !fieldId) {
      if (mountedRef.current) setState({ status: 'idle', metadata: null, cacheState: null, errorStatus: null });
      return undefined;
    }

    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ status: 'loading', metadata: null, cacheState: null, errorStatus: null });

    async function loadRaster() {
      let createdUrl = null;
      try {
        const metadata = await getRasterMetadata(fieldId, {
          indexCode,
          dateTo,
          signal: controller.signal,
        });
        if (generation !== generationRef.current || !mountedRef.current || controller.signal.aborted || !validMetadata(metadata)) {
          if (!validMetadata(metadata)) throw new Error('Invalid raster metadata');
          return;
        }
        const { blob, cacheState } = await getRasterImage(fieldId, {
          indexCode,
          observationDate: metadata.observation_date,
          size: metadata.default_size,
          signal: controller.signal,
        });
        if (generation !== generationRef.current || !mountedRef.current || controller.signal.aborted) return;
        if (!(blob instanceof Blob) || blob.size === 0 || !/^image\/png(?:;|$)/i.test(blob.type)) {
          throw new Error('Invalid raster image');
        }
        createdUrl = URL.createObjectURL(blob);
        objectUrlRef.current = createdUrl;
        metadataRef.current = metadata;
        addRaster(map);
        if (generation === generationRef.current && mountedRef.current) {
          setState({ status: 'ready', metadata, cacheState, errorStatus: null });
        }
      } catch (error) {
        if (isAbortError(error) || generation !== generationRef.current || !mountedRef.current) return;
        if (createdUrl && objectUrlRef.current !== createdUrl) URL.revokeObjectURL(createdUrl);
        clearRaster(map);
        const status = error?.response?.status;
        setState({ status: 'error', metadata: null, cacheState: null, errorStatus: status || 'invalid' });
      }
    }
    loadRaster();

    return () => {
      if (controllerRef.current === controller) controllerRef.current = null;
      controller.abort();
      if (generation === generationRef.current) clearRaster(map);
    };
  }, [addRaster, clearRaster, dateTo, enabled, fieldId, indexCode, map, retryKey]);

  useEffect(() => {
    if (!map || !map.isStyleLoaded?.() || !map.getLayer(NDVI_RASTER_LAYER_ID)) return;
    try {
      map.setPaintProperty(NDVI_RASTER_LAYER_ID, 'raster-opacity', opacity);
    } catch (_) {}
  }, [map, opacity]);

  useEffect(() => () => {
    mountedRef.current = false;
    ++generationRef.current;
    controllerRef.current?.abort();
    clearRaster(map);
  }, [clearRaster, map]);

  return {
    ...state,
    retry: () => {
      retryRef.current += 1;
      setRetryKey(retryRef.current);
    },
  };
}
