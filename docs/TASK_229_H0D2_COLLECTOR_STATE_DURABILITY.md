# TASK_229 — H0-D2: durable collector operational state

Branch `task/229-h0d2-collector-state-durability`, base `7d1a975` (TASK_228,
accepted). Production and `origin/main` stay on `4cd8ea7`. Backend only. No
migration, no deployment, and no change to the frontend, ops, Scheduled
Tasks, release tooling or production configuration.

## 1. Outcome

The canonical collector now publishes its operational state through one
contract, from the provider children to the operator:

provider children → forensic run summary (`collector_summary.json`,
unchanged) → compact operational snapshot (status schema 2) → reader-safe
atomic publication → health reader → metrics.

| commit | content |
|---|---|
| `49653a2` | reader-safe publication, heartbeat, finalization semantics (Parts A, B, F, G) |
| `4ad6e94` | compact status schema 2, one writer/reader contract, reader compatibility (Parts C, D, E, I) |
| `f4c3721` | metrics never publish partial counters as field totals (Part H) |
| docs commit | this report |

Production code changed: `backend/scripts/collect_satellite.py`,
`backend/services/health.py`, `backend/services/metrics.py`, and the new
`backend/services/collector_status.py`.

## 2. Defects on 7d1a975

All reproduced on this host: Windows Server 2019 (10.0.17763), Python 3.14.5.

### 2.1 A health read could fail a successful cycle

`atomic_json` wrote a temporary file, flushed and fsynced it, and called
`os.replace` once. On Windows, Python opens files without
`FILE_SHARE_DELETE`, so replacing a file that another handle holds open
fails. The codes observed on this host:

| situation | error |
|---|---|
| a reader holds the destination (as `services.health` reads it) | `PermissionError` [WinError 5] |
| a process holds the new temporary file (as a scanner does) | `PermissionError` [WinError 32] |
| the destination is a directory | [WinError 5] |
| the destination is read-only | [WinError 5] |
| source missing / destination directory missing | [WinError 2] / [WinError 3] |

A collision at the final status publication happened after
`finish_apply_run`, so the database recorded the run as `succeeded` while the
Scheduled Task exited 4. Against PostgreSQL the run row was `succeeded` and
the task exited 4 (`test_task229_collector_finalization_postgres.py`).

### 2.2 A heartbeat collision rewrote the database outcome

`HeartbeatPublisher.stop()` runs before `finish_apply_run`. Its failure set
`final_code = 4` first, so a reader holding `collector_heartbeat.json` for an
instant produced these effects:

- the run was recorded as `failed`, with `degraded` and `operational`;
- anomaly reconciliation and verification were skipped, because they run
  only for exit codes 0 and 1.

A failed scheduled beat also escaped the heartbeat loop and ended the thread
for the rest of the run.

### 2.3 The status grew with the field count

The status files listed every batch child. 275 fields at `--batch-size 25`
produced 22 entries (4 959 bytes). At 400 batches per provider the file is
170 KB, and at the collector's maximum scope it is 2.1 MB. The reader's
64 KiB bound is reached at about 3 800 fields. Beyond that the collector is
`rejected/oversized`.

### 2.4 Metrics presented partial totals as complete

`collector_metrics` summed the counters of the batches that reported them.
It did so even when `counters_batch_count < batch_count` (TASK_228 residual
risk 2).

## 3. Reader-safe atomic publication (Parts A, B)

`atomic_json` keeps its contract:

- a temporary file in the same directory;
- sanitized UTF-8 JSON;
- flush and fsync;
- one atomic `os.replace`;
- cleanup of the temporary file.

Only the final replacement is retried, and only for a collision that clears
by itself (`transient_replace_error`):

| error | retried |
|---|---|
| `ERROR_SHARING_VIOLATION` (32) | yes |
| `ERROR_ACCESS_DENIED` (5), destination an existing regular file without the read-only attribute | yes |
| `ERROR_ACCESS_DENIED` (5), destination a directory, read-only, missing or not stat-able | no: fails at once |
| any other code, any errno-only error (disk full, I/O, path not found, POSIX permission) | no: fails at once |

