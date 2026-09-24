# TASK_223 — A collection run must own its backend and name its failures once

## 1. What happened in production

The 2026-09-23 apply cycle (run `27db375c-d94d-4ee4-b96e-60d5e7e19383`,
release `97f1653`) collected correctly — 259 NDVI observations written,
freshness recomputed to FRESH 1372 / AGING 2 / STALE 1 — and then terminated
with exit code 4. The only diagnosis it left was this:

```
monitoring reconciliation failed: (psycopg2.errors.InFailedSqlTransaction)
[SQL: SELECT pg_advisory_unlock(%(key)s)] [parameters: {'key': 871320219}]
```

That statement is the `finally` of `finish_apply_run`. It is not the failure;
it is the statement that replaced it. The run row was left `status='running'`,
which the reconciler reads as an in-flight cycle and alerts on six hours
later, and which makes the next cycle refuse the same run key.

Two defects combined to produce that line. One destroyed the diagnosis; the
other caused the failure. Both are fixed here.

## 2. The failure: three vocabularies for one column (C3)

`satellite_collection_runs.failure_category` is bounded by
`ck_collection_runs_failure` to eight labels:

```
auth, quota, network, timeout, quality, lock_contention, contract, operational
```

`classify_failure()` in the collector emitted a different set — it could
return `cloud`, `partial` or `cancelled`, none of which the constraint
accepts — while `services/health.py` accepted a third set, which included the
collector's labels but *not* the database's `timeout` and `quality`.

`partial` is what an ordinary exit code 1 produces: a cycle that ran and in
which some fields failed. That is the common case, and it was the broken one.

Replayed against the archived production summary and a real constraint, with
the exit code that was live at the `finish_apply_run` call site:

```
classify_failure(exit_code=1) -> 'partial'
UPDATE ... failure_category='partial'
  -> psycopg2.errors.CheckViolation: ... нарушает ограничение-проверку
     "ck_collection_runs_failure"
  -> then, in that aborted transaction:
     psycopg2.errors.InFailedSqlTransaction: [SQL: SELECT pg_advisory_unlock(...)]
```

The second line is, verbatim, what production reported. **C3 was not a
separate backlog item; it was the root cause of the terminating failure.**

## 3. The amplifier: the run did not own its backend

A session-scoped advisory lock belongs to the PostgreSQL **backend** that took
it, not to the SQLAlchemy `Session`. A pooled `Session` releases its
connection on every `commit()`, and a cycle commits many times — once per
batch through `heartbeat()`, again in `refresh_freshness()`, again in
`reconcile_pixel_candidates()`. From the first commit the run is a tenant of
whatever connection the pool hands back. Three consequences, each reproduced
against a real database:

1. **The lock is stranded on a backend the run no longer uses**, and the pool
   hands that backend to unrelated callers — the web application included —
   while it still holds the lock.
2. **The single-writer guarantee is void.** A second `begin_apply_run` given
   that same pooled connection re-acquires the lock *re-entrantly*:
   `pg_try_advisory_lock` returns true on a backend that already holds the
   key. On the original code the second run starts cleanly.
3. **The unlock releases nothing**, because it runs on the wrong backend, so
   the lock leaks for the life of the pooled connection.

This was recorded in H0-A §9 item 2 and deferred to H0-D/C3.

## 4. The change

**`backend/services/collection_failure.py`** (new) — one vocabulary.
`RUN_FAILURE_CATEGORIES` is the database's eight labels; every producer's
label resolves through `canonical_failure_category()`. Provider names map onto
it (`authentication`→`auth`, `provider_error`→`operational`), conditions the
database expresses differently are folded (`cloud`, `cloud_blocked`,
`quality_blocked`→`quality`; `partial`, `cancelled`→`operational`), and an
unrecognised label degrades to `operational` rather than reaching the database
as itself. An unknown label is a reason to record a failure imprecisely, never
a reason to fail the write that records it.

