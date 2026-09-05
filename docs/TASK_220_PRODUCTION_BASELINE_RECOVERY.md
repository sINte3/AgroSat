# TASK_220 production application supervision recovery

The accepted TASK_219 deployment updated the serving processes but left the earlier
stabilization BootTrigger tasks bound to release `96f7e4e272596773b0e70b4bcffc40a8219fc49b`.
Windows task events 100/200 and process ancestry prove that those tasks started the old
backend, frontend and watchdog after the 2026-09-02 host boot. The old watchdog also
restarts its fixed application tasks after three unhealthy samples.

This recovery adds a repository-owned application wrapper to the existing release
tooling. It retains the existing backend/frontend task identities and BootTriggers,
uses the accepted Uvicorn application entry point and archive-owned static frontend
server, and binds both to a validated immutable manifest. Windows task restart settings
provide bounded process recovery. The old application watchdog is disabled reversibly;
no second application watchdog or collector is introduced.

The wrapper accepts only non-secret path and identity configuration. Application
credentials remain loaded by the existing explicit runtime environment mechanism.
It never migrates, collects, sends notifications, or writes business data. A file lock
and a refused occupied listener prevent duplicate application ownership. Child output
is discarded; sanitized lifecycle records and public health endpoints provide evidence.

Before activation, the continuation exports exact task XML and state, verifies both
old and accepted release assets, records read-only database counts and integrity, and
prepares restoration of the original task definitions and old process binding. The
canonical Sentinel task and both disabled legacy collectors remain unchanged.

Recovery evidence belongs to the existing TASK_220 run `20260905_075248` under
`00_IDENTITY_AND_RECOVERY/RECOVERY_CONTINUATION`. The original safety-stop ZIP remains
immutable. Full agronomy implementation can start only after recovery acceptance.
