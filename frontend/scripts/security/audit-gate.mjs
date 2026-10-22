// TASK_264 frontend npm audit policy gate (CI frontend job, last step).
//
// Replaces the bare `npm audit --audit-level=high` check. It runs two fresh, real
// audits of frontend/package-lock.json (full with dev dependencies, and --omit=dev),
// treats their JSON as untrusted data and fails closed. Exit 0 only for:
//   NPM_AUDIT_CLEAN           no HIGH or CRITICAL finding at all;
//   TIME_LIMITED_POLICY_PASS  every HIGH/CRITICAL finding derives solely from the one
//                             approved advisory GHSA-vfj7-8cjw-p6xm on `braces`, every
//                             affected instance is installed only through the Tailwind 3
//                             development graph, the runtime audit has no HIGH/CRITICAL
//                             and the UTC clock is before 2026-11-15T00:00:00Z.
// Anything else is FAIL (exit 1): another HIGH/CRITICAL advisory, an npm, network or
// registry failure, malformed or inconsistent JSON, or an expired waiver. MODERATE and
// LOW findings are always printed as warnings. A pass is a documented, temporary risk
// acceptance, not a clean audit: raw `npm audit` keeps exiting non-zero meanwhile.
//
// The gate takes no arguments and reads no clock or fixture override. npm is started
// without a shell as `<this node> <npm-cli.js bundled next to it>`; npm_execpath is
// not set when CI runs the gate with `node`, and an inherited path is not trusted.
import { spawn } from 'node:child_process';
import { existsSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const WAIVER = Object.freeze({
  advisoryUrl: 'https://github.com/advisories/GHSA-vfj7-8cjw-p6xm',
  packageName: 'braces',
  severity: 'high',
  graphRoot: 'tailwindcss',
  graphRootVersion: /^3\.\d+\.\d+$/,
  grantedAtUtc: '2026-10-21T00:00:00.000Z',
  lastValidSecondUtc: '2026-11-14T23:59:59Z',
  hardExpiryUtc: '2026-11-15T00:00:00.000Z',
});
const GRANTED_AT = Date.parse(WAIVER.grantedAtUtc);
const EXPIRES_AT = Date.parse(WAIVER.hardExpiryUtc);
const TAILWIND_LOCATION = `node_modules/${WAIVER.graphRoot}`;

export const REGISTRY = 'https://registry.npmjs.org/';
const AUDIT_ARGS = ['audit', '--json', '--package-lock-only', '--audit-level=low', `--registry=${REGISTRY}`];
export const FULL_AUDIT_ARGS = Object.freeze([...AUDIT_ARGS, '--include=dev', '--include=optional', '--include=peer']);
export const RUNTIME_AUDIT_ARGS = Object.freeze([...AUDIT_ARGS, '--omit=dev']);
export const LIMITS = Object.freeze({
  timeoutMs: 180_000,
  maxStdoutBytes: 8 * 1024 * 1024,
  maxStderrBytes: 1024 * 1024,
  maxLockfileBytes: 16 * 1024 * 1024,
});
export const LIBRARY_IMPORT = Symbol.for('agrosat.task264.auditGate.libraryImport');

const SEVERITY_RANK = Object.freeze({ info: 0, low: 1, moderate: 2, high: 3, critical: 4 });
const SEVERITIES = Object.keys(SEVERITY_RANK);
const HIGH = SEVERITY_RANK.high;
const GHSA_URL = /^https:\/\/github\.com\/advisories\/(GHSA(?:-[23456789cfghjmpqrvwx]{4}){3})$/;
const PACKAGE_NAME = /^(?:@[a-z0-9~-][a-z0-9._~-]*\/)?[a-z0-9~-][a-z0-9._~-]*$/i;
const VULNERABILITY_KEYS = ['name', 'severity', 'isDirect', 'via', 'effects', 'range', 'nodes', 'fixAvailable'];
const ADVISORY_KEYS = ['source', 'name', 'dependency', 'title', 'url', 'severity', 'range'];
const ADVISORY_OPTIONAL_KEYS = ['cwe', 'cvss'];
const DEPENDENCY_COUNT_KEYS = ['prod', 'dev', 'optional', 'peer', 'peerOptional', 'total'];

export class PolicyError extends Error {
  constructor(code, detail) {
    super(`${code}: ${detail}`);
    this.code = code;
    this.detail = detail;
  }
}

function fail(code, detail) {
  throw new PolicyError(code, detail);
}

// Audit text is untrusted: only a conservative character set reaches the log.
export function safe(value, max = 160) {
  return String(value).replace(/[^\w .,:;/@<>=^~*|+()#-]/g, '?').slice(0, max);
}

const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const isCount = (value) => Number.isSafeInteger(value) && value >= 0;
const isHigh = (severity) => SEVERITY_RANK[severity] >= HIGH;

function exactKeys(value, required, optional, where, code) {
  if (!isObject(value)) fail(code, `${where} is not an object`);
  for (const key of required) if (!own(value, key)) fail(code, `${where} lacks "${key}"`);
  for (const key of Object.keys(value)) {
    if (!required.includes(key) && !optional.includes(key)) fail(code, `${where} has unsupported key "${safe(key, 40)}"`);
  }
}

function isNodePath(location) {
  const parts = location.split('/node_modules/');
  if (!parts[0].startsWith('node_modules/')) return false;
  parts[0] = parts[0].slice('node_modules/'.length);
  return parts.every((part) => PACKAGE_NAME.test(part));
}

function isNodePathFor(location, name) {
  return isNodePath(location) && (location === `node_modules/${name}` || location.endsWith(`/node_modules/${name}`));
}

function isSeverity(value) {
  return typeof value === 'string' && own(SEVERITY_RANK, value);
}

// ---------------------------------------------------------------- npm results

export function parseCommandOutput(result, label) {
  if (!isObject(result)) fail('NPM_SPAWN_FAILED', `${label}: npm produced no command result`);
  if (result.error) fail('NPM_SPAWN_FAILED', `${label}: npm could not run (${safe(result.error, 60)})`);
  if (result.timedOut) fail('NPM_TIMEOUT', `${label}: npm exceeded ${LIMITS.timeoutMs} ms`);
  if (result.stdoutTruncated || result.stderrTruncated) fail('NPM_OUTPUT_LIMIT', `${label}: npm output exceeded the size limit`);
  if (result.signal) fail('NPM_SIGNAL', `${label}: npm was terminated by ${safe(result.signal, 20)}`);
  if (result.exitCode !== 0 && result.exitCode !== 1) {
    fail('NPM_EXIT_STATUS', `${label}: unexpected npm exit status ${safe(result.exitCode, 20)}`);
  }
  if (typeof result.stdout !== 'string' || !result.stdout.trim()) fail('AUDIT_JSON_INVALID', `${label}: npm printed no JSON`);
  try {
    return JSON.parse(result.stdout);
  } catch {
    return fail('AUDIT_JSON_INVALID', `${label}: npm output is not one complete JSON document`);
  }
}

export function validateAuditReport(report, label) {
  if (!isObject(report)) fail('AUDIT_SCHEMA', `${label}: top level is not a JSON object`);
  if (own(report, 'error')) {
    const code = isObject(report.error) && typeof report.error.code === 'string' ? report.error.code : 'unknown';
    fail('AUDIT_ERROR_OBJECT', `${label}: npm reported an error (${safe(code, 40)})`);
  }
  exactKeys(report, ['auditReportVersion', 'vulnerabilities', 'metadata'], [], label, 'AUDIT_SCHEMA');
  if (report.auditReportVersion !== 2) fail('AUDIT_SCHEMA', `${label}: unsupported auditReportVersion`);
  const vulnerabilities = report.vulnerabilities;
  if (!isObject(vulnerabilities)) fail('AUDIT_SCHEMA', `${label}: vulnerabilities is not an object`);
  exactKeys(report.metadata, ['vulnerabilities', 'dependencies'], [], `${label} metadata`, 'AUDIT_SCHEMA');
  const counts = report.metadata.vulnerabilities;
  exactKeys(counts, [...SEVERITIES, 'total'], [], `${label} metadata.vulnerabilities`, 'AUDIT_SCHEMA');
  for (const key of [...SEVERITIES, 'total']) {
    if (!isCount(counts[key])) fail('AUDIT_SCHEMA', `${label}: metadata.vulnerabilities.${key} is not a count`);
  }
  const dependencies = report.metadata.dependencies;
  exactKeys(dependencies, DEPENDENCY_COUNT_KEYS, [], `${label} metadata.dependencies`, 'AUDIT_SCHEMA');
  for (const key of DEPENDENCY_COUNT_KEYS) {
    if (!isCount(dependencies[key])) fail('AUDIT_SCHEMA', `${label}: metadata.dependencies.${key} is not a count`);
  }
  if (dependencies.total < 1) fail('AUDIT_SCHEMA', `${label}: the audit covered no dependencies`);

  const names = Object.keys(vulnerabilities);
  const advisoriesByPackage = new Map();
  for (const name of names) {
    const where = `${label} vulnerabilities["${safe(name, 80)}"]`;
    if (!PACKAGE_NAME.test(name)) fail('AUDIT_SCHEMA', `${where}: invalid package name`);
    const entry = vulnerabilities[name];
    exactKeys(entry, VULNERABILITY_KEYS, [], where, 'AUDIT_SCHEMA');
    if (entry.name !== name) fail('AUDIT_SCHEMA', `${where}: name does not match its key`);
    if (!isSeverity(entry.severity)) fail('AUDIT_SCHEMA', `${where}: unknown severity`);
    if (typeof entry.isDirect !== 'boolean' || typeof entry.range !== 'string') fail('AUDIT_SCHEMA', `${where}: malformed isDirect or range`);
    if (!Array.isArray(entry.via) || entry.via.length === 0) fail('AUDIT_SCHEMA', `${where}: via is not a non-empty array`);
    if (!Array.isArray(entry.effects)) fail('AUDIT_SCHEMA', `${where}: effects is not an array`);
    if (!Array.isArray(entry.nodes) || entry.nodes.length === 0) fail('AUDIT_SCHEMA', `${where}: nodes is not a non-empty array`);
    for (const node of entry.nodes) {
      if (typeof node !== 'string' || !isNodePathFor(node, name)) fail('AUDIT_SCHEMA', `${where}: node "${safe(node, 120)}" is not an install location of ${safe(name, 80)}`);
    }
    if (new Set(entry.nodes).size !== entry.nodes.length) fail('AUDIT_SCHEMA', `${where}: duplicate nodes`);
    if (typeof entry.fixAvailable !== 'boolean') {
      exactKeys(entry.fixAvailable, ['name', 'version', 'isSemVerMajor'], [], `${where} fixAvailable`, 'AUDIT_SCHEMA');
      const fix = entry.fixAvailable;
      if (typeof fix.name !== 'string' || typeof fix.version !== 'string' || typeof fix.isSemVerMajor !== 'boolean') {
        fail('AUDIT_SCHEMA', `${where}: malformed fixAvailable`);
      }
    }

    const references = new Set();
    const advisories = [];
    for (const via of entry.via) {
      if (typeof via === 'string') {
        if (!PACKAGE_NAME.test(via) || via === name || !own(vulnerabilities, via)) {
          fail('AUDIT_VIA_UNRESOLVED', `${where}: via reference "${safe(via, 80)}" does not resolve to another vulnerable package`);
        }
        if (references.has(via)) fail('AUDIT_SCHEMA', `${where}: duplicate via reference`);
        references.add(via);
        continue;
      }
      exactKeys(via, ADVISORY_KEYS, ADVISORY_OPTIONAL_KEYS, `${where} advisory`, 'AUDIT_SCHEMA');
      if (!Number.isSafeInteger(via.source) || via.source < 1) fail('AUDIT_SCHEMA', `${where}: advisory source is not a positive integer`);
      if (via.name !== name || via.dependency !== name) fail('AUDIT_SCHEMA', `${where}: advisory ${via.source} names another package`);
      if (typeof via.title !== 'string' || typeof via.range !== 'string') fail('AUDIT_SCHEMA', `${where}: advisory ${via.source} lacks title or range`);
      if (!isSeverity(via.severity)) fail('AUDIT_SCHEMA', `${where}: advisory ${via.source} has an unknown severity`);
      const match = typeof via.url === 'string' ? GHSA_URL.exec(via.url) : null;
      if (!match) fail('AUDIT_ADVISORY_URL', `${where}: advisory ${via.source} has no canonical GitHub advisory URL`);
      if (advisories.some((advisory) => advisory.url === via.url)) fail('AUDIT_SCHEMA', `${where}: advisory ${match[1]} is listed twice`);
      if (own(via, 'cwe') && (!Array.isArray(via.cwe) || !via.cwe.every((cwe) => typeof cwe === 'string' && /^CWE-\d{1,6}$/.test(cwe)))) {
        fail('AUDIT_SCHEMA', `${where}: advisory ${match[1]} has a malformed cwe list`);
      }
      if (own(via, 'cvss')) {
        exactKeys(via.cvss, ['score', 'vectorString'], [], `${where} advisory cvss`, 'AUDIT_SCHEMA');
        const { score, vectorString } = via.cvss;
        if (typeof score !== 'number' || !(score >= 0 && score <= 10) || (vectorString !== null && typeof vectorString !== 'string')) {
          fail('AUDIT_SCHEMA', `${where}: advisory ${match[1]} has a malformed cvss`);
        }
      }
      advisories.push({
        id: match[1],
        url: via.url,
        package: name,
        severity: via.severity,
        source: via.source,
        range: safe(via.range, 80),
        title: safe(via.title, 120),
      });
    }
    advisoriesByPackage.set(name, advisories);

    // npm does not list every dependent in effects, but every effect it lists must
    // name this package in its own via.
    const effects = new Set();
    for (const effect of entry.effects) {
      if (typeof effect !== 'string' || !PACKAGE_NAME.test(effect) || effect === name || !own(vulnerabilities, effect)) {
        fail('AUDIT_VIA_UNRESOLVED', `${where}: effect "${safe(effect, 80)}" does not resolve to another vulnerable package`);
      }
      if (effects.has(effect)) fail('AUDIT_SCHEMA', `${where}: duplicate effect`);
      effects.add(effect);
      const target = vulnerabilities[effect];
      if (!isObject(target) || !Array.isArray(target.via) || !target.via.includes(name)) {
        fail('AUDIT_GRAPH_INCONSISTENT', `${where}: ${safe(effect, 80)} does not list ${safe(name, 80)} in its via`);
      }
    }
  }

  const tally = Object.fromEntries(SEVERITIES.map((severity) => [severity, 0]));
  for (const name of names) tally[vulnerabilities[name].severity] += 1;
  for (const severity of SEVERITIES) {
    if (tally[severity] !== counts[severity]) {
      fail('AUDIT_COUNT_MISMATCH', `${label}: metadata reports ${counts[severity]} ${severity}, the entries show ${tally[severity]}`);
    }
  }
  const sum = SEVERITIES.reduce((total, severity) => total + counts[severity], 0);
  if (counts.total !== names.length || counts.total !== sum) {
    fail('AUDIT_COUNT_MISMATCH', `${label}: metadata total ${counts.total} disagrees with ${names.length} entries`);
  }

  // Walk vulnerabilities[package].via: strings reference other vulnerable packages,
  // objects are direct advisories. Every package's severity must equal the highest
  // severity it can reach, and the graph must be acyclic.
  const roots = new Map();
  const state = new Map();
  const visit = (name, trail) => {
    if (state.get(name) === 'done') return roots.get(name);
    if (state.get(name) === 'visiting') {
      fail('AUDIT_VIA_CYCLE', `${label}: via cycle ${[...trail, name].map((item) => safe(item, 60)).join(' -> ')}`);
    }
    state.set(name, 'visiting');
    const reachable = new Map();
    let rank = -1;
    for (const via of vulnerabilities[name].via) {
      if (typeof via === 'string') {
        for (const [key, advisory] of visit(via, [...trail, name])) reachable.set(key, advisory);
        rank = Math.max(rank, SEVERITY_RANK[vulnerabilities[via].severity]);
      }
    }
    for (const advisory of advisoriesByPackage.get(name)) {
      reachable.set(`${advisory.package} ${advisory.url}`, advisory);
      rank = Math.max(rank, SEVERITY_RANK[advisory.severity]);
    }
    if (rank !== SEVERITY_RANK[vulnerabilities[name].severity]) {
      fail('AUDIT_SEVERITY_INCONSISTENT', `${label}: ${safe(name, 80)} is ${vulnerabilities[name].severity} but its advisories reach ${SEVERITIES[rank] ?? 'nothing'}`);
    }
    state.set(name, 'done');
    roots.set(name, reachable);
    return reachable;
  };
  for (const name of names) visit(name, []);

  return {
    label,
    vulnerabilities,
    counts,
    dependencies,
    advisories: names.flatMap((name) => advisoriesByPackage.get(name)),
    rootsOf: (name) => [...roots.get(name).values()],
  };
}

function checkedAudit(result, label) {
  const audit = validateAuditReport(parseCommandOutput(result, label), label);
  const flagged = audit.counts.low + audit.counts.moderate + audit.counts.high + audit.counts.critical;
  if ((result.exitCode === 1) !== (flagged > 0)) {
    fail('NPM_EXIT_MISMATCH', `${label}: npm exit status ${result.exitCode} disagrees with ${flagged} findings at or above low`);
  }
  return audit;
}

// ------------------------------------------------------------------ lockfile

export function analyzeLockfile(text) {
  let lock;
  try {
    lock = JSON.parse(text);
  } catch {
    fail('LOCKFILE_INVALID', 'package-lock.json is not valid JSON');
  }
  if (!isObject(lock) || lock.lockfileVersion !== 3 || !isObject(lock.packages) || !own(lock.packages, '') || !isObject(lock.packages[''])) {
    fail('LOCKFILE_INVALID', 'package-lock.json is not a lockfileVersion 3 package map');
  }
  const packages = lock.packages;
  for (const [location, entry] of Object.entries(packages)) {
    if (!isObject(entry)) fail('LOCKFILE_INVALID', `lockfile entry "${safe(location, 120)}" is not an object`);
    if (location !== '' && !isNodePath(location)) fail('LOCKFILE_INVALID', `unsupported lockfile location "${safe(location, 120)}"`);
    if (entry.link) fail('LOCKFILE_INVALID', `linked package "${safe(location, 120)}" is not supported`);
    for (const field of ['dependencies', 'optionalDependencies', 'peerDependencies', 'devDependencies', 'peerDependenciesMeta']) {
      if (own(entry, field) && !isObject(entry[field])) fail('LOCKFILE_INVALID', `lockfile entry "${safe(location, 120)}" has a malformed ${field}`);
    }
  }
  const root = packages[''];
  const names = (entry, field) => (own(entry, field) ? Object.keys(entry[field]) : []);

  // Node module resolution over lockfile locations: nearest node_modules first.
  const resolve = (from, name) => {
    let base = from;
    for (;;) {
      const candidate = base ? `${base}/node_modules/${name}` : `node_modules/${name}`;
      if (own(packages, candidate)) return candidate;
      if (!base) return null;
      const cut = base.lastIndexOf('/node_modules/');
      base = cut === -1 ? '' : base.slice(0, cut);
    }
  };
  const edgesOf = (location, withDev) => {
    const entry = packages[location];
    const edges = new Map();
    for (const name of names(entry, 'dependencies')) edges.set(name, false);
    for (const name of names(entry, 'peerDependencies')) {
      const meta = own(entry, 'peerDependenciesMeta') && own(entry.peerDependenciesMeta, name) ? entry.peerDependenciesMeta[name] : null;
      if (!edges.has(name)) edges.set(name, isObject(meta) && meta.optional === true);
    }
    for (const name of names(entry, 'optionalDependencies')) edges.set(name, true);
    if (withDev) for (const name of names(entry, 'devDependencies')) if (!edges.has(name)) edges.set(name, false);
    return edges;
  };
  const closure = (start, { withDev = false, blocked = null } = {}) => {
    const seen = new Set();
    const queue = [[start, withDev]];
    while (queue.length) {
      const [location, includeDev] = queue.pop();
      for (const [name, optional] of edgesOf(location, includeDev)) {
        const target = resolve(location, name);
        if (!target) {
          if (!optional) fail('LOCKFILE_INVALID', `dependency "${safe(name, 80)}" of "${safe(location || '<root>', 120)}" does not resolve`);
          continue;
        }
        if (target === blocked || seen.has(target)) continue;
        seen.add(target);
        queue.push([target, false]);
      }
    }
    return seen;
  };

  return {
    packages,
    root,
    packageCount: Object.keys(packages).length - 1,
    rootDirect: new Set(['dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies'].flatMap((field) => names(root, field))),
    prodClosure: closure(''),
    allClosure: closure('', { withDev: true }),
    closure,
  };
}

function crossCheckLockfile(audit, lock, issues) {
  if (audit.dependencies.total !== lock.packageCount) {
    issues.push(['LOCKFILE_MISMATCH', `${audit.label}: npm audited ${audit.dependencies.total} packages, the lockfile has ${lock.packageCount}`]);
  }
  for (const [name, entry] of Object.entries(audit.vulnerabilities)) {
    if (entry.isDirect !== lock.rootDirect.has(name)) {
      issues.push(['LOCKFILE_MISMATCH', `${audit.label}: ${safe(name, 80)} isDirect=${entry.isDirect} disagrees with the lockfile root`]);
    }
    for (const node of entry.nodes) {
      const installed = own(lock.packages, node) ? lock.packages[node] : null;
      if (!installed || !lock.allClosure.has(node) || (own(installed, 'name') && installed.name !== name)) {
        issues.push(['LOCKFILE_MISMATCH', `${audit.label}: ${safe(node, 120)} is not an installed lockfile instance of ${safe(name, 80)}`]);
      }
    }
  }
}

function crossCheckRuntime(full, runtime, lock, issues) {
  for (const [name, entry] of Object.entries(runtime.vulnerabilities)) {
    if (!own(full.vulnerabilities, name)) {
      issues.push(['RUNTIME_AUDIT_MISMATCH', `runtime audit flags ${safe(name, 80)}, which the full audit does not`]);
    }
    for (const node of entry.nodes) {
      if (!lock.prodClosure.has(node)) issues.push(['RUNTIME_AUDIT_MISMATCH', `runtime audit flags ${safe(node, 120)}, which the lockfile graph classifies as development-only`]);
    }
  }
  for (const [name, entry] of Object.entries(full.vulnerabilities)) {
    if (!entry.nodes.some((node) => lock.prodClosure.has(node))) continue;
    const runtimeEntry = own(runtime.vulnerabilities, name) ? runtime.vulnerabilities[name] : null;
    if (!runtimeEntry || runtimeEntry.severity !== entry.severity) {
      issues.push(['RUNTIME_AUDIT_MISMATCH', `${safe(name, 80)} has a production instance but the runtime audit does not report it as ${entry.severity}`]);
    }
  }
}

// The waiver covers braces and its derived flags only where every affected
// instance is development-only and installed exclusively through Tailwind 3.
function verifyWaiverGraph(full, runtime, lock, highNames, issues) {
  const root = lock.root;
  const inRoot = (field) => own(root, field) && own(root[field], WAIVER.graphRoot);
  const tailwind = own(lock.packages, TAILWIND_LOCATION) ? lock.packages[TAILWIND_LOCATION] : null;
  if (!tailwind || !inRoot('devDependencies') || inRoot('dependencies') || inRoot('optionalDependencies') || inRoot('peerDependencies')) {
    issues.push(['WAIVER_GRAPH_UNVERIFIED', 'tailwindcss is not exclusively a root devDependency']);
    return;
  }
  if (typeof tailwind.version !== 'string' || !WAIVER.graphRootVersion.test(tailwind.version)) {
    issues.push(['WAIVER_GRAPH_UNVERIFIED', `installed tailwindcss ${safe(tailwind.version, 40)} is not Tailwind 3`]);
    return;
  }
  const viaTailwind = lock.closure(TAILWIND_LOCATION);
  const bypassingTailwind = lock.closure('', { withDev: true, blocked: TAILWIND_LOCATION });
  for (const name of highNames) {
    const entry = full.vulnerabilities[name];
    if (entry.isDirect && name !== WAIVER.graphRoot) {
      issues.push(['WAIVER_GRAPH_UNVERIFIED', `${safe(name, 80)} is a direct dependency outside the Tailwind 3 graph`]);
    }
    if (runtime && own(runtime.vulnerabilities, name)) {
      issues.push(['WAIVER_NOT_DEV_ONLY', `the runtime audit reports ${safe(name, 80)}`]);
    }
    for (const node of entry.nodes) {
      const installed = own(lock.packages, node) ? lock.packages[node] : null;
      if (!installed || installed.dev !== true || lock.prodClosure.has(node)) {
        issues.push(['WAIVER_NOT_DEV_ONLY', `${safe(node, 120)} is not a development-only instance`]);
      }
      if (node !== TAILWIND_LOCATION && (!viaTailwind.has(node) || bypassingTailwind.has(node))) {
        issues.push(['WAIVER_GRAPH_UNVERIFIED', `${safe(node, 120)} is not installed exclusively through tailwindcss 3`]);
      }
    }
  }
}

function isApprovedAdvisory(advisory) {
  return advisory.url === WAIVER.advisoryUrl && advisory.package === WAIVER.packageName && advisory.severity === WAIVER.severity;
}

// --------------------------------------------------------------- evaluation

function toIssue(error) {
  if (error instanceof PolicyError) return [error.code, error.detail];
  return ['INTERNAL_ERROR', safe(error?.message ?? error, 200)];
}

function describeAudit(audit, result) {
  if (!audit) return null;
  return {
    npmExitCode: result.exitCode,
    counts: { ...audit.counts },
    dependencies: audit.dependencies.total,
    packages: Object.fromEntries(Object.entries(audit.vulnerabilities).map(([name, entry]) => [name, entry.severity])),
    advisories: audit.advisories.map(({ id, package: name, severity, range, title }) => ({ id, package: name, severity, range, title })),
  };
}

export function evaluateAuditPolicy({ full, runtime, lockfileText, now }) {
  const issues = [];
  const attempt = (fn) => {
    try {
      return fn();
    } catch (error) {
      issues.push(toIssue(error));
      return null;
    }
  };
  const lock = attempt(() => analyzeLockfile(lockfileText));
  const fullAudit = attempt(() => checkedAudit(full, 'full audit'));
  const runtimeAudit = attempt(() => checkedAudit(runtime, 'runtime audit'));
  if (lock && fullAudit) attempt(() => crossCheckLockfile(fullAudit, lock, issues));
  if (lock && runtimeAudit) attempt(() => crossCheckLockfile(runtimeAudit, lock, issues));
  if (lock && fullAudit && runtimeAudit) attempt(() => crossCheckRuntime(fullAudit, runtimeAudit, lock, issues));

  if (runtimeAudit) {
    for (const [name, entry] of Object.entries(runtimeAudit.vulnerabilities)) {
      if (isHigh(entry.severity)) issues.push(['RUNTIME_HIGH_CRITICAL', `runtime audit: ${safe(name, 80)} is ${entry.severity}`]);
    }
  }

  let waiverRequired = false;
  let highNames = [];
  if (fullAudit) {
    highNames = Object.keys(fullAudit.vulnerabilities).filter((name) => isHigh(fullAudit.vulnerabilities[name].severity)).sort();
    waiverRequired = highNames.length > 0;
    for (const advisory of fullAudit.advisories) {
      if (isHigh(advisory.severity) && !isApprovedAdvisory(advisory)) {
        issues.push(['UNAPPROVED_HIGH_ADVISORY', `${advisory.id} (${advisory.severity}) on ${advisory.package} is not covered by the TASK_264 waiver`]);
      }
    }
    for (const name of highNames) {
      const highRoots = fullAudit.rootsOf(name).filter((advisory) => isHigh(advisory.severity));
      if (highRoots.length === 0) issues.push(['HIGH_WITHOUT_ROOT', `${safe(name, 80)} is HIGH/CRITICAL without a resolvable HIGH/CRITICAL advisory`]);
      else if (highRoots.length !== 1 || !isApprovedAdvisory(highRoots[0])) {
        issues.push(['HIGH_ROOT_NOT_APPROVED', `${safe(name, 80)} derives from ${highRoots.map((advisory) => `${advisory.id}@${advisory.package}`).join(', ')}`]);
      }
    }
    if (waiverRequired) {
      if (lock) attempt(() => verifyWaiverGraph(fullAudit, runtimeAudit, lock, highNames, issues));
      if (!Number.isFinite(now) || now < GRANTED_AT) issues.push(['CLOCK_IMPLAUSIBLE', 'the UTC clock is invalid or precedes the waiver grant']);
      else if (now >= EXPIRES_AT) issues.push(['WAIVER_EXPIRED', `the braces waiver expired at ${WAIVER.hardExpiryUtc}`]);
    }
  }

  const failures = issues.map(([code, detail]) => ({ code, detail: safe(detail, 300) }));
  const verdict = failures.length ? 'FAIL' : waiverRequired ? 'TIME_LIMITED_POLICY_PASS' : 'NPM_AUDIT_CLEAN';
  const waivedRoot = verdict === 'TIME_LIMITED_POLICY_PASS'
    ? fullAudit.advisories.find(isApprovedAdvisory)
    : null;
  const runtimeIds = new Set(runtimeAudit ? runtimeAudit.advisories.map((advisory) => `${advisory.package} ${advisory.id}`) : []);
  const lowerSeverityFindings = (fullAudit ? fullAudit.advisories : [])
    .filter((advisory) => !isHigh(advisory.severity))
    .map(({ id, package: name, severity }) => ({ id, package: name, severity, inRuntimeAudit: runtimeIds.has(`${name} ${id}`) }));
  return {
    gate: 'TASK_264 frontend npm audit policy',
    verdict,
    evaluatedAtUtc: Number.isFinite(now) ? new Date(now).toISOString() : 'invalid',
    rawNpmAuditHighClean: fullAudit ? fullAudit.counts.high + fullAudit.counts.critical === 0 : null,
    waiver: {
      advisory: WAIVER.advisoryUrl,
      package: WAIVER.packageName,
      severity: WAIVER.severity,
      scope: 'tailwindcss@3 development graph only',
      lastValidSecondUtc: WAIVER.lastValidSecondUtc,
      hardExpiryUtc: WAIVER.hardExpiryUtc,
      required: waiverRequired,
      applied: verdict === 'TIME_LIMITED_POLICY_PASS',
    },
    fullAudit: describeAudit(fullAudit, full),
    runtimeAudit: describeAudit(runtimeAudit, runtime),
    waivedRoot: waivedRoot ? { id: waivedRoot.id, package: waivedRoot.package, severity: waivedRoot.severity, range: waivedRoot.range } : null,
    propagatedPackages: waivedRoot ? highNames.filter((name) => name !== WAIVER.packageName) : [],
    lowerSeverityFindings,
    failures,
  };
}

// ---------------------------------------------------------------- execution

export function runBounded(file, args, { cwd, env, timeoutMs = LIMITS.timeoutMs, maxStdoutBytes = LIMITS.maxStdoutBytes, maxStderrBytes = LIMITS.maxStderrBytes } = {}) {
  return new Promise((resolve) => {
    const result = { exitCode: null, signal: null, timedOut: false, error: null, stdout: '', stderr: '', stdoutTruncated: false, stderrTruncated: false };
    const streams = { stdout: { chunks: [], bytes: 0, limit: maxStdoutBytes }, stderr: { chunks: [], bytes: 0, limit: maxStderrBytes } };
    let child = null;
    let settled = false;
    let timer = null;
    let backstop = null;
    const settle = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      clearTimeout(backstop);
      result.stdout = Buffer.concat(streams.stdout.chunks).toString('utf8');
      result.stderr = Buffer.concat(streams.stderr.chunks).toString('utf8');
      resolve(result);
    };
    const stop = () => {
      try {
        child.kill();
      } catch {
        // The process is already gone; "close" settles the result.
      }
      backstop ??= setTimeout(settle, 10_000);
    };
    try {
      child = spawn(file, args, { cwd, env, shell: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (error) {
      result.error = error?.code || 'spawn failed';
      settle();
      return;
    }
    for (const name of ['stdout', 'stderr']) {
      const stream = streams[name];
      child[name].on('data', (chunk) => {
        if (result[`${name}Truncated`]) return;
        stream.bytes += chunk.length;
        if (stream.bytes > stream.limit) {
          result[`${name}Truncated`] = true;
          stop();
          return;
        }
        stream.chunks.push(chunk);
      });
    }
    child.on('error', (error) => {
      result.error = error?.code || 'spawn failed';
      if (child.pid === undefined) settle();
    });
    child.on('close', (code, signal) => {
      if (settled) return;
      result.exitCode = code;
      result.signal = signal;
      settle();
    });
    timer = setTimeout(() => {
      result.timedOut = true;
      stop();
    }, timeoutMs);
  });
}

export function bundledNpmCli(execPath = process.execPath, platform = process.platform) {
  const nodeDirectory = path.dirname(execPath);
  const candidate = platform === 'win32'
    ? path.join(nodeDirectory, 'node_modules', 'npm', 'bin', 'npm-cli.js')
    : path.join(nodeDirectory, '..', 'lib', 'node_modules', 'npm', 'bin', 'npm-cli.js');
  return existsSync(candidate) ? candidate : null;
}

// npm configuration comes from the static arguments, not from inherited npm_* or
// NODE_* switches (NODE_ENV=production would silently imply --omit=dev).
export function childEnv(source) {
  return Object.fromEntries(Object.entries(source).filter(([key]) => !/^npm_|^NODE_OPTIONS$|^NODE_ENV$/i.test(key)));
}

function readLockfile(file) {
  try {
    if (statSync(file).size > LIMITS.maxLockfileBytes) return null;
    return readFileSync(file, 'utf8');
  } catch {
    return null;
  }
}

function printResult(result, npmVersion) {
  const lines = ['TASK_264 frontend npm audit policy gate', `npm ${npmVersion}; evaluated at ${result.evaluatedAtUtc}`];
  for (const [label, audit] of [['full audit (dev included)', result.fullAudit], ['runtime audit (--omit=dev)', result.runtimeAudit]]) {
    if (!audit) {
      lines.push(`${label}: unavailable`);
      continue;
    }
    const { counts } = audit;
    lines.push(`${label}: npm exit ${audit.npmExitCode}; critical ${counts.critical}, high ${counts.high}, moderate ${counts.moderate}, low ${counts.low}, info ${counts.info} (${counts.total} packages, ${audit.dependencies} audited)`);
    for (const advisory of audit.advisories) {
      const mark = result.waivedRoot && advisory.id === result.waivedRoot.id && advisory.package === result.waivedRoot.package ? '  [TIME-LIMITED WAIVER]' : '';
      lines.push(`  ${advisory.severity.toUpperCase()} ${advisory.id} ${advisory.package} ${advisory.range}${mark}`);
    }
  }
  if (result.waivedRoot) {
    lines.push(`waived root: ${result.waivedRoot.id} on ${result.waivedRoot.package}; derived package flags: ${result.propagatedPackages.join(', ')}; valid through ${WAIVER.lastValidSecondUtc}`);
    lines.push(`::warning::TIME_LIMITED_POLICY_PASS is not NPM_AUDIT_CLEAN: npm audit still reports ${result.fullAudit.counts.high} high; only ${result.waivedRoot.id} (braces, Tailwind 3 build graph) is accepted until ${WAIVER.lastValidSecondUtc}`);
  }
  for (const finding of result.lowerSeverityFindings) {
    lines.push(`::warning::${finding.severity.toUpperCase()} ${finding.id} on ${finding.package}${finding.inRuntimeAudit ? ' (runtime)' : ''} is reported, not waived`);
  }
  const lowerPackages = Object.entries(result.fullAudit?.packages ?? {}).filter(([, severity]) => !isHigh(severity));
  if (lowerPackages.length) {
    lines.push(`::warning::Lower-severity npm package flags (reported, not waived): ${lowerPackages.map(([name, severity]) => `${name}=${severity}`).join(', ')}`);
  }
  for (const failure of result.failures) lines.push(`::error::${failure.code}: ${failure.detail}`);
  console.log(lines.join('\n'));
  console.log(JSON.stringify(result, null, 2));
  console.log(`AUDIT_POLICY_RESULT=${result.verdict}`);
}

async function main(argv) {
  process.exitCode = 1;
  if (argv.length) {
    console.error('::error::USAGE: node scripts/security/audit-gate.mjs takes no arguments');
    console.log('AUDIT_POLICY_RESULT=FAIL');
    process.exitCode = 2;
    return;
  }
  const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
  const npmCli = bundledNpmCli();
  const env = childEnv(process.env);
  const run = (args) => (npmCli
    ? runBounded(process.execPath, [npmCli, ...args], { cwd: frontendRoot, env })
    : Promise.resolve({ exitCode: null, signal: null, timedOut: false, error: 'bundled npm-cli.js not found', stdout: '', stderr: '' }));
  const version = await run(['--version']);
  const npmVersion = version.exitCode === 0 && /^\d+\.\d+\.\d+\s*$/.test(version.stdout) ? version.stdout.trim() : 'unknown';
  const lockfileText = readLockfile(path.join(frontendRoot, 'package-lock.json'));
  const full = await run(FULL_AUDIT_ARGS);
  const runtime = await run(RUNTIME_AUDIT_ARGS);
  const result = evaluateAuditPolicy({ full, runtime, lockfileText, now: Date.now() });
  printResult(result, npmVersion);
  process.exitCode = result.verdict === 'FAIL' ? 1 : 0;
}

// Running is the default; only the offline test suite imports the module without it.
if (!globalThis[LIBRARY_IMPORT]) {
  main(process.argv.slice(2)).catch((error) => {
    console.error(`::error::INTERNAL_ERROR: ${safe(error?.message ?? error, 200)}`);
    console.log('AUDIT_POLICY_RESULT=FAIL');
    process.exitCode = 2;
  });
}
