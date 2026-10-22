// TASK_264 offline, deterministic tests for scripts/security/audit-gate.mjs.
//
// Every policy case drives the same evaluateAuditPolicy() the CI gate uses, with
// sanitized fixtures: the npm audit topology captured in TASK_263 (public advisory
// metadata only) over a synthetic lockfile. Nothing here touches the registry, the
// network or the wall clock: time is passed explicitly, and the few real child
// processes are local `node` invocations.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const securityDirectory = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(securityDirectory, '..', '..');
const gatePath = path.join(securityDirectory, 'audit-gate.mjs');

globalThis[Symbol.for('agrosat.task264.auditGate.libraryImport')] = true;
const gate = await import('./audit-gate.mjs');

const IN_WINDOW = Date.parse('2026-10-25T12:00:00Z');
const LAST_MOMENT = Date.parse('2026-11-14T23:59:59.999Z');
const HARD_EXPIRY = Date.parse('2026-11-15T00:00:00Z');
const BRACES_GHSA = 'GHSA-vfj7-8cjw-p6xm';
const BRACES_URL = `https://github.com/advisories/${BRACES_GHSA}`;
const RANK = { info: 0, low: 1, moderate: 2, high: 3, critical: 4 };

// ------------------------------------------------------------------ fixtures

function advisory(pkg, ghsa, severity, source, range = '*') {
  return {
    source,
    name: pkg,
    dependency: pkg,
    title: `fixture advisory ${ghsa}`,
    url: `https://github.com/advisories/${ghsa}`,
    severity,
    cwe: ['CWE-400'],
    cvss: { score: 7.5, vectorString: 'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H' },
    range,
  };
}

const bracesAdvisory = () => ({
  ...advisory('braces', BRACES_GHSA, 'high', 1240992, '<=3.0.3'),
  title: 'braces vulnerable to stack-exhaustion denial of service through deeply nested patterns',
  cwe: ['CWE-674'],
});

const selectorParserAdvisory = () => ({
  ...advisory('postcss-selector-parser', 'GHSA-rj75-hqrm-r3gf', 'moderate', 1241232, '<7.1.6'),
  title: 'PostCSS: Quadratic complexity in flat selector parsing allows CPU exhaustion',
  cwe: ['CWE-400', 'CWE-407'],
  cvss: { score: 5.9, vectorString: 'CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:N/I:N/A:H' },
});

function lockfile() {
  const dev = (version, dependencies) => ({ version, dev: true, ...(dependencies && { dependencies }) });
  return {
    name: 'agrosat-frontend',
    version: '0.1.0',
    lockfileVersion: 3,
    requires: true,
    packages: {
      '': {
        name: 'agrosat-frontend',
        version: '0.1.0',
        dependencies: { axios: '^1.7.2', react: '^18.3.1' },
        devDependencies: { tailwindcss: '^3.4.4', vite: '^8.1.0' },
      },
      'node_modules/axios': { version: '1.20.0', dependencies: { 'follow-redirects': '^1.16.0' } },
      'node_modules/follow-redirects': { version: '1.16.0' },
      'node_modules/react': { version: '18.3.1', dependencies: { 'loose-envify': '^1.1.0' } },
      'node_modules/loose-envify': { version: '1.4.0' },
      'node_modules/tailwindcss': dev('3.4.19', {
        chokidar: '^3.6.0',
        'fast-glob': '^3.3.2',
        micromatch: '^4.0.8',
        'postcss-nested': '^6.2.0',
        'postcss-selector-parser': '^6.1.2',
      }),
      'node_modules/chokidar': dev('3.6.0', { braces: '~3.0.2' }),
      'node_modules/fast-glob': dev('3.3.3', { micromatch: '^4.0.8' }),
      'node_modules/micromatch': dev('4.0.8', { braces: '^3.0.3' }),
      'node_modules/braces': dev('3.0.3'),
      'node_modules/postcss-nested': dev('6.2.0', { 'postcss-selector-parser': '^6.1.1' }),
      'node_modules/postcss-selector-parser': dev('6.1.4'),
      'node_modules/vite': dev('8.1.0'),
    },
  };
}

// TASK_263 post-patch topology (npm 11.13): the braces root, four derived highs,
// the postcss-selector-parser moderate and its derived postcss-nested moderate.
function baselineSpec() {
  return {
    braces: { via: [bracesAdvisory()] },
    chokidar: { via: ['braces'] },
    micromatch: { via: ['braces'] },
    'fast-glob': { via: ['micromatch'] },
    tailwindcss: { via: ['chokidar', 'fast-glob', 'micromatch', 'postcss-nested', 'postcss-selector-parser'] },
    'postcss-selector-parser': { via: [selectorParserAdvisory()] },
    'postcss-nested': { via: ['postcss-selector-parser'] },
  };
}

