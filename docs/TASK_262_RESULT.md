# TASK_262 — Program R3 / Macro B2 backend release-candidate integration

**TASK_262_RESULT: GO_BACKEND_CANDIDATE.** This means backend candidate development is complete. It is
**not** a release or pilot GO. Development and isolated tests only: nothing was pushed, merged, tagged,
migrated or deployed.

The TASK_255 satellite-index persistence fix is now on the CI-hardened TASK_258 base without any
TASK_253 acquisition-aware gating. The writer and its self-contained tests are byte-identical to the
TASK_255 source commit. The one gate-dependent propagation test was rewritten to be gate-free. A
disposable PostgreSQL proves the fail-closed chain end to end: child → multi-index runner → canonical
cycle → healthy retry.

## 1. Identity

| | |
|---|---|
| Repository | `sINte3/AgroSat` (local clone `C:\AgroSat`, used only for Git objects and worktree metadata) |
| Candidate worktree / branch | `C:\AgroSat_worktrees\program_r3_b2_backend_rc`, `task/262-program-r3-b2-backend-rc` (local only, not pushed) |
| Base | `23b50498e254204614334e3a81c8295f45e01e9d` (`task/258-windows-acl-ci-compatibility`, also `origin/task/258-…`) |
| Production / `origin/main` | `3bcbcab3db0386f4013bf54189186e3a4b0f9fc9`; local tracking ref, not refetched |
| TASK_255 source | code `4e449f1df39aa36c5c81f81df3cdbc28a3309bd5`, report `7e20f08139895176f5c8fb748772d822c922f694` (local branch `task/255-…`) |
| TASK_253 (excluded) | `6bf7106556daaa2836d41dbf5d253654efff5ef0`, code `5a690cbcc581b6322a7579118a795ef4c1bae371` |
| Code commit (RESULT_SHA) | `6e6347981b29ecd83bde5aa9c93e7737a02bd9ea`, single parent `23b5049` |
| Report commit | the commit that adds this file, directly after `6e63479` |
| Alembic head | `0016_operational_command_center`, unchanged: no migration |

### Entry checks (all verified before any edit)

- **Pinned objects.** All five pinned commits exist locally.
- **Ancestry.**
  - `merge-base --is-ancestor 3bcbcab 23b5049` → true.
  - `merge-base --is-ancestor 6bf7106 23b5049` → **false**.
  - `merge-base(23b5049, 6bf7106)` = `3bcbcab`.
- **Base contents.** `3bcbcab..23b5049` has exactly 4 commits (TASK_256/257/258) and 7 files:
  - `.github/workflows/ci.yml`, `postgres.yml`;
  - `backend/scripts/manage_user_accounts.py`;
  - `backend/tests/test_task258_credential_acl_probe.py`, `test_worktree_runtime_config.py`;
  - `docs/TASK_258_RESULT.md`;
  - `ops/tests/test_ci_contract.py`.
- **Other checkouts.**
  - `C:\AgroSat` is at `main` = `97f1653` (stale; not used as base). Its tracked state was ` M .claude/settings.json` before and after this task.
  - The `task_253`, `task_255` and `task_258` worktrees were only read.
- **Candidate creation.**
  - The candidate path and branch did not exist beforehand.
  - The candidate was created with `git worktree add -b … 23b5049`.
  - At creation, `git status --porcelain` was empty and HEAD was `23b5049`.

## 2. What was integrated

`git diff 6bf7106..4e449f1` touches 8 files. Each was decided individually:

