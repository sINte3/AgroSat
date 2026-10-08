# TASK_246 — FIX B7: FIELD-LEVEL SENTINEL STATISTICS INTEGRITY

**TASK_246_RESULT: PASS** (development only, **NOT DEPLOYED**)

All 14 acceptance gates hold (table at the end).
- **Code.** The code and tests are on local branch `task/246-fix-sentinel-field-statistics`; the code commit is `9ae9c1e`. Nothing was pushed or deployed.
- **Production.** Production stays on `077e20a`, and the zero-write proof passed 48/48.
- **Live probe.** The bounded live probe used 4 of 4 requests. It proved that the corrected request returns multi-pixel field statistics. It also exposed one more flaw in the first design, which was fixed before the commit (§8).

## 1. Exact root cause
The full analysis is in `root_cause/ROOT_CAUSE.md` in the evidence bundle.

| | cause (077e20a) | proof |
|---|---|---|
| A | Both Statistical API paths sent the EPSG:4326 polygon with `resx/resy = 10` (NDVI, `services/satellite.py`) or `20` (indices, `services/satellite_collection.py`, documented as "meters"). Sentinel Hub reads resolution in **CRS units, i.e. degrees**, so a field became **one output pixel**. Four legacy tools carried private copies. The code has been present since `1c4bcdc` | TASK_245: 87 800/87 800 `sampleCount = 1`. The live corrected request returned 1406 pixels for field 246 (§8) |
| B | With one pixel, `valid_pixels_pct` is 0 or 100. The NDVI parser defaulted a missing `noDataCount` to 0 ("100 % valid") and a missing statistic to 0.0 | code reading; TASK_245: 13 767/13 767 NDVI rows have min = max = mean, std 0 and 100 % valid |
| C | **Duplicate rows.** The NDVI child called `get_ndvi_stats(geometry, D-14, D)` without an interval, so the default `P5D` applied. The parser stored the **start** of the latest usable 5-day bucket. That bucket moves with the daily window, so one acquisition `A` was re-stored under the dates `A-4 … A`, and `UNIQUE(field_id, captured_date)` cannot stop rows with different dates. The suspected cause, a re-stored "latest P1D interval", is disproven | Over the TASK_245 capture, uninterrupted repeat runs have ≤ 5 rows (one case of 6). For runs of exactly five rows, the **last** date is a real P1D acquisition day in **365/368** cases; the **first** date is one in only 1/368 (`root_cause/duplicate_rows_hypothesis_test.json`) |
| D | **Fake zeros.** The index evalscript used `isValid = ![1,3,8,9,10,11].includes(SCL)`. It never read the input `dataMask` and omitted SCL 0 (NO_DATA). A no-data pixel (bands 0) therefore counted as valid, and every index evaluated to **exactly 0.0** | TASK_245: 433 accepted rows with an exact 0.0, often all four indices together |
| E | The detector took the latest 15 rows as 15 scenes, so duplicates became persistence and a "new" scene | The new PostgreSQL test fails on 077e20a: persistence 3 for one acquisition (`tests/mutation_baseline_detector/result.txt`) |

## 2. Exact files changed
`git diff --stat 077e20a 9ae9c1e`: 18 files, +1561 / −363.

| file | change |
|---|---|
| `backend/services/sentinel_grid.py` (new) | shared metric request grid: UTM zone selection, polygon re-projection, metre resolution |
| `backend/services/observation_quality.py` | field-sample contract (`observed_pixel_count`, `field_pixel_count`, `evaluate_field_samples`, `field_sample_gate`), `MIN_FIELD_VALID_SAMPLES`, `MIN_FIELD_VALID_SHARE_PCT`, `MAX_NDVI_FIELD_MEAN` |
| `backend/services/satellite.py` | NDVI: pure payload builder on the grid, P1D default, `observed` output, honest two-pass parser, field-mean bound, gate in `fetch_ndvi_for_field_date` |
| `backend/services/satellite_collection.py` | index payload on the grid (20 m), expected pixels to the parsers, gate in `filter_by_quality`, mock footprint |
| `backend/services/satellite_indices.py` | NDVI mask contract (input dataMask + SCL 0), `observed` output, per-response field pixels, sample fields on every record |
| `backend/services/autonomous_monitoring.py` | `_detection_series` collapses duplicate rows (identical statistics ≤ 4 days); evidence `duplicate_rows_collapsed` |
| `backend/scripts/collect_ndvi.py` | write gate: field-sample verdict required |
| `backend/scripts/collect_satellite_indices.py` | write gate: field-sample verdict required |
| `backend/scripts/{dry_run_multi_index_single_field,preview_multi_index_batch,write_multi_index_batch,write_multi_index_single_field}.py` | builders delegate to the canonical payload; the batch writer moves P5D → P1D; both writers drop records without a verdict; self-test mocks updated; two hard-coded self-test line ranges re-anchored (all 4 `--self-test` runs PASS) |
| `ops/qualification/Run-ProgramR1LiveSentinelQualification.py` | statistical contract pinned to the metric grid (CRS, re-projected geometry, 10 m). `sameCrs` is replaced by `consistentCrs`: statistics on UTM, the Process raster in EPSG:4326 |
| `backend/tests/test_task246_sentinel_field_statistics.py` (new, 82 tests), `backend/tests/test_task246_detector_duplicates_postgres.py` (new, 3 tests) | regression tests (§9) |
| `backend/tests/test_sentinel_provider.py`, `test_collect_ndvi.py`, `test_program_r1_live_sentinel_qualification.py` | fixtures gain the provider footprint and realistic counts; the label assertion follows the new P1D date semantics |

