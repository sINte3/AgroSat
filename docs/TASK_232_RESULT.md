# TASK_232 — H1 MANAGEMENT ANALYTICS V1 BACKEND RESULT

## Verdict

PASS_TASK_232

## Base

- Base: `origin/main` = `126b62ed45267e341cd727fd678ddd574e35292c`, verified after `git fetch origin` on 2026-09-26. Production runs the same SHA (TASK_231).
- Branch: `task/232-h1-management-analytics-backend`, created from that exact SHA. The local `C:\AgroSat` `main` (97f1653) was not used.
- Worktree: `C:\AgroSat_worktrees\task_232`. `C:\AgroSat` was left alone: `.claude\settings.json` stays modified and `task 231.md` stays untracked, exactly as they were.
- Commits:
  - `82fdf5a` — code and tests.
  - `9a920a3` — classifies the endpoint in the repository's authorization matrix. This is the final code commit, and it was regressed.
  - The docs-only commit that adds this report.

## Scope

Files changed:

| file | change |
|---|---|
| `backend/api/management_analytics.py` | new read-only router, `GET /api/management-analytics` |
| `backend/services/management_analytics.py` | new service: scope, one bounded SQL statement, response assembly |
| `backend/schemas/management_analytics.py` | new Pydantic response contract (`extra="forbid"`) |
| `backend/main.py` | two lines: import and `include_router` |
| `backend/tests/task232_support.py` | new PostgreSQL harness (extends the TASK_225 harness) |
| `backend/tests/test_task232_management_analytics_postgres.py` | new PostgreSQL suite, 27 tests |
| `backend/tests/test_task232_management_analytics_contract.py` | new database-free contract suite, 26 tests |
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
| current problem load, funnels | TASK_221 Operational Center case model `services/operational_center.py::CASES_CTE` (inspection, candidate and alert cases), classified by the TASK_225 projection `services/remediation_status.py::inspection_status_sql`, joined 1:1 to `field_inspections` and to the case's current `agronomy_plans` row |
| work items, plan overdue | `agronomy_work_items` of the current cycle (`w.cycle = p.cycle`) of live plans: the TASK_220 rule in `services/closed_loop_agronomy.py::queue_filter` |
| inspection overdue | `field_inspections.due_at`, the TASK_217 rule in `services/anomaly_inspections.py`; for legacy rows that only have a date, `due_date` |
| data unavailable | Operational Center `freshness` and `external` cases (`remediation_status = data_unavailable`) |
| period activity | `field_inspections.created_at`, `reviewed_at`, `cancelled_at`; `autonomous_anomaly_candidates.created_at`; `agronomy_plans.created_at`; `agronomy_events` (`approve`, `work_complete`, `cancel`) |
| verified outcomes, reopen | `agronomy_events` resolution events (`close`, `override_close`, `rework`/`reinspection` from `pending_verification`, `reopen`) and the latest `agronomy_verifications` row of the same plan cycle |
| cycle times | the canonical timestamps named per metric below |
| completion | the same events and verification rows, grouped per plan cycle |
| crop dimension | `crop_seasons` + `crop_types`: the field's latest season with `season_year` ≤ the generation year in Asia/Tashkent (the Operational Center rule) |

Plan cycles: `agronomy_events` has no cycle column. The cycle of an event is 1 plus the number of earlier cycle-opening events of the same plan, ordered by `id`. A cycle-opening event is `rework` or `reinspection` from `pending_verification`, or `reopen` from `closed`. This mirrors `closed_loop_agronomy.transition`, where exactly these transitions increment `agronomy_plans.cycle`. The PostgreSQL tests check the derived cycles against the `cycle` column of `agronomy_verifications`.

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
| `monitored_fields_with_active_problems` | distinct monitored fields with at least one active problem case |
| `ndvi_freshness.*` | monitored fields by `satellite_field_freshness.status` for `ndvi` (FRESH, AGING, STALE, NEVER_COLLECTED, CLOUD/QUALITY_BLOCKED, PROVIDER_DEGRADED); `not_evaluated` means no freshness row yet |

### Current problem load (current)

