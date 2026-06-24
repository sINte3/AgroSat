# TASK_037_FIX_START_BAT_SCHEDULER_WARNING.md

## Objective

Fix the obsolete scheduler warning logic in `start.bat`.

The current launcher warns that the backend does not expose `AGROSAT_DISABLE_SCHEDULER`, then starts uvicorn without `--reload` to avoid duplicate APScheduler jobs.

That warning is now obsolete because the FastAPI web process no longer starts APScheduler.

## Confirmed audit facts

`backend/main.py` contains FastAPI lifespan, but it does not contain:

* `start_scheduler`
* `BackgroundScheduler`
* `AsyncIOScheduler`
* `APScheduler`
* `AGROSAT_DISABLE_SCHEDULER`

`backend/scheduler.py` contains the APScheduler implementation.

`backend/scripts/run_ndvi_scheduler.py` is the standalone scheduler runner. It explicitly says:

* it starts only APScheduler jobs;
* it does not start uvicorn;
* it does not import the FastAPI app.

Therefore scheduler execution is already isolated from the FastAPI web process.

## Strict scope

Allowed file:

* `start.bat`

Do not modify:

* `backend/main.py`
* `backend/scheduler.py`
* `backend/scripts/run_ndvi_scheduler.py`
* frontend files
* database files
* migrations
* `.env`
* dependencies
* Git config

## Required behavior

Update `start.bat` so that:

1. It no longer checks for `AGROSAT_DISABLE_SCHEDULER` inside `backend/main.py`.
2. It no longer prints the false warning:

   * `Backend does not expose AGROSAT_DISABLE_SCHEDULER gate.`
   * `Starting without --reload to avoid duplicate APScheduler jobs.`
3. It treats the current architecture as safe for web reload because `backend/main.py` does not start scheduler.
4. It starts the FastAPI backend with `--reload` for local development when `backend/main.py` does not reference `start_scheduler`.
5. It still protects against future regression:

   * If `backend/main.py` ever contains `start_scheduler`, `start.bat` must print a clear warning and start uvicorn without `--reload`.

## Recommended minimal implementation

Replace the obsolete `AGROSAT_DISABLE_SCHEDULER` check with a direct check for `start_scheduler` in `backend/main.py`.

Expected logic:

* set `UVICORN_RELOAD=--reload` by default;
* run `findstr` against `%BACKEND_DIR%\main.py` for `start_scheduler`;
* if found:

  * print a warning that scheduler startup was detected in the FastAPI web process;
  * clear `UVICORN_RELOAD`;
* if not found:

  * print that no web-process scheduler startup was detected and `--reload` is enabled.

Do not add `AGROSAT_DISABLE_SCHEDULER` to backend Python code.

## Validation commands

Run from `C:\AgroSat`:

```cmd
git status --short
git diff --name-status
git diff --check
git diff -- start.bat
```

Then run:

```cmd
start.bat
```

Expected launcher behavior:

* no warning about missing `AGROSAT_DISABLE_SCHEDULER`;
* no warning about duplicate APScheduler jobs;
* backend starts with uvicorn `--reload`;
* Redis warning may still appear and is not part of this task;
* frontend starts normally.

Optional runtime checks:

```cmd
curl http://localhost:8000/health
curl http://localhost:5173
```

## Non-goals

Do not change scheduler implementation.
Do not change NDVI collection behavior.
Do not change FastAPI lifespan.
Do not add APScheduler to web runtime.
Do not remove the standalone scheduler script.
Do not commit changes.
