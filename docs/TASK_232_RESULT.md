# TASK_232 — H1 MANAGEMENT ANALYTICS V1 BACKEND RESULT

## Verdict

PASS_TASK_232

## Base

- Base: `origin/main` = `126b62ed45267e341cd727fd678ddd574e35292c`, verified after `git fetch origin` on 2026-09-26. Production runs the same SHA (TASK_231).
- Branch: `task/232-h1-management-analytics-backend`, created from that exact SHA. The local `C:\AgroSat` `main` (97f1653) was not used.
- Worktree: `C:\AgroSat_worktrees\task_232`. `C:\AgroSat` was left alone: `.claude\settings.json` stays modified and `task 231.md` stays untracked, exactly as they were.
- Commits, all on this one branch; nothing was amended or force-pushed:

| commit | content |
|---|---|
| `82fdf5a` | code and tests |
| `9a920a3` | classifies the endpoint in the repository's authorization matrix |
| `cb99b66` | first report |
| `c9be969` | acceptance review correction: one overdue meaning shared with the command center; crop is an explicit current classification; same-named figures aligned. Final code commit, regressed |
| docs commit | this updated report |

## Scope

Files changed:

| file | change |
|---|---|
| `backend/api/management_analytics.py` | new read-only router, `GET /api/management-analytics` |
| `backend/services/management_analytics.py` | new service: scope, one bounded SQL statement, response assembly |
| `backend/schemas/management_analytics.py` | new Pydantic response contract (`extra="forbid"`) |
| `backend/main.py` | two lines: import and `include_router` |
| `backend/tests/task232_support.py` | new PostgreSQL harness (extends the TASK_225 harness) |
| `backend/tests/test_task232_management_analytics_postgres.py` | new PostgreSQL suite, 29 tests |
| `backend/tests/test_task232_management_analytics_contract.py` | new database-free contract suite, 30 tests |
| `backend/tests/test_task209_authorization_matrix.py` | classifies `GET /api/management-analytics` (admin and manager, enterprise filter, cross-tenant 404); operation count 145 → 146 |
| `docs/TASK_232_RESULT.md` | this report |

- Backend only. No frontend, MapLibre, routing or CSS change. No release script, Scheduled Task, collector, notification worker, backup tool, detector configuration or environment file was touched.
- No deployment. Nothing was released, and `origin/main` was not modified.
- No Alembic migration, no index, no runtime DDL.
- No new dependency.
- The existing TASK_209 analytics surface, `/api/executive/*`, is unchanged. Its own `limitations` already label its cycle-time and outcome metrics as retired TASK_209 history.

## Canonical data sources

| metric family | canonical source |
|---|---|
| coverage | `fields.is_active` (the TASK_219 collection and detection population), `satellite_field_freshness` (index `ndvi`) |
| current problem load, state counts | TASK_221 Operational Center case model `services/operational_center.py::CASES_CTE` (inspection, candidate and alert cases), classified by the TASK_225 projection `services/remediation_status.py::inspection_status_sql`, joined 1:1 to `field_inspections` and to the case's current `agronomy_plans` row |
| overdue cases | the same case model's own column `is_overdue`, counted verbatim (see Overdue) |
| overdue work items, work items | `agronomy_work_items` in the current cycle (`w.cycle = p.cycle`) of live plans, with the TASK_220 item predicate `status IN ('planned','in_progress') AND due_at < now` |
| data unavailable | Operational Center `freshness` and `external` cases (`remediation_status = data_unavailable`) |
| period activity | `field_inspections.created_at`, `reviewed_at`, `cancelled_at`; `autonomous_anomaly_candidates.created_at`; `agronomy_plans.created_at`; `agronomy_events` (`approve`, `work_complete`, `cancel`) |
| verified outcomes, reopen events | `agronomy_events` resolution events (`close`, `override_close`, `rework`/`reinspection` from `pending_verification`, `reopen`) and the latest `agronomy_verifications` row of the same plan cycle |
| cycle times | the canonical timestamps named per metric below |
| completion | the same events and verification rows, grouped per plan cycle |
| crop dimension | `crop_seasons` + `crop_types`, as the field's **current** classification only (see Crop classification) |

Plan cycles: `agronomy_events` has no cycle column. The cycle of an event is 1 plus the number of earlier cycle-opening events of the same plan, ordered by `id`. A cycle-opening event is `rework` or `reinspection` from `pending_verification`, or `reopen` from `closed`. This mirrors `closed_loop_agronomy.transition`, where exactly these transitions increment `agronomy_plans.cycle`. The PostgreSQL tests check the derived cycles against the `cycle` column of `agronomy_verifications`.

## Overdue: final behaviour

**Definitions that exist in accepted code**, read at the base SHA:

| accepted source | unit | rule |
|---|---|---|
| TASK_221 `CASES_CTE` column `is_overdue` | case | While the case has no current plan: the inspection deadline `COALESCE(i.due_at, i.due_date) < as_of`, for inspection statuses `pending/new/assigned/in_progress/submitted`. Otherwise: `due_at < as_of` of the case's primary active work item, which is in progress first, then earliest due, then lowest id. Otherwise `false`. It drives the command center's queue order, `priority_reasons`, the queue `overdue` filter and the summary figure `overdue_work` |
| TASK_220 item predicate | work item | `status IN ('planned','in_progress') AND due_at < now`. The plan queue's `due_state=overdue` and its summary `overdue` apply it to a plan with *any* such item in the current cycle |
| TASK_221 notification reconciler | work item / inspection | one `overdue` notification per late active work item and per open inspection past due |
| TASK_217 inspection queue, executive backlog | inspection / work item | their own inspection and date-level variants |

**Decision.** "Overdue case" is a case-level management figure, and the command center's `is_overdue` is the only accepted case-level definition. H1's current state is built on that same case model, so it uses the same flag.
- `current.overdue_cases.total` is `count(*) FILTER (WHERE is_overdue)` over H1's active cases. It equals `/api/operational-center/summary.overdue_work` for the same user and scope.
- It splits into `inspection_stage` (no current plan) and `work_stage` (primary work item late). These are the two branches of the command center's own expression, not new rules.
- Breakdown rows carry the same flag as `current.overdue_cases`.

**Different unit, different name.** `current.work_items.overdue_work_items` counts late active work items in the current cycle of live plans. It uses the TASK_220 item predicate, which is also the predicate of the TASK_221 per-item `overdue` notification. Breakdown rows carry it as `overdue_work_items`.

**Removed.** H1 no longer has a bare `overdue` key, no plan-level "any late item" count, and no rule of its own for legacy date-only deadlines. A legacy `due_date` follows the command center: it is overdue from the first instant of that date in the session time zone (Asia/Tashkent on this server).

**Required test.** `OverdueTests.test_one_plan_main_work_item_on_time_secondary_late` builds one plan with two work items:
- The main item is in progress and due in 2 days; the secondary item is planned and was due 1 hour ago.
- The command center returns `is_overdue = false` for the case and `overdue_work = 0`.
- H1 returns `overdue_cases = {total: 0, inspection_stage: 0, work_stage: 0}` and `overdue_work_items = 1`.
- The only per-item `overdue` notification the reconciler creates is the secondary item's.
- The field row shows `overdue_cases 0`, `overdue_work_items 1`, and there is no bare `overdue` key.

