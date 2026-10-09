# TASK_251 — Sentinel collector HTTP 429 resilience

**TASK_251_RESULT: PASS** (development only; NOT DEPLOYED; detector remains disabled)

| | |
|---|---|
| Branch | `task/251-sentinel-collector-429-resilience` (local, not pushed), base `f3458818f4dabca07b649fe5a502229219a8c848` |
| Code commit | `925f37c` fix(task251): pace Sentinel collector requests and survive HTTP 429 |
| Evidence | `C:\AgroSat_backups\TASK_251_COLLECTOR_RESILIENCE_20261009_101534` |
| Root cause source | `docs/TASK_250_RESULT.md` (E1–E5), `docs/TASK_249_RESULT.md` §1 (in the task_247 worktree) |

## 1. Root cause addressed

TASK_250 established the root cause.

- **Throttling.** The Statistical API throttles this account at about 21–25 requests/min. The exact rule is **UNKNOWN**, and it is not the documented 300/min.
- **Accidental pacing.** The NDVI stage stayed under the limit only because every insert waited out a failing Redis connect (about 2 s per child).
- **Speed-up.** Once TASK_246/248 stopped the spurious inserts, NDVI ran at 23–27 children/min.
- **Cycle-wide stop.** `run_ndvi_collection_cycle.py` made a 429 terminal, and `collect_satellite.py` then stopped the whole cycle, so the supporting-index stage never ran.

TASK_251 replaces the accidental pacing with explicit, global pacing, and turns a transient 429 into a bounded, recoverable retry.

| | Before (f3458818) | After (925f37c) |
|---|---|---|
| Request spacing | none; ~2 s per child after the rebuild (Redis side effect before) | ≥ 5 s between Statistical requests across all children and both stages (≤ 12/min) |
| HTTP 429 in NDVI | terminal: batch and whole cycle stop | retried in the child: Retry-After honoured, back-off 15/30/60/120 s, max 4 retries, 300 s budget, shared cooldown |
| HTTP 429 in indices | runner re-spawned the child after 2 s (no Retry-After) | same in-child policy; no re-spawn of an exhausted child |
| Persistent throttling | whole cycle stopped at the first 429 | `rate_limited` stops only that stage; the other stage still runs |
| Suspected quota / auth | `quota` stopped the cycle | `quota_suspected` (long Retry-After, quota wording) / `authentication`: provider-wide stop, as before |
| Endpoint of a failure | one identical log line for token and Statistical | `endpoint=oauth`, `endpoint=statistical`, … |
| Run report | exit code + counters | per-stage attempted/completed/skipped/blocked/failed, unfinished field IDs, `run_status`, 429 count, retries, cooldown, actual rate |

## 2. Files changed (code commit 925f37c)

| File | Change |
|---|---|
| `backend/services/provider_governor.py` (new) | `ProviderPolicy` (bounded settings), cross-process pacer + shared cooldown, bounded 429 retry (`post`), Retry-After parsing, sanitized diagnostics, run ledger/report, failure-category helpers, environment helpers |
| `backend/services/satellite.py`, `backend/services/satellite_collection.py` | token and Statistical POSTs go through `_provider_post`; a governor is used only when a collector process attached one (class attribute `provider_governor = None`) |
| `backend/services/satellite_safety.py` | `ProviderThrottleError` → `rate_limited` / `quota_suspected`; `ProviderPacingError` → `pacing_unavailable`; endpoint label in the sanitized summary |
| `backend/scripts/collect_ndvi.py`, `backend/scripts/collect_satellite_indices.py` | attach the governor after the real-service check; report `provider_resilience` in the child JSON |
| `backend/scripts/run_ndvi_collection_cycle.py`, `backend/scripts/run_multi_index_collection_cycle.py` | shared governor environment; stage-terminal categories (`rate_limited`, `quota`, `authentication`) stop the batch without re-spawning; aggregated `provider_resilience` |
| `backend/scripts/collect_satellite.py` | bounded `--provider-*` options (defaults = production policy); one `ProviderEnvironment` per run; stage-level stop on `rate_limited`; `stages`, `stage_stops`, `run_status`, `provider_resilience` in the run summary |
| `backend/services/collection_failure.py` | `rate_limited`, `quota_suspected`, `quota_or_rate_limit` → existing DB label `quota` (no constraint change) |
| `backend/tests/test_task251_collector_429_resilience.py` (new) | 48 tests, scenarios A–M plus simulation |
| `backend/tests/test_task251_collector_idempotency_postgres.py` (new) | 2 real-database idempotency tests |

Unchanged: no frontend change, migration, schema, statistics calculation, resolution, geometry, detector or FastAPI scheduler change. The Scheduled Task runner (`Invoke-Collector.ps1`) and `collector-task.json` are also untouched.

## 3. Pacing implementation

