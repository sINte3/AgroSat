# TASK_209 Current Master Status

## Decision at review handoff

`PARTIAL`

Independent implementation is complete through Phase 9G and is committed on
`task/task209-agrosat-global-program`. External runtime prerequisites prevent a
full program PASS.

## Implemented

- UX/accessibility repairs found by acceptance review
- server-side role and enterprise authorization matrix
- bounded collection APIs and explicit relationship loading policy
- standalone satellite collector and Windows task artifacts
- health, metrics, load, backup/restore, release, and rollback artifacts
- inspection result, corrective action, closure, reopen, audit timeline, and
  next-observation verification
- executive accountability aggregates, drill-down, and export
- tenant-scoped field vector tiles and raster provider lifecycle
- deterministic pixel anomaly workflow and inspection linkage
- offline scouting queue and conflict UX
- Wialon read-only provider boundary
- weather/irrigation evidence workflow
- yield map preview/import contract
- deterministic productivity zones
- human-entered variable-rate recommendation drafts
- commercial tenant membership/configuration/lifecycle boundary

## Tested

- complete backend suite and exact OpenAPI authorization coverage
- offline PostgreSQL migration upgrade/downgrade compilation
- single Alembic head
- frontend workflow contracts and production build
- deterministic browser fixtures at desktop, tablet, and mobile viewports
- MapLibre ownership, stale-response, cancellation, and cleanup contracts
- secret-redaction and no-production-write evidence

Exact final counts and hashes are recorded in the external TASK_209 evidence
ledgers generated from the final branch.

## Blocked

- isolated PostgreSQL/PostGIS migration, write, restore, load, and concurrency
  execution
- isolated Redis integration
- broad Playwright MCP acceptance where the connector blocks loopback or its
  transport closes
- service-worker installation/activation in the available Playwright policy
- live Sentinel read-only validation
- live Wialon read-only validation and authoritative tenant mappings

No fixture result is represented as live external validation.

## Not production-applied

- no production deployment
- no production database change
- no production Redis mutation or flush
- no real Windows Scheduled Task change
- no Wialon write
- no Telegram message
- no tenant export/deletion execution
- no payment processing
- no merge into `main`

## Migration order

TASK_209 migrations extend the repository head in this order:

```text
0006
0007_pixel_anomalies
0008_irrigation_context
0009_yield_map_imports
0010_productivity_zones
0011_variable_rate_recommendations
0012_commercial_tenant_boundary
```

Apply only after backup verification, isolated restore rehearsal, human review,
and explicit production authorization.

## Required human decisions

- provide or approve an isolated PostgreSQL/PostGIS target
- provide or approve isolated Redis
- enable an approved Playwright browser bridge for the full acceptance matrix
- decide whether and how to provide read-only Sentinel credentials
- approve Wialon API version, read-only credential, and tenant/unit mappings
- review migrations and downgrade guards
- review measured performance evidence and production budgets
- approve the staged release and rollback order

## Known limitations

- production bundle retains the recorded large-chunk warning
- database-backed and live-provider claims remain blocked
- service-worker full offline reload remains blocked by browser policy
- variable-rate output is decision support and requires agronomist validation
- satellite movement cannot establish agronomic causality by itself

## Review and deployment order

1. Verify branch/evidence hashes and secret scan.
2. Review authorization, tenant, migration, and rollback contracts.
3. Provision isolated PostgreSQL/PostGIS and Redis.
4. Execute all migrations and database-backed tests in isolation.
5. Complete full Playwright, service-worker, Sentinel, and Wialon gates where
   approved prerequisites exist.
6. Reconcile load budgets and restored-database smoke evidence.
7. Obtain human release approval.
8. Apply the documented migration and application order.

Rollback follows `ops/release/README.md`, migration downgrade classifications,
frontend asset rollback, collector rollback, and Scheduled Task rollback. Never
run a destructive downgrade when a migration guard reports domain data.
