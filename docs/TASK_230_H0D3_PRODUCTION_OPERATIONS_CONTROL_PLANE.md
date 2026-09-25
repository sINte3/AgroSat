# TASK_230 — H0-D3 production operations control plane

Branch `task/230-h0d3-production-operations-control-plane`, based on exactly
`448f40407ffcdbf635a5cc7816fcab484dd80a5b` (TASK_229, on TASK_228). Development
and rehearsal only: no production service, Scheduled Task, release, database or
backup schedule was changed; `OBSERVATION_DETECTION_ENABLED=false` untouched.

Raw evidence: `C:\AgroSat_backups\TASK_230_H0D3_OPS_CONTROL_PLANE_EVIDENCE_20260925\`.
The production deployment plan for the next task is
`docs/TASK_231_PRODUCTION_DEPLOYMENT_PLAN.md`.

## 1. Process-tree ownership (Parts A, B)

**Defect, reproduced.** Production's application tasks run
`venv\Scripts\python.exe` (the venv launcher), which runs the supervisor in a
job that lets grandchildren break away silently; the supervisor started
uvicorn/node with `CREATE_NO_WINDOW` (their own console). Stopping the task
ends the launcher and the supervisor only. Measured with the 448f404 launcher
(constants pointed at spare ports) under temporary `\AgroSat_TASK230_*` SYSTEM
tasks: uvicorn, its descendant, node and its descendant all survived the stop
and kept their ports.

**Fix.** `Run-AgroSatApplication.py` joins a Windows Job Object limited with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and no breakaway
(`agrosat_process_ownership.py`) before it starts any child. Its only handle
is never closed explicitly, so the end of the supervisor, for any reason,
terminates every member. The schema 2 configuration binds the helper by
SHA-256; a rehearsal profile may use its own roots and spare ports and is
refused if any value is a production one.

**Qualified on this host** (`ops/qualification/Run-Task230ProcessOwnershipQualification.py`,
final run `evidence/ownership_final`, 11/11 scenarios as expected):

| scenario | result |
|---|---|
| backend, TASK_230 launcher | whole tree gone 0.905 s after `Stop-ScheduledTask`, port free, listener PID was a job member |
| backend with descendant listener | same (0.917 s), both ports free |
| frontend (node) with node descendant | same (0.904 s) |
| backend / frontend, 448f404 launcher | orphans survived with ports held (defect reproduced); they expired on their own probe timer after about 75 s, nothing was killed |
| second launcher, same component | `APPLICATION_OWNERSHIP_LOCK_HELD` (exit 70); IgnoreNew kept one task instance; listener owner unchanged; tree gone 0.937 s after the stop |
| invalid release identity | exit 70 `APPLICATION_MANIFEST_HASH_REJECTED`, no child, no listener |
| child exits unexpectedly | supervisor exits with the child's code 3; Task Scheduler did not restart it within 210 s although RestartCount=2 (RestartOnFailure ignores non-zero exit codes); bounded at one start |

No `taskkill`, `Stop-Process` or name-based search was used anywhere.

## 2. Worker process ownership (Part C)

Measured, not assumed: with the unchanged runners the whole worker tree
(runner `powershell.exe`, collector or reconciler Python, provider child
Python) ended within 0.91 s (collector) and 0.93 s (notifications) of the
stop. The Task Scheduler job contains only `powershell.exe`; the Python
processes break away from it, but they all share the task's console, which
the stop tears down. A diagnostic whose provider
child had its own console left exactly that child running. The canonical
workers never create one, so the runners are unchanged.

## 3. Release identity, authorization, state machine (Parts D-H)

See `ops/release/README.md`. In short:

* **Manifest (schema 2).** Exact candidate and current SHAs, fetched
  `origin/*` refs containing the candidate, ancestry, clean material verified
  against Git, archive SHA-256, the candidate's own Alembic head, the runtime
  contract. No branch, task or worktree names (the old
  `New-ReleaseManifest.ps1` hardcoded five macrostage worktrees and baselines).
* **Authorization.** One artifact outside every source tree, bound to
  operation, release id, both SHAs, database, migration permission and
  rollback strategy; at most 24 h; replay refused; its SHA-256 typed on the
  command line in production; validated before anything is locked or created.
* **Gates.** PRECHECK .. COMMIT with immutable evidence per attempt in one
  directory per release id; the identity hash is bound at creation; a
  contradictory re-run is refused; a terminal id never runs again; each gate
  re-inspects the live system on resume (a switch already done is recorded as
  `already_switched`, never restarted). Building the real-host resume test
  exposed one defect, fixed in `89534d5`: a controller ended between writing a
  gate's attempt record and saving the state could not record that gate again
  (`IMMUTABLE_RECORD_CONFLICT`, which after the switch would have forced a
  rollback); the orphan record is now kept and the resume records the next
  attempt.
* **Health.** Candidates: `/health/live` and `/health/ready` 200,
  `release_revision` = candidate, database `ready`, `revision_match` true,
  both migration revisions equal to the candidate head; collector and cache
  recorded, never blocking. The previous release or a rollback target may
  predate TASK_228 (production 4cd8ea7 does): the absence of `revision_match`
  and `expected_migration_revision` is accepted for it, never for a
  candidate, and its `migration_revision` must still equal the head derived
  from its own migration graph.

## 4. Migrations and rollback (Parts I, J, K)

The plan reads the database revision read-only and the candidate graph through
Alembic's `ScriptDirectory`: equal is noop; behind needs the exact authorized
upgrade; ahead, foreign, missing, multiple revisions or several candidate heads
block. `ops/release/rollback-contract.json` classifies all 17 shipped
migrations (audited from every `upgrade()`/`downgrade()`):

| class | revisions |
|---|---|
| reversible_without_data_loss | 0003, 0004 |
| destructive_after_data | 0002, 478de3d1f6d0, 0005-0012, 0014, 0015, 0016 |
| restore_backup_or_roll_forward_only | 0001 (baseline), 0013 (lossy downgrade, deletes audit rows) |

Audit notes: 0006's downgrade narrows `alembic_version.version_num` to 32
characters, so any downgrade below 0003 fails and rolls back; the guards of
0008-0012 run online only; 0013's downgrade is lossy even without new data.

Rollback: case 1 (no migration) rebinds to the recorded previous actions;
case 2 (every step reversible and authorized) downgrades; case 3 forbids
automatic downgrade and, when authorized, restores the validated pre-release
backup into a new database and swaps it in by renaming, keeping the live
database under an aside name; otherwise MANUAL_RECOVERY_REQUIRED.

## 5. Schedule contracts (Parts L, M)

The canonical Sentinel identity requires exactly one daily trigger at
06:00:00, SYSTEM, IgnoreNew, StartWhenAvailable, 6 h, 3 restarts every 15
minutes; the historical `["06:00:00", "18:00:00"]` template, zero triggers or
any other time fail closed in the validator and the installer (regression
tests in `ops/tests/test_windows_task_contracts.py`). The notification task is
pinned to 15 minutes, IgnoreNew, SYSTEM, 10 minutes, 3 restarts every 5
minutes. A release changes only actions and proves triggers, settings and
principal unchanged. The live production tasks meet these contracts (read-only
check).

## 6. Database backup contract (Parts N-R)

See `ops/database/README.md`. Qualified against isolated databases: backup
validated from the dump itself (restore list, table data, revision and row
counts from COPY data, schema fingerprint, SHA-256), secondary copy verified
by SHA-256 into a second local root (not off-host), restore rehearsal into a
new `agrosat_task230_*` database passing all checks, retention preview/apply,
scheduled task installed disabled from an explicit policy, run once, removed.

Two real findings fixed during rehearsal: PostgreSQL re-deparses CHECK and
partial-index predicates after a restore (the schema fingerprint now compares
their names and shape, not expression text), and PostGIS repopulates
`spatial_ref_sys` on restore (extension tables are excluded from row counts).

## 7. Stale release tests (Part T)

The 14 known failures (`test_task212_release_runtime` x12,
`test_task209_release_rollback_artifacts` x2) came from rehearsal roots and
manifests hardcoded to historical macrostages. The tests now drive the new
contract with equivalent or stronger assertions.

## 8. CI (Parts U, V)

`.github/workflows/ci.yml` (Windows fast lane) and `postgres.yml` (PostGIS 16
lane). The GitHub CLI is not available on this host, so remote runs were not
observed; every lane's commands were executed locally (section 10).
`ops/tests/test_ci_contract.py` pins what the workflows must and must not do:
the repository's runtime contract, read-only permissions, no secret, no
production path, task, port or database, bounded timeouts, real Job Objects
on Windows, PostgreSQL suites one process per file.

## 9. End-to-end rehearsal on this host (Part X)

`ops/qualification/Run-Task230ControlPlaneRehearsal.py`, candidate `37b7500`,
the last code commit (run `f6`, `evidence/rehearsal_37b7500`, 18:07-18:16
local; the earlier heads `b6fe82e` (run `f5`) and `b6ea32b` (run `f4`, before
the resume phase existed) also passed every phase). Isolation: roots under
`C:\AgroSat_rehearsal\T230\f6`, databases `agrosat_task230_*_f6` on the local
PostgreSQL 16 server, ports 58400-58403, tasks `\AgroSat_TASK230_R1_*`,
`\AgroSat_TASK230_R2_*` and `\AgroSat_TASK230_DatabaseBackup` (none fires by
itself), generated environment files readable by SYSTEM and Administrators only
and deleted at the end. Production releases were only read, to copy
4cd8ea7's venv and `dist` into the rehearsal's release A. The harness refuses
to run between 05:30 and 07:00 or while the production Sentinel runs.

| phase | what happened | result |
|---|---|---|
| database | source database at 0016 with synthetic rows; backup validated (pg_dump exit, non-empty, restore list, table data, single revision, critical tables) and copied to a second local root (SHA-256 verified): fully protected; restore rehearsal into a new database: revision, constraints, indexes, row counts, critical tables, schema fingerprint, PostGIS all match; retention plan and apply (nothing eligible) | PASS |
| release (run 1) | A = 4cd8ea7 running under its production-shaped legacy supervisor. Release to 37b7500: the controller process was ended (TerminateProcess) right after SWITCH_BACKEND was recorded, inside VERIFY_BACKEND; the same command resumed it: VERIFY_BACKEND 2 starts / 1 interruption, every other gate 1 start, the candidate backend supervisor started once; COMMIT, 166 s in total. The legacy handoff ended the 3 captured descendants of A's backend (python, conhost, python) that outlived the task stop; no A process left. The same command again: exit 2 `AUTHORIZATION_REPLAY_REJECTED`, state and all four task bindings unchanged. Explicit rollback to 4cd8ea7: 12/12 gates, database case 1 (0016 stays 0016), bindings equal to the recorded 4cd8ea7 actions, no B process left, A healthy under the compatible readiness contract | PASS |
| failure (run 2) | A' = 387eaeda at 0015. Release to 37b7500 with an authorized 0015 -> 0016 migration and the rehearsal fault `backend_unready_after_switch`: MIGRATE applied 0016, SWITCH_BACKEND passed, VERIFY_BACKEND failed as injected. Automatic rollback: candidate backend stopped through its Job Object in 0.592 s (no handoff needed), database case 3 (automatic downgrade forbidden for 0016): the validated pre-release backup restored into a new database and swapped in by rename, the migrated database kept as `agrosat_task230_rel2_f6_pre_rb_rced_failure` (still at 0016); A' rebound and listening after 4.0 s, healthy; database back at 0015; 0 of 6 captured candidate processes alive; exit 3 `ROLLED_BACK` | PASS |
| backuptask | installer and inspector of the candidate release: preview, apply (installed Disabled), enabled and run once as SYSTEM: result 0; execution record PASS (validated backup, verified secondary copy, retention); inspector: no contract violation; unregistered | PASS |
| cleanup | every rehearsal task stopped (the legacy A trees through their captured lineage) and unregistered, ports free, restore target dropped with its evidence, environment files deleted | done |

Observation: `Get-ScheduledTaskInfo` on this host reports run times with odd
seconds (NextRunTime 03:17:17 for a 03:17:00 trigger). A probe task with a
17:59:00 trigger actually ran at 17:59:00.02 while the scheduler reported
17:59:59; the contract checks read the trigger itself, never those times.

## 10. Regression on the final head

Every CI lane run locally on `37b7500` (the last code commit; the head after
it adds only these two documents), `PYTHONUTF8=1` and
`PYTHONDONTWRITEBYTECODE=1` as in CI, no `DATABASE_URL` except in the
PostgreSQL lane (`evidence/final_regression_37b7500*`):

| lane (workflow job) | result |
|---|---|
| guards: `rollback-contract --backend backend` | PASS, single head 0016, 17/17 migrations classified |
| guards: `test_web_startup_safety.py` | 6 passed |
| guards: retired production mutation endpoints (4 files) | 137 passed |
| backend: `pytest tests` | 1419 passed, 116 skipped, 0 failed (448f404: 1396 passed, 14 failed) |
| ops: PowerShell parse of every `ops/**/*.ps1` | 24 parsed, 0 errors |
| ops: `pytest ops/tests` (in-memory control plane, contracts, backup, real Job Objects) | 117 passed |
| frontend: `npm ci`, `test:contracts`, `build`, `npm audit --audit-level=high` (Node 24.16.0) | 22/22 suites, build OK, 0 vulnerabilities |
| postgres: empty database -> `alembic upgrade head` (`agrosat_task230_ci_37b7500`) | single head, database at 0016 |
| postgres: 8 PostgreSQL suites, one process each (`agrosat_h0a_task229`) | 116 passed |

The first run of this regression, on `b6fe82e`, found two defects that the
earlier runs had not exercised, both fixed with a regression test:
`rollback-contract --backend backend` as CI calls it resolved the path twice
(`f9175ee`); under `PYTHONUTF8=1`, PowerShell error text in this host's OEM
code page broke output decoding in a backup installer test (`37b7500`).

## 11. Residual risks

1. **No off-host backup copy.** No secondary destination exists on another
   host or disk; the rehearsal's secondary is a second local root. Until one
   is provided, production backups are primary-only (`fully_protected`
   false), recorded as such, never presented as protected.
2. **No production backup policy or schedule.** Nothing was invented: backup
   root, retention values and time are operator decisions (TASK_231 plan,
   section 2); `\AgroSat_PROGRAM_R3_DatabaseBackup` does not exist.
3. **Application crash stays down.** Task Scheduler does not restart a task
   whose action exits non-zero (measured, RestartCount 2 ignored); after a
   child crash the supervisor exits with its code and the component stays
   down until a start or reboot. Visible in health; an automatic restart
   policy is a separate decision.
4. **First switch needs the legacy handoff.** 4cd8ea7's supervisor predates
   Job Objects, so its uvicorn/node outlive the task stop; the controller ends
   exactly the captured descendants. Rehearsed with 4cd8ea7's own launcher.
5. **CI not observed remotely.** No GitHub CLI or API access here; every lane
   ran locally with equivalent commands, the PostGIS lane against the local
   PostgreSQL 16.14 server instead of the `postgis/postgis:16-3.4` container.
6. **MAX_PATH.** Long paths are disabled on this host; the deepest production
   release file is 236 characters (24 of headroom). A release that would
   exceed it stops at MATERIALIZE (`RELEASE_PATH_TOO_LONG`), before any switch.
7. **Worker ownership relies on the shared console.** A future provider child
   started with its own console would outlive a task stop (the diagnostic
   showed it); no current worker does.
8. **Schema fingerprint.** CHECK constraints and partial-index predicates are
   compared by name and shape, not expression text, because PostgreSQL
   re-deparses them on restore; a change only inside such an expression is
   not detected by restore validation.
9. **Pre-TASK_228 rollback targets.** Rollback to 4cd8ea7 accepts its older
   readiness payload (no `revision_match`), still requiring its
   `migration_revision` to equal its own head.
10. **Same-SHA retry after a rollback** needs the rolled-back attempt's runtime
    directory moved aside (`RUNTIME_DIRECTORY_EXISTS_UNOWNED`).
11. **Sentinel window.** The collector dry run of VERIFY_WORKERS takes the
    collector's global mutex; PRECHECK refuses while the Sentinel runs and the
    plan forbids 05:30-07:00, but a manual collector run started during a
    release would contend.

## 12. Evidence index

Under `C:\AgroSat_backups\TASK_230_H0D3_OPS_CONTROL_PLANE_EVIDENCE_20260925\evidence`:

| directory | content |
|---|---|
| `baseline` | 448f404 backend suite (14 failed, 1396 passed) |
| `ownership_before_after`, `ownership_final` | process ownership qualification (first and final run) |
| `worker_ownership` | worker tree measurements and the detached-provider diagnostic |
| `rehearsal_dbcheck*`, `rehearsal_full*`, `rehearsal_final` | earlier rehearsal attempts and the findings they produced |
| `rehearsal_b6ea32b` (f4), `rehearsal_b6fe82e` (f5), `rehearsal_37b7500` (f6, final) | full rehearsals, every phase PASS |
| `production_readonly` | `tasks --mode production` against the live tasks: PASS |
| `final_regression*` | the regression of section 10 |

Scripts used: `scripts/` next to `evidence/` (per-file PostgreSQL runner,
empty-database migration, regression and frontend lanes, superseded-database
cleanup).

The per-gate state-machine evidence (`state.json`, `events.jsonl`, `gates/`,
`snapshots/`, `summary.md` per release id) stays in each rehearsal's control
root, `C:\AgroSat_rehearsal\T230\<run>\r1|r2\control\releases\`. Kept for
inspection: the rehearsal roots (`C:\AgroSat_rehearsal\T230`, about 6.3 GB, and
`C:\AgroSat_rehearsal\TASK_230`, about 0.7 GB) and the four databases of the
final run (`agrosat_task230_src_f6`, `_rel1_f6`, `_rel2_f6`,
`_rel2_f6_pre_rb_rced_failure`). The 23 databases of superseded attempts
were dropped after their evidence was recorded
(`evidence/cleanup_superseded_databases.json`). Nothing else was created
outside the branch.