// Builds a self-consistent npm audit v2 report: severities propagate along via,
// effects mirror via, metadata counts match the entries.
function buildReport(spec, lock = lockfile()) {
  const names = Object.keys(spec).sort();
  const root = lock.packages[''];
  const direct = new Set([...Object.keys(root.dependencies ?? {}), ...Object.keys(root.devDependencies ?? {})]);
  const severityOf = (name) => spec[name].via.reduce((top, via) => {
    const severity = typeof via === 'string' ? severityOf(via) : via.severity;
    return RANK[severity] > RANK[top] ? severity : top;
  }, 'info');
  const vulnerabilities = {};
  for (const name of names) {
    vulnerabilities[name] = {
      name,
      severity: severityOf(name),
      isDirect: direct.has(name),
      via: structuredClone(spec[name].via),
      effects: names.filter((other) => spec[other].via.includes(name)),
      range: '*',
      nodes: spec[name].nodes ?? [`node_modules/${name}`],
      fixAvailable: { name: 'tailwindcss', version: '4.3.3', isSemVerMajor: true },
    };
  }
  const counts = { info: 0, low: 0, moderate: 0, high: 0, critical: 0 };
  for (const entry of Object.values(vulnerabilities)) counts[entry.severity] += 1;
  return {
    auditReportVersion: 2,
    vulnerabilities,
    metadata: {
      vulnerabilities: { ...counts, total: names.length },
      dependencies: { prod: 4, dev: 8, optional: 0, peer: 0, peerOptional: 0, total: Object.keys(lock.packages).length - 1 },
    },
  };
}

function npmResult(report, overrides = {}) {
  const counts = report?.metadata?.vulnerabilities;
  const exitCode = counts && counts.low + counts.moderate + counts.high + counts.critical === 0 ? 0 : 1;
  return {
    exitCode,
    signal: null,
    timedOut: false,
    error: null,
    stdout: typeof report === 'string' ? report : JSON.stringify(report),
    stderr: '',
    stdoutTruncated: false,
    stderrTruncated: false,
    ...overrides,
  };
}

function evaluate({ spec = baselineSpec(), runtimeSpec = {}, lock = lockfile(), full, runtime, lockfileText, now = IN_WINDOW } = {}) {
  return gate.evaluateAuditPolicy({
    full: full ?? npmResult(buildReport(spec, lock)),
    runtime: runtime ?? npmResult(buildReport(runtimeSpec, lock)),
    lockfileText: lockfileText === undefined ? JSON.stringify(lock) : lockfileText,
    now,
  });
}

function mutated(mutate, spec = baselineSpec(), lock = lockfile()) {
  const report = buildReport(spec, lock);
  mutate(report);
  return report;
}

const codes = (result) => result.failures.map((failure) => failure.code);

function expectFail(result, ...expected) {
  assert.equal(result.verdict, 'FAIL', `verdict ${result.verdict}`);
  assert.ok(!codes(result).includes('INTERNAL_ERROR'), `internal error: ${JSON.stringify(result.failures)}`);
  for (const code of expected) {
    assert.ok(codes(result).includes(code), `expected ${code}; got ${codes(result).join(', ') || 'none'}`);
  }
}

function expectPass(result, verdict) {
  assert.deepEqual(result.failures, [], 'failures');
  assert.equal(result.verdict, verdict);
}

// ---------------------------------------------------------------------- cases

const cases = [];
const test = (name, fn) => cases.push({ name, fn });

test('P01 TASK_263 baseline topology before expiry -> TIME_LIMITED_POLICY_PASS', () => {
  const result = evaluate();
  expectPass(result, 'TIME_LIMITED_POLICY_PASS');
  assert.deepEqual(result.waivedRoot, { id: BRACES_GHSA, package: 'braces', severity: 'high', range: '<=3.0.3' });
  assert.deepEqual(result.propagatedPackages, ['chokidar', 'fast-glob', 'micromatch', 'tailwindcss']);
  assert.equal(result.rawNpmAuditHighClean, false);
  assert.deepEqual(result.fullAudit.counts, { info: 0, low: 0, moderate: 2, high: 5, critical: 0, total: 7 });
  assert.deepEqual(result.lowerSeverityFindings, [
    { id: 'GHSA-rj75-hqrm-r3gf', package: 'postcss-selector-parser', severity: 'moderate', inRuntimeAudit: false },
  ]);
  assert.equal(result.waiver.applied, true);
});

test('P02 last valid millisecond 2026-11-14T23:59:59.999Z -> TIME_LIMITED_POLICY_PASS', () => {
  expectPass(evaluate({ now: LAST_MOMENT }), 'TIME_LIMITED_POLICY_PASS');
});