Population: Operational Center case rows in scope whose root source is `inspection`, `candidate` or `alert` and whose TASK_225 remediation status is one of the 11 non-terminal states. The model yields exactly one row per canonical instance:

- An **inspection case** is a `field_inspections` row in `pending/new/assigned/in_progress/submitted`, or a `confirmed` row that has a current plan or has never had a plan.
  - The current plan is the inspection's single live plan. If there is none, it is its latest plan closed within 30 days.
  - A confirmed inspection whose plans are all cancelled or superseded, or were closed more than 30 days ago, is not current (inherited rule).
- A **candidate case** is an `autonomous_anomaly_candidates` row in `NEW/CONFIRMED` without an inspection.
- An **alert case** is an active alert that no open canonical inspection references (`source_alert_id`). Opening the inspection replaces the alert case, so it is never counted twice (tested).

| key | definition |
|---|---|
| `active_problems.total` | active cases in scope |
| `fields_affected` | distinct fields with at least one active case |
| `by_source.*` | active cases by root source |
| `by_priority.*` | Operational Center `priority_rank`: 0 critical (plan/inspection `urgent`, candidate `EXTREME`, alert `critical`), 1 high, 2 normal (`normal/medium/moderate`, alert `warning`), 3 low |
| `legacy_open_inspections` | active inspection cases whose row is a pre-0013 legacy inspection (`source_kind='legacy'`, `pending/in_progress`). The TASK_225 projection still counts them as workload to drain; they are shown separately |

### Inspection funnel (current)

The keys are the canonical remediation states. They map only to accepted state-machine values.

| key | canonical mapping |
|---|---|
| `needs_inspection` | inspection `new`/`assigned` (legacy `pending`) without a current plan, plus candidate and alert cases. Sub-keys: `inspections`, `candidates`, `alerts`, and `unassigned_inspections` (inspection rows with `assigned_to_id IS NULL`) |
| `inspection_active` | inspection `in_progress` (canonical or legacy) |
| `awaiting_review` | inspection `submitted` without a current plan |
| `awaiting_decision` | inspection `confirmed` without a current plan |

### Remediation funnel (current)

The case's current TASK_220 plan determines the state.

| key | canonical mapping |
|---|---|
| `plan_active` (`draft`, `approved`) | plan `draft` / `approved` |
| `work_active` | plan `in_progress` |
| `awaiting_satellite_verification` (`pending_data`, `too_early`) | plan `pending_verification` with `PENDING_DATA` / `TOO_EARLY` |
| `verification_blocked` (`cloud_blocked`, `quality_blocked`, `provider_degraded`, `inconclusive`) | plan `pending_verification` with a status that cannot conclude. This is the only blocked state that TASK_220 represents; work items have no blocked state |
| `improved_awaiting_closure` | plan `pending_verification` with `IMPROVED` (provisional, not yet an outcome) |
| `not_improved` (`unchanged` = `NO_MATERIAL_CHANGE`, `worsened`) | plan `pending_verification` with a non-improving conclusive status |
| `reopened` | plan `rework`: returned after verification (`rework`/`reinspection`) or reopened after closure (`reopen`) |

The funnel stages sum to `active_problems.total` (tested).

### Work items, overdue, data availability (current)

| key | definition |
|---|---|
| `work_items.active/planned/in_progress/unassigned` | `planned`/`in_progress` work items in the current cycle of live plans (not `closed/cancelled/superseded`) |
| `overdue.inspections` | inspection-stage cases (no current plan) in `pending/new/assigned/in_progress/submitted` whose deadline has passed: `due_at < as_of` (TASK_217). A legacy date-only `due_date` becomes overdue once the local due day has ended (`due_date < as_of` local date) |
| `overdue.work_items` = `work_items.overdue` | active current-cycle work items of live plans with `due_at < as_of` (TASK_220 rule) |
| `overdue.plans_with_overdue_work` | live plans with at least one such item (TASK_220 summary `overdue`) |
| `overdue.cases` | `inspections + plans_with_overdue_work`. A case is either in the inspection stage or the plan stage, so nothing is double-counted |
| `data_unavailable.freshness_cases` | Operational Center freshness cases: field × index not FRESH without an open case on that index |
| `data_unavailable.external_cases` | Operational Center external cases: the latest collection run is degraded, failed or a stale `running` run. One per enterprise with active fields; enterprise-level, so shown only without a field or crop filter |

