import client from './client';

export async function getAgronomicInterpretation(fieldId, params, signal) {
  const { data } = await client.get(`agronomic-interpretation/fields/${fieldId}`, {
    params,
    signal,
  });
  return data;
}
