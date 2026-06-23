# TASK_034_FIX_BACKFILL_CLI_ORM_IMPORTS.md

## Context

AgroSat has a standalone backend CLI script:

- `backend/scripts/run_remaining_backfill.py`

Dry run works and finds 17 candidate fields. Real run fails before inserting data with SQLAlchemy ORM registry error:

`expression 'Enterprise' failed to locate a name ('Enterprise')`

The failure happens when the script reaches:

`field = db.query(Field).get(cf["id"])`

Root cause: the CLI imports `Field` and `NDVIRecord`, but does not import all ORM model classes needed to register SQLAlchemy string relationships before querying ORM entities.

## Objective

Fix the standalone backfill CLI so it can run without ORM registry errors.

## Scope

Backend only.

Allowed file:

- `backend/scripts/run_remaining_backfill.py`

Optional allowed file only if strictly needed:

- `backend/models/__init__.py`

Do not modify:

- frontend files
- database schema
- Alembic migrations
- scheduler startup behavior
- API routers
- `.env`
- `db_backups/`
- `logs/`

## Requirements

1. Read these files first:

- `backend/scripts/run_remaining_backfill.py`
- `backend/models/field.py`
- `backend/models/enterprise.py`
- `backend/models/crop.py`
- `backend/models/monitoring.py`
- `backend/database.py`

2. Ensure all SQLAlchemy ORM classes referenced by string relationships are imported before any ORM query is executed.

At minimum, `Enterprise` must be registered before querying `Field`.

Prefer a narrow explicit import block in `run_remaining_backfill.py`. Adjust names only according to actual model definitions.

3. Replace deprecated SQLAlchemy call:

`db.query(Field).get(cf["id"])`

with:

`db.get(Field, cf["id"])`

4. Do not change candidate selection logic.

5. Do not change Sentinel Hub fetch logic.

6. Do not create or modify migrations.

7. Do not run the real backfill in this task. Only compile and dry-run validation are allowed.

## Validation

Run from PowerShell:

Set-Location "C:\AgroSat"

chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

Write-Host "===== GIT STATUS BEFORE VALIDATION ====="
git status --short

Write-Host "`n===== PY COMPILE ====="
Set-Location "C:\AgroSat\backend"
python -m py_compile .\scripts\run_remaining_backfill.py

Write-Host "`n===== DRY RUN ====="
python .\scripts\run_remaining_backfill.py --dry-run

Set-Location "C:\AgroSat"

Write-Host "`n===== DIFF ====="
git diff -- backend/scripts/run_remaining_backfill.py backend/models/__init__.py

Write-Host "`n===== STATUS ====="
git status --short

## Acceptance criteria

- `python -m py_compile .\scripts\run_remaining_backfill.py` passes.
- `python .\scripts\run_remaining_backfill.py --dry-run` runs successfully.
- Dry run still reports 17 candidate fields before the real retry.
- No SQLAlchemy `Enterprise failed to locate a name` error.
- Only allowed source files are modified.
- `db_backups/` and `logs/` are not staged and do not appear in `git status --short`.
- No database writes are performed during validation.
