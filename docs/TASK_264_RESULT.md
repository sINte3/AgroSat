# TASK_264 — Result: frontend npm audit policy gate (time-limited braces waiver)

```text
TASK_264_RESULT=HOLD_FRONTEND_POLICY
BASE_SHA=23b50498e254204614334e3a81c8295f45e01e9d
LOCKFILE_COMMIT=27fd30e13e643a5296117d4abe001ec5cdfbe867
POLICY_COMMIT=7e02dbeba650ae02f7999845ec8571f84974b90f
RESULT_SHA=<the report-only commit that adds this file; recorded in the evidence README_RU.md and the operator handoff>
FULL_NPM_AUDIT_RAW=FAIL
TEMPORARY_CI_POLICY=FAIL
RUNTIME_HIGH_CRITICAL=NONZERO
NEGATIVE_POLICY_TESTS=PASS
FRONTEND_CONTRACTS=PASS
FRONTEND_BUILD=PASS
PRODUCTION_MUTATIONS=0
GIT_PUSH=NO
REMOTE_CI=NOT_RUN
DEPLOYED=NO
```

## Why HOLD

The gate works as specified and **correctly fails** on the fresh audit of 2026-10-21. Three HIGH advisories were published after the TASK_263 capture of 2026-10-15. The approved waiver does not cover them:

| Advisory | Package @ installed | Severity | Scope | Range | npm `fixAvailable` |
|---|---|---|---|---|---|
| GHSA-q3jh-wrc6-jgq8 | follow-redirects 1.16.0 | high | **production runtime** (via axios `^1.16.0`) | >=1.16.0 <1.16.1 | true |
| GHSA-8r9p-f939-6h3c | follow-redirects 1.16.0 | high | **production runtime** | >=1.16.0 <1.16.1 | true |
| GHSA-587h-7wmj-h5pm | vite 8.1.0 (direct) | high | development | >=8.0.0 <=8.1.5 | true |
| GHSA-698v-4r3x-3vcv, GHSA-r825-w8mm-5pxw | vite 8.1.0 | moderate | development | >=8.0.0 <=8.1.5 | true |

Remediating them requires lockfile changes beyond the vetted TASK_263 patch. This task does not authorize that, so they were not attempted. The waiver itself is still valid: GHSA-vfj7-8cjw-p6xm remains HIGH, unpatched (`first_patched_version: null`), not withdrawn, and `braces@latest` is still 3.0.3. The gate attributes the braces chain cleanly. It reports no failure on braces, chokidar, micromatch, fast-glob or tailwindcss. Offline, the TASK_263 post-patch topology evaluated against this lockfile yields `TIME_LIMITED_POLICY_PASS`.

## Starting state (verified read-only)

* Worktree `C:\AgroSat_worktrees\program_r3_b2_frontend_security_rc`, branch `task/263-program-r3-b2-frontend-security`, HEAD `23b50498…`. The only change was the uncommitted `frontend/package-lock.json`.
* That diff was byte-identical to TASK_263 evidence `raw/41_frontend_lockfile.patch` (sha256 `94e576ff…aa4ded`). The TASK_263 ZIP checksum matched its sidecar.
* Neither TASK_253 `6bf71065…` nor TASK_262 `06c06509…`/`6e634798…` is an ancestor. Production baseline `3bcbcab3…` is an ancestor.

## Changes (local commits only)

| Commit | Files | Content |
|---|---|---|
| A `27fd30e1…` | `frontend/package-lock.json` | Vetted TASK_263 patch only: axios 1.18.0 → 1.20.0, source-map-js 1.2.1 → 1.2.2. Integrity matches registry `dist.integrity`. 265 → 265 packages, 0 added, 0 removed. Root manifest unchanged. |
| B `7e02dbeb…` | `.github/workflows/ci.yml`, `frontend/scripts/security/audit-gate.mjs`, `frontend/scripts/security/audit-gate.test.mjs` | Fail-closed gate and 103 offline policy tests. The single CI audit step is replaced. The first three frontend commands are unchanged. |
| C (this file) | `docs/TASK_264_RESULT.md` | Report only. |

Not changed: `frontend/package.json`, `frontend/src`, styles, Tailwind/PostCSS/Vite config, backend, ops, production checkout.

## Policy implemented (`audit-gate.mjs`)