**Not changed.**
- **Schema and migrations.** No migration and no schema change; Alembic is a single head at `0016_operational_command_center`.
- **Other code.** No frontend change, no scheduler, no runtime DDL, no API contract change.
- **Process API raster path.** It stays in EPSG:4326 with width/height. Pixel dimensions there are correct; there is no `resx`.

## 3. Spatial-resolution implementation
`services/sentinel_grid.metric_request_grid(geometry, resolution_m)`:
- **Input.** Only valid, non-empty EPSG:4326 Polygon/MultiPolygon fields are accepted (coordinates are checked to be lon/lat).
- **Projection.** The UTM zone of the centroid is chosen, and the **whole polygon** is transformed with `pyproj.Transformer` (`always_xy`) and `shapely.ops.transform`. It is never replaced by a centroid, bounding box or representative point; vertex count and geodesic area are preserved (tested within 0.5 %).
- **Request block.** The helper returns `bounds()` (metric geometry + `http://www.opengis.net/def/crs/EPSG/0/326zz`), `resolution()` (`resx = resy` in metres), `expected_field_pixels` and sanitized metadata.
- **Allowed resolutions.** Only 10 m (NDVI: B04/B08 are native 10 m) and 20 m (indices: B05/B8A/B11 are native 20 m). Anything else is an error, and there is **no EPSG:4326 fallback** (tested).
- **Shared use.** Both canonical builders call it (`build_ndvi_statistical_payload`, `satellite_collection.build_statistical_payload`), and so do all legacy tools through delegation.
- **Dependencies.** pyproj 3.7.2 and Shapely 2.1.2 were already pinned; no dependency was added.

## 4. CRS behaviour, including field 246
- **Rule.** The standard 6° zone, `zone = floor((lon+180)/6)+1` (180° → 60). The code is 326zz north and 327zz south. A point on a boundary belongs to the zone east of it. Latitudes are limited to [−80, 84]. Norway/Svalbard exceptions are not applied; the standard zone is still metric.
- **Field 246** (40.00338 N, 64.44036 E) → **EPSG:32641** (UTM 41N, 60–66 °E), not 42N. The live probe sent exactly `http://www.opengis.net/def/crs/EPSG/0/32641`, `resx = 10` for NDVI and 20 for the indices. Field 258 → 32641.
- **Boundary tests.** 59.999999 → 32640, 60.0 → 32641, 65.999999 → 32641, 66.0 → 32642, −180 → 32601, 180 → 32660, latitude −0.0001 → 32741. Out-of-range or non-finite input is rejected (`requests/crs_selection.json`).

## 5. Quality and sample-count behaviour
**Rule per output and per interval** (provider counts only).

| quantity | definition |
|---|---|
| valid pixels | index `sampleCount − noDataCount`. The mask is input `dataMask` AND SCL ∉ {0,1,3,8,9,10,11} |
| observed pixels | `observed` output `sampleCount − noDataCount` (mask: input `dataMask`, which is 0 outside the polygon and where there is no data) |
| field pixels | the largest observed footprint among the response's intervals (one grid), floored by the polygon area in pixels |
| `valid_pixels_pct` | valid / field × 100 |

