const OFFLINE_SESSION_ROLES = new Set(['admin', 'manager', 'agronomist', 'viewer']);

const positiveId = (value) => {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
};

export function readCachedActiveUser(storage = globalThis.localStorage) {
  if (!storage?.getItem) return null;
  let value;
  try {
    value = JSON.parse(storage.getItem('agrosat_user') || 'null');
  } catch {
    return null;
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const id = positiveId(value.id);
  const role = String(value.role || '').trim().toLowerCase();
  const enterpriseId = value.enterprise_id == null ? null : positiveId(value.enterprise_id);
  if (!id || !OFFLINE_SESSION_ROLES.has(role) || value.is_active === false) return null;
  if (role !== 'admin' && !enterpriseId) return null;
  return { ...value, id, role, enterprise_id: enterpriseId };
}

export function isTransientSessionFailure(error) {
  return !error?.response;
}

export function isSessionRevoked(error) {
  return [401, 403].includes(Number(error?.response?.status || 0));
}
