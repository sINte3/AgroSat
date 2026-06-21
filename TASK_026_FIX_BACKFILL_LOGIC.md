# TASK_026_FIX_BACKFILL_LOGIC

## Status
Critical backend hotfix. One isolated result only: fix historical NDVI backfill field selection and idempotent insertion in `backend/scripts/run_remaining_backfill.py`.

Do not touch frontend, deployment scripts, database schema, migrations, MapLibre code, AI, PDF, Telegram, or scheduler architecture in this task.

## Context
AgroSat currently has 275 active fields. Historical NDVI backfill is incomplete: 47 fields do not have January 2026 history. Some of these fields already have fresh June NDVI rows created by the automatic scheduler. The current remaining-backfill script incorrectly treats those fields as already historically backfilled because it checks for the existence of any record before a June cutoff instead of checking the earliest NDVI date per field.

The correct logic is: a field still needs historical backfill when its earliest existing NDVI record is missing or later than approximately February 1, 2026. For such fields, the script must backfill from January 1, 2026 up to, but not including, the first existing NDVI record date.

## Files to read first
Read these files before editing anything:

1. `CLAUDE_CODE_PLAN.md`
2. `AgroSat_status_report.md` if present in the repository
3. `backend/scripts/run_remaining_backfill.py`
4. `backend/scheduler.py`
5. `backend/services/satellite.py`
6. `backend/models/monitoring.py`

## Hard constraints
1. Do not replace the whole file. Patch only the relevant blocks in `backend/scripts/run_remaining_backfill.py`.
2. Keep the task atomic. No frontend changes, no deployment changes, no scheduler refactor, no DB schema change.
3. Do not add APScheduler or any new background scheduler.
4. Do not use SQLAlchemy lazy loading for bulk field selection. Use explicit SQL or explicit joins/aggregation.
5. Every NDVI row inserted by the script must pass `validate_ndvi_quality()` either directly or through `fetch_ndvi_for_field_date()`.
6. The script must be safe to rerun. A second run must not create duplicate `(field_id, captured_date)` rows.
7. Do not create historical alerts during this backfill unless the existing script already explicitly does that and there is a documented product requirement. This task is about NDVI history repair, not alert generation.
8. Preserve existing Sentinel Hub/mock behavior. Do not change API credentials, token logic, evalscript, or satellite service selection.

## Current bug to fix
Find the current logic in `backend/scripts/run_remaining_backfill.py` that selects fields based on whether they have records before a June cutoff such as `2026-06-16`.

This logic is wrong because a field with only one scheduler-created NDVI row on or around June 15, 2026 satisfies that condition and is skipped, even though it has no January-May history.

Remove the old idea of "remaining fields = fields with no records before June cutoff".

## Required selection logic
Replace the field selection with earliest-record based coverage detection.

Use this semantic query:

```sql
SELECT
    f.id,
    f.name,
    MIN(n.captured_date) AS first_ndvi_date,
    MAX(n.captured_date) AS last_ndvi_date,
    COUNT(n.id) AS ndvi_count
FROM fields f
LEFT JOIN ndvi_records n ON n.field_id = f.id
WHERE f.is_active = true
GROUP BY f.id, f.name
HAVING MIN(n.captured_date) IS NULL
   OR MIN(n.captured_date) > DATE '2026-02-01'
ORDER BY f.id;
```

Implementation may use SQLAlchemy `text()` or explicit SQLAlchemy aggregation, but the semantics must remain the same.

Define constants in the script:

```python
BACKFILL_START_DATE = date(2026, 1, 1)
EARLY_HISTORY_THRESHOLD_DATE = date(2026, 2, 1)
```

The selected fields must include:

1. fields with no NDVI rows at all;
2. fields whose earliest NDVI row is later than `2026-02-01`;
3. fields that only have June scheduler rows.

The selected fields must exclude fields whose earliest NDVI row is in January 2026.

## Required per-field backfill window
For each selected field:

1. If `first_ndvi_date` exists, set `backfill_end_exclusive = first_ndvi_date`.
2. If `first_ndvi_date` is missing, set `backfill_end_exclusive = date.today()` unless the existing script already has a safer configured historical end date. If such a configured end date exists, preserve it.
3. Generate target dates from `BACKFILL_START_DATE` up to, but not including, `backfill_end_exclusive`.
4. Preserve the existing date step/cadence already used by the script unless it is directly tied to the broken June cutoff logic.
5. Do not request or insert dates on or after the first existing record date for that field.

