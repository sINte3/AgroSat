// TASK_226 targeted browser qualification of the production build.
// The TASK_225 API contract is served by a deterministic fixture (retired
// writes answer 410), so every workflow materially changed by TASK_226 is
// exercised end to end on a desktop and a mobile viewport.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { build, preview } from 'vite';
import axe from 'axe-core';

import { chromiumExecutable } from './lib/browser.mjs';
import { MockApi, PASSWORD } from './lib/task226-mock-api.mjs';
import { toTashkentDateTimeInput } from '../src/utils/tashkentTime.js';

// TASK226_APP_ROOT qualifies another checkout (for example the TASK_225 base)
// with the same flows; TASK226_CONTINUE_ON_FAILURE reports every flow.
const root = process.env.TASK226_APP_ROOT || path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const evidenceDirectory = process.argv[2] || process.env.TASK226_EVIDENCE_DIR || '';
const continueOnFailure = Boolean(process.env.TASK226_CONTINUE_ON_FAILURE);
const configFile = path.join(root, 'vite.config.js');
if (!process.env.TASK226_SKIP_BUILD) await build({ configFile, root, logLevel: 'error' });
const server = await preview({ configFile, root, logLevel: 'error', preview: { port: 4273, strictPort: false, host: '127.0.0.1' } });
const baseUrl = server.resolvedUrls.local[0].replace(/\/$/, '');
const browser = await chromium.launch({ headless: true, executablePath: chromiumExecutable() });

const VIEWPORTS = [
  { name: 'desktop', options: { viewport: { width: 1440, height: 900 } } },
  { name: 'mobile', options: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
];
const DUE_INPUT = toTashkentDateTimeInput(new Date(Date.now() + 2 * 86400000));
const TASHKENT_ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00\+05:00$/;
const IDEMPOTENCY_KEY = /^[A-Za-z0-9._:-]{8,64}$/;
const report = { base_url: baseUrl, chromium: chromiumExecutable() || 'playwright-default', flows: [] };

async function openContext(viewport, api, { expectedConsole = [] } = {}) {
  const context = await browser.newContext({ ...viewport.options, serviceWorkers: 'block', locale: 'ru-RU', timezoneId: 'Europe/Berlin' });
  await context.addInitScript(() => {
    const native = indexedDB.deleteDatabase.bind(indexedDB);
    indexedDB.deleteDatabase = (...args) => {
      localStorage.setItem('__task226_delete_database_calls', String(Number(localStorage.getItem('__task226_delete_database_calls') || 0) + 1));
      return native(...args);
    };
  });
  await context.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const response = api.handle(route.request());
    await route.fulfill({ status: response.status, contentType: 'application/json; charset=utf-8', body: JSON.stringify(response.body), headers: { 'Cache-Control': 'no-store' } });
  });
  const diagnostics = { pageErrors: [], consoleErrors: [] };
  context.on('page', (page) => {
    page.on('pageerror', (error) => diagnostics.pageErrors.push(error.message.slice(0, 300)));
    page.on('console', (message) => {
      if (message.type() !== 'error') return;
      const text = message.text();
      if (/Failed to load resource: the server responded with a status of (401|404|405|410|500|503)/.test(text)) return;
      if (expectedConsole.some((pattern) => pattern.test(text))) return;
      diagnostics.consoleErrors.push(text.slice(0, 300));
    });
  });
  return { context, diagnostics };
}

async function login(page, email, { expectFailure = false } = {}) {
  if (!page.url().includes('/login')) await page.goto(`${baseUrl}/login`);
  await page.locator('#login-email').fill(email);
  await page.locator('#login-password').fill(expectFailure === 'password' ? 'wrong-password' : PASSWORD);
  await page.getByRole('button', { name: 'Войти' }).click();
  if (expectFailure) return page.locator('#login-error').textContent();
  await page.waitForURL((url) => url.pathname !== '/login');
  return null;
}