`test_every_stage_follows_the_operational_center_flag` covers:
- a late main item
- no item started and the earliest item late
- an on-time item
- a late new inspection
- a late submitted inspection
- a late inspection that already has a draft plan (plan stage, no deadline)
- a completed plan with late but completed work

H1 flags exactly the command center's four cases (2 inspection stage, 2 work stage) and 2 late work items.

## Crop classification: final behaviour (option B)

**Decision.** The persisted data cannot establish the crop grown when a past event happened, so no metric is attributed to a historical crop. The crop dimension is explicitly the field's **current** classification. It uses the Operational Center rule: the field's latest `crop_seasons` row with `season_year` not after the Asia/Tashkent year of `generated_at`.

**Why the data is insufficient.** Facts from the model and its writers at the base SHA:
1. **One replaceable row per field and year.** `crop_seasons` is unique on `(field_id, season_year)`. The only application writer, `POST /api/fields/{field_id}/season` ("Set or replace"), deletes the existing row for that field and year and inserts a new one. A crop change within a season therefore overwrites history instead of versioning it.
2. **No season end.**
   - The application writer takes an untyped body and writes only `crop_type_id`, `season_year`, `planting_date` (optional) and `variety`. The `SeasonCreate` schema declares harvest dates, but nothing uses it.
   - `actual_harvest_date` is written nowhere. `expected_harvest_date` is written only by the PROGRAM R1 qualification seed script, for synthetic data.
   - All three date columns are `timestamp without time zone`.
3. **`season_year` is not the calendar year the crop was on the field.** The production import scripts (`scripts/import_kml.py`, `scripts/import_servis.py`) write `season_year = 2026` with `planting_date` 2026-04-10 for cotton, but 2025-10-15 for other crops (winter wheat). Mapping an event's year to `season_year` would therefore attribute an autumn event to the wrong season.
4. **A second crop in the same year cannot be recorded**, because of the single row per field and year.
5. **Plan snapshots do not help.** `agronomy_plans.input_snapshot.season` stores a crop *name* only. It looks the season up by the calendar year at draft time, so it inherits the ambiguity in point 3.

**Behaviour:**
- `crop_classification` in every response: `{basis: "current_crop_season", reference_year, rule, historical_crop_at_event: false}`. The schema types `historical_crop_at_event` as `Literal[False]`.
- The filter is `current_crop_type_id`. A bare `crop_type_id` is refused with 422 and a message pointing to `current_crop_type_id`, instead of being silently ignored. It is not listed in OpenAPI.
- The breakdown is `breakdowns.current_crops` with `current_crop_type_id` and `current_crop_name`. Field rows carry `current_crop_type_id` and `current_crop_name`. There is no `crops` key and no `crop_type_id` key anywhere.

**What can and cannot be filtered or grouped by crop:**

| section | crop filter / grouping | meaning |
|---|---|---|
| `coverage`, `current` | yes | the crop the field has now. Exact for current state |
| `period_activity`, `outcomes`, `cycle_times`, `completion`, `breakdowns.current_crops[].period`, field-row `period` | yes | grouped by the field's **current** crop. It is **not** the crop grown when the event happened |
| any metric by crop at event time | **no** | not available from the persisted data (reasons above) |

**Tests:**
- `CropSemanticsTests.test_crop_is_a_current_classification_never_crop_at_event_time` uses a field with cotton last season and wheat now, with a closure moved into last August:
  - The August period reports the outcome under `current_crops` wheat and never under cotton.
  - `current_crop_type_id=<cotton>` finds 0 fields and 0 outcomes.
  - `crop_type_id` gets 422.
  - The response carries `historical_crop_at_event: false` and no `crops` or `crop_type_id` key.
- `test_current_crop_filter_and_grouping_use_the_current_season` pins the classification rule itself: a rotated field, a previous-year-only field, a future season that is ignored, and a null bucket.

## Metric definitions

All counts are counts of distinct canonical lifecycle instances or events. "In the period" means the anchor timestamp is ≥ `date_from` 00:00 Asia/Tashkent and < (`date_to` + 1 day) 00:00 Asia/Tashkent. Both dates are inclusive. The default window is the last 30 days ending today; the maximum is 366 days (the `/api/executive` window rule).

### Period semantics

- Current-state sections (`coverage`, `current`) are the lifecycle state at `generated_at`, which is also `current.as_of`. The period never narrows them, so an inspection opened 60 days ago and still open is current workload under the default 30-day window.
- Windowed sections (`period_activity`, `outcomes`, `cycle_times`) count facts whose own anchor timestamp is in the period. The anchor is named for every metric.
- Cohorts (`completion`) are selected by their starting event in the period. Their numerators are the state of the same cohort at `generated_at`.

### Coverage (current)

| key | population / definition |
|---|---|
| `fields_in_scope` | fields in the effective scope (authorization ∩ filters), active or not |
| `monitored_fields` | `fields.is_active = true`: the population the TASK_219 collector, freshness refresh and detection iterate |
| `inactive_fields` | `fields_in_scope − monitored_fields` |
| `monitored_fields_with_active_problems` | distinct monitored fields with at least one active case |
| `ndvi_freshness.*` | monitored fields by `satellite_field_freshness.status` for `ndvi`; `not_evaluated` means no freshness row yet |

### Current problem load (current)

Population: Operational Center case rows in scope whose root source is `inspection`, `candidate` or `alert` and whose TASK_225 remediation status is one of the 11 non-terminal states. The model yields exactly one row per canonical instance:

- An **inspection case** is a `field_inspections` row in `pending/new/assigned/in_progress/submitted`, or a `confirmed` row that has a current plan or has never had a plan.
  - The current plan is the inspection's single live plan. If there is none, it is its latest plan closed within 30 days.
  - A confirmed inspection whose plans are all cancelled or superseded, or were closed more than 30 days ago, is not current (inherited rule).
- A **candidate case** is an `autonomous_anomaly_candidates` row in `NEW/CONFIRMED` without an inspection.
- An **alert case** is an active alert that no open canonical inspection references (`source_alert_id`). Opening the inspection replaces the alert case, so it is never counted twice (tested).

| key | definition |
|---|---|
| `active_problems.total` | active cases in scope. `/api/operational-center/summary.active_situations` = this + `data_unavailable.*` (tested) |
| `fields_affected` | distinct fields with at least one active case |
| `by_source.*` | active cases by root source |
| `by_priority.*` | Operational Center `priority_rank`: 0 critical, 1 high, 2 normal, 3 low |
| `legacy_open_inspections` | active inspection cases whose row is a pre-0013 legacy inspection. The TASK_225 projection still counts them as workload to drain |

### Cases by remediation status (current)

`current.by_remediation_status` is keyed by the TASK_225 `remediation_status` values themselves, exactly the value the command center returns per queue item. Every active case is in exactly one state, and the states sum to `active_problems.total` (tested).

