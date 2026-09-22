# H0-A — Accepted Observation Contract Repair

**Task:** one atomic backend stabilization change — establish a single canonical
definition of an *accepted satellite observation* that distinguishes **unknown
cloud metadata** from **known excessive cloud cover**.

**Branch:** `task/h0-a-accepted-observation-contract-repair`
**Base:** `dc23f12` (`test(task221): scope browser navigation checks`)
**Alembic head:** `0016_operational_command_center` — **unchanged, no migration created**
**Production:** not touched. Nothing deployed, nothing merged, notification worker left disabled.

---

## 1. Root cause

Every consumer that had to decide "is this observation good enough to use?"
wrote its own SQL or Python predicate, and each of them collapsed *absent* cloud
metadata into *disqualifying* cloud metadata. The clearest instance, in
`backend/services/autonomous_monitoring.py`, drove the satellite freshness table:

```sql
COALESCE(n.valid_pixels_pct, 0) >= 60 AND COALESCE(n.cloud_cover_pct, 101) <= 30
```

`COALESCE(cloud_cover_pct, 101)` substitutes a sentinel that is *deliberately
above the maximum*, so a NULL cloud percentage is rejected exactly as if the
scene had been measured at 101 % cloud.

The canonical Sentinel-2 collectors never populate `cloud_cover_pct`. They apply
Scene Classification Layer (SCL) masking and record `valid_pixels_pct`; they do
not compute a separate scene-level cloud percentage. So the column is NULL for
every observation the platform actually produces, and the predicate rejected
**all of them** — not because of cloud, but because of a missing measurement.

The same conflation existed, in five more hand-written variants, in
`closed_loop_agronomy.py`, `executive_accountability.py`,
`operational_closure.py`, `agronomy_policy.py` and `operational_verification.py`.
Because each was written separately, they disagreed in detail (differing
valid-pixel tiers, differing NaN handling, differing NULL handling), and no
single place in the codebase stated what an accepted observation *is*.

### Why `valid_pixels_pct` is the real cloud signal

This matters for the correctness of the fix, not just its convenience.

SCL masking runs **before** `valid_pixels_pct` is computed. The mask discards
cloud (classes 8, 9), cloud shadow (3), thin cirrus (10), snow/ice (11),
saturated/defective (1) and no-data (0) pixels. `valid_pixels_pct` is then the
share of the field's pixels that *survived* that mask. A cloudy scene therefore
shows up as a **low `valid_pixels_pct`**, and it is already rejected on that
basis.

`cloud_cover_pct`, where a provider does supply it, is a coarse *scene-level*
metadata figure covering an area far larger than one field. It is a useful
secondary veto when present, and it is meaningless when absent. That is why the
repair is scientifically sound rather than a loosening of standards:

> **NULL cloud metadata means "not separately measured". It does not mean
> "cloud-free", and it does not mean "cloud-blocked".**

`valid_pixels_pct` remains **mandatory** at every acceptance site, at its
existing threshold. The repair removes a false rejection; it removes no
quality filtering.

---

## 2. Production evidence (diagnostics of 2026-09-22)

| Measurement | Result |
|---|---|
| `ndvi_records` with `cloud_cover_pct IS NULL` | **13,579 / 13,579 (100 %)** |
| `satellite_index_records` with `cloud_cover_pct IS NULL` | **32,317 / 32,317 (100 %)** |
| `satellite_field_freshness` rows not `FRESH` | **1,375 / 1,375 (100 %)** |
| False "satellite data unavailable" notifications generated | **1,375** |
| Observations rejected for *measured* excessive cloud | **0** |

Not one row in either observation table carries a cloud measurement, and not one
freshness row is healthy. The defect is total, not marginal: the platform
possessed 45,896 usable observations and reported that it had none.

The operator has **temporarily disabled the production notification task**
because of the resulting false alerts. That task remains disabled; this change
does not re-enable it.

---

## 3. The canonical contract

`backend/services/observation_quality.py` is the single definition. It is a pure
module — it imports only `__future__`, `dataclasses`, `math` and `re`, has no
database, network, ORM or settings dependency, and is enforced by test to stay
that way.

