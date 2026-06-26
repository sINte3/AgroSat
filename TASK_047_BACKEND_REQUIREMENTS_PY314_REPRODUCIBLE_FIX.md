# TASK_047_BACKEND_REQUIREMENTS_PY314_REPRODUCIBLE_FIX

## Role

You are Claude Code acting as a senior backend dependency/reproducibility engineer for the AgroSat project.

Your job is to make the backend dependency set reproducible from a clean clone on the currently installed development/runtime Python: **Python 3.14.5**.

This task is intentionally narrow. Do not turn it into a backend refactor.

---

## Context

Repository:

```text
C:\AgroSat
```

Current remote state after TASK_045:

```text
HEAD == origin/main == 41ccb42
branch: main
working tree: clean
```

TASK_046 clean-clone smoke partially passed:

```text
OK:
- source repo clean
- HEAD == origin/main == 41ccb42
- clean clone HEAD == 41ccb42
- frontend npm ci passed
- frontend build passed
- required task files exist
- no tracked .env/key/pem/token-like files
- clean clone git status clean

FAIL:
- backend pip install -r requirements.txt failed
- backend import main failed because dependencies were not installed
```

The failure was reproduced in TASK_046A matrix diagnostic.

Only Python available on this Windows machine:

```text
Python 3.14.5
```

Unavailable:

```text
Python 3.11
Python 3.12
Python 3.13
```

Current clean-clone `backend/requirements.txt` key pins:

```text
fastapi==0.111.0
uvicorn[standard]==0.29.0
sqlalchemy==2.0.30
geoalchemy2==0.15.1
alembic==1.13.1
psycopg2-binary==2.9.9
shapely==2.0.4
pyproj==3.6.1
rasterio==1.3.10
numpy==1.26.4
sentinelhub==3.10.2
pydantic-settings==2.2.1
pydantic==2.7.1
```

Observed blocker:

```text
psycopg2-binary==2.9.9 has no suitable binary wheel for Python 3.14.
pip wheel/download check on Python 3.14 showed available versions only 2.9.11 and 2.9.12.
pip tried to build psycopg2-binary==2.9.9 from source and failed.
```

The existing local working backend venv is already running with newer Python 3.14-compatible packages:

```text
fastapi==0.137.0
GeoAlchemy2==0.20.0
numpy==2.4.6
psycopg2-binary==2.9.12
pydantic==2.13.4
pydantic-settings==2.14.1
pydantic_core==2.46.4
pyproj==3.7.2
sentinelhub==3.11.5
shapely==2.1.2
SQLAlchemy==2.0.50
uvicorn==0.49.0
```

The project currently works in the local venv because that venv is newer than `requirements.txt`. The repository is not reproducible from a clean clone.

---

## Hard scope

Allowed to change:

```text
backend/requirements.txt
TASK_047_BACKEND_REQUIREMENTS_PY314_REPRODUCIBLE_FIX.md
```

Do not change anything else.

Forbidden:

```text
backend Python code changes
frontend changes
database migration changes
alembic revision changes
main.py changes
scheduler changes
APScheduler inside FastAPI/lifespan/web workers
format-only rewrites outside requirements.txt
lockfile generation unless already required by the project
```

If clean install requires changes outside `backend/requirements.txt`, stop and report exactly why. Do not expand scope.

---

## Technical rules

1. Keep dependency pins explicit.
2. Prefer versions already proven in the existing local Python 3.14 backend venv when compatible.
3. Do not weaken reproducibility by replacing pinned versions with broad ranges.
4. Do not remove required GIS/remote-sensing packages unless static code search proves they are unused and the removal is explicitly justified. In this task, prefer not to remove.
5. Do not introduce a new package manager.
6. Do not add Docker, Poetry, uv, Pipenv, Conda, or pyproject migration.
7. Do not touch secrets or `.env` files.
8. Do not modify `.gitignore`.
9. Do not commit.

---

## Required investigation

Before editing, inspect:

```powershell
Set-Location "C:\AgroSat"

git status --short
git rev-parse --short HEAD
git rev-parse --short origin/main

Get-Content "backend\requirements.txt" -Encoding UTF8
```

Also inspect imports to avoid accidentally removing needed packages:

```powershell
Select-String -Path "backend\**\*.py" -Pattern "fastapi|uvicorn|sqlalchemy|geoalchemy|alembic|psycopg2|shapely|pyproj|rasterio|numpy|sentinelhub|pydantic|pydantic_settings|dotenv|passlib|jwt|jose|bcrypt|httpx|requests|PIL|pillow|pandas" -CaseSensitive:$false
```

---

## Expected fix direction

Update `backend/requirements.txt` so clean install works on Python 3.14.5.

Use the existing local working venv as the primary evidence for compatible pins.

Minimum expected pin updates include:

```text
fastapi==0.137.0
uvicorn[standard]==0.49.0
sqlalchemy==2.0.50
geoalchemy2==0.20.0
psycopg2-binary==2.9.12
numpy==2.4.6
shapely==2.1.2
pyproj==3.7.2
sentinelhub==3.11.5
pydantic==2.13.4
pydantic-settings==2.14.1
```

You must check the rest of `requirements.txt` as well. Native/GIS packages such as `rasterio` may also need Python 3.14-compatible pins. Do not assume only `psycopg2-binary` is broken.

If one specific package has no Python 3.14 wheel and cannot install cleanly, stop and report the package and exact error. Do not hide the failure.

---

