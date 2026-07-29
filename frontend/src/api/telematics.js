import client from './client';


export async function getFieldTelematics(
  fieldId,
  { hours = 24, limit = 50 } = {},
  signal,
) {
  const response = await client.get(`telematics/fields/${fieldId}`, {
    params: { hours, limit },
    signal,
  });
  return response.data;
}