### Semantics

An observation is **accepted** when all of the following hold:

1. the index value is present, finite, and within `[-1.0, 1.0]`;
2. `valid_pixels_pct` is present, finite, within `[0, 100]`, and **at or above
   the consumer's minimum**;
3. **and** the cloud percentage is *either* absent/unavailable, *or* present,
   finite, and within `[0.0, 30.0]`.

It is **rejected as cloud-blocked** only when cloud cover was *actually
measured* and exceeds the maximum.

Malformed cloud metadata — NaN, ±Inf, negative, or above 100 — is classified
explicitly as `malformed` and rejected conservatively. It is **never** silently
downgraded to "unavailable", because a corrupt measurement is evidence that
something is wrong with the scene metadata, not evidence that no measurement was
attempted.

### Verdict table

| Case | value | valid % | cloud % | accepted | reason | cloud metadata |
|---|---|---|---|---|---|---|
| A | 0.62 | 100 | **NULL** | **yes** | `accepted` | `unavailable` |
| B | 0.62 | 100 | 12 | yes | `accepted` | `measured` |
| C | 0.62 | 100 | 80 | no | `rejected_cloud` | `measured` |
| D | 0.62 | **10** | NULL | no | `rejected_quality` | `unavailable` |
| E | **NaN** | 100 | NULL | no | `rejected_quality` | `unavailable` |
| F | 0.62 | **NULL** | NULL | no | `rejected_quality` | `unavailable` |
| G | 0.62 | 100 | **NaN** | no | `rejected_quality` | `malformed` |
| H | 0.62 | 100 | **-5** | no | `rejected_quality` | `malformed` |
| I | 0.62 | 100 | **150** | no | `rejected_quality` | `malformed` |
| J | 0.62 | 100 | **+Inf** | no | `rejected_quality` | `malformed` |

Case A is the repair. Cases C–J are the quality filtering that remains intact.

**One further behavioural change, disclosed:** a cloud percentage above 100 (case
I) previously reported `CLOUD_BLOCKED` in `agronomy_policy.quality()`, because
`150 > 30` was tested before anything checked that 150 is not a valid
percentage. It now reports `QUALITY_BLOCKED`, classified as `malformed`. Both
verdicts reject the observation, so nothing is newly accepted; the new answer is
simply the honest one — a value outside `[0, 100]` is corrupt metadata, not a
measurement of heavy cloud.

### Existing thresholds preserved, not flattened

The two valid-pixel tiers are genuinely different business rules and are kept
distinct, as required:

| Constant | Value | Used by |
|---|---|---|
| `MIN_VALID_PIXELS_FRESHNESS_PCT` | **60.0 %** | satellite freshness |
| `MIN_VALID_PIXELS_ANALYSIS_PCT` | **50.0 %** | closed-loop agronomy, executive accountability, verification |
| `MAX_CLOUD_COVER_PCT` | **30.0 %** | all sites |

Freshness age boundaries (`FRESH` ≤ 10 days, `AGING` ≤ 20 days, `STALE` beyond)
are untouched.

### API

```python
evaluate(*, value, valid_pixels_pct, cloud_cover_pct, minimum_valid_pixels_pct) -> QualityVerdict
is_accepted(...) -> bool                 # boolean shorthand
classify_cloud_metadata(cloud_cover_pct) -> str   # measured | unavailable | malformed
legacy_blocked_status(verdict) -> str | None      # None | CLOUD_BLOCKED | QUALITY_BLOCKED
accepted_observation_sql(*, value_column, minimum_valid_pixels_pct,
                         valid_pixels_column, cloud_column,
                         require_cloud_metadata=False) -> str
```

`accepted_observation_sql` renders the *same* rule as SQL so the persistence
layer and the Python layer cannot drift. It emits no bind parameters and
validates every column identifier against `^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$`,
so a caller cannot inject SQL through a column name.

Rendered forms:

