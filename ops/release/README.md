# TASK_209 release and rollback contract

`New-ReleaseManifest.ps1` performs read-only Git inspection and creates a
sanitized manifest. It verifies the exact source baseline, source `main`
cleanliness, program branch, origin alignment, changed files, changed migration
files, and explicit apply order. It never deploys, migrates, switches assets, or
changes a Scheduled Task.

`Test-RollbackReadiness.ps1` is a read-only drill. It verifies that rollback
coverage exists for the application, migrations, collector, frontend assets,
and Scheduled Task. Every migration added by the program must have an explicit
entry in `rollback-contract.json` before the drill can pass.

Release order is:

1. Human review and approval.
2. Sanitized backup plus isolated restore and API smoke.
3. Per-migration safety review.
4. Immutable application artifact staging.
5. Database migration apply.
6. Backend health/readiness and critical smoke.
7. Immutable frontend asset switch.
8. Exact collector Scheduled Task switch.
9. Post-release role, tenant, and operational acceptance.

Rollback never rewrites Git history or mutates the program worktree. Redeploy a
previously approved immutable artifact. Disable only the exact collector task.
Migration downgrade is a separate human decision: destructive or data-losing
revisions must be roll-forward-only and must not be downgraded.

No production release or rollback is authorized by TASK_209. The scripts create
review evidence only.
