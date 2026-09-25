# Production deployment plan for the next task (prepared by TASK_230)

TASK_230 does not deploy. This is the exact plan for the task that does. Every
value below is either fixed by the repository or marked as a decision the
operator must make; nothing here is production truth by assumption.

## 1. Identity

| item | value |
|---|---|
| current production SHA | `4cd8ea7240bbd2307488ad4871e504a672a812a6` (backend, frontend, SentinelCycle, OperationalNotifications) |
| accepted candidate SHA | the reviewed head of `task/230-h0d3-production-operations-control-plane` (contains TASK_228 `7d1a975`, TASK_229 `448f404`, TASK_230) |
| ancestry | `4cd8ea7` is an ancestor of the candidate; `origin/main` is `4cd8ea7`, so the candidate fast-forwards it |
| database | `agrosat`, revision `0016_operational_command_center` |
| candidate Alembic head | `0016_operational_command_center`: **migration plan = noop** |
| requirements / frontend | unchanged since `4cd8ea7`: venv and `dist` are copied from the `4cd8ea7` release |
| detector | `OBSERVATION_DETECTION_ENABLED=false` stays as is in `C:\AgroSat\backend\.env` |

## 2. Prerequisites the operator must decide (not in the repository)

1. **Production backup policy** (`database-backup-policy.example.json` shape,
   `example_only: false`): primary `backup_root` outside `C:\AgroSat`,
   `C:\AgroSat_releases` and `C:\AgroSat_runtime`; retention values (the
   example's 7/4/3/72h are illustrations only).
2. **Secondary (off-host) destination.** None exists. Either provide an
   absolute or UNC destination on another host or disk and set
   `secondary.required: true`, or explicitly record that the release's
   pre-release backup is primary-only (not fully protected).
3. **Backup schedule time** (`schedule.daily_at_local_time`). None is defined.
   The disabled legacy task `\AgroSat_PROGRAM_R3_Stabilization_Backup` (04:30,
   release 96f7e4e) is not authoritative.
4. **Timing.** Not between 05:30 and 07:00 local (the 06:00 Sentinel cycle);
   PRECHECK also refuses while `\AgroSat_PROGRAM_R3_SentinelCycle` is running.

## 3. Authorization (outside every source tree)

```
python -B <candidate checkout>\ops\release\Invoke-AgroSatControlPlane.py authorize ^
  --operation release --release-id R20260926-task231 ^
  --candidate <accepted candidate SHA> ^
  --expected-current 4cd8ea7240bbd2307488ad4871e504a672a812a6 ^
  --database agrosat --database-rollback-strategy none ^
  --output C:\AgroSat_ops\authorizations\R20260926-task231.json ^
  --authorized-by "<operator>" --valid-hours 4
```

No migration is authorized (the plan is noop; an authorized migration with a
noop plan is refused). Keep the printed SHA-256: the release needs it typed.

## 4. Release (production mode)

```
python -B <candidate checkout>\ops\release\Invoke-AgroSatControlPlane.py release --mode production ^
  --release-id R20260926-task231 --candidate <accepted candidate SHA> ^
  --expected-current 4cd8ea7240bbd2307488ad4871e504a672a812a6 ^
  --authorization C:\AgroSat_ops\authorizations\R20260926-task231.json ^
  --authorization-sha256 <sha256 printed by authorize> ^
  --repository <candidate checkout> --backup-policy <production policy.json>
```

Evidence: `C:\AgroSat_runtime\PROGRAM_R3\control\releases\R20260926-task231\`.
A re-run with the same arguments resumes where an interruption stopped it.

## 5. Scheduled Tasks affected (actions only; triggers, settings, principal unchanged and verified)

| task | change |
|---|---|
| `\AgroSat_PROGRAM_R3_Stabilization_Backend` | action to `<candidate release>\backend\venv\Scripts\python.exe -B <runtime>\application\Run-AgroSatApplication.py ... --component backend` (Job Object supervisor, schema 2 configuration) |
| `\AgroSat_PROGRAM_R3_Stabilization_Frontend` | same, `--component frontend` |
| `\AgroSat_PROGRAM_R3_SentinelCycle` | action to `<runtime>\scheduler\Invoke-Collector.ps1` + `collector-task.json` of the candidate (signed runner, 06:00-only validator); trigger stays exactly one 06:00:00 |
| `\AgroSat_PROGRAM_R3_OperationalNotifications` | action to `<runtime>\operational-notifications\...` of the candidate; every 15 minutes unchanged |
| `\AgroSat_PROGRAM_R3_DatabaseBackup` | not touched by the release; installed separately (section 8) |

**First switch away from 4cd8ea7.** Its supervisor predates Job Object
ownership, so stopping its tasks leaves uvicorn/node running (measured). The
controller captures the process lineage below each task's Task Scheduler
engine PID before stopping it and ends exactly those captured descendants if
they outlive the stop; the evidence lists every PID and creation time. After
this release every supervisor owns its tree and no handoff is needed.

## 6. Gates and health

PRECHECK (all four tasks bound to 4cd8ea7, contracts intact, snapshots) ->
BACKUP (validated pre-release backup) -> MATERIALIZE (exact archive, venv and
dist copied, MAX_PATH checked, signed runners) -> VALIDATE -> MIGRATION_PLAN
(noop) -> MIGRATE (no-op) -> SWITCH_BACKEND -> VERIFY_BACKEND -> SWITCH_FRONTEND
-> VERIFY_FRONTEND -> REBIND_WORKERS -> VERIFY_WORKERS -> FINAL_HEALTH -> COMMIT.

Candidate health: `/health/live` 200 and `/health/ready` 200 on 127.0.0.1:8000
with `release_revision` = candidate, `database.status` = ready,
`revision_match` true, `migration_revision` = `expected_migration_revision` =
`0016_operational_command_center`. Collector state and Redis are recorded,
never blocking. Frontend on 127.0.0.1:5173 serves the release's exact
`index.html` and proxies `/health/live` to the candidate backend.

After this deployment, `/health/ready` enforces the schema head (TASK_228) and
the collector status files become schema 2 (TASK_229, writer and reader
deployed together).

## 7. Rollback path

* Automatic: any failure from SWITCH_BACKEND on returns every switched task to
  its recorded 4cd8ea7 action and verifies 4cd8ea7's health (its pre-TASK_228
  readiness payload is accepted only with `migration_revision` equal to its
  own head). Database: case 1 (no migration), nothing to do.
* Explicit, after COMMIT: `authorize --operation rollback --candidate 4cd8ea7...
  --expected-current <candidate>` then `rollback --mode production ...`; also
  case 1.
* The 4cd8ea7 release and runtime directories stay in place.
* Retrying the same candidate after a rollback needs a new release id and a
  new authorization (a terminal id never runs again). MATERIALIZE reuses the
  verified release directory but refuses the runtime directory the rolled-back
  attempt created (`RUNTIME_DIRECTORY_EXISTS_UNOWNED`, before any service is
  touched): move that directory aside first and keep it as evidence.
* An interrupted release or rollback (console closed, host restart) is resumed
  by running the identical command again; every gate re-inspects the live
  system (rehearsed: controller ended right after SWITCH_BACKEND, resumed to
  COMMIT with a single candidate start).

## 8. Backup schedule (after the release, once section 2 is decided)

```
powershell -NoProfile -File <candidate release>\ops\database\Install-DatabaseBackupTask.ps1 ^
  -PolicyPath <production policy.json> -ReleaseDirectory <candidate release> ^
  -ControlRoot C:\AgroSat_runtime\PROGRAM_R3\control            (preview)
... -Apply                                                        (installs DISABLED)
Enable-ScheduledTask -TaskName AgroSat_PROGRAM_R3_DatabaseBackup  (after review)
Inspect-DatabaseBackupTask.ps1 -PolicyPath <production policy.json>
```

The installer asks for confirmation (`ShouldProcess`, high impact). Windows
PowerShell 5.1 cannot bind `-Confirm:$false` passed through `-File`; from
automation use `powershell -NoProfile -NonInteractive -Command "& '<installer>'
-PolicyPath '...' -ReleaseDirectory '...' -ControlRoot '...' -Apply
-Confirm:$false"`, as the TASK_230 rehearsal does.

Run one restore rehearsal of the first scheduled backup into a new
`agrosat_taskNNN_*` database before relying on it:
`Invoke-AgroSatControlPlane.py backup restore-rehearsal --policy <policy>
--backup-id <id> --target agrosat_taskNNN_<name> --evidence <dir>`.

## 9. Post-release verification (read-only)

1. `Invoke-AgroSatControlPlane.py status --mode production --release-id
   R20260926-task231`: status `completed`, every gate through COMMIT passed.
2. `Invoke-AgroSatControlPlane.py tasks --mode production`: `PASS`, no
   contract violation on any of the four tasks (TASK_230 ran this against the
   live 4cd8ea7 bindings: PASS).
3. `/health/ready` on 8000: 200, `release_revision` = candidate,
   `revision_match` true; 5173 serves the release's `index.html`.
4. The listeners on 8000 and 5173 belong to processes inside each supervisor's
   Job Object (the evidence of SWITCH_* lists them); no 4cd8ea7 process remains.
5. `OBSERVATION_DETECTION_ENABLED=false` unchanged in `C:\AgroSat\backend\.env`.
6. The next 06:00 Sentinel cycle ends with result 0 and writes schema 2
   collector status; the 15-minute notification runs keep ending with 0.

## 10. Source-of-truth fast-forward gate

Only after COMMIT passed and post-release checks are clean: fast-forward
`origin/main` from `4cd8ea7` to the exact candidate SHA (no merge commit, no
force), using a per-SHA push rule. If the release rolled back, `origin/main`
stays at `4cd8ea7`.
