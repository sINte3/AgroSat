# TASK_220 Closed-loop agronomy

## Product and coverage contract

The canonical case remains the submitted TASK_217 inspection. A plan references that
inspection and derives its field, enterprise, alert and autonomous candidate from
the stored inspection, never from client-supplied tenant identifiers.

| Requirement | Accepted reuse | Missing implementation and proof |
| --- | --- | --- |
| Case and evidence | TASK_217 inspection, findings, source geometry, private photo storage | Plan, work orders, linked execution photos and one combined timeline; real HTTP lifecycle proof. |
| Decision support | Existing crop seasons, observations, findings, TASK_219 freshness | Immutable deterministic policy and evidence snapshot, explicit uncertainty, human approval. |
| Work lifecycle | Existing users, server tenant scope, optimistic conflict semantics | Plan prerequisites, assigned execution, deadlines, versioned idempotent commands and append-only events. |
| Verification | Accepted NDVI/multi-index observations and quality rules | Frozen baseline, later-scene minimum wait, material-change threshold, measurements and human resolution. |
| Monitoring | TASK_219 candidate identity, collector ownership and separate CLI | Bounded isolated reconciliation, transactional candidate closure and reopen. |
| Workspace | Existing Russian shell, visual tokens, source map, IndexedDB drafts | Measures and control queue, accountable work and explainable verification at all three viewports. |
| Qualification | Existing protected PostgreSQL, browser and release tools | TASK_220 target guards, migration/restore, real workflow, one final regression, frozen archive qualification. |
| Deployment | Immutable release mechanisms and restored TASK_219 application supervision | Exact candidate/backup binding, scheduler rebinding, guarded migration, production smoke and rollback. |

New work orders belong to approved plans. The older `corrective_actions` lifecycle
has independent confirmation and manual verification semantics; changing those rows
would change accepted TASK_217 behavior. Dedicated plan work orders preserve that
history without introducing another inspection or case root. Execution attachments
extend the existing private inspection evidence table and share its storage validation;
legacy photo endpoints must exclude plan-owned attachments.

Decision roles are the existing admin, manager and agronomist roles. Only the global
admin sees multiple enterprises. Assigned agronomists execute their work; viewers
never mutate. Human decisions and overrides require an explicit reason. Satellite
change is observational evidence and must never be presented as proof of causation.

Policy `r3-f-v1` requires accepted quality, a seven-day minimum wait after completion
and an absolute NDVI material-change threshold of 0.05. Missing, stale, cloudy or
degraded evidence remains explicit. Field statistics are labeled as field statistics;
they cannot establish an affected zone's recovery without comparable zone evidence.

## Continuity

The run is `20260905_075248`; its initial safety-stop package is immutable historical
evidence. Production application identity was reversibly recovered to accepted
`f3a95f4e4d97b025a967ae3812603e5aae0d969d`, with database revision `0014` unchanged.
The recovery checkpoint and current gate ledger are stored in that same run.
Passed gates are reused until a relevant source or runtime change invalidates them.
Macrostage G is outside this task.
