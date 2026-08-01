import React, { createContext, useContext, useState, useEffect } from 'react';
import apiClient, { loginWithPassword } from '../api/client';
import { purgeOfflineScoutingData } from '../offline/offlineScoutingStore.js';
import {
  isSessionRevoked,
  isTransientSessionFailure,
  readCachedActiveUser,
} from '../offline/offlineSession.js';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(localStorage.getItem('agrosat_token'));
  const [loading, setLoading] = useState(true);

  const logout = () => {
    void purgeOfflineScoutingData().catch(() => {});
    localStorage.removeItem('agrosat_token');
    localStorage.removeItem('agrosat_user');
    setUser(null);
    setToken(null);
    window.dispatchEvent(new Event('agrosat:logout'));
  };

  const login = async (email, password) => {
    try {
      const tokenData = await loginWithPassword(email, password);
      const accessToken = tokenData.access_token;

      localStorage.setItem('agrosat_token', accessToken);

      const res = await apiClient.get('auth/me');

      setUser(res.data);
      setToken(accessToken);
      localStorage.setItem('agrosat_user', JSON.stringify(res.data));

      return true;
    } catch {
      logout();
      return false;
    }
  };

  const revalidateSession = async () => {
    if (!token) return;

    try {
      const res = await apiClient.get('auth/me');
      setUser(res.data);
      localStorage.setItem('agrosat_user', JSON.stringify(res.data));
      return true;
    } catch (error) {
      const cachedUser = readCachedActiveUser();
      if (isTransientSessionFailure(error) && cachedUser) {
        setUser(cachedUser);
        return false;
      }
      if (isSessionRevoked(error) || !cachedUser) logout();
      return false;
    }
  };

  useEffect(() => {
    const initAuth = async () => {
      if (!token) {
        setLoading(false);
        return;
      }

      try {
        const res = await apiClient.get('auth/me');
        setUser(res.data);
        localStorage.setItem('agrosat_user', JSON.stringify(res.data));
      } catch (error) {
        const cachedUser = readCachedActiveUser();
        if (isTransientSessionFailure(error) && cachedUser) {
          setUser(cachedUser);
        } else {
          logout();
        }
      } finally {
        setLoading(false);
      }
    };

    initAuth();

    const handleStorageChange = (event) => {
      if (event.key !== 'agrosat_token') return;

      const newToken = event.newValue;

      if (!newToken || newToken !== token) {
        logout();
        window.location.href = '/login';
      }
    };

    window.addEventListener('storage', handleStorageChange);
    const handleOnline = () => {
      void revalidateSession();
    };
    window.addEventListener('online', handleOnline);
    return () => {
      window.removeEventListener('storage', handleStorageChange);
      window.removeEventListener('online', handleOnline);
    };
  }, [token]);

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        loading,
        login,
        logout,
        revalidateSession,
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
