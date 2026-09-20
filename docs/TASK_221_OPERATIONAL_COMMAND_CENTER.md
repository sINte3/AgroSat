# TASK 221 — Operational Command Center

## Purpose

The Operational Command Center is the authenticated operational surface that joins the already accepted monitoring, anomaly, inspection, agronomy, evidence, weather, and verification workflows. It is a server-owned read model over those workflows, not a replacement lifecycle and not a universal scoring engine.

The primary route is `/operational-center`. The API prefix is `/api/operational-center`.

## Reused authority

The implementation keeps the following existing entities authoritative:

- `autonomous_anomaly_candidates` and satellite freshness/collection state for monitoring facts;
- `alerts` for active accepted alerts;
- `field_inspections`, inspection results, evidence, and audit events for field inspection state;
- `agronomy_plans`, `agronomy_work_items`, `agronomy_verifications`, and `agronomy_events` for closed-loop execution and verification;
- the existing Open-Meteo service for weather context;
- the existing read-only telematics service and TASK 209 Wialon safety contract;
- the existing users, roles, enterprises, fields, crop seasons, and crop types for authorization and context.

TASK 221 does not add copies of inspections, plans, work items, actual executions, or provider observations.

## Persistent additions

Alembic revision `0016_operational_command_center` adds only:

- `operational_notifications`, a tenant- and recipient-scoped current notification state with deterministic deduplication;
- `operational_notification_events`, an append-only transition history with idempotent command identity.

Both tables have explicit foreign keys, composite tenant integrity where a field is present, validation constraints, bounded-query indexes, immutable source identity, and timestamps. A downgrade is allowed while both tables are empty. Once operational history exists, downgrade fails closed and recovery uses the validated pre-TASK-221 backup or a reviewed roll-forward.

## Operational case identity and ordering

Stable case keys are server-derived:

- `inspection:<id>`;
- `candidate:<id>`;
- `alert:<id>`;
- `freshness:<field_id>:<index_code>`;
- `external:<enterprise_id>:<collection_run_id>`.

Tenant identity is never taken from the browser as authority. Tenant roles are constrained in the SQL query itself; unauthorized and cross-tenant object lookups use the established non-enumerating `404` behavior.

Queue ordering is deterministic:

1. overdue work or inspection first;
2. accepted source priority/severity rank;
3. due time, with missing due times last;
4. stale or unavailable external context;
5. latest source event;
6. stable case key.

Every item returns `priority_reasons`; there is no hidden combined employee or case score.

## API

All endpoints require authentication and use bounded limits and deterministic ordering:

- `GET /api/operational-center/queue` — paginated operational queue and filters;
- `GET /api/operational-center/summary` — bounded operational counts under the same authorization/filter contract;
- `GET /api/operational-center/filter-options` — authorized enterprise, field, crop, and assignee choices;
- `GET /api/operational-center/cases/{case_key}` — source snapshot, work/evidence, weather, telematics, verification, notifications, links, and timeline;
- `GET /api/operational-center/fields/{field_id}/timeline` — bounded accepted workflow history;
- `GET /api/operational-center/notifications` — the current user's paginated inbox;
- `POST /api/operational-center/notifications/{id}/transition` — idempotent `read` or `dismiss` transition with optimistic versioning.

The queue and summary are based on explicit SQL common-table expressions. Detail and timeline execute bounded fixed query sets. No ORM relationship or lazy loading is added, and list endpoints make no per-row provider calls.

## RBAC

- `admin`: authorized global read scope and notification transitions for notifications addressed to that user;
- `manager`: enterprise-scoped operational read and addressed notification transitions;
- `agronomist`: enterprise scope further restricted to assigned inspections or active assigned work;
- `viewer`: enterprise-scoped read-only access; notification transitions are forbidden;
- unauthenticated callers receive `401`;
- unauthorized or cross-tenant objects receive `404`.

Planning, assignment, execution, and verification continue through the existing inspection and “Меры и контроль” workflows and their existing role policies.

## Notifications and reconciliation

`backend/scripts/reconcile_operational_notifications.py` is the only TASK 221 notification generator. It supports explicit `--dry-run` or `--apply`, a maximum batch of 500, a transaction-scoped PostgreSQL advisory lock, sanitized status output, and release identity. It never runs from FastAPI lifespan.

The reconciler generates deterministic recipient-specific notifications for:

- new critical situations;
- assignment;
- due within 24 hours;
- overdue work or inspection;
- missing required execution result;
- awaiting eligible satellite verification;
- stale or failed external collection context.

Repeated reconciliation uses a deterministic SHA-256 dedupe identity and cannot create another row for the same source cycle and recipient. When the authoritative source stops being actionable, active notifications are resolved with an append-only system event.

