# TASK_266 — Program R3 / Macro B2 integrated release candidate (local)

**TASK_266_RESULT: HOLD_RC_INTEGRATION.**

The integration itself is clean: both pinned component heads merged without conflict, the merged scope is exactly the 14
prequalified files, every blob is byte-identical to its component head, and TASK_253 is absent. The focused backend
tests, the frontend contracts and build, the unchanged audit gate and the runtime audit all pass.

**Why HOLD.** One existing CI contract test fails on the integrated tree:
`ops/tests/test_ci_contract.py::test_fast_lane_covers_the_required_checks` (line 63,
`assert "npm audit --audit-level=high" in run`).
- It was introduced by TASK_264 commit `7e02dbe` on the frontend candidate, not by this merge.
- That commit replaced the CI step `npm audit --audit-level=high` with the audit gate, but did not update this test.
- The test is unchanged since the base (blob `60735ac1`).
- The CI `ops` job runs `python -m pytest ops/tests -q`, so remote CI is expected to fail on this branch as it stands.
- Fixing it means editing a test or CI file. This task forbids that, so nothing was changed.

This is local integration evidence only. Nothing was pushed, tagged, merged to main, migrated, released or deployed.

## 1. Identity

| | |
|---|---|
| Integration worktree / branch | `C:\AgroSat_worktrees\program_r3_b2_integrated_rc`, `task/266-program-r3-b2-integrated-rc` (local only) |
| Common base | `23b50498e254204614334e3a81c8295f45e01e9d` (TASK_258) |
| Backend candidate | `06c065090bad5d36a88475405169189e9dd60b1b` (`task/262-program-r3-b2-backend-rc`; code `6e634798`) |
| Frontend candidate | `ab0a010f2ead44a2aff1f211d2d26c5caa9a2e44` (`task/263-program-r3-b2-frontend-security`; TASK_264 `27fd30e1`, `7e02dbeb`; TASK_265 `9e97ccad`) |
| Merge 1 (backend) | `f518dd0363c9bb37b6d9abd694b58ee6da950aab`, parents `23b50498`, `06c06509` |
| Merge 2 (frontend), qualified tree | `0bb57cfa6151ac9750da52f0453a2ccc83daaf49`, parents `f518dd03`, `ab0a010f` |
| Report commit | the commit that adds this file, directly on `0bb57cfa`. Its SHA is in the evidence `README_RU.md` |
| Excluded TASK_253 | `6bf7106556daaa2836d41dbf5d253654efff5ef0` (code `5a690cbc`); TASK_255 source `4e449f1d` is also not an ancestor |
| Production baseline | `R20261009-task252`, `3bcbcab3db0386f4013bf54189186e3a4b0f9fc9`. It is an ancestor of the base. Production was not re-audited |
| Alembic | single head `0016_operational_command_center` (17 revisions, base `0001_baseline_existing_schema`); no migration |

**Local main divergence.** This is recorded only, nothing was acted on.
- `C:\AgroSat` is on `main` = `97f165334e8275cb67373c059598a5bec5adb08a`.
- Its cached `origin/main` is `3bcbcab3`.
- `97f1653` is not an ancestor of the base: the base has 61 commits that `97f1653` lacks, and `97f1653` has 0 commits
  that are not in the base.
- TASK_253 is not an ancestor of `97f1653`.
- Its tracked state was ` M .claude/settings.json` before and after this task (SHA-256 `9196a56f…`, unchanged).
- Local main HEAD was ignored when assembling the candidate.

## 2. Preflight (read-only, `git --no-optional-locks`)

- **Objects.** `git cat-file -t` returns `commit` for the base, backend and frontend SHAs. The same holds for
  `6e634798`, `9e97ccad`, `6bf71065`, `3bcbcab3` and `97f16533`.
- **Worktrees.** Both component worktrees have the expected branch and HEAD, and
  `status --porcelain --untracked-files=all` is empty for both. They share the common Git dir `C:/AgroSat/.git`.
- **Merge base.** `merge-base(06c06509, ab0a010f)` = `23b50498`, exactly.
- **TASK_253.** `6bf71065` is not an ancestor of the backend head, the frontend head or the base.
- **Overlap.** The component change sets share no path.
- **Destination.** The destination path and the branch did not exist beforehand.
- **Git settings.** No active hooks. No `.gitattributes`, so the LFS filter is inert. `core.autocrlf=true`.
- **Operator.** The operator was non-elevated (`IsInRole(Administrator)=False`).

## 3. Integration transaction (one attempt)

1. `git worktree add -b task/266-program-r3-b2-integrated-rc <dest> 23b50498…`. Afterwards HEAD = base and the
   worktree was clean.
2. Merge the backend head: `git merge --no-ff --no-edit --no-verify --no-gpg-sign 06c06509…`. The `recursive` strategy
   ran with no conflict: 8 files.
