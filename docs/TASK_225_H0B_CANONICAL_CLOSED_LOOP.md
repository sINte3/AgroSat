# TASK_225 — H0-B: one canonical closed loop in the backend

Branch `task/225-h0b-canonical-closed-loop`, based on `origin/main`
`66c1be8bab62832c0d94402b7a8f3ac7672fffe0`. Backend, API and tests only: no
frontend, no deployment, no scheduled-task or production-database change, and
no migration.

## 1. Outcome

The backend now supports one path, end to end, under PostgreSQL-backed tests:

```
accepted satellite observation (collector)
  -> deterministic signal (autonomous_anomaly_candidates, field scope)
  -> canonical inspection (field_inspections, TASK_217 workflow)
  -> finding -> submit -> review
  -> agronomy plan (TASK_220) -> work item -> execution evidence
  -> later accepted observation over the same verification scope
  -> collector reconciliation -> IMPROVED / NO_MATERIAL_CHANGE / WORSENED
  -> normal close, or rework / reopen
```

The acceptance case — a satellite-origin case that reaches `IMPROVED`
automatically and closes with the normal `close` operation, no override — is
`test_satellite_origin_case_is_verified_improved_and_closes_normally`.

## 2. Architecture before and after

**Before (66c1be8).** Three generations wrote overlapping state:

| generation | writes | problem |
|---|---|---|
| TASK_209 | `field_inspections` (pending/in_progress/completed), `inspection_results`, `corrective_actions`, `action_verification_requests` | still live; `POST /api/field-inspections/{id}/result` could move a *canonical* in-progress inspection to `completed`, a state outside the TASK_217 machine |
| TASK_217 | inspection workflow (new/assigned/in_progress/submitted/confirmed/rejected/cancelled) **and** its own corrective-action lifecycle | a second remediation state machine next to TASK_220 |
| TASK_220 | `agronomy_plans`, `agronomy_work_items`, `agronomy_verifications`, `agronomy_events` | satellite-origin plans could never verify (C4) |

Four modules wrote `INSERT INTO field_inspections` independently; the pixel
path omitted the 0013 columns (C2). The autonomous producer had no upstream:
candidates came only from `pixel_anomalies`, which nothing produces in
production (the numeric pixel provider is fail-closed) (C6). The Operational
Center took "blocked" from `corrective_actions`, and reported any closed plan —
including an override closure after `WORSENED` — as `improved_closed`. The web
process could call Sentinel and write `ndvi_records` synchronously (C13).

**After.**

| concern | canonical owner | entry points |
|---|---|---|
| inspection lifecycle | `services/anomaly_inspections.py` (TASK_217 states) | `/api/anomaly-inspections/*` |
| the one inspection INSERT | `anomaly_inspections.insert_inspection` | HTTP create, pixel-anomaly promotion, candidate promotion (automatic and operator), plan re-inspection |
| remediation lifecycle | `services/closed_loop_agronomy.py` (TASK_220) | `/api/agronomy-plans/*`, collector reconciliation |
| signal production | `autonomous_monitoring.detect_observation_candidates` + `promote_automatic_candidates` | collector apply cycle (flag-gated), `scripts/preview_observation_candidates.py` (read-only) |
| verification scope | `closed_loop_agronomy.verification_scope` / `scope_comparison` | frozen into `agronomy_plans.input_snapshot` at draft |
| current status | `services/remediation_status.py` | Operational Center, executive backlog, inspection queue summary |
| satellite collection | standalone collector only | `/api/ndvi/{id}/refresh` is 410 |

Only `services/anomaly_inspections.py` contains `INSERT INTO field_inspections`
(pinned by `test_only_the_canonical_service_inserts_inspections`).

## 3. Defects, base evidence, change

Base evidence comes from running the new suites unmodified against a detached
checkout of 66c1be8 with the same venv and database, plus one scripted
reproduction for C4 (§3.2). Final base totals are in §7.

### 3.1 C2 — pixel anomaly -> inspection