**Schedule.** `REPLACE_RETRY_DELAYS_SECONDS = (0.005, 0.01, 0.02, 0.04,
0.08 × 10)`. It doubles from 5 ms to 80 ms, then pauses 80 ms each time: 15
attempts, and at most 0.875 s of added delay. It is deterministic, and the
pause is injectable (`atomic_json(..., sleep=...)`), so no test sleeps for
real seconds.

**Guarantees:**

- The same temporary file serves every attempt.
- The destination keeps its previous complete content until the single
  successful replacement. It is never written in place.
- On any failure, including an interrupt during a pause, the temporary file
  is removed.
- A collision that outlasts the schedule raises the operating system's own
  error.

**Access-control denials.** A denial on the status file itself returns the
same `ERROR_ACCESS_DENIED` as a reader. It is retried within the bound, then
raised.

**Why this schedule.** Under an adversarial reader that reopened the file
about 20 000 times a second, the first schedule tried (8 attempts in 0.95 s)
lost 9 of 314 publications. The adopted schedule lost 0 of 557 over the same
20 s, with a smaller budget (§8.1).

## 4. Finalization semantics (Parts F, G)

The heartbeat and status files are operational state. They are published
around the collection and never into it. The database run is terminalized by
`finish_apply_run` with the provider outcome, and only afterwards can a
persistence failure change the Scheduled Task's exit code.

| case | database run | task exit | operational files | diagnostics |
|---|---|---|---|---|
| a transient collision on any publication | provider outcome | provider outcome | published | none |
| permanent failure of the latest or history status | provider outcome, terminal | 4 | latest may still hold the running snapshot | `latest status persistence failed: …`, also written into the run's forensic summary |
| permanent failure of the final heartbeat | provider outcome, terminal (7d1a975: `failed`) | 4 | latest reads failed / 4 / `operational`; each provider shows its real result | `heartbeat persistence failed: …` |
| permanent failure of the forensic summary | provider outcome, terminal | 4 | latest reads failed / 4 | `summary persistence failed: …` |
| a scheduled beat not persisted, the final beat persisted | provider outcome | provider outcome | heartbeat current again | `heartbeat persistence failed: … (N scheduled beat(s) missed)` |

**Heartbeat.** The cadence is unchanged: 30 s. `_beat()` catches a failed
scheduled beat, counts it and keeps the thread alive. `stop()` decides
whether persistence failed.

**Diagnostics.** A persistence diagnostic names three things: the error
class, its Windows or errno code, and the system message, plus the file's
name. It never includes a path. An `OSError` prints its file names in repr
form (doubled backslashes), and the existing user-path redaction does not
match that form. On 7d1a975 such diagnostics carried
`C:\\Users\\…\\.tmp-….json -> …`.

**Unchanged:**

- **Start-up publications.** A permanent failure to publish the running
  snapshot or the first heartbeat still aborts the cycle before
  `begin_apply_run`, with exit code 4. No database row is created. Transient
  collisions there are now retried.
- **Monitoring failures.** A monitoring failure still terminalizes the run
  with exit code 4 (TASK_223, TASK_225).

## 5. Compact status, schema 2 (Parts C, E)

`collector_latest_status.json`, `collector_last_success.json` and
`collector_last_failure.json` carry these top-level fields:

- `schema_version: 2`
- `run_id`, `mode`, `status`
- `started_at`, `finished_at`, `duration_seconds`
- `exit_code`, `failure_category`
- `providers`: at most one summary per logical provider, `ndvi` then `multi`

Each summary has these fields:

| key | meaning |
|---|---|
| `batch_count` | batch children of this provider path |
| `succeeded_batch_count` | batches whose child exited 0 without timing out |
| `failed_batch_count` | every other batch |
| `timed_out_batch_count` | the failed batches that timed out |
| `status` | `succeeded` when every batch succeeded, `failed` when none did, `partial` otherwise |
| `exit_code` | the highest batch exit code |
| `timed_out` | any batch timed out |
| `counters` | the six counters summed over the batches that reported them; null when none did |
| `counters_batch_count` | how many batches `counters` covers |

These are TASK_228's semantics. The fold was moved, not rewritten, into
`services/collector_status.py`, which both the writer and the reader use.
Counters are never invented: a partial provider shows
`counters_batch_count < batch_count`.

