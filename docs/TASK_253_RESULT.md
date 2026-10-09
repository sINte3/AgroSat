# TASK_253 — Sentinel acquisition-aware request optimization

**TASK_253_RESULT: PASS**

This was isolated backend development; nothing was deployed. The feature is implemented, tested and **off by default**. Production, origin/main and the Scheduled Task are unchanged. No live Sentinel Hub, Catalog or OAuth request was made, and nothing was written to the production database.

**Main conclusion.** The default policy relies only on documented provider behaviour. It saves **≈ 14 % (observed replay) to ≈ 20 % (simulated)** of the Statistical requests. That is not enough to bring the ~16 500 requests/month under a 10 000/month quota.

The optional `footprint` policy saves **≈ 54–56 %**, with zero observations lost in every replay. It rests on two provider semantics that the documentation implies but does not state. It therefore needs a READ ONLY live qualification before anyone enables it.

## 1. Identity

| | |
|---|---|
| Worktree / branch | `C:\AgroSat_worktrees\task_253`, `task/253-sentinel-acquisition-aware-collection` (local only, not pushed) |
| Baseline | `3bcbcab3db0386f4013bf54189186e3a4b0f9fc9` (production R20261009-task252 = origin/main) |
| Code commit | `5a690cb` feat(task253): acquisition-aware preflight for the canonical Sentinel collector |
| Report commit | this file (the commit after `5a690cb`) |
| Evidence | `C:\AgroSat_backups\TASK_253_ACQUISITION_OPTIMIZATION_20261009_170601` |
| Untracked | `task_253.md` (the operator's task specification; task specifications are not tracked in this repository) |

### Files changed (`git diff --stat 3bcbcab 5a690cb`)

| File | Change |
|---|---|
| `backend/services/acquisition_gate.py` | **new**: discovery contract, checkpoint, fingerprints, decision, `GateRun` |
| `backend/scripts/collect_satellite.py` | gate wiring, CLI flags, `NO_NEW_ACQUISITIONS`, `not_called`, optional status metadata (+209/−13) |
| `backend/services/satellite.py` | `get_catalog_response` uses the attached governor in collector processes; the web path keeps its exact timeout (+18/−8) |
| `backend/services/provider_governor.py` | stage `catalog` added to `KNOWN_STAGES` (+2/−1) |
| `backend/tests/test_task253_acquisition_gate.py` | **new**: 90 deterministic tests |
| `backend/tests/test_task253_acquisition_gate_postgres.py` | **new**: isolated PostgreSQL suite (2 tests) |
| `backend/tests/task253_support.py` | **new**: Catalog-timeline fixture |

Nothing else changed:
- no migration, no frontend, no ops script, no runner, no Scheduled Task, no `collector-task.json`, no environment;
- no math module: `sentinel_grid`, `observation_quality` and `satellite_indices` are untouched, and `satellite.py` changed only in `get_catalog_response`.

## 2. Phase A — audit

Full text: `audit/SOURCE_AUDIT.md`.

- **Requests.** Every daily cycle starts 550 per-field child processes. Each child makes 1 OAuth and 1 Statistical request (NDVI 10 m, indices 20 m, P1D, `maxCloudCoverage` 80) over the full `[today−14, today]` window.
- **Idempotency.** The database decides it only after the request: `ON CONFLICT DO NOTHING` and `check_exists_by_key`.
- **Redundancy.** On days with no change, every one of those requests is redundant. Recorded cycles 2026-08-26..10-08 inserted rows on only 18 of 43 days.
- **Catalog.** The only Catalog code was the web endpoint, which was ungoverned and read a single page.
- **Window composition (key finding).** `observation_quality.field_pixel_count` takes the largest observed footprint among a response's intervals. So an acquisition *leaving* the window can change another interval's field-sample verdict without any new data. A departure is therefore not provably redundant in general.

## 3. Phase B — Catalog feasibility

Files: `feasibility/catalog_provider_documentation.md` (quotes and URLs, documentation pages only) and `feasibility/FEASIBILITY_DECISION.md`.

**Documented, and used:**
- endpoint POST `https://sh.dataspace.copernicus.eu/catalog/v1/search`, STAC API 1.0.0, collection `sentinel-2-l2a`;
- the same OAuth token as the Statistical API;
- `limit` ≤ 100; continuation by `context.next` / `rel=next` with `body.next`; "no next token … no more items exist";
- 0.01 PU per Catalog request for this area;
- 429 `Retry-After` in milliseconds;
- Catalog errors 400/403/500.

**Not documented (gaps):**
- that the Catalog lists exactly what the Statistical API processes, or when;
- whether a reprocessed product replaces an item, sits beside it or changes its id;
- whether item `geometry` is the data footprint;
- whether `eo:cloud_cover` is the value `maxCloudCoverage` filters on;
- whether a Catalog request counts toward the 10 000 requests/month quota (implied, not stated).

**Decision: feasible, not BLOCKED_FEASIBILITY.**
- Reading the Catalog completely is documented and enforced fail-closed.
- Every undocumented point can only make the Catalog behind, ahead of, or different from the Statistical index. Under this design each of those becomes a redundant collection or a delay of at most `reconcile_days − 1` days, never a lost or fabricated observation. The replay confirms this, including a Catalog that never lists anything (§6).
- The undocumented semantics are kept out of the default policy.

**Discovery geometry.** Measured READ ONLY on production: 275 active fields, extent 64.357–64.536 E × 39.913–40.048 N, 229 km², diagonal 21.4 km; `ST_Covers` 275/275. One bounding box of all fields plus 0.01° is used. It can only add items and never hide one that touches a field, it costs one page, and it costs the minimum 0.01 PU.

## 4. Phase C/D — what was implemented

Design: `design/ARCHITECTURE_AND_STATE_MACHINE.md`.

- **Activation.**
  - `collect_satellite.py --acquisition-gate` turns it on. `--acquisition-gate-reconcile-days N` (default 3, bounds 1..7, and lookback ≥ 2N) and `--acquisition-gate-policy strict|footprint` (default `strict`) tune it.
  - It requires `--apply --all-active-fields` and the rolling lookback; otherwise ContractError before any provider call.
  - Dry-run and diagnostic modes report "not evaluated" and never touch the checkpoint.
- **Placement.** One decision per cycle in the canonical collector, after `begin_apply_run` and the active-field read, before any Statistical request. NDVI and the indices share it.
- **Discovery.**
  - The configured preset's Catalog goes through the TASK_251 governor: stage `catalog`, processing budget with 5 s spacing, shared 429 cooldown, bounded retries, Retry-After in ms.
  - A 240 s discovery deadline caps every wait and clips the 30 s request timeout.
  - At most 10 pages of 100 items, 4 MiB each. Every item is validated (id, collection, UTC datetime inside the window, polygonal geometry). Repeats, disagreeing or non-advancing tokens, and `context.returned` mismatches all mean "incomplete".
  - An item's identity is the sha256 of id + all properties + geometry + bbox.
- **Checkpoint.** `<state_dir>/acquisition_checkpoint.json`.
  - Schema v1 with exact key sets and an inventory digest, ≤ 1 MiB / 1000 items.
  - No credentials, tokens, URLs or paths.
  - Atomic write (temporary file + fsync + replace), only under the canonical lock, the global mutex and the PostgreSQL advisory lock.
  - **Transitions.** `pending` is written before any Statistical request. After the cycle it becomes `verified` only on SUCCESS with a complete pre-collection inventory, or on a clean skip. Otherwise it becomes `invalid` with a reason.
  - A crash leaves `pending`, so the next cycle collects. Listing never marks an acquisition processed.
- **Decision.** `NO_NEW_ACQUISITIONS` only when every condition holds:
  - the discovery is complete;
  - the checkpoint is trusted;
  - the contract is identical: provider, collection, a processing fingerprint over 12 modules and the constants, indices, lookback, policy;
  - the scope is identical: IDs and `md5(ST_AsBinary(geometry))` of every active field;
  - the previous completed cycle is the checkpoint's chain run and it succeeded;
  - no runner retry queue is pending;
  - the reconciliation is not due;
  - no item is new, changed or removed;
  - no departure (`strict`), or a departure compensated by a hull-covering day within the cloud ceiling (`footprint`).
  Every failing condition is reported as a reason.
- **Failure classes.**
  - Token refused at the OAuth endpoint → `PROVIDER_STOP` `authentication`.
  - Suspected quota → `PROVIDER_STOP` `quota`.
  - In both cases: exit 1, DEGRADED, no Statistical request.
  - Any other doubt (Catalog 401/403 with a valid token, 429 after the budget, timeout, network, 5xx, malformed, incomplete, gate defect, the gate cannot be built) → the normal collection runs.
- **Status.**
  - **Skipped cycle.** `run_status` `NO_NEW_ACQUISITIONS` (never SUCCESS) and `stages {}`. The status file is `succeeded` with `providers []` plus an optional `acquisition_gate` object, present only when the gate is enabled. The DB row is `succeeded` / `not_called`; that value is already allowed by `ck_collection_runs_provider`, so there is no schema change. `counters.acquisition_gate` is set.
  - **Freshness.** It is recomputed from persisted observations only.
  - **Gate disabled.** Everything is byte-for-byte the TASK_252 behaviour: no new keys, no Catalog call, no checkpoint.

## 5. Tests

| Lane | Result |
|---|---|
| New gate tests `test_task253_acquisition_gate.py` (all 44 §11 cases plus edge cases) | **90 passed** |
| Full backend suite (`tests`, no database), rerun at commit `5a690cb` | **1751 passed, 177 skipped, 0 failed**. The 177 = baseline 175 + 2 new PostgreSQL tests, which skip without a database and pass in the PG lane. The non-TASK_253 count 1661 matches the TASK_252 baseline: 1662, minus `test_task218_release_manifest`, which skips or runs depending on the Git publication state |
| Ops suite (`ops/tests`) | **117 passed** (= baseline) |
| Isolated PostgreSQL lane, `agrosat_h0a_task253` at 0016, one process per file | **14 suites, 177 passed, 0 failed**, including `test_task253_acquisition_gate_postgres.py` 2/2, `test_task251_collector_idempotency_postgres.py` 2/2 and `test_task229_collector_finalization_postgres.py` 4/4 |
| Focused lanes at commit `5a690cb` (TASK_246 statistics, TASK_251, TASK_251A, canonical collector, NDVI/multi runners, provider presets, write safety, web startup, TASK_253) | **479 passed** |
| Baseline comparison | 3bcbcab exported with `git archive` and run the same way. Its only failures come from the export environment (no `.git`, no frontend/spec files). The candidate has none; skip lists differ only as explained above (`tests/*_rs.txt`) |

**Mapping of the §11 cases** (test names carry the numbers):

| Cases | Covered by |
|---|---|
| 1–3 | `test_01..03` |
| 4, 5, 16–18 | new, partial-area, delayed, older-unseen, reprocessed/changed/removed |
| 6–11 | scope, provider, processing-code, indices/lookback/policy |
| 12–15 | PARTIAL/DEGRADED/FAILED, retry queue, unreadable queue, a gate-disabled cycle in between |
| 19–21 | missing, 5 kinds of corruption, version, oversize |
| 22–27 | 429 recovered and persistent, Catalog 401/403, refused token, quota, timeout/network/deadline, 8 malformed shapes, non-JSON, 4 pagination failures, search area, footprint coverage cases |
| 28 | reconciliation at R = 2/3/7, R = 1, bounds, clock regression |
| 29–32 | checkpoint commit failure, crash after collection, crash during a skip, lost status publication, consecutive cycles, a separate interpreter |
| 33–37 | PostgreSQL suite: real writers, no duplicates, no rows/freshness moved on skip, `not_called`, `NO_NEW_ACQUISITIONS` |
| 38–40 | shared policy/deadline/stage in children after the gate, production arguments unchanged, CLI `--help`, ad-hoc scopes refused |
| 41–43 | real `SentinelHubService` + `ProviderGovernor` with `httpx.post` replaced: 1 OAuth + 1 Catalog per gated cycle, ledger stage `catalog`, 0 Statistical on a skip |
| 44 | the disabled gate changes nothing |

## 6. Quantitative evaluation

Full tables: `evaluation/EVALUATION.md`. Every decision is made by the real `decide()`.

- **OBSERVED.** Recorded production Statistical visibility of 43 daily cycles, 2026-08-26..10-08.
- **SIMULATED.** The 48 production acquisition days of 2026-06-24..10-09 (`db/geometry_probe.json`).
- **Not a production measurement.** No replay measures the gate in production: the gate never ran against the live provider.

Per 30 days, R = 3:

| | Baseline | `strict` observed | `strict` simulated | `footprint` observed | `footprint` simulated |
|---|---|---|---|---|---|
| Cycles skipped | 0 % | 14.0 % (6/43) | 20.4 % (22/108) | 53.5 % (23/43) | 55.6 % (60/108) |
| Statistical requests | 16 500 | 14 198 | 13 139 | 7 674 | 7 333 |
| Statistical saved / day | — | 76.7 | 112.0 | 294.2 | 305.6 |
| Catalog requests | 0 | +30 | +30 | +30 | +30 |
| OAuth requests | 16 500 | 14 228 | 13 169 | 7 704 | 7 363 |
| All-API reduction (Stat + Catalog + OAuth) | — | 13.8 % | 20.2 % | 53.3 % | 55.4 % |
| Estimated PU (87 PU/full cycle, 0.01/Catalog) | ≈ 2 610 | ≈ 2 246 | ≈ 2 079 | ≈ 1 214 | ≈ 1 160 |
| Observations lost / max extra delay | — | 0 / 0 days | 0 / 0 | 0 / 0 | 0 / 0 |

- **Sensitivity** (simulated, R = 3). All give **0 lost observations** and a maximum extra delay of **≤ 2 days = R − 1**:
  - a Catalog lagging the Statistical index by 1–20 days, including a Catalog that never lists anything;
  - a Catalog ahead of it by 1–4 days;
  - a publication delay of 1–3 days.
  With R = 7 the maximum extra delay is 5 days.
- **Reconciliation.** R = 3 forced no cycle beyond what R = 5/7 would, because acquisitions arrive every 1–3 days.
- **Worst case.** R = 1, or a Catalog that is always incomplete: the baseline + 1 OAuth + 1 Catalog per cycle (+0.2 %; at most +11 with the 10-page budget).
- **Runtime.**
  - A skipped cycle took a median 0.091 s for the database path on 275 synthetic fields, measured on the isolated database. Process start, one OAuth and one Catalog round trip come on top (not measurable here). Expect seconds instead of 2 763 s.
  - A collecting cycle takes 2 763 s plus about 10 s (one Catalog slot and one 5 s pacing slot).
- **Why `strict` saves little.** Under the 14-day window an acquisition leaves on about as many days as one arrives (~44 % each). `strict` collects on both, and only ≈ 15–20 % of days have neither.

## 7. Production safety evidence

- **Zero writes.** READ ONLY snapshots before the geometry probe (12:42Z) and at the end of development: **PASS 48/48, zero `pg_stat` insert/update/delete on every table**, including the natural notification tables. Files: `db/ZERO_WRITE_PROOF_geometry_probe.json`, `db/ZERO_WRITE_PROOF_whole_task.json`.
- **Production state** (`db/production_state_after.json`):
  - release R20261009-task252 / 3bcbcab;
  - runtime `collector-task.json` and `Invoke-Collector.ps1` last written at 13:23 local (TASK_252), with no `acquisition` option;
  - no `acquisition_checkpoint.json` in the production state directory;
  - Sentinel task Ready, next run 2026-10-10 06:00, bound to 3bcbcab;
  - `git ls-remote origin refs/heads/main` = 3bcbcab.
- **No live provider request.** All provider traffic in tests is simulated (`httpx.post` replaced or fixture Catalog). The documentation research read web pages only.
- **Detector.** Untouched (`OBSERVATION_DETECTION_ENABLED=false`); the tests patch it off. No pilot, alert, inspection or anomaly change.
- **Test database.** `agrosat_h0a_task253` (created by this task, PostGIS, head 0016) is kept idle for independent reproduction, following the earlier `agrosat_h0a_task2xx` practice. Drop it when no longer needed.

## 8. Residual risks and limitations

1. **Savings with the documented-only default are small** (≈ 14–20 %). Monthly Statistical use stays ≈ 13–14 k, above a possible 10 k/month quota.
   - The `footprint` policy (≈ 54–56 %) brings it to ≈ 7.3–7.7 k.
   - It must first pass a READ ONLY live qualification in a separate task:
     - check the item id format and property set;
     - check that item geometry is the data footprint;
     - check that `eo:cloud_cover` matches the dates the Statistical API returns;
     - check the request/PU accounting of one Catalog page.
2. **Catalog volatility.** If the live Catalog puts volatile values in item properties (for example an `updated` stamp that changes daily), every cycle will see `acquisition_changed` and collect. That is safe, but there are no savings. Qualification will show it.
3. **The provider documents no Catalog/Statistical consistency.** Any mismatch is bounded to a delay of at most R − 1 days, but **only while the forced reconciliation cycles succeed**. Consecutive failed cycles extend the delay; at most R = 7, which leaves one retry within the lookback.
4. **Pre-existing behaviour the gate does not change.**
   - **Silent insert failure.** A multi-index insert failure is counted as "skipped" by `collect_satellite_indices._insert_satellite_index_record`. A verified cycle could then hold a missing row that the gate only re-attempts at the next full cycle (≤ R − 1 days).
   - **NDVI keeps one interval.** NDVI stores only the latest accepted interval per cycle.
5. **Window-composition flips under `footprint`.** These are bounded by R. Their precondition (no full-area day left in the window) never occurred in 94 days of production history.
6. **Hypothesis only.** The CDSE FAQ says over-quota users are "moved to a slower interface". That could explain the unexplained ~21–25 req/min throttle seen since September. Worth checking in the CDSE dashboard.
7. **Untested against the live provider.** The 429 path (TASK_252 note), the Catalog response shape and the Catalog throttling remain untested against the live provider.

## 9. Next steps (not started)

- A qualification task with one READ ONLY live Catalog search over the production AOI.
- Then a rollout task through the control plane that adds `--acquisition-gate` (and, if qualified, `--acquisition-gate-policy footprint`) to the collector arguments.
- Neither is part of TASK_253.

**TASK_253_RESULT: PASS**
