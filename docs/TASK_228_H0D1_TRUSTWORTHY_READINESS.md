# TASK_228 — H0-D1: readiness that can be trusted

Branch `task/228-h0d1-trustworthy-readiness`, base `4cd8ea7` (production and
`origin/main`). Backend only. No migration, no deployment, no frontend, ops,
Scheduled Task or release-tooling change.

## 1. Outcome

`/health/ready` now answers two independent questions:

1. **Schema identity (blocking).** Is the database reachable, and is it at
   exactly the Alembic head of the migration graph shipped with the running
   code? Anything else is HTTP 503.
2. **Collector state (non-blocking).** What did the canonical standalone
   collector last report? The reader accepts the format the collector actually
   writes. Collector state never affects readiness.

Redis stays optional and non-blocking. `/health/live` and `/health` are
unchanged.

Production code changed: `backend/services/health.py`, plus the new
`backend/services/migration_head.py`. The collector writer
(`backend/scripts/collect_satellite.py`) and `backend/api/health.py` are
unchanged.

| commit | content |
|---|---|
| `f5c9378` | collector status reader (Part A), its tests and the production-shaped fixture |
| `bbf274e` | migration-head resolver and schema-checked database readiness (Parts B–D) |
| docs commit | this report |

## 2. Defects on 4cd8ea7

### 2.1 The collector's own status was reported as `missing`

The TASK_219 batching (79ddcaf) made `collect_satellite.py` write one
`providers` entry per provider path per field batch. Take the status file
production wrote at the end of the 2026-09-25 01:00 UTC (06:00 local) cycle:
4 971 bytes, exit code 0, 22 entries (11 NDVI and 11 multi-index, for 275
fields at `--batch-size 25`). `_read_collector_file` rejected any list longer
than two, so readiness reported `collector.status = "missing"` for a healthy
collector. For the same reason `/api/operations/metrics` had published no
collector field series at all since the batching change. TASK_223 §7 item 3
recorded this defect but did not fix it.

The same reader had two more problems:

- **It raised instead of failing closed.** Crafted input broke it:
  - deeply nested JSON raised `RecursionError`;
  - a 5 000-digit integer raised `ValueError`;
  - a list where a string belongs raised `TypeError: unhashable`;
  - a UTC offset at either end of the calendar raised `OverflowError`.

  Each of these escaped as HTTP 500 from `/health/ready` and `/health`.
- **It accepted files it should have rejected** and published them as a valid
  collector state:
  - `succeeded` with exit code 1;
  - `"exit_code": "0"` and `"timed_out": "false"` written as text;
  - a child exit code of 130;
  - a running run with a finish time;
  - timestamps in the future, which read as age 0 forever.

### 2.2 Readiness never compared the database with the code

`database_readiness` ran `SELECT 1` and required one `alembic_version` row,
so any single revision counted as "ready". The live runs in §7 show the
result. After a real `alembic downgrade -1` (0016 → 0015), base 4cd8ea7
answered **200 ready**. With `alembic_version` stamped to an unknown future
revision, it also answered **200 ready**.

## 3. Collector status contract

### 3.1 What the reader accepts (status schema 1)

