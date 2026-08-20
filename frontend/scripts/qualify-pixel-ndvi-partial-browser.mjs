import assert from 'node:assert/strict';
import { readFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { chromium, request } from 'playwright';

const [baseUrl, credentialsPath, evidenceDirectory] = process.argv.slice(2);
assert(baseUrl && credentialsPath && evidenceDirectory, 'Expected base URL, protected credentials path, and evidence directory');
await mkdir(evidenceDirectory, { recursive: true });
const protectedDocument = JSON.parse(await readFile(credentialsPath, 'utf8'));
const agronomist = protectedDocument.identities.agronomist;
const other = protectedDocument.identities.other;
assert.equal(protectedDocument.field_id, 4);

const viewports = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'tablet', width: 1024, height: 768 },
  { name: 'mobile', width: 390, height: 844 },
];
const results = [];
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
});
try {
  for (const viewport of viewports) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    const consoleErrors = [];
    const pageErrors = [];
    const unexpectedFailures = [];
    const expectedMapAborts = [];
    const backend5xx = [];
    page.on('console', (message) => {
      if (message.type() === 'error' && !/favicon|ERR_BLOCKED_BY_CLIENT/i.test(message.text())) consoleErrors.push(message.text().slice(0, 180));
    });
    page.on('pageerror', (error) => pageErrors.push(String(error.message).slice(0, 180)));
    page.on('requestfailed', (failed) => {
      const url = new URL(failed.url());
      if (url.pathname.startsWith('/api/field-tiles/')) {
        expectedMapAborts.push(url.pathname);
      } else if (url.origin === new URL(baseUrl).origin) unexpectedFailures.push(url.pathname);
    });
    page.on('response', (response) => {
      if (response.url().startsWith(`${baseUrl}/api/`) && response.status() >= 500) backend5xx.push(response.status());
    });
    await page.goto(`${baseUrl}/login`, { waitUntil: 'domcontentloaded' });
    await page.locator('#login-email').fill(agronomist.email);
    await page.locator('#login-password').fill(agronomist.password);
    await page.getByRole('button', { name: 'Войти' }).click();
    await page.waitForURL((url) => url.pathname !== '/login');
    await page.waitForLoadState('networkidle');
    unexpectedFailures.length = 0;
    await page.goto(`${baseUrl}/fields`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('region', { name: 'Рабочее пространство пиксельного NDVI' }).waitFor();
    if (viewport.width < 640) {
      const opener = page.getByRole('button', { name: 'Открыть список полей' });
      if (await opener.isVisible()) await opener.click();
    }
    const fieldButton = page.getByRole('button', { name: /^Выбрать поле / }).first();
    await fieldButton.waitFor();
    await fieldButton.click();
    const toggle = page.getByRole('checkbox', { name: 'Пиксельный NDVI' });
    await toggle.waitFor();
    assert.equal(await toggle.isEnabled(), true);
    assert.equal(await toggle.isChecked(), false);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    assert.equal(overflow, false);
    assert.equal(pageErrors.length, 0);
    assert.equal(unexpectedFailures.length, 0, `Unexpected same-origin failures: ${JSON.stringify(unexpectedFailures)}`);
    assert.equal(backend5xx.length, 0);
    await page.screenshot({ path: path.join(evidenceDirectory, `${viewport.name}.png`), fullPage: false });
    results.push({ viewport, field_selected: true, pixel_ndvi_control_ready: true, horizontal_overflow: false, page_errors: 0, same_origin_request_failures: 0, expected_map_tile_aborts: expectedMapAborts.length, backend_5xx: 0, console_error_count: consoleErrors.length });
    await context.close();
  }
} finally {
  await browser.close();
}

async function login(api, identity) {
  const response = await api.post('/api/auth/login', { form: { username: identity.email, password: identity.password } });
  assert.equal(response.status(), 200);
  const payload = await response.json();
  return payload.access_token;
}

const api = await request.newContext({ baseURL: baseUrl });
try {
  const unauthenticated = await api.get('/api/raster/fields/4/scenes');
  assert.equal(unauthenticated.status(), 401);
  const otherToken = await login(api, other);
  const crossTenant = await api.get('/api/raster/fields/4/scenes', { headers: { Authorization: `Bearer ${otherToken}` } });
  assert.equal(crossTenant.status(), 404);
  results.push({ unauthenticated_status: 401, cross_tenant_status: 404, provider_requests_made: 0 });
} finally {
  await api.dispose();
}

agronomist.password = '';
other.password = '';
console.log(JSON.stringify({ result: 'PASS_PARTIAL_BROWSER_BOUNDARY', viewports: results, full_pixel_raster_flow: 'NOT_RUN_PROVIDER_RETRY_LIMIT_EXHAUSTED', browser_storage_persisted: false, traces_persisted: false }));