| key | canonical mapping |
|---|---|
| `needs_inspection` | inspection `new`/`assigned` (legacy `pending`) without a current plan, plus candidate and alert cases. Sub-keys: `inspections`, `candidates`, `alerts`, and `unassigned` (inspection cases without an assignee) |
| `inspection_active` | inspection `in_progress` |
| `awaiting_review` | inspection `submitted` without a current plan |
| `awaiting_decision` | inspection `confirmed` without a current plan |
| `plan_active` (`draft`, `approved`) | current plan `draft` / `approved` |
| `work_active` | current plan `in_progress` |
| `awaiting_satellite_verification` (`pending_data`, `too_early`) | plan `pending_verification` with `PENDING_DATA` / `TOO_EARLY`, the TASK_225 state only |
| `verification_blocked` (`cloud_blocked`, `quality_blocked`, `provider_degraded`, `inconclusive`) | plan `pending_verification` with a status that cannot conclude. This is the only blocked state TASK_220 represents |
| `improved_awaiting_closure` | plan `pending_verification` with `IMPROVED` (provisional, not an outcome) |
| `not_improved` (`unchanged` = `NO_MATERIAL_CHANGE`, `worsened`) | plan `pending_verification` with a non-improving conclusive status |
| `reopened` | plan `rework`: returned after verification or reopened after closure |

| key | definition |
|---|---|
| `plans_pending_verification` | cases whose current plan is `pending_verification` in any verification status. This is the figure the command center summary calls `awaiting_satellite_verification` and the TASK_220 plan summary calls `pending_verification` (tested) |
| `work_items.active/planned/in_progress/unassigned` | `planned`/`in_progress` work items in the current cycle of live plans |
| `work_items.overdue_work_items` | see Overdue |
| `overdue_cases.total/inspection_stage/work_stage` | see Overdue |
| `data_unavailable.freshness_cases` | Operational Center freshness cases: field × index not FRESH without an open case on that index |
| `data_unavailable.external_cases` | Operational Center external cases: the latest collection run is degraded, failed or a stale `running` run. One per enterprise with active fields; enterprise-level, so shown only without a field or crop filter |

### Period activity (windowed)

| key | anchor |
|---|---|
| `anomaly_candidates_detected` | `autonomous_anomaly_candidates.created_at` |
| `inspections_opened.total/manual/alert/pixel_ndvi` | `field_inspections.created_at`, canonical rows only |
| `inspections_confirmed` / `inspections_rejected` | `reviewed_at` of rows now `confirmed` / `rejected` |
| `inspections_cancelled` | `cancelled_at`, canonical rows |
| `plans_drafted` | `agronomy_plans.created_at` |
| `plan_cycles_approved` | `occurred_at` of `approve` (one per plan cycle) |
| `plan_cycles_work_completed` | `occurred_at` of the `work_complete` event whose resulting status is `pending_verification` (one per cycle, however many items) |
| `plan_cycles_verified` | `created_at` of the first conclusive `agronomy_verifications` row of a plan cycle |
| `plans_cancelled` | `occurred_at` of `cancel` |

### Verified outcomes and reopen events (windowed)

Population: plan cycles resolved in the period. A cycle is resolved when it closes (`close`, `override_close`) or is returned after verification (`rework`/`reinspection` from `pending_verification`). A verified cycle ends exactly once, so it is counted once. The anchor is the resolution event time.

The outcome is the status of the cycle's latest verification row, or `PENDING_DATA` when the cycle has none. This equals `agronomy_plans.verification_status` at resolution.

| key | definition |
|---|---|
| `resolved_cycles` | resolved cycles |
| `verified.improved/unchanged/worsened/total` | outcome `IMPROVED` / `NO_MATERIAL_CHANGE` / `WORSENED` |
| `unverified.*` | resolved with `PENDING_DATA`, `TOO_EARLY`, `CLOUD_BLOCKED`, `QUALITY_BLOCKED`, `PROVIDER_DEGRADED` or `INCONCLUSIVE`. These are never outcomes |
| `closed.total/improved/without_improvement` | resolutions that closed the plan |
| `returned_for_rework` | resolutions that opened another cycle |
| `reopen_events.after_closure` | `reopen` events, anchored at event time |
| `reopen_events.after_verification` | `rework`/`reinspection` events from `pending_verification`, anchored at event time |

`reopen_events` counts events. The current state `by_remediation_status.reopened` counts cases in `rework` now, and that is the command center's `reopened` figure.

A verification that is conclusive but still waiting for a human decision is not an outcome yet. It appears in `current.by_remediation_status` and in `completion.verification_completion`.

### Cycle times (windowed)

Unit: hours, rounded to 2 decimals.
- Samples are completed, correctly ordered pairs only. An open cycle contributes nothing.
- `median_hours` is `percentile_cont(0.5)`, reported from one sample.
- `p90_hours` is `percentile_cont(0.9)`, reported only from 10 samples, otherwise `null`.
- `status` is `measured` or `no_samples`.

| metric | start event | end event (= period anchor) | population |
|---|---|---|---|
| `signal_to_inspection_opened` | `autonomous_anomaly_candidates.created_at` | `field_inspections.created_at` | canonical inspections opened from a candidate |
| `inspection_opened_to_reviewed` | `field_inspections.created_at` | `field_inspections.reviewed_at` | canonical inspections confirmed or rejected |
| `inspection_submitted_to_plan_drafted` | `field_inspections.submitted_at` | `agronomy_plans.created_at` of the inspection's first plan | inspections whose first plan was drafted |
| `plan_approved_to_work_completed` | `approve` event of the cycle | `work_complete` → `pending_verification` event of the same cycle | plan cycles whose work was completed |
| `work_completed_to_verified` | `agronomy_verifications.completed_at` | `created_at` of the cycle's first conclusive verification | plan cycles with a conclusive verification. At least 8 days by policy r3-f-v1 |
| `case_opened_to_verified_closure` | `field_inspections.created_at` of the case root | `agronomy_plans.closed_at` | plans currently `closed` with a conclusive verification |

Unsupported: `alert_signal_to_inspection_opened`. `alerts.triggered_at` is `timestamp without time zone`.

### Completion (cohorts)

`rate = numerator / denominator`, rounded to 4 places, and `null` when the denominator is 0.

| key | denominator (cohort in the period) | numerator and splits (as of `generated_at`) |
|---|---|---|
| `work_completion` | plan cycles approved in the period | cycles whose work was completed; `ended_without_completion`; `open` |
| `verification_completion` | plan cycles whose work was completed in the period | cycles whose latest verification is conclusive; `improved`, `unchanged`, `worsened`, `not_conclusive`; `improved_rate` |
| `plan_closure` | plans drafted in the period and not superseded | plans now `closed`; `closed_improved`, `closed_without_improvement`, `cancelled`, `open` |

Completed work is never reported as success. Success is only the verification outcome.

### Breakdowns

