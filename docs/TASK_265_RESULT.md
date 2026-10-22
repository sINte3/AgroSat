# TASK_265 — Result: frontend lockfile remediation (follow-redirects, vite)

```text
TASK_265_RESULT=GO_FRONTEND_CANDIDATE
BASE_SHA=9b7ee308c90ad0e9a7b5c35962b53053130522ed
RESULT_SHA=<the report-only commit that adds this file; recorded in the evidence README_RU.md and the operator handoff>
LOCKFILE_COMMIT=9e97ccadcf985453a7cf8c5e33e56b700d3a43fe
FOLLOW_REDIRECTS_VERSION=1.16.1
VITE_VERSION=8.1.6
RAW_FULL_NPM_AUDIT=FAIL
TEMPORARY_CI_POLICY=TIME_LIMITED_POLICY_PASS
RUNTIME_HIGH_CRITICAL=0
NEGATIVE_POLICY_TESTS=PASS
FRONTEND_CONTRACTS=PASS
FRONTEND_BUILD=PASS
PRODUCTION_MUTATIONS=0
GIT_PUSH=NO
REMOTE_CI=NOT_RUN
DEPLOYED=NO
```

## Outcome

The unchanged TASK_264 gate now returns `TIME_LIMITED_POLICY_PASS`. The runtime (`--omit=dev`) audit has 0 findings. In the full audit, the only HIGH root is the waived `braces` advisory GHSA-vfj7-8cjw-p6xm, which expires at **2026-11-15T00:00:00Z**. Raw `npm audit --audit-level=high` still exits 1 (5 high, 2 moderate), so it is **not** `NPM_AUDIT_CLEAN`. No new HIGH or CRITICAL advisory appeared, and no correction retry was needed.

## Starting state (verified read-only)

* Worktree `C:\AgroSat_worktrees\program_r3_b2_frontend_security_rc`, branch `task/263-program-r3-b2-frontend-security`. HEAD was exactly `9b7ee308…` and the tree was clean.
* `27fd30e1…`, `7e02dbeb…` and `23b50498…` are ancestors. Backend `06c06509…` and TASK_253 `6bf71065…` are not.
* Lockfile: follow-redirects 1.16.0 (production, via axios `^1.16.0`) and vite 8.1.0 (dev, root `^8.1.0`). Each had one instance.
* The operator was non-elevated. The toolchain was Node 24.16.0 with its bundled npm 11.13.0, the same npm the gate uses. The user-global npm 11.16.0 on PATH was not used.

## Advisories (GitHub Advisory API, 2026-10-22)

| Advisory | Package | Severity | Affected | Patched |
|---|---|---|---|---|
| GHSA-q3jh-wrc6-jgq8 | follow-redirects | high | >=1.16.0 <1.16.1 | 1.16.1 |
| GHSA-8r9p-f939-6h3c | follow-redirects | high | >=1.16.0 <1.16.1 | 1.16.1 |
| GHSA-587h-7wmj-h5pm | vite (launch-editor) | high | >=8.0.0 <=8.1.5 | 8.1.6 |
| GHSA-698v-4r3x-3vcv | vite (launch-editor) | moderate | >=8.0.0 <=8.1.5 | 8.1.6 |
| GHSA-r825-w8mm-5pxw | vite (launch-editor) | moderate | >=8.0.0 <=8.1.5 | 8.1.6 |
| GHSA-rq7h-c2jc-7f22 | vite | moderate | >=8.0.0 <=8.1.5 | 8.1.6 |
| GHSA-9jrq-w75r-8gcw | vite | low | >=8.0.0 <=8.1.5 | 8.1.6 (published after TASK_264) |

For `follow-redirects@1.16.1` and `vite@8.1.6`, the `affects=` query returns 0 advisories. So 1.16.1 is the minimal secure follow-redirects and also the registry `latest`. 8.1.6 is the first patched 8.1.x and is enough; no newer vite was needed. GHSA-vfj7-8cjw-p6xm (braces) is still HIGH, unpatched and not withdrawn.

## Change (local commit `9e97ccad…`, `frontend/package-lock.json` only)

The versions were resolved with the bundled npm 11.13.0 in lockfile-only mode (`--package-lock-only --ignore-scripts`). The run used a restricted allow-listed environment, with a task-private cache and an empty user npmrc.

1. `npm update follow-redirects` — follow-redirects 1.16.0 → 1.16.1. That was the only change.
2. vite 8.1.6. npm can pin an exact version only through `npm install vite@8.1.6`, which also writes package.json. `--no-save` skips the lockfile write as well. This step therefore ran on a **scratch copy** outside the repository. The scratch `package.json` was then restored byte-identical, and `npm install --package-lock-only` brought the root lockfile metadata back to `^8.1.0`. A second pass left the lockfile byte-identical. The worktree's `package.json` was never written.

