// TASK_233 browser qualification of the Management Analytics page on the
// production build. The TASK_232 API is served by a deterministic fixture in
// the exact response contract; every request the page makes is recorded, so
// the suite proves which endpoints the page reads and what it sends.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { build, preview } from 'vite';
import axe from 'axe-core';

import { chromiumExecutable } from './lib/browser.mjs';
import {
  ENTERPRISES,
  adminSnapshot,
  emptyScopeSnapshot,
  filterOptions,
  managerSnapshot,
  quietSnapshot,
} from './lib/management-analytics-fixture.mjs';
import { formatPeriod, presetPeriod } from '../src/utils/managementAnalytics.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const evidenceDirectory = process.argv[2] || process.env.MA_EVIDENCE_DIR || '';
const configFile = path.join(root, 'vite.config.js');
if (!process.env.MA_SKIP_BUILD) await build({ configFile, root, logLevel: 'error' });
const server = await preview({ configFile, root, logLevel: 'error', preview: { port: 4283, strictPort: false, host: '127.0.0.1' } });
const baseUrl = server.resolvedUrls.local[0].replace(/\/$/, '');
const browser = await chromium.launch({ headless: true, executablePath: chromiumExecutable() });

const USERS = Object.freeze({
  admin: { id: 1, full_name: 'Администратор Тест', email: 'admin@agrosat.test', role: 'admin', enterprise_id: null, is_active: true },
  manager: { id: 11, full_name: 'Руководитель Тест', email: 'manager@agrosat.test', role: 'manager', enterprise_id: 7, is_active: true },
  viewer: { id: 31, full_name: 'Наблюдатель Тест', email: 'viewer@agrosat.test', role: 'viewer', enterprise_id: 7, is_active: true },
  agronomist: { id: 21, full_name: 'Агроном Тест', email: 'agro@agrosat.test', role: 'agronomist', enterprise_id: 7, is_active: true },
});
const VIEWPORTS = Object.freeze({
  desktop: { viewport: { width: 1440, height: 900 } },
  tablet: { viewport: { width: 1024, height: 768 } },
  mobile: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
});
const KNOWN_ENDPOINTS = new Set(['/api/auth/me', '/api/enterprises/', '/api/operational-center/filter-options', '/api/management-analytics']);
const TODAY_PERIOD = presetPeriod(30);
const normalize = (value) => String(value ?? '').replace(/[\u00a0\u202f]/g, ' ').replace(/\s+/g, ' ').trim();
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const report = { base_url: baseUrl, chromium: chromiumExecutable() || 'playwright-default', flows: [], accessibility: [] };

/** Records every /api request and answers it from the fixture or a scenario override. */
class AnalyticsApi {
  constructor(role, { token = true } = {}) {
    this.role = role;
    this.user = USERS[role];
    this.token = token;
    this.requests = [];
    this.analytics = null;
  }

  analyticsRequests() { return this.requests.filter((item) => item.path === '/api/management-analytics'); }

  unexpected() { return this.requests.filter((item) => !KNOWN_ENDPOINTS.has(item.path)); }

  snapshot(query) {
    if (this.role === 'admin') return adminSnapshot(query);
    const snapshot = managerSnapshot();
    snapshot.scope = { ...snapshot.scope, field_id: query.field_id ? Number(query.field_id) : null, current_crop_type_id: query.current_crop_type_id ? Number(query.current_crop_type_id) : null };
    if (query.date_from && query.date_to) {
      snapshot.period.requested = { date_from: query.date_from, date_to: query.date_to };
      snapshot.period.effective = { ...snapshot.period.effective, date_from: query.date_from, date_to: query.date_to };
    }
    return snapshot;
  }

  async handle(entry) {
    if (entry.method !== 'GET') return { status: 405, body: { detail: 'Fixture writes are disabled' } };
    if (entry.path === '/api/auth/me') return this.token ? { status: 200, body: this.user } : { status: 401, body: { detail: 'Not authenticated' } };
    if (entry.path === '/api/enterprises/') {
      const list = this.role === 'admin' ? ENTERPRISES : ENTERPRISES.filter((item) => item.id === this.user.enterprise_id);
      return { status: 200, body: list.map((item) => ({ id: item.id, name: item.name, total_fields: 40 })) };
    }
    if (entry.path === '/api/operational-center/filter-options') {
      const enterpriseId = Number(entry.query.enterprise_id || this.user.enterprise_id || 7);
      return { status: 200, body: filterOptions(enterpriseId) };
    }
    if (entry.path === '/api/management-analytics') {
      const custom = this.analytics ? await this.analytics(entry, this) : null;
      return custom || { status: 200, body: this.snapshot(entry.query) };
    }
    return { status: 404, body: { detail: 'Fixture route not found' } };
  }
}