| TASK_255 file | Decision | Candidate blob (= source blob unless noted) |
|---|---|---|
| `backend/scripts/collect_satellite_indices.py` | carried byte-for-byte (`git checkout 4e449f1 -- path`) | `4b2fc602937671ce1969d9dbdd5a4d60baa4fed2` = TASK_255 writer (audit prefix `4b2fc602`) |
| `backend/tests/test_task255_persistence_outcome.py` | carried unchanged (32 tests) | `fbb67566…` |
| `backend/tests/test_task255_persistence_postgres.py` | carried unchanged (16 tests) | `deb1020e…` |
| `backend/tests/task255_support.py` | carried unchanged | `70734abf…` |
| `backend/tests/task255_child_launcher.py` | carried unchanged | `2a644214…` |
| `backend/tests/test_satellite_write_safety.py` | carried unchanged (asserts `INSERTED`) | `3f56ca2a…` |
| `backend/tests/test_task255_failure_propagation_postgres.py` | **rewritten gate-free** (see §3) | `f412a127…` (source `a776b5af…`) |
| `backend/tests/test_task253_acquisition_gate_postgres.py` | **not imported** (TASK_253 test) | absent |

Why a byte-for-byte carry is valid:
- The writer pre-image is identical in base and source (`00d259a0…` at `3bcbcab`, `23b5049` and `6bf7106`): TASK_253 never touched it.
- The writer and the carried tests import nothing from TASK_253. The only TASK_253 import in TASK_255 (`task253_support`: `Catalog`, `FakeDate`, `feature`) was in the propagation test and is removed.
- The TASK_255 evidence diff (`task255_code_6bf7106..4e449f1.diff`, SHA-256 `9ec4e093…6bd6539`) matches the live `git diff 6bf7106 4e449f1` and its own `SHA256SUMS.txt`. The fix was taken from the original commit, not from the patch.

No production-base API adaptation was needed (action D did not apply). The writer contract is unchanged from TASK_255:
- `PersistenceOutcome.INSERTED` / `ALREADY_EXISTS`;
- `SatelliteIndexWriteError` (= `WRITE_FAILED`), with stage, error class and SQLSTATE only;
- one-transaction `--overwrite`;
- invalidation only after a durable commit;
- JSON `exit_code` = process exit code;
- `db_write_failed` counter and `per_field_results`.

### Scope proof (`git diff --name-status 23b5049 6e63479`)

- **Changed files.** Exactly the 7 files above: `backend/scripts/collect_satellite_indices.py` plus 6 files in `backend/tests/`. This report adds `docs/TASK_262_RESULT.md`.
- **Untouched areas.** No model, Alembic, requirements, `.github`, `ops`, frontend, `backend/services`, `backend/api`, `main.py` or `config.py` file changed. `git diff --check` is clean.
- **No TASK_253 code was added.**
  - The added lines contain no `acquisition_gate`, `task253`/`TASK_253`, `Catalog`, `checkpoint`, `footprint`, `gate_factory`, `NO_NEW_ACQUISITIONS`, `provider_governor` or `strict`.
  - `acquisition_gate.py`, `task253_support.py`, `test_task253_acquisition_gate*.py` and `TASK_253_RESULT.md` are absent.
  - `collect_satellite.py` (`429b5e20`), `provider_governor.py` (`8dd81c90`), `satellite.py` (`ac4bff9a`) and `run_multi_index_collection_cycle.py` (`e7bbdac2`) are identical to the base. Their TASK_253 versions differ (for example `collect_satellite.py` `6c202676`).
  - The only repository-wide TASK_253 mention is the untouched historical line `docs/TASK_258_RESULT.md:30`. It is present in the base too.
- **Ancestry after commit.** `6bf7106`, `5a690cb` and `4e449f1` are **not** ancestors of `6e63479`. The commit has one parent (`23b5049`), with no merge and no cherry-pick of the TASK_255 lineage.

## 3. Gate-free propagation test (action C)

`test_task255_failure_propagation_postgres.py` keeps `BatchPropagationTests` unchanged:
- PostgreSQL rejects field A's NDMI INSERT (READ ONLY, `25006`) on both attempts.
- Child exit 1 with `db_write_failed 1`.
- Runner `failed [A]`, `retry_queue_after [A]`, batch exit 1.
- The healthy retry inserts exactly the missing key.

The TASK_253 class `CanonicalCheckpointPropagationTests` is replaced by `CanonicalCyclePropagationTests`. It runs the **base** canonical collector (`--apply --all-active-fields`, no gate flag, no checkpoint file) through the real runner and real child processes. A local `FakeDate` replaces the `task253_support` import.