Example:

```python
if first_ndvi_date is None:
    backfill_end_exclusive = existing_configured_end_date_or_today
else:
    backfill_end_exclusive = first_ndvi_date

if backfill_end_exclusive <= BACKFILL_START_DATE:
    logger.info("Field %s already has early history; skip", field.id)
    continue
```

## Required idempotency before insert
Before inserting any NDVI row, check the actual captured date returned by Sentinel Hub/mock data, not only the requested target date.

Required logic:

```python
captured_date = date.fromisoformat(ndvi_data["captured_date"])

existing = db.query(NDVIRecord).filter(
    NDVIRecord.field_id == field.id,
    NDVIRecord.captured_date == captured_date,
).first()

if existing:
    logger.info(
        "Field %s: NDVI for %s already exists, skipping",
        field.id,
        captured_date,
    )
    skipped_existing += 1
    continue
```

Then insert only if `existing` is missing.

If the current script already has an insert helper, patch that helper rather than duplicating insertion logic.

## Required quality gate usage
Prefer using the existing helper from `backend/services/satellite.py`:

```python
from services.satellite import fetch_ndvi_for_field_date
```

That helper already obtains field geometry, fetches NDVI for a specific date window, and applies `validate_ndvi_quality()` before returning data.

If the script currently bypasses `fetch_ndvi_for_field_date()`, then add an explicit quality gate immediately before insert:

```python
from services.satellite import validate_ndvi_quality

is_valid, reason = validate_ndvi_quality(
    mean_ndvi=ndvi_data["mean_ndvi"],
    cloud_cover_pct=ndvi_data.get("cloud_cover_pct"),
    min_ndvi=ndvi_data.get("min_ndvi"),
    max_ndvi=ndvi_data.get("max_ndvi"),
    field_name=field.name,
)
if not is_valid:
    logger.info("Field %s: quality gate rejected %s: %s", field.id, target_date, reason)
    skipped_quality += 1
    continue
```

Do not insert NDVI rows that failed the quality gate.

## Required NDVIRecord insert fields
When inserting a new row, preserve all available fields from `ndvi_data`:

```python
record = NDVIRecord(
    field_id=field.id,
    captured_date=captured_date,
    mean_ndvi=ndvi_data.get("mean_ndvi"),
    min_ndvi=ndvi_data.get("min_ndvi"),
    max_ndvi=ndvi_data.get("max_ndvi"),
    std_ndvi=ndvi_data.get("std_ndvi"),
    p10_ndvi=ndvi_data.get("p10_ndvi"),
    p90_ndvi=ndvi_data.get("p90_ndvi"),
    cloud_cover_pct=ndvi_data.get("cloud_cover_pct"),
    valid_pixels_pct=ndvi_data.get("valid_pixels_pct"),
    satellite=ndvi_data.get("satellite", "Sentinel-2"),
)
```

Do not silently drop `cloud_cover_pct` if it is present.

## Required change recalculation
After all attempted inserts for a field are complete, recalculate `ndvi_change` and `ndvi_change_pct` for that field in chronological order.

Reason: the scheduler may already have inserted June rows before historical January-May rows existed. After backfill, the pre-existing June row must compare against the newly inserted previous historical row, not against `None` or a stale previous value.

Required behavior:

1. Load all NDVI records for that field ordered by `captured_date ASC`.
2. First chronological record gets `ndvi_change = None` and `ndvi_change_pct = None`.
3. Each later record gets:

```python
record.ndvi_change = record.mean_ndvi - previous.mean_ndvi
record.ndvi_change_pct = (record.ndvi_change / previous.mean_ndvi) * 100 if previous.mean_ndvi else None
```

4. Commit after recalculation for that field.

Do not create alerts during recalculation.

## Required dry-run mode
Add a safe dry-run mode if the script does not already have one.

Required command behavior:

```powershell
python backend/scripts/run_remaining_backfill.py --dry-run
```

Dry-run must:

1. print selected candidate fields;
2. print each field's `first_ndvi_date`, `last_ndvi_date`, `ndvi_count`;
3. print computed backfill window for each selected field;
4. print planned target date count;
5. perform zero database writes.

## Logging requirements
The script must log a final summary with at least these counters:

