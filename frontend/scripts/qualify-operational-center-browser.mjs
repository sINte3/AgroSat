import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium, request } from 'playwright';
import axe from 'axe-core';

const [baseUrl, evidenceDirectory, caseKey] = process.argv.slice(2);
assert(baseUrl && evidenceDirectory && caseKey);
await mkdir(evidenceDirectory, { recursive: true });
const identity = {
  username: process.env.TASK221_BROWSER_USERNAME,
  password: process.env.TASK221_BROWSER_PASSWORD,
};
assert(identity.username && identity.password);
delete process.env.TASK221_BROWSER_USERNAME;
delete process.env.TASK221_BROWSER_PASSWORD;

const api = await request.newContext({ baseURL: baseUrl });
const unauthenticated = await api.get('/api/operational-center/queue');
assert.equal(unauthenticated.status(), 401);
const login = await api.post('/api/auth/login', { form: identity });
assert.equal(login.status(), 200);
const token = (await login.json()).access_token;
const headers = { Authorization: `Bearer ${token}` };
const caseResponse = await api.get(
  `/api/operational-center/cases/${encodeURIComponent(caseKey)}`,
  { headers },
);
assert.equal(caseResponse.status(), 200);
const caseDetail = await caseResponse.json();
const inspectionId = caseDetail.case?.inspection_id;
const planId = caseDetail.case?.plan_id;
const fieldId = caseDetail.case?.field_id;
assert(Number.isInteger(inspectionId) && Number.isInteger(planId) && Number.isInteger(fieldId));
for (const endpoint of [
  '/api/operational-center/queue?limit=100',
  '/api/operational-center/summary',
  `/api/operational-center/fields/${fieldId}/timeline`,
]) {
  const response = await api.get(endpoint, { headers });
  assert.equal(response.status(), 200, endpoint);
}
await api.dispose();

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
    const diagnostics = { console: [], page: [], failed: [], backend5xx: [] };
    let offlineProbe = false;
    page.on('console', message => {
      if (message.type() === 'error' && !/favicon/i.test(message.text())) {
        diagnostics.console.push(message.text().slice(0, 240));
      }
    });
    page.on('pageerror', error => diagnostics.page.push(error.message));
    page.on('requestfailed', requestValue => {
      const failure = requestValue.failure()?.errorText || '';
      const target = new URL(requestValue.url());
      if (
        !offlineProbe
        && target.origin === new URL(baseUrl).origin
        && !/ERR_ABORTED|NS_BINDING_ABORTED/i.test(failure)
      ) diagnostics.failed.push(`${target.pathname}:${failure}`);
    });
    page.on('response', response => {
      if (response.url().includes('/api/') && response.status() >= 500) {
        diagnostics.backend5xx.push(`${response.status()}:${new URL(response.url()).pathname}`);
      }
    });

    await page.goto(`${baseUrl}/login`, { waitUntil: 'domcontentloaded' });
    await page.locator('#login-email').fill(identity.username);
    await page.locator('#login-password').fill(identity.password);
    await page.getByRole('button', { name: 'Войти' }).click();
    await page.waitForURL(url => url.pathname !== '/login');
    await page.goto(
      `${baseUrl}/operational-center/cases/${encodeURIComponent(caseKey)}`,
      { waitUntil: 'networkidle' },
    );
    if (viewport.name !== 'mobile') {
      await page.getByRole('heading', { name: 'Приоритетная очередь' }).waitFor();
    }
    await page.getByRole('heading', { name: 'TASK 221 linked inspection' }).waitFor();
    await page.getByRole('heading', { name: 'Погода как контекст' }).waitFor();
    await page.getByRole('heading', { name: 'Техника и присутствие' }).waitFor();
    await page.getByText('Не поддерживается', { exact: true }).waitFor();
    await page.getByText('Материалы выполнения: 1.', { exact: false }).waitFor();

    const filterToggle = page.getByRole('button', { name: 'Показать' });
    if (await filterToggle.isVisible().catch(() => false)) await filterToggle.click();
    await page.getByLabel('Состояние').selectOption('awaiting_verification');
    await page.waitForResponse(response => (
      response.url().includes('/api/operational-center/queue')
      && response.request().method() === 'GET'
    ));
    await page.getByRole('heading', { name: 'TASK 221 linked inspection' }).waitFor();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    );
    assert.equal(overflow, false);
    const undersizedTargets = await page.locator('main button:visible, main select:visible, main input:visible').evaluateAll(
      elements => elements.filter(element => {
        const box = element.getBoundingClientRect();
        return box.width > 0 && box.height > 0 && (box.width < 44 || box.height < 44);
      }).map(element => ({
        text: element.textContent?.trim().slice(0, 80) || element.getAttribute('aria-label'),
        box: element.getBoundingClientRect().toJSON(),
      })),
    );
    assert.deepEqual(undersizedTargets, []);
    await page.keyboard.press('Tab');
    const keyboardFocusVisible = await page.evaluate(() => document.activeElement !== document.body);
    assert.equal(keyboardFocusVisible, true);

    await page.addScriptTag({ content: axe.source });
    const accessibility = await page.evaluate(
      () => window.axe.run(document, {
        runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
      }),
    );
    const serious = accessibility.violations.filter(
      violation => ['serious', 'critical'].includes(violation.impact),
    );
    await writeFile(
      path.join(evidenceDirectory, `AXE_${viewport.name}.json`),
      `${JSON.stringify(accessibility.violations, null, 2)}\n`,
      'utf8',
    );
    assert.equal(serious.length, 0, JSON.stringify(serious.map(value => value.id)));

    let navigationChecks = 0;
    let mapRuntimeChecks = 0;
    if (viewport.name === 'desktop') {
      const destinations = [
        [`Осмотр #${inspectionId}`, `/inspections/${inspectionId}`],
        ['Меры и контроль', `/agronomy-plans/${planId}`],
        ['Поле', `/fields/${fieldId}`],
        ['Мониторинг', '/monitoring'],
      ];
      for (const [label, expectedPath] of destinations) {
        await page.goto(
          `${baseUrl}/operational-center/cases/${encodeURIComponent(caseKey)}`,
          { waitUntil: 'networkidle' },
        );
        await page.getByRole('button', { name: label, exact: true }).click();
        await page.waitForURL(url => url.pathname === expectedPath);
        navigationChecks += 1;
      }
      for (let index = 0; index < 5; index += 1) {
        await page.goto(`${baseUrl}/fields`, { waitUntil: 'networkidle' });
        const map = page.locator('[aria-label="Интерактивная карта полей"]');
        await map.waitFor();
        await page.waitForFunction(() => {
          const value = document.querySelector('[aria-label="Интерактивная карта полей"]')
            ?.getAttribute('data-spatial-status');
          return value === 'ready' || value === 'empty' || value === 'error';
        });
        assert.notEqual(await map.getAttribute('data-spatial-status'), 'error');
        mapRuntimeChecks += 1;
        await page.goto(`${baseUrl}/dashboard`, { waitUntil: 'networkidle' });
        await page.goto(
          `${baseUrl}/operational-center/cases/${encodeURIComponent(caseKey)}`,
          { waitUntil: 'networkidle' },
        );
        await page.getByRole('heading', { name: 'TASK 221 linked inspection' }).waitFor();
      }
    }

    offlineProbe = true;
    await context.setOffline(true);
    await page.evaluate(() => window.dispatchEvent(new Event('offline')));
    await page.getByText('Нет сети.', { exact: false }).first().waitFor();
    const activeOfflineMutation = await page.locator('button').filter({ hasText: 'Прочитано' }).evaluateAll(
      buttons => buttons.some(button => !button.disabled),
    );
    assert.equal(activeOfflineMutation, false);
    await context.setOffline(false);
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    offlineProbe = false;
    await page.waitForTimeout(250);

    assert.deepEqual(diagnostics, { console: [], page: [], failed: [], backend5xx: [] });
    await page.screenshot({
      path: path.join(evidenceDirectory, `operational-center-${viewport.name}.png`),
      fullPage: false,
    });
    results.push({
      viewport,
      horizontal_overflow: false,
      undersized_interactive_targets: 0,
      keyboard_focus_reachable: true,
      serious_or_critical_accessibility_violations: 0,
      console_errors: 0,
      page_errors: 0,
      same_origin_request_failures: 0,
      backend_5xx: 0,
      navigation_checks: navigationChecks,
      map_runtime_checks: mapRuntimeChecks,
      offline_read_only_state: true,
      map_cleanup: viewport.name === 'desktop'
        ? 'existing_map_runtime_remounted_5_times; no_task221_map_owned'
        : 'not_applicable_no_task221_map',
    });
    await context.close();
  }
} finally {
  await browser.close();
  identity.username = '';
  identity.password = '';
}

const report = {
  result: 'PASS_TASK221_OPERATIONAL_CENTER_BROWSER',
  database_identity: process.env.TASK221_BROWSER_DATABASE_IDENTITY,
  case_key: caseKey,
  viewports: results,
  unauthenticated_status: 401,
  weather_context_rendered: true,
  telematics_degraded_rendered: true,
  filters_exercised: true,
  navigation_targets_exercised: 4,
  maplibre_runtime_remounts: results.reduce((total, value) => total + value.map_runtime_checks, 0),
  authenticated_storage_exported: false,
  traces_persisted: false,
  credentials_logged: false,
};
await writeFile(
  path.join(evidenceDirectory, 'BROWSER_QUALIFICATION.json'),
  `${JSON.stringify(report, null, 2)}\n`,
  'utf8',
);
console.log(JSON.stringify({ result: report.result, viewport_count: results.length }));