Base: `services/pixel_anomalies.create_inspection` inserted only the pre-0013
columns. PostgreSQL rejected the row (`source_kind`/`source_reason` are NOT
NULL since 0013) and the IntegrityError was converted into
`409 Concurrent anomaly inspection conflict` — three base failures carry exactly
that message.

Change:
- the pixel path delegates to `insert_inspection` with a complete `pixel_ndvi`
  snapshot: provider, `item_id` = the observation record the pixel run analysed
  (`ndvi_record:<id>`), acquisition date (UTC midnight), index, zone geometry
  copied bit-for-bit (hex EWKB), field geometry hash, priority from severity;
- the zone's own median current/comparison values are now persisted in the
  anomaly provenance by the pixel algorithm; a zone recorded without them is
  refused with a typed 409 instead of borrowing the field mean;
- only a unique violation (`23505`) is treated as a concurrency or replay
  outcome; NOT NULL / CHECK / FK violations are re-raised (a contract defect
  is no longer disguised as a conflict);
- a zone already covered through the candidate path is not inspected twice,
  and vice versa.

### 3.2 C4 — satellite-origin verification

Base reproduction (scripted on 66c1be8): a candidate whose zone is the whole
field, promoted by the base operator path, driven to pending verification, then
an accepted observation +0.18 above baseline:

```
BASE promoted inspection: {'source_kind': 'manual', 'source_zone': None, 'source_provider': None}
BASE verification: INCONCLUSIVE delta 0.18 scope field
BASE normal close: 409 An improved eligible observation is required; use an explicit override with reason
```

The plan requires zone identity (`candidate_id` is set) but the observation
reader returned field means without a `zone_key`, so every satellite-origin
plan was INCONCLUSIVE forever and could only be closed by override.

Change — an explicit verification scope, frozen at draft in
`input_snapshot.verification_scope`:

| kind | when | verification |
|---|---|---|
| `field` | the source zone **is** the field polygon: a field-scope candidate (zone key = detection-time geometry hash) or an inspection zone `ST_Equals` the field | baseline and post are the persisted field means, labelled with the zone key of the field's current geometry; comparable only if that key equals the frozen one |
| `subfield` / `point` | a smaller zone or a point | no stored zonal statistic exists: typed `INCONCLUSIVE`, reason `zone_statistics_unavailable` |
| `unscoped` | manual/alert source without geometry | unchanged field-level verification |

`scope_comparison` records why a measurement is or is not comparable
(`comparable`, `zone_geometry_changed`, `baseline_missing`,
`baseline_outside_scope`, `post_outside_scope`, `zone_statistics_unavailable`),
and the measurement stores both zone keys and both geometry hashes. The r3-f-v1
thresholds are untouched (0.05 material change, 7-day wait, accepted-quality
rules from `observation_quality`). A field mean is never presented as a
sub-field zone mean; a boundary edit after detection makes the case
non-comparable instead of silently re-scoping it. Plans drafted before TASK_225
(none exist in production) keep the old conservative behaviour.

### 3.3 C5 — one remediation lifecycle

Base evidence: `POST /api/field-inspections/{current}/result` returned 200 and
completed a canonical in-progress inspection; the canonical `PUT .../finding`
returned 200 on a legacy row; cancelling a submitted inspection that roots a
live plan succeeded; a legacy pending row had no way out except the legacy
workflow; `POST /api/field-inspections` still created legacy work.

Classification:

