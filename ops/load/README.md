# TASK_209 isolated load harness

The harness accepts only loopback targets, bounded concurrency/request counts,
credential-free URLs, non-destructive HTTP methods, bounded scenario bodies,
and absolute scenario/output paths. Response bodies are never written to the
report. Authorization can only be supplied through
`TASK209_LOAD_AUTH_TOKEN`; its value is never emitted.

`readiness_scenarios.json` is safe against an isolated runtime with an
intentionally unavailable database. `full_scenarios.example.json` records the
required business scenarios and their current prerequisites. Do not enable
write scenarios unless the target is the dedicated TASK_209 database and
`--allow-writes` has been explicitly approved.

Performance budgets must be set from measured business-scenario baselines.
The readiness-only run is not a substitute for those baselines.