3. Merge the frontend head with the same flags: `ab0a010f…`. No conflict: 6 files.

Both merges ran with `core.hooksPath` set to an empty private directory and `commit.gpgsign=false`. No release tool was
invoked and no source file was edited.

## 4. Scope proof (`23b50498..0bb57cfa`)

**Ancestry.**
- Ancestors of `0bb57cfa`: `23b50498`, `06c06509`, `6e634798`, `ab0a010f`, `9e97ccad`, `7e02dbeb`, `27fd30e1`.
- Not ancestors: `6bf71065`, `5a690cbc`, `4e449f1d`.
- `base..HEAD` has 9 commits: 2 backend + 5 frontend + 2 merges. No non-merge commit comes from anywhere else.

**Changed files.** Exactly 14, equal to the union of the component scopes:
- `git diff 06c06509 0bb57cfa` lists exactly the frontend scope.
- `git diff ab0a010f 0bb57cfa` lists exactly the backend scope.

| Path | Blob (integrated = component head) | Origin |
|---|---|---|
| `backend/scripts/collect_satellite_indices.py` | `4b2fc602937671ce1969d9dbdd5a4d60baa4fed2` | backend |
| `backend/tests/task255_child_launcher.py` | `2a6442143e6131140a94b91f9a8975aba410289b` | backend |
| `backend/tests/task255_support.py` | `70734abf818830b415b4043415ddf40b316eb15b` | backend |
| `backend/tests/test_satellite_write_safety.py` | `3f56ca2ac2e75e52309ca2ed7744d0484d5d291a` | backend |
| `backend/tests/test_task255_failure_propagation_postgres.py` | `f412a1278065583920ee4dedaf4421ec5f983f48` | backend |
| `backend/tests/test_task255_persistence_outcome.py` | `fbb6756681cc0f2f23996a5ca64bc5ecf40aeed5` | backend |
| `backend/tests/test_task255_persistence_postgres.py` | `deb1020e5e14cdce8ca5f0ef0cd0bb69c7586fe1` | backend |
| `docs/TASK_262_RESULT.md` | `3ce2af2dabc058a9e1a5f7e2595a4c6b7fa46a2e` | backend |
| `.github/workflows/ci.yml` | `6794849d567744cc89f09dd8108c538cf0fb16cb` | frontend |
| `docs/TASK_264_RESULT.md` | `be0ab9c287ec048a9fca7bdb52f33b95b111b7f5` | frontend |
| `docs/TASK_265_RESULT.md` | `ca447922032130f5881cfa21fa69251f8dde1b8f` | frontend |
| `frontend/package-lock.json` | `7cb2d90b279a94c7fa0efb14ef147f454df209de` | frontend |
| `frontend/scripts/security/audit-gate.mjs` | `dbcd933bb459e213f3a22ec83b1c4f435147dc44` | frontend |
| `frontend/scripts/security/audit-gate.test.mjs` | `3c34fa3106876cb356b5098f153d21d89f717fe2` | frontend |

**What did not change.**
- No path matches models, Alembic, migrations, requirements, `package.json`, README, `main.py`, `config.py`,
  `backend/services` or `backend/api`.
- `frontend/package.json` keeps the base blob `43b1e3c4`.

**TASK_253 search.**
- Added non-doc lines contain no `acquisition_gate`, `task253`, `TASK_253`, `gate_factory`, `NO_NEW_ACQUISITIONS` or
  `CanonicalCheckpoint`: 0 hits.
- `acquisition_gate.py`, `task253_support.py`, `test_task253_acquisition_gate_postgres.py` and `TASK_253_RESULT.md`
  are absent.
- The tree mentions TASK_253 only in result documents.

**Whitespace.** `git diff --check 23b50498 0bb57cfa` exits 0.

## 5. Qualification (one pass, one environment-only correction)

**Toolchain.**
- Backend: the venv interpreter `C:\AgroSat\backend\venv\Scripts\python.exe` (Python 3.14.5, pytest 8.4.2), read-only,
  with cwd in the new worktree.
- Frontend: Node 24.16.0 with its bundled npm 11.13.0 (`node npm-cli.js`), the toolchain of TASK_264/265. The npm
  11.16.0 on PATH was not used.

**Environment.** Every child environment was built from an allow-list.
- **Dropped unread:** the inherited machine-scope `SECRET_KEY`, `DRONE_API_TOKEN`, `FUEL_API_TOKEN`,
  `CLAUDE_CODE_MESSAGING_TOKEN` and `GIT_ASKPASS`. No `DATABASE_URL`, `PG*`, `AGROSAT*`, `SENTINEL*` or `SUPABASE*`
  variable reached any child.