| Step | Proven (captured artifacts, real PostgreSQL) |
|---|---|
| 1. A's NDMI write fails | A: two child attempts, each exit 1, JSON `exit_code 1, errors 1, db_write_failed 1, failure_count 1`, `write_failures=[ndmi, insert, 25006]`. B: exit 0, 4 inserted. Batch: exit 1, `failed [A]`, `successful [B]`, `retry_queue_after [A]`, 7 inserted / 3 skipped. Canonical: exit 1, `run_status PARTIAL`, `stages.multi` completed 1 / failed 1 / unfinished `[A]`. DB run `degraded/degraded/operational`. `collector_latest_status` `failed`, and no `collector_last_success.json` was written. **No falsely fresh date:** A has no NDMI row at all. |
| 2. Healthy retry | Canonical exit 0, `SUCCESS`, DB run `succeeded/healthy`. A is retried first; 1 inserted, 7 skipped. `(A, day, ndmi)` count = **1**. **Positive control 1:** all 7 pre-existing rows keep the same `id` and `xmin`, so a true existing key stays a skip. |
| 3. New acquisition | **Positive control 2:** a new day commits 8 keys (2 fields × 4 indices), 8 skipped, 16 rows in total, and the earlier rows are untouched. No duplicates. Every child connected to `agrosat_h0a_task262`. |

**Negative control.** The base writer (blob `00d259a0`) was temporarily swapped into the worktree and the file was run:
- Both tests **fail** at their first assertion: the runner exits 0, and the canonical exits 0 with no diagnostics. That is the original false-success defect.
- The writer was then restored from the index and verified as `4b2fc602` again.

## 4. Fail-closed checklist (§4 of the task)

| Requirement | Evidence |
|---|---|
| Insert → real COMMIT → `INSERTED`, one row, duplicate → existing skip | PG `test_a_committed_insert_is_inserted_and_the_same_key_then_skips`, `…confirmed_duplicate…without_any_mutation` (same id, xmin and sequence) |
| execute / commit / FK / read-only / connectivity failure ≠ existing / success | PG: read-only `25006`, terminated backend before COMMIT, FK `23503`, NOT NULL `23502`, PK `23505`. Offline: connection `08006`, pre-check failure, raised unique violation |
| Ambiguous commit fails closed | PG `test_an_ambiguous_commit_fails_closed…`: `commit_outcome_unknown`, never `INSERTED`; the next run reads the key |
| `--overwrite` atomic | PG: failed replacement INSERT, COMMIT or DELETE keeps the original row; success is one swap |
| Cache failure after commit keeps the outcome | offline + PG `…cache_outage_after_commit…` |
| JSON / exit / counters / retry / canonical consistent | offline `RunRealAccountingTests` (11) + §3 artifacts |
| One failed index ⇒ never `SUCCESS` or a fresh date; retry exactly once | §3 steps 1–2; PG `MixedFieldPostgresTests` |
| No-scene, quality-rejected, invalid geometry and dry-run stay distinct | offline `test_no_scene…`, `test_a_quality_blocked…`, `test_dry_run_never_writes`, `test_apply_mode_never_writes`; base suites `test_satellite_write_safety`, `test_multi_index_collection_cycle` |
| No SQL values, credentials or URLs in logs or JSON | offline sanitization asserts. E2E capture: 36 artifact files scanned for the run's exact SECRET_KEY, DB password and URL → 0 hits |

## 5. Tests

All runs used the venv interpreter `C:\AgroSat\backend\venv\Scripts\python.exe` (Python 3.14.5, pytest 8.4.2, SQLAlchemy 2.0.50), read-only, with cwd at the candidate `backend`.

The environment was constructed for each run:
- Inherited `DATABASE_URL`, `AGROSAT_*`, `SENTINEL_*`, `SUPABASE*`, `PG*`, `REDIS*` and similar variables were dropped. The only one present was a machine-scope `SECRET_KEY`.
- That inherited `SECRET_KEY` was dropped **unread** and replaced by a per-run `token_urlsafe(48)`: the CI job-scoped pattern.
- `PYTHONUTF8=1` and `PYTHONDONTWRITEBYTECODE=1` were set.
- The candidate has no `backend/.env`.