const remediatedSpec = () => ({
  tailwindcss: { via: ['postcss-nested', 'postcss-selector-parser'] },
  'postcss-selector-parser': { via: [selectorParserAdvisory()] },
  'postcss-nested': { via: ['postcss-selector-parser'] },
});

test('P03 genuine remediation to zero HIGH/CRITICAL -> NPM_AUDIT_CLEAN, moderate still reported', () => {
  const result = evaluate({ spec: remediatedSpec() });
  expectPass(result, 'NPM_AUDIT_CLEAN');
  assert.equal(result.waiver.required, false);
  assert.equal(result.rawNpmAuditHighClean, true);
  assert.equal(result.lowerSeverityFindings[0].id, 'GHSA-rj75-hqrm-r3gf');
});

test('P04 genuine remediation needs no waiver after expiry -> NPM_AUDIT_CLEAN', () => {
  expectPass(evaluate({ spec: remediatedSpec(), now: Date.parse('2027-03-01T00:00:00Z') }), 'NPM_AUDIT_CLEAN');
});

test('P05 fully clean audit with npm exit 0 -> NPM_AUDIT_CLEAN', () => {
  const result = evaluate({ spec: {} });
  expectPass(result, 'NPM_AUDIT_CLEAN');
  assert.equal(result.fullAudit.npmExitCode, 0);
});

test('P06 runtime MODERATE is a warning, not a failure -> TIME_LIMITED_POLICY_PASS', () => {
  const axios = { via: [advisory('axios', 'GHSA-2222-3333-4444', 'moderate', 990001)] };
  const result = evaluate({ spec: { ...baselineSpec(), axios }, runtimeSpec: { axios } });
  expectPass(result, 'TIME_LIMITED_POLICY_PASS');
  assert.ok(result.lowerSeverityFindings.some((finding) => finding.package === 'axios' && finding.inRuntimeAudit));
  assert.equal(result.runtimeAudit.counts.moderate, 1);
});

// Clock.
test('N01 at hard expiry 2026-11-15T00:00:00Z -> FAIL', () => {
  expectFail(evaluate({ now: HARD_EXPIRY }), 'WAIVER_EXPIRED');
});
test('N02 after expiry -> FAIL', () => {
  expectFail(evaluate({ now: Date.parse('2026-12-01T00:00:00Z') }), 'WAIVER_EXPIRED');
});
test('N03 clock before the waiver grant -> FAIL', () => {
  expectFail(evaluate({ now: Date.parse('2026-01-01T00:00:00Z') }), 'CLOCK_IMPLAUSIBLE');
});
test('N04 invalid clock -> FAIL', () => {
  expectFail(evaluate({ now: Number.NaN }), 'CLOCK_IMPLAUSIBLE');
});