```sql
-- freshness tier (60 %)
(n.mean_ndvi IS NOT NULL AND n.mean_ndvi >= -1.0 AND n.mean_ndvi <= 1.0
 AND n.valid_pixels_pct IS NOT NULL AND n.valid_pixels_pct >= 60.0 AND n.valid_pixels_pct <= 100.0
 AND (n.cloud_cover_pct IS NULL OR (n.cloud_cover_pct >= 0.0 AND n.cloud_cover_pct <= 30.0)))

-- analysis tier (50 %)
(n.mean_ndvi IS NOT NULL AND n.mean_ndvi >= -1.0 AND n.mean_ndvi <= 1.0
 AND n.valid_pixels_pct IS NOT NULL AND n.valid_pixels_pct >= 50.0 AND n.valid_pixels_pct <= 100.0
 AND (n.cloud_cover_pct IS NULL OR (n.cloud_cover_pct >= 0.0 AND n.cloud_cover_pct <= 30.0)))
```

Note that malformed cloud values are rejected in SQL too: PostgreSQL sorts
`NaN` above every other float, so `cloud_cover_pct <= 30.0` is false for `NaN`,
and the explicit `>= 0.0` lower bound rejects negatives.

---

## 4. Files changed

### Added

| File | Purpose |
|---|---|
| `backend/services/observation_quality.py` | the canonical contract (pure module) |
| `backend/scripts/recompute_satellite_freshness.py` | read-only-by-default freshness reconciliation CLI |
| `backend/tests/test_h0a_observation_quality_contract.py` | 41 unit tests |
| `backend/tests/test_h0a_freshness_postgres_contract.py` | 22 PostgreSQL-backed tests |

### Modified — acceptance consumers now delegate

| File | Before | After |
|---|---|---|
| `services/autonomous_monitoring.py` | `COALESCE(valid_pixels_pct,0)>=60 AND COALESCE(cloud_cover_pct,101)<=30` | `_FRESHNESS_NDVI_ACCEPTED` / `_FRESHNESS_INDEX_ACCEPTED` from the canonical builder |
| `services/closed_loop_agronomy.py` | `valid_pixels_pct BETWEEN 50 AND 100 AND cloud_cover_pct BETWEEN 0 AND 30` | `_ACCEPTED_NDVI_OBSERVATION` / `_ACCEPTED_INDEX_OBSERVATION` |
| `services/executive_accountability.py` | `valid_pixels_pct >= 50 AND cloud_cover_pct <= 30` | canonical predicates |
| `services/operational_closure.py` | hand-written `common_quality` string | canonical predicates, `require_cloud_metadata=True` (see §9) |
| `services/agronomy_policy.py` | hand-rolled `math`-based checks | delegates to `observation_quality.evaluate` |
| `services/operational_verification.py` | local threshold constants, `cloud <= 10` inline | constants sourced from the contract; `_cloud_within` helper for the *confidence* heuristic |

`autonomous_monitoring.py` additionally extracts `_FRESHNESS_COMPUTED_SQL`, a
single shared CTE used by both the write path (`refresh_freshness`) and a new
read-only `freshness_preview`, so the reconciliation dry run cannot disagree with
what the write would do.

### Modified — ingest gates: shared constant only

`services/satellite.py` and `services/satellite_indices.py` validate observations
at *write* time. That is a different and deliberately looser rule than acceptance
(it admits `valid_pixels_pct >= 30`, where analysis requires 50–60), and both
already tolerated NULL cloud correctly. Their predicates are **unchanged in
behaviour**; only the hardcoded `30` ceiling now comes from
`observation_quality.MAX_CLOUD_COVER_PCT`, so the number exists in exactly one
place. A test asserts they source the constant and still accept NULL cloud.

### Modified — one pre-existing test assertion corrected

`backend/tests/test_task220_agronomy_policy.py` asserted:

```python
({'cloud': None}, 'QUALITY_BLOCKED')
```

**That assertion encoded the defect as the specification.** It was changed, and
this is the only behavioural expectation in the repository that this task
reverses. It is replaced by `test_absent_cloud_metadata_is_neutral_not_blocking`,
which documents why, and the parametrized blocked cases are *extended* with
malformed cloud (`-5`, `150`, `NaN`) so the case count rises from 5 to 7.