**Shared state.** Every collector child (NDVI and supporting-index alike) reserves its request slot in one state file, `<collector state dir>\provider_pacer_state.json`.
- Access is guarded by an exclusive OS file lock (`provider_pacer.lock`: msvcrt on Windows, fcntl elsewhere), bounded by a 30 s lock timeout.
- The state is written atomically (temp file + `os.replace`).

**Budgets.**

| Budget | Requests | Default spacing |
|---|---|---|
| `processing` | Statistical | 5 s |
| `oauth` | token | 1 s (its limit is UNKNOWN) |

- Each budget keeps `next_at` (queued reservations) and `cooldown_until` (set by any 429).
- A stage or child that continues after a 429 therefore waits the same cooldown.

**Activation.**
- `collect_satellite.py` exports the policy, pacer directory, run id and stage to its children (`AGROSAT_PROVIDER_*`) and restores the environment afterwards.
- A standalone cycle runner configures the default policy itself.
- The FastAPI process never attaches a governor, so web workers never sleep or retry.
- Nothing depends on Redis latency.

**Bounds.**
- A stored slot or cooldown further ahead than any policy could produce (corrupt state, wall-clock step) is discarded.
- No single wait exceeds the 900 s cap.
- A real two-process test proved that queued reservations are honoured. A first draft clamped them, and the test caught it.

## 4. Retry / back-off behaviour

- **Only HTTP 429 is retried.**
  - 401/403 → `authentication`; a 403 with quota wording → `quota_suspected`.
  - Other 4xx/5xx and transport errors are returned or raised unchanged, so the existing runner-level `--max-attempts` handles them.
- **`Retry-After`.** Sentinel Hub documents it in **milliseconds**: `3398` = 3.4 s (docs.sentinel-hub.com, rate limiting; sentinelhub-py divides by 1000). The OAuth endpoint uses RFC 9110 seconds or an HTTP date. Negative or unparsable values are ignored.
- **Delay.**
  - With `Retry-After`: max(Retry-After, pacing floor).
  - Without it: 15 s doubling, capped at 120 s.
- **Bounds.** Max 4 retries (configurable 0–10) and a 300 s total wait per request (10–1800).
- **Escalation.**
  - A `Retry-After` above 600 s, or provider quota wording, gives `quota_suspected`: not retried, provider-wide stop.
  - Exhausting retries or budget gives `rate_limited`.
- **Diagnostics.** Only allow-listed numeric header values are kept (`retry-after`, `x-ratelimit-*`, `ratelimit-*`, `x-processingunits-spent`), plus a provider error code token and a quota-keyword flag. No body text, token or credential is kept (tested).
- **Exact provider limit.** UNKNOWN; reported as `provider_limit: UNKNOWN`.

## 5. Supporting-index starvation prevention

- A recoverable 429 is absorbed inside the child; neither the batch nor the stage stops.
- A `rate_limited` failure (persistent beyond the budget) stops only the current stage's remaining batches. The next stage (supporting indices) still runs, and it starts after the shared cooldown.
- `quota` or `authentication` stop the whole run (provider-wide), as before. The unfinished fields of every stage are still listed.

## 6. Failure and resume semantics

| `run_status` | Meaning |
|---|---|
| `SUCCESS` | exit 0 and no unfinished field. Never reported for incomplete work |
| `PARTIAL` | exit 1 from ordinary field failures |
| `DEGRADED` | a stage was stopped by `rate_limited` / `quota` / `authentication` |
| `FAILED` | exit 2–4, including a late persistence failure |

- **Unfinished field IDs** come per stage from failed + unattempted fields, every field of a batch that never ran, and every field of a failed batch without a readable summary.
- **Resume.** The existing retry queues and rotation state are unchanged (failed fields enter the retry queue), and the next run covers all active fields again.
- **Database category.** `quota` (the run status column still separates degraded from failed). The readiness snapshot contract (status schema 2) is unchanged.
- **Overlap prevention** is the existing contract: the canonical lock plus Windows mutex, cycle locks, and the advisory lock in `begin_apply_run`. This is tested.

## 7. Simulated request rates

Source: `simulation/rate_simulation.md`. The simulation drives the real `ProviderGovernor` on a fake clock:
- one full 275-field cycle, NDVI then indices;
- one OAuth plus one Statistical request per child, with 1 s of child overhead;
- a provider that throttles Statistical requests above L per sliding 60 s.

It is a simplified model; the real limiter is UNKNOWN, and the legacy collector survived 77–175 requests in production.

| Provider limit / 60 s | Legacy (pre-TASK_251) | TASK_251 default |
|---|---|---|
| 300 (documented) | SUCCESS, 25.7 min, 22/min | SUCCESS, 45.8 min, ≤ 12/min, 0 × 429 |
| 25 | SUCCESS, 25.7 min | SUCCESS, 45.8 min, 0 × 429 |
| 21 (observed) | **FAILED**: stopped after 21 NDVI, 0 index | **SUCCESS**: 275 + 275, 0 × 429 |
| 15 / 12 | **FAILED** after 15 / 12 | **SUCCESS**, 0 × 429 |
| 8 (stricter, hypothetical) | **FAILED** after 8 | **SUCCESS**: 68 × 429, 68 retries, 70.2 min, ≤ 8/min |