Stages with no deadline in the domain model are never overdue: candidates, alerts, `awaiting_decision`, draft plans whose work has no due date, and plans waiting for verification.

The Operational Center's own `is_overdue` looks only at a case's primary active work item (in progress first). H1 instead applies the TASK_220 rule, so a plan is overdue if any active item of its current cycle is overdue. The two can therefore differ for a plan that has an in-progress item on time and a planned item late.

### Period activity (windowed)

| key | anchor |
|---|---|
| `anomaly_candidates_detected` | `autonomous_anomaly_candidates.created_at` |
| `inspections_opened.total/manual/alert/pixel_ndvi` | `field_inspections.created_at`, canonical rows only (`source_kind <> 'legacy'`) |
| `inspections_confirmed` / `inspections_rejected` | `reviewed_at` of rows now `confirmed` / `rejected` (terminal review states) |
| `inspections_cancelled` | `cancelled_at`, canonical rows |
| `plans_drafted` | `agronomy_plans.created_at` |
| `plan_cycles_approved` | `agronomy_events.occurred_at` of `approve` (one per plan cycle) |
| `plan_cycles_work_completed` | `occurred_at` of the `work_complete` event whose resulting status is `pending_verification`. There is one per cycle, however many work items the cycle has |
| `plan_cycles_verified` | `agronomy_verifications.created_at` of the first conclusive row of a plan cycle (one per cycle, however many re-evaluations follow) |
| `plans_cancelled` | `occurred_at` of `cancel` |

### Verified outcomes and reopen (windowed)

Population: plan cycles resolved in the period. A cycle is resolved when it closes (`close`, `override_close`) or is returned after verification (`rework`/`reinspection` from `pending_verification`). A verified cycle ends exactly once, so it is counted once. The anchor is the resolution event time.

The outcome is the status of the cycle's latest `agronomy_verifications` row, or `PENDING_DATA` when the cycle has none. This equals `agronomy_plans.verification_status` at resolution: rows are written only while the plan is `pending_verification`, and every new row also sets that column.

| key | definition |
|---|---|
| `resolved_cycles` | resolved cycles |
| `verified.improved/unchanged/worsened/total` | outcome `IMPROVED` / `NO_MATERIAL_CHANGE` / `WORSENED` |
| `unverified.*` | resolved with `PENDING_DATA`, `TOO_EARLY`, `CLOUD_BLOCKED`, `QUALITY_BLOCKED`, `PROVIDER_DEGRADED` or `INCONCLUSIVE`. These are never outcomes |
| `closed.total/improved/without_improvement` | resolutions that closed the plan, split by an `IMPROVED` outcome (improved_closed vs closed_without_improvement) |
| `returned_for_rework` | resolutions that opened another cycle |
| `reopened.after_closure` | `reopen` events (closed → rework), anchored at event time |
| `reopened.after_verification` | `rework`/`reinspection` events from `pending_verification`, anchored at event time |

A verification that is conclusive but still waiting for a human decision is not an outcome yet. It appears in `current.remediation_funnel` and in `completion.verification_completion`.

### Cycle times (windowed)

Unit: hours, rounded to 2 decimals.
- Samples are completed, correctly ordered pairs only (end ≥ start). An open cycle has no end event and contributes nothing.
- `median_hours` is `percentile_cont(0.5)`, reported from one sample.
- `p90_hours` is `percentile_cont(0.9)`, reported only from 10 samples (`p90_minimum_samples`), otherwise `null`.
- `status` is `measured` or `no_samples`.
- Each metric's `start_event`, `end_event`, `period_anchor` and `population` are also returned in the response.

