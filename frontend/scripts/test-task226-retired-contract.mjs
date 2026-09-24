// TASK_226 source/bundle contract: no production frontend code can invoke a
// TASK_225 retired write endpoint, trigger a browser-side NDVI refresh, or
// globally purge offline scouting data. Run after `npm run build` to also scan
// the production bundle.
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sourceRoot = path.join(root, 'src');
let assertions = 0;
const check = (condition, message) => { assert.ok(condition, message); assertions += 1; };

function files(directory, pattern) {
  return readdirSync(directory).flatMap((name) => {
    const candidate = path.join(directory, name);
    if (statSync(candidate).isDirectory()) return files(candidate, pattern);
    return pattern.test(name) ? [candidate] : [];
  });
}
const relative = (file) => path.relative(root, file).replaceAll('\\', '/');
const sources = files(sourceRoot, /\.(js|jsx)$/).map((file) => ({ file: relative(file), text: readFileSync(file, 'utf8') }));

// Retired TASK_225 write endpoints as they would appear in frontend code.
const RETIRED_SOURCE_PATTERNS = [
  ['legacy inspection sub-resource', /field-inspections\/(?:\$\{|\d|[a-z])/],
  ['legacy inspection collection write', /\.(?:post|put|patch|delete)\(\s*[`'"](?:\/api\/)?field-inspections[`'"?]/],
  ['TASK_209 operational action', /operational-actions/],
  ['TASK_209 verification request', /verification-requests/],
  ['TASK_217 inspection action create', /anomaly-inspections\/\$\{[^}]+\}\/actions/],
  ['TASK_217 inspection action transition/verify', /anomaly-inspections\/actions/],
  ['browser NDVI refresh', /ndvi\/\$\{[^}]+\}\/refresh|\/refresh[`'"]/],
];
const RETIRED_HELPERS = [
  'createFieldInspection', 'updateFieldInspection', 'startFieldInspection', 'completeFieldInspection',
  'cancelFieldInspection', 'recordInspectionResult', 'attachInspectionEvidence', 'createCorrectiveAction',
  'updateCorrectiveAction', 'closeCorrectiveAction', 'reopenCorrectiveAction', 'requestActionVerification',
  'resolveActionVerification', 'createInspectionAction', 'transitionInspectionAction', 'verifyInspectionAction',
  'refreshNDVI', 'syncOfflineQueue', 'purgeOfflineScoutingData',
];

for (const { file, text } of sources) {
  for (const [label, pattern] of RETIRED_SOURCE_PATTERNS) {
    check(!pattern.test(text), `${file}: ${label} must not be reachable (${pattern})`);
  }
  for (const helper of RETIRED_HELPERS) {
    check(!new RegExp(`\\b${helper}\\b`).test(text), `${file}: retired helper ${helper} must not exist`);
  }
  check(!/deleteDatabase\(/.test(text), `${file}: no code deletes the whole offline database`);
}

// Retired modules and the unrouted TASK_209 workflow UI stay removed.
for (const removed of [
  'src/api/fieldInspections.js', 'src/offline/offlineScoutingSync.js', 'src/pages/FieldInspectionsPage.jsx',
  'src/components/Inspections/InspectionActionModal.jsx', 'src/components/Inspections/InspectionDetailDrawer.jsx',
  'src/components/Inspections/OperationalClosurePanel.jsx', 'src/components/Inspections/OperationalWorkflowModal.jsx',
  'src/components/Inspections/OfflineScoutingPanel.jsx', 'src/components/Inspections/OfflineScoutingStatus.jsx',
]) {
  check(!existsSync(path.join(root, removed)), `${removed} is removed`);
}

// The canonical replacements are wired where the retired calls used to be.
const source = (name) => sources.find((item) => item.file === name)?.text || '';
check(/createAnomalyInspection\(/.test(source('src/components/Inspections/InspectionCreateModal.jsx')), 'attention inspections use POST /api/anomaly-inspections');
check(/createAnomalyInspection\(/.test(source('src/components/Field/FieldIrrigationContextPanel.jsx')), 'irrigation inspections use POST /api/anomaly-inspections');
check(/source_kind: 'manual'/.test(source('src/utils/inspectionRequests.js')), 'manual source kind is used for context-created inspections');
check(!/source: 'irrigation_context'|source: 'attention_queue', field_id: Number/.test(source('src/components/Field/FieldIrrigationContextPanel.jsx') + source('src/components/Inspections/InspectionCreateModal.jsx')), 'no invented source kind is sent');
check(/createAgronomyDraft\(/.test(source('src/components/Inspections/AnomalyInspectionDetail.jsx')), 'remediation from an inspection goes through /api/agronomy-plans');
check(/cancelAnomalyInspection\(/.test(source('src/components/Inspections/AnomalyInspectionDetail.jsx')), 'inspection cancellation and legacy close-out use the canonical cancel');
check(/'anomaly-inspections\/queue'/.test(source('src/api/anomalyInspections.js')), 'the canonical queue is read');
check(/getAnomalyInspectionQueue/.test(source('src/pages/DashboardPage.jsx')), 'the dashboard counts canonical open inspections');

// Session model (C7): the HTTP layer never reaches offline storage.
const clientSource = source('src/api/client.js');
check(!/offline/i.test(clientSource.replace(/\/\/.*$/gm, '')), 'client.js does not import offline storage');
check(/invalidateSession\(\{ reason: 'unauthorized', failedToken/.test(clientSource), '401 invalidates the session with the failing credential');
const sessionSource = source('src/auth/session.js');
check(/purgeOfflineScope\(scope\)/.test(sessionSource) && sessionSource.match(/purgeOfflineScope\(/g).length === 1, 'only the explicit logout purges, and only its own scope');
const authSource = source('src/context/AuthContext.jsx');
check(!/purgeOfflineScope|deleteDatabase/.test(authSource), 'AuthContext never purges offline data itself');
check(/logoutExplicitly\(userRef\.current\)/.test(authSource), 'only the explicit logout action reaches the purge');
check(/SAFE_RETRY_METHODS = Object\.freeze\(\['get', 'head'\]\)/.test(clientSource), 'automatic retries are limited to GET and HEAD');

// Production bundle scan (when built).
const assets = path.join(root, 'dist', 'assets');
let bundleFiles = 0;
if (existsSync(assets)) {
  for (const file of files(assets, /\.js$/)) {
    const text = readFileSync(file, 'utf8');
    bundleFiles += 1;
    for (const fragment of ['operational-actions', 'verification-requests', 'field-inspections/', '/refresh`', 'anomaly-inspections/actions']) {
      check(!text.includes(fragment), `${relative(file)} must not contain ${fragment}`);
    }
    check(!/indexedDB\.deleteDatabase/.test(text), `${relative(file)} contains no global offline purge`);
  }
}

console.log(JSON.stringify({
  status: 'PASS',
  suite: 'TASK_226 retired-mutation and session source contract',
  assertions,
  source_files: sources.length,
  bundle_files_scanned: bundleFiles,
}));
