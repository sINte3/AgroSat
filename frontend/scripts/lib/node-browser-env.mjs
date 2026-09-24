import { register } from 'node:module';

// Minimal browser globals for importing the API layer in Node contract tests.
// Every storage and IndexedDB access is recorded so tests can prove what an
// authentication failure does and does not touch.

register(new URL('./extensionless-loader.mjs', import.meta.url));

export class MemoryStorage {
  constructor() { this.map = new Map(); this.log = []; }
  getItem(key) { this.log.push(['get', key]); return this.map.has(key) ? this.map.get(key) : null; }
  setItem(key, value) { this.log.push(['set', key]); this.map.set(key, String(value)); }
  removeItem(key) { this.log.push(['remove', key]); this.map.delete(key); }
  clear() { this.log.push(['clear']); this.map.clear(); }
  get length() { return this.map.size; }
  key(index) { return Array.from(this.map.keys())[index] ?? null; }
}

export function installBrowserEnvironment() {
  const indexedDbCalls = [];
  const events = [];
  const target = new EventTarget();
  const originalDispatch = target.dispatchEvent.bind(target);
  const windowObject = {
    location: { origin: 'http://agrosat.test', pathname: '/dashboard', href: 'http://agrosat.test/dashboard' },
    addEventListener: target.addEventListener.bind(target),
    removeEventListener: target.removeEventListener.bind(target),
    dispatchEvent(event) {
      events.push({ type: event.type, detail: event.detail ?? null });
      return originalDispatch(event);
    },
    indexedDB: {
      open(...args) { indexedDbCalls.push(['open', ...args]); throw new Error('IndexedDB must not be touched here'); },
      deleteDatabase(...args) { indexedDbCalls.push(['deleteDatabase', ...args]); throw new Error('IndexedDB must not be deleted here'); },
    },
  };
  globalThis.window = windowObject;
  globalThis.localStorage = new MemoryStorage();
  globalThis.sessionStorage = new MemoryStorage();
  return { window: windowObject, events, indexedDbCalls };
}