**Field-level acceptance** (`evaluate_field_samples`) requires all of the following:
- every count is present and integral (missing → `missing_sample_counts`, never 100 %);
- the counts are consistent;
- the grid has **≥ 2 pixels** (`sampleCount ≤ 1` → `single_pixel_grid`, never field-level);
- the field has **≥ 10 pixels**;
- there are **≥ 10 valid pixels** (0.1 ha at 10 m, 0.4 ha at 20 m; the smallest active field of 0.52 ha has ≈ 52 / 13 pixels);
- valid share **≥ 50 %**, the existing analysis tier (`partial_field_coverage`).

**Selection and write path.**
- **Selection.** The NDVI parser returns the **latest accepted** interval. If none is accepted, it returns the latest observed interval flagged `field_samples_accepted = False`.
- **Write path.** Every write path (`collect_ndvi`, `collect_satellite_indices`, `fetch_ndvi_for_field_date`, `filter_by_quality`, legacy writers) goes through `field_sample_gate`, which **fails closed**. A rejected interval becomes a quality block, never a silent row.
- **Not the only test.** `std = 0` and `p10 = p90` are not used as rejection rules.

## 6. Missing, zero and extreme values
- **Missing or non-finite statistics.** A missing or non-finite mean, min, max or stDev makes the interval unusable; it never becomes 0.0. `NaN` and `Infinity` (string or float) are rejected. Missing percentiles stay `None`.
- **Missing footprint.** A missing `observed` output is not a field statistic, even when the polygon area is known.
- **No data vs real zero.** No-data is excluded by the mask, so a no-data day yields **no record**, not 0.0. A genuine 0.0 index from many valid pixels (NDMI 0.0 from 96 pixels) is kept and passes every gate (tested). There is no blanket rule against zero.
- **Admission ranges.**

| metric | admitted field mean | basis |
|---|---|---|
| NDVI | (0, 0.95] | existing `≤ 0` rule (water, shadow, no signal); new upper bound: a field mean above 0.95 needs near-zero red reflectance in every valid pixel, while closed crop canopies stay below ≈ 0.92 |
| SAVI, EVI, NDMI, NDRE | [−1, 1] | existing definitions; valid share ≥ 30 % at ingestion (existing) plus the field-sample contract |

- **Exact 1.000.** The parser reports an exact 1.000 unchanged (it never rewrites values), and the write path quality-blocks it (tested).

## 7. Duplicate-acquisition fix
- **At ingestion.** NDVI now uses `P1D` (`CANONICAL_AGGREGATION_INTERVAL`). Each interval is one UTC day of acquisitions, so `captured_date` is the acquisition day (semantics `utc_acquisition_day_of_p1d_interval`; multi-day intervals keep the "not acquisition date" label). The same acquisition seen by later runs keeps its date, so `ON CONFLICT (field_id, captured_date)` skips it. A newer acquisition gets a new date. This was tested end to end through `collect_ndvi.run`: insert, then skip, then insert. The legacy batch writer was moved from P5D to P1D too.
- **Detector.** Existing duplicate rows stay in production; this task deletes nothing. `_detection_series` collapses rows with identical statistics within 4 days into one scene. The first row keeps its id and date, so a later copy is a replay, not a new scene.
- **Detector proof.** In PostgreSQL, one acquisition stored three times gives persistence **1** and MODERATE severity, where 077e20a gave persistence 3 and EXTREME. Three distinct acquisitions still give persistence 3. A fourth copy counts as `replayed_scene`.
- **No schema change.** Application-level correctness at ingestion was sufficient.

## 8. Live provider probe
The probe ran with a hard counter: **4/4 requests, all HTTP 200** — OAuth, NDVI for field 246, indices for field 246, NDVI for field 258.
- **Provider and scope.** The provider is `cdse`. Credentials were read from `backend\.env` in memory and never printed or stored.
- **What was kept.** Field polygons were read in a READ ONLY transaction. Only counts and statistics were kept (`live_probe/live_probe.json`); there are no raw bodies, coordinates or tokens.
- **No writes.** No row was written and no collector was run.

**Corrected NDVI request** (field 246, 4.63 ha, EPSG:32641, 10 m, P1D, window 2026-09-24…10-07):

| day | grid pixels (sampleCount) | observed field pixels | valid | mean | stDev |
|---|---|---|---|---|---|
| 09-26 | 1406 | 316 | 316 | 0.8341 | 0.0809 |
| 09-28 | 1406 | 527 | 527 | 0.8266 | 0.0853 |
| 10-03 | 1406 | 527 | 527 | 0.6886 | 0.0652 |
| 10-06 | 1406 | 38 (partial swath) | 38 | 0.7539 | 0.0764 |