async function openPage(role, viewportName = 'desktop', { token = true, expectedConsole = [] } = {}) {
  const api = new AnalyticsApi(role, { token });
  const context = await browser.newContext({
    ...VIEWPORTS[viewportName], serviceWorkers: 'block', locale: 'ru-RU', timezoneId: 'Europe/Berlin',
  });
  if (token) await context.addInitScript(() => { localStorage.setItem('agrosat_token', 'task233-fixture-token'); });
  await context.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const entry = { method: request.method(), path: url.pathname, query: Object.fromEntries(url.searchParams), search: url.search, at: Date.now() };
    api.requests.push(entry);
    const response = await api.handle(entry);
    try {
      if (response.abort) await route.abort('failed');
      else await route.fulfill({ status: response.status, contentType: 'application/json; charset=utf-8', body: JSON.stringify(response.body), headers: { 'Cache-Control': 'no-store' } });
    } catch {
      // The page aborted the request first (a superseded filter); nothing to answer.
    }
  });
  const diagnostics = { pageErrors: [], consoleErrors: [] };
  const page = await context.newPage();
  page.on('pageerror', (error) => diagnostics.pageErrors.push(error.message.slice(0, 300)));
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const text = message.text();
    if (/Failed to load resource: (the server responded with a status of (401|403|404|422|500)|net::ERR_FAILED|net::ERR_ABORTED)/.test(text)) return;
    if (expectedConsole.some((pattern) => pattern.test(text))) return;
    diagnostics.consoleErrors.push(text.slice(0, 300));
  });
  return { api, context, page, diagnostics };
}

async function waitFor(condition, message, timeout = 10000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    try {
      last = await condition();
      if (last) return last;
    } catch (error) {
      last = error;
    }
    await sleep(50);
  }
  throw new Error(`Timed out: ${message} (last: ${last instanceof Error ? last.message : JSON.stringify(last)})`);
}

const text = async (locator) => normalize(await locator.first().textContent());
const kpi = (page, id) => text(page.getByTestId(`management-analytics-kpi-${id}`));
async function waitForKpi(page, id, expected) {
  await waitFor(async () => (await page.getByTestId(`management-analytics-kpi-${id}`).count()) && (await kpi(page, id)) === String(expected), `KPI ${id} = ${expected}`);
}
async function waitForAnalyticsCount(api, count, message) {
  await waitFor(() => api.analyticsRequests().length >= count, message);
  return api.analyticsRequests()[count - 1];
}
const breakdownButton = (page, name) => page.locator('section[aria-labelledby="management-analytics-breakdown-title"]').getByRole('button', { name, exact: true });
const noHorizontalOverflow = (page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

async function auditAccessibility(page, label) {
  await page.mouse.move(0, 0);
  await page.waitForTimeout(350);
  await page.addScriptTag({ content: axe.source });
  const result = await page.evaluate(() => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } }));
  const blocking = result.violations
    .filter((violation) => ['serious', 'critical'].includes(violation.impact))
    .flatMap((violation) => violation.nodes.map((node) => ({ id: violation.id, target: node.target.join(' '), html: node.html.replace(/\s+/g, ' ').slice(0, 160) })));
  report.accessibility.push({ page: label, violations: result.violations.length, serious_or_critical: blocking.length, minor_or_moderate: result.violations.filter((item) => !['serious', 'critical'].includes(item.impact)).map((item) => item.id) });
  assert.deepEqual(blocking, [], `${label}: serious or critical accessibility violations`);
}

async function screenshot(page, name) {
  if (!evidenceDirectory) return;
  await mkdir(evidenceDirectory, { recursive: true });
  await page.screenshot({ path: path.join(evidenceDirectory, `${name}.png`) });
}

async function finish(name, { context, diagnostics, api }, details = {}, { allowUnexpected = false } = {}) {
  assert.deepEqual(diagnostics.pageErrors, [], `${name}: no uncaught page errors`);
  assert.deepEqual(diagnostics.consoleErrors, [], `${name}: no unexpected console errors`);
  if (!allowUnexpected) assert.deepEqual(api.unexpected().map((item) => item.path), [], `${name}: only the analytics, option, enterprise and session endpoints are read`);
  report.flows.push({ flow: name, analytics_requests: api.analyticsRequests().length, other_requests: api.requests.length - api.analyticsRequests().length, ...details });
  await context.close();
}

const flows = [];
const flow = (name, run) => flows.push({ name, run });