* **Audits.** It runs two fresh, real audits through the npm CLI bundled next to the running Node (`node npm-cli.js`, no shell). Each call has a 180 s timeout and an 8 MiB stdout limit. Arguments are static: `audit --json --package-lock-only --audit-level=low --registry=https://registry.npmjs.org/`, plus `--include=dev --include=optional --include=peer` for the full audit and `--omit=dev` for the runtime audit. `npm_*`, `NODE_OPTIONS` and `NODE_ENV` are removed from the child environment.
* **Command and output checks.** Any of these is FAIL: exit status other than 0/1, an exit status that disagrees with the findings, a timeout, a signal, the output limit, empty, partial or concatenated JSON, a top-level `error`, or unknown keys at any level. The report must have `auditReportVersion` 2, metadata counts that equal the entries, and an audited package total equal to the lockfile. A cycle or an unresolved `via`/`effects` reference fails. So does a package severity that is not the maximum it reaches, or an advisory URL that is not exactly `https://github.com/advisories/GHSA-xxxx-xxxx-xxxx`.
* **Waiver.** Every HIGH/CRITICAL direct advisory must be exactly `https://github.com/advisories/GHSA-vfj7-8cjw-p6xm` at severity `high` on package `braces`. Each HIGH/CRITICAL package must reach that root and no other HIGH/CRITICAL root. Every affected lockfile instance must meet all of these:
  * it is flagged `dev: true`;
  * it is unreachable from the root's production edges;
  * it is reachable from `node_modules/tailwindcss` 3.x, which must be a root devDependency only;
  * it is unreachable from the root once Tailwind is removed.

  The runtime audit must report no HIGH/CRITICAL and no affected package. It must also agree with the lockfile's production graph.
* **Clock.** The waiver is active only while `2026-10-21T00:00:00Z <= Date.now() < 2026-11-15T00:00:00Z`. The gate has no argument, environment or fixture override; tests pass the clock explicitly.
* **Verdicts.** `NPM_AUDIT_CLEAN` means no HIGH/CRITICAL at all, and needs no waiver. `TIME_LIMITED_POLICY_PASS` means the waiver was applied. `FAIL` exits with code 1. MODERATE/LOW advisories and package flags are always printed as `::warning::`.

## Verification (fresh, this host, restricted environment, Node 24.16.0 / npm 11.13.0)

| Check | Command | Exit | Result |
|---|---|---|---|
| Clean install | `npm ci --no-fund --no-audit` | 0 | PASS (233 packages) |
| Policy tests | `node scripts/security/audit-gate.test.mjs` | 0 | **103/103 PASS** |
| Contracts | `npm run test:contracts` | 0 | **23/23 PASS** |
| Production build | `npm run build` | 0 | PASS (pre-existing chunk-size warning) |
| Raw audit | `npm audit --audit-level=high` | 1 | FAIL: 7 high / 2 moderate |
| Raw full JSON | `npm audit --json` | 1 | 7 high / 2 moderate (9 packages, 265 audited) |
| Raw runtime JSON | `npm audit --omit=dev --json` | 1 | **1 high** (follow-redirects) |
| **Gate** | `node scripts/security/audit-gate.mjs` | **1** | `AUDIT_POLICY_RESULT=FAIL`, 11 failure records, all on follow-redirects/vite |
| Whitespace | `git diff --check 23b50498..HEAD` | 0 | PASS |
| Mutation probe | 7 weakened copies of the gate (scratch only) | — | each one fails the unchanged test suite |

Fresh full-audit packages: braces, chokidar, fast-glob, micromatch, tailwindcss (high, braces root); follow-redirects (high); vite (high); postcss-selector-parser (moderate, GHSA-rj75-hqrm-r3gf); postcss-nested (moderate, derived). Compared with TASK_263 (5 high / 2 moderate), the counts rose to 7 high / 2 moderate.

## Raw `npm audit` vs policy pass

`npm audit --audit-level=high` will keep exiting 1 for as long as GHSA-vfj7-8cjw-p6xm exists. No braces release fixes it. A future `TIME_LIMITED_POLICY_PASS` would therefore be a documented, temporary risk acceptance for an isolated, trusted GitHub Actions build. It is not `NPM_AUDIT_CLEAN` and does not mean "all vulnerabilities fixed". It also gives no trust to builds on the production-associated Windows host; B1 host security remains HOLD.

## Limitations

* The gate is bound to the npm 11 audit JSON v2 shape. A format change fails closed and needs a HOLD_AUDIT_POLICY review. npm does not list every dependent in `effects`, so only the effect → via direction is enforced.
* The registry and `windows-latest` behaviour (pwsh `&&`, the npm bundled with the runner's Node 24) were not exercised remotely (`REMOTE_CI=NOT_RUN`).
* A proxy configured only through `npm_config_*` variables would be stripped, and the gate would fail closed.

## Next decision (coordinator, not part of TASK_264)

1. Authorize a separate, vetted, lockfile-only update for follow-redirects (≥1.16.1, inside axios `^1.16.0`) and vite (>8.1.5, inside `^8.1.0`). npm reports in-range fixes for both. Then re-run this gate unchanged; `TIME_LIMITED_POLICY_PASS` is expected only if no other new advisory appears.
2. The waiver hard-expires at **2026-11-15T00:00:00Z**. After that the gate fails while braces remains unfixed; only a fixed braces release or the Tailwind 4 migration will clear it.
3. Integration with TASK_262 and any remote CI push need separate permission.