Result: 265 → 265 packages, 0 added, 0 removed, 23 entries changed. Every changed `integrity` matches the registry `dist.integrity`.

| Entries | Change | Why |
|---|---|---|
| follow-redirects | 1.16.0 → 1.16.1 (production) | target |
| vite | 8.1.0 → 8.1.6 (dev) | target |
| rolldown | 1.1.3 → 1.1.5 (dev) | vite 8.1.6 declares `rolldown ~1.1.5`; 1.1.5 is the minimal satisfying version |
| @oxc-project/types | 0.137.0 → 0.139.0 (dev) | rolldown 1.1.5 pins `=0.139.0` |
| 15 × @rolldown/binding-* | 1.1.3 → 1.1.5 (dev, optional, per platform) | rolldown 1.1.5 pins them exactly |
| vite/node_modules/picomatch | 4.0.4 → 4.0.7 (dev) | vite 8.1.6 declares `^4.0.5`; npm chose the highest satisfying version, not 4.0.5 |
| @napi-rs/wasm-runtime, @tybys/wasm-util | 1.1.6 → 1.2.5, 0.10.3 → 0.10.4 (dev, optional) | not required by any range; npm re-resolved them under the replaced `@rolldown/binding-wasm32-wasi` (cpu `wasm32`) |

Blast radius: every change except follow-redirects is dev-only and inside vite's own dependency closure. Nothing outside it changed. The two optional wasm32 packages are not installed on win32-x64 (verified) or on linux-x64. axios stays at 1.20.0 and source-map-js at 1.2.2. `package.json`, `.github/workflows/ci.yml`, `audit-gate.mjs`, `audit-gate.test.mjs`, `frontend/src` and `frontend/scripts` have the same blob/tree ids as at preflight.

Line endings: the repository uses `core.autocrlf=true`. The tested worktree file is CRLF. The committed blob is its LF normalization (`git hash-object` equals the HEAD blob `7cb2d90b…`). TASK_264 had the same arrangement.

## Verification (fresh, this host, restricted environment)

| Check | Command | Exit | Result |
|---|---|---|---|
| Clean install | `npm ci --no-fund --no-audit` | 0 | PASS (233 packages, same as TASK_264) |
| Identity | `npm ls vite follow-redirects axios source-map-js rolldown --all`; `npm ls --all` | 0 / 0 | vite 8.1.6 (plugin-react deduped), follow-redirects 1.16.1, axios 1.20.0, source-map-js 1.2.2; no invalid, missing or extraneous entries |
| Policy tests (unchanged) | `node scripts/security/audit-gate.test.mjs` | 0 | **103/103 PASS** |
| **Gate (unchanged)** | `node scripts/security/audit-gate.mjs` | **0** | **`TIME_LIMITED_POLICY_PASS`**, `failures: []` |
| Raw full audit | `npm audit --json` | 1 | 5 high (braces, chokidar, fast-glob, micromatch, tailwindcss — the braces root only), 2 moderate (postcss-selector-parser GHSA-rj75-hqrm-r3gf, postcss-nested) |
| Raw runtime audit | `npm audit --omit=dev --json` | 0 | 0 findings |
| Raw text audit | `npm audit --audit-level=high` | 1 | FAIL (braces chain) |
| Contracts | `npm run test:contracts` | 0 | **23/23 PASS** |
| Production build | `npm run build` | 0 | PASS on vite v8.1.6 (same chunk-size warning as before) |
| Whitespace / scope | `git diff --check`; `git diff --name-only 9b7ee308..9e97ccad` | 0 | PASS; only `frontend/package-lock.json` |

Compared with TASK_264, the full audit fell from 7 high / 2 moderate to 5 high / 2 moderate, and the runtime audit from 1 high to 0. The build bundle is essentially unchanged: main JS 2,442.56 → 2,441.63 kB, CSS hash identical, no sourcemaps, no build-only packages in `dist`. Vite reports 817 modules transformed instead of 683; this was observed and not investigated further. follow-redirects does not appear in the browser bundle.

## Limitations

* The waiver still hard-expires at 2026-11-15T00:00:00Z. After that the gate fails until a fixed braces release ships or the Tailwind 4 migration happens. Neither is part of this task.
* `TIME_LIMITED_POLICY_PASS` is a documented, temporary risk acceptance for a trusted CI build. It is not a clean audit and gives no trust to builds on the production-associated Windows host.
* The two moderate postcss-selector-parser/postcss-nested advisories were already present and remain reported, not waived.
* No browser smoke test was run (left for the consolidated RC UAT). Remote CI was not run, and nothing was pushed or deployed.