- **Backend:** a per-run synthetic `SECRET_KEY` (`token_urlsafe(48)`), with `PYTHONUTF8=1` and
  `PYTHONDONTWRITEBYTECODE=1`. The key occurs 0 times in any log.
- **npm:** a task-private cache, an empty `userconfig` and task-private `APPDATA`/`LOCALAPPDATA`. No user `.npmrc`
  exists.

**Environment correction (the only one).** The first backend pass gave `B02` 283 failed / 290 passed.
- **Cause:** `FileNotFoundError` (WinError 3 / Errno 2). The runner's private `TEMP` was 176 characters long, which
  pushed pytest `tmp_path` files past Windows MAX_PATH.
- **Correction:** the lane was re-run with a 39-character private `TEMP` (removed afterwards). Commands, files and the
  worktree were the same.
- Both passes are kept in the evidence.

### Backend (second pass, after the correction)

| Step | Files | Result |
|---|---|---|
| B01 TASK_255 writer contract | `tests/test_task255_persistence_outcome.py` | **32 passed** |
| B02 collector regression | 16 files (see below) | **573 passed** |
| B03 CI guard: no APScheduler / web-embedded scheduler | `tests/test_web_startup_safety.py` | **6 passed** |
| B04 CI guard: retired mutation endpoints | `tests/test_field_inspections.py`, `tests/test_satellite_write_safety.py`, `tests/test_task209_operational_closure_backend.py`, `tests/test_task225_contracts.py` | **137 passed** |
| B05 TASK_255 PostgreSQL files | `tests/test_task255_persistence_postgres.py`, `tests/test_task255_failure_propagation_postgres.py` | **18 skipped** ("AGROSAT_TEST_DATABASE_URL is not set"): **PG_NOT_RERUN** |
| B06 schema/migration contract (offline) | `tests/test_migration_schema_contract.py` | **19 passed** |
| B07 Alembic graph (`ScriptDirectory`; no DB, no `env.py`) | — | **1 head** `0016_operational_command_center` |
| B08 ops CI workflow contract | `ops/tests/test_ci_contract.py` | **1 failed, 4 passed**: see "Why HOLD" |

B02 runs these files:
- `test_satellite_write_safety`, `test_multi_index_collection_cycle`, `test_task209_canonical_collector`;
- `test_task223_collection_run_finish`, `test_task223_failure_category_vocabulary`, `test_task228_collector_status_contract`;
- `test_task229_atomic_publication`, `test_task229_collector_finalization`, `test_task229_collector_metrics`, `test_task229_compact_status`;
- `test_task251_collector_429_resilience`, `test_task251a_timeout_and_evidence`;
- `test_ndvi_collection_cycle`, `test_collect_ndvi`, `test_satellite_data_quality_tenant_scope`, `test_web_startup_safety`.

TASK_262 reported 564 for its own 16-file U-2 selection. Identity of the two selections is not claimed.

**B08 reproduction.** `git archive` exports of `ops` and `.github` were tested outside any worktree:
- `23b50498`: 5 passed;
- `06c06509`: 5 passed;
- `ab0a010f`: 1 failed;
- `0bb57cfa`: 1 failed.

The substring now appears only in a YAML comment of `ci.yml`, and the test checks `run` commands.

**Not rerun here (component evidence only).** These are TASK_262 results and were not repeated:
- the full suite: 1713 passed, 193 skipped;
- the disposable PostgreSQL lane: 193 passed;
- the ops suite: 118 passed, at the base `ci.yml`.

No PostgreSQL was connected to, and SQLite was not substituted.

### Frontend (`frontend/`, the new worktree)

| Step | Command | Exit | Result |
|---|---|---|---|
| F01 | `npm ci --no-fund --no-audit` | 0 | 233 packages |
| F02/F03 | `npm ls … --all`; `npm ls --all --json` | 0 / 0 | follow-redirects **1.16.1**, vite **8.1.6**, axios **1.20.0**, rolldown 1.1.5, source-map-js 1.2.2, tailwindcss 3.4.19, braces 3.0.3; **0** invalid / missing / extraneous |
| F04 | `node scripts/security/audit-gate.test.mjs` (unchanged) | 0 | **103/103 PASS** |
| F05 | `node scripts/security/audit-gate.mjs` (unchanged) | 0 | **`AUDIT_POLICY_RESULT=TIME_LIMITED_POLICY_PASS`**, `failures: []`, evaluated 2026-10-22T05:26:16Z |
| F06 | `npm audit --omit=dev --json` | 0 | **0** findings (265 audited) |
| F07 | `npm audit --json` | 1 | 5 high / 2 moderate / 0 critical (see below) |
| F08 | `npm audit --audit-level=high` | 1 | raw text audit **FAIL**. It is not clean, and it is not reported as clean |
| F09 | `npm run test:contracts` | 0 | **23/23 PASS** |
| F10 | `npm run build` | 0 | PASS on vite v8.1.6, 817 modules, the existing chunk-size warning |

