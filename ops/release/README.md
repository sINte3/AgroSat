# AgroSat production operations control plane (TASK_230)

One operator entry point, `Invoke-AgroSatControlPlane.py`, drives release,
rollback, migration planning, health qualification and the database backup
contract. It uses only the Python standard library (plus Alembic from the
release's own venv to read the migration graph) and prints one JSON document
per command. Exit code 0 is PASS; 2 is a fail-closed stop with a stable error
code; 3 means a release ended without completing (failed, rolled back or
MANUAL_RECOVERY_REQUIRED).

```
python -B Invoke-AgroSatControlPlane.py release  --mode production --release-id <id> \
    --candidate <40-hex> --expected-current <40-hex> --authorization <file> \
    --authorization-sha256 <sha256 of that file> --repository <git checkout> \
    --backup-policy <policy.json>
python -B Invoke-AgroSatControlPlane.py rollback --mode production ...   (same identity rules)
python -B Invoke-AgroSatControlPlane.py preflight | manifest | migration-plan |
          rollback-contract | tasks | authorize | status | backup ...
```

## Release identity (Part D)

A release is the exact candidate SHA, the exact production SHA it replaces,
remote-tracking refs fetched at release time (the candidate must be contained
in an `origin/*` ref), ancestry (production must be an ancestor of the
candidate), the candidate's Git tree, the SHA-256 of the exact
`git archive --format=zip` material, the Alembic head read from the
candidate's own migration graph, and the runtime contract
(`runtime-contract.json`). No branch, task or worktree name is part of it.
The immutable `release-manifest.json` (schema 2) is written into the release
directory once; `git_sha` keeps its name for the launcher and worker runners.

## Production authorization (Part E)

Production is implemented but cannot start by accident:

* `--mode production` uses the fixed production identity in
  `controlplane/profiles.py` (roots, ports 8000/5173, database `agrosat`, the
  canonical task names); it takes no profile file;
* an authorization file (see `controlplane/authorization.py`, or
  `authorize` to write one) binds operation, release id, candidate SHA,
  expected current SHA, database, migration permission, database rollback
  strategy and a validity window of at most 24 hours; unknown keys,
  credential-like values, a file inside a source or release tree, an expired
  window and a reused release id are all refused;
* the file's SHA-256 must be typed on the command line.

The authorization is validated before anything is locked or created.

## Gates (Parts F, H, I)

`PRECHECK -> BACKUP -> MATERIALIZE -> VALIDATE -> MIGRATION_PLAN -> MIGRATE ->
SWITCH_BACKEND -> VERIFY_BACKEND -> SWITCH_FRONTEND -> VERIFY_FRONTEND ->
REBIND_WORKERS -> VERIFY_WORKERS -> FINAL_HEALTH -> COMMIT`

* PRECHECK proves all tasks are bound to the expected current SHA and meet
  their schedule contracts, snapshots every task definition and worker
  configuration, refuses to run during a Sentinel cycle.
* BACKUP takes a validated pre-release backup (always).
* MATERIALIZE extracts the exact archive into a staging directory, copies the
  previous venv (requirements unchanged; otherwise `VENV_REBUILD_REQUIRED`)
  and the previous `dist` (frontend unchanged; otherwise `npm ci` + build),
  writes the manifest, verifies the material against Git with a temporary
  index, and renames it into place. The runtime directory gets the launcher,
  the ownership helper, the schema 2 application configuration and signed
  worker runners with configurations derived from the current ones.
* VALIDATE re-verifies material and manifest, the runtime contract (Python
  3.14, Node 24, PostgreSQL client 16, lockfile v3), launcher `--validate-only`,
  runner signatures (allowlisted thumbprint) and worker configurations under
  `AllSigned`, and rollback classification coverage.
* MIGRATION_PLAN reads the database revision read-only and the candidate graph
  with Alembic: equal is `noop`; behind is `upgrade` and must be authorized
  with the exact from/to revisions; ahead, foreign, missing or multiple
  revisions, or a candidate with several heads, is blocked.
* SWITCH stops the task, proves its whole process tree and listener are gone,
  rebinds only the action, proves triggers/settings/principal are unchanged,
  starts it and proves the listener belongs to the task's process lineage.
* VERIFY_BACKEND requires `/health/live` 200 and `/health/ready` 200 with the
  candidate `release_revision`, database `ready`, `revision_match` true and
  both migration revisions equal to the candidate head. Collector and cache
  state are recorded, never blocking.
* COMMIT writes `control/current-release.json` and `previous-release.json`.

Every gate writes `gates/NN_<GATE>-attemptK.json` into the release's single
evidence directory `control/releases/<release_id>/` (with `state.json`,
`events.jsonl`, `snapshots/`, `summary.md`). Credential-like values are
refused before anything is written.

## Resume and idempotency (Part G)

A release id is bound to its identity hash. Re-running it resumes at the
recorded gate; every gate inspects the live system first (an already bound
and listening task is not switched again, an applied migration is not run
again, a second listener is never started). A different identity for an open
release id is refused; a terminal release id (completed, failed, rolled back,
manual recovery) never runs again, and completed ids are refused at
authorization time.

## Rollback (Parts J, K)

`rollback-contract.json` classifies every shipped migration as
`reversible_without_data_loss`, `destructive_after_data` or
`restore_backup_or_roll_forward_only` (audited 2026-09-25; only 0003 and 0004
are reversible). A failure from SWITCH_BACKEND on triggers the automatic
rollback:

* case 1, no migration applied: rebind to the exact recorded previous actions;
* case 2, every applied step reversible and a downgrade authorized:
  `alembic downgrade` to the recorded revision, then rebind;
* case 3, any destructive step: automatic downgrade is forbidden. With
  `restore_validated_backup` authorized, the validated pre-release backup is
  restored into a new database, validated, and swapped in by renaming; the
  live database is kept under an aside name, nothing is dropped. Otherwise:
  MANUAL_RECOVERY_REQUIRED.

The explicit `rollback` operation follows the same rules and returns every
task to the actions recorded when the release being undone replaced its
predecessor.

## Frontend bind address (TASK_243)

The frontend listens on `127.0.0.1` unless the operator sets one line in the
runtime environment file (`AGROSAT_RUNTIME_ENV_FILE`, in production
`C:\AgroSat\backend\.env`):

```
AGROSAT_FRONTEND_BIND_ADDRESS=10.103.25.14
```

* The value must be one canonical IPv4 literal: loopback (`127.0.0.0/8`) or, in
  production only, a private RFC 1918 address. A rehearsal stays on loopback.
  `0.0.0.0`, host names, IPv6, ports, whitespace and anything else are refused
  (`FRONTEND_BIND_ADDRESS_REJECTED`) at PRECHECK, before anything changes.
  `preflight` shows what a release would use.
* PRECHECK reads the setting once. MATERIALIZE writes it into the release's
  `application-release.json` as `frontend_bind_address`. The setting takes
  effect only through a release, never by editing the file and restarting.
* The supervisor proves the address belongs to this host (`--validate-only`
  at VALIDATE, and again before the start). It passes the address to node only
  through the child environment, never on a command line. The scheduled task
  action is unchanged.
* VERIFY_FRONTEND and FINAL_HEALTH probe the frontend on that address. They
  also prove every listener on 5173 is on exactly that address (no wildcard, no
  extra socket) and every listener on 8000 is on `127.0.0.1`. COMMIT records
  the address in `current-release.json`.
* A rollback, automatic or explicit, restores the address of the release it
  returns to, read from that release's own configuration. The setting in the
  environment file is not consulted. A release made before TASK_243 has no key,
  so it listens on `127.0.0.1`.
* The backend never follows the setting: uvicorn stays on `127.0.0.1:8000` and
  the frontend proxies relative `/api/` and `/health` to it. The proxy drops
  client-supplied `Forwarded`, `X-Forwarded-*`, `X-Real-IP` and similar
  headers, because uvicorn trusts `X-Forwarded-For`/`-Proto` from `127.0.0.1`.
* With the setting active, `http://127.0.0.1:5173` stops answering. Use
  `http://<address>:5173`, which also works on the server itself. The setting
  opens no firewall rule; the network boundary is a separate decision.

## Process ownership

`Run-AgroSatApplication.py` places itself in a kill-on-close Windows Job
Object without breakaway (`agrosat_process_ownership.py`) before starting
uvicorn or node; stopping or killing the supervisor ends the whole tree.
Pre-TASK_230 supervisors (schema 1 configuration) do not own their children:
when switching away from one, the controller captures the process lineage
below the task's Task Scheduler engine PID before stopping it and ends exactly
those captured descendants (PID plus creation time) if they outlive the stop.
It never terminates a process it did not capture, and never searches by name.

## Rehearsal

`--mode rehearsal --profile <json>` runs the same code against isolated
targets. `controlplane/profiles.py` refuses any rehearsal whose roots, ports,
tasks, database or environment file touch production. `fault_injection:
backend_unready_after_switch` points only the candidate backend at a database
that does not exist, to exercise the post-switch rollback.
