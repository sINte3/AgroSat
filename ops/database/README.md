# TASK_209 database backup and isolated restore

These scripts provide a reviewable PostgreSQL backup/restore contract. They do
not read environment files, accept connection URLs, accept credentials on the
command line, print server names, or automatically remove a failed restore.
Authentication is delegated to the execution identity and a separately managed
libpq credential mechanism.

All commands are preview-only unless `-Apply` is supplied. Backup apply also
requires `-ConfirmReadOnlySource`. Restore and validation reject every mutable
target that does not match `agrosat_task209_*`. Artifact paths must remain below
`C:\AgroSat_backups\task209_global_program`.

A data-bearing archive is rejected when its apply path is below the sanitized
`evidence` directory. Use a separate `database_archives` child and exclude it
from evidence packaging. Password hashes and business rows make a real archive
non-sanitized even when the archive has no connection credentials.

The backup is a custom-format schema-and-data archive without owners or ACLs.
The script creates a restore list, rejects an empty list, calculates SHA-256,
and writes sanitized metadata. Restore verifies SHA-256 and the restore list
before creating a new isolated database. It refuses to overwrite an existing
database and uses a single restore transaction.

Post-restore validation records the Alembic revision, exact counts for critical
tables, invalid constraint/index counts, and a schema-only SHA-256. A critical
API smoke must then run with an isolated synthetic user; this is intentionally
not embedded in the database script.

Current TASK_209 execution status: contract and preview validation only.
PostgreSQL client tools and an isolated PostGIS server are unavailable (B-001),
so no backup, restore, database creation, migration, or smoke was executed.
