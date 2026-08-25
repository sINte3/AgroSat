import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium, request } from 'playwright';
import axe from 'axe-core';

const [baseUrl, evidenceDirectory] = process.argv.slice(2);
assert(baseUrl && evidenceDirectory, 'Expected base URL and evidence directory');
await mkdir(evidenceDirectory, { recursive: true });
const credentials = {
  admin: { username: process.env.TASK219_BROWSER_ADMIN_USERNAME, password: process.env.TASK219_BROWSER_ADMIN_PASSWORD },
  tenantA: { username: process.env.TASK219_BROWSER_TENANT_A_USERNAME, password: process.env.TASK219_BROWSER_TENANT_PASSWORD },
  tenantB: { username: process.env.TASK219_BROWSER_TENANT_B_USERNAME, password: process.env.TASK219_BROWSER_TENANT_PASSWORD },
};
for (const identity of Object.values(credentials)) assert(identity.username && identity.password, 'Protected browser credential missing');
delete process.env.TASK219_BROWSER_ADMIN_USERNAME;
delete process.env.TASK219_BROWSER_ADMIN_PASSWORD;
delete process.env.TASK219_BROWSER_TENANT_A_USERNAME;
delete process.env.TASK219_BROWSER_TENANT_B_USERNAME;
delete process.env.TASK219_BROWSER_TENANT_PASSWORD;

async function loginApi(api, identity) {
  const response = await api.post('/api/auth/login', { form: identity });
  assert.equal(response.status(), 200);
  return (await response.json()).access_token;
}