// Additional advisories.
test('N10 new HIGH advisory on axios (production) -> FAIL', () => {
  const axios = { via: [advisory('axios', 'GHSA-2222-3333-4444', 'high', 990002)] };
  expectFail(evaluate({ spec: { ...baselineSpec(), axios }, runtimeSpec: { axios } }), 'UNAPPROVED_HIGH_ADVISORY', 'RUNTIME_HIGH_CRITICAL');
});
test('N11 new CRITICAL advisory on tailwindcss (development) -> FAIL', () => {
  const spec = baselineSpec();
  spec.tailwindcss.via.push(advisory('tailwindcss', 'GHSA-cfgh-jmpq-rvwx', 'critical', 990003));
  expectFail(evaluate({ spec }), 'UNAPPROVED_HIGH_ADVISORY', 'HIGH_ROOT_NOT_APPROVED');
});
test('N12 new HIGH advisory on vite (development, outside Tailwind) -> FAIL', () => {
  const vite = { via: [advisory('vite', 'GHSA-587h-7wmj-h5pm', 'high', 1242660, '>=8.0.0 <=8.1.5')] };
  expectFail(evaluate({ spec: { ...baselineSpec(), vite } }), 'UNAPPROVED_HIGH_ADVISORY', 'HIGH_ROOT_NOT_APPROVED');
});
test('N13 fresh 2026-10-21 topology (follow-redirects runtime HIGH, vite HIGH) -> FAIL', () => {
  const followRedirects = {
    via: [
      advisory('follow-redirects', 'GHSA-q3jh-wrc6-jgq8', 'high', 1242604, '>=1.16.0 <1.16.1'),
      advisory('follow-redirects', 'GHSA-8r9p-f939-6h3c', 'high', 1242605, '>=1.16.0 <1.16.1'),
    ],
  };
  const vite = {
    via: [
      advisory('vite', 'GHSA-698v-4r3x-3vcv', 'moderate', 1242624),
      advisory('vite', 'GHSA-r825-w8mm-5pxw', 'moderate', 1242653),
      advisory('vite', 'GHSA-587h-7wmj-h5pm', 'high', 1242660),
    ],
  };
  const result = evaluate({ spec: { ...baselineSpec(), 'follow-redirects': followRedirects, vite }, runtimeSpec: { 'follow-redirects': followRedirects } });
  expectFail(result, 'UNAPPROVED_HIGH_ADVISORY', 'RUNTIME_HIGH_CRITICAL', 'WAIVER_NOT_DEV_ONLY');
  assert.equal(result.failures.filter((failure) => failure.code === 'UNAPPROVED_HIGH_ADVISORY').length, 3);
});
test('N14 a different HIGH braces advisory alongside the approved one -> FAIL', () => {
  const spec = baselineSpec();
  spec.braces.via.push(advisory('braces', 'GHSA-9999-8888-7777', 'high', 990004));
  expectFail(evaluate({ spec }), 'UNAPPROVED_HIGH_ADVISORY', 'HIGH_ROOT_NOT_APPROVED');
});
test('N15 approved braces advisory rescored CRITICAL -> FAIL', () => {
  const spec = baselineSpec();
  spec.braces.via[0].severity = 'critical';
  expectFail(evaluate({ spec }), 'UNAPPROVED_HIGH_ADVISORY');
});
test('N16 approved GHSA URL attached to another package -> FAIL', () => {
  const spec = baselineSpec();
  spec.micromatch.via.push(advisory('micromatch', BRACES_GHSA, 'high', 990005));
  expectFail(evaluate({ spec }), 'UNAPPROVED_HIGH_ADVISORY');
});
test('N17 postcss-selector-parser moderate escalated to HIGH -> FAIL', () => {
  const spec = baselineSpec();
  spec['postcss-selector-parser'].via[0].severity = 'high';
  expectFail(evaluate({ spec }), 'UNAPPROVED_HIGH_ADVISORY', 'HIGH_ROOT_NOT_APPROVED');
});
for (const url of [
  'http://github.com/advisories/GHSA-vfj7-8cjw-p6xm',
  `${BRACES_URL}/`,
  `${BRACES_URL}?ref=x`,
  `${BRACES_URL}#x`,
  `${BRACES_URL}\n`,
  ' https://github.com/advisories/GHSA-vfj7-8cjw-p6xm',
  'https://github.com/advisories/ghsa-vfj7-8cjw-p6xm',
  'https://github.com.example/advisories/GHSA-vfj7-8cjw-p6xm',
  'https://example.com/?u=https://github.com/advisories/GHSA-vfj7-8cjw-p6xm',
  'https://github.com/advisories/GHSA-vfj7-8cjw-p6xm-x',
  '',
  42,
]) {
  test(`N18 non-canonical advisory URL ${JSON.stringify(url)} -> FAIL`, () => {
    const spec = baselineSpec();
    spec.braces.via[0].url = url;
    expectFail(evaluate({ spec }), 'AUDIT_ADVISORY_URL');
  });
}
test('N18 missing advisory URL -> FAIL', () => {
  const spec = baselineSpec();
  delete spec.braces.via[0].url;
  expectFail(evaluate({ spec }), 'AUDIT_SCHEMA');
});
test('N19 advisory object naming another package -> FAIL', () => {
  const spec = baselineSpec();
  spec.braces.via[0].dependency = 'micromatch';
  expectFail(evaluate({ spec }), 'AUDIT_SCHEMA');
});
test('N20 approved advisory listed twice -> FAIL', () => {
  const spec = baselineSpec();
  spec.braces.via.push(bracesAdvisory());
  expectFail(evaluate({ spec }), 'AUDIT_SCHEMA');
});