## 8. Estimated full-cycle duration

- **Normal case.** 550 Statistical requests × 5 s = **≈ 46 min** (simulated 45.8 min). OAuth spacing and child start-up overlap inside the 5 s slots. Historical pre-TASK_247 cycles took 30–42 min.
- **Stricter limit.** About 70 min in the 8/min simulation.
- **Upper bound.**
  - Each request can wait at most 300 s on 429 retries.
  - A `rate_limited` stage stops after one exhausted budget.
  - The existing 6 h cycle timeout and 180 s field timeout still bound the run.
- **Schedule fit.** The 06:00 Asia/Tashkent schedule (01:00Z) finishes about 01:46Z.

## 9. Test results

| Lane | Result |
|---|---|
| Focused `test_task251_collector_429_resilience.py` | **48 passed** (A–M, simulation, secret-free diagnostics); the real cross-process pacing test was repeated 5 times, all passed |
| Collector-related existing suites (canonical collector, NDVI/multi cycle runners, collect_ndvi, TASK_246 statistics, write safety) | passed (332 in the focused regression) |
| Full backend suite | **1627 passed, 175 skipped, 0 failed** (skips = PostgreSQL suites without the test URL) |
| Ops suite (`ops/tests`) | **117 passed** (Windows task contracts, control plane, CI contract) |
| Isolated PostgreSQL lane (`agrosat_h0a_task240`, Alembic 0016, one process per file) | **13 suites, 175 passed**, including `test_task251_collector_idempotency_postgres` (a 429 followed by a retry stores exactly one `ndvi_records` row; a repeat run skips it; an exhausted budget writes nothing and reports `rate_limited`) |

Scenario mapping:
- A: `test_a_*`.
- B: `test_b_*`.
- C: `test_c_*` (4 tests).
- D: `test_d_*` (11 cases).
- E: `test_e_*`.
- F: `test_f_*` (fake clock + real processes).
- G: `test_g_*`.
- H: `test_h_*` and the runner test.
- I: `test_i_*` (3 tests).
- J: `test_j_*` + PG suite.
- K: `test_k_*` (3 tests) + existing TASK_246 tests.
- L: `test_l_*` (7 tests).
- M: `test_m_*` (4 tests).

## 10. Database / migration status

- **No migration**, no schema change and no constraint change. The new labels map onto the existing `ck_collection_runs_failure` vocabulary (`quota`).
- No production database access by any test; PostgreSQL tests used only `agrosat_h0a_task240`.

## 11. Production zero-write proof

`db/ZERO_WRITE_PROOF.json` compares two READ ONLY snapshots of `agrosat` taken at the start and end of TASK_251: **PASS 48/48**, with zero pg_stat insert/update/delete on every table.

Production stays on f3458818 / R20261008-task247 (`prod/release_pointer.txt`):
- Scheduled Tasks unchanged (`prod/scheduled_tasks.txt`; Sentinel next 2026-10-10 06:00 +05:00);
- `OBSERVATION_DETECTION_ENABLED=false` (`prod/detector.txt`);
- origin/main = f3458818, and no remote task/251 branch (`prod/origin_main.txt`);
- no collector run and no provider, OAuth or Catalog request.

## 12. Residual risks

1. **The exact provider limit is UNKNOWN.** The 5 s default is derived from production observations (0 throttles at ≤ ~15/min). If the account limit is lower, the retries and shared cooldown keep the cycle bounded and observable (DEGRADED), but slower.
2. **Not exercised live.** No provider request was allowed. The first production cycle after deployment is the live qualification: check `run_status`, `provider_resilience.ledger.request_rates` and 429 counts in `collector_summary.json`.
3. **Monthly quota.** Account-level monthly quotas (TASK_250: about 550 requests/day against a possible 10,000/month) are not addressed by pacing. A sustained quota block will surface as `quota_suspected` / DEGRADED. Usage reduction is a separate decision (TASK_250 §5).
4. **Run duration.** A cycle now takes about 46 min instead of about 8 min (the post-rebuild partial runs) or 30–42 min (historically). It ends around 01:46Z.
5. **No rotation of the start position.** The provider order and id-ordered batches are unchanged. If a stage were repeatedly `rate_limited`, its later fields would still be reached last; they are listed as unfinished and enter the retry queue for failed fields.
6. **OAuth.** OAuth tokens are still fetched per child, one per field; only the spacing is 1 s. Its limit is UNKNOWN, and its 429s are retried and labelled `endpoint=oauth`.

## 13. Deployment

**NOT DEPLOYED.** Deploying requires a separate release task through the control plane. The new options need no runtime-config change: their defaults are the policy. **DETECTOR REMAINS DISABLED.**
