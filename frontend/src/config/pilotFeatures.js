export const FIRST_PILOT_FEATURES = Object.freeze({
  // Wialon is deferred to the next pilot wave (Integrated Operations).
  // Undefined is deliberately false for release-candidate builds.
  wialon: import.meta.env?.VITE_WIALON_ENABLED === 'true',
});
