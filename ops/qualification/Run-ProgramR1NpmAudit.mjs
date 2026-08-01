#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith('--') || value === undefined) {
      throw new Error('Arguments must be provided as --name value pairs.');
    }
    values[key.slice(2)] = value;
  }
  for (const required of ['frontend', 'evidence-dir', 'expected-head']) {
    if (!values[required]) throw new Error(`Missing --${required}.`);
  }
  return values;
}

function atomicJson(path, value) {
  const temporary = `${path}.${process.pid}.tmp`;
  writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  renameSync(temporary, path);
}

function runAudit(frontend, extraArgs) {
  const npmCli = process.platform === 'win32'
    ? join(dirname(process.execPath), 'node_modules', 'npm', 'bin', 'npm-cli.js')
    : null;
  if (npmCli && !existsSync(npmCli)) throw new Error('npm CLI module is unavailable.');
  const command = npmCli ? process.execPath : 'npm';
  const commandArgs = npmCli
    ? [npmCli, 'audit', '--json', '--audit-level=low', ...extraArgs]
    : ['audit', '--json', '--audit-level=low', ...extraArgs];
  const result = spawnSync(
    command,
    commandArgs,
    {
      cwd: frontend,
      encoding: 'utf8',
      maxBuffer: 32 * 1024 * 1024,
      windowsHide: true,
    },
  );
  if (result.error) throw result.error;
  let payload;
  try {
    payload = JSON.parse(result.stdout);
  } catch {
    throw new Error(`npm audit did not return JSON (exit ${result.status}).`);
  }
  return { payload, exitCode: result.status ?? 1 };
}

const args = parseArgs(process.argv.slice(2));
const frontend = resolve(args.frontend);
const evidenceDir = resolve(args['evidence-dir']);
const expectedPrefix = 'c:\\agrosat_backups\\program_r1_completion_run\\';
if (process.platform === 'win32' && !evidenceDir.toLowerCase().startsWith(expectedPrefix)) {
  throw new Error('Unexpected evidence directory.');
}
if (!existsSync(join(frontend, 'package-lock.json'))) {
  throw new Error('package-lock.json is required for npm audit.');
}
for (const forbidden of ['pnpm-lock.yaml', 'yarn.lock']) {
  if (existsSync(join(frontend, forbidden))) {
    throw new Error(`Conflicting lockfile detected: ${forbidden}`);
  }
}

const packageJson = JSON.parse(readFileSync(join(frontend, 'package.json'), 'utf8'));
const all = runAudit(frontend, []);
const production = runAudit(frontend, ['--omit=dev']);
atomicJson(join(evidenceDir, 'FRONTEND_NPM_AUDIT_ALL.json'), all.payload);
atomicJson(join(evidenceDir, 'FRONTEND_NPM_AUDIT_PRODUCTION.json'), production.payload);

const counts = (payload) => payload?.metadata?.vulnerabilities ?? {};
const allCounts = counts(all.payload);
const productionCounts = counts(production.payload);
const summary = {
  schemaVersion: 1,
  gate: 'GATE5',
  operation: 'frontend_dependency_audit',
  head: args['expected-head'],
  packageManager: 'npm',
  lockfile: basename(join(frontend, 'package-lock.json')),
  packageName: packageJson.name,
  commands: [
    'npm audit --json --audit-level=low',
    'npm audit --json --omit=dev --audit-level=low',
  ],
  allDependencies: {
    exitCode: all.exitCode,
    vulnerabilities: allCounts,
  },
  productionDependencies: {
    exitCode: production.exitCode,
    vulnerabilities: productionCounts,
  },
  unresolvedHighOrCritical: Number(productionCounts.high ?? 0) + Number(productionCounts.critical ?? 0),
  status: 'REQUIRES_REVIEW',
  productionWrites: 0,
};
atomicJson(join(evidenceDir, 'FRONTEND_NPM_AUDIT_SUMMARY.json'), summary);
console.log(JSON.stringify({
  packageManager: summary.packageManager,
  productionHighOrCritical: summary.unresolvedHighOrCritical,
  all: allCounts,
  production: productionCounts,
}));
