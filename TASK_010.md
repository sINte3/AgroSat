# TASK_010: EMERGENCY FIX — Restore API Route Prefixes

## Problem

All API routers except `enterprises` lost their `/api/...` prefixes.
Frontend calls `/api/fields/geojson/all` but server only has `/geojson/all`.

This is a MINIMAL fix — only change the `APIRouter(prefix=...)` line in each file.
DO NOT rewrite files. DO NOT change any function logic.

---

## STEP 0: Read all router files first

Before making ANY changes, read and print the first 5 lines of each file to see current prefixes:

```bash
cd C:\AgroSat\backend
head -5 api/fields.py
head -5 api/alerts.py
head -5 api/dashboard.py
head -5 api/ndvi.py
head -5 api/weather.py
head -5 api/enterprises.py
```

On Windows use:
```powershell
Get-Content api\fields.py -Head 10
Get-Content api\alerts.py -Head 10
Get-Content api\dashboard.py -Head 10
Get-Content api\ndvi.py -Head 10
Get-Content api\weather.py -Head 10
Get-Content api\enterprises.py -Head 10
```

---

## STEP 1: Fix each router prefix

For EACH file below, find the `APIRouter(...)` line and ensure the prefix is EXACTLY as shown.
ONLY change the line with `APIRouter(...)`. Touch NOTHING else.

### `backend/api/fields.py`
```python
router = APIRouter(prefix="/api/fields", tags=["fields"])
```

### `backend/api/alerts.py`
```python
router = APIRouter(prefix="/api/alerts", tags=["alerts"])
```

### `backend/api/dashboard.py`
```python
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
```

### `backend/api/ndvi.py`
```python
router = APIRouter(prefix="/api/ndvi", tags=["ndvi"])
```

### `backend/api/weather.py`
```python
router = APIRouter(prefix="/api/weather", tags=["weather"])
```

### `backend/api/enterprises.py`
This one is already correct — verify but don't change:
```python
router = APIRouter(prefix="/api/enterprises", tags=["enterprises"])
```

---

## STEP 2: Check main.py include_router calls

Read `backend/main.py`. Make sure `include_router` does NOT override prefixes.

WRONG (overrides prefix to empty):
```python
app.include_router(fields_router, prefix="")
```

CORRECT (no prefix override):
```python
app.include_router(fields_router)
```

If any `include_router` call has `prefix=""` or `prefix="/"`, REMOVE the prefix parameter.

---

## STEP 3: Verify fix

After saving, uvicorn will auto-reload. Open:

```
http://localhost:8000/api/docs
```

Verify ALL routes have `/api/` prefix:
- `/api/fields/...` (not `/geojson/all`)
- `/api/alerts/...` (not `/{field_id}`)
- `/api/dashboard/summary` (not `/summary`)
- `/api/ndvi/...`
- `/api/weather/...`
- `/api/enterprises/...`

Then test in browser:
```
http://localhost:8000/api/fields/geojson/all
http://localhost:8000/api/dashboard/summary
http://localhost:8000/api/enterprises/
```

All three must return JSON data (not 404).

---

## CRITICAL RULES

- ONLY change the `APIRouter(prefix=...)` line in each file
- ONLY remove bad `prefix=` from `include_router()` calls in main.py
- DO NOT rewrite any file entirely
- DO NOT change any endpoint function
- DO NOT add new imports
- DO NOT change any function logic or SQL queries