**Field-count independence.** The file is O(provider types). The same run,
written through `atomic_json`:

| batches per provider | schema 1 (7d1a975) | schema 2 |
|---|---|---|
| 11 (production: 275 fields at 25) | 4 959 B | 1 015 B |
| 400 (10 000 fields at 25) | 170 462 B, `rejected/oversized` | 1 031 B |
| 5 000 (10 000 fields at 2, the collector's maximum) | 2 105 280 B, `rejected/oversized` | 1 037 B |

The per-batch detail stays in `collector_summary.json`. Its contract and
schema version are unchanged. The maximum-scope test writes all 10 000
children there and reads them back.

## 6. Reader compatibility (Part D)

`services/health.py` accepts three formats and publishes the same shape for
all of them: TASK_228's `latest`, `last_success` and `last_failure`, with the
same keys and the same provider summaries.

- **Schema 1, batch-expanded.** TASK_228's production fixture: 22 entries,
  11 NDVI and 11 multi-index, 275 fields.
- **Schema 1, one entry per provider,** with or without counters, and with
  or without `schema_version`.
- **Schema 2.**

**Schema-2 validation.** Each summary is accepted only if it is exactly what
the fold publishes for the counts it states:

- The status, failed count and timeout flag are derived again, and must
  match in value and type.
- The exit code must fit the counts. Clean batches exit 0, and a failed
  batch that did not time out did not.
- The counter totals must be integers from 0 to `counters_batch_count` ×
  10 000 000. They are null exactly when no batch reported counters.
- `batch_count` has the collector's own bound of 5 000.

**Rejected** (`rejected/invalid_contract`): a duplicate or unknown provider,
more than two entries, schema-1 batch entries under schema 2, any unknown
schema version, and every malformed variant tested (§7).

**Unchanged:** unknown keys are never published, and the 64 KiB bounded read
stays as it was.

## 7. Metrics (Part H)

For each logical provider present, `collector_metrics` renders one gauge,
`agrosat_collector_last_run_provider_counters_complete{provider="…"}`. It is
`1` when every batch reported counters and `0` otherwise. The six
`agrosat_collector_last_run_fields` series are rendered only when it is `1`.

- **Labels.** The only labels are `provider`, `outcome`, `status` and
  `failure_category`.
- **No duplicate series.** Each provider is rendered once, even if a
  component lists it twice.
- **Schema independence.** A schema-1 and a schema-2 file of the same run
  give identical metrics.
- **Production.** The 2026-09-25 production cycle renders 12 field series
  and two gauges at 1.

## 8. Evidence

Raw evidence: `C:\AgroSat_backups\TASK_229_H0D2_COLLECTOR_STATE_EVIDENCE_20260925`.

### 8.1 Real concurrency, no mocks

`scripts/concurrency_stress.py` runs two things for 60 s. A reader thread
reads the status file exactly as `services.health` does, in a tight loop. The
main thread publishes the file with the collector's own `atomic_json`.

| | base 7d1a975 | candidate |
|---|---|---|
| publications | 18 874 | 1 975 |
| failed publications | 9 933 (52.6 %), all [WinError 5] | 1 (0.05 %), after the full 0.875 s schedule |
| slowest publication | 0.024 s | 0.887 s |
| torn or partial reads | 0 | 0 |
| temporary files left | 0 | 0 |
| reader open failures (`PermissionError`, the rename instant) | 12 534 | 3 205 |

This reader holds the file almost continuously, far beyond any health probe.
In practice a probe collides rarely, and the retry absorbs it. The last row
is a reader-side effect that exists on base too (§11, item 1).

### 8.2 Snapshot cost

Median of repeated runs:

| batches per provider | base: snapshot / reader | candidate: snapshot / reader |
|---|---|---|
| 11 | 0.97 ms / 0.27 ms | 0.28 ms / 0.19 ms |
| 400 | 33.3 ms / file rejected | 2.4 ms / 0.18 ms |
| 5 000 | 440 ms / file rejected | 28.9 ms / 0.22 ms |

## 9. Tests

All runs used the same environment:

- this host and `C:\AgroSat\backend\venv`;
- PostgreSQL 16.14 with PostGIS, isolated databases `agrosat_h0a_task229`
  and `agrosat_h0a_task229b`, migrated to `0016`.

New suites, 157 tests:

| suite | tests | covers |
|---|---|---|
| `test_task229_atomic_publication.py` | 37 | the host's sharing facts; a real reader, reader across attempts, scanner, a reader that never lets go; directory and read-only destinations; the retry contract with injected failures and time; unrelated errors; interrupt cleanup; the classifier; sanitize/fsync order; heartbeat publish, stop and loop |
| `test_task229_compact_status.py` | 92 | the writer's schema 2; writer ≡ reader fold across batch mixes; partial providers; no invented counters; no raw detail; size independence; maximum scope (10 000 children); every on-disk format; 8 valid and 49 malformed schema-2 cases; unknown versions; run-level contract; byte cap; a real-shaped 275-field cycle |
| `test_task229_collector_metrics.py` | 6 | complete, partial and absent counters; two providers; no duplicates; schema independence; label cardinality |
| `test_task229_collector_finalization.py` | 18 | run() in apply mode with database recorders: collisions at every publication; partial cycles; permanent status, heartbeat and summary failures; missed beats; exactly one terminalization with the provider outcome; diagnostics without paths or secrets |
| `test_task229_collector_finalization_postgres.py` | 4 | the real apply lifecycle on PostgreSQL: the run row is `succeeded` and terminal after a status or heartbeat collision (exit 0) and after a permanent status or heartbeat failure (exit 4), and the advisory lock is released |

On Windows the collisions are real: `ReplaceFaults` opens the file as the
reader does and lets the real `os.replace` fail. Elsewhere they are simulated
with the Windows errors, so CI does not depend on Windows.

Updated existing tests:

- the TASK_209 writer assertion now expects the schema-2 shape;
- TASK_209's metrics input is now in the reader's shape;
- TASK_228's unknown-version case uses 3, because 2 is now known;
- TASK_228's web-safety AST list now includes `collector_status.py`.

| run | result |
|---|---|
| new tests on pristine 7d1a975, before the implementation | 75 failed, 76 passed, 4 skipped (no DB URL); PostgreSQL suite 3 failed, 1 passed |
| final test files overlaid on pristine 7d1a975 | 82 failed, 241 passed; causes below |
| focused suites (TASK_209, 219, 223, 225, 228, 229), candidate | 412 passed |
| each commit on its own, focused and PostgreSQL | `49653a2` 312 + 4, `4ad6e94` 406 + 4, `f4c3721` 412 + 4 passed |
| full backend, base 7d1a975, run twice (`agrosat_h0a_task229`, then `…229b`) | 25 failed, 1342 passed, 1 skipped |
| full backend, candidate (once, on `…229b`) | 25 failed, 1501 passed, 1 skipped |
| PostgreSQL suites with `DATABASE_URL` set, one process per file, base / candidate | 112 / 116 passed |

Why the 82 fail on base (`evidence/final_tests_on_base_by_cause.txt`):

| count | cause on base |
|---|---|
| 5 | a real reader or scanner collision raised out of `atomic_json` |
| 10 | a transient collision turned a finished cycle into exit code 4 (2 against PostgreSQL) |
| 3 | a heartbeat failure rewrote the database outcome (PostgreSQL row `failed`) |
| 2 | a failed scheduled beat ended the heartbeat thread |
| 27 | no bounded retry contract: no schedule, injectable pause or classifier |
| 14 | the writer published the batch-expanded schema 1 |
| 12 | the reader rejected schema 2 |
| 5 | metrics published partial totals, had no completeness gauge, duplicated series |
| 1 | the forensic summary did not record a status persistence failure |
| 1 | the persistence diagnostic carried full paths |
| 2 | the new module does not exist on base (AST list) |

The 241 that pass on base are guards that must hold on both sides:

- the host's own sharing facts;
- fail-fast on directory and read-only destinations;
- sanitize and fsync before the replacement;
- the heartbeat cadence;
- the schema-1 files still accepted;
- malformed files and unknown versions rejected;
- the byte cap and label cardinality;
- failures of publications that come after `finish_apply_run`;
- every pre-existing test.

The full-suite failure and skip lists are identical on base and candidate.
They are the same list TASK_228 recorded:

- `test_task212_release_runtime` ×12 and
  `test_task209_release_rollback_artifacts` ×2: release tooling.
- `test_h0a_freshness_postgres_contract` ×11: environmental; it needs
  `DATABASE_URL`, and passes with it (the 112 / 116 row).
- The skip, `test_task218_release_manifest`, runs only once the branch is on
  `origin`. Every run took place before the push.

**Environment note.** Running all PostgreSQL suites in one pytest process
with `DATABASE_URL` set hung on base on this host. A session from
`test_h0a_freshness_postgres_contract` stayed idle in transaction, and the
next suite's `TRUNCATE` in `test_task225_closed_loop_postgres` waited on it
forever. This is a pre-existing test-isolation issue, independent of this
change. Running each PostgreSQL file in its own process avoids it, and both
base and candidate were run that way (`scripts/run_pg_per_file.sh`). The hung
run (pids 6396 and 4788) still holds `agrosat_h0a_task229` and was left in
place. All later database runs used `agrosat_h0a_task229b`.

## 10. Rollback, verification and side effects

**Rollback.** Code only: revert `f4c3721`, `4ad6e94` and `49653a2`. There is
no migration and no data or configuration change. Revert the writer and the
reader together. TASK_228's reader rejects schema 2, so after a rollback
`collector.status` reads `rejected/invalid_contract` until the next cycle
rewrites the latest file in schema 1. A schema-2 `collector_last_success.json`
or `collector_last_failure.json` stays unreadable until a run of that outcome
rewrites it.

**Verify on the target after deployment**, after the next cycle:

- `collector_latest_status.json` has `schema_version` 2, is about 1 KB and
  holds two provider summaries;
- `/health/ready` shows `collector.status` `succeeded` (or `stale`) with the
  same `latest` shape as TASK_228;
- `/api/operations/metrics` shows 12 `agrosat_collector_last_run_fields`
  series and two `…_provider_counters_complete` gauges at 1;
- the Scheduled Task's last result is 0 (Operational log event 201);
- the newest `satellite_collection_runs` row is `succeeded`.

**Side effects:**

- **Status format.** The status files change to schema 2. Nothing in this
  repository reads them except `services/health.py`; there is no consumer in
  the frontend or in ops.
- **New metric.** `agrosat_collector_last_run_provider_counters_complete`
  appears. A provider's field series disappear while its counters are
  incomplete, by design.
- **Heartbeat failures.** A permanent heartbeat failure no longer records the
  collection as failed in the database. The task still exits 4.
- **Diagnostics.** Persistence diagnostics keep their prefixes but no longer
  carry paths.
- **Slower publication under collision.** A publication can take up to
  0.875 s longer while a collision lasts.

## 11. Residual risks

1. **The reader's side of a replacement.** On Windows a read that coincides
   with the rename can fail to open the file. `services.health` then reports
   that probe as `rejected/unreadable`, and the next probe recovers. This
   exists on base too, and it is negligible at probe rates (§8.1). A
   reader-side retry would close it; it was left out of this writer task.
2. **A reader that never lets go.** A collision lasting longer than 0.875 s
   is a bounded operational failure (exit code 4), as specified. Under the
   adversarial stress this happened in 1 of 1 975 publications.
3. **Access-control denials.** An ACL denial on the status file itself is
   indistinguishable from a reader. It costs one schedule (0.875 s), then
   fails with the real error.
4. **A stale running snapshot.** After a permanent latest-status failure, the
   previous running snapshot stays until the next cycle; health reads
   `running`, then `stale`. The exit code and the forensic summary state the
   failure.
5. **Crash leftovers.** A process killed between `mkstemp` and the
   replacement leaves a `.tmp-*.json` beside the status files. This is
   pre-existing, harmless to the reader, and not swept.
6. **Rollback window.** See §10: a schema-2 file is unreadable to the
   TASK_228 reader.
7. **Test environment.** The one-process PostgreSQL hang in §9 is
   pre-existing. The two hung processes on `agrosat_h0a_task229` still need
   stopping.
8. **Not deployed.** Production still runs `4cd8ea7`. It has neither TASK_228
   nor this change: the collector keeps writing schema 1, and readiness
   reports `collector: missing`.