---

## 5. Tests — exact commands and results

Environment used: PostgreSQL 16 + PostGIS 3.4, migrated to
`0016_operational_command_center`.

### Unit contract (no database)

```
cd backend
python3 -m pytest tests/test_h0a_observation_quality_contract.py -q
```
→ **41 passed**

Covers: required cases A–F, both threshold tiers, `valid_pixels_pct` still
mandatory, cloud classification, malformed-cloud conservatism, the SQL builder
(including identifier validation / injection attempts and the absence of bind
parameters), the legacy status mapping, the verification engine, the ingest-gate
guards, and a `SingleSourceOfTruth` class that greps the six consumers for
re-introduced hand-written predicates.

### PostgreSQL-backed contract

```
cd backend
export AGROSAT_TEST_DATABASE_URL="postgresql://…/agrosat_h0a_contract"
python3 -m pytest tests/test_h0a_freshness_postgres_contract.py -q
```
→ **22 passed**

The suite refuses to run against anything but an isolated database: the target
name must start with `agrosat_h0a`, and the literal name `agrosat` is rejected.
Without `AGROSAT_TEST_DATABASE_URL` the whole file skips, which is why the
regression skip count rises by 22.

### Affected suite

```
python3 -m pytest tests/test_task220_agronomy_policy.py \
  tests/test_task209_operational_closure_backend.py \
  tests/test_task209_executive_accountability.py \
  tests/test_satellite_write_safety.py \
  tests/test_task219_autonomous_monitoring.py \
  tests/test_h0a_observation_quality_contract.py -q
```
→ **187 passed**

### Full backend regression, compared against the base commit

Both runs executed in the same container, same interpreter, no database URL set.

| | Base `dc23f12` | This branch |
|---|---|---|
| passed | 1002 | **1044** (+42) |
| skipped | 4 | **26** (+22) |
| failed | 26 | **26** |

The two `FAILED` lists were diffed and are **byte-identical** — no failure
introduced, none masked. The 26 are the platform's known Windows/PowerShell-only
tests (release runtime, database-recovery artifacts, worktree junctions,
supervision ownership lock) plus
`test_multi_index_collection_cycle::test_29_empty_active_set_is_two`, which
needs a reachable database. That last one was re-run in a throwaway git worktree
at `dc23f12` and fails identically there, so it is pre-existing and
environmental.

Deltas fully accounted for: +42 passed = 39 new unit tests + 2 extra
parametrized malformed-cloud cases + 1 new NULL-neutrality test; +22 skipped =
the database-gated file.

---

## 6. PostgreSQL contract proof

Run against real PostgreSQL 16 / PostGIS 3.4 at revision
`0016_operational_command_center`, with production-shaped rows
(`satellite = 'Sentinel-2'`, `valid_pixels_pct = 100`, `cloud_cover_pct = NULL`):

```
Observations inserted: 3 NDVI + 12 multi-index

--- OLD predicate (COALESCE(cloud_cover_pct,101) <= 30) accepts ---
  ndvi_records            : 0
  satellite_index_records : 0

--- freshness BEFORE repair (production defect shape) ---
  NEVER_COLLECTED    15

--- dry-run preview (computed vs stored) ---
  computed=FRESH    stored=NEVER_COLLECTED  rows=15

--- freshness AFTER repair ---
  FRESH              15
  fields with >=1 FRESH index: 3
```

The old predicate accepts 0 of 15; the repaired contract accepts 15 of 15 on the
*same rows*. Tests additionally assert on the real database that: tenant identity
(`enterprise_id`) is preserved through the rewritten CTE; cloud 80, valid 10,
value 7.5 and malformed cloud (`-5`, `150`, `NaN`) are still rejected; the age
boundaries still map 0 → `FRESH`, 10 → `FRESH`, 15 → `AGING`, 20 → `AGING`,
25 → `STALE`; and a degraded run outcome still yields `PROVIDER_DEGRADED` rather
than a false `FRESH`.

---

## 7. Freshness reconciliation procedure