// ─── 1. Manager: populated snapshot, semantics, provenance, a11y ────────────
flow('manager-populated-desktop', async () => {
  const session = await openPage('manager', 'desktop');
  const { page, api } = session;
  await page.goto(`${baseUrl}/management-analytics`);
  await waitForKpi(page, 'active_problems', 15);
  await sleep(400);

  assert.equal(normalize(await page.locator('header h1').textContent()), 'Управленческая аналитика', 'page title');
  const navItem = page.getByRole('button', { name: 'Управленческая аналитика' });
  assert.equal(await navItem.getAttribute('aria-current'), 'page', 'the navigation item is current');

  // Executive KPIs are the TASK_232 values, not recomputed.
  const kpis = {};
  for (const id of ['monitored_fields', 'active_problems', 'overdue_cases', 'work_items', 'plans_pending_verification']) kpis[id] = await kpi(page, id);
  assert.deepEqual(kpis, { monitored_fields: '13', active_problems: '15', overdue_cases: '0', work_items: '3', plans_pending_verification: '5' });
  // Overdue: one late secondary item, no overdue case (the TASK_232 parity case).
  assert.equal(await text(page.getByTestId('management-analytics-overdue-work-items')), '1', 'late work items are their own unit');
  assert.match(await text(page.locator('[data-kpi="overdue_cases"]')), /Как «Просрочено» в Операционном центре/);

  // Workflow: every state once, snapshot wording, no conversion percentages.
  const states = {};
  for (const key of ['needs_inspection', 'inspection_active', 'awaiting_review', 'awaiting_decision', 'plan_active', 'reopened', 'work_active', 'awaiting_satellite_verification', 'verification_blocked', 'improved_awaiting_closure', 'not_improved']) {
    states[key] = await text(page.getByTestId(`management-analytics-state-${key}`));
  }
  assert.deepEqual(states, { needs_inspection: '3', inspection_active: '1', awaiting_review: '1', awaiting_decision: '1', plan_active: '2', reopened: '1', work_active: '1', awaiting_satellite_verification: '1', verification_blocked: '1', improved_awaiting_closure: '1', not_improved: '2' });
  const lifecycle = await text(page.locator('section[aria-labelledby="management-analytics-lifecycle-title"]'));
  assert.match(lifecycle, /Это не воронка конверсии/);
  assert.doesNotMatch(lifecycle, /%/, 'the workflow shows no conversion percentage');
  assert.deepEqual(await page.locator('[data-phase]').evaluateAll((items) => items.map((item) => item.getAttribute('data-phase'))), ['attention', 'inspection', 'review', 'plan', 'work', 'verification']);

  // Outcomes: only IMPROVED / UNCHANGED / WORSENED; the rest is apart.
  const verified = page.getByTestId('management-analytics-verified-outcomes');
  assert.deepEqual(await verified.locator('[data-outcome]').evaluateAll((items) => items.map((item) => item.getAttribute('data-outcome'))), ['improved', 'unchanged', 'worsened']);
  assert.deepEqual([
    await text(page.getByTestId('management-analytics-outcome-improved')),
    await text(page.getByTestId('management-analytics-outcome-unchanged')),
    await text(page.getByTestId('management-analytics-outcome-worsened')),
    await text(page.getByTestId('management-analytics-outcome-total')),
  ], ['2', '1', '1', '4']);
  const verifiedText = await text(verified);
  for (const label of ['Облачность', 'Недостаточно доказательств', 'Нет нового допустимого снимка', 'Слишком рано', 'качество']) {
    assert.ok(!verifiedText.includes(label), `${label} is not shown as a verified outcome`);
  }
  assert.equal(await text(page.getByTestId('management-analytics-unverified-total')), '2', 'unverified resolutions are counted apart');
  assert.match(await text(page.getByTestId('management-analytics-unverified')), /Не результат мер/);
  assert.equal(await text(page.getByTestId('management-analytics-reopen-events')), '2', 'reopen events of the period');
  assert.equal(await text(page.getByTestId('management-analytics-reopened-now')), '1', 'reopened now is the current state');
  assert.match(await text(page.locator('article[aria-labelledby="management-analytics-outcomes-title"]')), /не доказательство агрономической причины, урожайности или экономического эффекта/);

  // Completion keeps numerator, denominator and the server's rate.
  assert.equal(await text(page.getByTestId('management-analytics-completion-work_completion')), '7 из 9 (77,8 %)');
  assert.equal(await text(page.getByTestId('management-analytics-completion-verification_completion')), '5 из 7 (71,4 %)');
  assert.equal(await text(page.getByTestId('management-analytics-completion-plan_closure')), '1 из 10 (10 %)');

  // Cycle times show their sample counts; the alert interval is explained, not invented.
  assert.deepEqual([
    await text(page.getByTestId('management-analytics-samples-signal_to_inspection_opened')),
    await text(page.getByTestId('management-analytics-samples-inspection_opened_to_reviewed')),
    await text(page.getByTestId('management-analytics-samples-case_opened_to_verified_closure')),
  ], ['3', '11', '0']);
  const cycleTimes = await text(page.getByTestId('management-analytics-cycle-times'));
  assert.match(cycleTimes, /нужно ≥ 10/);
  assert.match(cycleTimes, /нет наблюдений/);
  assert.match(await text(page.locator('article[aria-labelledby="management-analytics-cycle-times-title"]')), /Предупреждение → осмотр открыт — Не измеряется/);

  // Context and provenance.
  assert.equal(await text(page.getByTestId('management-analytics-period')), `${formatPeriod(TODAY_PERIOD.dateFrom, TODAY_PERIOD.dateTo)} · 30 дн. включительно`);
  assert.equal(await text(page.getByTestId('management-analytics-scope')), 'Предприятие: Альфа Агро');
  assert.equal(await text(page.getByTestId('management-analytics-generated')), '26.09.2026, 12:21 · Ташкент');
  const definitions = page.getByTestId('management-analytics-definitions');
  assert.match(await text(definitions), /management_analytics_v1/);
  assert.match(await text(definitions), /r3-f-v1/);
  assert.match(await text(definitions), /TASK_209\) не используется/);
  await definitions.locator('summary').click();
  assert.equal(await definitions.evaluate((element) => element.open), true, 'the definitions disclosure opens');

  // Crop semantics: the current classification, never crop at event time.
  assert.equal(await text(page.locator('label[for="management-analytics-crop"]')), 'Текущая культура');
  assert.deepEqual(await page.getByTestId('management-analytics-crop').locator('option').evaluateAll((items) => items.map((item) => item.textContent)), ['Все культуры', 'Пшеница', 'Хлопок']);
  await breakdownButton(page, 'Текущие культуры').click();
  const breakdown = page.getByTestId('management-analytics-breakdown');
  await waitFor(async () => (await breakdown.getAttribute('data-dimension')) === 'current_crops', 'crop dimension');
  assert.match(await text(page.locator('section[aria-labelledby="management-analytics-breakdown-title"]')), /не культура на момент события/);
  assert.match(await text(breakdown), /Культура не указана/);

  // Manager scope is the server's: no enterprise selector, the own enterprise named.
  assert.equal(await page.getByTestId('management-analytics-enterprise').count(), 0, 'a manager cannot choose an enterprise');
  assert.equal(await text(page.getByTestId('management-analytics-own-enterprise')), 'Альфа Агро');
  assert.equal(await breakdownButton(page, 'Предприятия').count(), 0, 'no enterprise breakdown in a tenant scope');

  // One analytics request, the exact accepted parameters.
  await sleep(300);
  assert.equal(api.analyticsRequests().length, 1, 'no duplicate request on load');
  assert.deepEqual(api.analyticsRequests()[0].query, {
    date_from: TODAY_PERIOD.dateFrom, date_to: TODAY_PERIOD.dateTo, granularity: 'week', field_limit: '50', field_offset: '0',
  });
  assert.equal(api.requests.filter((item) => item.path === '/api/operational-center/filter-options').length, 1, 'field options loaded once');
  assert.equal(api.requests.filter((item) => item.path === '/api/operational-center/filter-options')[0].search, '', 'the manager never sends an enterprise for options');

  await auditAccessibility(page, 'manager-desktop');
  await screenshot(page, 'manager-desktop');
  await finish('manager-populated-desktop', session, { kpis, states, verified: ['improved', 'unchanged', 'worsened'] });
});

