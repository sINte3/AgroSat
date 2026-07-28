import client from './client';

export async function getFieldTileMetadata({ enterpriseId, signal } = {}) {
  const { data } = await client.get('field-tiles/metadata', {
    params: enterpriseId ? { enterprise_id: enterpriseId } : {},
    signal,
  });
  return data;
}

export function validFieldTileMetadata(metadata) {
  const bounds = metadata?.bounds;
  const boundsValid = bounds === null || (
    Array.isArray(bounds)
    && bounds.length === 4
    && bounds.every(Number.isFinite)
    && bounds[0] < bounds[2]
    && bounds[1] < bounds[3]
  );
  return metadata?.schema_version === 'task209_field_mvt_v1'
    && metadata?.source_layer === 'fields'
    && typeof metadata?.tile_template === 'string'
    && metadata.tile_template.startsWith('/api/field-tiles/')
    && Number.isInteger(metadata?.min_zoom)
    && Number.isInteger(metadata?.max_zoom)
    && metadata.min_zoom <= metadata.max_zoom
    && Number.isInteger(metadata?.field_count)
    && metadata.field_count >= 0
    && boundsValid;
}

export function resolveFieldTileTemplate(metadata) {
  if (!validFieldTileMetadata(metadata) || typeof window === 'undefined') {
    throw new Error('Invalid field tile metadata');
  }
  const target = new URL(metadata.tile_template, window.location.origin);
  if (
    target.origin !== window.location.origin
    || !target.pathname.startsWith('/api/field-tiles/')
  ) {
    throw new Error('Invalid field tile origin');
  }
  // URL serialisation percent-encodes MapLibre's {z}/{x}/{y} placeholders.
  // The validated server-relative template can be joined without rewriting it.
  return `${window.location.origin}${metadata.tile_template}`;
}