// Malformed, partial or inconsistent audit JSON.
const baselineText = () => JSON.stringify(buildReport(baselineSpec()));
test('N30 empty npm output -> FAIL', () => {
  expectFail(evaluate({ full: npmResult('') }), 'AUDIT_JSON_INVALID');
});
test('N31 human-readable npm output instead of JSON -> FAIL', () => {
  expectFail(evaluate({ full: npmResult('# npm audit report\n\nbraces  *\nSeverity: high\n') }), 'AUDIT_JSON_INVALID');
});
test('N32 truncated JSON -> FAIL', () => {
  const text = baselineText();
  expectFail(evaluate({ full: npmResult(text.slice(0, Math.floor(text.length / 2))) }), 'AUDIT_JSON_INVALID');
});
test('N32 two concatenated JSON documents -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(`${baselineText()}\n${baselineText()}`) }), 'AUDIT_JSON_INVALID');
});
for (const text of ['[]', 'null', '42', '"ok"', '{}']) {
  test(`N33 top-level ${text} -> FAIL`, () => {
    expectFail(evaluate({ full: npmResult(text, { exitCode: 0 }) }), 'AUDIT_SCHEMA');
  });
}
test('N34 registry outage error object (ENOTFOUND) -> FAIL', () => {
  const outage = { error: { code: 'ENOTFOUND', summary: 'request to https://registry.npmjs.org/-/npm/v1/security/advisories/bulk failed', detail: '' } };
  expectFail(evaluate({ full: npmResult(outage, { exitCode: 1 }) }), 'AUDIT_ERROR_OBJECT');
});
test('N34 error object next to vulnerability data -> FAIL', () => {
  const report = mutated((value) => { value.error = { code: 'ENOAUDIT', summary: 'audit endpoint returned an error' }; });
  expectFail(evaluate({ full: npmResult(report) }), 'AUDIT_ERROR_OBJECT');
});
test('N35 unknown top-level key -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(mutated((value) => { value.advisories = {}; })) }), 'AUDIT_SCHEMA');
});
test('N35 unsupported auditReportVersion -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(mutated((value) => { value.auditReportVersion = 1; })) }), 'AUDIT_SCHEMA');
});
test('N35 missing metadata -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(mutated((value) => { delete value.metadata; }), { exitCode: 1 }) }), 'AUDIT_SCHEMA');
});
test('N35 missing vulnerabilities map -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(mutated((value) => { delete value.vulnerabilities; })) }), 'AUDIT_SCHEMA');
});
test('N36 "clean" report that audited no dependencies -> FAIL', () => {
  const empty = mutated((value) => { value.metadata.dependencies = { prod: 0, dev: 0, optional: 0, peer: 0, peerOptional: 0, total: 0 }; }, {});
  expectFail(evaluate({ full: npmResult(empty), runtime: npmResult(empty) }), 'AUDIT_SCHEMA');
});
test('N37 unknown via reference -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(mutated((value) => { value.vulnerabilities.chokidar.via.push('ghost-package'); })) }), 'AUDIT_VIA_UNRESOLVED');
});
test('N37 prototype-name via reference ("constructor") -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(mutated((value) => { value.vulnerabilities.chokidar.via.push('constructor'); })) }), 'AUDIT_VIA_UNRESOLVED');
});
test('N37 unknown effects reference -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(mutated((value) => { value.vulnerabilities.braces.effects.push('ghost-package'); })) }), 'AUDIT_VIA_UNRESOLVED');
});
test('N37 effect that does not reference back through via -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(mutated((value) => { value.vulnerabilities.braces.effects.push('fast-glob'); })) }), 'AUDIT_GRAPH_INCONSISTENT');
});
test('N38 via cycle -> FAIL', () => {
  const report = mutated((value) => {
    value.vulnerabilities.chokidar.via.push('tailwindcss');
    value.vulnerabilities.tailwindcss.effects.push('chokidar');
  });
  expectFail(evaluate({ full: npmResult(report) }), 'AUDIT_VIA_CYCLE');
});
test('N39 metadata HIGH count disagrees with the entries -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(mutated((value) => { value.metadata.vulnerabilities.high = 4; })) }), 'AUDIT_COUNT_MISMATCH');
});
test('N39 metadata total disagrees with the entries -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(mutated((value) => { value.metadata.vulnerabilities.total = 6; })) }), 'AUDIT_COUNT_MISMATCH');
});
test('N39 vulnerability entries hidden behind zero counts -> FAIL', () => {
  const report = mutated((value) => { value.metadata.vulnerabilities = { info: 0, low: 0, moderate: 0, high: 0, critical: 0, total: 0 }; });
  expectFail(evaluate({ full: npmResult(report, { exitCode: 0 }) }), 'AUDIT_COUNT_MISMATCH');
});
test('N40 package severity inconsistent with its advisories -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(mutated((value) => {
    value.vulnerabilities.tailwindcss.severity = 'moderate';
    value.metadata.vulnerabilities.high = 4;
    value.metadata.vulnerabilities.moderate = 3;
  })) }), 'AUDIT_SEVERITY_INCONSISTENT');
});
test('N41 HIGH package without a resolvable HIGH root -> FAIL', () => {
  const report = mutated((value) => {
    value.vulnerabilities['fast-glob'].via = ['postcss-nested'];
    value.vulnerabilities['postcss-nested'].effects.push('fast-glob');
    value.vulnerabilities.micromatch.effects = value.vulnerabilities.micromatch.effects.filter((name) => name !== 'fast-glob');
  });
  expectFail(evaluate({ full: npmResult(report) }), 'AUDIT_SEVERITY_INCONSISTENT');
});
test('N42 malformed vulnerability entries -> FAIL', () => {
  const mutations = [
    (value) => { value.vulnerabilities.braces.nodes = ['node_modules/micromatch']; },
    (value) => { value.vulnerabilities.braces.nodes = []; },
    (value) => { value.vulnerabilities.braces.severity = 'severe'; },
    (value) => { value.vulnerabilities.braces.via = []; },
    (value) => { value.vulnerabilities.braces.name = 'micromatch'; },
    (value) => { value.vulnerabilities.braces.isDirect = 'no'; },
    (value) => { value.vulnerabilities.braces.extra = true; },
    (value) => { value.vulnerabilities.braces.via[0].source = '1240992'; },
    (value) => { value.vulnerabilities.braces.via[0].cvss = { score: 99, vectorString: null }; },
    (value) => { value.vulnerabilities.braces.via[0].script = 'process.exit(0)'; },
    (value) => { value.vulnerabilities.braces.fixAvailable = 'yes'; },
  ];
  for (const mutate of mutations) expectFail(evaluate({ full: npmResult(mutated(mutate)) }), 'AUDIT_SCHEMA');
});
test('N42 own "__proto__" package key in audit JSON -> FAIL', () => {
  const entry = '{"name":"__proto__","severity":"high","isDirect":false,"via":["braces"],"effects":[],"range":"*","nodes":["node_modules/__proto__"],"fixAvailable":true}';
  const text = baselineText().replace('"vulnerabilities":{', `"vulnerabilities":{"__proto__":${entry},`);
  expectFail(evaluate({ full: npmResult(text, { exitCode: 1 }) }), 'AUDIT_SCHEMA');
});
test('N43 isDirect disagrees with the lockfile root -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(mutated((value) => { value.vulnerabilities.braces.isDirect = true; })) }), 'LOCKFILE_MISMATCH');
});
test('N43 audited package count disagrees with the lockfile -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(mutated((value) => { value.metadata.dependencies.total = 300; })) }), 'LOCKFILE_MISMATCH');
});
test('N43 audit node missing from the lockfile -> FAIL', () => {
  const spec = baselineSpec();
  spec.braces.nodes = ['node_modules/braces', 'node_modules/chokidar/node_modules/braces'];
  expectFail(evaluate({ spec }), 'LOCKFILE_MISMATCH');
});