| class | endpoint | result |
|---|---|---|
| A canonical, retained | `/api/anomaly-inspections` (create, queue, detail, assignment, start, finding, submit, review, cancel, photos) | canonical rows only; every transition except `cancel` answers 409 for a legacy row |
| A canonical, retained | `/api/agronomy-plans/*` | unchanged API; scope-aware verification |
| B read-only compatibility | `GET /api/field-inspections`, `GET /api/field-inspections/{id}`, `GET /api/field-inspections/{id}/timeline`, `GET /api/field-inspections/{id}/closure`, `GET /api/operational-actions`, `GET /api/anomaly-inspections/{id}` (historical `actions`) | history stays readable |
| C retired, 410 | `POST /api/field-inspections`, `PATCH /api/field-inspections/{id}`, `POST .../start`, `POST .../complete`, `POST .../cancel` | replacement: `POST /api/anomaly-inspections`; legacy close-out via `POST /api/anomaly-inspections/{id}/cancel` |
| C retired, 410 | `POST /api/field-inspections/{id}/result`, `.../evidence`, `.../actions` | replacement: canonical finding/submit, photos, `POST /api/agronomy-plans` |
| C retired, 410 | `PATCH /api/operational-actions/{id}`, `.../close`, `.../reopen`, `.../verification-requests`, `POST /api/verification-requests/{id}/resolve` | replacement: plan work transitions, plan transition, collector verification / `.../reevaluate` |
| C retired, 410 | `POST /api/anomaly-inspections/{id}/actions`, `.../actions/{id}/transition`, `.../actions/{id}/verify` (TASK_217 corrective actions) | replacement: `/api/agronomy-plans` |
| C retired, 410 | `POST /api/ndvi/{field_id}/refresh` | replacement: the standalone collector |
| D quarantined | `scripts/process_pixel_anomalies.py` | not canonical detection; `--write` stays fail-closed because the numeric Sentinel pixel provider is not configured; kept for fixture dry-runs |
| D dead library | `services/operational_verification.py` | no production caller after retirement; kept for its tests and history |

Every 410 authenticates first, carries `{"code": "lifecycle_endpoint_retired",
"endpoint", "replacement", "message"}` and runs no statement. No compatibility
adapter was retained: adapting the legacy create would have produced canonical
rows invisible to the legacy list (it filters `source_kind='legacy'`), i.e. two
half-working vocabularies. The only transition the canonical API offers a legacy
row is the reasoned close-out: `cancel` from `pending`/`in_progress`, with
`expected_version`, manager/admin, and an audit event carrying
`origin=legacy_closeout`. Rejecting or cancelling an inspection that roots a
live plan is refused (409) in both directions.

The retired service write functions were removed (`field_inspections.create/
update/transition`, the eight `operational_closure` writes, the three TASK_217
action functions), so nothing can re-wire them by accident.

### 3.4 C6 — automatic signal producer

Base: no production path produced candidates from collected observations;
`autonomous_anomaly_engine.assess_candidate`/`robust_signal` had no production
caller (base run: 17 acceptance tests stop at
`no attribute 'detect_observation_candidates'`).

Change — `detect_observation_candidates(run)`, called by the collector after
`refresh_freshness` and before promotion:

- input: accepted NDVI observations (`observation_quality`, TASK_219 60 % tier)
  per active field, at most 15 per field within 120 days (one LATERAL query);
  supporting SAVI/EVI/NDMI/NDRE only for a field that already shows a signal;
- decision: `persistence` = consecutive trailing scenes that are each a robust
  drop against the history *before* them (window 3); `assess_candidate` on the
  pre-drop baseline (≤12 scenes, ≥5 required); supporting agreement = other
  indices whose nearest scene within ±3 days is also a robust drop. The drop
  predicate (`MIN_DROP_MAGNITUDE`, `MIN_ROBUST_DEVIATION`) is now defined once
  in the engine;
- output: a `NEW` field-scope candidate (geometry `ST_Multi(field)`, evidence
  with baseline and persistence record ids, supporting detail, quality and
  scope limitation), idempotent on the candidate unique key; a field with an
  active case for the same zone is skipped;
- promotion (`promote_automatic_candidates`, per-candidate savepoint, existing
  cap 20 and >10 % spike guard) and operator promotion both open the inspection
  through `insert_inspection`: `source_kind=pixel_ndvi`, complete snapshot,
  zone = candidate geometry, client request id `candidate-<id>`, candidate
  transition and audit event (`origin`);
- no provider call, no FastAPI scheduler, no new Windows task.