for (const viewportName of ['tablet', 'mobile']) {
  flow(`manager-responsive-${viewportName}`, async () => {
    const session = await openPage('manager', viewportName);
    const { page } = session;
    await page.goto(`${baseUrl}/management-analytics`);
    await waitForKpi(page, 'active_problems', 15);
    await sleep(300);
    assert.equal(await noHorizontalOverflow(page), true, `${viewportName}: no horizontal page scroll`);
    const pageSection = page.getByTestId('management-analytics-page');
    assert.equal(await pageSection.evaluate((element) => element.scrollWidth <= element.clientWidth + 1), true, `${viewportName}: page content fits`);
    const header = await page.locator('header').first().boundingBox();
    const filters = await page.locator('section[aria-labelledby="management-analytics-filters-title"]').boundingBox();
    assert.ok(filters.y >= header.y + header.height - 1, `${viewportName}: content starts below the fixed header`);
    for (const id of ['monitored_fields', 'active_problems', 'overdue_cases', 'work_items', 'plans_pending_verification']) {
      assert.equal(await page.getByTestId(`management-analytics-kpi-${id}`).isVisible(), true, `${viewportName}: KPI ${id} visible`);
    }
    const table = page.getByTestId('management-analytics-breakdown');
    const scroller = table.locator('xpath=..');
    const scrollable = await scroller.evaluate((element) => ({ overflowX: getComputedStyle(element).overflowX, wider: element.scrollWidth > element.clientWidth }));
    assert.equal(scrollable.overflowX, 'auto', `${viewportName}: the breakdown table scrolls inside its card`);
    if (viewportName === 'mobile') {
      assert.equal(scrollable.wider, true, 'mobile: the wide table scrolls horizontally instead of clipping');
      const sticky = await table.locator('tbody th').first().evaluate((element) => getComputedStyle(element).position);
      assert.equal(sticky, 'sticky', 'mobile: row names stay visible while scrolling');
      const fieldSelect = await page.getByTestId('management-analytics-field').boundingBox();
      assert.ok(fieldSelect.width >= 300, 'mobile: filters use the full width');
      await page.getByRole('button', { name: 'Открыть основную навигацию' }).click();
      await waitFor(() => page.getByRole('dialog', { name: 'Основная навигация' }).isVisible(), 'mobile navigation opens');
      assert.equal(await page.getByRole('dialog', { name: 'Основная навигация' }).getByRole('button', { name: 'Управленческая аналитика' }).isVisible(), true, 'mobile navigation lists the page');
      await page.keyboard.press('Escape');
      await auditAccessibility(page, 'manager-mobile');
    }
    await screenshot(page, `manager-${viewportName}`);
    await finish(`manager-responsive-${viewportName}`, session, { viewport: VIEWPORTS[viewportName].viewport, table_overflow: scrollable });
  });
}