Windows Task Scheduler support is under `ops/windows-task`. The exact task name is `\AgroSat_PROGRAM_R3_OperationalNotifications`. Installation is release-bound and initially disabled. The accepted configuration uses `IgnoreNew`, a bounded execution time, bounded retry, external sanitized status storage, and no secret in the action command line or task XML. Enablement is a production release action only after the TASK 221 candidate and task binding pass qualification.

## Wialon and equipment boundary

G0 established `UNSUPPORTED_NO_CREDENTIALS`: required Wialon credential names and a tenant mapping source were not available, and production supervision keeps Wialon disabled. TASK 221 therefore does not add a provider adapter, mapping table, scheduled Wialon worker, invented quota, or fabricated telemetry.

Case detail uses the accepted telematics boundary and returns an honest `unsupported` state with reason `mapping_unavailable`. Absence of telemetry is explicitly not proof that field work did not happen. No remote command, unit configuration, geofence mutation, or provider-side mutation exists.

## Weather boundary

Case detail reuses the existing Open-Meteo service. Weather is requested only for one selected case, never once per queue row. Provider, fetch time, timezone, and available/unavailable state are retained when present. The UI and API repeat the causal limitation: weather, irrigation, satellite, and telematics context do not confirm an agronomic cause without human inspection evidence. Weather failure does not affect API readiness.

## Frontend

The Russian UI provides:

- a compact operational summary;
- all required tenant-safe filters;
- a deterministic queue with visible priority reasons;
- one selected case with source facts, accountability, deadline, evidence, weather, telematics, verification, notifications, and timeline;
- links to the existing inspection, “Меры и контроль”, field, and monitoring pages;
- explicit offline, unavailable, stale, and unsupported states.

The page uses native interactive elements, visible focus treatment, at least 44 CSS pixel interactive targets, semantic headings/lists/definitions, and text in addition to color for state. Desktop uses a queue/detail split; tablet stacks the same information; mobile shows one pane at a time with an explicit return control. Async requests have task-owned `AbortController` cleanup. TASK 221 introduces no map, layer, source, control, timer, or MapLibre listener, so no new MapLibre resource ownership exists.

The page is read-only while offline. It does not queue notification mutations and links users to the existing supported offline scouting workflow instead of adding a second offline mutation system.

The frontend runtime contract is Node.js 22 or newer. Qualification pins MapLibre GL JS `6.10.0`, uses its ESM namespace export, and emits the matching module worker through Vite's same-origin worker pipeline. Existing map owners continue to import the centralized `maplibreRuntime`; browser qualification exercises a field map after repeated mount/unmount cycles so this dependency security upgrade is covered even though TASK 221 adds no map of its own.

## Qualification

TASK 221 qualification tools are under `ops/qualification` and reject production database identity. They accept only `agrosat_r3_task221_*` targets, validate that the protected source configuration resolves to database `agrosat`, strip inherited sensitive variables, and force Wialon and Telegram off.

Qualification covers:

- a fresh PostGIS database upgraded to `0016_operational_command_center`;
- `alembic heads`, `current`, `check`, empty downgrade/re-upgrade, metadata comparison, constraint/index validation, dump listing, and restore;
- an accepted production backup restored into another task-owned database and upgraded to `0016`;
- a real D/E/F-linked inspection → plan → work → evidence → awaiting-verification case plus a deterministic overdue case;
- notification reconciliation replay, transition idempotency, RBAC, non-enumeration, query budgets, degraded external context, readiness independence, and populated downgrade refusal;
- affected and full backend tests, frontend contract suites, production build, and non-forced npm audit;
- authenticated browser verification at 1440×900, 1024×768, and 390×844 with filters, detail, navigation, offline behavior, keyboard focus, accessibility, overflow, console, page, request, and 5xx checks, plus a desktop MapLibre runtime and remount check after the dependency upgrade.

Evidence and screenshots are written only to the active TASK 221 run under `C:\AgroSat_backups`; runtime media/cache are also outside the source tree. No raw evidence photo, credential, database URL, token, cookie, authorization header, or provider payload is copied into the source archive or sanitized report.

## Release and rollback

The release tooling recognizes the TASK 221 worktree, evidence root, and isolated database prefix while preserving the earlier guards. Candidate construction requires a clean worktree, an aligned pushed task branch, the exact full candidate SHA, a Git-native archive, a manifest, and SHA-256 verification.

Before production mutation, the accepted production database is backed up, the archive is listed and restored into a protected TASK 221 database, the restored copy is migrated to `0016`, and the release pointer/application/task rollback is rehearsed. Populated `0016` data is not destructively downgraded; database rollback is the validated backup restore path.

The canonical Sentinel collector remains a separate standalone process owned by `\AgroSat_PROGRAM_R3_SentinelCycle`. TASK 221 does not start Sentinel, reconciliation, Wialon, or any recurring scheduler from FastAPI.