F07 breaks down as follows:
- The only HIGH root is braces GHSA-vfj7-8cjw-p6xm. chokidar, fast-glob, micromatch and tailwindcss are HIGH only
  through it.
- The moderates are postcss-selector-parser GHSA-rj75-hqrm-r3gf and postcss-nested.

After the run, the lockfile and `package.json` worktree hashes equal their HEAD blobs. The worktree status is empty;
only the ignored `frontend/node_modules/` and `frontend/dist/` exist.

### Security decision

- **Runtime.** HIGH/CRITICAL in the runtime audit: **0**.
- **Waiver.** The full audit is non-zero only under the signed-off, time-limited GHSA-vfj7-8cjw-p6xm waiver:
  - scope: braces in the Tailwind 3 build-only graph;
  - granted 2026-10-21T00:00:00Z;
  - last valid second 2026-11-14T23:59:59Z;
  - hard expiry **2026-11-15T00:00:00Z**, strict `<`.
- **Fail-closed coverage.** The unchanged policy tests show that the gate fails closed in each of these cases:
  - at and after expiry: N01, N02;
  - on any other HIGH/CRITICAL: N11, N15, N17 and others;
  - on malformed JSON: N35, N42;
  - when npm or the registry is unavailable: N30, N34, N63, N67, X02.
- **Moderates.** The two moderates remain reported as `::warning::` and are not waived.
- **Unchanged files.** Policy, tests, `ci.yml` and the lockfile were not modified.

### Ops invariants (read-only, offline)

- **Scheduler.** No APScheduler in FastAPI startup (B03). No `apscheduler` in `backend/requirements*.txt`, and no
  runtime import outside one string literal in `backend/scripts/write_multi_index_batch.py` (a forbidden-word list).
- **Alembic.** Single head (B07). The schema/migration contract passes (B06).
- **Git scope.** See §4.
- **Not run.** The CI Guards step `Invoke-AgroSatControlPlane.py rollback-contract` was **not run**: this task does
  not invoke the release control plane.

## 6. Production non-mutation

- **Writes.** Only these were written:
  - the new worktree;
  - the new local branch ref and objects (two merge commits plus this report commit) in `C:\AgroSat\.git`;
  - the new evidence folder;
  - session-private scratch directories.
- **Untouched.** `C:\AgroSat` tracked files and both component worktrees, with the same HEAD and status before and
  after.
- **No connections.** No database, provider, Sentinel/CDSE, `.env`, backup or credential was opened or connected to.
  Pre-existing listeners on `:8000`, `:5173` and `:5432` (created 2026-10-18) were not contacted.
- **Services.** `postgresql-x64-16` was Running, PID 3428, before and after. TASK_262 recorded PID 4188, so a restart
  happened between 2026-10-15 and 2026-10-18, before this task. `\AgroSat_PROGRAM_R3_DatabaseBackup` was Ready.
  Nothing was started, stopped or changed.
- **Git.** No push, PR, tag, merge to main or remote ref. No ACL change.

## 7. Limitations and future gates

- Remote Windows GitHub Actions has not run for this branch. It is expected to fail in the `ops` job until the
  CI-contract mismatch is resolved under a separately authorized task.
- PostgreSQL TASK_255 lanes were not rerun here (`PG_NOT_RERUN`).
- B1 host ACL and secret exposure remains HOLD. Independent live-data/NDVI verification, backup restore qualification,
  browser/mobile/offline UAT and operator sign-off are missing.
- The braces waiver hard-expires 2026-11-15T00:00:00Z.
- **DEPLOY=NO; PILOT=HOLD.**

## 8. Machine-readable verdict

```text
TASK_266_RESULT=HOLD_RC_INTEGRATION
BASE_SHA=23b50498e254204614334e3a81c8295f45e01e9d
BACKEND_SHA=06c065090bad5d36a88475405169189e9dd60b1b
FRONTEND_SHA=ab0a010f2ead44a2aff1f211d2d26c5caa9a2e44
QUALIFIED_MERGE_SHA=0bb57cfa6151ac9750da52f0453a2ccc83daaf49
TASK_253_IMPORTED=NO
BACKEND_FOCUSED_TESTS=PASS
OPS_CI_CONTRACT=FAIL
FRONTEND_CONTRACTS=PASS
FRONTEND_BUILD=PASS
TEMPORARY_CI_POLICY=TIME_LIMITED_POLICY_PASS
RUNTIME_HIGH_CRITICAL=0
PRODUCTION_MUTATIONS=0
GIT_PUSH=NO
REMOTE_CI=NOT_RUN
DEPLOYED=NO
PILOT=HOLD
```