The collector runs detection only when `OBSERVATION_DETECTION_ENABLED=true`
(default **false**). The first enabled cycle records every *current* robust
NDVI drop at once, seasonal harvest and defoliation drops included; enabling is
a deployment decision to take after `scripts/preview_observation_candidates.py`
(read-only, same decision function) has been reviewed against live data. A
monitoring-step failure now still terminalises the collection run (the run row
is never left `running`).

### 3.5 C13 — web-process satellite collection

Base: `POST /api/ndvi/{id}/refresh` (`async def`) called the provider and wrote
`ndvi_records` synchronously on the event loop, outside any collection run
(base test: `AssertionError: provider must not be called`). Now 410 with no
provider or database dependency. `GET /api/ndvi/{id}/history|latest` are plain
`def`.

### 3.6 Blocking synchronous work (touched surface only)

`get_current_user`/`get_current_active_user` executed a synchronous SQLAlchemy
query inside `async def`, i.e. on the event loop, for every authenticated
request. They are plain `def` now (threadpool). The two photo upload handlers
(`/api/anomaly-inspections/{id}/photos`, `/api/agronomy-plans/{id}/work/{item}/evidence`)
read the upload synchronously and run as `def`. Other `async def` handlers
(`agronomic_risk`, `enterprises`, `ndvi_raster`, `satellite_data_quality`,
`satellite_indices`) are outside this task and are listed as residual.

## 4. Canonical status projection

`services/remediation_status.py` defines one decision table over the canonical
inspection and its current plan, as SQL (`inspection_status_sql`) and as Python
(`inspection_status`); a PostgreSQL test evaluates the SQL over all 729
combinations and compares it with the Python table.

| state | meaning |
|---|---|
| `needs_inspection` | new/assigned (or legacy pending) inspection; candidate or alert without inspection |
| `inspection_active` | in progress |
| `awaiting_review` / `awaiting_decision` | submitted / confirmed without a plan |
| `plan_active` / `work_active` | plan draft or approved / in progress |
| `awaiting_satellite_verification` | pending verification, PENDING_DATA or TOO_EARLY |
| `verification_blocked` | CLOUD/QUALITY/PROVIDER blocked or INCONCLUSIVE |
| `improved_awaiting_closure` / `improved_closed` | IMPROVED, not yet closed / closed |
| `not_improved` / `reopened` | NO_MATERIAL_CHANGE or WORSENED / plan in rework |
| `closed_without_improvement` | closed without an IMPROVED verification (override) |
| `rejected`, `cancelled`, `inspection_closed`, `data_unavailable` | terminal or data states |

Re-pointed readers: Operational Center queue/summary/case (new field
`remediation_status`; `blocked` no longer reads `corrective_actions`; an
override closure is no longer `improved_closed`; the candidate case detail no
longer selects a non-existent `provenance` column), the inspection queue
summary (`active_actions`/`verification_due` from plans), the executive
backlog, owner rows and accountability lists (work items and plans), and the
irrigation panel's `active_inspection`. Windowed executive analytics
(cycle times, verification outcomes) still describe the TASK_209 history and are
labelled as such in `limitations`; re-baselining them is H1.

## 5. Schema decision

No migration. The 0013–0016 schema represents the design:
- satellite-origin inspections use the existing `pixel_ndvi` source kind, whose
  CHECK requires the complete scene/value/geometry/location snapshot that the
  candidate and pixel paths now provide; provenance to the candidate is the
  existing `autonomous_anomaly_candidates.inspection_id` (unique) and
  `agronomy_plans.candidate_id`;
- the verification scope and its comparison live in the existing JSONB
  (`input_snapshot`, `baseline`, `measurements`);
- verification stays NDVI-based (`agronomy_verifications` record FKs point at
  `ndvi_records`), and detection is NDVI-primary with the other indices as
  supporting evidence, so equivalent metrics are compared;
- zone medians of pixel anomalies live in the existing `provenance` JSONB.

Alembic head stays `0016_operational_command_center` (single head). Nothing to
upgrade or downgrade.

## 6. Legacy data