- `enterprises`, `current_crops`, `fields` rows each have `monitored_fields`, `current` and `period` numbers.
  - `current` holds `active_problems`, `by_remediation_status` (the 11 states), `overdue_cases` and `overdue_work_items`.
  - `period` holds `inspections_opened`, `resolved_cycles`, `improved`, `unchanged`, `worsened`, `unverified` and `reopen_events`.
  - Enterprise and current-crop rows are sums of the field rows computed in the same statement, so they reconcile exactly with the totals (tested).
- `fields` is paged: `field_limit` 1..200 (default 50), `field_offset` 0..10000, plus `total`. Order: active problems desc, overdue cases desc, resolved cycles desc, name, id. Paging covers every field exactly once (tested).
- Enterprise rows are capped at 500 and current-crop rows at 200. Above the cap the response is `422 result_too_large`, never silently truncated.
- `periods`: `granularity` `day`, `week` (ISO Monday) or `month` in the Asia/Tashkent calendar. The first and last buckets are clipped to the period, empty buckets are present with zeros, and counts sum to the windowed totals (tested).

## Alignment with accepted projections (re-audit)

After the two corrections, I re-checked every figure against:
- the command center summary
- the TASK_220 plan summary
- the TASK_217 inspection queue summary
- the executive backlog

A figure either has exactly the same definition as the accepted figure it shares a name with, or it has its own explicit name.

| H1 figure | accepted figure | relation |
|---|---|---|
| `current.overdue_cases.total` | command center summary `overdue_work` | identical definition (same `is_overdue`); tested |
| `current.plans_pending_verification` | command center summary `awaiting_satellite_verification`; TASK_220 summary `pending_verification` | identical population; tested against the command center |
| `current.by_remediation_status.reopened` / `.not_improved` / `.verification_blocked` | command center summary `reopened` / `not_improved` / `verification_blocked` | identical (same TASK_225 state); tested |
| `current.by_remediation_status.work_active` | command center summary `awaiting_evidence` | identical population; tested |
| `current.by_remediation_status.*` | command center queue item `remediation_status` | same vocabulary; the map is keyed by the state value |
| `current.by_remediation_status.awaiting_satellite_verification` | command center summary `awaiting_satellite_verification` | **different concept**: the TASK_225 state only. It sits inside the explicitly named state map, and the command center figure is `plans_pending_verification` |
| `current.by_remediation_status.awaiting_review` | TASK_217 inspection queue summary `awaiting_review` (raw `submitted`, even with a draft plan) | different concept, in the state map; H1 exposes no raw-status figure |
| `current.active_problems.total` | command center summary `active_situations` | own name. `active_situations = active_problems.total + data_unavailable.*` (tested) |
| `current.work_items.overdue_work_items` | TASK_220 item predicate; TASK_221 per-item `overdue` notifications | same item predicate; tested against the notifications. Like the TASK_220 queue, H1 counts only items in the current cycle of live plans. The reconciler also sees planned items left behind in a superseded plan |
| `outcomes.reopen_events.*` | — | renamed from `reopened` so that `reopened` means only the current state, as in the command center |
| `current.by_remediation_status.needs_inspection.unassigned` | executive backlog `unassigned_inspections` (all open statuses) | own name (differs only for legacy `in_progress` rows without an assignee) |
| `outcomes.verified.improved/unchanged/worsened` | TASK_220 summary `improved` (current plan status, all time); executive `verification_outcomes` (retired TASK_209 history) | own path. Windowed resolved plan cycles on the canonical lifecycle, which is the H1 re-baseline |

Other checks, all unchanged by the correction and covered by the tests:
- population, anchor, current vs terminal semantics
- one-to-many joins
- total ↔ breakdown reconciliation
- tenant scope
- TASK_209 exclusion

## Legacy exclusion

- The statement never references `corrective_actions`, `action_verification_requests` or `operational_audit_events`. A contract test pins this, and the PostgreSQL suite asserts it on the executed SQL. `provenance.excluded_legacy_sources` names both TASK_209 tables.
- `period_activity` and the cycle times exclude `field_inspections` rows with `source_kind='legacy'`.
- Open legacy inspections (`pending`/`in_progress`) remain current workload, as the TASK_225 projection and the command center define it. They are reported separately in `legacy_open_inspections`. Their date-only deadlines follow the command center's `is_overdue`.
- `test_legacy_task209_history_never_becomes_current_truth` seeds legacy inspections, a result, open, closed and reopened corrective actions, and two resolved `improved` `action_verification_requests`. Every windowed and outcome number stays 0, every completion denominator is 0, and `overdue_cases` equals the command center's `overdue_work`.

## Authorization

- Authentication comes from `get_current_active_user`; without it the endpoint answers 401.
- Roles are `admin` and `manager`, the `/api/executive` precedent. `agronomist` and `viewer` get 403 before any SQL. A manager without an enterprise gets 403.
- Effective scope = server authority ∩ request filters:
  - A manager is bound to `users.enterprise_id` (`scope.authorization = "tenant"`).
  - An admin is global (`"global"`).
  - `enterprise_id`, `field_id` and `current_crop_type_id` only narrow.
- Non-enumerating responses:
  - A foreign enterprise and a non-existent one both get `404 {"detail":"Enterprise not found"}`.
  - A field outside the effective scope and a non-existent field both get `404 {"detail":"Field not found"}`.
  - An unknown crop type gets `404 {"detail":"Crop type not found"}`. `crop_types` is a global reference table.
  - Tests compare status and body byte for byte.

## Query design

- **Statements.** A request runs exactly 1 SQL statement, or 2 when a filter is given: a single existence check for the filter targets, then the snapshot statement.
  - The snapshot statement is `CASES_CTE` followed by the H1 CTEs, returning one row of JSON aggregates.
  - Because it is one statement, all sections read one consistent snapshot and reconcile even under concurrent writes.
  - `test_statement_count_does_not_grow_with_the_data` pins `(1, 2)` on an empty scope and again on the full panorama.
- **No N+1.** Only `text()` SQL is used. No ORM entity is loaded, no relationship is traversed, and nothing is issued per row, enterprise, field or plan. One-to-many joins are avoided: cases join their inspection and plan 1:1, and work items and verification rows are aggregated separately. The correction removed H1's own per-case overdue `EXISTS`; the flag now comes from the case model.
- **Tenant pushdown.** The tenant and field conditions are applied to `scope_fields` and inside the case model (`c.enterprise_id`, `c.field_id`).
- **Bounded output.** The period is at most 366 days, the field page is ≤ 200 rows, and enterprise and current-crop rows are capped.
- **Measured performance.** Service call timings on the final code, isolated databases, PostgreSQL 16.14 on the production host, 7 runs, median:

| volume | scope | median |
|---|---|---|
| 300 fields, 1,200 inspections, 600 plans, 3,840 events, 900 open problems | admin, 30 days | 184 ms |
| same | admin, 366 days, day buckets | 250 ms |
| same | manager (60 fields) | 62–69 ms |
| same | one field | 29 ms |
| 3,000 fields, 12,000 inspections, 6,000 plans, 38,400 events, 9,000 open problems | admin, 30 / 366 days | 2.89–3.05 s |
| same | manager (600 fields) | 618–664 ms |
| same | one field | 38 ms |

