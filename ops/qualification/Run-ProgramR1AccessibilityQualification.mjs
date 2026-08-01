import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { mkdir, readFile, rename, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { promisify } from 'node:util';


const execFileAsync = promisify(execFile);
const requiredEnvironment = [
  'R1_CREDENTIALS_PATH',
  'R1_EVIDENCE_DIR',
  'R1_PLAYWRIGHT_PACKAGE_ROOT',
  'R1_CHROMIUM_EXECUTABLE',
  'R1_FRONTEND_URL',
  'R1_EXPECTED_HEAD',
  'R1_WORKTREE_ROOT',
];
for (const name of requiredEnvironment) {
  if (!process.env[name]) throw new Error(`Missing required qualification setting: ${name}`);
}

const evidenceDir = path.resolve(process.env.R1_EVIDENCE_DIR);
if (!evidenceDir.toLowerCase().startsWith('c:\\agrosat_backups\\program_r1_completion_run\\')) {
  throw new Error('Evidence directory is outside the approved completion root.');
}
const credentialsPath = path.resolve(process.env.R1_CREDENTIALS_PATH);
if (!credentialsPath.toLowerCase().startsWith('c:\\tmp\\agrosat_r1_completion_')) {
  throw new Error('Credentials are outside the isolated temporary root.');
}
const worktreeRoot = path.resolve(process.env.R1_WORKTREE_ROOT);
const frontendUrl = new URL(process.env.R1_FRONTEND_URL).origin;
if (new URL(frontendUrl).hostname !== '127.0.0.1') {
  throw new Error('Qualification frontend must be loopback-only.');
}

const gitHead = (await execFileAsync('git', ['rev-parse', 'HEAD'], {
  cwd: worktreeRoot,
  windowsHide: true,
  timeout: 10000,
})).stdout.trim();
assert.equal(gitHead, process.env.R1_EXPECTED_HEAD, 'Qualification HEAD is not exact');
const worktreeDirty = Boolean((await execFileAsync('git', ['status', '--porcelain'], {
  cwd: worktreeRoot,
  windowsHide: true,
  timeout: 10000,
})).stdout.trim());
if (worktreeDirty && process.env.R1_ALLOW_DIRTY_REPAIR_CANDIDATE !== 'true') {
  throw new Error('Qualification requires a clean worktree unless an explicit candidate run is requested.');
}

await mkdir(evidenceDir, { recursive: true });
const screenshotDir = path.join(evidenceDir, 'screenshots');
await mkdir(screenshotDir, { recursive: true });
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const requireFromPlaywright = createRequire(
  path.join(path.resolve(process.env.R1_PLAYWRIGHT_PACKAGE_ROOT), 'package.json'),
);
const { chromium } = requireFromPlaywright('playwright-core');
const ONE_PIXEL_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
);
const MAP_STUB_HOSTS = new Set([
  'tile.openstreetmap.org',
  'server.arcgisonline.com',
  'demotiles.maplibre.org',
]);

const report = {
  schemaVersion: 1,
  recordedAt: new Date().toISOString(),
  exactHead: process.env.R1_EXPECTED_HEAD,
  worktreeState: worktreeDirty ? 'dirty_repair_candidate' : 'clean_exact_head',
  runtimeClass: 'isolated_ephemeral_real_chromium_keyboard_focus_responsive',
  status: 'running',
  marker: null,
  stage: 'initializing',
  checks: [],
  screenshots: [],
  browser: {
    consoleErrors: [],
    pageErrors: [],
    unexpectedFailedRequests: [],
    expectedCancelledRequests: [],
    expectedAuthDenials: [],
    unexpectedHttpErrors: [],
  },
  credentialsIncluded: false,
  productionContacted: false,
  productionWrites: 0,
};

function check(name, passed, details = {}) {
  report.checks.push({ name, passed: Boolean(passed), ...details });
}

function stage(value) {
  report.stage = value;
}