| metric | start event | end event (= period anchor) | population |
|---|---|---|---|
| `signal_to_inspection_opened` | `autonomous_anomaly_candidates.created_at` | `field_inspections.created_at` | canonical inspections opened from a candidate |
| `inspection_opened_to_reviewed` | `field_inspections.created_at` | `field_inspections.reviewed_at` | canonical inspections confirmed or rejected |
| `inspection_submitted_to_plan_drafted` | `field_inspections.submitted_at` | `agronomy_plans.created_at` of the inspection's first plan | inspections whose first plan was drafted. A plan may be drafted from a submitted inspection, so review time would not be a valid start |
| `plan_approved_to_work_completed` | `approve` event of the cycle | `work_complete` → `pending_verification` event of the same cycle | plan cycles whose work was completed |
| `work_completed_to_verified` | `agronomy_verifications.completed_at` (cycle completion) | `created_at` of the cycle's first conclusive verification | plan cycles with a conclusive verification. At least 8 days by policy r3-f-v1 |
| `case_opened_to_verified_closure` | `field_inspections.created_at` of the case root | `agronomy_plans.closed_at` | plans currently `closed` with a conclusive verification. A reopened case leaves the population until it closes again |

Unsupported: `alert_signal_to_inspection_opened`. `alerts.triggered_at` is `timestamp without time zone`, so no timezone-safe signal time is persisted for alert-origin cases. Manual cases have no signal before the inspection.

### Completion (cohorts)

`rate = numerator / denominator`, rounded to 4 places, and `null` when the denominator is 0.

| key | denominator (cohort in the period) | numerator and splits (as of `generated_at`) |
|---|---|---|
| `work_completion` | plan cycles approved in the period | cycles whose work was completed; `ended_without_completion` (cycle cancelled or superseded); `open` |
| `verification_completion` | plan cycles whose work was completed in the period | cycles whose latest verification is conclusive; `improved`, `unchanged`, `worsened`, `not_conclusive`; `improved_rate = improved / denominator` |
| `plan_closure` | plans drafted in the period and not superseded (the replacement plan carries the case) | plans now `closed`; `closed_improved` (verification `IMPROVED`), `closed_without_improvement`, `cancelled`, `open` |

Completed work is never reported as success. Success is only the verification outcome.

### Breakdowns

- `enterprises`, `crops`, `fields`: each row has `monitored_fields`, `current` and `period` numbers.
  - `current` holds `active_problems`, the 11 states, `overdue_cases` and `overdue_work_items`.
  - `period` holds `inspections_opened`, `resolved_cycles`, `improved`, `unchanged`, `worsened`, `unverified` and `reopened`.
  - Enterprise and crop rows are sums of the field rows computed in the same statement, so they reconcile exactly with the totals (tested). The crop row `crop_type_id = null` groups fields without a crop season.
- `fields` is paged: `field_limit` 1..200 (default 50), `field_offset` 0..10000, plus `total`.
  - Order: active problems desc, overdue cases desc, resolved cycles desc, name, id.
  - Paging covers every field exactly once (tested).
- Enterprise rows are capped at 500 and crop rows at 200. Above the cap the response is `422 result_too_large` (the `api/query_bounds.py` convention), never silently truncated.
- `periods`: `granularity` `day`, `week` (ISO Monday) or `month` (default `week`) in the Asia/Tashkent local calendar.
  - The first and last buckets are clipped to the period. Empty buckets are present with zeros.
  - Their counts sum to the windowed totals (tested).
- External cases are enterprise-level and appear only in `current.data_unavailable`.

## Legacy exclusion

- The statement never references `corrective_actions`, `action_verification_requests` or `operational_audit_events`. A contract test pins this, and the PostgreSQL suite asserts it on the SQL that is actually executed. `provenance.excluded_legacy_sources` names both TASK_209 tables.
- `period_activity` and the cycle times exclude `field_inspections` rows with `source_kind='legacy'`. Completed legacy inspections are in no section.
- Open legacy inspections (`pending`/`in_progress`) remain current workload, as the TASK_225 projection and the Operational Center define it: they must be drained through the canonical close-out. They are reported separately in `legacy_open_inspections`.
- `test_legacy_task209_history_never_becomes_current_truth` seeds legacy inspections, a legacy result, open, closed and reopened corrective actions, and two resolved `improved` `action_verification_requests`. Every windowed and outcome number stays 0, and every completion denominator is 0.

