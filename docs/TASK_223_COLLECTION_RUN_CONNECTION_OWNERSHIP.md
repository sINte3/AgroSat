# TASK_223 — A collection run must own the backend that holds its lock

## 1. What happened in production

The 2026-09-23 apply cycle (`run_key` 27db375c-d94d-4ee4-b96e-60d5e7e19383,
release `97f1653`) collected correctly — 259 NDVI observations written,
freshness recomputed to FRESH 1372 / AGING 2 / STALE 1 — and then terminated
with exit code 4. The only diagnosis it left was this:

```
monitoring reconciliation failed: (psycopg2.errors.InFailedSqlTransaction)
[SQL: SELECT pg_advisory_unlock(%(key)s)] [parameters: {'key': 871320219}]
```

That statement is the `finally` of `finish_apply_run`. It is not the failure;
it is the statement that replaced the failure. The run row was left
`status='running'`, which the notification reconciler reads as an in-flight
cycle and alerts on six hours later, and which makes the next cycle refuse the
same run key.

The underlying weakness was already recorded — H0-A §9, item 2, deferred to
H0-D/C3:

> `begin_apply_run` takes a *session-scoped* `pg_try_advisory_lock`; SQLAlchemy
> returns the connection to the pool on commit, so the lock can be released on
> a different backend than the one that will try to unlock it.

## 2. Mechanism

A session-scoped advisory lock belongs to the PostgreSQL **backend** that took
it, not to the SQLAlchemy `Session`. A pooled `Session` releases its connection
on every `commit()`, and a cycle commits many times — once per batch through
`heartbeat()`, again in `refresh_freshness()`, again in
`reconcile_pixel_candidates()`. From the first commit onward the run is a
tenant of whatever connection the pool hands back. Three consequences, all
reproduced against a real database in
`tests/test_task223_collection_run_lock_postgres.py`:

1. **The lock is stranded on a backend the run no longer uses**, and the pool
   hands that backend to unrelated callers — the web application included —
   while it still holds the lock.
2. **The single-writer guarantee is void.** A second `begin_apply_run` that is
   given that same pooled connection re-acquires the lock *re-entrantly*:
   `pg_try_advisory_lock` returns true on a backend that already holds the key.
   On the original code the second run starts cleanly; no contention is
   reported.
3. **The unlock releases nothing.** `finish_apply_run` issues
   `pg_advisory_unlock` on whichever backend it happens to hold, so the lock
   leaks for the life of the pooled connection.

The production symptom adds a fourth: a connection handed back to the run may
already be in a failed transaction, so the first statement of
`finish_apply_run` raises `InFailedSqlTransaction` — and the bare `finally`
unlock, running in that same aborted transaction, raises over it and destroys
the original exception.

## 3. The change

`backend/services/autonomous_monitoring.py`, three edits:

* **`begin_apply_run` checks out one connection and keeps it.**
  `engine.connect()` plus `SessionLocal(bind=connection)`. The session no
  longer returns anything to the pool on commit, so the lock, every heartbeat,
  the freshness refresh and the finish all execute on one backend. The failure
  path closes the connection as well as the session.
* **`ApplyRun` carries that connection** (`connection: object = None`, optional
  so a caller supplying its own session still works — the H0-A freshness tests
  do exactly that).
* **`finish_apply_run` no longer destroys the reason the cycle ended.** The
  status write is attempted; if it fails the transaction is rolled back and a
  second, guarded write (`WHERE id=:id AND status='running'`, without the
  counters payload that may itself be the cause) records the terminal status,
  so the row is never left `running`. Release moved into `release_apply_run`,
  which rolls back first — an aborted transaction rejects the unlock too — and
  *returns* its failure rather than raising it. The original exception is
  re-raised; a release failure surfaces only when there is no original.

No migration. No schema change. No change to the collector orchestration in
`backend/scripts/collect_satellite.py`, which is byte-identical to the previous
production release and was never the defect.

## 4. Verification

| suite | original main | with this change |
|---|---|---|
| `test_task223_collection_run_finish.py` (focused, no database) | 8 failed | 8 passed |
| `test_task223_collection_run_lock_postgres.py` (PostgreSQL 16 / PostGIS) | 4 failed, 1 passed | 5 passed |
| full backend regression, same worktree | 14 failed, 1111 passed, 1 skipped | 14 failed, 1123 passed, 1 skipped |

The regression failure lists are identical; the delta is exactly the 12 new
tests. The 14 pre-existing failures are release/launcher identity tests
(`test_task212_release_runtime`, `test_worktree_runtime_config`) that fail on
this machine before and after the change. The one skip is
`test_task218_release_manifest`, which requires the task branch to be pushed.

The single Postgres test that passes on both revisions —
`test_finish_records_a_terminal_status` — is a regression guard, not a proof.

## 5. Rollback

Code-only. Revert the commit; nothing else was written. No production release,
runtime binding, scheduled task or database row was touched by this task.

## 6. Known remaining defects — recorded, not fixed

1. **The root cause of the 2026-09-23 abort is still unproven.** This change
   removes the masking, so the *next* occurrence will report its real error,
   and removes the most likely mechanism (a foreign pooled connection). It does
   not prove that mechanism was the one that fired. The PostgreSQL server log
   for that day is empty — the logging collector wrote nothing after its
   midnight rotation — so the server-side error is unrecoverable.
2. **`/health` cannot see the collector.** `collector_readiness` rejects any
   status file with more than two `providers` entries; the collector writes one
   entry per batch (22 at 275 fields / batch 25). Present on `dc23f12` too —
   its own successful run is rejected the same way. Out of scope here.
3. **H0-A §9 items 1 and 3–10 remain untouched**, including C3
   (`failure_category` mismatch between writer and reader).