const noHorizontalOverflow = (page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
const token = (page) => page.evaluate(() => localStorage.getItem('agrosat_token'));
const deleteDatabaseCalls = (page) => page.evaluate(() => Number(localStorage.getItem('__task226_delete_database_calls') || 0));
function readDrafts(page) {
  return page.evaluate(() => new Promise((resolve, reject) => {
    const request = indexedDB.open('agrosat-offline-scouting', 1);
    request.onupgradeneeded = () => {
      for (const name of ['snapshots', 'drafts', 'queue']) {
        if (!request.result.objectStoreNames.contains(name)) request.result.createObjectStore(name, { keyPath: 'key' });
      }
    };
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const database = request.result;
      const all = database.transaction('drafts', 'readonly').objectStore('drafts').getAll();
      all.onerror = () => reject(all.error);
      all.onsuccess = () => {
        database.close();
        resolve(all.result.map((record) => ({ key: record.key, scope: record.scope, status: record.status || null, inspectionId: record.inspectionId || null, observations: record.finding?.observations || null })));
      };
    };
  }));
}
// WCAG 2.2 AA audit of the page as rendered; serious or critical violations fail.
// Only these color-contrast findings are exempt: they exist identically on the
// TASK_225 base a055040 in styles TASK_226 does not touch (LoginPage submit
// button, SummaryCards KPI colour, EnterpriseDetailPage/EnterpriseDashboard/
// EnterpriseFieldsTable inline palette). They are reported, not hidden.
const PRE_EXISTING_CONTRAST = Object.freeze({
  'login-error': /bg-emerald-600/,
  dashboard: /text-agro-warning/,
  'enterprise-alerts-failed': /rgb\(107, 133, 120\)|rgb\(22, 163, 74\)|rgb\(234, 179, 8\)|rgb\(241, 245, 243\)|background: none|<span>(Код|Бухара|Обновлено)|Сортировать по столбцу|padding: 2px 7px/,
});
const TASK226_COLOURS = /rgb\(146, 64, 14\)/;
const accessibility = [];
async function auditAccessibility(page, label, viewport) {
  // Let hover/focus transitions settle so colours are measured at rest.
  await page.mouse.move(0, 0);
  await page.waitForTimeout(350);
  await page.addScriptTag({ content: axe.source });
  const result = await page.evaluate(() => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
  }));
  const findings = result.violations
    .filter((violation) => ['serious', 'critical'].includes(violation.impact))
    .flatMap((violation) => violation.nodes.map((node) => ({
      id: violation.id, target: node.target.join(' '), html: node.html.replace(/\s+/g, ' ').slice(0, 160),
      ratio: node.any?.[0]?.data?.contrastRatio ?? null,
    })));
  const exempt = (finding) => finding.id === 'color-contrast'
    && PRE_EXISTING_CONTRAST[label]?.test(finding.html)
    && !TASK226_COLOURS.test(finding.html);
  const blocking = findings.filter((finding) => !exempt(finding));
  accessibility.push({ page: label, viewport, violations: result.violations.length, serious_blocking: blocking, serious_pre_existing: findings.length - blocking.length });
  assert.deepEqual(blocking, [], `${label}/${viewport}: serious or critical accessibility violations`);
}

function finish(name, viewport, diagnostics, details) {
  assert.deepEqual(diagnostics.pageErrors, [], `${name}/${viewport}: no uncaught page errors`);
  assert.deepEqual(diagnostics.consoleErrors, [], `${name}/${viewport}: no unexpected console errors`);
  report.flows.push({ flow: name, viewport, ...details, page_errors: 0, unexpected_console_errors: 0 });
}

// ─── 1. Login and session expiry ─────────────────────────────────────────────
async function sessionExpiryFlow(viewport) {
  const api = new MockApi();
  const { context, diagnostics } = await openContext(viewport, api);
  const page = await context.newPage();
  await page.goto(`${baseUrl}/operational-center`);
  await page.waitForURL((url) => url.pathname === '/login');
  const wrongPassword = await login(page, 'manager@agrosat.test', { expectFailure: 'password' });
  assert.equal(wrongPassword, 'Неверный email или пароль');
  api.loginUnavailable = true;
  const unavailable = await login(page, 'manager@agrosat.test', { expectFailure: 'server' });
  assert.match(unavailable, /Сервер недоступен/);
  await auditAccessibility(page, 'login-error', viewport.name);
  api.loginUnavailable = false;
  await login(page, 'manager@agrosat.test');
  assert.equal(new URL(page.url()).pathname, '/operational-center', 'login returns to the requested page');
  await page.locator('[data-remediation-status="closed_without_improvement"]').first().waitFor();

  const expiredToken = await token(page);
  api.revoked.add(expiredToken);
  await page.reload();
  await page.waitForURL((url) => url.pathname === '/login');
  assert.equal(await token(page), null, 'the expired credential is cleared');
  await login(page, 'manager@agrosat.test');
  assert.equal(new URL(page.url()).pathname, '/operational-center', 'after re-authentication the user returns to the page');

  // An API 401 in a mounted page ends the session without a hard reload.
  api.revoked.add(await token(page));
  const filterToggle = page.getByRole('button', { name: 'Показать' });
  if (await filterToggle.isVisible().catch(() => false)) await filterToggle.click();
  await page.getByLabel('Состояние').selectOption('awaiting_verification');
  await page.waitForURL((url) => url.pathname === '/login');
  await page.locator('#login-email').waitFor();
  assert.equal(await token(page), null);
  assert.equal(await deleteDatabaseCalls(page), 0, 'no offline database deletion on session expiry');
  await context.close();
  finish('login_session_expiry', viewport.name, diagnostics, { wrong_password_message: wrongPassword, unavailable_message: unavailable, return_path_preserved: true });
}