### Lane U (offline, no database target)

| Run | Command | Result |
|---|---|---|
| U-1 TASK_255 contract | `python -m pytest -q -p no:cacheprovider -rs tests/test_task255_persistence_outcome.py` | **32 passed** |
| U-2 collector regression (16 files: write safety, multi-index runner, canonical collector, TASK_209/223/228/229/251/251A, NDVI runner and collector, quality scope, web startup safety) | `python -m pytest -q -p no:cacheprovider -rs <files>` | **564 passed** |
| U-3 full backend suite (= CI "Backend suite") | `python -m pytest tests -q -p no:cacheprovider -rs` | **1714 passed, 192 skipped, 0 failed**; all skips "AGROSAT_TEST_DATABASE_URL is not set" |
| Final, on committed `6e63479`, clean tree | same | **1713 passed, 193 skipped, 0 failed**. The one extra skip is `test_task218_release_manifest`: "qualified once this commit is published beyond origin/main". That is environmental: the commit is local only, as in TASK_255 §5.1. |
| Ops suite (CI `ops` job; no ops file changed) | `python -m pytest ../ops/tests -q -p no:cacheprovider` | **118 passed** |

### Lane PG (disposable PostgreSQL)

**Cluster.**
- A new cluster was created by this task with `initdb` (PostgreSQL 16.14) in an operator-private directory.
- It listened on `127.0.0.1:55462` only, with SCRAM authentication and superuser `task262_owner`.
- A throwaway password was generated into a private file and never printed.
- `system_identifier 7696794618896023052`.
- **Database.** `agrosat_h0a_task262` was created with PostGIS 3.6.2 (`CREATE DATABASE` refuses reuse) and migrated with `python -m alembic upgrade head` to `0016_operational_command_center` (single head).
- **Not production.** Production PostgreSQL is the `postgresql-x64-16` service, PID 4188 on port 5432. It was never connected to.

**Checks before any mutation** (read-only transaction):
- `current_database()` = `agrosat_h0a_task262`, port 55462.
- The cluster has no database named `agrosat`.
- `satellite_index_records` has the non-deferrable `uq_satellite_index_records_field_date_code UNIQUE (field_id, captured_date, index_code)`, no CHECK constraint and no trigger, and 0 rows.

That **verifies on real PostgreSQL** the migration-`0002` invariant the writer's `ON CONFLICT` relies on. The guards also refuse non-isolated targets: `Task225Base`, the suite's `current_database()` check, and the child launcher's name and port check.

| Run | Result |
|---|---|
| PG-1 `test_task255_persistence_postgres.py` | **16 passed** |
| PG-1 `test_task255_failure_propagation_postgres.py` (batch + gate-free canonical) | **2 passed** |
| Negative control (base writer `00d259a0`) | 2 failed, as required |
| PG-2 all `tests/*postgres*.py`, one process per file (= `postgres.yml`) | **15 files, 193 passed, 0 failed, 0 skipped** |
| E2E artifact capture re-run (evidence-only plugin loaded from outside the candidate) | 2 passed; secret scan 0 hits |
| Final on committed `6e63479`: TASK_255 ×2, TASK_251, TASK_229, TASK_223 PG files | **16 + 2 + 2 + 4 + 8 = 32 passed** |

The post-test metadata again showed 0 rows, the same identity and the same unique key. The server log contains only the injected errors:
- READ ONLY INSERT/DELETE;
- FK, NOT NULL and PK violations.

The cluster was stopped at the end. Its data directory is kept, operator-private, outside the evidence ZIP.

### CI qualification (evidence only)