Read-only production inspection (single READ ONLY transaction, aggregate counts
only), alembic `0016_operational_command_center`:

| table | rows |
|---|---|
| `field_inspections` | 1 (`legacy`, `pending`) |
| `inspection_results`, `inspection_evidence`, `corrective_actions`, `action_verification_requests`, `operational_audit_events` | 0 |
| `pixel_anomalies`, `pixel_anomaly_runs`, `autonomous_anomaly_candidates` | 0 |
| `agronomy_plans`, `agronomy_work_items`, `agronomy_verifications`, `agronomy_events` | 0 |
| `ndvi_records` / active fields | 13 986 / 275 |

No row is rewritten or deleted. The one active legacy inspection stays
readable, appears in the projection as `needs_inspection`, and can be closed
out with a reason through the canonical cancel.

## 7. Tests

Isolated database `agrosat_h0a_task225` (PostgreSQL 16.14, PostGIS, migrated to
0016), venv `C:\AgroSat\backend\venv` (Python 3.14.5), same environment for base
and candidate. `AGROSAT_TEST_DATABASE_URL` points at it; suites refuse any
database whose name does not start with `agrosat_h0a`.

New suites:

| suite | tests | covers |
|---|---|---|
| `test_task225_signal_to_inspection_postgres.py` | 14 | producer, central anomaly->inspection acceptance, replay, quality rejection, stale/short history, spike guard, operator promotion, preview parity, preview CLI, pixel path through the real INSERT, pixel replay/conflicts, tenant, pixel/candidate de-duplication |
| `test_task225_closed_loop_postgres.py` | 14 | end-to-end over HTTP, manual unscoped case, UNCHANGED, WORSENED/rework, reopen, bad-quality post, missing post, boundary edit, sub-field zone, override not improved, concurrent close, concurrent promotion, projection SQL=Python, candidate case detail |
| `test_task225_lifecycle_boundaries_postgres.py` | 12 | every retired write 410 with no mutation, legacy result vs current inspection, NDVI refresh never reaches the provider, legacy history readable, canonical transitions refuse legacy rows, legacy close-out, live-plan guard, tenant reads non-enumerating, tenant writes, client enterprise ignored, admin global, viewer read-only |
| `test_task225_contracts.py` | 39 | one INSERT, 0013 columns, snapshot validation, unique-violation classification, no retired writers, readers, projection vocabulary and table, no web collection, threadpool handlers, detection without provider, collector order, flag default, terminalisation |

Updated tests (they specified retired behaviour): TASK_209 closure and
field-inspection write tests replaced by 410/no-SQL contracts; the pixel
fake-session test now pins the canonical INSERT; the NDVI refresh tests now pin
the retirement; auth/history tests call the now-synchronous dependencies
directly; executive SQL fragments follow the canonical definitions; the H0-A
single-source test no longer lists the closure module as an observation
consumer (it reads no observations).

Results:

| run | result |
|---|---|
| new TASK_225 suites on the candidate | 79 passed |
| new TASK_225 suites on base 66c1be8 | 71 failed, 8 passed |
| full backend, base 66c1be8 | 14 failed, 1139 passed, 1 skipped |
| full backend, candidate | 14 failed, 1197 passed, 1 skipped |

The 8 new tests that pass on base are guards that must already hold there:
the five tenant-boundary tests, legacy history readability, the pixel foreign
tenant test, and the snapshot-refusal test (on base the pixel INSERT fails
anyway, so nothing is written). The discriminating base failures carry the
defects themselves: `409: Concurrent anomaly inspection conflict` (C2),
`KeyError: 'verification_scope'` and the §3.2 reproduction (C4), `200 != 410`
for the legacy result on a canonical inspection and `200 != 409` for a
canonical finding on a legacy row (C5), `no attribute
'detect_observation_candidates'` (C6), `AssertionError: provider must not be
called` (C13), `ImportError: remediation_status` (projection).

