# TASK_209 Executive Accountability Metric Contract

## Purpose

This document defines the management read model added by TASK_209 Phase 6.
The read model answers operational accountability questions from persisted
AgroSat records. It does not infer agronomic causes and it does not calculate
tenant-wide totals in the browser.

## Authorization and tenant scope

- `admin` may read the global scope or select one enterprise.
- `manager` may read only the manager's `enterprise_id`.
- `agronomist` does not receive the executive read model; the existing
  inspection and attention views remain the agronomist's operational surface.
- `viewer` does not receive the executive read model.
- A manager-supplied foreign `enterprise_id` is rejected with 403.
- Unknown roles and tenant roles without an enterprise are rejected with 403.
- Every list, total, owner group, enterprise group, and export uses the same
  resolved enterprise predicate.

Frontend visibility is only a usability control. These rules are enforced by
the API service.

## Time and filter semantics

- Local operational dates use `Asia/Tashkent`.
- `date_from` and `date_to` are ISO dates and are inclusive.
- The default reporting window is the 30 local calendar days ending today.
- A requested range may contain at most 366 inclusive calendar days.
- `date_from` must not be later than `date_to`.
- Snapshot backlog metrics are evaluated at the end of `date_to`.
- Duration cohorts and verification outcomes include events whose terminal
  event occurred within the inclusive date window.
- The response always returns the resolved date range and timezone.

## Stable definitions

### Fields requiring attention now

`attention_fields_now` uses the existing deterministic field-attention
algorithm without redefining its score:

- current-season active fields in the resolved tenant scope;
- accepted observations in the configured lookback window;
- active alerts;
- the existing `score_field` priority;
- priority `medium`, `high`, or `critical`;
- evaluated as of `date_to`.

The executive service consumes the server-side attention summary and field
items. It does not recompute this aggregate in React.

### Inspection backlog

- `unassigned_inspections`: `pending` or `in_progress` inspections with
  `assigned_to_id IS NULL` at the snapshot.
- `overdue_inspections`: `pending` or `in_progress` inspections with
  `due_date < date_to`.
- `open_inspections`: all `pending` or `in_progress` inspections.

### Corrective-action backlog

- `open_actions`: actions in `open`, `in_progress`, or `blocked`.
- `overdue_actions`: open actions with `due_date < date_to`.
- `awaiting_verification`: verification requests in
  `awaiting_observation`.
- Every action item returns its persisted owner and deadline.

### Cycle-time metrics

- `attention_signal_to_inspection_hours`: hours from local midnight on
  `source_observation_date` to inspection `created_at`, only for
  `source='attention_queue'`.
- `inspection_to_action_hours`: hours from inspection `completed_at` to
  corrective-action `created_at`.
- `action_to_close_hours`: hours from corrective-action `created_at` to
  `closed_at`.

Each duration metric returns `sample_count`, `median_hours`, and
`p90_hours`. Negative durations are excluded as invalid.

`attention_signal_to_inspection_hours` is a coarse proxy, not an exact queue
residence time: the current schema has no durable `attention_entered_at`
event. The API and UI must expose this limitation.

### Verification outcomes

Resolved verification requests are counted by:

- `improved`;
- `unchanged`;
- `worsened`;
- `insufficient_data`.

The cohort uses `resolved_at` within the date range. An outcome describes the
observed supported-index direction under `observation_direction_v1`; it does
not prove agronomic causality.

### Data quality and freshness

- `attention_stale_fields`: attention fields whose existing spectral summary
  has `data_status='stale'`.
- `attention_no_data_fields`: attention fields whose existing spectral
  summary has `data_status='no_data'`.
- `attention_low_confidence_fields`: attention fields whose existing overall
  confidence is `low` or `insufficient`.
- `latest_observation_by_index`: latest accepted stored observation date for
  NDVI, SAVI, EVI, NDMI, and NDRE in the tenant scope.

No-data and low-confidence counts are operational warnings, not diagnoses.

## API contract

### `GET /api/executive/overview`

Bounded query parameters:

- `date_from`;
- `date_to`;
- `enterprise_id`;
- `attention_lookback_days` from 30 through 365.

The response contains:

- resolved scope and date window;
- backlog totals;
- cycle-time metrics;
- verification outcome counts;
- freshness and confidence warnings;
- per-enterprise accountability aggregates;
- per-owner unresolved-action aggregates;
- metric limitations and definitions version.

### `GET /api/executive/accountability`

Bounded query parameters:

- the same date/scope filters;
- `kind`: `unassigned_inspections`, `overdue_inspections`,
  `open_actions`, `overdue_actions`, or `awaiting_verification`;
- optional `owner_id`;
- `limit` from 1 through 200;
- `offset` from 0 through 10,000.

The response contains one total query and one bounded page query. The list
uses the same predicates as the overview.

### `GET /api/executive/export.xlsx`

The workbook is generated from the same overview read model. It includes:

- Summary;
- Enterprises;
- Owners;
- Definitions and limitations.

The export totals must equal the JSON overview totals for the same filters.

## Query and pagination contract

- The attention algorithm performs four constant queries for a non-empty
  scope and one query for an empty scope.
- Executive operational aggregates use constant CTE/aggregate queries.
- Accountability pages use exactly one total plus one page query.
- Enterprise and owner rows are aggregated in SQL or from the already
  server-computed attention result, never through per-row relationship loads.
- All list endpoints have explicit `limit` and `offset` bounds.
- SQLAlchemy relationship lazy loading is not used.

## UI contract

- Admin sees global or enterprise drill-down.
- Manager sees only the manager's enterprise and cannot select another.
- Agronomist remains on personal attention and inspection workflows.
- Viewer retains existing read-only reports but not the executive
  accountability surface.
- Loading, empty, forbidden, invalid-filter, and server-error states are
  explicit.
- Freshness and confidence warnings are shown before charts or tables.
- Buttons that open backlog records preserve the existing inspection and
  attention routes.
- The Excel control uses the resolved filters shown on screen.

## Known rollout limitation

This read model depends on Alembic revision `0006`. Until B-001 is resolved,
database-backed reconciliation, query-count execution, and export-content
validation remain blocked. Static SQL contracts and deterministic response
fixtures must not be reported as live database validation.

## Definitions version

`task209_executive_v1`