`satellite_field_freshness` is **derived** state that the collector already
rewrites on every cycle. The 1,375 stale rows will therefore heal on the next
collection cycle with no intervention at all. The CLI exists so the operator does
not have to wait for one, and so the outcome can be previewed first.

```powershell
# 1. Preview. Writes nothing. This is the default.
python backend\scripts\recompute_satellite_freshness.py

# 2. Apply, once the preview looks right.
python backend\scripts\recompute_satellite_freshness.py --apply
```

Properties:

* **dry run by default** — `--apply` is required to write anything;
* writes to **no table other than `satellite_field_freshness`**;
* **contacts no satellite provider** and imports no provider module (asserted by test);
* **creates no scheduler and no Scheduled Task**;
* refuses to run while a collection cycle is in flight (`status='running'` with a
  heartbeat inside 6 hours) and then takes the collector's advisory lock, so the
  two are mutually exclusive;
* the lock is `pg_try_advisory_xact_lock`, held in the *same transaction* as the
  write — a session-scoped lock is unreliable here because SQLAlchemy returns the
  connection to the pool on commit (see §9);
* **idempotent** — a second `--apply` reports `rows_changing: 0`;
* emits one JSON summary (`freshness_before`, `transitions`, `rows_evaluated`,
  `rows_changing`, `observations_written`, `freshness_after`, `provider_calls: 0`)
  with credential-shaped text redacted;
* exit codes: `0` ok, `2` contract/configuration, `3` lock contention, `4` operational.

No Alembic migration is involved. No observation row is created, modified or
deleted.

---

## 8. Notification recovery procedure — **not a blocker**

**Question asked:** can the existing reconciler resolve the 1,375 false
notifications automatically once freshness becomes healthy, without deleting
them?

**Answer: yes.** Verified end-to-end against real PostgreSQL, using the
production code path for both creation and retirement — nothing hand-inserted.

`STALE_ACTIVE_SQL` in `services/operational_notifications.py` keeps a freshness
notification alive only while its justification holds:

```sql
n.notification_type='external_source_unavailable' AND n.source_kind='freshness'
AND EXISTS (SELECT 1 FROM satellite_field_freshness s
            WHERE … AND s.status <> 'FRESH')
```

Once `status` becomes `FRESH` that `EXISTS` fails, the row is selected as no
longer actionable, and `_resolve_notification` performs:

```sql
UPDATE operational_notifications
SET status='resolved', version=version+1, updated_at=…, resolved_at=…
```

plus an `operational_notification_events` row with
`actor_key='system:reconciler'`, `reason='source_not_actionable'`.

**This is a versioned update with an audit trail. Nothing is deleted.** No
cleanup script is needed, none was written, and no manual deletion was added.

Procedure:

```powershell
# After freshness is healthy (§7). Dry run first.
python backend\scripts\reconcile_operational_notifications.py
python backend\scripts\reconcile_operational_notifications.py --apply
```

The reconciler is **bounded**: `DEFAULT_LIMIT = 200`, `MAX_LIMIT = 500`. So 1,375
rows need **3 passes at the maximum limit, or 7 at the default** — a test pins
those arithmetic facts so this runbook cannot drift from the code. Run `--apply`
repeatedly until `resolved` reports 0.

Proven by test, on a real database:

* while freshness is unhealthy, `would_resolve == 0` — the alert is still justified;
* after the repair, `would_resolve == 5` and `resolved == 5` for a 5-index field;
* every row still exists afterwards, with `status='resolved'`, a non-null
  `resolved_at`, an incremented `version`, and one `resolved` audit event each;
* a field that is *genuinely* stale (40 days old) still raises notifications
  after the repair and they stay open — the fix does not silence real staleness;
* a repaired field generates no new notification on the next pass.

Retirement keys off `satellite_field_freshness.status` alone, so it does **not**
require the disabled notification generator to be re-enabled first. Ordering is
therefore: repair freshness → run the reconciler → only then consider
re-enabling the worker (a separate decision, out of scope here).

---

## 9. Known remaining defects — recorded, deliberately not fixed

These were all identified during this work and left alone, in line with "no
opportunistic fixes".