These meet every live requirement: `sampleCount` > 1, valid count > 1, min ≠ max, std > 0, metric CRS, metre-scale resolution. The indices request for 246 (20 m) returned 342 grid pixels, 142 field pixels on clear days, and real values from 11 pixels on 10-06 where 077e20a would have produced zeros. Field 258 returned 2704 grid pixels and 651 field pixels.

**What the probe changed** (fixed before commit, and the reason the probe was required):
1. **Constant mask.** A constant-mask "field" output returned the whole bounding box. The provider does not clip it to the polygon, so it was replaced by the `observed` footprint.
2. **Partial swath.** The 10-06 partial swath (38 of 527 field pixels) would have been taken as the latest observation; the 50 % share rule now rejects it.

**Re-parse with the final parser** (`live_probe/live_reverify_final_parser.json`):

| field | selected interval | valid / field pixels | mean NDVI | stDev | p10–p90 |
|---|---|---|---|---|---|
| 246 | **2026-10-03** | 527/527 (100 %) | 0.6886 | 0.065 | 0.592–0.747 |
| 258 | 2026-10-03 | 651/651 | 0.511 | — | — |

For field 246, the indices on 09-26 (59.9 %), 09-28 and 10-03 are accepted, and 10-06 (7.7 %) is blocked. The final index evalscript's `observed` output uses the construct that was live-validated in the NDVI request; the index request itself was not re-sent, because the budget was exhausted (residual R2).

## 9. Test results (committed head `9ae9c1e`, `tests/final_9ae9c1e`)
| lane | result | baseline on 077e20a (`tests/baseline_077e20a`) |
|---|---|---|
| backend | **1579 passed, 173 skipped, 0 failed** | 1497 / 170 / 0 |
| ops pytest | **117 passed** | 117 |
| PowerShell parse | 24/24 | 24/24 |
| PostgreSQL (one process per suite; isolated `agrosat_h0a_task240`) | **12 suites, 173 passed, 0 failed** | 11 suites, 170 |
| startup-safety guard | 6 passed | 6 |
| legacy `--self-test` × 4 | PASS | PASS |

**Focused tests** (all pass): `test_task246_sentinel_field_statistics.py` has 82 tests: A CRS and resolution; B multi-pixel counts (120/24 → 96 valid, 80 %), `sampleCount = 1` never field-level; C missing, NaN/Inf and zero; D NDVI extremes including 1.000; E duplicate acquisitions and persistence; F shared footprint; live-like partial-swath cases. `test_task246_detector_duplicates_postgres.py` has 3 tests.

## 10. Alembic
A single head, `0016_operational_command_center`, with no migration and no schema change; the isolated test DB reports `alembic current` = 0016. BLOCKED_SCHEMA_CHANGE_REQUIRED was not needed.

## 11. Production zero-write proof
- **Snapshots.** BEFORE 10:15:52Z → AFTER 10:52:14Z: **PASS 48/48** over **all 43 public tables** (`db/zero_write_proof.json`). The closing snapshot, after the report, is in `db/zero_write_proof_closing.json`.
- **Coverage.** Each table's row count, row md5 and status counts. This includes `ndvi_records` (17 402), `satellite_index_records` (37 357), `satellite_field_freshness` (1 375), `satellite_collection_runs` (45), `alerts` (215 active / 0 acknowledged), `autonomous_anomaly_candidates` (0), `field_inspections` (1), inspection results and evidence, agronomy plans, work items, verifications and events, `fields`, `crop_seasons`, and `users` with a full-row md5 that includes `last_login`.
- **Counters.** `pg_stat` DML deltas: none.
- **How production was read.** Production was read only in explicit READ ONLY transactions: snapshots and the two probe polygons. All tests used the isolated test database.

## 12. Production release SHA unchanged
- **Release.** `current-release.json` is unchanged (SHA-256 identical before and after). It points to `077e20acdb5da9de3d62d7877bb48ad770fa0f21`, R20260927-task241.
- **Services.** `/health/ready` is `ready`, on 077e20a, 0016, `revision_match` true. The same 7 AgroSat PIDs ran before and after (2592, 2624, 5880, 5888, 5696, 4192, 5352).
- **Git and detector.** origin/main is 077e20a, and `OBSERVATION_DETECTION_ENABLED=false`.
- **Not touched.** No restart, deploy, Scheduled Task, network, Cloudflare or runtime-configuration change.

## 13. Branch
`task/246-fix-sentinel-field-statistics`, local only, in worktree `C:\AgroSat_worktrees\task_246`, created from `077e20a`. The task_240 worktree and its untracked reports are untouched.

