# AgroSat database backup contract (TASK_230)

One contract, implemented in `ops/release/controlplane/backup.py` and driven by
`ops/release/Invoke-AgroSatControlPlane.py backup <action> --policy <json>`.
It replaces the TASK_209 review scripts.

## Policy

Everything comes from an explicit policy file; nothing has a default.
`database-backup-policy.example.json` is an example only (`example_only: true`
is refused, and its placeholders are refused). Its retention numbers are
illustrations, not the company's retention policy. No production backup time,
backup root or secondary destination exists in this repository: they are
deployment prerequisites (TASK_231).

## Backup

`pg_dump --format=custom --no-owner --no-acl` of one database, read-only.
Credentials are read from the runtime environment file's `DATABASE_URL` and
reach `pg_dump`/`pg_restore` only through PG* variables of the child process,
never argv, output or evidence. A backup is valid only when the dump proves
itself:

* the restore list parses and every public table carries table data;
* the Alembic revision (exactly one) and per-table row counts are read back
  from the dump's own COPY data;
* a normalized schema hash is computed from the dump;
* the file's SHA-256 is recorded.

Each backup is sealed in `<backup_root>/<backup_id>/` with an immutable
`metadata.json`; an invalid dump is moved to `.quarantine/` for inspection.
`pg_dump` exiting 0 is never enough.

## Retention

`backup retention-plan` previews; `backup retention-apply --plan-sha256`
executes exactly that plan. The newest validated backup, the minimum count,
the daily and weekly keepers, backups younger than the minimum age and every
backup a live or current/previous release or rollback record references are
kept. Unvalidated or foreign entries are never deleted. Deletion happens only
for direct children of the backup root without reparse points, re-verified by
SHA-256, and every deletion is appended to `retention-log.jsonl`.

## Secondary copy

With `secondary.required: true` a backup is fully protected only after the
dump and metadata are copied to the configured absolute or UNC destination and
the copy's SHA-256 equals the primary's (`secondary.json` in both places). No
destination is assumed; TASK_230 qualified the mechanism with two local roots
only, which is not off-host protection.

## Restore rehearsal

`backup restore-rehearsal --backup-id <id> --target agrosat_taskNNN_*` restores
into a new database (never an existing one), then validates the Alembic
revision, constraints, indexes, per-table row counts and schema hash against
the dump. A failed target is kept for inspection. `backup
drop-rehearsal-target` removes a target only with its recorded evidence.

## Scheduled backups

`Install-DatabaseBackupTask.ps1 -PolicyPath -ReleaseDirectory -ControlRoot`
previews, and with `-Apply` registers the policy's task (SYSTEM or the
policy's explicit SID, one daily trigger at the policy's explicit time,
IgnoreNew, bounded execution limit and retries), installed disabled. It runs
`backup scheduled`: backup, secondary copy, retention, one immutable
execution record in `<backup_root>/executions/`. The release controller rebinds
an installed backup task to each new release. `Inspect-DatabaseBackupTask.ps1`
compares the registered task with its policy (read-only).