// ─── 2. Admin: filters, dependent options, reset, paging, drill-down ───────
flow('admin-filters', async () => {
  const session = await openPage('admin', 'desktop');
  const { page, api } = session;
  await page.goto(`${baseUrl}/management-analytics`);
  await waitForKpi(page, 'active_problems', 72);
  const field = page.getByTestId('management-analytics-field');
  const crop = page.getByTestId('management-analytics-crop');
  const enterprise = page.getByTestId('management-analytics-enterprise');
  assert.equal(await field.isDisabled(), true, 'an admin chooses the enterprise before a field');
  assert.match(await text(page.locator('#management-analytics-field-hint')), /Сначала выберите предприятие/);
  assert.equal(api.requests.filter((item) => item.path === '/api/operational-center/filter-options').length, 0, 'no unbounded all-field list is requested');
  assert.deepEqual(await enterprise.locator('option').evaluateAll((items) => items.map((item) => item.textContent)), ['Все предприятия', 'Альфа Агро', 'Бета Хлопок', 'Гамма Зерно']);
  assert.equal(await text(page.getByTestId('management-analytics-scope')), 'Все предприятия');
  await auditAccessibility(page, 'admin-desktop');

  // Enterprise narrows the server scope and reloads the field options.
  let count = api.analyticsRequests().length;
  await enterprise.selectOption('8');
  let request = await waitForAnalyticsCount(api, count + 1, 'enterprise request');
  assert.deepEqual({ enterprise: request.query.enterprise_id, field: request.query.field_id, crop: request.query.current_crop_type_id }, { enterprise: '8', field: undefined, crop: undefined });
  await waitForKpi(page, 'active_problems', 24);
  await waitFor(async () => !(await field.isDisabled()) && (await field.locator('option').count()) === 7, 'field options of enterprise 8');
  assert.equal(api.requests.filter((item) => item.path === '/api/operational-center/filter-options').at(-1).query.enterprise_id, '8');
  assert.equal(await text(page.getByTestId('management-analytics-scope')), 'Предприятие: Бета Хлопок');

  // Current crop: options are the current classes of this scope, sent as current_crop_type_id.
  await waitFor(async () => !(await crop.isDisabled()), 'crop options ready');
  count = api.analyticsRequests().length;
  await crop.selectOption('301');
  request = await waitForAnalyticsCount(api, count + 1, 'crop request');
  assert.equal(request.query.current_crop_type_id, '301');
  assert.equal(request.query.enterprise_id, '8');
  assert.equal('crop_type_id' in request.query, false, 'a bare crop_type_id is never sent');
  await waitFor(async () => (await text(page.getByTestId('management-analytics-scope'))) === 'Предприятие: Бета Хлопок · Текущая культура: Хлопок', 'crop scope label');

  // A field resets the crop filter and locks it.
  count = api.analyticsRequests().length;
  await field.selectOption('801');
  request = await waitForAnalyticsCount(api, count + 1, 'field request');
  assert.deepEqual({ enterprise: request.query.enterprise_id, field: request.query.field_id, crop: request.query.current_crop_type_id }, { enterprise: '8', field: '801', crop: undefined });
  await waitFor(() => crop.isDisabled(), 'crop select locked for one field');
  assert.equal(await crop.inputValue(), '');

  // Period presets, then a refused and an accepted custom period.
  count = api.analyticsRequests().length;
  await page.getByRole('button', { name: '7 дней' }).click();
  request = await waitForAnalyticsCount(api, count + 1, 'preset request');
  const week = presetPeriod(7);
  assert.deepEqual({ from: request.query.date_from, to: request.query.date_to, field: request.query.field_id, offset: request.query.field_offset }, { from: week.dateFrom, to: week.dateTo, field: '801', offset: '0' });
  assert.equal(await page.getByRole('button', { name: '7 дней' }).getAttribute('aria-pressed'), 'true');
  count = api.analyticsRequests().length;
  await page.getByTestId('management-analytics-date-from').fill('2026-09-20');
  await page.getByTestId('management-analytics-date-to').fill('2026-09-10');
  await page.getByRole('button', { name: 'Применить период' }).click();
  await waitFor(async () => /позже его окончания/.test(await text(page.locator('#management-analytics-period-help'))), 'inline period error');
  await sleep(300);
  assert.equal(api.analyticsRequests().length, count, 'an invalid period sends nothing');
  await page.getByTestId('management-analytics-date-to').fill('2026-09-25');
  await page.getByRole('button', { name: 'Применить период' }).click();
  request = await waitForAnalyticsCount(api, count + 1, 'custom period request');
  assert.deepEqual({ from: request.query.date_from, to: request.query.date_to }, { from: '2026-09-20', to: '2026-09-25' });
  await waitFor(async () => (await text(page.getByTestId('management-analytics-period'))).startsWith('20.09.2026 — 25.09.2026'), 'effective period shown');
  assert.equal(await page.getByRole('button', { name: '7 дней' }).getAttribute('aria-pressed'), 'false');

  // Reset is deterministic: default period, no narrowing, first page, weekly buckets.
  count = api.analyticsRequests().length;
  await page.getByRole('button', { name: 'Сбросить фильтры' }).first().click();
  request = await waitForAnalyticsCount(api, count + 1, 'reset request');
  assert.deepEqual(request.query, { date_from: TODAY_PERIOD.dateFrom, date_to: TODAY_PERIOD.dateTo, granularity: 'week', field_limit: '50', field_offset: '0' });
  await waitForKpi(page, 'active_problems', 72);
  assert.equal(await enterprise.inputValue(), '');
  assert.equal(await field.isDisabled(), true);
  assert.equal(await page.getByRole('button', { name: '30 дней' }).getAttribute('aria-pressed'), 'true');

  // Server paging keeps the frame (same scope); granularity is a view change too.
  await breakdownButton(page, 'Поля').click();
  assert.equal(normalize(await page.getByTestId('management-analytics-field-page').textContent()), '1–50 из 120');
  api.analytics = async (entry) => {
    if (entry.query.field_offset === '50') await sleep(800);
    return null;
  };
  count = api.analyticsRequests().length;
  await page.getByRole('button', { name: 'Далее' }).click();
  request = await waitForAnalyticsCount(api, count + 1, 'page 2 request');
  assert.deepEqual({ offset: request.query.field_offset, limit: request.query.field_limit, from: request.query.date_from }, { offset: '50', limit: '50', from: TODAY_PERIOD.dateFrom });
  await waitFor(async () => (await page.getByTestId('management-analytics-status').getAttribute('data-state')) === 'refreshing', 'same-scope refresh state');
  assert.equal(await page.getByTestId('management-analytics-kpi-active_problems').isVisible(), true, 'no skeleton flash while paging');
  assert.equal(await page.getByTestId('management-analytics-loading').count(), 0, 'the previous render is held, not replaced by a skeleton');
  await waitFor(async () => normalize(await page.getByTestId('management-analytics-field-page').textContent()) === '51–100 из 120', 'second page shown');
  api.analytics = null;
  count = api.analyticsRequests().length;
  await page.getByRole('button', { name: 'Месяцы' }).click();
  request = await waitForAnalyticsCount(api, count + 1, 'granularity request');
  assert.equal(request.query.granularity, 'month');
  assert.equal(request.query.field_offset, '50', 'a view change keeps the field page');

  // Drill-down from the enterprise breakdown narrows the scope.
  await breakdownButton(page, 'Предприятия').click();
  count = api.analyticsRequests().length;
  await page.getByRole('button', { name: 'Показать аналитику предприятия «Гамма Зерно»' }).click();
  request = await waitForAnalyticsCount(api, count + 1, 'drill-down request');
  assert.equal(request.query.enterprise_id, '9');
  assert.equal(request.query.field_offset, '0');
  await waitForKpi(page, 'active_problems', 27);

  // Keyboard: presets are real buttons.
  count = api.analyticsRequests().length;
  await page.getByRole('button', { name: '90 дней' }).focus();
  await page.keyboard.press('Enter');
  request = await waitForAnalyticsCount(api, count + 1, 'keyboard preset');
  assert.equal(request.query.date_from, presetPeriod(90).dateFrom);
  await screenshot(page, 'admin-desktop');
  await finish('admin-filters', session, { steps: ['enterprise', 'crop', 'field', 'preset', 'invalid period', 'custom period', 'reset', 'page', 'granularity', 'drill-down', 'keyboard'] });
});