- For reference, production on 2026-09-24 had 275 active fields, 1 inspection and 0 plans.
- At the 3,000-field volume, the accepted `operational_center.summary` alone takes 2.60 s on the same data. Almost all of the time is inside the TASK_221 case model:
  - The current-plan `LATERAL` has no index leading with `agronomy_plans.inspection_id`.
  - The freshness-case `NOT EXISTS` uses a `BitmapAnd`.
- A probe index `agronomy_plans(inspection_id)`, created and dropped in the isolated perf database only, gave:
  - admin 2.89 → 1.82 s
  - manager 618 → 166 ms
  - `/api/operational-center/summary` 2.61 → 1.58 s
- No index is required at realistic volume, so none was added.
- Raw numbers: `evidence\perf_300_fields_c9be969.txt`, `perf_3000_fields_c9be969.txt`, `perf_index_probe_c9be969.txt`.

## API contract

`GET /api/management-analytics`. Read-only; the router registers no other method or path.

| parameter | type | rule |
|---|---|---|
| `date_from`, `date_to` | date | inclusive, Asia/Tashkent; default the last 30 days ending today; `date_from > date_to` → 422; more than 366 days → 422 |
| `enterprise_id` | int > 0 | narrows; foreign or missing → 404 |
| `field_id` | int > 0 | narrows; outside scope or missing → 404 |
| `current_crop_type_id` | int > 0 | narrows to fields whose **current** crop season has this crop; missing → 404 |
| `crop_type_id` | — | refused with 422 (see Crop classification); not in OpenAPI |
| `granularity` | `day` \| `week` \| `month` | default `week` |
| `field_limit`, `field_offset` | int | 1..200 (default 50), 0..10000 (default 0) |

Top-level response keys:
- metadata: `definitions_version` (`management_analytics_v1`), `generated_at`, `timezone`, `scope`, `crop_classification`, `period`, `provenance`
- current state: `coverage`, `current`
- windowed: `period_activity`, `outcomes`, `cycle_times`, `completion`
- `breakdowns` (`enterprises`, `current_crops`, `periods`, `fields`), `limitations`

`provenance.definitions_fingerprint` is the SHA-256 of every definition the response depends on: the case model SQL (so it also changes if `CASES_CTE` changes), the projection, this statement, the duration catalogue, the policy version and the p90 threshold. The contract test pins it next to `definitions_version`. The version stays `management_analytics_v1`: it has never been released or consumed, and its definitions were finalized by this correction before acceptance. The fingerprint is identical for LF and CRLF checkouts.

Example (sanitized). It is the manager response from the TASK_232 panorama test scenario, with synthetic names and ids. `breakdowns.fields.items` is shortened to 1 of 3 rows, and `limitations` to 1 of 8 entries:

