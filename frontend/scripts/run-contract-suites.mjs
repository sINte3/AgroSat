// Runs every non-browser frontend contract suite and reports each result.
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SUITES = [
  'test-task226-units.mjs',
  'test-task226-http-contract.mjs',
  'test-task226-retired-contract.mjs',
  'test-anomaly-inspection-workflow.mjs',
  'test-offline-scouting-infrastructure.mjs',
  'test-operational-center.mjs',
  'test-closed-loop-agronomy.mjs',
  'test-executive-accountability.mjs',
  'test-weather-irrigation-workflow.mjs',
  'test-autonomous-monitoring.mjs',
  'test-index-request-lifecycle.mjs',
  'test-enterprise-ndvi-history.mjs',
  'test-program-r1-accessibility.mjs',
  'test-vector-raster-architecture.mjs',
  'test-pixel-anomaly-workflow.mjs',
  'test-pixel-ndvi-workspace.mjs',
  'test-wialon-read-only.mjs',
  'test-yield-map-import.mjs',
  'test-productivity-zones.mjs',
  'test-variable-rate-recommendations.mjs',
  'test-commercial-tenant.mjs',
  'test-production-api-origin.mjs',
];

const results = SUITES.map((suite) => {
  const run = spawnSync(process.execPath, [path.join('scripts', suite)], { cwd: root, encoding: 'utf8' });
  const lines = `${run.stdout || ''}${run.stderr || ''}`.trim().split(/\r?\n/);
  return { suite, status: run.status === 0 ? 'PASS' : 'FAIL', summary: lines.at(-1)?.slice(0, 200) || '' };
});
for (const result of results) console.log(`${result.status}  ${result.suite}  ${result.summary}`);
const failed = results.filter((result) => result.status !== 'PASS');
console.log(JSON.stringify({ suites: results.length, passed: results.length - failed.length, failed: failed.map((item) => item.suite) }));
process.exit(failed.length ? 1 : 0);