// ─── 2. Dashboard totals vs list size ────────────────────────────────────────
async function dashboardFlow(viewport) {
  const api = new MockApi();
  const { context, diagnostics } = await openContext(viewport, api);
  const page = await context.newPage();
  await page.goto(`${baseUrl}/login`);
  await login(page, 'manager@agrosat.test');
  await page.goto(`${baseUrl}/dashboard`);
  const kpis = page.getByRole('region', { name: 'Оперативные показатели' });
  await kpis.waitFor();
  const kpiText = await kpis.innerText();
  assert.match(kpiText, /Активные предупреждения\s*57/, 'the KPI shows the server total, not the 20 fetched rows');
  assert.match(kpiText, /Открытые осмотры\s*6/, 'open inspections come from the canonical queue');
  const totals = await page.locator('[data-testid="dashboard-alert-totals"]').innerText();
  assert.match(totals, /Всего активных: 57/);
  assert.match(totals, /Критично: 12/);
  assert.match(totals, /Высокий риск: 30/);
  assert.doesNotMatch(totals, /Инфо/, 'no info total is fabricated');
  assert.match(await page.locator('main').textContent(), /Первые по приоритету: 5 из 57/);
  const alertRequest = api.requests.find((request) => request.path === '/api/alerts/');
  assert.equal(new URLSearchParams(alertRequest.search).get('limit'), '20');
  assert.ok(api.requests.some((request) => request.path === '/api/anomaly-inspections/queue'));
  assert.equal(api.requests.filter((request) => request.path.startsWith('/api/field-inspections')).length, 0);
  assert.equal(await noHorizontalOverflow(page), true);
  await auditAccessibility(page, 'dashboard', viewport.name);
  await context.close();
  finish('dashboard_totals', viewport.name, diagnostics, { kpi_active_alerts: 57, list_rows_fetched: 20, open_inspections: 6 });
}