```json
{
  "definitions_version": "management_analytics_v1",
  "generated_at": "2026-09-26T12:21:55.036036+05:00",
  "timezone": "Asia/Tashkent",
  "scope": {
    "role": "manager",
    "authorization": "tenant",
    "enterprise_id": 1,
    "field_id": null,
    "current_crop_type_id": null
  },
  "crop_classification": {
    "basis": "current_crop_season",
    "reference_year": 2026,
    "rule": "each field is classified once by its latest crop_seasons row with season_year <= reference_year (the Operational Center rule)",
    "historical_crop_at_event": false
  },
  "period": {
    "requested": {
      "date_from": null,
      "date_to": null
    },
    "effective": {
      "date_from": "2026-08-28",
      "date_to": "2026-09-26",
      "inclusive": true,
      "days": 30,
      "starts_at": "2026-08-28T00:00:00+05:00",
      "ends_before": "2026-09-27T00:00:00+05:00",
      "granularity": "week"
    }
  },
  "provenance": {
    "lifecycle": "task220_canonical_remediation",
    "inspection_workflow": "task217_canonical_inspection",
    "status_projection": "task225_remediation_status",
    "case_model": "task221_operational_center_cases",
    "verification_policy_version": "r3-f-v1",
    "sources": [
      "fields",
      "crop_seasons",
      "field_inspections",
      "autonomous_anomaly_candidates",
      "alerts",
      "agronomy_plans",
      "agronomy_work_items",
      "agronomy_verifications",
      "agronomy_events",
      "satellite_field_freshness",
      "satellite_collection_runs"
    ],
    "excluded_legacy_sources": [
      "corrective_actions",
      "action_verification_requests"
    ],
    "definitions_fingerprint": "91bca1b614782a938742b28a71db41d21a9a0fc916a942479f4964540211ea05"
  },
  "coverage": {
    "fields_in_scope": 14,
    "monitored_fields": 14,
    "inactive_fields": 0,
    "monitored_fields_with_active_problems": 13,
    "ndvi_freshness": {
      "fresh": 0,
      "aging": 0,
      "stale": 0,
      "never_collected": 0,
      "cloud_blocked": 0,
      "provider_degraded": 0,
      "quality_blocked": 0,
      "not_evaluated": 14
    }
  },
  "current": {
    "as_of": "2026-09-26T12:21:55.036036+05:00",
    "active_problems": {
      "total": 15,
      "fields_affected": 13,
      "by_source": {
        "inspection": 14,
        "candidate": 0,
        "alert": 1
      },
      "by_priority": {
        "critical": 1,
        "high": 0,
        "normal": 14,
        "low": 0
      },
      "legacy_open_inspections": 0
    },
    "by_remediation_status": {
      "needs_inspection": {
        "total": 3,
        "inspections": 2,
        "candidates": 0,
        "alerts": 1,
        "unassigned": 1
      },
      "inspection_active": 1,
      "awaiting_review": 1,
      "awaiting_decision": 1,
      "plan_active": {
        "total": 2,
        "draft": 1,
        "approved": 1
      },
      "work_active": 1,
      "awaiting_satellite_verification": {
        "total": 1,
        "pending_data": 1,
        "too_early": 0
      },
      "verification_blocked": {
        "total": 1,
        "cloud_blocked": 0,
        "quality_blocked": 1,
        "provider_degraded": 0,
        "inconclusive": 0
      },
      "improved_awaiting_closure": 1,
      "not_improved": {
        "total": 2,
        "unchanged": 1,
        "worsened": 1
      },
      "reopened": 1
    },
    "plans_pending_verification": 5,
    "work_items": {
      "active": 3,
      "planned": 1,
      "in_progress": 2,
      "unassigned": 0,
      "overdue_work_items": 0
    },
    "overdue_cases": {
      "total": 0,
      "inspection_stage": 0,
      "work_stage": 0
    },
    "data_unavailable": {
      "freshness_cases": 0,
      "external_cases": 0
    }
  },
  "period_activity": {
    "anomaly_candidates_detected": 0,
    "inspections_opened": {
      "total": 15,
      "manual": 15,
      "alert": 0,
      "pixel_ndvi": 0
    },
    "inspections_confirmed": 11,
    "inspections_rejected": 0,
    "inspections_cancelled": 0,
    "plans_drafted": 10,
    "plan_cycles_approved": 9,
    "plan_cycles_work_completed": 7,
    "plan_cycles_verified": 5,
    "plans_cancelled": 0
  },
  "outcomes": {
    "resolved_cycles": 2,
    "verified": {
      "improved": 1,
      "unchanged": 0,
      "worsened": 1,
      "total": 2
    },
    "unverified": {
      "total": 0,
      "pending_data": 0,
      "too_early": 0,
      "cloud_blocked": 0,
      "quality_blocked": 0,
      "provider_degraded": 0,
      "inconclusive": 0
    },
    "closed": {
      "total": 1,
      "improved": 1,
      "without_improvement": 0
    },
    "returned_for_rework": 1,
    "reopen_events": {
      "total": 1,
      "after_closure": 0,
      "after_verification": 1
    }
  },
  "cycle_times": {
    "unit": "hours",
    "p90_minimum_samples": 10,
    "metrics": {
      "signal_to_inspection_opened": {
        "start_event": "autonomous_anomaly_candidates.created_at (satellite signal detected)",
        "end_event": "field_inspections.created_at (canonical inspection opened from that candidate)",
        "period_anchor": "field_inspections.created_at",
        "population": "canonical inspections opened from an autonomous anomaly candidate",
        "sample_count": 0,
        "median_hours": null,
        "p90_hours": null,
        "status": "no_samples"
      },
      "inspection_opened_to_reviewed": {
        "start_event": "field_inspections.created_at (inspection opened)",
        "end_event": "field_inspections.reviewed_at (finding confirmed or rejected)",
        "period_anchor": "field_inspections.reviewed_at",
        "population": "canonical inspections reviewed (confirmed or rejected)",
        "sample_count": 11,
        "median_hours": 0.0,
        "p90_hours": 0.0,
        "status": "measured"
      },
      "inspection_submitted_to_plan_drafted": {
        "start_event": "field_inspections.submitted_at (finding submitted)",
        "end_event": "agronomy_plans.created_at of the inspection's first plan",
        "period_anchor": "agronomy_plans.created_at of the first plan",
        "population": "inspections whose first TASK_220 plan was drafted",
        "sample_count": 10,
        "median_hours": 0.0,
        "p90_hours": 0.0,
        "status": "measured"
      },
      "plan_approved_to_work_completed": {
        "start_event": "agronomy_events 'approve' of the plan cycle",
        "end_event": "agronomy_events 'work_complete' that moved the cycle to pending_verification",
        "period_anchor": "work completion event time",
        "population": "plan cycles whose required work was completed",
        "sample_count": 7,
        "median_hours": 0.0,
        "p90_hours": null,
        "status": "measured"
      },
      "work_completed_to_verified": {
        "start_event": "agronomy_verifications.completed_at (cycle work completion)",
        "end_event": "agronomy_verifications.created_at of the cycle's first conclusive verification",
        "period_anchor": "first conclusive verification time",
        "population": "plan cycles with a conclusive IMPROVED, NO_MATERIAL_CHANGE or WORSENED verification",
        "sample_count": 5,
        "median_hours": 0.0,
        "p90_hours": null,
        "status": "measured"
      },
      "case_opened_to_verified_closure": {
        "start_event": "field_inspections.created_at (canonical case root opened)",
        "end_event": "agronomy_plans.closed_at (plan closed with a conclusive verification)",
        "period_anchor": "agronomy_plans.closed_at",
        "population": "plans currently closed whose closing verification is conclusive",
        "sample_count": 1,
        "median_hours": 0.0,
        "p90_hours": null,
        "status": "measured"
      }
    },
    "unsupported": [
      {
        "metric": "alert_signal_to_inspection_opened",
        "reason": "alerts.triggered_at is stored without a time zone, so no timezone-safe persisted signal time exists for alert-origin cases"
      }
    ]
  },
  "completion": {
    "work_completion": {
      "population": "plan cycles approved in the period (agronomy_events 'approve')",
      "numerator": 7,
      "denominator": 9,
      "rate": 0.7778,
      "completed": 7,
      "ended_without_completion": 0,
      "open": 2
    },
    "verification_completion": {
      "population": "plan cycles whose required work was completed in the period",
      "numerator": 5,
      "denominator": 7,
      "rate": 0.7143,
      "improved": 2,
      "unchanged": 1,
      "worsened": 2,
      "not_conclusive": 2,
      "improved_rate": 0.2857
    },
    "plan_closure": {
      "population": "agronomy plans drafted in the period and not superseded",
      "numerator": 1,
      "denominator": 10,
      "rate": 0.1,
      "closed_improved": 1,
      "closed_without_improvement": 0,
      "cancelled": 0,
      "open": 9
    }
  },
  "breakdowns": {
    "enterprises": [
      {
        "monitored_fields": 14,
        "current": {
          "active_problems": 15,
          "by_remediation_status": {
            "needs_inspection": 3,
            "inspection_active": 1,
            "awaiting_review": 1,
            "awaiting_decision": 1,
            "plan_active": 2,
            "work_active": 1,
            "awaiting_satellite_verification": 1,
            "verification_blocked": 1,
            "improved_awaiting_closure": 1,
            "not_improved": 2,
            "reopened": 1
          },
          "overdue_cases": 0,
          "overdue_work_items": 0
        },
        "period": {
          "inspections_opened": 15,
          "resolved_cycles": 2,
          "improved": 1,
          "unchanged": 0,
          "worsened": 1,
          "unverified": 0,
          "reopen_events": 1
        },
        "enterprise_id": 1,
        "enterprise_name": "T225 Alpha"
      }
    ],
    "current_crops": [
      {
        "monitored_fields": 1,
        "current": {
          "active_problems": 1,
          "by_remediation_status": {
            "needs_inspection": 0,
            "inspection_active": 0,
            "awaiting_review": 0,
            "awaiting_decision": 0,
            "plan_active": 0,
            "work_active": 0,
            "awaiting_satellite_verification": 0,
            "verification_blocked": 0,
            "improved_awaiting_closure": 0,
            "not_improved": 1,
            "reopened": 0
          },
          "overdue_cases": 0,
          "overdue_work_items": 0
        },
        "period": {
          "inspections_opened": 1,
          "resolved_cycles": 0,
          "improved": 0,
          "unchanged": 0,
          "worsened": 0,
          "unverified": 0,
          "reopen_events": 0
        },
        "current_crop_type_id": 302,
        "current_crop_name": "T232 Пшеница"
      },
      {
        "monitored_fields": 1,
        "current": {
          "active_problems": 2,
          "by_remediation_status": {
            "needs_inspection": 2,
            "inspection_active": 0,
            "awaiting_review": 0,
            "awaiting_decision": 0,
            "plan_active": 0,
            "work_active": 0,
            "awaiting_satellite_verification": 0,
            "verification_blocked": 0,
            "improved_awaiting_closure": 0,
            "not_improved": 0,
            "reopened": 0
          },
          "overdue_cases": 0,
          "overdue_work_items": 0
        },
        "period": {
          "inspections_opened": 1,
          "resolved_cycles": 0,
          "improved": 0,
          "unchanged": 0,
          "worsened": 0,
          "unverified": 0,
          "reopen_events": 0
        },
        "current_crop_type_id": 301,
        "current_crop_name": "T232 Хлопок"
      },
      {
        "monitored_fields": 12,
        "current": {
          "active_problems": 12,
          "by_remediation_status": {
            "needs_inspection": 1,
            "inspection_active": 1,
            "awaiting_review": 1,
            "awaiting_decision": 1,
            "plan_active": 2,
            "work_active": 1,
            "awaiting_satellite_verification": 1,
            "verification_blocked": 1,
            "improved_awaiting_closure": 1,
            "not_improved": 1,
            "reopened": 1
          },
          "overdue_cases": 0,
          "overdue_work_items": 0
        },
        "period": {
          "inspections_opened": 13,
          "resolved_cycles": 2,
          "improved": 1,
          "unchanged": 0,
          "worsened": 1,
          "unverified": 0,
          "reopen_events": 1
        },
        "current_crop_type_id": null,
        "current_crop_name": null
      }
    ],
    "periods": [
      {
        "bucket_start": "2026-08-28",
        "bucket_end": "2026-08-30",
        "anomaly_candidates_detected": 0,
        "inspections_opened": 0,
        "plans_drafted": 0,
        "plan_cycles_approved": 0,
        "plan_cycles_work_completed": 0,
        "plan_cycles_verified": 0,
        "resolved_cycles": 0,
        "improved": 0,
        "unchanged": 0,
        "worsened": 0,
        "unverified": 0,
        "closed": 0,
        "returned_for_rework": 0,
        "reopen_events": 0
      },
      {
        "bucket_start": "2026-08-31",
        "bucket_end": "2026-09-06",
        "anomaly_candidates_detected": 0,
        "inspections_opened": 0,
        "plans_drafted": 0,
        "plan_cycles_approved": 0,
        "plan_cycles_work_completed": 0,
        "plan_cycles_verified": 0,
        "resolved_cycles": 0,
        "improved": 0,
        "unchanged": 0,
        "worsened": 0,
        "unverified": 0,
        "closed": 0,
        "returned_for_rework": 0,
        "reopen_events": 0
      },
      {
        "bucket_start": "2026-09-07",
        "bucket_end": "2026-09-13",
        "anomaly_candidates_detected": 0,
        "inspections_opened": 0,
        "plans_drafted": 0,
        "plan_cycles_approved": 0,
        "plan_cycles_work_completed": 0,
        "plan_cycles_verified": 0,
        "resolved_cycles": 0,
        "improved": 0,
        "unchanged": 0,
        "worsened": 0,
        "unverified": 0,
        "closed": 0,
        "returned_for_rework": 0,
        "reopen_events": 0
      },
      {
        "bucket_start": "2026-09-14",
        "bucket_end": "2026-09-20",
        "anomaly_candidates_detected": 0,
        "inspections_opened": 0,
        "plans_drafted": 0,
        "plan_cycles_approved": 0,
        "plan_cycles_work_completed": 0,
        "plan_cycles_verified": 0,
        "resolved_cycles": 0,
        "improved": 0,
        "unchanged": 0,
        "worsened": 0,
        "unverified": 0,
        "closed": 0,
        "returned_for_rework": 0,
        "reopen_events": 0
      },
      {
        "bucket_start": "2026-09-21",
        "bucket_end": "2026-09-26",
        "anomaly_candidates_detected": 0,
        "inspections_opened": 15,
        "plans_drafted": 10,
        "plan_cycles_approved": 9,
        "plan_cycles_work_completed": 7,
        "plan_cycles_verified": 5,
        "resolved_cycles": 2,
        "improved": 1,
        "unchanged": 0,
        "worsened": 1,
        "unverified": 0,
        "closed": 1,
        "returned_for_rework": 1,
        "reopen_events": 1
      }
    ],
    "fields": {
      "items": [
        {
          "monitored_fields": 1,
          "current": {
            "active_problems": 2,
            "by_remediation_status": {
              "needs_inspection": 2,
              "inspection_active": 0,
              "awaiting_review": 0,
              "awaiting_decision": 0,
              "plan_active": 0,
              "work_active": 0,
              "awaiting_satellite_verification": 0,
              "verification_blocked": 0,
              "improved_awaiting_closure": 0,
              "not_improved": 0,
              "reopened": 0
            },
            "overdue_cases": 0,
            "overdue_work_items": 0
          },
          "period": {
            "inspections_opened": 1,
            "resolved_cycles": 0,
            "improved": 0,
            "unchanged": 0,
            "worsened": 0,
            "unverified": 0,
            "reopen_events": 0
          },
          "field_id": 3,
          "field_name": "T225 Quiet 0",
          "enterprise_id": 1,
          "enterprise_name": "T225 Alpha",
          "current_crop_type_id": 301,
          "current_crop_name": "T232 Хлопок"
        }
      ],
      "total": 14,
      "limit": 3,
      "offset": 0
    }
  },
  "limitations": [
    "Current-state sections describe the lifecycle at generated_at and are not narrowed by the period; windowed sections count facts whose documented anchor timestamp falls inside the period."
  ]
}
```