## Authorization

- Authentication comes from `get_current_active_user`; without it the endpoint answers 401.
- Roles are `admin` and `manager`, the precedent set by `/api/executive`. `agronomist` and `viewer` get 403 before any SQL: an agronomist's Operational Center scope is limited to assigned work, and enterprise-wide aggregates would widen it. A manager without an enterprise gets 403.
- Effective scope = server authority ∩ request filters:
  - A manager is bound to `users.enterprise_id` (`scope.authorization = "tenant"`).
  - An admin is global (`"global"`).
  - `enterprise_id`, `field_id` and `crop_type_id` only add conditions. They are never used to widen scope.
- Non-enumerating responses:
  - A manager naming another enterprise, or anyone naming a non-existent one, gets the identical `404 {"detail":"Enterprise not found"}`.
  - A field outside the effective scope and a non-existent field both get `404 {"detail":"Field not found"}`. An admin combining enterprise A with a field of B gets the same.
  - An unknown crop type gets `404 {"detail":"Crop type not found"}`. `crop_types` is a global reference table, so this reveals no tenant data.
  - Tests compare status and body byte for byte.
- `scope` in the response echoes only the caller's own role, the effective enterprise and the validated filters.

## Query design

- **Statements.** A request runs exactly 1 SQL statement, or 2 when a filter is given: a single existence check for the filter targets, then the snapshot statement.
  - The snapshot statement is `CASES_CTE` followed by the H1 CTEs, returning one row of JSON aggregates.
  - Because the whole snapshot is one statement, it reads one consistent snapshot of the data (READ COMMITTED gives each statement its own snapshot), and its sections reconcile with each other even under concurrent writes.
  - `test_statement_count_does_not_grow_with_the_data` pins `(1, 2)` on an empty scope and again on the full panorama with crop seasons.
- **No N+1.** Only `text()` SQL is used. No ORM entity is loaded, no relationship is traversed, and nothing is issued per row, per enterprise, per field or per plan (contract test). One-to-many joins are avoided:
  - Cases join their inspection and plan 1:1 by primary key.
  - Work items and verification rows are aggregated separately per plan cycle, or tested with `EXISTS`.
  - Tests prove that 3 work items, 12 photos and 2 verification rows per cycle count one case, one completed cycle and one verified cycle.
- **Tenant pushdown.** The tenant and field conditions are applied both to `scope_fields` and inside the case model (`c.enterprise_id`, `c.field_id`), so PostgreSQL pushes them into each branch.
- **Bounded output.** The period is at most 366 days (≤ 366 day buckets), the field page is ≤ 200 rows, and enterprise and crop rows are capped.
- **Measured performance.** Service call timings, isolated databases, PostgreSQL 16.14 on the production host, 7 runs, median:

| volume | scope | median |
|---|---|---|
| 300 fields, 1,200 inspections, 600 plans, 3,840 events, 900 open problems | admin, 30 days | 191 ms |
| same | admin, 366 days, day buckets | 253 ms |
| same | manager (60 fields) | 64–71 ms |
| same | one field | 30 ms |
| 3,000 fields, 12,000 inspections, 6,000 plans, 38,400 events, 9,000 open problems | admin, 30 / 366 days | 2.9–3.1 s |
| same | manager (600 fields) | 631–671 ms |
| same | one field | 41 ms |

  - For reference, production on 2026-09-24 had 275 active fields, 1 inspection and 0 plans.
  - At the 3,000-field volume, the accepted `operational_center.summary` alone takes 2.6 s on the same data.
  - `EXPLAIN (ANALYZE, BUFFERS)` puts almost all of the time inside the TASK_221 case model:
    - The current-plan `LATERAL` scans `uq_agronomy_plan_binding`, because no index leads with `agronomy_plans.inspection_id` for closed plans: about 1.0 s.
    - The freshness-case `NOT EXISTS` uses a `BitmapAnd`: about 1.2 s.
  - The H1 CTEs add about 0.3–0.4 s.
  - A probe index `agronomy_plans(inspection_id)`, created and dropped in the isolated perf database only, gave:
    - admin 2.9 → 1.9 s
    - manager 630 → 163 ms
    - `/api/operational-center/summary` 2.64 → 1.59 s
  - No index is required for acceptable performance at realistic volume, so none was added (see residual gaps).

