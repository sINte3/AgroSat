import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import apiClient, { loginWithPassword } from '../api/client';
import {
  beginSession,
  clearUserScopedCaches,
  invalidateSession,
  logoutExplicitly,
  readSessionToken,
  sameIdentity,
  SESSION_ENDED_EVENT,
  SESSION_INVALIDATED_EVENT,
  SESSION_TOKEN_KEY,
  writeSessionToken,
  writeSessionUser,
} from '../auth/session.js';
import {
  isSessionRevoked,
  isTransientSessionFailure,
  readCachedActiveUser,
} from '../offline/offlineSession.js';

const AuthContext = createContext(null);

/** 'credentials' | 'disabled' | 'unavailable' for a failed login attempt. */
export function classifyLoginFailure(error, { credentialsRequest = true } = {}) {
  const status = Number(error?.response?.status || 0);
  if (status === 401 && credentialsRequest) return 'credentials';
  if (status === 403) return 'disabled';
  return 'unavailable';
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(() => readSessionToken());
  const [loading, setLoading] = useState(true);
  const tokenRef = useRef(token);
  const userRef = useRef(user);
  const validatedTokenRef = useRef(null);

  useEffect(() => { tokenRef.current = token; }, [token]);
  useEffect(() => { userRef.current = user; }, [user]);

  const dropSessionState = useCallback(() => {
    tokenRef.current = null;
    validatedTokenRef.current = null;
    setUser(null);
    setToken(null);
    setLoading(false);
  }, []);

  // A. Involuntary authentication loss ends the React session state. Storage
  // and caches are handled by invalidateSession(); offline scouting drafts are
  // never purged on this path.
  useEffect(() => {
    window.addEventListener(SESSION_INVALIDATED_EVENT, dropSessionState);
    return () => window.removeEventListener(SESSION_INVALIDATED_EVENT, dropSessionState);
  }, [dropSessionState]);

  // B. Explicit, user-initiated logout.
  const logout = useCallback(async () => {
    await logoutExplicitly(userRef.current);
  }, []);

  const login = useCallback(async (email, password) => {
    let accessToken = null;
    try {
      const tokenData = await loginWithPassword(email, password);
      accessToken = tokenData?.access_token || null;
    } catch (error) {
      return { ok: false, reason: classifyLoginFailure(error) };
    }
    if (!accessToken) return { ok: false, reason: 'unavailable' };

    try {
      writeSessionToken(accessToken);
      const res = await apiClient.get('auth/me');
      // C. A different identity drops user-scoped caches before its data loads.
      beginSession(accessToken, res.data, userRef.current || readCachedActiveUser());
      tokenRef.current = accessToken;
      validatedTokenRef.current = accessToken;
      setUser(res.data);
      setToken(accessToken);
      return { ok: true };
    } catch (error) {
      // The credential was issued but the session could not be completed: drop
      // that credential only (a 401 has already done so). Nothing else.
      invalidateSession({ reason: 'login_incomplete', failedToken: accessToken });
      return { ok: false, reason: classifyLoginFailure(error, { credentialsRequest: false }) };
    }
  }, []);

  const refreshUser = useCallback(async () => {
    const current = tokenRef.current;
    if (!current) return false;
    try {
      const res = await apiClient.get('auth/me');
      if (tokenRef.current !== current) return false;
      // C. The same credential now resolves to another identity or scope.
      if (userRef.current && !sameIdentity(userRef.current, res.data)) clearUserScopedCaches();
      writeSessionUser(res.data);
      validatedTokenRef.current = current;
      setUser(res.data);
      return true;
    } catch (error) {
      if (tokenRef.current !== current) return false;
      const cachedUser = readCachedActiveUser();
      if (isTransientSessionFailure(error) && cachedUser) {
        setUser(cachedUser);
        return false;
      }
      if (isSessionRevoked(error) || !cachedUser) {
        // A 401 has already ended the session in the interceptor; a 403
        // (inactive user, unknown role) ends it here. Offline drafts are kept.
        invalidateSession({ reason: 'revalidation_failed', failedToken: current });
      }
      return false;
    }
  }, []);

  useEffect(() => {
    let active = true;
    if (!token) {
      setLoading(false);
      return undefined;
    }
    if (validatedTokenRef.current === token) {
      setLoading(false);
      return undefined;
    }
    setLoading(true);
    refreshUser().finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [token, refreshUser]);

  useEffect(() => {
    const handleOnline = () => { void refreshUser(); };
    // Cross-tab changes never purge offline data. Another tab ending the
    // session ends it here too; another credential rebuilds this tab under it.
    const handleStorageChange = (event) => {
      if (event.key !== null && event.key !== SESSION_TOKEN_KEY) return;
      const next = readSessionToken();
      const current = tokenRef.current;
      if (next === current) return;
      clearUserScopedCaches();
      if (!next) {
        dropSessionState();
        window.dispatchEvent(new CustomEvent(SESSION_ENDED_EVENT, { detail: { reason: 'other_tab' } }));
        return;
      }
      if (!current) {
        tokenRef.current = next;
        setToken(next);
        return;
      }
      window.location.reload();
    };
    window.addEventListener('storage', handleStorageChange);
    window.addEventListener('online', handleOnline);
    return () => {
      window.removeEventListener('storage', handleStorageChange);
      window.removeEventListener('online', handleOnline);
    };
  }, [dropSessionState, refreshUser]);

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        loading,
        login,
        logout,
        revalidateSession: refreshUser,
        isAuthenticated: Boolean(token && user),
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }

  return context;
}