## Tests

Environment:
- Worktree `C:\AgroSat_worktrees\task_232`, venv `C:\AgroSat\backend\venv` (Python 3.14.5), PostgreSQL 16.14 with PostGIS 3.6.
- Isolated database `agrosat_h0a_task232`, created empty and upgraded to `0016_operational_command_center` with this branch's Alembic scripts.
- `AGROSAT_TEST_DATABASE_URL` and `DATABASE_URL` both point at it for the PostgreSQL lane only; the harness refuses any name not starting with `agrosat_h0a`.

Focused corrected cases first: `python -m pytest tests/test_task232_management_analytics_postgres.py -k "OverdueTests or CropSemanticsTests"` gave **4 passed**. Then the whole TASK_232 PostgreSQL suite: **29 passed**. Contract and authorization-matrix suites: **40 passed**.

The script `C:\AgroSat_backups\TASK_232_H1_MANAGEMENT_ANALYTICS_EVIDENCE_20260926\scripts\final_regression.sh` mirrors `.github/workflows/ci.yml` and `postgres.yml`. Its runs:
- `82fdf5a`: one matrix failure, fixed in `9a920a3`
- `9a920a3`: green
- `c9be969`: final

Logs are under `evidence\code_<sha>`.

Final run on code commit `c9be969` (`c9be9696b9990b4dba943dd1d1f0c930bb46bce1`); the docs-only commit that follows changes no code:

| lane | command (from `C:\AgroSat_worktrees\task_232`) | result |
|---|---|---|
| guard: migration graph | `python -B ops/release/Invoke-AgroSatControlPlane.py rollback-contract --backend backend` | exit 0, `status: PASS`, head `0016_operational_command_center` |
| guard: Alembic heads | `cd backend && python -m alembic heads` | `0016_operational_command_center (head)`, single head |
| guard: startup safety | `python -m pytest tests/test_web_startup_safety.py -q -p no:cacheprovider` | 6 passed |
| guard: retired endpoints | `python -m pytest -q -p no:cacheprovider tests/test_field_inspections.py tests/test_satellite_write_safety.py tests/test_task209_operational_closure_backend.py tests/test_task225_contracts.py` | 137 passed |
| backend (no database URL) | `cd backend && python -m pytest tests -q -p no:cacheprovider` | **1448 passed, 0 failed, 146 skipped** (1594 total) |
| ops: PowerShell parse | parser over `ops/**/*.ps1` (as in `ci.yml`) | 24 parsed, 0 failed |
| ops tests | `python -m pytest ops/tests -q -p no:cacheprovider` | 117 passed |
| PostgreSQL: current revision | `python -m alembic current` on `agrosat_h0a_task232` | `0016_operational_command_center (head)` |
| PostgreSQL, one process per file | `python -m pytest <file> -q -p no:cacheprovider` for every `tests/*postgres*.py` | **145 passed, 0 failed** (table below) |

| PostgreSQL suite | result |
|---|---|
| `test_h0a_freshness_postgres_contract` | 34 passed |
| `test_h0a_notification_lifecycle_postgres` | 16 passed |
| `test_task223_collection_run_lock_postgres` | 8 passed |
| `test_task225_closed_loop_postgres` | 14 passed |
| `test_task225_lifecycle_boundaries_postgres` | 12 passed |
| `test_task225_signal_to_inspection_postgres` | 14 passed |
| `test_task228_schema_readiness_postgres` | 14 passed |
| `test_task229_collector_finalization_postgres` | 4 passed |
| **`test_task232_management_analytics_postgres` (new)** | **29 passed** |

Comparison with the same code before this task: TASK_230's recorded run on `126b62e` had 1418 passed, 0 failed and 117 skipped in the backend lane, 117 passed in the ops lane, and 116 passed in the PostgreSQL lane.
- The backend lane grew by exactly 59 tests: the 30 new contract tests pass, and the 29 new PostgreSQL tests skip without `AGROSAT_TEST_DATABASE_URL`.
- The skip lists differ by exactly those 29 tests (`evidence\base_126b62e_skips.txt` vs `evidence\code_c9be969_skips.txt`).
- No baseline test changed outcome.

New suites:

| suite | tests | covers |
|---|---|---|
| `test_task232_management_analytics_postgres.py` | 29 | Contract: zero snapshot, read-only (table snapshots), invalid parameters including a refused `crop_type_id`, empty past period. Lifecycle: every current TASK_225 state once (sum = total), windowed activity, outcomes, completion, sample counts, closed/rejected/cancelled not current, satellite signal → inspection, candidates and alerts counted once. Exclusions: TASK_209 legacy rows, missing / quality-blocked / inconclusive never outcomes, pending verification not terminal. Tenancy: server scope wins, filters only narrow, foreign = missing (404 bodies), roles. Crop semantics: current classification rule; a crop change between seasons is reported by current crop and never claimed as crop at event time. Aggregation: enterprise, current-crop, field (paged) and period rows reconcile; same-named figures equal the command center (`overdue_work`, `awaiting_satellite_verification` via `plans_pending_verification`, `reopened`, `not_improved`, `verification_blocked`, `awaiting_evidence`, `awaiting_work`, `awaiting_field_inspection`, `active_situations`); work/evidence/verification rows do not multiply; reopened cycles per cycle. Overdue: one plan with the main item on time and a secondary item late matches the command center (0 overdue cases, 1 overdue work item = the one per-item notification), and every stage follows `is_overdue`. Time: half-open local boundaries and the UTC trap, local week and month buckets, event anchors, open cycles, median/p90/sample count. Query budget: `(1, 2)` statements constant with growing data |
| `test_task232_management_analytics_contract.py` | 30 | GET-only secured route in OpenAPI, scope and 404/403 rules, SQL scope fragments, no retired tables, no ORM loading. `overdue_cases` is the case model's `is_overdue` and H1 carries no rule of its own. No current figure reuses a command-center summary name. The crop dimension is declared current-only. Also: outcome vocabulary equals policy r3-f-v1, pinned definitions fingerprint, p50/p90 rules, rates, local calendar buckets, strict versioned schema, 422/403/404 before any SQL (including the `crop_type_id` refusal), 401 |

Evidence (logs, JUnit XML, scripts, performance probes, example response): `C:\AgroSat_backups\TASK_232_H1_MANAGEMENT_ANALYTICS_EVIDENCE_20260926`.

## Alembic

- Starting head: `0016_operational_command_center` (single head).
- Ending head: `0016_operational_command_center` (single head). `rollback-contract` reports `PASS`, head `0016_operational_command_center`, every revision classified.
- Migration added: no. `git diff 126b62e..HEAD -- backend/alembic backend/models` is empty. The crop decision needed no schema change: it declares the limits of the existing data instead of inventing facts.

## Rollback, staging checks, side effects

- **Rollback.** Nothing is deployed. If the branch is merged and released later, rolling back is a code rollback through the TASK_230 control plane.
  - There is no schema or data change to undo, and the endpoint writes nothing (tested: table snapshots before and after are identical).
  - Removing the two `main.py` lines disables the endpoint.
- **What to check on staging.** As a manager, call `GET /api/management-analytics` and `/api/operational-center/summary` and compare:
  - `current.overdue_cases.total` = `overdue_work`
  - `current.plans_pending_verification` = `awaiting_satellite_verification`
  - `current.active_problems.total + data_unavailable.*` = `active_situations`
  - `reopened`, `not_improved` and `verification_blocked` match

  Then check the error paths: `crop_type_id=1` → 422, and `enterprise_id=<another enterprise>` → 404.
- **Side effects.** The endpoint is read-only: it takes no row locks and writes nothing. The DB load of one call is about one Operational Center summary plus the H1 CTEs. The only shared code change is a router registration. `/api/executive`, the Operational Center and the TASK_217/220 workflows are unchanged.

## Residual gaps (non-blocking)

1. **Optional index for 10× scale.**
   - At the 3,000-field synthetic volume the admin-global snapshot is dominated by the TASK_221 case model, which already costs the same in the deployed `/api/operational-center/summary`.
   - An index leading with `agronomy_plans.inspection_id` measurably helps both endpoints (probe in the isolated perf database only, see Query design).
   - It belongs in one isolated migration task if growth approaches that volume; it is not needed at current or realistic volume.
2. **Pre-existing defect outside TASK_232.**
   - `POST /api/anomaly-inspections/{id}/cancel` on an unassigned `new` canonical inspection violates `ck_field_inspections_assignment_state` (`source_kind='legacy' OR status='new' OR assigned_to_id IS NOT NULL`). The CheckViolation is unhandled, so the request would answer 500. It was observed while building this suite, which now documents it and cancels an assigned inspection instead.
   - The consequence is that such inspections cannot be cancelled, so they stay in `needs_inspection`.
3. **Alert-origin signal time is unsupported.** `alerts.triggered_at` is `timestamp without time zone`, so the time from an alert to its inspection cannot be measured safely.

The API is ready for a later frontend task.