// ─── 3. A superseded response never replaces a newer one ────────────────────
flow('admin-stale-response', async () => {
  const session = await openPage('admin', 'desktop');
  const { page, api } = session;
  await page.goto(`${baseUrl}/management-analytics`);
  await waitForKpi(page, 'active_problems', 72);
  api.analytics = async (entry) => {
    if (entry.query.enterprise_id === '7') await sleep(1500);
    return null;
  };
  const enterprise = page.getByTestId('management-analytics-enterprise');
  await enterprise.selectOption('7');
  await waitFor(() => api.analyticsRequests().some((item) => item.query.enterprise_id === '7'), 'slow request sent');
  await waitFor(async () => (await page.getByTestId('management-analytics-loading').count()) === 1, 'new scope shows loading');
  assert.equal(await page.getByTestId('management-analytics-kpi-active_problems').count(), 0, 'no stale numbers from the previous scope');
  await enterprise.selectOption('8');
  await waitForKpi(page, 'active_problems', 24);
  await sleep(1900);
  assert.equal(await kpi(page, 'active_problems'), '24', 'the slow, superseded response is ignored');
  assert.equal(await text(page.getByTestId('management-analytics-scope')), 'Предприятие: Бета Хлопок');
  await finish('admin-stale-response', session, { superseded: 'enterprise 7 (1500 ms)', shown: 'enterprise 8' });
});