// ─── 3. Canonical inspection creation from the attention queue ───────────────
async function attentionInspectionFlow(viewport) {
  const api = new MockApi();
  const { context, diagnostics } = await openContext(viewport, api);
  const page = await context.newPage();
  await page.goto(`${baseUrl}/login`);
  await login(page, 'manager@agrosat.test');
  await page.goto(`${baseUrl}/attention`);
  await page.getByRole('button', { name: 'Назначить осмотр' }).click();
  const dialog = page.getByRole('dialog', { name: 'Создать осмотр' });
  await dialog.waitFor();
  const reason = await dialog.locator('textarea').inputValue();
  assert.match(reason, /^Очередь внимания: поле «Поле внимания»\./);
  assert.match(reason, /ndvi_drop/);
  assert.equal(await dialog.locator('select').first().inputValue(), 'urgent', 'critical attention priority maps to urgent');
  const submit = dialog.getByRole('button', { name: 'Создать осмотр' });
  assert.equal(await submit.isDisabled(), true, 'the deadline is required before submitting');
  await dialog.locator('input[type="datetime-local"]').fill(DUE_INPUT);
  await dialog.locator('select').nth(1).selectOption('21');
  await auditAccessibility(page, 'attention-create-dialog', viewport.name);
  await submit.click();
  await page.waitForURL((url) => url.pathname === '/inspections/501');
  await page.getByRole('heading', { name: /Осмотр #501/ }).waitFor();
  await auditAccessibility(page, 'inspection-detail', viewport.name);
  const created = api.createdInspections[0];
  assert.deepEqual(Object.keys(created.body).sort(), ['assigned_to_id', 'due_at', 'field_id', 'priority', 'reason', 'source_kind']);
  assert.equal(created.body.source_kind, 'manual');
  assert.equal(created.body.field_id, 11);
  assert.equal(created.body.priority, 'urgent');
  assert.equal(created.body.assigned_to_id, 21);
  assert.match(created.body.due_at, TASHKENT_ISO);
  assert.equal(created.body.due_at, `${DUE_INPUT}:00+05:00`, 'the chosen wall time is sent as Asia/Tashkent');
  assert.match(created.idempotencyKey, IDEMPOTENCY_KEY);
  assert.deepEqual(api.retiredCalls(), [], 'no retired endpoint was called');
  assert.equal(api.writes('/api/anomaly-inspections').length, 1, 'exactly one creation request');
  await context.close();
  finish('attention_inspection_create', viewport.name, diagnostics, { request: created.body });
}

// ─── 4. Canonical inspection creation from the irrigation context ────────────
async function irrigationInspectionFlow(viewport) {
  const api = new MockApi();
  const { context, diagnostics } = await openContext(viewport, api);
  const page = await context.newPage();
  await page.goto(`${baseUrl}/login`);
  await login(page, 'manager@agrosat.test');
  await page.goto(`${baseUrl}/fields/1`);
  await page.getByRole('tab', { name: 'Погода' }).click();
  const form = page.locator('form', { has: page.getByRole('heading', { name: 'Назначить проверку поля' }) });
  await form.waitFor();
  const create = form.getByRole('button', { name: 'Создать осмотр' });
  assert.equal(await create.isDisabled(), true, 'no hidden deadline: the button waits for an explicit due date');
  await form.locator('select').nth(1).selectOption('high');
  await auditAccessibility(page, 'irrigation-create-form', viewport.name);
  await form.locator('input[type="datetime-local"]').fill(DUE_INPUT);
  await create.click();
  await page.getByRole('heading', { name: /Активный осмотр #501/ }).waitFor();
  const created = api.createdInspections[0];
  assert.deepEqual(created.body, {
    field_id: 1, source_kind: 'manual',
    reason: 'Контекст орошения: Подозрение на водный стресс. Проверить состояние в поле и зафиксировать фактические доказательства.',
    priority: 'high', due_at: `${DUE_INPUT}:00+05:00`,
  });
  assert.match(created.idempotencyKey, IDEMPOTENCY_KEY);
  assert.equal(await page.getByRole('link', { name: 'Открыть осмотр' }).getAttribute('href'), '/inspections/501');
  assert.deepEqual(api.retiredCalls(), []);
  assert.equal(await noHorizontalOverflow(page), true);
  await context.close();

  // An agronomist sees the context but cannot create a canonical inspection.
  const agronomistApi = new MockApi();
  const agronomist = await openContext(viewport, agronomistApi);
  const agronomistPage = await agronomist.context.newPage();
  await agronomistPage.goto(`${baseUrl}/login`);
  await login(agronomistPage, 'agro-a@agrosat.test');
  await agronomistPage.goto(`${baseUrl}/fields/1`);
  await agronomistPage.getByRole('tab', { name: 'Погода' }).click();
  await agronomistPage.getByText('Осмотр по контексту орошения назначает менеджер или администратор.').waitFor();
  assert.equal(await agronomistPage.getByRole('button', { name: 'Создать осмотр' }).count(), 0);
  await agronomist.context.close();
  finish('irrigation_inspection_create', viewport.name, diagnostics, { request: created.body, agronomist_create_control: false });
}

// ─── 5. Operational Center canonical states ─────────────────────────────────
async function operationalCenterFlow(viewport) {
  const api = new MockApi();
  const { context, diagnostics } = await openContext(viewport, api);
  const page = await context.newPage();
  await page.goto(`${baseUrl}/login`);
  await login(page, 'manager@agrosat.test');
  await page.goto(`${baseUrl}/operational-center`);
  await page.locator('[data-remediation-status="closed_without_improvement"]').first().waitFor();
  const chip = async (status, index = 0) => page.locator(`[data-remediation-status="${status}"]`).nth(index);
  const closedWithout = await chip('closed_without_improvement');
  assert.equal((await closedWithout.innerText()).trim(), 'Закрыто без подтверждённого улучшения');
  assert.doesNotMatch(await closedWithout.getAttribute('class'), /emerald/, 'closed without improvement is never green');
  assert.match(await (await chip('improved_closed')).getAttribute('class'), /emerald/, 'a verified improvement is green');
  assert.equal((await (await chip('not_improved', 0)).innerText()).trim(), 'Ухудшение после мер');
  assert.equal((await (await chip('not_improved', 1)).innerText()).trim(), 'Без существенных изменений после мер');
  assert.equal((await (await chip('reopened')).innerText()).trim(), 'Возвращено на доработку');
  assert.equal((await (await chip('verification_blocked')).innerText()).trim(), 'Спутниковая проверка заблокирована');
  assert.equal((await (await chip('data_unavailable')).innerText()).trim(), 'Данные устарели');
  const summary = await page.getByLabel('Оперативная сводка').innerText();
  for (const [label, value] of [['Закрыто без улучшения · 30 дн.', 4], ['Улучшение не подтверждено', 3], ['На доработке', 2], ['Проверка заблокирована', 5], ['Закрыто с улучшением · 30 дн.', 1]]) {
    assert.match(summary, new RegExp(`${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*${value}`), `summary ${label}`);
  }
  const toggle = page.getByRole('button', { name: 'Показать' });
  if (await toggle.isVisible().catch(() => false)) await toggle.click();
  const stateOptions = await page.getByLabel('Состояние').locator('option').evaluateAll((options) => options.map((option) => option.value));
  assert.ok(!stateOptions.includes('closed_without_improvement'), 'the filter never offers a value the backend rejects');
  assert.equal(stateOptions.length, 10);
  await auditAccessibility(page, 'operational-center-queue', viewport.name);

  await page.getByRole('button', { name: /Закрыто без улучшения/ }).first().click();
  await page.waitForURL((url) => url.pathname === `/operational-center/cases/${encodeURIComponent('inspection:101')}`);
  const verification = page.getByRole('heading', { name: 'Проверка результата' }).locator('..');
  await verification.getByText('Ухудшение', { exact: true }).waitFor();
  await auditAccessibility(page, 'operational-center-case', viewport.name);
  await page.getByRole('navigation', { name: 'Связанные рабочие разделы' }).getByRole('button', { name: 'План мер #7' }).click();
  await page.waitForURL((url) => url.pathname === '/agronomy-plans/7');
  await page.getByRole('heading', { name: /план #7/ }).waitFor();
  assert.ok(!api.requests.some((request) => request.path === '/api/operational-center/queue' && /closed_without_improvement/.test(request.search)));
  assert.equal(await noHorizontalOverflow(page), true);
  await context.close();
  finish('operational_center_states', viewport.name, diagnostics, { state_options: stateOptions.length, plan_navigation: '/agronomy-plans/7' });
}

// ─── 6. Agronomy plan date input safety ──────────────────────────────────────
async function agronomyDateFlow(viewport) {
  const api = new MockApi();
  const { context, diagnostics } = await openContext(viewport, api);
  const page = await context.newPage();
  await page.goto(`${baseUrl}/login`);
  await login(page, 'manager@agrosat.test');
  await page.goto(`${baseUrl}/agronomy-plans/5`);
  const form = page.locator('form', { has: page.getByRole('heading', { name: 'Добавить работу' }) });
  await form.waitFor();
  await form.locator('textarea').fill('Проверить капельную линию на северном краю');
  await form.locator('select').nth(1).selectOption('21');
  const due = form.locator('input[type="datetime-local"]').nth(1);
  const start = form.locator('input[type="datetime-local"]').nth(0);
  await due.fill('2026-10-01T09:30');
  await due.focus();
  await page.keyboard.press('Backspace');
  await page.evaluate(() => {
    const input = [...document.querySelectorAll('input[type="datetime-local"]')].at(-1);
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, '');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await form.getByRole('button', { name: 'Добавить' }).click();
  await form.getByText('Укажите срок работы.').waitFor();
  await auditAccessibility(page, 'agronomy-plan-validation', viewport.name);
  assert.equal(api.workCreated.length, 0, 'incomplete date input is never submitted');
  await start.fill('2026-10-01T10:00');
  await due.fill('2026-10-01T09:30');
  await form.getByRole('button', { name: 'Добавить' }).click();
  await form.getByText('Срок не может быть раньше планового начала.').waitFor();
  assert.equal(api.workCreated.length, 0);
  await start.fill('2026-09-30T08:00');
  await form.getByRole('button', { name: 'Добавить' }).click();
  await page.waitForFunction(() => document.querySelector('form textarea')?.value === '');
  assert.equal(api.workCreated.length, 1);
  const body = api.workCreated[0].body;
  assert.equal(body.due_at, '2026-10-01T09:30:00+05:00');
  assert.equal(body.planned_start_at, '2026-09-30T08:00:00+05:00');
  assert.match(api.workCreated[0].idempotencyKey, IDEMPOTENCY_KEY);
  await context.close();
  finish('agronomy_date_input', viewport.name, diagnostics, { submitted: { due_at: body.due_at, planned_start_at: body.planned_start_at }, partial_input_render_crash: false });
}

// ─── 7. Offline draft survives 401 and re-authentication; user isolation ──────
async function offlineDraftFlow(viewport) {
  const api = new MockApi();
  const { context, diagnostics } = await openContext(viewport, api, { expectedConsole: [/ERR_INTERNET_DISCONNECTED/] });
  const page = await context.newPage();
  await page.goto(`${baseUrl}/login`);
  await login(page, 'agro-a@agrosat.test');
  await page.goto(`${baseUrl}/inspections/41`);
  const finding = page.locator('section', { has: page.getByRole('heading', { name: 'Результаты выезда' }) });
  await finding.locator('textarea').first().waitFor();
  const observations = `Наблюдение агронома А (${viewport.name})`;
  await context.setOffline(true);
  await page.evaluate(() => window.dispatchEvent(new Event('offline')));
  await finding.getByLabel('Затронутая площадь').fill('1.5');
  await finding.getByLabel('Наблюдения').fill(observations);
  await finding.getByLabel('Рекомендованное действие').fill('Проверить полив и повторить осмотр');
  await finding.getByRole('button', { name: 'Сохранить на сервере' }).click();
  await finding.getByRole('status').filter({ hasText: 'Ожидает синхронизации' }).waitFor();
  const queued = await readDrafts(page);
  assert.deepEqual(queued.map(({ scope, status, inspectionId, observations: text }) => ({ scope, status, inspectionId, text })), [{ scope: '7:21', status: 'pending_sync', inspectionId: 41, text: observations }]);

  // The token expires while the agronomist is offline; reconnecting answers 401.
  const expired = await token(page);
  api.revoked.add(expired);
  await context.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await page.waitForURL((url) => url.pathname === '/login');
  assert.ok(api.requests.some((request) => request.path === '/api/anomaly-inspections/41/finding' && request.token === expired), 'the reconnect sync was attempted with the expired credential');
  assert.equal(api.findings.length, 0, 'the expired credential could not submit the finding');
  assert.equal(await token(page), null, 'the web session ended');
  const afterExpiry = await readDrafts(page);
  assert.equal(afterExpiry.length, 1, '401 kept the unsynchronized draft');
  assert.equal(afterExpiry[0].status, 'pending_sync');

  // A failed login attempt keeps it too.
  assert.equal(await login(page, 'agro-a@agrosat.test', { expectFailure: 'password' }), 'Неверный email или пароль');
  assert.equal((await readDrafts(page)).length, 1, 'a failed login keeps the draft');

  // User B on the same device: A's draft is neither shown nor synchronized.
  await login(page, 'agro-b@agrosat.test');
  await page.goto(`${baseUrl}/inspections`);
  await page.locator('[data-testid="inspection-queue"]').waitFor();
  assert.equal(await page.locator('[data-testid="offline-drafts"]').count(), 0, 'B sees no drafts of A');
  await page.goto(`${baseUrl}/inspections/41`);
  const findingB = page.locator('section', { has: page.getByRole('heading', { name: 'Результаты выезда' }) });
  await findingB.locator('textarea').first().waitFor();
  await page.waitForTimeout(400);
  assert.notEqual(await findingB.getByLabel('Наблюдения').inputValue(), observations, 'A\'s draft is not loaded for B');
  assert.equal(await findingB.getByRole('status').filter({ hasText: 'Ожидает синхронизации' }).count(), 0);
  assert.equal(api.requests.filter((request) => request.path === '/api/anomaly-inspections/41/finding' && request.userId === 22).length, 0, 'B never synchronizes A\'s draft');
  // B's explicit logout removes only B's partition.
  if (viewport.name === 'mobile') await page.getByRole('button', { name: 'Открыть основную навигацию' }).first().click();
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await page.waitForURL((url) => url.pathname === '/login');
  assert.equal((await readDrafts(page)).length, 1, 'B\'s logout keeps A\'s draft');

  // A signs in again and recovers the draft.
  await login(page, 'agro-a@agrosat.test');
  await page.goto(`${baseUrl}/inspections`);
  const notice = page.locator('[data-testid="offline-drafts"]');
  await notice.waitFor();
  assert.match(await notice.innerText(), /Несинхронизированные черновики на этом устройстве: 1/);
  await auditAccessibility(page, 'inspection-queue-drafts', viewport.name);
  await notice.getByRole('button', { name: /Осмотр #41 · ожидает отправки/ }).click();
  await page.waitForURL((url) => url.pathname === '/inspections/41');
  await page.getByRole('status').filter({ hasText: 'Синхронизировано' }).waitFor();
  assert.equal(api.findings.length, 1, 'the recovered draft is submitted exactly once');
  const submitted = api.findings[0];
  assert.equal(submitted.userId, 21);
  assert.equal(submitted.body.observations, observations);
  assert.equal(submitted.body.expected_version, 4, 'the draft keeps its base version (optimistic concurrency)');
  assert.equal(submitted.body.sync_state, 'pending_sync');
  assert.match(submitted.body.inspected_at, /\+05:00$/);
  assert.deepEqual(await readDrafts(page), [], 'the synchronized draft is removed');

  // Cross-tab: another tab loses the session; this tab ends it without purging.
  await page.goto(`${baseUrl}/inspections/41`);
  const findingAgain = page.locator('section', { has: page.getByRole('heading', { name: 'Результаты выезда' }) });
  await findingAgain.getByLabel('Наблюдения').fill('Локальный черновик перед сменой вкладки');
  await findingAgain.getByRole('button', { name: 'Сохранить локально' }).click();
  await findingAgain.getByRole('status').filter({ hasText: 'Сохранено локально' }).waitFor();
  const otherTab = await context.newPage();
  await otherTab.goto(`${baseUrl}/login`);
  await otherTab.evaluate(() => localStorage.removeItem('agrosat_token'));
  await page.waitForURL((url) => url.pathname === '/login');
  const afterCrossTab = await readDrafts(page);
  assert.deepEqual(afterCrossTab.map(({ scope, status }) => ({ scope, status })), [{ scope: '7:21', status: 'local_draft' }], 'a cross-tab session loss keeps the draft');
  assert.equal(await deleteDatabaseCalls(page), 0, 'the offline database was never deleted');
  assert.deepEqual(api.retiredCalls(), []);
  await context.close();
  finish('offline_draft_401_recovery', viewport.name, diagnostics, {
    draft_after_401: 'kept', failed_login: 'kept', other_user_visibility: 'none', other_user_sync: 0,
    other_user_logout: 'kept', recovered_by_owner: true, submitted_version: submitted.body.expected_version, cross_tab: 'kept',
  });
}

// ─── 8. Route ErrorBoundary ─────────────────────────────────────────────────
async function errorBoundaryFlow(viewport) {
  const api = new MockApi();
  api.override('GET', '/api/operational-center/queue', () => ({
    status: 200,
    body: { as_of: new Date().toISOString(), items: [{ case_key: 'inspection:999', title: 'Нарушенный ответ', priority_reasons: null, remediation_status: 'needs_inspection', operational_status: 'awaiting_inspection', source: 'inspection' }], total: 1, limit: 50, offset: 0 },
  }));
  const { context, diagnostics } = await openContext(viewport, api, { expectedConsole: [/priority_reasons|Cannot read properties of null|\[AgroSat\] render failure|TypeError/] });
  const page = await context.newPage();
  await page.goto(`${baseUrl}/login`);
  await login(page, 'manager@agrosat.test');
  await page.goto(`${baseUrl}/operational-center`);
  const fallback = page.locator('[data-testid="route-error-boundary"]');
  await fallback.waitFor();
  assert.match(await fallback.innerText(), /Раздел не удалось отобразить/);
  await auditAccessibility(page, 'route-error-fallback', viewport.name);
  const queueCalls = () => api.requests.filter((request) => request.path === '/api/operational-center/queue').length;
  await fallback.getByRole('button', { name: 'Повторить' }).click();
  await fallback.waitFor();
  const callsAfterRetry = queueCalls();
  await page.waitForTimeout(1500);
  assert.equal(queueCalls(), callsAfterRetry, 'no automatic retry loop');
  // The shell stays usable: navigate to another section.
  if (viewport.name === 'mobile') await page.getByRole('button', { name: 'Открыть основную навигацию' }).first().click();
  await page.getByRole('navigation', { name: 'Основная навигация' }).getByRole('button', { name: 'Сегодня' }).click();
  await page.waitForURL((url) => url.pathname === '/dashboard');
  await page.getByRole('region', { name: 'Оперативные показатели' }).waitFor();
  assert.equal(await page.locator('[data-testid="route-error-boundary"]').count(), 0, 'navigation resets the boundary');
  // The safe navigation action of the fallback works too.
  await page.goto(`${baseUrl}/operational-center`);
  await fallback.waitFor();
  await fallback.getByRole('button', { name: 'На главную' }).click();
  await page.waitForURL((url) => url.pathname === '/dashboard');
  await context.close();
  assert.deepEqual(diagnostics.pageErrors, [], 'a caught render failure is not an uncaught page error');
  finish('route_error_boundary', viewport.name, diagnostics, { fallback: true, shell_navigation: true, retry_loop: false });
}

// ─── 9. Error is not empty: enterprise alerts ────────────────────────────────
async function enterpriseAlertsFlow(viewport) {
  const api = new MockApi();
  let failAlerts = true;
  api.override('GET', '/api/alerts/', () => (failAlerts ? { status: 500, body: { detail: 'fixture failure' } } : null));
  const { context, diagnostics } = await openContext(viewport, api);
  const page = await context.newPage();
  await page.goto(`${baseUrl}/login`);
  await login(page, 'manager@agrosat.test');
  await page.goto(`${baseUrl}/enterprises/7`);
  await page.getByText('Предупреждения не загружены').waitFor({ timeout: 15000 });
  const enterpriseAlertReads = () => api.requests.filter((request) => request.path === '/api/alerts/' && new URLSearchParams(request.search).get('enterprise_id') === '7');
  assert.equal(enterpriseAlertReads().length, 3, 'a failed read is retried within the bound');
  assert.equal(new URLSearchParams(enterpriseAlertReads()[0].search).get('limit'), '200');
  await page.getByRole('button', { name: 'Рекомендации' }).click();
  await page.getByText('Не удалось загрузить предупреждения предприятия.').waitFor();
  await auditAccessibility(page, 'enterprise-alerts-failed', viewport.name);
  assert.equal(await page.getByText(/Активных критических предупреждений/).count(), 0, 'a failure is never shown as "no alerts"');
  failAlerts = false;
  await page.getByRole('button', { name: 'Повторить' }).click();
  await page.getByText(/Предупреждение 1$/).first().waitFor();
  assert.equal(await page.getByText('Не удалось загрузить предупреждения предприятия.').count(), 0, 'the retried read replaces the error state');
  await context.close();
  finish('enterprise_alerts_error_vs_empty', viewport.name, diagnostics, { failed_state: 'explicit', retried_reads: 3 });
}

const failures = [];
try {
  for (const viewport of VIEWPORTS) {
    for (const flow of [sessionExpiryFlow, dashboardFlow, attentionInspectionFlow, irrigationInspectionFlow, operationalCenterFlow, agronomyDateFlow, offlineDraftFlow, errorBoundaryFlow, enterpriseAlertsFlow]) {
      try {
        await flow(viewport);
        console.log(`PASS ${flow.name} ${viewport.name}`);
      } catch (error) {
        if (!continueOnFailure) throw error;
        const message = String(error?.message || error).split('\n')[0].slice(0, 240);
        failures.push({ flow: flow.name, viewport: viewport.name, message });
        console.log(`FAIL ${flow.name} ${viewport.name} :: ${message}`);
        for (const context of browser.contexts()) await context.close().catch(() => {});
      }
    }
  }
} finally {
  await browser.close();
  await new Promise((resolve) => server.httpServer.close(resolve));
}
if (failures.length) {
  console.log(JSON.stringify({ result: 'FAIL_TASK226_BROWSER_QUALIFICATION', failures }));
  process.exit(1);
}

report.result = 'PASS_TASK226_BROWSER_QUALIFICATION';
report.accessibility = accessibility;
if (evidenceDirectory) {
  await mkdir(evidenceDirectory, { recursive: true });
  await writeFile(path.join(evidenceDirectory, 'TASK226_BROWSER_QUALIFICATION.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
}
console.log(JSON.stringify({ result: report.result, flows: report.flows.length, viewports: VIEWPORTS.map((item) => item.name) }));