The 14 failures are identical in both runs (`test_task212_release_runtime` ×12,
`test_task209_release_rollback_artifacts` ×2: release/launcher identity checks
that fail on this machine independently of this task).

## 8. Frontend contract delta for H0-C (not implemented here)

1. `POST /api/field-inspections` answers 410. `InspectionCreateModal.jsx` and
   `FieldIrrigationContextPanel.jsx` (`source: 'irrigation_context'`) must use
   `POST /api/anomaly-inspections` (`source_kind: 'manual'`, optional
   `point`/`zone`, `due_at`, `Idempotency-Key`). Until then those two create
   buttons show a 410.
2. `PATCH /api/field-inspections/{id}`, `.../start`, `.../complete`,
   `.../cancel` answer 410; a legacy row is closed out with
   `POST /api/anomaly-inspections/{id}/cancel` (`expected_version`, `reason`).
3. The TASK_209 closure writes in `api/fieldInspections.js`
   (`result`, `evidence`, `actions`, `operational-actions/*`,
   `verification-requests/*/resolve`) and the TASK_217 action writes in
   `api/anomalyInspections.js` (`actions`, `actions/*/transition`,
   `actions/*/verify`) answer 410; remediation UI goes through
   `/api/agronomy-plans`.
4. `POST /api/ndvi/{id}/refresh` answers 410; remove the refresh action.
5. Operational Center: `QueueItem.remediation_status` is new (canonical
   vocabulary, §4); `operational_status` gains `closed_without_improvement`;
   `SummaryResponse` gains `closed_without_improvement_recent`,
   `not_improved`, `reopened`, `verification_blocked`.
6. `/api/pixel-anomalies/{id}/inspection` returns the canonical status (`new`
   or `assigned`) instead of `pending`; a zone without a value snapshot answers
   409.
7. Inspection queue summary `active_actions`/`verification_due` now count live
   plans and plans awaiting verification.
8. Executive: `definitions_version` is `task225_canonical_backlog_v1`;
   accountability items gain `plan_id`; action lists are agronomy work items
   and plans.
9. Plan verification `measurements` gain `scope` (kind, reason, zone keys,
   geometry hashes); `statistics_scope` is always `field` for scoped plans.

## 9. Production prerequisites (H0-D) and rollback

- Deploy with H0-C, or accept item 8.1–8.4 as 410s in the current UI.
- Before setting `OBSERVATION_DETECTION_ENABLED=true`, run
  `python scripts/preview_observation_candidates.py` against production
  (read-only) and review the candidates; harvest-season drops are real drops
  and will appear. The collector needs an active admin user as the audit
  identity for automatic inspections (already required before TASK_225).
- No migration, no data backfill. Rollback is a code rollback to 66c1be8; rows
  written meanwhile (canonical inspections, candidates with `scope` evidence,
  plans with `verification_scope`) remain valid under the 0016 schema and
  readable by the old code.

## 10. Residual risks

1. Sub-field zones (pixel anomalies, drawn zones, points) verify to
   `INCONCLUSIVE/zone_statistics_unavailable` by design: no zonal statistics
   are persisted. Closing them needs an override or a future zonal-statistics
   collector.
2. Field geometry is compared by hash at evaluation time; an edit that is
   reverted between baseline and post capture is not detectable without
   per-observation geometry provenance.
3. Detection is not phenology-aware; seasonal drops are recorded as signals
   (see §9). The automatic path keeps the cap and spike guard; `EXTREME`
   candidates still raise `new_critical` notifications.
4. Windowed executive analytics (cycle times, outcomes) still read the retired
   TASK_209 history until H1 re-baselines them.
5. Out-of-scope `async def` handlers with synchronous work remain
   (`agronomic_risk`, `enterprises`, `ndvi_raster`, `satellite_data_quality`,
   `satellite_indices`).
6. Not addressed here by scope: C7 frontend offline purge, C8 process
   supervision/orphans, C9 readiness/Alembic mismatch, C11 backups, C12
   frontend retry handling, release tooling, CI, Redis, Wialon, weather, H1
   analytics, yield, VRA.