// Lockfile input.
test('N50 unreadable or invalid lockfile -> FAIL', () => {
  for (const lockfileText of [null, '', '{', '[]', JSON.stringify({ lockfileVersion: 2, packages: { '': {} } })]) {
    expectFail(evaluate({ lockfileText }), 'LOCKFILE_INVALID');
  }
});
test('N50 lockfile dependency that does not resolve -> FAIL', () => {
  const lock = lockfile();
  lock.packages['node_modules/micromatch'].dependencies.picomatch = '^2.3.1';
  expectFail(evaluate({ lock }), 'LOCKFILE_INVALID');
});
test('N50 linked lockfile package -> FAIL', () => {
  const lock = lockfile();
  lock.packages['node_modules/vite'] = { resolved: '../vite', link: true };
  expectFail(evaluate({ lock }), 'LOCKFILE_INVALID');
});

// npm command, network and registry failures.
test('N60 npm could not be started -> FAIL', () => {
  expectFail(evaluate({ full: npmResult('', { exitCode: null, error: 'ENOENT' }) }), 'NPM_SPAWN_FAILED');
});
test('N61 npm timed out -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(baselineText(), { timedOut: true }) }), 'NPM_TIMEOUT');
});
test('N62 npm output over the size limit -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(baselineText(), { stdoutTruncated: true }) }), 'NPM_OUTPUT_LIMIT');
});
test('N63 npm killed by a signal -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(baselineText(), { exitCode: null, signal: 'SIGTERM' }) }), 'NPM_SIGNAL');
});
for (const exitCode of [2, 3, 127, 255, -4058, null, '1', 1.5]) {
  test(`N64 unexpected npm exit status ${JSON.stringify(exitCode)} -> FAIL`, () => {
    expectFail(evaluate({ full: npmResult(baselineText(), { exitCode }) }), 'NPM_EXIT_STATUS');
  });
}
test('N65 npm exit 0 while HIGH findings are reported -> FAIL', () => {
  expectFail(evaluate({ full: npmResult(baselineText(), { exitCode: 0 }) }), 'NPM_EXIT_MISMATCH');
});
test('N65 npm exit 1 with a clean report -> FAIL', () => {
  expectFail(evaluate({ spec: {}, full: npmResult(buildReport({}), { exitCode: 1 }) }), 'NPM_EXIT_MISMATCH');
});
test('N66 runtime audit command failure -> FAIL', () => {
  expectFail(evaluate({ runtime: npmResult('', { exitCode: 2 }) }), 'NPM_EXIT_STATUS');
});
test('N67 runtime audit registry error -> FAIL', () => {
  expectFail(evaluate({ runtime: npmResult({ error: { code: 'E503', summary: 'Service Unavailable' } }, { exitCode: 1 }) }), 'AUDIT_ERROR_OBJECT');
});