const apiResults = {};
const api = await request.newContext({ baseURL: baseUrl });
try {
  assert.equal((await api.get('/api/monitoring/status')).status(), 401);
  const adminToken = await loginApi(api, credentials.admin);
  const tenantAToken = await loginApi(api, credentials.tenantA);
  const tenantBToken = await loginApi(api, credentials.tenantB);
  const auth = token => ({ Authorization: `Bearer ${token}` });
  const status = await api.get('/api/monitoring/status', { headers: auth(adminToken) });
  assert.equal(status.status(), 200);
  const statusBody = await status.json();
  assert.equal(statusBody.provider_affects_web_readiness, false);
  assert.equal(statusBody.latest_run.provider_status, 'degraded');
  assert.equal(statusBody.latest_run.failure_category, 'auth');
  const ready = await api.get('/health/ready');
  assert.equal(ready.status(), 200);
  const aFreshness = await api.get('/api/monitoring/freshness?limit=1000', { headers: auth(tenantAToken) });
  const bFreshness = await api.get('/api/monitoring/freshness?limit=1000', { headers: auth(tenantBToken) });
  assert.equal(aFreshness.status(), 200);
  assert.equal(bFreshness.status(), 200);
  const aItems = (await aFreshness.json()).items;
  const bItems = (await bFreshness.json()).items;
  assert(aItems.length > 0 && bItems.length > 0);
  const enterpriseA = aItems[0].enterprise_id;
  const enterpriseB = bItems[0].enterprise_id;
  assert.notEqual(enterpriseA, enterpriseB);
  assert(aItems.every(item => item.enterprise_id === enterpriseA));
  assert(bItems.every(item => item.enterprise_id === enterpriseB));
  assert.equal((await api.get(`/api/monitoring/freshness?enterprise_id=${enterpriseB}`, { headers: auth(tenantAToken) })).status(), 404);
  assert.equal((await api.get(`/api/monitoring/candidates?enterprise_id=${enterpriseB}`, { headers: auth(tenantAToken) })).status(), 404);
  assert.equal((await api.get('/api/monitoring/status', { headers: auth(tenantAToken) })).status(), 403);
  assert.equal((await api.post('/api/monitoring/candidates/999999/transition', { headers: auth(tenantAToken), data: { action: 'confirm', reason: 'tenant boundary', expected_version: 1 } })).status(), 403);
  apiResults.unauthenticated = 401;
  apiResults.admin_status = 200;
  apiResults.web_ready_during_provider_degradation = 200;
  apiResults.tenant_cross_scope = 404;
  apiResults.tenant_operational_status = 403;
  apiResults.tenant_write = 403;
  apiResults.enterprise_a_freshness_rows = aItems.length;
  apiResults.enterprise_b_freshness_rows = bItems.length;
} finally {
  await api.dispose();
}

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
    const diagnostics = { consoleErrors: [], pageErrors: [], requestFailures: [], expectedNavigationAborts: [], backend5xx: [] };
    page.on('console', message => {
      if (message.type() === 'error' && !/favicon|tile\.openstreetmap|ERR_BLOCKED_BY_CLIENT/i.test(message.text())) diagnostics.consoleErrors.push(message.text().slice(0, 240));
    });
    page.on('pageerror', error => diagnostics.pageErrors.push(String(error.message).slice(0, 240)));
    page.on('requestfailed', failed => {
      const url = new URL(failed.url());
      if (url.origin !== new URL(baseUrl).origin) return;
      if (/ERR_ABORTED/i.test(failed.failure()?.errorText || '')) diagnostics.expectedNavigationAborts.push(url.pathname);
      else diagnostics.requestFailures.push(url.pathname);
    });
    page.on('response', response => {
      if (response.url().startsWith(`${baseUrl}/api/`) && response.status() >= 500) diagnostics.backend5xx.push(response.status());
    });
    await page.goto(`${baseUrl}/login`, { waitUntil: 'domcontentloaded' });
    await page.locator('#login-email').fill(credentials.admin.username);
    await page.locator('#login-password').fill(credentials.admin.password);
    await page.getByRole('button', { name: 'Войти' }).click();
    await page.waitForURL(url => url.pathname !== '/login');
    await page.goto(`${baseUrl}/monitoring`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: 'Автономный спутниковый цикл' }).waitFor();
    await page.getByText('Провайдер: 1375', { exact: true }).waitFor();
    await page.getByText('Подтверждённых геометрических зон пока нет.', { exact: false }).waitFor();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    assert.equal(overflow, false);
    await page.addScriptTag({ content: axe.source });
    const accessibility = await page.evaluate(async () => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } }));
    const accessibilitySummary = accessibility.violations.map(item => ({
      id: item.id, impact: item.impact, help: item.help,
      nodes: item.nodes.map(node => ({ target: node.target, failureSummary: node.failureSummary })),
    }));
    await writeFile(path.join(evidenceDirectory, `AXE_${viewport.name}.json`), `${JSON.stringify(accessibilitySummary, null, 2)}\n`, 'utf8');
    const serious = accessibility.violations.filter(item => ['serious', 'critical'].includes(item.impact));
    assert.equal(serious.length, 0, JSON.stringify(serious.map(item => item.id)));
    if (viewport.name === 'desktop') {
      for (let cycle = 0; cycle < 20; cycle += 1) {
        await page.goto(`${baseUrl}/dashboard`, { waitUntil: 'networkidle' });
        await page.goto(`${baseUrl}/monitoring`, { waitUntil: 'networkidle' });
        await page.getByRole('heading', { name: 'Автономный спутниковый цикл' }).waitFor();
      }
      await context.setOffline(true);
      await page.evaluate(() => window.dispatchEvent(new Event('offline')));
      await page.getByText('Нет сети. Показано последнее загруженное состояние', { exact: false }).waitFor();
      await context.setOffline(false);
    }
    assert.equal(diagnostics.pageErrors.length, 0);
    assert.equal(diagnostics.requestFailures.length, 0, JSON.stringify(diagnostics.requestFailures));
    assert.equal(diagnostics.backend5xx.length, 0);
    assert.equal(diagnostics.consoleErrors.length, 0, JSON.stringify(diagnostics.consoleErrors));
    await page.screenshot({ path: path.join(evidenceDirectory, `${viewport.name}.png`), fullPage: false });
    results.push({ viewport, horizontal_overflow: false, serious_or_critical_accessibility_violations: 0, accessibility_violation_count: accessibility.violations.length, map_navigation_cleanup_cycles: viewport.name === 'desktop' ? 20 : 0, expected_navigation_aborts: diagnostics.expectedNavigationAborts.length, console_errors: 0, page_errors: 0, same_origin_request_failures: 0, backend_5xx: 0 });
    await context.close();
  }
} finally {
  await browser.close();
  for (const identity of Object.values(credentials)) { identity.username = ''; identity.password = ''; }
}

const report = {
  result: 'PASS_TASK219_AUTONOMOUS_MONITORING_BROWSER',
  database_identity: process.env.TASK219_BROWSER_DATABASE_IDENTITY,
  api: apiResults,
  viewports: results,
  authenticated_storage_persisted: false,
  traces_persisted: false,
  credentials_logged: false,
};
await writeFile(path.join(evidenceDirectory, 'BROWSER_QUALIFICATION.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
console.log(JSON.stringify({ result: report.result, viewport_count: results.length, cleanup_cycles: 20 }));
