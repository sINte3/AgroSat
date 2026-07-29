export function registerOfflineShell() {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return;
  const register = () => {
    void navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {
      // Offline shell is an enhancement; inspection writes remain explicit and server-validated.
    });
  };
  if (document.readyState === 'complete') register();
  else window.addEventListener('load', register, { once: true });
}