// ─── 4. Quiet and empty scopes are not errors ───────────────────────────────
flow('manager-quiet-and-empty', async () => {
  const session = await openPage('manager', 'desktop');
  const { page, api } = session;
  api.analytics = async (entry) => ({ status: 200, body: entry.query.current_crop_type_id ? emptyScopeSnapshot({ current_crop_type_id: Number(entry.query.current_crop_type_id) }) : quietSnapshot() });
  await page.goto(`${baseUrl}/management-analytics`);
  await waitForKpi(page, 'active_problems', 0);
  assert.match(await text(page.locator('section[aria-labelledby="management-analytics-lifecycle-title"]')), /Активных проблем в выбранной области сейчас нет/);
  assert.match(await text(page.locator('article[aria-labelledby="management-analytics-outcomes-title"]')), /за период нет циклов с завершённой проверкой/);
  for (const key of ['work_completion', 'verification_completion', 'plan_closure']) {
    assert.equal(await text(page.getByTestId(`management-analytics-completion-${key}`)), 'нет данных в выборке за период', `${key}: zero denominator`);
  }
  assert.match(await text(page.getByTestId('management-analytics-cycle-times')), /нет наблюдений/);
  assert.equal(await page.getByTestId('management-analytics-chart-empty').isVisible(), true, 'an all-zero chart says so');
  assert.equal(await page.getByTestId('management-analytics-error').count(), 0, 'zero is not an error');
  // An unused current crop: no field matches, which is a valid empty result.
  await page.getByTestId('management-analytics-crop').selectOption('301');
  await waitFor(() => page.getByTestId('management-analytics-empty').isVisible(), 'empty scope panel');
  assert.equal(await page.getByTestId('management-analytics-kpi-active_problems').count(), 0, 'no numbers for an empty scope');
  assert.equal(await page.getByTestId('management-analytics-error').count(), 0, 'empty is not an error');
  await screenshot(page, 'manager-empty-scope');
  await page.getByTestId('management-analytics-empty').getByRole('button', { name: 'Сбросить фильтры' }).click();
  await waitForKpi(page, 'active_problems', 0);
  await finish('manager-quiet-and-empty', session);
});

// ─── 5. Errors: 403, 404, 422, 5xx, network — never shown as empty ─────────
flow('manager-errors', async () => {
  const session = await openPage('manager', 'desktop');
  const { page, api } = session;
  let mode = 'forbidden';
  api.analytics = async (entry) => {
    if (mode === 'forbidden') return { status: 403, body: { detail: 'Management analytics requires a management role' } };
    if (mode === 'server') return { status: 500, body: { detail: 'Internal Server Error' } };
    if (mode === 'network') return { abort: true };
    if (entry.query.field_id) return { status: 404, body: { detail: 'Field not found' } };
    if (entry.query.current_crop_type_id) return { status: 422, body: { detail: { code: 'result_too_large', resource: 'management_analytics.current_crops', row_cap: 200 } } };
    return null;
  };
  await page.goto(`${baseUrl}/management-analytics`);
  const error = page.getByTestId('management-analytics-error');
  await waitFor(() => error.isVisible(), '403 panel');
  assert.equal(await error.getAttribute('data-error-kind'), 'forbidden');
  assert.match(await text(error), /Нет доступа к управленческой аналитике/);
  assert.equal(await error.getByRole('button', { name: 'Повторить' }).count(), 0, 'a 403 offers no pointless retry');
  assert.equal(await page.getByTestId('management-analytics-kpi-active_problems').count(), 0, 'no numbers after a 403');
  assert.equal(api.analyticsRequests().length, 1, '403 is not retried');
  await screenshot(page, 'manager-403');

  // 5xx: bounded automatic retries of the shared client, then an explicit retry.
  mode = 'server';
  await page.getByRole('button', { name: 'Обновить' }).click();
  await waitFor(async () => (await error.getAttribute('data-error-kind')) === 'server', 'server error panel', 15000);
  assert.equal(api.analyticsRequests().length, 4, 'one request plus two bounded automatic retries');
  assert.match(await text(error), /ошибка загрузки не означает, что данных нет/);
  mode = 'ok';
  await error.getByRole('button', { name: 'Повторить' }).click();
  await waitForKpi(page, 'active_problems', 15);
  assert.equal(api.analyticsRequests().length, 5, 'the explicit retry sends exactly one request');

  // 404 for a filter target: explained, and reset recovers.
  const field = page.getByTestId('management-analytics-field');
  await waitFor(async () => !(await field.isDisabled()), 'field options');
  await field.selectOption({ index: 1 });
  await waitFor(async () => (await error.count()) && (await error.getAttribute('data-error-kind')) === 'not_found', '404 panel');
  assert.match(await text(error), /Выбранное поле не найдено или не входит в вашу область доступа/);
  assert.equal(await page.getByTestId('management-analytics-kpi-active_problems').count(), 0, 'no stale numbers after a 404');
  await error.getByRole('button', { name: 'Сбросить фильтры' }).click();
  await waitForKpi(page, 'active_problems', 15);

  // 422 for a filter: explained as a refused filter.
  await page.getByTestId('management-analytics-crop').selectOption('301');
  await waitFor(async () => (await error.count()) && (await error.getAttribute('data-error-kind')) === 'invalid', '422 panel');
  assert.match(await text(error), /слишком много строк/);
  await error.getByRole('button', { name: 'Сбросить фильтры' }).click();
  await waitForKpi(page, 'active_problems', 15);

  // Network failure on a same-scope refresh keeps the numbers with a banner.
  mode = 'network';
  const before = api.analyticsRequests().length;
  await page.getByRole('button', { name: 'Обновить' }).click();
  const banner = page.getByTestId('management-analytics-refresh-error');
  await waitFor(() => banner.isVisible(), 'refresh error banner');
  assert.equal(api.analyticsRequests().length, before + 1, 'a request without a response is not retried automatically');
  assert.match(await text(banner), /Нет связи с сервером/);
  assert.match(await text(banner), /сформированные 26\.09\.2026, 12:21/);
  assert.equal(await kpi(page, 'active_problems'), '15', 'same-scope numbers stay, labelled by their time');
  mode = 'ok';
  await banner.getByRole('button', { name: 'Повторить' }).click();
  await waitFor(async () => (await banner.count()) === 0, 'banner cleared');
  await finish('manager-errors', session, { kinds: ['forbidden', 'server', 'not_found', 'invalid', 'network'] });
});