async function atomicJson(name, value) {
  const target = path.join(evidenceDir, name);
  const temporary = `${target}.${randomUUID()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  await rename(temporary, target);
}

async function capture(page, role, viewport, route, action) {
  const index = String(report.screenshots.length + 1).padStart(2, '0');
  const fileName = `${index}-${role}-${viewport.width}x${viewport.height}-${action}.png`;
  const filePath = path.join(screenshotDir, fileName);
  await page.screenshot({ path: filePath, fullPage: true });
  const fileStat = await stat(filePath);
  report.screenshots.push({
    file: path.relative(evidenceDir, filePath),
    role,
    viewport: `${viewport.width}x${viewport.height}`,
    route,
    action,
    exactHead: report.exactHead,
    bytes: fileStat.size,
    sha256: createHash('sha256').update(await readFile(filePath)).digest('hex'),
  });
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function installContextRoutes(context) {
  await context.route('**/*', async (route) => {
    const parsed = new URL(route.request().url());
    if (parsed.hostname === 'fonts.googleapis.com') {
      return route.fulfill({ status: 200, contentType: 'text/css', body: '' });
    }
    if (parsed.hostname === 'fonts.gstatic.com') {
      return route.fulfill({ status: 204, body: '' });
    }
    if (!MAP_STUB_HOSTS.has(parsed.hostname)) return route.continue();
    if (parsed.pathname.endsWith('.pbf')) {
      return route.fulfill({ status: 204, contentType: 'application/x-protobuf', body: Buffer.alloc(0) });
    }
    return route.fulfill({ status: 200, contentType: 'image/png', body: ONE_PIXEL_PNG });
  });
}

function observe(page) {
  page.on('console', (message) => {
    if (message.type() === 'error') {
      const text = message.text().slice(0, 1000);
      if (report.stage === 'disabled_login_error' && /status of (401|403)/.test(text)) {
        report.browser.expectedAuthDenials.push({ stage: report.stage, channel: 'console', classification: 'disabled_user_denied' });
      } else {
        report.browser.consoleErrors.push({ stage: report.stage, text });
      }
    }
  });
  page.on('pageerror', (error) => {
    report.browser.pageErrors.push({ stage: report.stage, text: String(error).slice(0, 1000) });
  });
  page.on('requestfailed', (request) => {
    const failure = request.failure()?.errorText || 'unknown';
    const item = {
      stage: report.stage,
      method: request.method(),
      path: new URL(request.url()).pathname,
      error: failure,
    };
    if (failure.includes('ERR_ABORTED')) report.browser.expectedCancelledRequests.push(item);
    else report.browser.unexpectedFailedRequests.push(item);
  });
  page.on('response', (response) => {
    if (response.status() >= 400) {
      const pathname = new URL(response.url()).pathname;
      if (report.stage === 'disabled_login_error' && pathname === '/api/auth/login' && [401, 403].includes(response.status())) {
        report.browser.expectedAuthDenials.push({
          stage: report.stage,
          channel: 'http',
          path: pathname,
          status: response.status(),
          classification: 'disabled_user_denied',
        });
      } else {
        report.browser.unexpectedHttpErrors.push({
          stage: report.stage,
          method: response.request().method(),
          path: pathname,
          status: response.status(),
        });
      }
    }
  });
}

async function newPage(browser, viewport) {
  const context = await browser.newContext({ viewport });
  await installContextRoutes(context);
  const page = await context.newPage();
  observe(page);
  return { context, page };
}

async function activeDescriptor(page) {
  return page.evaluate(() => {
    const active = document.activeElement;
    if (!active) return null;
    return {
      tag: active.tagName.toLowerCase(),
      id: active.id || null,
      type: active.getAttribute('type'),
      ariaLabel: active.getAttribute('aria-label'),
      text: (active.textContent || '').trim().slice(0, 80),
    };
  });
}

async function keyboardLogin(page, role) {
  await page.goto(`${frontendUrl}/login`, { waitUntil: 'networkidle' });
  await page.getByLabel('Email').waitFor();
  const initial = await activeDescriptor(page);
  check(`${report.stage}_email_autofocus`, initial?.id === 'login-email', { active: initial });
  if (initial?.id !== 'login-email') await page.getByLabel('Email').focus();
  await page.keyboard.type(credentials.users[role].email);
  await page.keyboard.press('Tab');
  const passwordFocus = await activeDescriptor(page);
  check(`${report.stage}_password_keyboard_reachable`, passwordFocus?.id === 'login-password', { active: passwordFocus });
  await page.keyboard.type(credentials.users[role].password);
  await page.keyboard.press('Tab');
  const submitFocus = await activeDescriptor(page);
  const submit = page.locator('button[type="submit"]');
  const submitBox = await submit.boundingBox();
  check(`${report.stage}_submit_keyboard_reachable`, submitFocus?.type === 'submit', { active: submitFocus });
  check(`${report.stage}_submit_visible_when_focused`, Boolean(
    submitBox && submitBox.y >= 0 && submitBox.y + submitBox.height <= (await page.viewportSize()).height,
  ), { submitBox });
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => location.pathname !== '/login');
}

async function horizontalOverflow(page) {
  return page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    overflowPx: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
  }));
}

const browser = await chromium.launch({
  executablePath: process.env.R1_CHROMIUM_EXECUTABLE,
  headless: true,
});

try {
  const desktopViewport = { width: 1440, height: 900 };
  const desktop = await newPage(browser, desktopViewport);
  const { page } = desktop;
  stage('desktop_manager_keyboard_login');
  await keyboardLogin(page, 'manager');

  const fields = await page.evaluate(async () => {
    const response = await fetch('/api/fields/?include_ndvi=true', {
      headers: { authorization: `Bearer ${localStorage.getItem('agrosat_token')}` },
      cache: 'no-store',
    });
    return response.json();
  });
  const fieldOne = fields.find((item) => Number(item.id) === 1);
  const fieldTwo = fields.find((item) => Number(item.id) === 2);
  assert.ok(fieldOne && fieldTwo);

  stage('desktop_field_list_keyboard');
  const navigation = page.getByRole('navigation', { name: 'Основная навигация' });
  const fieldsNav = navigation.getByRole('button', { name: 'Поля', exact: true });
  await fieldsNav.focus();
  await page.keyboard.press('Enter');
  await page.waitForURL(/\/fields$/);
  const fieldPanel = page.locator('#field-list-panel');
  await fieldPanel.waitFor();
  const semanticFieldButton = fieldPanel.getByRole('button', {
    name: `Выбрать поле ${fieldOne.name}`,
    exact: true,
  });
  const semanticFieldCount = await semanticFieldButton.count();
  check('field_list_item_has_button_semantics', semanticFieldCount === 1, { count: semanticFieldCount });
  if (semanticFieldCount === 1) {
    await semanticFieldButton.focus();
    const focused = await activeDescriptor(page);
    await page.keyboard.press('Enter');
    check('field_list_item_activates_with_keyboard', focused?.tag === 'button', { active: focused });
  } else {
    const visualRow = fieldPanel.getByText(fieldOne.name, { exact: true })
      .locator('xpath=ancestor::div[contains(@class,"cursor-pointer")][1]');
    await visualRow.click();
    check('field_list_item_activates_with_keyboard', false, { fallbackMouseUsed: true });
  }
  await page.goto(`${frontendUrl}/fields/${fieldOne.id}`, { waitUntil: 'networkidle' });
  const detailHeading = page.locator('h2').filter({ hasText: fieldOne.name }).last();
  await detailHeading.waitFor();
  const backButton = detailHeading.locator('xpath=../preceding-sibling::button[1]');
  const backName = await backButton.getAttribute('aria-label');
  check('field_detail_back_button_has_accessible_name', Boolean(backName), { accessibleName: backName });
  await capture(page, 'manager', desktopViewport, '/fields', 'keyboard-field-selection');

  stage('desktop_enterprise_table_and_dialog');
  await page.goto(`${frontendUrl}/enterprises/1`, { waitUntil: 'networkidle' });
  const enterpriseRow = page.locator('tbody tr').filter({ hasText: fieldTwo.name }).first();
  await enterpriseRow.waitFor();
  const historyButton = enterpriseRow.getByRole('button', {
    name: `Открыть историю NDVI поля ${fieldTwo.name}`,
    exact: true,
  });
  const historyButtonCount = await historyButton.count();
  check('enterprise_field_history_has_keyboard_button', historyButtonCount === 1, { count: historyButtonCount });
  if (historyButtonCount === 1) {
    await historyButton.focus();
    await page.keyboard.press('Enter');
  } else {
    await enterpriseRow.click();
  }
  const modalHeading = page.getByRole('heading', { name: `NDVI История — ${fieldTwo.name}` });
  await modalHeading.waitFor();
  const dialog = page.getByRole('dialog', { name: `NDVI История — ${fieldTwo.name}` });
  const dialogCount = await dialog.count();
  check('ndvi_history_uses_named_modal_dialog', dialogCount === 1, { count: dialogCount });
  let focusInside = false;
  if (dialogCount === 1) {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      focusInside = await dialog.evaluate((node) => node.contains(document.activeElement));
      if (focusInside) break;
      await page.waitForTimeout(50);
    }
  }
  check('ndvi_history_initial_focus_inside_dialog', focusInside);
  await capture(page, 'manager', desktopViewport, '/enterprises/1', 'ndvi-history-dialog');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(250);
  const closedByEscape = await modalHeading.count() === 0;
  check('ndvi_history_closes_with_escape', closedByEscape);
  if (!closedByEscape) {
    await page.locator('button').filter({ hasText: '×' }).last().click();
  }
  const restored = historyButtonCount === 1
    ? await historyButton.evaluate((node) => document.activeElement === node)
    : false;
  check('ndvi_history_restores_trigger_focus', restored);
  await desktop.context.close();

  const tabletViewport = { width: 1024, height: 768 };
  const tablet = await newPage(browser, tabletViewport);
  stage('tablet_manager_layout');
  await keyboardLogin(tablet.page, 'manager');
  await tablet.page.goto(`${frontendUrl}/fields`, { waitUntil: 'networkidle' });
  const tabletOverflow = await horizontalOverflow(tablet.page);
  check('tablet_1024_no_document_horizontal_overflow', tabletOverflow.overflowPx === 0, tabletOverflow);
  await capture(tablet.page, 'manager', tabletViewport, '/fields', 'tablet-fields');
  await tablet.context.close();

  const portraitViewport = { width: 390, height: 844 };
  const portrait = await newPage(browser, portraitViewport);
  stage('mobile_portrait_navigation_drawer');
  await keyboardLogin(portrait.page, 'manager');
  const menuButton = portrait.page.getByRole('button', { name: 'Открыть основную навигацию' });
  const menuBox = await menuButton.boundingBox();
  check('mobile_menu_target_at_least_44px', Boolean(menuBox && menuBox.width >= 44 && menuBox.height >= 44), { box: menuBox });
  await menuButton.focus();
  await portrait.page.keyboard.press('Enter');
  const mobileDrawer = portrait.page.getByRole('dialog', { name: 'Основная навигация' });
  const mobileDrawerCount = await mobileDrawer.count();
  check('mobile_navigation_is_modal_dialog', mobileDrawerCount === 1, { count: mobileDrawerCount });
  const mobileFocusInside = mobileDrawerCount === 1
    ? await mobileDrawer.evaluate((node) => node.contains(document.activeElement))
    : false;
  check('mobile_navigation_moves_focus_inside', mobileFocusInside);
  await capture(portrait.page, 'manager', portraitViewport, '/dashboard', 'mobile-navigation-open');
  await portrait.page.keyboard.press('Escape');
  await portrait.page.waitForTimeout(250);
  const drawerClosed = await portrait.page.getByRole('dialog', { name: 'Основная навигация' }).count() === 0;
  const menuFocusRestored = await menuButton.evaluate((node) => document.activeElement === node);
  check('mobile_navigation_escape_closes', drawerClosed);
  check('mobile_navigation_restores_trigger_focus', menuFocusRestored);
  const portraitOverflow = await horizontalOverflow(portrait.page);
  check('mobile_390_no_document_horizontal_overflow', portraitOverflow.overflowPx === 0, portraitOverflow);
  await portrait.context.close();

  const landscapeViewport = { width: 844, height: 390 };
  const landscape = await newPage(browser, landscapeViewport);
  stage('mobile_landscape_keyboard_login');
  await landscape.page.goto(`${frontendUrl}/login`, { waitUntil: 'networkidle' });
  await capture(landscape.page, 'manager', landscapeViewport, '/login', 'mobile-landscape-login');
  await keyboardLogin(landscape.page, 'manager');
  const landscapeOverflow = await horizontalOverflow(landscape.page);
  check('mobile_landscape_no_document_horizontal_overflow', landscapeOverflow.overflowPx === 0, landscapeOverflow);
  await landscape.context.close();

  const disabledViewport = { width: 390, height: 844 };
  const disabled = await newPage(browser, disabledViewport);
  stage('disabled_login_error');
  await disabled.page.goto(`${frontendUrl}/login`, { waitUntil: 'networkidle' });
  await disabled.page.getByLabel('Email').fill(credentials.users.disabled.email);
  await disabled.page.getByLabel('Пароль').fill(credentials.users.disabled.password);
  await disabled.page.locator('button[type="submit"]').click();
  const alert = disabled.page.getByRole('alert');
  await alert.waitFor();
  check('login_error_is_announced_as_alert', await alert.isVisible());
  check('disabled_user_remains_on_login', new URL(disabled.page.url()).pathname === '/login');
  await disabled.context.close();

  check('accessibility_console_errors_zero', report.browser.consoleErrors.length === 0, {
    count: report.browser.consoleErrors.length,
  });
  check('accessibility_page_errors_zero', report.browser.pageErrors.length === 0, {
    count: report.browser.pageErrors.length,
  });
  check('accessibility_unexpected_failed_requests_zero', report.browser.unexpectedFailedRequests.length === 0, {
    count: report.browser.unexpectedFailedRequests.length,
  });
  check('accessibility_unexpected_http_errors_zero', report.browser.unexpectedHttpErrors.length === 0, {
    count: report.browser.unexpectedHttpErrors.length,
  });

  const failed = report.checks.filter((item) => !item.passed);
  report.completedAt = new Date().toISOString();
  report.status = failed.length === 0 ? 'PASS' : 'FAIL';
  report.marker = failed.length === 0
    ? 'PASS_ACCESSIBILITY_KEYBOARD_FOCUS_RESPONSIVE'
    : 'FAIL_ACCESSIBILITY_KEYBOARD_FOCUS_RESPONSIVE';
  report.failedChecks = failed.map((item) => item.name);
  await atomicJson(
    failed.length === 0 ? 'ACCESSIBILITY_QUALIFICATION.json' : 'ACCESSIBILITY_QUALIFICATION_FAILURE.json',
    report,
  );
  await atomicJson('ACCESSIBILITY_SCREENSHOT_INDEX.json', {
    schemaVersion: 1,
    exactHead: report.exactHead,
    screenshots: report.screenshots,
  });
  process.stdout.write(`${report.marker}\n`);
  process.stdout.write(`CHECKS=${report.checks.length}; FAILED=${failed.length}\n`);
  process.stdout.write('PRODUCTION_WRITES=0\n');
  if (failed.length > 0) process.exitCode = 1;
} catch (error) {
  report.status = 'FAIL';
  report.marker = 'FAIL_ACCESSIBILITY_QUALIFICATION_HARNESS';
  report.failure = `${error?.name || 'Error'}: ${error?.message || String(error)}`.slice(0, 2000);
  report.failedAt = new Date().toISOString();
  await atomicJson('ACCESSIBILITY_QUALIFICATION_FAILURE.json', report);
  throw error;
} finally {
  await browser.close().catch(() => {});
}
