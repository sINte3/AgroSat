export function isAbortError(error) {
  return error?.name === 'AbortError'
    || error?.name === 'CanceledError'
    || error?.code === 'ERR_CANCELED';
}

export function chooseInitialScenes(scenes) {
  const available = Array.isArray(scenes) ? scenes.filter((scene) => scene?.raster_available) : [];
  return {
    sceneA: available[0]?.scene_id || null,
    sceneB: available[1]?.scene_id || available[0]?.scene_id || null,
  };
}

export function clampDivider(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 50;
  return Math.max(5, Math.min(95, Math.round(numeric)));
}

export function validWorkspace(workspace) {
  const corners = workspace?.corners;
  return workspace?.schema_version === 'program_r3_pixel_ndvi_v1'
    && Array.isArray(corners)
    && corners.length === 4
    && corners.every((corner) => Array.isArray(corner) && corner.length === 2 && corner.every(Number.isFinite))
    && Number.isFinite(workspace?.width)
    && Number.isFinite(workspace?.height);
}

export function safeErrorStatus(error) {
  const status = Number(error?.response?.status);
  return Number.isInteger(status) ? status : 'invalid';
}