// ─── 6. 401 ends the session; other roles never reach the page ─────────────
flow('manager-401', async () => {
  const session = await openPage('manager', 'desktop');
  const { page, api } = session;
  api.analytics = async () => ({ status: 401, body: { detail: 'Could not validate credentials' } });
  await page.goto(`${baseUrl}/management-analytics`);
  await page.waitForURL((url) => url.pathname === '/login', { timeout: 10000 });
  assert.equal(await page.evaluate(() => localStorage.getItem('agrosat_token')), null, 'the rejected credential is dropped');
  assert.equal(api.analyticsRequests().length, 1, '401 is not retried');
  await finish('manager-401', session, { redirected_to: '/login' });
});

flow('unauthenticated', async () => {
  const session = await openPage('manager', 'desktop', { token: false });
  const { page, api } = session;
  await page.goto(`${baseUrl}/management-analytics`);
  await page.waitForURL((url) => url.pathname === '/login', { timeout: 10000 });
  assert.equal(api.analyticsRequests().length, 0, 'no analytics request without a session');
  await finish('unauthenticated', session, { redirected_to: '/login' });
});

for (const [role, home] of [['viewer', '/dashboard'], ['agronomist', '/inspections']]) {
  flow(`${role}-no-access`, async () => {
    const session = await openPage(role, 'desktop', { expectedConsole: [/.*/] });
    const { page, api } = session;
    await page.goto(`${baseUrl}/management-analytics`);
    await page.waitForURL((url) => url.pathname === home, { timeout: 10000 });
    await page.getByRole('navigation', { name: 'Основная навигация' }).waitFor();
    await sleep(500);
    assert.equal(await page.getByRole('button', { name: 'Управленческая аналитика' }).count(), 0, `${role}: no navigation item`);
    assert.equal(api.analyticsRequests().length, 0, `${role}: the page never loads for this role`);
    await finish(`${role}-no-access`, session, { redirected_to: home }, { allowUnexpected: true });
  });
}

// ─── Run ────────────────────────────────────────────────────────────────────
let failures = 0;
const only = process.env.MA_ONLY ? new Set(process.env.MA_ONLY.split(',')) : null;
for (const item of flows.filter((entry) => !only || only.has(entry.name))) {
  try {
    await item.run();
    console.log(`PASS ${item.name}`);
  } catch (error) {
    failures += 1;
    console.log(`FAIL ${item.name}: ${error.message}`);
    report.flows.push({ flow: item.name, failure: error.message.slice(0, 500) });
  }
}
await browser.close();
await new Promise((resolve) => server.httpServer.close(resolve));
if (evidenceDirectory) {
  await mkdir(evidenceDirectory, { recursive: true });
  await writeFile(path.join(evidenceDirectory, 'management-analytics-browser.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
}
console.log(JSON.stringify({ status: failures ? 'FAIL' : 'PASS', suite: 'TASK_233 management analytics browser qualification', flows: flows.length, failures, accessibility: report.accessibility }));
process.exit(failures ? 1 : 0);
