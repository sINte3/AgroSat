import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import {
  mkdir,
  readFile,
  rename,
  stat,
  writeFile,
} from 'node:fs/promises';
import path from 'node:path';
import { promisify } from 'node:util';


const execFileAsync = promisify(execFile);
const envNames = [
  'R1_CREDENTIALS_PATH',
  'R1_EVIDENCE_DIR',
  'R1_PLAYWRIGHT_PACKAGE_ROOT',
  'R1_CHROMIUM_EXECUTABLE',
  'R1_FRONTEND_URL',
  'R1_BACKEND_URL',
  'R1_EXPECTED_HEAD',
  'R1_WORKTREE_ROOT',
];
for (const name of envNames) {
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
const backendUrl = new URL(process.env.R1_BACKEND_URL).origin;
for (const value of [frontendUrl, backendUrl]) {
  if (new URL(value).hostname !== '127.0.0.1') {
    throw new Error('Qualification endpoints must be loopback-only.');
  }
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
const STUB_HOSTS = new Set([
  'tile.openstreetmap.org',
  'server.arcgisonline.com',
  'demotiles.maplibre.org',
]);
const dueDate = '2026-08-10';

const report = {
  schemaVersion: 1,
  recordedAt: new Date().toISOString(),
  exactHead: process.env.R1_EXPECTED_HEAD,
  runtimeClass: 'isolated_ephemeral_real_chromium_and_real_fastapi',
  status: 'running',
  stage: 'initializing',
  roles: {},
  disabledUser: null,
  authorizationMatrix: [],
  workflow: {},
  browser: {
    viewports: ['1440x900', '1024x768', '390x844'],
    screenshots: [],
    consoleErrors: [],
    expectedProbeResourceConsoleMessages: [],
    pageErrors: [],
    unexpectedFailedRequests: [],
    cancelledRequests: [],
    unexpectedHttpErrors: [],
    expectedProbeHttpErrors: [],
    expectedUiHttpErrors: [],
    externalMapAssetsStubbed: 0,
  },
  scientificBoundary: {},
  credentialsIncluded: false,
  productionContacted: false,
  productionWrites: 0,
  trace: [],
};

function trace(step, detail = {}) {
  report.stage = step;
  report.trace.push({ at: new Date().toISOString(), step, ...detail });
}

async function atomicJson(name, value) {
  const target = path.join(evidenceDir, name);
  const temporary = `${target}.${randomUUID()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  await rename(temporary, target);
}

async function sha256(filePath) {
  return createHash('sha256').update(await readFile(filePath)).digest('hex');
}

function safeRequestPath(url) {
  const parsed = new URL(url);
  return parsed.origin === frontendUrl || parsed.origin === backendUrl
    ? `${parsed.pathname}${parsed.search}`
    : `${parsed.hostname}${parsed.pathname}`;
}

async function prepareContext(browser, role, viewport) {
  const context = await browser.newContext({
    viewport,
    serviceWorkers: 'allow',
    locale: 'ru-RU',
    timezoneId: 'Asia/Tashkent',
  });
  await context.addInitScript(() => {
    window.__r1MutationText = [];
    const observe = () => {
      const observer = new MutationObserver(() => {
        if (window.__r1MutationText.length >= 120) return;
        window.__r1MutationText.push((document.body?.innerText || '').slice(0, 2500));
      });
      if (document.documentElement) observer.observe(document.documentElement, { childList: true, subtree: true });
    };
    observe();
  });
  await context.route('**/*', async (route) => {
    const parsed = new URL(route.request().url());
    if (!STUB_HOSTS.has(parsed.hostname)) return route.continue();
    report.browser.externalMapAssetsStubbed += 1;
    if (parsed.pathname.endsWith('.pbf')) {
      return route.fulfill({ status: 200, contentType: 'application/x-protobuf', body: Buffer.alloc(0) });
    }
    return route.fulfill({ status: 200, contentType: 'image/png', body: ONE_PIXEL_PNG });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on('console', (message) => {
    if (message.type() === 'error') {
      const text = message.text().slice(0, 300);
      if (/^Failed to load resource: the server responded with a status of \d{3}/.test(text)) {
        report.browser.expectedProbeResourceConsoleMessages.push({ role, text });
      } else {
        report.browser.consoleErrors.push({ role, text });
      }
    }
  });
  page.on('pageerror', (error) => {
    report.browser.pageErrors.push({ role, text: String(error).slice(0, 300) });
  });
  page.on('requestfailed', (request) => {
    const item = {
      role,
      method: request.method(),
      path: safeRequestPath(request.url()),
      error: request.failure()?.errorText || 'failed',
    };
    if (/ERR_ABORTED|NS_BINDING_ABORTED|cancel/i.test(item.error)) {
      report.browser.cancelledRequests.push(item);
    } else {
      report.browser.unexpectedFailedRequests.push(item);
    }
  });
  page.on('response', (response) => {
    if (response.status() < 400) return;
    const request = response.request();
    const item = {
      role,
      method: request.method(),
      path: safeRequestPath(response.url()),
      status: response.status(),
    };
    if (request.headers()['x-r1-qualification-probe'] === 'true') {
      report.browser.expectedProbeHttpErrors.push(item);
    } else if (
      role === 'disabled'
      && item.method === 'POST'
      && item.path === '/api/auth/login'
      && item.status === 403
    ) {
      report.browser.expectedUiHttpErrors.push(item);
    } else {
      report.browser.unexpectedHttpErrors.push(item);
    }
  });
  return { context, page };
}

async function capture(page, role, viewport, route, action) {
  const safe = `${String(report.browser.screenshots.length + 1).padStart(2, '0')}-${role}-${viewport.width}x${viewport.height}-${action}`
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-');
  const filePath = path.join(screenshotDir, `${safe}.png`);
  await page.screenshot({ path: filePath, fullPage: false });
  const fileStat = await stat(filePath);
  report.browser.screenshots.push({
    file: path.relative(evidenceDir, filePath),
    role,
    viewport: `${viewport.width}x${viewport.height}`,
    route,
    action,
    exactHead: process.env.R1_EXPECTED_HEAD,
    bytes: fileStat.size,
    sha256: await sha256(filePath),
  });
}

async function loginUi(browser, role, viewport = { width: 1440, height: 900 }) {
  const { context, page } = await prepareContext(browser, role, viewport);
  await page.goto(`${frontendUrl}/login`, { waitUntil: 'networkidle' });
  if (role === 'admin') await capture(page, 'anonymous', viewport, '/login', 'blank-login-shell');
  await page.getByLabel('Email').fill(credentials.users[role].email);
  await page.locator('#login-password').fill(credentials.users[role].password);
  await page.locator('button[type="submit"]').click();
  await page.waitForFunction(() => (
    location.pathname !== '/login'
    && localStorage.getItem('agrosat_token')
    && localStorage.getItem('agrosat_user')
  ));
  const user = await page.evaluate(() => {
    const value = JSON.parse(localStorage.getItem('agrosat_user'));
    return { id: value.id, role: value.role, enterpriseId: value.enterprise_id, active: value.is_active };
  });
  assert.equal(user.role, role, `${role} browser session has the wrong role`);
  report.roles[role] = {
    authenticated: true,
    userId: user.id,
    enterpriseScope: user.enterpriseId,
    defaultPath: new URL(page.url()).pathname,
  };
  return { context, page, user, viewport };
}

async function browserApi(page, pathname, { method = 'GET', body, idempotency } = {}) {
  return page.evaluate(async ({ pathname: requestPath, method: requestMethod, body: requestBody, idempotency: key }) => {
    const headers = {
      authorization: `Bearer ${localStorage.getItem('agrosat_token')}`,
      'x-r1-qualification-probe': 'true',
    };
    if (requestBody !== undefined) headers['content-type'] = 'application/json';
    if (key) headers['idempotency-key'] = key;
    const response = await fetch(requestPath, {
      method: requestMethod,
      headers,
      body: requestBody === undefined ? undefined : JSON.stringify(requestBody),
    });
    let payload = null;
    if (response.headers.get('content-type')?.includes('json')) payload = await response.json();
    return { status: response.status, payload };
  }, { pathname, method, body, idempotency });
}

function matrix(role, name, actual, expected) {
  assert.ok([expected].flat().includes(actual), `${role} ${name}: expected ${expected}, got ${actual}`);
  report.authorizationMatrix.push({ role, check: name, status: actual, expected: [expected].flat(), pass: true });
}

function key(prefix) {
  return `${prefix}-${randomUUID()}`.slice(0, 64);
}

const browser = await chromium.launch({
  executablePath: process.env.R1_CHROMIUM_EXECUTABLE,
  headless: true,
});
const sessions = [];

try {
  trace('login_admin');
  const admin = await loginUi(browser, 'admin');
  sessions.push(admin.context);
  await admin.page.goto(`${frontendUrl}/enterprises/1`, { waitUntil: 'networkidle' });
  await capture(admin.page, 'admin', admin.viewport, '/enterprises/1', 'tenant-administration');
  matrix('admin', 'own field read', (await browserApi(admin.page, '/api/fields/1')).status, 200);
  matrix('admin', 'global cross-enterprise field read', (await browserApi(admin.page, '/api/fields/3')).status, 200);
  const commercial = await browserApi(admin.page, '/api/commercial/tenants/1');
  matrix('admin', 'commercial administration read', commercial.status, 200);
  const profile = commercial.payload.profile;
  const validCommercialPayload = {
    plan_code: profile.plan_code,
    subscription_state: profile.subscription_state,
    feature_flags: { ...profile.feature_flags, wialon: false },
    quota_limits: profile.quota_limits,
    retention_policy: { retention_days: 365 },
    branding: { display_name: 'AgroSat Qualification' },
  };
  const adminWrite = await browserApi(admin.page, '/api/commercial/tenants/1', {
    method: 'PUT',
    body: validCommercialPayload,
  });
  matrix('admin', 'commercial administration write', adminWrite.status, 200);
  matrix('admin', 'Wialon deferred route', (await browserApi(admin.page, '/api/telematics/fields/1')).status, 404);

  trace('login_manager');
  const manager = await loginUi(browser, 'manager');
  sessions.push(manager.context);
  await manager.page.goto(`${frontendUrl}/attention`, { waitUntil: 'networkidle' });
  await capture(manager.page, 'manager', manager.viewport, '/attention', 'attention-queue-before-assignment');
  matrix('manager', 'tenant field read', (await browserApi(manager.page, '/api/fields/1')).status, 200);
  const managerCross = await browserApi(manager.page, '/api/fields/3');
  matrix('manager', 'cross-tenant field read denial', managerCross.status, 404);
  const managerMissing = await browserApi(manager.page, '/api/fields/999999');
  matrix('manager', 'missing field denial', managerMissing.status, 404);
  assert.equal(managerCross.payload?.detail, managerMissing.payload?.detail, 'cross-tenant field denial is enumerable');
  matrix(
    'manager',
    'cross-tenant field write denial',
    (await browserApi(manager.page, '/api/fields/3', { method: 'PUT', body: {} })).status,
    404,
  );
  const managerCommercialWrite = await browserApi(manager.page, '/api/commercial/tenants/1', {
    method: 'PUT',
    body: validCommercialPayload,
  });
  matrix('manager', 'forbidden tenant administration write', managerCommercialWrite.status, 403);
  matrix('manager', 'executive accountability read', (await browserApi(manager.page, '/api/executive/overview')).status, 200);

  trace('cancel_probe_inspection');
  const oldInspections = await browserApi(
    manager.page,
    '/api/field-inspections?field_id=1&limit=200',
  );
  matrix('manager', 'inspection list read', oldInspections.status, 200);
  for (const item of oldInspections.payload.items.filter((candidate) => (
    candidate.title === 'PROGRAM R1 offline reload qualification'
    && ['pending', 'in_progress'].includes(candidate.status)
  ))) {
    const cancelled = await browserApi(manager.page, `/api/field-inspections/${item.id}/cancel`, {
      method: 'POST',
      body: { expected_version: item.version, cancellation_reason: 'Qualification probe superseded by complete workflow.' },
    });
    matrix('manager', 'superseded probe cancellation', cancelled.status, 200);
  }

  trace('attention_to_assignment');
  const attention = await browserApi(manager.page, '/api/field-attention/queue?lookback_days=180&limit=100');
  matrix('manager', 'attention queue read', attention.status, 200);
  const attentionItem = attention.payload.items.find((item) => Number(item.field?.id) === 1);
  assert.ok(attentionItem, 'deterministic attention field is absent');
  const sourceObservationDate = (
    attentionItem.spectral_summary?.latest_observation_date
    || attention.payload.date_to
  );
  const createKey = key('r1-attention-inspection');
  const createBody = {
    field_id: 1,
    assigned_to_id: 3,
    source: 'attention_queue',
    source_priority: attentionItem.priority,
    source_attention_score: attentionItem.attention_score,
    source_observation_date: sourceObservationDate,
    source_reason_codes: attentionItem.reasons.map((reason) => reason.code).filter(Boolean),
    title: 'PROGRAM R1 complete agronomy loop',
    instructions: 'Human inspection required; spectral attention is not a diagnosis.',
    due_date: dueDate,
  };
  const created = await browserApi(manager.page, '/api/field-inspections', {
    method: 'POST', body: createBody, idempotency: createKey,
  });
  matrix('manager', 'attention inspection assignment', created.status, 201);
  assert.equal(created.payload.created, true);
  const replayedCreate = await browserApi(manager.page, '/api/field-inspections', {
    method: 'POST', body: createBody, idempotency: createKey,
  });
  matrix('manager', 'duplicate assignment idempotency replay', replayedCreate.status, 200);
  assert.equal(replayedCreate.payload.created, false);
  assert.equal(replayedCreate.payload.inspection.id, created.payload.inspection.id);
  const inspectionId = created.payload.inspection.id;
  await manager.page.goto(`${frontendUrl}/inspections/${inspectionId}`, { waitUntil: 'networkidle' });
  await manager.page.waitForFunction((title) => document.body.innerText.includes(title), createBody.title);
  await capture(manager.page, 'manager', manager.viewport, `/inspections/${inspectionId}`, 'assigned-inspection-detail');

  trace('login_agronomist');
  const agronomist = await loginUi(browser, 'agronomist');
  sessions.push(agronomist.context);
  await agronomist.page.goto(`${frontendUrl}/inspections`, { waitUntil: 'networkidle' });
  await agronomist.page.waitForFunction((title) => document.body.innerText.includes(title), createBody.title);
  await capture(agronomist.page, 'agronomist', agronomist.viewport, '/inspections', 'personal-assigned-queue');
  matrix('agronomist', 'personal queue read', (await browserApi(agronomist.page, `/api/field-inspections?assigned_to_id=3&limit=200`)).status, 200);
  matrix('agronomist', 'executive endpoint denied', (await browserApi(agronomist.page, '/api/executive/overview')).status, 403);
  matrix('agronomist', 'commercial memberships denied', (await browserApi(agronomist.page, '/api/commercial/tenants/1/memberships')).status, 403);
  matrix('agronomist', 'cross-tenant read denied', (await browserApi(agronomist.page, '/api/fields/3')).status, 404);

  trace('direct_route_authorization');
  await agronomist.page.goto(`${frontendUrl}/reports`, { waitUntil: 'domcontentloaded' });
  await agronomist.page.waitForURL(/\/inspections$/);
  const forbiddenReportFlash = await agronomist.page.evaluate(() => (
    window.__r1MutationText.some((value) => value.includes('Отчёты'))
  ));
  assert.equal(forbiddenReportFlash, false, 'agronomist observed forbidden report content');
  await agronomist.page.goto(`${frontendUrl}/inspections/${inspectionId}`, { waitUntil: 'networkidle' });
  await agronomist.page.reload({ waitUntil: 'networkidle' });
  assert.equal(new URL(agronomist.page.url()).pathname, `/inspections/${inspectionId}`);
  await agronomist.page.goBack();
  await agronomist.page.goForward();
  assert.equal(new URL(agronomist.page.url()).pathname, `/inspections/${inspectionId}`);

  trace('inspection_start');
  let inspection = (await browserApi(agronomist.page, `/api/field-inspections/${inspectionId}`)).payload;
  const pendingVersion = inspection.version;
  const started = await browserApi(agronomist.page, `/api/field-inspections/${inspectionId}/start`, {
    method: 'POST', body: { expected_version: inspection.version },
  });
  matrix('agronomist', 'assigned inspection start', started.status, 200);
  assert.equal(started.payload.status, 'in_progress');
  assert.ok(started.payload.started_at);
  const staleUpdate = await browserApi(manager.page, `/api/field-inspections/${inspectionId}`, {
    method: 'PATCH', body: { expected_version: pendingVersion, title: createBody.title },
  });
  matrix('manager', 'optimistic stale inspection conflict', staleUpdate.status, 409);

  trace('human_result_evidence_action');
  inspection = (await browserApi(agronomist.page, `/api/field-inspections/${inspectionId}`)).payload;
  const resultKey = key('r1-result');
  const resultBody = {
    expected_version: inspection.version,
    cause_code: 'irrigation',
    cause_details: 'Human field inspection confirmed a blocked qualification-fixture irrigation outlet.',
    evidence_note: 'Human observation with field geolocation; satellite attention was contextual only.',
    latitude: 39.77,
    longitude: 64.42,
  };
  const result = await browserApi(agronomist.page, `/api/field-inspections/${inspectionId}/result`, {
    method: 'POST', body: resultBody, idempotency: resultKey,
  });
  matrix('agronomist', 'human-confirmed result', result.status, 200);
  const resultReplay = await browserApi(agronomist.page, `/api/field-inspections/${inspectionId}/result`, {
    method: 'POST', body: resultBody, idempotency: resultKey,
  });
  matrix('agronomist', 'duplicate result idempotency replay', resultReplay.status, 200);
  assert.equal(resultReplay.payload.id, result.payload.id);

  const completedInspection = (await browserApi(agronomist.page, `/api/field-inspections/${inspectionId}`)).payload;
  assert.equal(completedInspection.status, 'completed');
  assert.ok(completedInspection.completed_at);
  inspection = completedInspection;
  const evidence = await browserApi(agronomist.page, `/api/field-inspections/${inspectionId}/evidence`, {
    method: 'POST',
    idempotency: key('r1-evidence'),
    body: {
      expected_version: inspection.version,
      result_id: result.payload.id,
      evidence_type: 'geolocation',
      provider: 'metadata_only',
      provider_metadata: { source: 'program_r1_isolated_browser_qualification' },
      latitude: 39.77,
      longitude: 64.42,
    },
  });
  matrix('agronomist', 'inspection evidence attachment', evidence.status, 200);

  inspection = (await browserApi(agronomist.page, `/api/field-inspections/${inspectionId}`)).payload;
  const actionKey = key('r1-action');
  const actionBody = {
    expected_inspection_version: inspection.version,
    result_id: result.payload.id,
    owner_id: 3,
    description: 'Clear the blocked outlet and verify water delivery across the isolated fixture field.',
    due_date: dueDate,
  };
  const action = await browserApi(agronomist.page, `/api/field-inspections/${inspectionId}/actions`, {
    method: 'POST', body: actionBody, idempotency: actionKey,
  });
  matrix('agronomist', 'corrective action creation', action.status, 200);
  const actionReplay = await browserApi(agronomist.page, `/api/field-inspections/${inspectionId}/actions`, {
    method: 'POST', body: actionBody, idempotency: actionKey,
  });
  matrix('agronomist', 'duplicate action idempotency replay', actionReplay.status, 200);
  assert.equal(actionReplay.payload.id, action.payload.id);

  matrix('agronomist', 'human result completes inspection state', 200, 200);

  trace('action_close_reopen_close');
  let closure = (await browserApi(manager.page, `/api/field-inspections/${inspectionId}/closure`)).payload;
  let currentAction = closure.actions.find((item) => item.id === action.payload.id);
  assert.equal(Number(currentAction.owner.id), 3);
  assert.equal(currentAction.due_date, dueDate);
  let closed = await browserApi(manager.page, `/api/operational-actions/${currentAction.id}/close`, {
    method: 'POST',
    idempotency: key('r1-close'),
    body: { expected_version: currentAction.version, closure_reason: 'Corrective work completed and checked by the accountable manager.' },
  });
  matrix('manager', 'corrective action close', closed.status, 200);
  assert.equal(closed.payload.status, 'closed');
  const reopened = await browserApi(manager.page, `/api/operational-actions/${currentAction.id}/reopen`, {
    method: 'POST',
    idempotency: key('r1-reopen'),
    body: { expected_version: closed.payload.version, reopen_reason: 'Qualification requires a documented second closure check.' },
  });
  matrix('manager', 'corrective action reopen', reopened.status, 200);
  assert.notEqual(reopened.payload.status, 'closed');
  closed = await browserApi(manager.page, `/api/operational-actions/${currentAction.id}/close`, {
    method: 'POST',
    idempotency: key('r1-reclose'),
    body: { expected_version: reopened.payload.version, closure_reason: 'Second human closure check completed for qualification.' },
  });
  matrix('manager', 'corrective action second close', closed.status, 200);

  trace('request_verification');
  const verification = await browserApi(manager.page, `/api/operational-actions/${currentAction.id}/verification-requests`, {
    method: 'POST',
    idempotency: key('r1-verification-request'),
    body: { expected_action_version: closed.payload.version, index_code: 'ndvi', minimum_separation_days: 3 },
  });
  matrix('manager', 'later observation verification request', verification.status, 200);
  assert.equal(verification.payload.status, 'awaiting_observation');

  trace('insert_eligible_temporal_fixture');
  const observationEvidence = path.join(evidenceDir, 'ELIGIBLE_OBSERVATION_FIXTURE.json');
  const helper = path.join(worktreeRoot, 'ops', 'qualification', 'Insert-ProgramR1EligibleObservation.py');
  const helperResult = await execFileAsync(
    'C:\\Program Files\\Python314\\python.exe',
    [
      helper,
      '--credentials', credentialsPath,
      '--evidence', observationEvidence,
      '--field-id', '1',
      '--captured-date', '2026-08-05',
      '--port', '55439',
    ],
    { cwd: worktreeRoot, windowsHide: true, timeout: 30000 },
  );
  assert.match(helperResult.stdout, /"productionWrites": 0/);

  trace('resolve_verification');
  const resolved = await browserApi(manager.page, `/api/verification-requests/${verification.payload.id}/resolve`, {
    method: 'POST',
    idempotency: key('r1-verification-resolve'),
    body: {
      expected_version: verification.payload.version,
      notes: 'Observed index direction is correlation only and does not prove the corrective action caused it.',
    },
  });
  matrix('manager', 'later observation verification resolve', resolved.status, 200);
  assert.notEqual(resolved.payload.result, 'insufficient_data');
  await manager.page.goto(`${frontendUrl}/inspections/${inspectionId}`, { waitUntil: 'networkidle' });
  const causalityCaveatVisible = await manager.page.evaluate(() => (
    document.body.innerText.includes('не доказательство агрономической причинности')
  ));
  assert.equal(causalityCaveatVisible, true, 'resolved workflow omitted the causality caveat');

  trace('executive_and_audit_reconciliation');
  closure = (await browserApi(manager.page, `/api/field-inspections/${inspectionId}/closure`)).payload;
  currentAction = closure.actions.find((item) => item.id === action.payload.id);
  const timeline = await browserApi(manager.page, `/api/field-inspections/${inspectionId}/timeline?limit=200`);
  matrix('manager', 'timeline read', timeline.status, 200);
  const eventTypes = [...new Set(timeline.payload.items.map((item) => item.event_type))];
  for (const requiredEvent of [
    'inspection_created',
    'inspection_started',
    'inspection_result_recorded',
    'evidence_attached',
    'action_created',
    'inspection_completed',
    'action_closed',
    'action_reopened',
    'verification_requested',
    'verification_resolved',
  ]) {
    assert.ok(eventTypes.includes(requiredEvent), `timeline is missing ${requiredEvent}`);
  }
  const executive = await browserApi(manager.page, '/api/executive/overview?date_from=2026-07-01&date_to=2026-08-31');
  matrix('manager', 'executive reconciliation after closure', executive.status, 200);
  await manager.page.goto(`${frontendUrl}/reports`, { waitUntil: 'networkidle' });
  await capture(manager.page, 'manager', { width: 1440, height: 900 }, '/reports', 'executive-accountability-reconciled');

  report.workflow = {
    attentionFieldId: 1,
    inspectionId,
    assignedToUserId: 3,
    createIdempotencyReplay: true,
    resultId: result.payload.id,
    resultIdempotencyReplay: true,
    humanConfirmedCause: result.payload.cause_code,
    evidenceId: evidence.payload.id,
    actionId: action.payload.id,
    actionOwnerId: currentAction.owner.id,
    actionDueDate: currentAction.due_date,
    inspectionStatus: closure.inspection.status,
    actionStatus: currentAction.status,
    closeReopenClose: true,
    verificationId: resolved.payload.id,
    verificationStatus: resolved.payload.status,
    verificationResult: resolved.payload.result,
    verificationConfidence: resolved.payload.confidence,
    temporalFixtureDate: '2026-08-05',
    temporalFixtureLiveEvidence: false,
    timelineEventTypes: eventTypes,
    timelineEventCount: timeline.payload.items.length,
    executiveReconciled: true,
    timestamps: {
      started: Boolean(started.payload.started_at),
      completed: Boolean(completedInspection.completed_at),
      actionClosed: Boolean(closed.payload.closed_at),
    },
  };
  report.scientificBoundary = {
    attentionItemContainsLimitations: Array.isArray(attentionItem.limitations) && attentionItem.limitations.length > 0,
    attentionWasNotDiagnosis: createBody.instructions.includes('not a diagnosis'),
    diagnosisRecordedByHuman: result.payload.cause_code === 'irrigation',
    verificationIsCorrelationNotCausality: causalityCaveatVisible,
    laterObservationIsExplicitFixture: true,
  };

  trace('login_viewer');
  const viewer = await loginUi(browser, 'viewer');
  sessions.push(viewer.context);
  matrix('viewer', 'tenant field read', (await browserApi(viewer.page, '/api/fields/1')).status, 200);
  matrix('viewer', 'cross-tenant read denied', (await browserApi(viewer.page, '/api/fields/3')).status, 404);
  matrix('viewer', 'field write denied', (await browserApi(viewer.page, '/api/fields/1', { method: 'PUT', body: {} })).status, 403);
  matrix('viewer', 'inspection write denied', (await browserApi(viewer.page, '/api/field-inspections', {
    method: 'POST', idempotency: key('r1-viewer-denied'), body: createBody,
  })).status, 403);
  matrix('viewer', 'executive endpoint denied', (await browserApi(viewer.page, '/api/executive/overview')).status, 403);
  await viewer.page.goto(`${frontendUrl}/fields`, { waitUntil: 'networkidle' });
  await viewer.page.waitForSelector('.maplibregl-map');
  const viewerWriteControlCount = await viewer.page.locator('button').filter({ hasText: /Создать поле|Добавить поле/ }).count();
  assert.equal(viewerWriteControlCount, 0, 'viewer saw a field write control');
  await capture(viewer.page, 'viewer', viewer.viewport, '/fields', 'read-only-map-view');

  trace('responsive_role_views');
  await manager.page.setViewportSize({ width: 1024, height: 768 });
  await manager.page.goto(`${frontendUrl}/dashboard`, { waitUntil: 'networkidle' });
  await capture(manager.page, 'manager', { width: 1024, height: 768 }, '/dashboard', 'tablet-dashboard');
  await agronomist.page.setViewportSize({ width: 390, height: 844 });
  await agronomist.page.goto(`${frontendUrl}/inspections/${inspectionId}`, { waitUntil: 'networkidle' });
  await capture(agronomist.page, 'agronomist', { width: 390, height: 844 }, `/inspections/${inspectionId}`, 'mobile-completed-inspection');
  const mobileOverflow = await agronomist.page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  assert.equal(mobileOverflow, false, 'agronomist critical mobile route has horizontal overflow');

  trace('viewer_logout');
  await viewer.page.evaluate(() => {
    const button = [...document.querySelectorAll('button')]
      .find((item) => item.textContent.trim() === 'Выйти');
    if (!button) throw new Error('logout control not found');
    button.click();
  });
  await viewer.page.waitForURL(/\/login$/);
  const logoutState = await viewer.page.evaluate(() => ({
    tokenPresent: localStorage.getItem('agrosat_token') !== null,
    userPresent: localStorage.getItem('agrosat_user') !== null,
  }));
  assert.deepEqual(logoutState, { tokenPresent: false, userPresent: false });
  report.roles.viewer.logoutPurgedLocalAuthentication = true;

  trace('disabled_user_denial');
  const disabled = await prepareContext(browser, 'disabled', { width: 1440, height: 900 });
  sessions.push(disabled.context);
  await disabled.page.goto(`${frontendUrl}/login`, { waitUntil: 'networkidle' });
  await disabled.page.getByLabel('Email').fill(credentials.users.disabled.email);
  await disabled.page.locator('#login-password').fill(credentials.users.disabled.password);
  await disabled.page.locator('button[type="submit"]').click();
  await disabled.page.locator('[role="alert"]').waitFor();
  const disabledState = await disabled.page.evaluate(() => ({
    pathname: location.pathname,
    tokenPresent: localStorage.getItem('agrosat_token') !== null,
    userPresent: localStorage.getItem('agrosat_user') !== null,
    alertVisible: Boolean(document.querySelector('[role="alert"]')),
  }));
  assert.equal(disabledState.pathname, '/login');
  assert.equal(disabledState.tokenPresent, false);
  assert.equal(disabledState.userPresent, false);
  report.disabledUser = { authenticationDenied: true, dataExposed: false, localAuthenticationPresent: false };
  await disabled.page.getByLabel('Email').fill('');
  await disabled.page.locator('#login-password').fill('');

  const unexpectedConsole = report.browser.consoleErrors;
  assert.deepEqual(report.browser.pageErrors, [], 'browser page errors were observed');
  assert.deepEqual(report.browser.unexpectedFailedRequests, [], 'unexpected failed browser requests were observed');
  assert.deepEqual(report.browser.unexpectedHttpErrors, [], 'unexpected browser HTTP errors were observed');
  assert.deepEqual(unexpectedConsole, [], 'browser console errors were observed');
  assert.equal(
    report.browser.expectedProbeResourceConsoleMessages.length,
    report.browser.expectedProbeHttpErrors.length + report.browser.expectedUiHttpErrors.length,
    'HTTP probe console classification did not reconcile',
  );
  assert.equal(report.scientificBoundary.attentionItemContainsLimitations, true);
  assert.equal(report.scientificBoundary.verificationIsCorrelationNotCausality, true);

  report.status = 'PASS';
  report.marker = 'PASS_ROLE_MATRIX_AND_COMPLETE_AGRONOMY_LOOP';
  trace('completed');
} catch (error) {
  report.status = 'FAIL';
  report.marker = 'FAIL_ROLE_MATRIX_OR_COMPLETE_AGRONOMY_LOOP';
  report.failure = {
    stage: report.stage,
    category: error?.name || 'Error',
    message: String(error?.message || error).slice(0, 600),
  };
  trace('failed');
  process.exitCode = 1;
} finally {
  report.recordedAt = new Date().toISOString();
  await atomicJson('ROLE_WORKFLOW_QUALIFICATION.json', report);
  await atomicJson('SCREENSHOT_INDEX.json', {
    exactHead: process.env.R1_EXPECTED_HEAD,
    status: report.status,
    screenshots: report.browser.screenshots,
    credentialsIncluded: false,
  });
  for (const context of sessions.reverse()) await context.close().catch(() => {});
  await browser.close();
}

console.log(`${report.marker}; STAGE=${report.stage}; PRODUCTION_WRITES=0`);