| field | rule |
|---|---|
| file | at most 64 KiB, read with a hard bound (`read(64 KiB + 1)`); a UTF-8 JSON object |
| `schema_version` | absent or `1` |
| `run_id` | 32 lowercase hex characters |
| `status` | `running`, `succeeded`, `failed` or `cancelled` |
| `exit_code` | must agree with `status`: running → null, succeeded → 0, failed → 1–4, cancelled → 130 |
| `started_at` | ISO 8601 with an offset, ≤ 64 characters, at most 300 s ahead of the reader's clock (same host) |
| `finished_at` | null exactly when running; otherwise the same rules as `started_at` |
| `failure_category` | null or one of `RUN_FAILURE_CATEGORIES` (the TASK_223 vocabulary); null when running or succeeded |
| `duration_seconds` | null or a number from 0 to 21 600 (the collector's cycle cap) |
| `providers` | a list of batch entries (below), at most 5 000 per provider path |

Each `providers` entry describes one child, which is one field batch:

| key | rule |
|---|---|
| `provider` | `ndvi` or `multi` |
| `exit_code` | integer 0–4 (a child's contract; only the whole run can end with 130) |
| `timed_out` | JSON boolean |
| `counters` | absent (older files), null, or exactly the six counter fields, each an integer from 0 to 10 000 000 |

Unknown keys are ignored and never published. Any other violation rejects
the whole file.

The 5 000-batch bound is the collector's own limit, not a number chosen here.
The collector accepts at most 10 000 active fields (`MAX_ACTIVE_FIELDS`), split
into batches of at least two (`validate()`), so one provider path can report
at most 5 000 batches. A test derives the minimum batch size by calling the
collector's `validate()`, then pins the reader's bound, counter fields and
duration cap to the collector's constants. In practice the 64 KiB byte cap
applies first and is tighter: about 306 batch entries with counters.

### 3.2 What readiness publishes

`latest`, `last_success` and `last_failure` keep their previous top-level
keys: `run_id`, `mode`, `status`, `started_at`, `finished_at`, `exit_code`,
`failure_category`, `duration_seconds` and `providers`. `providers` now holds
at most one summary per logical provider, in run order (`ndvi`, then
`multi`):

| key | meaning |
|---|---|
| `batch_count` | batch children the collector reported for this provider |
| `succeeded_batch_count` | batches that exited 0 without timing out |
| `failed_batch_count` | every other batch: exit 1 (some fields failed), 2–4 (the child failed), or timed out |
| `timed_out_batch_count` | the failed batches that timed out |
| `status` | `succeeded` when no batch failed, `failed` when no batch succeeded, `partial` otherwise |
| `exit_code` | the highest batch exit code, which for any file the collector writes equals its own fold of the run |
| `timed_out` | true if any batch timed out |
| `counters` | the six counters summed over the batches that reported counters; null when none did |
| `counters_batch_count` | the number of batches `counters` covers |

**Partial providers.** A provider with some failed batches is `partial`.
The reader never counts a missing counter as zero. A child that crashed or
timed out leaves no counters, so `counters` covers only the batches that
reported them, and `counters_batch_count < batch_count` shows the gap. A
provider path that never started because the cycle stopped earlier is absent
from `providers`; the run's own `status`, `exit_code` and `failure_category`
say why.

Production's 22-entry file now publishes two summaries (§7):

- **NDVI:** 11/11 batches `succeeded`, 275 fields, 146 new observations.
- **Multi-index:** 11/11 batches `succeeded`, 275 fields.

### 3.3 Component states

| `collector.status` | when |
|---|---|
| `unconfigured`, `invalid` | no directory configured, or a relative one (unchanged) |
| `missing` | the latest status file does not exist |
| `rejected` + `reason` | the file exists but is `unreadable`, `oversized`, `malformed_json` or `invalid_contract` (new; 4cd8ea7 reported these as `missing`) |
| `running`, `succeeded`, `failed`, `cancelled` | the state of the latest run |
| `stale` | the time since `finished_at` (or `started_at` while running) exceeds the clamped threshold (unchanged) |

`required_for_api_readiness` is `false` in every state.

### 3.4 Compatibility

- **Batch-expanded files**, as production writes today, are accepted; the
  fixture is such a file.
- **Older one-entry-per-provider files** are accepted with `batch_count` 1,
  both with counters (e206973 … 79ddcaf^) and without them (b8eef17,
  f593c88).
- **Files written before TASK_223** that carry the collector-only labels
  `cloud`, `partial` or `cancelled` remain rejected, as TASK_223 decided. They
  now report `rejected/invalid_contract` instead of `missing`.
- **The writer is unchanged.** Aggregating in the writer would bound the file
  by construction, but the files already deployed would still need this
  reader. A bug in the collector's status persistence also turns a successful
  cycle into exit code 4. Changing the writer was not worth that risk here
  (§10).

## 4. Schema identity

### 4.1 One resolver

`services/migration_head.py` is the only source of the expected revision:

- **Reads the graph the migrations use.** It opens `backend/alembic.ini` next
  to the running code through Alembic's `Config` and
  `ScriptDirectory.from_config`, so it resolves exactly what
  `alembic upgrade head` resolves.
- **Does not touch the import path.** The ini tells the CLI to prepend `.` to
  `sys.path`; the resolver empties `prepend_sys_path` for its own read so a
  probe never rewrites a web process's import path (tested).
- **Requires exactly one head** matching `^[A-Za-z0-9._-]{1,128}$`. It also
  keeps the set of known revisions, which separates a database that is merely
  behind from one the code has never heard of.
- **Never raises.** Its problems are `no_head`, `multiple_heads` and
  `unreadable`, with no path or exception text.
- **Resolves once per process**, since a release is immutable. An unresolved
  graph is retried at most every 60 s. A transient read error heals without a
  restart, and a broken graph costs one read per minute rather than one per
  request. A double-checked lock ensures concurrent first probes resolve once
  (tested).

No revision literal appears in the health modules (tested). Nothing is
derived from `RELEASE_REVISION` or from Git.

### 4.2 Database component

The component always reports `status`, `migration_revision`,
`expected_migration_revision`, `revision_match`, `reason` and `latency_ms`.

| database / code | status | reason | HTTP |
|---|---|---|---|
| one revision, equal to the code head | `ready` | null | 200 |
| one revision, an ancestor of the head | `schema_mismatch` | `database_behind_code` | 503 |
| one revision the code does not know (a newer release, or foreign) | `schema_mismatch` | `database_revision_unknown_to_code` | 503 |
| no `alembic_version` row | `schema_mismatch` | `database_revision_missing` | 503 |
| more than one row | `schema_mismatch` | `database_revision_multiple` | 503 |
| code graph with no head, several heads, or unreadable | `schema_unverified` | `code_head_missing`, `code_head_multiple`, `code_head_unreadable` | 503 |
| unreachable, or the query failed | `unavailable` | `database_unreachable` | 503 |

`migration_revision` is sanitized; an unsafe value is published as
`unknown`. The probe runs two statements, `SELECT 1` and
`SELECT version_num FROM alembic_version LIMIT 2`. Health never runs a
migration.

### 4.3 Release identity (Part D)

`release_revision` stays sanitized: a path, a URL with credentials, or a
129-character value is published as `unknown`. The expected head does not
depend on it. With `RELEASE_REVISION` set to 66c1be8, 4cd8ea7, `unknown`,
empty, or garbage, readiness expects the same head. A database behind that
head is `not_ready` even when the release SHA looks right (tested).

## 5. HTTP contract

| endpoint | contract |
|---|---|
| `/health/live` | unchanged; probes no dependency and does not read the migration graph |
| `/health/ready` | 200 exactly when `components.database.status == "ready"`, otherwise 503 |
| `/health` | unchanged; always 200, with status `ok` or `degraded` |

## 6. Tests

All runs used the same environment:

- this machine and `C:\AgroSat\backend\venv` (Python 3.14.5, Alembic 1.13.1);
- PostgreSQL 16.14 with PostGIS, using the isolated database
  `agrosat_h0a_task228`, migrated to head;
- `AGROSAT_TEST_DATABASE_URL` set and `DATABASE_URL` unset (worktrees have
  no `.env`).

New suites, 156 tests:

| suite | tests | covers |
|---|---|---|
| `test_task228_collector_status_contract.py` | 88 | production 22-batch fixture and history file; order-independent aggregation; failed, fatal and timed-out batches; legacy files; malformed providers, counters, identity and timestamps; contradictory states; crafted bytes; byte cap; derived bound; staleness; real writer → reader round trip; sanitization; metrics series |
| `test_task228_schema_readiness.py` | 54 | resolver on real (edited) graphs; caching, backoff and concurrency; `sys.path`; database states; HTTP mapping; cache outage; liveness; release identity; AST web safety; no hardcoded revision |
| `test_task228_schema_readiness_postgres.py` | 14 | the real migrated database through the real route: match, behind, unknown, multiple, missing, unreachable, code ahead, code rolled back, branched graph, unreadable graph, cache outage, production collector fixture, collector failure stays non-blocking |

`test_task209_health_readiness.py` was updated in three places:

- its fake database now sits at the code head, because a database at `0005`
  counting as ready was the defect;
- the revision query now carries `LIMIT 2`;
- an oversized status file is now `rejected/oversized` instead of `missing`.

The fixture `tests/fixtures/task228_collector_latest_status_production_shape.json`
is production's status file from the 2026-09-25 01:00 UTC cycle on release
4cd8ea7, with only the run id replaced. It contains no path, host or
credential. The tests persist it through the collector's own `atomic_json`,
which reproduces the production file's size exactly (4 971 bytes).

| run | result |
|---|---|
| new suites, candidate | 156 passed |
| the same final test files on pristine 4cd8ea7 | collector suite 82 failed / 6 passed; PostgreSQL suite 10 failed / 4 passed; schema suite and updated TASK_209 suite fail to import (no resolver) |
| focused health, collector and contract suites, candidate | 214 passed |
| full backend, base 4cd8ea7 | 25 failed, 1186 passed, 1 skipped |
| full backend, candidate | 25 failed, 1342 passed, 1 skipped |
| PostgreSQL suites with `DATABASE_URL` set, base / candidate | 98 passed / 112 passed |

The same final test files, run against pristine 4cd8ea7, fail for these
reasons:

| count | cause on base |
|---|---|
| 10 | real collector output (production file, multi-batch files, the writer's own output, metrics) rejected as `missing` |
| 2 | database behind the code, or at an unknown revision, answered **HTTP 200 ready** |
| 7 | the reader raised (`RecursionError`, `ValueError`, unhashable `TypeError`, `OverflowError`), i.e. HTTP 500 |
| 19 | a malformed or self-contradictory file was published as a valid collector state |
| 42 | failed closed on base but was labelled `missing` or `unavailable` instead of `rejected` or `schema_mismatch` |
| 14 | the new published shape, fields, bound constant or resolver (including the 2 suites that fail to import) |

The 10 tests that pass on base are guards that must hold on both sides:

- stale-age arithmetic (3);
- terminal states the collector writes;
- an absent file reported as `missing`;
- the collector never being required for readiness;
- a database at the head answering 200;
- an unreachable database answering 503;
- a cache outage answering 200;
- a collector failure answering 200.

The full-suite failure and skip lists are identical in both runs:

- `test_task212_release_runtime` ×12 and
  `test_task209_release_rollback_artifacts` ×2: release tooling, known to fail
  on this machine.
- `test_h0a_freshness_postgres_contract` ×11: environmental.
  `recompute_satellite_freshness` refuses to run without `DATABASE_URL`. With
  it set, all 11 pass on both base and candidate (the 98 / 112 row).

The skip is `test_task218_release_manifest`, which runs only once the branch
exists on `origin`. Both runs took place before the push.

## 7. Live evidence

Two real uvicorn servers ran on loopback: pristine base 4cd8ea7 and this
candidate. Both used the isolated database with `ENVIRONMENT=production`,
Redis pointed at a closed port, and a collector directory holding the
production fixture. The database was then moved through real Alembic states:

| database state | base 4cd8ea7 | candidate |
|---|---|---|
| 0016 (head) | 200 ready; collector `missing` | 200 ready, `revision_match` true; collector `succeeded`, ndvi 11/11 batches (275 fields, 146 inserted), multi 11/11 batches (275 fields) |
| real `alembic downgrade -1` → 0015 | **200 ready** | 503 `schema_mismatch` / `database_behind_code` |
| real `alembic upgrade head` → 0016 | 200 | 200 |
| stamped `0017_task228_future_release` | **200 ready** | 503 `schema_mismatch` / `database_revision_unknown_to_code` |
| two rows (0015 and 0016) | 503 `unavailable` | 503 `schema_mismatch` / `database_revision_multiple` |
| restored to 0016 | 200 | 200 |

In every state `/health/live` answered 200 and `/health` answered 200 (`ok`
or `degraded`), and the unavailable cache never blocked readiness. The
database ended at 0016 and both ports were released.

## 8. Performance and web safety

- **Migration graph.** It is resolved once per process: about 0.31 s cold
  (the Alembic import and 17 migration modules), then about 0.4 µs per call.
  Only the first readiness request after start pays that cost.
- **Database.** The probe runs two constant, read-only statements, the second
  with `LIMIT 2`: about 0.57 ms p50 in process against local PostgreSQL. It
  uses no ORM and no lazy loading.
- **Collector.** At most three files are read, each bounded to 64 KiB + 1
  byte, and only known keys are visited. The 22-entry production file plus
  its history file parse in about 0.47 ms p50.
- **End to end over loopback.** Over 200 sequential `/health/ready` requests,
  p50 was 16.4 ms for both base and candidate (p95 17.4 ms and 19.3 ms). There
  is no regression.
- **Web safety.** The health modules contain no subprocess, Git, scheduler,
  provider or network call and no migration command (AST test).

## 9. Rollback, verification and side effects

**Rollback.** The change is code only: revert the TASK_228 commits. There is
no migration, no data change and no configuration change.

**Verify on the target after deployment:**

- `/health/ready` answers 200, with `database.revision_match` true and
  `expected_migration_revision` equal to `migration_revision`;
- `collector.status` is `succeeded` (or `stale`), with two provider
  summaries;
- `/api/operations/metrics` shows 12 `agrosat_collector_last_run_fields`
  series.

**Side effects to expect:**

- **503 whenever the database is not at the code head.** Release tooling must
  apply migrations before it switches traffic to a release.
  `Test-AgroSatReleaseHealth.ps1` (`Invoke-RestMethod`) will now fail on a
  release whose migrations were not applied, which is intended. A code
  rollback over an already-migrated database also answers 503
  (`database_revision_unknown_to_code`): rolling code back across a migration
  needs the database rolled back too.
- **New collector state.** `collector.status` has a new value, `rejected`,
  which comes with a `reason`.
- **Metrics return.** The collector field series, empty since the batching
  change, reappear.

## 10. Residual risks

1. **Status file growth.** The batch-expanded format grows by about 213 bytes
   per batch entry, so the 64 KiB cap is reached at about 306 entries. At
   `--batch-size 25` that is roughly 3 800 active fields; today there are 275.
   Beyond that, the collector reports `rejected/oversized` — visible, never
   shown as healthy. The durable fix, aggregating in the writer, was left out
   on purpose (§3.4).
2. **Partial counters in metrics.** `/api/operations/metrics` publishes the
   summed counters even when `counters_batch_count < batch_count`, because
   `services/metrics.py` was not changed. The readiness JSON states the gap
   explicitly.
3. **Legacy labels.** Status files older than TASK_223 that carry `cloud`,
   `partial` or `cancelled` stay rejected until the next cycle rewrites them.
4. **Process-lifetime cache.** The resolver does not notice migration files
   replaced under a running process. Immutable releases make this moot, and a
   restart re-resolves.
5. **Unresolved-graph backoff.** After a transient read failure, readiness
   can stay 503 for up to 60 s.
6. **Pre-existing, unchanged:**
   - The database probe has no connect or statement timeout.
   - On Windows, `os.replace` of a file that another handle holds open fails
     with `PermissionError` [WinError 5] (reproduced on this host). If a
     readiness probe reads a status file at the exact moment the collector
     replaces it, the collector records a status-persistence failure (exit
     code 4). The reader holds each handle only for one bounded read, the same
     window as 4cd8ea7. The fix belongs in the writer (retry the replace) in a
     later task.
7. **Not yet deployed.** Production keeps reporting
   `collector.status = "missing"` and an unchecked schema until this change is
   deployed; deployment is outside this task.