## Validation commands

Run all validation from the real repo first:

```powershell
Set-Location "C:\AgroSat"

Write-Host "===== STATUS / DIFF ====="
git status --short
git diff --name-status
git diff --check
```

Confirm only allowed files changed:

```powershell
$changed = git diff --name-only
$untracked = git ls-files --others --exclude-standard

Write-Host "Changed:"
$changed
Write-Host "Untracked:"
$untracked

$badChanged = $changed | Where-Object {
    $_ -ne "backend/requirements.txt"
}

$badUntracked = $untracked | Where-Object {
    $_ -ne "TASK_047_BACKEND_REQUIREMENTS_PY314_REPRODUCIBLE_FIX.md"
}

if ($badChanged -or $badUntracked) {
    Write-Host "FORBIDDEN_FILES_FOUND"
    $badChanged
    $badUntracked
    exit 1
}
```

Create a fresh venv outside the repo and install requirements:

```powershell
$ErrorActionPreference = "Stop"

$repo = "C:\AgroSat"
$tempRoot = "C:\AgroSat_temp"
$venvDir = Join-Path $tempRoot "TASK_047_backend_venv"
$pythonExe = "C:\Program Files\Python314\python.exe"

if (Test-Path $venvDir) {
    Remove-Item $venvDir -Recurse -Force
}

& $pythonExe -m venv $venvDir
$venvPy = Join-Path $venvDir "Scripts\python.exe"

& $venvPy --version
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r "$repo\backend\requirements.txt"
```

Run backend compile/import from the repo backend folder:

```powershell
Set-Location "C:\AgroSat\backend"

& $venvPy -m compileall -q .
if ($LASTEXITCODE -ne 0) {
    Write-Host "BACKEND_COMPILEALL_FAILED"
    exit 1
}
Write-Host "BACKEND_COMPILEALL_OK"

$appImportCode = @'
import sys
print("PYTHON:", sys.version)
try:
    import main
    print("APP_IMPORT_OK")
except Exception as e:
    print("APP_IMPORT_FAIL:", type(e).__name__, str(e))
    raise
'@

$appImportCode | & $venvPy -
if ($LASTEXITCODE -ne 0) {
    Write-Host "BACKEND_APP_IMPORT_FAILED"
    exit 1
}
```

Run a clean-clone backend dependency smoke to prove reproducibility from repository state, not the local working venv:

```powershell
$ErrorActionPreference = "Stop"

$originUrl = "https://github.com/sINte3/AgroSat.git"
$tempRoot = "C:\AgroSat_temp"
$cloneDir = Join-Path $tempRoot "TASK_047_clean_clone"
$venvDir = Join-Path $tempRoot "TASK_047_clean_clone_backend_venv"
$pythonExe = "C:\Program Files\Python314\python.exe"

foreach ($p in @($cloneDir, $venvDir)) {
    if (Test-Path $p) {
        Remove-Item $p -Recurse -Force
    }
}

git clone --branch main --single-branch $originUrl $cloneDir

# IMPORTANT:
# The remote still has old requirements until this task is committed and pushed.
# For pre-commit validation, copy the candidate requirements.txt from the working repo into the clean clone.
Copy-Item "C:\AgroSat\backend\requirements.txt" "$cloneDir\backend\requirements.txt" -Force

& $pythonExe -m venv $venvDir
$venvPy = Join-Path $venvDir "Scripts\python.exe"

& $venvPy --version
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r "$cloneDir\backend\requirements.txt"

Set-Location "$cloneDir\backend"

& $venvPy -m compileall -q .
if ($LASTEXITCODE -ne 0) {
    Write-Host "CLEAN_CLONE_BACKEND_COMPILEALL_FAILED"
    exit 1
}
Write-Host "CLEAN_CLONE_BACKEND_COMPILEALL_OK"

$appImportCode = @'
import sys
print("PYTHON:", sys.version)
try:
    import main
    print("APP_IMPORT_OK")
except Exception as e:
    print("APP_IMPORT_FAIL:", type(e).__name__, str(e))
    raise
'@

$appImportCode | & $venvPy -
if ($LASTEXITCODE -ne 0) {
    Write-Host "CLEAN_CLONE_BACKEND_APP_IMPORT_FAILED"
    exit 1
}

Write-Host "TASK_047_CLEAN_CLONE_BACKEND_REPRO_OK"
```

Run final status:

```powershell
Set-Location "C:\AgroSat"
git status --short
git diff --name-status
git diff --check
git diff -- backend/requirements.txt
```

---

## Acceptance criteria

This task is accepted only if all are true:

1. Only `backend/requirements.txt` and `TASK_047_BACKEND_REQUIREMENTS_PY314_REPRODUCIBLE_FIX.md` are changed/untracked.
2. `pip install -r backend/requirements.txt` succeeds in a fresh Python 3.14.5 venv.
3. `python -m compileall -q backend` succeeds.
4. `import main` succeeds and prints `APP_IMPORT_OK`.
5. Clean-clone backend smoke succeeds with the candidate `requirements.txt` copied into the clean clone.
6. No backend source code changed.
7. No frontend code changed.
8. No migrations changed.
9. No commit made.

---

## Report format

When done, report:

```text
TASK_047_RESULT
changed_files:
validation:
- fresh venv install:
- compileall:
- app import:
- clean clone backend smoke:
notable dependency pin changes:
risks:
commit: not made
```

If validation fails, report the exact failed package/step and stop.
