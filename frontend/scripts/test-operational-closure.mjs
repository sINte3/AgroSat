import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../src/', import.meta.url);
const read = (path) => readFile(new URL(path, root), 'utf8');

const [api, panel, modal, actionModal, drawer, page] = await Promise.all([
  read('api/fieldInspections.js'),
  read('components/Inspections/OperationalClosurePanel.jsx'),
  read('components/Inspections/OperationalWorkflowModal.jsx'),
  read('components/Inspections/InspectionActionModal.jsx'),
  read('components/Inspections/InspectionDetailDrawer.jsx'),
  read('pages/FieldInspectionsPage.jsx'),
]);

const apiContracts = [
  'getOperationalClosure',
  'getInspectionTimeline',
  'recordInspectionResult',
  'attachInspectionEvidence',
  'createCorrectiveAction',
  'updateCorrectiveAction',
  'closeCorrectiveAction',
  'reopenCorrectiveAction',
  'requestActionVerification',
  'resolveActionVerification',
];
for (const name of apiContracts) {
  assert.match(api, new RegExp(`export async function ${name}\\b`), `${name} API is missing`);
}
assert.match(api, /'Idempotency-Key': idempotencyKey/g);
assert.match(api, /__retryCount: NO_AUTOMATIC_RETRY_COUNT/);

assert.match(actionModal, /recordInspectionResult/);
assert.match(actionModal, /cause_code: causeCode/);
assert.match(actionModal, /expected_version: item\.version/);
assert.doesNotMatch(actionModal, /completeFieldInspection/);

assert.match(panel, /Promise\.all/);
assert.match(panel, /generation !== generationRef\.current/);
assert.match(panel, /controllerRef\.current\?\.abort\(\)/);
assert.match(panel, /Только чтение/);
assert.match(panel, /Изменение спутникового индекса — это наблюдение/);
assert.match(panel, /action\.status === 'closed'/);
assert.match(panel, /action\.latest_verification\?\.status === 'awaiting_observation'/);

assert.match(modal, /idempotencyKeyRef = useRef\(createIdempotencyKey\(\)\)/);
assert.match(modal, /provider: 'metadata_only'/);
assert.match(modal, /Файл не загружается/);
assert.match(modal, /controllerRef\.current\?\.abort\(\)/);
assert.match(modal, /expected_action_version: action\.version/);
assert.match(modal, /expected_version: action\.latest_verification\.version/);

assert.match(drawer, /workflowModalOpen/);
assert.match(drawer, /OperationalClosurePanel/);
assert.match(page, /assignees=\{assignees\}/);

console.log('TASK209 operational closure frontend contract: PASS');