// Runtime classification of the waived graph.
test('N70 braces instance not flagged dev in the lockfile -> FAIL', () => {
  const lock = lockfile();
  delete lock.packages['node_modules/braces'].dev;
  expectFail(evaluate({ lock }), 'WAIVER_NOT_DEV_ONLY');
});
test('N71 production package reaches micromatch/braces (stale dev flags) -> FAIL', () => {
  const lock = lockfile();
  lock.packages['node_modules/axios'].dependencies.micromatch = '^4.0.8';
  expectFail(evaluate({ lock }), 'WAIVER_NOT_DEV_ONLY');
});
test('N72 runtime audit reports braces HIGH -> FAIL', () => {
  const runtimeSpec = { braces: { via: [bracesAdvisory()] } };
  expectFail(evaluate({ runtimeSpec }), 'RUNTIME_HIGH_CRITICAL', 'WAIVER_NOT_DEV_ONLY');
});
test('N73 runtime audit reports HIGH on a production package -> FAIL', () => {
  const followRedirects = { via: [advisory('follow-redirects', 'GHSA-q3jh-wrc6-jgq8', 'high', 1242604)] };
  expectFail(evaluate({ spec: { ...baselineSpec(), 'follow-redirects': followRedirects }, runtimeSpec: { 'follow-redirects': followRedirects } }), 'RUNTIME_HIGH_CRITICAL');
});
test('N74 tailwindcss declared as a production dependency -> FAIL', () => {
  const lock = lockfile();
  delete lock.packages[''].devDependencies.tailwindcss;
  lock.packages[''].dependencies.tailwindcss = '^3.4.4';
  expectFail(evaluate({ lock }), 'WAIVER_GRAPH_UNVERIFIED');
});
test('N75 Tailwind 4 installed -> FAIL', () => {
  const lock = lockfile();
  lock.packages['node_modules/tailwindcss'].version = '4.3.3';
  expectFail(evaluate({ lock }), 'WAIVER_GRAPH_UNVERIFIED');
});
test('N76 another development tool reaches micromatch outside Tailwind -> FAIL', () => {
  const lock = lockfile();
  lock.packages[''].devDependencies['glob-tool'] = '^1.0.0';
  lock.packages['node_modules/glob-tool'] = { version: '1.0.0', dev: true, dependencies: { micromatch: '^4.0.8' } };
  expectFail(evaluate({ lock }), 'WAIVER_GRAPH_UNVERIFIED');
});
test('N77 second braces instance under a production package -> FAIL', () => {
  const lock = lockfile();
  lock.packages['node_modules/axios'].dependencies.braces = '^3.0.3';
  lock.packages['node_modules/axios/node_modules/braces'] = { version: '3.0.3' };
  const spec = baselineSpec();
  spec.braces.nodes = ['node_modules/axios/node_modules/braces', 'node_modules/braces'];
  expectFail(evaluate({ spec, lock }), 'WAIVER_NOT_DEV_ONLY');
});
test('N78 runtime audit flags a package the full audit does not -> FAIL', () => {
  const axios = { via: [advisory('axios', 'GHSA-2222-3333-4444', 'low', 990006)] };
  expectFail(evaluate({ runtimeSpec: { axios } }), 'RUNTIME_AUDIT_MISMATCH');
});
test('N79 runtime audit misses a production finding -> FAIL', () => {
  const axios = { via: [advisory('axios', 'GHSA-2222-3333-4444', 'moderate', 990007)] };
  expectFail(evaluate({ spec: { ...baselineSpec(), axios } }), 'RUNTIME_AUDIT_MISMATCH');
});