**`backend/scripts/collect_satellite.py`** — `classify_failure` now names the
condition through `_failure_condition` (its previous body, unchanged) and
returns `canonical_failure_category(...)` of it.

**`backend/services/health.py`** — `ALLOWED_FAILURE_CATEGORIES` *is*
`RUN_FAILURE_CATEGORIES`, not a copy of it.

**`backend/services/autonomous_monitoring.py`** — `begin_apply_run` checks out
one connection and binds the session to it, so the lock, every heartbeat, the
freshness refresh and the finish share one backend; `ApplyRun` carries that
connection and the failure path closes it. `finish_apply_run` no longer
destroys the reason the cycle ended: on a failed status write it rolls back
and records the terminal status through a guarded second write without the
counters payload, so the row is never left `running`. Release moved into
`release_apply_run`, which rolls back before unlocking — an aborted
transaction rejects the unlock too — and *returns* its failure instead of
raising it. The original exception is re-raised; a release failure surfaces
only when there is no original.

**`backend/tests/test_task209_canonical_collector.py`** — two cases in
`test_failure_categories_are_bounded_labels` asserted `cloud` and `cancelled`,
so they specified the defect. They now assert the durable category each
condition maps to, and the test additionally checks membership of
`RUN_FAILURE_CATEGORIES`.

No migration. No schema change. The constraint is treated as the contract and
the code was brought to it — the reverse would mean shipping code that writes
labels the deployed database rejects, which is the defect again until the
migration lands.

## 5. Verification

| suite | original main | with this change |
|---|---|---|
| `test_task223_collection_run_finish.py` (focused) | 8 failed | 8 passed |
| `test_task223_failure_category_vocabulary.py` (focused) | 6 failed, 6 passed | 12 passed |
| `test_task223_collection_run_lock_postgres.py` (PostgreSQL) | 5 failed, 3 passed | 8 passed |
| `test_task209_canonical_collector.py` (corrected spec) | 1 failed, 13 passed | 14 passed |
| full backend regression, same worktree | 14 failed, 1111 passed, 1 skipped | 14 failed, 1139 passed, 1 skipped |

The per-module figures are measured with every changed file reverted to
`97f1653` in the working tree, so the only variable is this task's code. The
regression failure lists are identical; the delta is exactly the 28 new
tests. The 14
pre-existing failures are release/launcher identity tests
(`test_task212_release_runtime`, `test_worktree_runtime_config`) that fail on
this machine before and after. The skip is `test_task218_release_manifest`,
which requires the task branch to be pushed.

`test_the_code_vocabulary_is_the_schema_vocabulary` reads
`ck_collection_runs_failure` from `pg_constraint` and compares it to
`RUN_FAILURE_CATEGORIES`, so the two cannot drift again without a test
failure.

## 6. Rollback

Code-only. Revert the commits; nothing else was written. No production
release, runtime binding, scheduled task or database row was touched.

## 7. Known remaining defects — recorded, not fixed

1. **Unverified under the real collector.** Both fixes are proven by tests,
   not by a production cycle, because deployment is out of scope here.
2. **Status files written by earlier releases** may carry `cloud`, `partial`
   or `cancelled`. The readiness reader now rejects those, reporting the
   collector as `missing` until the next cycle rewrites the file. Production's
   current files carry `operational` and are unaffected.
3. **`/health` cannot see the collector for an unrelated reason.**
   `collector_readiness` rejects any status file with more than two
   `providers` entries; the collector writes one per batch (22 at 275 fields
   / batch 25). Present on `dc23f12` too — its own successful run is rejected
   the same way. Not touched here.
4. **`timeout` remains a category the database accepts and the collector never
   emits**: a timed-out child still classifies as `network`. That mapping is
   correct enough to leave alone, and narrowing it is a behaviour change with
   no defect behind it.
5. **H0-A §9 items 1 and 3–10 remain untouched**, except C3, which this task
   closes.