## 14. Final commit SHA
- Code: `9ae9c1e7a34fb9684c514636292a487ffbde5fe5` (`fix(task246): field-level Sentinel statistics integrity (B7)`)
- Report: the docs commit on top (`docs(task246): …`), given in the final response. **Not pushed.**

## 15. Evidence path
`C:\AgroSat_backups\TASK_246_SENTINEL_FIELD_STATS_FIX_20261008_151542`

| path | content |
|---|---|
| `preflight\` | identity, processes before and after, health, detector flag, release pointer |
| `root_cause\` | the analysis and the duplicate-row proof |
| `requests\` | sanitized before/after requests and CRS selection |
| `live_probe\` | sanitized probe results and the final-parser re-parse |
| `tests\` | baseline and final lanes, junit and the mutation run |
| `git\` | diff, stat and commit |
| `db\` | snapshots and zero-write proofs |
| `scripts\` | the scripts used |
| `EVIDENCE_MANIFEST.sha256` | the SHA-256 manifest |

The secret scan is clean.

## 16. Report path
`docs\TASK_246_RESULT.md` on the task branch, with a copy in the evidence directory.

## 17. Residual risks
1. **R1: History is unchanged** (by design; no cleanup or backfill).
   - **What remains.** Production still holds the single-pixel rows, the P5D duplicates, the fake 0.0 index rows and the 1.000 NDVI rows. The read-side acceptance contract still admits them, and the detector only collapses the duplicates.
   - **After deployment.** Time series and baselines would mix legacy point samples with field means; field 246 on 10-03 reads 0.638 as a point sample and 0.689 as the field mean. A legacy row can also occupy a date and make the new field-level observation for that date `skipped_existing`.
   - **Before relying on the data.** This needs a separate history task: re-collect or replace and quarantine the legacy rows. The detector must stay disabled until baselines are rebuilt.
2. **R2: Index request not re-sent live.** The final supporting-index evalscript (`observed` output) was not live-called, because the budget was spent. Its construct is identical to the live-validated NDVI one. The first post-deploy diagnostic run should confirm non-zero `observed` counts.
3. **R3: Boundary pixels.** The provider's polygon rasterisation counts about 14–19 % more pixels than area ÷ pixel area (it includes boundary pixels), so edge pixels contribute to field statistics. An inward buffer would reduce edge mixing; that is a separate decision.
4. **R4: Processing units.** Requests now cost about 500–3000 pixels per field at 10 m instead of 1, over 15 daily intervals. The provider quota and processing units should be checked before deployment.
5. **R5: More quality blocks.** `valid_pixels_pct` now means real field coverage, so cloudy or partial-swath acquisitions will be quality-blocked. This is intended, but collector `quality_blocked_count` will rise.
6. **R6: Detector heuristic.** Collapsing identical statistics within 4 days could merge two genuinely different legacy point samples that happen to share all 4-decimal statistics. The probability is negligible, and it under-counts rather than over-counts.
7. **R7: Legacy tools remain.** The legacy multi-index tools are now safe but are not part of the canonical pipeline; retiring them is a separate decision.
8. **R8: Same-day acquisitions.** Two acquisitions on one UTC day (orbit overlap) form one daily mosaic, so they give one logical observation per day. This is intended.

## 18. NOT DEPLOYED
**Not deployed:**
- no release, push or restart;
- no production row changed, deleted, backfilled or inserted;
- no collector run against production;
- detector disabled; no migration.

### Acceptance gates
| # | gate | status |
|---|---|---|
| 1 | root cause proven | ✅ A–E with code and data proof (§1) |
| 2 | metric request fixed | ✅ UTM 326zz/327zz, 10 m / 20 m |
| 3 | polygon full-field | ✅ the whole polygon is re-projected; vertices and area preserved |
| 4 | multi-pixel live | ✅ 1406-pixel grid, 527 valid, std 0.065 (§8) |
| 5 | sampleCount/noDataCount correct | ✅ provider counts, polygon-relative share |
| 6 | missing ≠ 0 | ✅ |
| 7 | duplicates not new scenes | ✅ P1D at ingestion and detector collapse (PostgreSQL proof) |
| 8 | shared footprint logic | ✅ one grid helper and one mask contract |
| 9 | detector disabled | ✅ `false`, 0 candidates |
| 10 | no production row changed | ✅ 48/48 |
| 11 | no process or release change | ✅ |
| 12 | full relevant tests pass | ✅ |
| 13 | no migration | ✅ |
| 14 | no unrelated change | ✅ (§2) |
