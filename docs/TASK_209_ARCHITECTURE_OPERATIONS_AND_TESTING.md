# TASK_209 Architecture, Operations, and Test Guide

## System purpose

AgroSat closes an operational agronomy loop:

```text
observation -> attention -> inspection -> evidence -> corrective action
-> accountable closure -> later-observation verification -> management control
```

Satellite and analytical values are evidence. They are not an autonomous
diagnosis or prescription.

## Runtime architecture

- FastAPI serves authenticated, role-aware, tenant-scoped APIs.
- PostgreSQL/PostGIS is the system of record for business and spatial data.
- Redis is an optional bounded cache. Cache failure must not change business
  truth or make a failed database write appear successful.
- Scheduled satellite collection, anomaly processing, and productivity-zone
  processing use standalone bounded CLIs. Nothing schedules work from FastAPI
  lifespan, application imports, request handlers, or web workers.
- React owns product workflow state. MapLibre owners remove their sources,
  layers, listeners, controls, object URLs, and pending requests when replaced
  or unmounted.
- Field base geometry uses tenant-scoped vector tiles. Satellite rasters use a
  provider abstraction with cancellation, timeout, quality, provenance, and
  explicit unavailable states.

## Authorization and tenant model

Server authorization is authoritative. Frontend navigation is only a usability
control.

```text
admin       global operational scope
manager     assigned enterprise; management transitions
agronomist  assigned enterprise; operational work
viewer      assigned enterprise; read-only
```

Unknown and disabled roles fail closed. Tenant object queries place enterprise
scope inside SQL; cross-tenant object identifiers normally return 404. Lists,
details, aggregates, tiles, exports, and write targets use the same scope.
SQLAlchemy relationships use `lazy="raise_on_sql"` and runtime services use
explicit SQL, joins, or bounded eager loading.

Phase 9G retains `enterprises` as the rollout tenant and
`users.enterprise_id` as the compatibility scope. Commercial memberships,
features, quotas, retention, branding, provider references, namespaces,
reviewed lifecycle requests, and audit events are defined by migration 0012.
No payment processing is implemented.

## Current domain contracts

- [Operational closure](TASK_209_OPERATIONAL_CLOSURE_SPEC.md)
- [Executive metrics](TASK_209_EXECUTIVE_ACCOUNTABILITY_METRICS.md)
- [Vector/raster architecture](TASK_209_VECTOR_RASTER_ARCHITECTURE.md)
- [Pixel anomaly](TASK_209_PIXEL_ANOMALY_CONTRACT.md)
- [Offline scouting](TASK_209_OFFLINE_SCOUTING_CONTRACT.md)
- [Wialon read-only boundary](TASK_209_WIALON_READ_ONLY_CONTRACT.md)
- [Weather and irrigation](TASK_209_WEATHER_IRRIGATION_WORKFLOW.md)
- [Yield map import](TASK_209_YIELD_MAP_IMPORT_CONTRACT.md)
- [Productivity zones](TASK_209_PRODUCTIVITY_ZONE_CONTRACT.md)
- [Variable-rate safety](TASK_209_VARIABLE_RATE_SAFETY_CONTRACT.md)
- [Commercial tenant boundary](TASK_209_COMMERCIAL_TENANT_BOUNDARY.md)

## Collector and Windows Scheduled Task

The canonical satellite entrypoint is:

```powershell
python backend\scripts\collect_satellite.py --help
```

Start with explicit fields, indices, a bounded date window, and dry-run/no-write
diagnostics. Apply mode requires approved runtime credentials and an isolated or
human-approved target.

Reviewable Windows Scheduled Task scripts are documented in
`ops/windows-task/README.md`. They provide install, inspect, smoke-test,
disable, and uninstall operations. TASK_209 did not create or modify a real
Windows Scheduled Task.

## Observability

Liveness, readiness, database readiness, cache readiness, release revision,
migration visibility, and sanitized collector operational state are exposed by
the health/operations APIs. Metrics cover bounded API, database, cache,
collector, freshness, backlog, map, and raster behavior without field names,
emails, geometry, or other high-cardinality business labels.

Structured logs must not contain credentials, tokens, connection URLs,
Authorization headers, cookies, user emails, or business response bodies.

## Backup, restore, release, and rollback

- Database backup and isolated restore: `ops/database/README.md`
- Deterministic load harness: `ops/load/README.md`
- Release manifest and rollback readiness: `ops/release/README.md`
- Scheduled-task rollback: `ops/windows-task/README.md`

Never restore into production as a test. Run migration and business-write tests
only against an isolated database. Downgrade guards intentionally stop removal
of tables that contain domain data.

## Isolated local validation

Required mutable test services:

```text
PostgreSQL with PostGIS: dedicated agrosat_task209_* database
Redis: dedicated instance, port, namespace, or DB index
Synthetic users: admin, manager, agronomist, viewer across two enterprises
Frontend/backend: unique strict loopback ports and exact process ownership
```

Do not copy production credentials or data. If an isolated service cannot be
created, run offline migration compilation and deterministic contract fixtures,
record the blocker, and do not fall back to production.

## Full test execution

Backend:

```powershell
Set-Location backend
python -m pytest tests -q
python -m alembic heads
```

Frontend:

```powershell
Set-Location frontend
npm run test:closure
npm run test:executive
npm run test:vector-raster
npm run test:pixel-anomaly
npm run test:offline-scouting
npm run test:wialon-read-only
npm run test:weather-irrigation
npm run test:yield-map-import
npm run test:productivity-zones
npm run test:variable-rate
npm run test:commercial-tenant
npm run build
```

Browser acceptance must use a fresh production build, unique strict ports,
synthetic fixture identities, cache-disabled navigation, console and failed
network inspection, and viewport coverage appropriate to the changed workflow.
Fixture runtime proves UI behavior only; it does not prove PostgreSQL/PostGIS,
Redis, Sentinel, Wialon, service-worker, or production behavior.

## Deployment boundary

TASK_209 produces a review branch, migrations, tests, scripts, documentation,
and sanitized evidence. It does not deploy, merge to `main`, mutate production
data, create a Scheduled Task, write Wialon data, send Telegram messages, or
execute tenant deletion/export.