## API contract

`GET /api/management-analytics`. Read-only; the router registers no other method or path. Query parameters:

| parameter | type | rule |
|---|---|---|
| `date_from`, `date_to` | date | inclusive, Asia/Tashkent; default the last 30 days ending today; `date_from > date_to` → 422; more than 366 days → 422 |
| `enterprise_id` | int > 0 | narrows; foreign or missing → 404 |
| `field_id` | int > 0 | narrows; outside scope or missing → 404 |
| `crop_type_id` | int > 0 | narrows to fields whose current crop season has this crop; missing → 404 |
| `granularity` | `day` \| `week` \| `month` | default `week` |
| `field_limit`, `field_offset` | int | 1..200 (default 50), 0..10000 (default 0) |

Top-level response keys:
- metadata: `definitions_version` (`management_analytics_v1`), `generated_at`, `timezone`, `scope`, `period` (`requested`, `effective` with `starts_at` and `ends_before`), `provenance`
- current state: `coverage`, `current`
- windowed: `period_activity`, `outcomes`, `cycle_times`, `completion`
- `breakdowns`, `limitations`

`provenance.definitions_fingerprint` is the SHA-256 of every definition the response depends on: the case model SQL, the projection, this statement, the duration catalogue, the policy version and the p90 threshold. The contract test pins it next to `definitions_version`, so any change to a definition fails a test until the version has been reviewed. The fingerprint is identical for LF and CRLF checkouts (verified).

Example (sanitized). It is the manager response from the TASK_232 panorama test scenario, with synthetic names and ids. `breakdowns.fields.items` is shortened to 1 of 3 rows, and `limitations` to 1 of 7 entries:

