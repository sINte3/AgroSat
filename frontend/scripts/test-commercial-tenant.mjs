import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) => readFile(path.join(root, name), 'utf8');
const [api, panel, page] = await Promise.all([
  read('src/api/commercialTenant.js'),
  read('src/components/Enterprise/CommercialTenantPanel.jsx'),
  read('src/pages/EnterpriseDetailPage.jsx'),
]);

for (const phrase of [
  'commercial/tenants/',
  'memberships',
  'lifecycle-requests',
  'Idempotency-Key',
  'confirm: true',
  'signal',
]) assert.match(api, new RegExp(phrase));

for (const phrase of [
  "role === 'admin'",
  "role === 'manager'",
  'request_type',
  'payment_processing',
  'feature_flags',
  'quota_limits',
  'retention_policy',
  'namespaces',
  'providers',
  'memberships',
  'approved',
  'rejected',
  'new AbortController',
  'generationRef',
  'aria-live',
  'min-h-11',
]) assert.match(panel, new RegExp(phrase));

assert.match(panel, /не запускают внешнее или необратимое действие/);
assert.match(panel, /никогда не передаются в браузер/);
assert.doesNotMatch(panel, /secret_reference|Authorization|localStorage|setInterval/);
assert.match(page, /CommercialTenantPanel/);
assert.match(page, /key: 'commercial'/);
assert.match(page, /role === 'admin' \|\| role === 'manager'/);

console.log('TASK209_COMMERCIAL_TENANT_FRONTEND_CONTRACT=PASS');