```text
candidate_fields=<int>
attempted_dates=<int>
inserted_records=<int>
skipped_existing=<int>
skipped_no_data=<int>
skipped_quality=<int>
errors=<int>
```

For the known broken class of fields, logs must show that fields whose first record is in June are selected and processed, not skipped as already complete.

## Validation commands
Run these commands from the repository root after patching:

```powershell
cd C:\AgroSat
python -m py_compile backend\scripts\run_remaining_backfill.py backend\services\satellite.py backend\scheduler.py
python backend\scripts\run_remaining_backfill.py --dry-run
```

After dry-run looks correct, run the real backfill manually with FastAPI/scheduler stopped to avoid a race with automatic NDVI writes:

```powershell
cd C:\AgroSat
python backend\scripts\run_remaining_backfill.py
```

Then run SQL validation against the configured database.

Candidate fields remaining after real run:

```sql
SELECT
    f.id,
    f.name,
    MIN(n.captured_date) AS first_ndvi_date,
    MAX(n.captured_date) AS last_ndvi_date,
    COUNT(n.id) AS ndvi_count
FROM fields f
LEFT JOIN ndvi_records n ON n.field_id = f.id
WHERE f.is_active = true
GROUP BY f.id, f.name
HAVING MIN(n.captured_date) IS NULL
   OR MIN(n.captured_date) > DATE '2026-02-01'
ORDER BY f.id;
```

Duplicate detection:

```sql
SELECT field_id, captured_date, COUNT(*) AS duplicate_count
FROM ndvi_records
GROUP BY field_id, captured_date
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC, field_id, captured_date;
```

January coverage:

```sql
SELECT COUNT(DISTINCT f.id) AS fields_with_january_ndvi
FROM fields f
JOIN ndvi_records n ON n.field_id = f.id
WHERE f.is_active = true
  AND n.captured_date >= DATE '2026-01-01'
  AND n.captured_date < DATE '2026-02-01';
```

Monthly coverage:

```sql
SELECT
    DATE_TRUNC('month', n.captured_date)::date AS month,
    COUNT(DISTINCT n.field_id) AS fields_count,
    COUNT(*) AS records_count
FROM ndvi_records n
JOIN fields f ON f.id = n.field_id
WHERE f.is_active = true
  AND n.captured_date >= DATE '2026-01-01'
GROUP BY DATE_TRUNC('month', n.captured_date)
ORDER BY month;
```

## Acceptance Criteria
The task is accepted only if all criteria below are true:

1. `backend/scripts/run_remaining_backfill.py` no longer classifies a field as complete merely because it has any NDVI row before a June cutoff.
2. Candidate selection is based on `MIN(ndvi_records.captured_date)` per active field.
3. Fields with `MIN(captured_date) IS NULL` are included.
4. Fields with `MIN(captured_date) > DATE '2026-02-01'` are included.
5. Fields whose only existing records are June scheduler records are included.
6. For each selected field with an existing first NDVI row, the script backfills only `[2026-01-01, first_ndvi_date)` and does not request dates on or after `first_ndvi_date`.
7. Every inserted row passes `validate_ndvi_quality()` directly or via `fetch_ndvi_for_field_date()`.
8. Re-running the script does not create duplicate `(field_id, captured_date)` rows.
9. The duplicate detection SQL returns zero rows after the backfill.
10. January coverage increases from the previously reported 228 fields, except for fields where all January attempts are rejected by quality gate or no satellite data exists.
11. Field ids `64-94` and `211`, previously reported as June-only/no-January cases, are not skipped by the candidate selection logic if their earliest record is after `2026-02-01`.
12. `ndvi_change` and `ndvi_change_pct` are recalculated chronologically for every processed field after inserts.
13. The script has a `--dry-run` mode that performs no database writes and prints selected fields plus computed windows.
14. `python -m py_compile backend\scripts\run_remaining_backfill.py backend\services\satellite.py backend\scheduler.py` passes.
15. No frontend files, scheduler architecture, database schema, migrations, AI, PDF, Telegram, or deployment files are changed.

## Final report required from Claude Code
After implementation and validation, provide a concise report with:

1. files changed;
2. exact old selection criterion found in `run_remaining_backfill.py`;
3. exact new selection criterion implemented;
4. dry-run candidate count;
5. real run summary counters;
6. duplicate detection result;
7. January coverage before and after;
8. any fields still lacking January records with the reason from logs: no data, quality gate rejection, or error.