```json
{
  "definitions_version": "management_analytics_v1",
  "generated_at": "2026-09-26T11:27:42.554503+05:00",
  "timezone": "Asia/Tashkent",
  "scope": {
    "role": "manager",
    "authorization": "tenant",
    "enterprise_id": 1,
    "field_id": null,
    "crop_type_id": null
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
    "definitions_fingerprint": "691a9ccb2fa939de87a5b910a8ff24a3054560201af3b6995fb3c508062a9776"
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
    "as_of": "2026-09-26T11:27:42.554503+05:00",
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
    "inspection_funnel": {
      "needs_inspection": {
        "total": 3,
        "inspections": 2,
        "candidates": 0,
        "alerts": 1,
        "unassigned_inspections": 1
      },
      "inspection_active": 1,
      "awaiting_review": 1,
      "awaiting_decision": 1
    },
    "remediation_funnel": {
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
    "work_items": {
      "active": 3,
      "planned": 1,
      "in_progress": 2,
      "unassigned": 0,
      "overdue": 0
    },
    "overdue": {
      "cases": 0,
      "inspections": 0,
      "plans_with_overdue_work": 0,
      "work_items": 0
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
    "reopened": {
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
          "reopened": 1,
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
          "reopened": 1
        },
        "enterprise_id": 1,
        "enterprise_name": "T225 Alpha"
      }
    ],
    "crops": [
      {
        "monitored_fields": 1,
        "current": {
          "active_problems": 1,
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
          "reopened": 0,
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
          "reopened": 0
        },
        "crop_type_id": 176,
        "crop_name": "T232 Пшеница"
      },
      {
        "monitored_fields": 1,
        "current": {
          "active_problems": 2,
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
          "reopened": 0,
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
          "reopened": 0
        },
        "crop_type_id": 175,
        "crop_name": "T232 Хлопок"
      },
      {
        "monitored_fields": 12,
        "current": {
          "active_problems": 12,
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
          "reopened": 1,
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
          "reopened": 1
        },
        "crop_type_id": null,
        "crop_name": null
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
        "reopened": 0
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
        "reopened": 0
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
        "reopened": 0
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
        "reopened": 0
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
        "reopened": 1
      }
    ],
    "fields": {
      "items": [
        {
          "monitored_fields": 1,
          "current": {
            "active_problems": 2,
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
            "reopened": 0,
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
            "reopened": 0
          },
          "field_id": 3,
          "field_name": "T225 Quiet 0",
          "enterprise_id": 1,
          "enterprise_name": "T225 Alpha",
          "crop_type_id": 175,
          "crop_name": "T232 Хлопок"
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

The script `C:\AgroSat_backups\TASK_232_H1_MANAGEMENT_ANALYTICS_EVIDENCE_20260926\scripts\final_regression.sh` mirrors `.github/workflows/ci.yml` and `postgres.yml`. It ran first on `82fdf5a` and again, finally, on `9a920a3`. Logs and JUnit XML are under `evidence\code_82fdf5a` and `evidence\code_9a920a3`.

Final run on code commit `9a920a3` (the docs-only commit that follows changes no code):

| lane | command (from `C:\AgroSat_worktrees\task_232`) | result |
|---|---|---|
| guard: migration graph | `python -B ops/release/Invoke-AgroSatControlPlane.py rollback-contract --backend backend` | exit 0, `status: PASS`, head `0016_operational_command_center` |
| guard: Alembic heads | `cd backend && python -m alembic heads` | `0016_operational_command_center (head)`, single head |
| guard: startup safety | `python -m pytest tests/test_web_startup_safety.py -q -p no:cacheprovider` | 6 passed |
| guard: retired endpoints | `python -m pytest -q -p no:cacheprovider tests/test_field_inspections.py tests/test_satellite_write_safety.py tests/test_task209_operational_closure_backend.py tests/test_task225_contracts.py` | 137 passed |
| backend (no database URL) | `cd backend && python -m pytest tests -q -p no:cacheprovider` | **1444 passed, 0 failed, 144 skipped** |
| ops: PowerShell parse | parser over `ops/**/*.ps1` (as in `ci.yml`) | 24 parsed, 0 failed |
| ops tests | `python -m pytest ops/tests -q -p no:cacheprovider` | 117 passed |
| PostgreSQL: current revision | `python -m alembic current` on `agrosat_h0a_task232` | `0016_operational_command_center (head)` |
| PostgreSQL, one process per file | `python -m pytest <file> -q -p no:cacheprovider` for every `tests/*postgres*.py` | 143 passed, 0 failed (table below) |

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
| **`test_task232_management_analytics_postgres` (new)** | **27 passed** |

Comparison with the same code before this task: TASK_230's recorded run on `126b62e` had 1418 passed, 0 failed and 117 skipped in the backend lane, 117 passed in the ops lane, and 116 passed in the PostgreSQL lane.
- The backend lane grew by exactly 53 tests: the 26 new contract tests pass, and the 27 new PostgreSQL tests skip without `AGROSAT_TEST_DATABASE_URL`.
- The skip lists differ by exactly those 27 tests (`evidence\base_126b62e_skips.txt` vs `evidence\code_9a920a3_skips.txt`).
- No baseline test changed outcome.
- The first full run, on `82fdf5a`, found one failure: `test_task209_authorization_matrix::test_matrix_covers_every_openapi_operation_exactly`. The repository requires every OpenAPI operation to be classified. `9a920a3` adds the endpoint to the matrix (admin and manager, enterprise filter, cross-tenant 404; 146 operations).

New suites:

| suite | tests | covers |
|---|---|---|
| `test_task232_management_analytics_postgres.py` | 27 | Contract: deterministic zero snapshot, read-only (table snapshots), invalid period and paging, empty past period. Lifecycle: every current canonical state once (11 states, funnel = total), windowed activity, outcomes, completion, sample counts, closed/rejected/cancelled not current, satellite signal → inspection, candidates and alerts counted once. Exclusions: TASK_209 legacy rows, missing / quality-blocked / inconclusive never outcomes, pending verification not terminal. Tenancy: server scope wins, filters only narrow, foreign = missing (404 bodies), roles, current crop season. Aggregation: enterprise, crop, field (paged) and period rows reconcile with totals; parity with `/api/operational-center/summary`; work/evidence/verification rows do not multiply; reopened cycles per cycle. Time: half-open local boundaries and the UTC trap, local week and month buckets, event anchors, open cycles, median/p90/sample count, overdue per stage. Query budget: statement count `(1, 2)` constant with growing data; executed SQL never names the TASK_209 tables |
| `test_task232_management_analytics_contract.py` | 26 | GET-only secured route in OpenAPI, scope and 404/403 rules, SQL scope fragments, no retired tables, no ORM loading, outcome vocabulary equals policy r3-f-v1, pinned definitions fingerprint, p50/p90 rules, rates, local calendar buckets, strict versioned schema, 422/403/404 answered before any SQL, 401 |

Evidence (logs, JUnit XML, scripts, performance probes, example response): `C:\AgroSat_backups\TASK_232_H1_MANAGEMENT_ANALYTICS_EVIDENCE_20260926`.

## Alembic

- Starting head: `0016_operational_command_center` (single head).
- Ending head: `0016_operational_command_center` (single head). `rollback-contract` reports `PASS`, head `0016_operational_command_center`, every revision classified.
- Migration added: no. `git diff 126b62e..HEAD -- backend/alembic backend/models` is empty.

## Rollback, staging checks, side effects

- **Rollback.** Nothing is deployed. If the branch is merged and released later, rolling back is a code rollback through the TASK_230 control plane.
  - There is no schema or data change to undo, and the endpoint writes nothing (tested: table snapshots before and after are identical).
  - Removing the two `main.py` lines disables the endpoint.
- **What to check on staging.** Each of these calls should return 200 with `definitions_version = management_analytics_v1`:
  - as a manager, `GET /api/management-analytics`
  - as a manager, the same call with `enterprise_id=<another enterprise>` → 404
  - as an admin, `?granularity=month&date_from=…&date_to=…`

  Then compare `current` with `/api/operational-center/summary` for the same user. `active_situations` should equal `active_problems.total + data_unavailable.*`, and `reopened`, `not_improved` and `verification_blocked` should match.
- **Side effects.** The endpoint is read-only: it takes no row locks and writes nothing. The DB load of one call is about one Operational Center summary plus the H1 CTEs. The only shared code change is a router registration. `/api/executive`, the Operational Center and the TASK_217/220 workflows are unchanged.

## Residual gaps

1. **Cost of the shared case model at 10× volume.**
   - At 3,000 fields and 9,000 open problems, the admin-global snapshot takes about 3 s. About 2.6 s of that is the TASK_221 case model, which already costs the same in the deployed `/api/operational-center/summary`.
   - An index leading with `agronomy_plans.inspection_id` measurably helps both endpoints.
   - If growth approaches that volume, it belongs in one isolated migration task. It is not needed at current or realistic volume (about 0.2 s admin, 0.07 s manager).
2. **Pre-existing defect found (not fixed; outside scope).**
   - `POST /api/anomaly-inspections/{id}/cancel` on an unassigned `new` canonical inspection violates `ck_field_inspections_assignment_state` (`source_kind='legacy' OR status='new' OR assigned_to_id IS NOT NULL`). The CheckViolation is unhandled, so the request would answer 500. It was observed while building this suite. The suite now documents the constraint and cancels an assigned inspection instead.
   - The consequence is that such inspections cannot be cancelled, so they stay in `needs_inspection`.
3. **Crop attribution is current-season (type-1).** A period in an earlier season is attributed to each field's current crop. Historical rotation is not reconstructed; `crop_seasons.season_year` alone cannot place seasons that span a year boundary.
4. **Alert-origin signal time is unsupported.** `alerts.triggered_at` has no time zone.
5. **Overdue differs from the command center in one edge case.** The Operational Center's per-case `is_overdue` uses only the primary work item, while H1 uses the TASK_220 any-item rule (see Overdue).
6. **The API is ready for a later frontend task.** No frontend consumes it yet.