- **Workflows.** Unchanged; no remote CI run exists for this unpublished commit, and none is claimed.
- **Local equivalents passed:**
  - Backend suite;
  - PostgreSQL per-file lane;
  - ops suite;
  - Guards' pytest steps: `test_web_startup_safety.py` (no APScheduler) and the retired-endpoint files, both inside the backend suite;
  - single Alembic head on the isolated DB.
- **Not run.** The Guards step `Invoke-AgroSatControlPlane.py rollback-contract` was not run: this task does not run the release control plane. No migration file changed.
- **Frontend.** `npm audit` is the known separate blocker DI-3. It was not run, and no frontend file changed.

## 6. Production non-mutation

| Check | Result |
|---|---|
| Production database | never connected; 0 DML/DDL. Every connection string was constructed for `127.0.0.1:55462/agrosat_h0a_task262` |
| Provider / external endpoints | 0 calls. Fixture service only; children block non-loopback sockets; no provider credential existed in any test environment |
| `.env` / secrets | no production `.env` or secret file opened; the inherited `SECRET_KEY` was dropped unread |
| Services / Scheduled Tasks | none created, changed, started or stopped. Final read-only snapshot: `postgresql-x64-16` Running (PID 4188); `\AgroSat_PROGRAM_R3_DatabaseBackup` Ready |
| `C:\AgroSat` tracked files, active release, runtime pointer | untouched. Only `.git` worktree metadata, the new branch ref and new objects were added (authorized) |
| ACLs, CI workflows, schema, migrations | unchanged |
| Git | no push, merge, tag or remote branch; two local commits on `task/262-…` |

## 7. Residual risks and observations

1. **Production uniqueness is evidenced, not re-verified here.**
   - It was verified on the isolated DB at head. For production it rests on the TASK_248 read-only preflight (via TASK_255).
   - If the key were ever absent, every write fails loudly with `42P10` (`WRITE_FAILED`): fail-closed, never a duplicate.
2. **Ambiguous COMMIT.** It is reported as `WRITE_FAILED` with `commit_outcome=unknown`, even if the server applied it. The retry reads the key and reports `ALREADY_EXISTS`. This is by design.
3. **Failure category heuristics (pre-existing, unchanged).**
   - The runner's text heuristic can label a failed-write attempt `cloud`, and the canonical text classifier can do the same with real stdout.
   - The run is still non-success. In the test the canonical category is `operational`.
   - A structured category belongs to a separate task.
4. **Database outage outside the writer.** It still crashes the child before its JSON is written. The runner marks it `FATAL_LOG_CONTRACT_ERROR`, which fails closed. This is pre-existing.
5. **Unpublished commit.**
   - There is no remote CI.
   - `test_task218_release_manifest` skips until the commit is published.
   - The Guards control-plane step was not run locally.
6. **Test platform.** Tests ran on PostgreSQL 16.14 + PostGIS 3.6.2 on Windows. CI uses `postgis/postgis:16-3.4` on Linux, and portability there is not proven by this run.
7. **Host observations for the B1 host-security lane (not acted on).**
   - A machine-scope `SECRET_KEY` environment variable is visible to every process on the host.
   - `%LOCALAPPDATA%\AgroSat` grants inherited read access to `SRV-YOQSH\CodexSandboxUsers`. That covers the evidence directory and the stopped disposable cluster's throwaway password. ACLs were not changed (out of scope).
   - The disposable cluster directory can be deleted after review.
8. **Not pilot-ready.** This candidate still needs independent B1 host-security closure, frontend security qualification (DI-3) and UAT.

## 8. Machine-readable verdict

```text
TASK_262_RESULT=GO_BACKEND_CANDIDATE
BASE_SHA=23b50498e254204614334e3a81c8295f45e01e9d
RESULT_SHA=6e6347981b29ecd83bde5aa9c93e7737a02bd9ea
TASK_253_IMPORTED=NO
WRITE_FAILURE_REPORTED_AS_SUCCESS=NO
GATE_FREE_PROPAGATION=PASS
POSTGRES_TESTS=PASS
BACKEND_REGRESSION=PASS
PRODUCTION_MUTATIONS=0
GIT_PUSH=NO
DEPLOYED=NO
```