**In-scope-adjacent, found by this task:**

1. **`operational_closure.py` still requires non-null cloud metadata.** This is
   the one acceptance site that keeps the stricter rule, via the documented
   `require_cloud_metadata=True` flag rather than a second hand-written
   predicate. It is forced by two CHECK constraints —
   `ck_action_verification_reference_shape` and
   `ck_action_verification_observation_shape` — which require a non-null cloud
   percentage whenever a value is present. Relaxing it therefore **requires an
   Alembic migration**, which is explicitly out of scope for H0-A. Consequence:
   the legacy TASK_209 verification path remains unable to close actions from
   NULL-cloud observations. This is the correct next migration if that path is
   to be retained.

2. **The collector's own advisory lock has the pooling weakness fixed in the new
   CLI.** `begin_apply_run` takes a *session-scoped* `pg_try_advisory_lock`;
   SQLAlchemy returns the connection to the pool on commit, so the lock can be
   released on a different backend than the one that will try to unlock it. My
   own test caught this pattern in my first draft of the reconciliation CLI
   (second `--apply` wrongly reported lock contention). The CLI now uses
   `pg_try_advisory_xact_lock`; the collector was **not** changed.

**Carried forward from the audit, untouched:**

3. **C2** — pixel-anomaly INSERT defect.
4. **C3** — `failure_category` mismatch between writer and reader.
5. **C4** — zone verification is not possible from field-average statistics.
6. **C5** — legacy lifecycle consolidation.
7. **C6** — the anomaly producer is unscheduled.
8. **C7** — 401 offline purge.
9. **C8** — application child-process ownership.
10. **C9** — readiness revision match.

Also untouched, as instructed: area/UTM repair, dashboard and frontend fixes,
management analytics, yield forecasting, financial exposure, Wialon, Telegram,
release framework redesign, CI redesign.

---

## 10. Rollback

**Code rollback only. There is nothing else to undo.**

* No Alembic migration was created; the head remains `0016_operational_command_center`.
* No schema object was added, altered or dropped.
* No observation row is written, changed or deleted by this change.
* The only row mutation available is the reconciliation CLI's rewrite of
  `satellite_field_freshness`, which is derived state the collector regenerates
  on every cycle. Reverting the code and running one collection cycle restores
  the previous derived values exactly.

```powershell
git revert <commit>   # or redeploy the previous release pointer
```

Notification rows resolved by §8 carry `status='resolved'` and an audit event. If
a revert were needed *after* resolution, those rows would be regenerated by the
reconciler on its next pass, because the freshness rows would again be
non-`FRESH`. No data is lost either way.

---

## 11. Production deployment plan

Not executed. Notification worker stays disabled throughout steps 1–4.

1. **Deploy the code.** No migration step. Confirm `alembic current` still reports
   `0016_operational_command_center`.
2. **Preview freshness.** `python backend\scripts\recompute_satellite_freshness.py`
   Expect `rows_changing` ≈ 1,375 and `transitions` showing
   `computed=FRESH stored=NEVER_COLLECTED`. Confirm `provider_calls: 0`.
3. **Apply freshness.** Same command with `--apply`. Re-run once; expect
   `rows_changing: 0` (idempotent). Alternatively skip steps 2–3 entirely and let
   the next scheduled collection cycle rewrite freshness itself.
4. **Retire the false notifications.** Run
   `reconcile_operational_notifications.py` dry, then `--apply`, repeating until
   `resolved` reports 0 (3–7 passes for 1,375 rows). Verify no row count dropped:
   `SELECT status, count(*) FROM operational_notifications GROUP BY 1`.
5. **Re-enabling the notification task is a separate decision** and is not part of
   this change. Do it only after step 4 shows a clean board, and only after
   confirming that genuinely stale fields still notify as intended.

Verification query after step 3:

```sql
SELECT status, count(*) FROM satellite_field_freshness GROUP BY 1 ORDER BY 1;
```

Expected: the overwhelming majority `FRESH`, with `AGING`/`STALE` only where
observations are genuinely old and `NEVER_COLLECTED` only for
(field, index) pairs that truly have no observation.
