import { existsSync, readdirSync } from 'node:fs';
import path from 'node:path';

// Chromium for local qualification: an explicit executable, else the newest
// Playwright-cached Chromium, else Playwright's default resolution.
export function chromiumExecutable() {
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE) return process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
  const cache = process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, 'ms-playwright') : null;
  if (!cache || !existsSync(cache)) return undefined;
  const candidates = readdirSync(cache)
    .filter((name) => /^chromium-\d+$/.test(name))
    .sort((left, right) => Number(right.split('-')[1]) - Number(left.split('-')[1]))
    .map((name) => path.join(cache, name, 'chrome-win64', 'chrome.exe'))
    .filter((candidate) => existsSync(candidate));
  return candidates[0];
}

// 1×1 transparent PNG.
export const PNG_BYTES = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
);