// Real child processes (local node only) and the fixed CLI surface.
const node = (source) => [process.execPath, ['-e', source]];
test('X01 runBounded reports a real non-0/1 exit status -> FAIL', async () => {
  const result = await gate.runBounded(...node('process.exit(3)'), { timeoutMs: 20_000 });
  assert.equal(result.exitCode, 3);
  expectFail(evaluate({ full: result }), 'NPM_EXIT_STATUS');
});
test('X02 runBounded enforces the timeout -> FAIL', async () => {
  const result = await gate.runBounded(...node('setTimeout(() => {}, 60000)'), { timeoutMs: 300 });
  assert.equal(result.timedOut, true);
  expectFail(evaluate({ full: result }), 'NPM_TIMEOUT');
});
test('X03 runBounded enforces the output limit -> FAIL', async () => {
  const result = await gate.runBounded(...node("process.stdout.write('x'.repeat(200000)); setTimeout(() => {}, 60000)"), { timeoutMs: 20_000, maxStdoutBytes: 1000 });
  assert.equal(result.stdoutTruncated, true);
  assert.ok(result.stdout.length <= 1000);
  expectFail(evaluate({ full: result }), 'NPM_OUTPUT_LIMIT');
});
test('X04 runBounded reports a missing executable -> FAIL', async () => {
  const result = await gate.runBounded(path.join(securityDirectory, 'no-such-npm.exe'), ['audit'], { timeoutMs: 20_000 });
  assert.ok(result.error);
  expectFail(evaluate({ full: result }), 'NPM_SPAWN_FAILED');
});
test('X05 the CLI accepts no arguments, so no fixture or clock can be injected', () => {
  const run = spawnSync(process.execPath, [gatePath, '--now=2026-11-01T00:00:00Z'], { cwd: frontendRoot, encoding: 'utf8', timeout: 20_000 });
  assert.equal(run.status, 2);
  assert.match(run.stdout, /AUDIT_POLICY_RESULT=FAIL/);
});
test('X06 npm child environment drops npm_* and NODE_OPTIONS/NODE_ENV overrides', () => {
  const env = gate.childEnv({ PATH: 'p', npm_config_omit: 'dev', NPM_CONFIG_AUDIT_LEVEL: 'critical', npm_execpath: 'x', NODE_OPTIONS: '--require x', NODE_ENV: 'production', CI: 'true' });
  assert.deepEqual(env, { PATH: 'p', CI: 'true' });
});
test('X07 static npm audit invocations', () => {
  const common = ['audit', '--json', '--package-lock-only', '--audit-level=low', '--registry=https://registry.npmjs.org/'];
  assert.deepEqual(gate.FULL_AUDIT_ARGS, [...common, '--include=dev', '--include=optional', '--include=peer']);
  assert.deepEqual(gate.RUNTIME_AUDIT_ARGS, [...common, '--omit=dev']);
  const cli = gate.bundledNpmCli();
  assert.ok(cli && existsSync(cli), 'npm-cli.js bundled with this Node runtime');
  assert.equal(gate.bundledNpmCli(path.join(securityDirectory, 'node.exe'), 'win32'), null);
});
test('X08 waiver constants are exact', () => {
  assert.equal(gate.WAIVER.advisoryUrl, BRACES_URL);
  assert.equal(gate.WAIVER.packageName, 'braces');
  assert.equal(gate.WAIVER.severity, 'high');
  assert.equal(gate.WAIVER.lastValidSecondUtc, '2026-11-14T23:59:59Z');
  assert.equal(new Date(gate.WAIVER.hardExpiryUtc).toISOString(), '2026-11-15T00:00:00.000Z');
});
test('X09 the gate reads no clock, fixture or policy override from the environment', () => {
  const source = readFileSync(gatePath, 'utf8');
  assert.deepEqual(source.match(/process\.env\b[^\r\n]*/g), ['process.env);']);
  assert.deepEqual(source.match(/process\.argv\b[^\r\n]*/g), ['process.argv.slice(2)).catch((error) => {']);
  assert.equal((source.match(/Date\.now\(\)/g) ?? []).length, 1);
});
test('X10 CI frontend job keeps its first three commands and ends with the gate', () => {
  const workflow = readFileSync(path.resolve(frontendRoot, '..', '.github', 'workflows', 'ci.yml'), 'utf8');
  const job = workflow.split(/\r?\n/).reduce((state, line) => {
    if (/^ {2}frontend:$/.test(line)) state.inside = true;
    else if (state.inside && /^ {2}\S/.test(line)) state.inside = false;
    if (state.inside) state.lines.push(line);
    return state;
  }, { inside: false, lines: [] }).lines;
  const runs = job.map((line) => /^\s+run: (.*)$/.exec(line)?.[1]).filter(Boolean);
  assert.deepEqual(runs, [
    'npm ci --no-fund --no-audit',
    'npm run test:contracts',
    'npm run build',
    'node scripts/security/audit-gate.test.mjs && node scripts/security/audit-gate.mjs',
  ]);
  const text = job.join('\n');
  assert.match(text, /working-directory: frontend/);
  assert.doesNotMatch(text, /continue-on-error|\|\| *true|^\s+if:|^\s+shell:/m);
});

let failed = 0;
for (const { name, fn } of cases) {
  try {
    await fn();
    console.log(`PASS  ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`FAIL  ${name}\n      ${String(error?.message ?? error).split('\n')[0]}`);
  }
}
console.log(JSON.stringify({ suite: 'audit-gate policy tests', cases: cases.length, passed: cases.length - failed, failed }));
process.exitCode = failed ? 1 : 0;
