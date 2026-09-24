import { offlineScope, purgeOfflineScope } from '../offline/offlineScoutingStore.js';

// The web session has three distinct ends, deliberately kept apart:
//   A. invalidateSession()   involuntary authentication loss (401, expired or
//                            revoked token, failed login completion). Clears the
//                            credential only; offline scouting data is never touched.
//   B. logoutExplicitly()    a user-initiated logout. Removes the current user's
//                            own offline partition (product contract), never another's.
//   C. beginSession()        a (re)authenticated user; when the identity differs
//                            from the previous one, user-scoped caches are dropped.

export const SESSION_TOKEN_KEY = 'agrosat_token';
export const SESSION_USER_KEY = 'agrosat_user';
export const SESSION_INVALIDATED_EVENT = 'agrosat:session-invalidated';
// Historical name kept for in-memory consumers (FieldMap) that drop business
// state whenever any session ends.
export const SESSION_ENDED_EVENT = 'agrosat:logout';

const cacheClearers = new Set();

function storage() {
  try {
    return globalThis.localStorage || null;
  } catch {
    return null;
  }
}

function dispatch(name, detail) {
  if (typeof window === 'undefined' || typeof window.dispatchEvent !== 'function') return;
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

export function readSessionToken() {
  try {
    return storage()?.getItem(SESSION_TOKEN_KEY) || null;
  } catch {
    return null;
  }
}

export function writeSessionToken(token) {
  storage()?.setItem(SESSION_TOKEN_KEY, token);
}

export function writeSessionUser(user) {
  storage()?.setItem(SESSION_USER_KEY, JSON.stringify(user));
}

/** In-memory response caches register here; they hold one user's business data. */
export function registerUserScopedCacheClearer(clear) {
  cacheClearers.add(clear);
  return () => cacheClearers.delete(clear);
}

export function clearUserScopedCaches() {
  for (const clear of cacheClearers) {
    try {
      clear();
    } catch {
      // A cache that cannot be cleared must not block the session transition.
    }
  }
}

export function sameIdentity(left, right) {
  return Boolean(left && right)
    && String(left.id) === String(right.id)
    && String(left.enterprise_id ?? '') === String(right.enterprise_id ?? '')
    && String(left.role ?? '') === String(right.role ?? '');
}

/**
 * A. Involuntary authentication loss.
 *
 * `failedToken` is the credential the failing request actually carried. When a
 * newer credential has been stored since, the failure belongs to an older
 * session and nothing is cleared. IndexedDB is never touched here.
 */
export function invalidateSession({ reason = 'authentication_lost', failedToken } = {}) {
  const current = readSessionToken();
  if (failedToken !== undefined && current && current !== failedToken) return false;
  try {
    storage()?.removeItem(SESSION_TOKEN_KEY);
    storage()?.removeItem(SESSION_USER_KEY);
  } catch {
    // Storage may be unavailable (private mode); the in-memory session still ends.
  }
  clearUserScopedCaches();
  dispatch(SESSION_INVALIDATED_EVENT, { reason });
  dispatch(SESSION_ENDED_EVENT, { reason });
  return true;
}

/**
 * B. Explicit, user-initiated logout: removes the current user's own offline
 * partition, then ends the session. Another user's partition is never removed.
 */
export async function logoutExplicitly(user) {
  const scope = offlineScope(user);
  let purged = null;
  if (scope) {
    try {
      purged = await purgeOfflineScope(scope);
    } catch {
      purged = { failed: true };
    }
  }
  invalidateSession({ reason: 'explicit_logout' });
  return purged;
}

/**
 * C. A successfully authenticated user. Returns true when the identity differs
 * from `previousUser`, in which case user-scoped caches are dropped first.
 */
export function beginSession(token, user, previousUser) {
  const changed = !sameIdentity(previousUser, user);
  if (changed) clearUserScopedCaches();
  writeSessionToken(token);
  writeSessionUser(user);
  return changed;
}
