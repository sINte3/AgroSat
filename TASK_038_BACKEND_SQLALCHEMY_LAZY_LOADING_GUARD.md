# TASK_038_BACKEND_SQLALCHEMY_LAZY_LOADING_GUARD

## Role

You are working on AgroSat, an industrial AgTech/GIS backend.

Act as a senior backend engineer. Make a small surgical backend-only change. Do not commit.

## Context

Audit facts:

- There are 14 SQLAlchemy relationship declarations.
- All 14 relationships currently have no explicit lazy option.
- There is no joinedload, selectinload, contains_eager, subqueryload, raiseload, or lazy option usage in backend code.
- Real relationship access was found only in:
  - backend/models/field.py: Field.current_season iterates self.seasons
  - backend/services/alert_engine.py: field.seasons
  - backend/services/alert_engine.py: current_season.crop_type
- analyze_field_ndvi(field, record, db) is called from:
  - backend/scheduler.py
  - backend/scripts/fetch_all_ndvi.py

Project rule: hidden SQLAlchemy lazy loading is forbidden. Use explicit eager loading or explicit joins only.

## Allowed files

You may modify only:

- backend/models/crop.py
- backend/models/enterprise.py
- backend/models/field.py
- backend/models/monitoring.py
- backend/scheduler.py
- backend/scripts/fetch_all_ndvi.py

## Forbidden

- Do not touch frontend.
- Do not modify database schema.
- Do not create Alembic migrations.
- Do not modify backend/main.py.
- Do not change scheduler architecture.
- Do not move APScheduler into FastAPI.
- Do not rewrite unrelated APIs.
- Do not fix unrelated encoding/BOM warnings.
- Do not commit.

## Required changes

1. Add lazy-loading guard to every relationship in the allowed model files.

Every relationship must use:

lazy="raise_on_sql"

Target relationships:

- CropType.seasons
- Enterprise.fields
- Enterprise.users
- Field.enterprise
- Field.seasons
- Field.ndvi_records
- Field.alerts
- Field.scouting_notes
- CropSeason.field
- CropSeason.crop_type
- NDVIRecord.field
- Alert.field
- ScoutingNote.field
- User.enterprise

2. Fix backend/scheduler.py.

The scheduler currently loads active fields and later calls analyze_field_ndvi(field, record, db).

Update the active Field query so it explicitly eager-loads:

- Field.seasons
- CropSeason.crop_type

Use SQLAlchemy selectinload.

Expected pattern:

from sqlalchemy.orm import selectinload
from models.field import Field, CropSeason

fields = (
    db.query(Field)
    .options(selectinload(Field.seasons).selectinload(CropSeason.crop_type))
    .filter(Field.is_active == True)
    .all()
)

Keep behavior otherwise unchanged.

3. Fix backend/scripts/fetch_all_ndvi.py.

Find the Field query that feeds analyze_field_ndvi(field, record, db).

Update it with the same selectinload strategy:

Field.seasons -> CropSeason.crop_type

Import selectinload and CropSeason as needed.

4. Keep Field.current_season passive.

Do not make Field.current_season query the database. It may continue iterating self.seasons. With lazy="raise_on_sql", this property must only work when seasons were explicitly loaded.

## Validation commands to run and report

Run:

Set-Location C:\AgroSat
git status --short
git diff --name-status
git diff --check

Run py_compile:

Set-Location C:\AgroSat
& C:\AgroSat\backend\venv\Scripts\python.exe -m py_compile backend\models\crop.py backend\models\enterprise.py backend\models\field.py backend\models\monitoring.py backend\scheduler.py backend\scripts\fetch_all_ndvi.py

Run relationship inspection:

Set-Location C:\AgroSat
& C:\AgroSat\backend\venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend'); from sqlalchemy.inspection import inspect; from models.crop import CropType; from models.enterprise import Enterprise; from models.field import Field, CropSeason; from models.monitoring import NDVIRecord, Alert, ScoutingNote, User; classes=[CropType,Enterprise,Field,CropSeason,NDVIRecord,Alert,ScoutingNote,User]; bad=[]; [bad.append(f'{c.__name__}.{r.key}:{r.lazy}') for c in classes for r in inspect(c).relationships if r.lazy!='raise_on_sql']; print('BAD_RELATIONSHIPS=', bad); raise SystemExit(1 if bad else 0)"

Run import smoke:

Set-Location C:\AgroSat\backend
& C:\AgroSat\backend\venv\Scripts\python.exe -c "import main; import scheduler; from scripts import fetch_all_ndvi; print('IMPORT_OK')"

Run final:

Set-Location C:\AgroSat
git status --short
git diff --name-status

## Acceptance criteria

- Every SQLAlchemy relationship has lazy="raise_on_sql".
- scheduler.py explicitly eager-loads Field.seasons -> CropSeason.crop_type.
- fetch_all_ndvi.py explicitly eager-loads Field.seasons -> CropSeason.crop_type.
- No frontend files changed.
- No migrations created.
- backend/main.py unchanged.
- Validation commands pass.
- Final diff includes only allowed files.
