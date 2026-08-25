# TASK_219 Autonomous Monitoring Contract

## Coverage map

| Requirement | Reused component | TASK_219 closure |
| --- | --- | --- |
| Canonical collection | `backend/scripts/collect_satellite.py`, existing NDVI and multi-index child collectors | Persist cycle identity, add PostgreSQL advisory ownership, heartbeat, production defaults, deterministic outcomes, and freshness/anomaly reconciliation. |
| Provider and quality | Sentinel provider, satellite safety, data-quality services | Keep metadata-first provider behavior and explicit rejection categories; expose sanitized counters without coupling web readiness to provider health. |
| Pixel anomalies | Pixel scene provider, deterministic pixel algorithm, pixel anomaly persistence | Add a versioned robust-baseline policy, stable autonomous candidate identity, persistence/cooldown, spike guard, automatic-inspection caps, and audited review states. |
| Inspections | Accepted anomaly-inspection and field-inspection workflows | Link only high-confidence persistent candidates; preserve optimistic versions, source snapshots, tenant boundaries, and one-open-inspection semantics. |
| Operational API | Auth/RBAC dependencies and explicit SQL service pattern | Add focused status, freshness, candidate, and transition endpoints. Global scheduler status remains admin-only; all field data is tenant-scoped. |
| Operator interface | Existing application shell, tokens, MapLibre runtime, inspection navigation | Add one responsive Monitoring workspace with status/freshness summaries, filters, anomaly list/map, explanations, review actions, and offline/degraded states. |
| Scheduling | Existing repository-owned Windows task scripts | Bind one exact task to an immutable-release dispatcher, two local triggers, protected runtime environment, external state/status/lock directories, IgnoreNew, retry, and six-hour limit. |
| Release/rollback | Existing immutable archive, manifest, release health, and rollback tooling | Extend qualification and operator documentation; freeze only after source closure and keep production mutation fail-closed. |

## Domain contract

The single Alembic revision `0014_autonomous_satellite_monitoring` adds five normalized structures: immutable anomaly rule versions, collection runs, per-field/per-index freshness, autonomous anomaly candidates, and candidate transition audit events. All business rows carry explicit enterprise and field foreign keys where applicable. Candidate geometry is SRID 4326 `MULTIPOLYGON`, and stable source/zone keys plus partial unique indexes enforce replay safety.

Freshness is computed only from the last accepted valid observation: `FRESH` through 10 days, `AGING` through 20 days, and `STALE` after 20 days. A missing accepted observation is classified separately as `NEVER_COLLECTED`, `CLOUD_BLOCKED`, `PROVIDER_DEGRADED`, or `QUALITY_BLOCKED` from the most recent explicit outcome.

The default rule `r3-e-v1` is deterministic statistical detection, not machine learning. It uses median/MAD NDVI deviation, supporting-index agreement, quality, affected area, persistence, and stable geometry identity. Normal automatic action requires two valid scenes; an extreme one-scene path is allowed only by the explicit high-confidence threshold. Automatic inspection creation is capped at 20 per cycle, one open inspection per stable source/zone, and is disabled for a cycle when more than 10 percent of active fields would trigger.

## Operating defaults

- indices: NDVI, SAVI, EVI, NDMI, NDRE;
- active-field batch size: 25;
- lookback: 14 days;
- provider attempts: 2 with bounded exponential backoff and jitter;
- field timeout: 180 seconds;
- cycle timeout: 21,600 seconds;
- cooldown: 14 days;
- minimum anomaly area: the safer maximum of 0.25 hectares or 1 percent of field area;
- automatic inspections: at most 20 per cycle and never during a greater-than-10-percent spike;
- schedule: 06:00 and 18:00 Asia/Tashkent unless the recorded provider-capacity calculation requires a safer sustainable cadence.

## Safety boundaries

The web application never starts collection. The collector remains a separate CLI and Windows task. Provider degradation is reported independently and never makes `/api/health/ready` fail. No fixture or mock provider may reach an apply path. The scheduler dispatcher verifies immutable release and manifest identity before invoking the canonical collector. Every database qualification helper rejects names outside `agrosat_r3_task219_*`, and production mutation requires the captured revision, verified backup/restore rehearsal, rollback manifest, and a disabled or absent TASK_219 scheduler.

## Operator runbook

The Monitoring workspace is the operator source of truth for the last collection run,
heartbeat, provider class, counters, freshness distribution, review candidates, and
inspection linkage. `succeeded` includes a valid no-new-scene or zero-anomaly outcome.
`degraded` means the remote provider, authentication, quota, network, quality, or a
bounded timeout prevented a complete cycle; it does not make the web application
unready. `failed` is a local contract or operational error. `running` is healthy only
while its heartbeat advances and both ownership locks remain bound to the recorded
release.

For a bounded manual diagnostic, load the protected runtime environment through the
repository-owned dispatcher and invoke `backend/scripts/collect_satellite.py
--diagnostic` with explicit discovered field IDs, all five indices, a 14-day lookback,
two attempts, and external output/state/lock directories. Never place credentials on
the command line and never use the web UI to start a cycle. A provider authentication,
quota, or endpoint failure is recorded as a sanitized category and stops the scope
without fan-out. Retry only after the external prerequisite is corrected; metadata-first
no-scene results need no corrective retry.

The only scheduler identity is `AgroSat_PROGRAM_R3_SentinelCycle`. Disable it before
manual recovery or rollback and confirm both legacy tasks remain Disabled. The default
triggers are 06:00 and 18:00 Asia/Tashkent, `MultipleInstances` is `IgnoreNew`, missed
runs start when the host is available, failed starts retry after 15 minutes up to three
times, and one invocation stops after six hours. Runtime status, state, lock, and logs
are candidate-specific directories outside the immutable release. Inspect the atomic
latest-status document and per-run summary; never treat Redis as scheduler or readiness
truth.

Moderate candidates stay in the review queue. Confirm or dismiss them with an explicit
reason, or create an inspection when field action is warranted. A high-confidence
persistent candidate may create one inspection automatically, subject to cooldown,
one-open-source identity, the cap of 20 per cycle, and the regional spike guard. When
more than 10 percent of active fields would trigger, automatic creation is suppressed;
review the provider/quality distribution and rule evidence before any later retry.

Rollback starts by disabling the TASK_219 task and stopping only TASK_219-owned
collector/application processes. Verify the rollback manifest and exact previous
archive hash, restore the previous application pointer, restore the bound custom-format
database backup only through the guarded production recovery script, restore prior task
states, and then prove health, revision, tenant integrity, and listener identity. A hash,
database identity, or revision mismatch is fail-closed and must not be bypassed.
