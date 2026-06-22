# TASK_032: Secure Windows start.bat Launcher for Redis, Backend, and Frontend

## Context
AgroSat is developed locally on Windows. The project is started from the repository root via `start.bat`. The launcher must start/check Redis, FastAPI backend, React frontend, and open the browser without leaking environment variables or launching unsafe executables through ambiguous Windows PATH resolution.

This task updates only the root `start.bat` file.

## Critical Safety Rules
1. Modify only `start.bat`.
2. Read the existing `start.bat` first.
3. Do not print environment variables.
4. Do not print `.env` contents.
5. Do not print `DATABASE_URL`, `SECRET_KEY`, Sentinel Hub credentials, Anthropic API key, Telegram bot token, or JWT tokens.
6. Do not launch `redis-server` by bare command name.
7. Do not rely only on `tasklist` to verify Redis.
8. Verify Redis by checking TCP port `127.0.0.1:6379`.
9. Verify backend/frontend ports before starting duplicate processes.
10. Use quoted paths everywhere.
11. Use paths relative to the location of `start.bat`, not hardcoded `C:\AgroSat`, except in user-facing messages.
12. Do not use Docker.
13. Do not modify backend Python code in this task.
14. `uvicorn --reload` must be enabled only if the backend already supports a scheduler-disable environment gate. If not supported, start backend without `--reload` and print a warning.

## File to Modify
* `start.bat`

No Python, frontend, Alembic, database, or `.env` files should be changed.

---

## Required Final `start.bat`

Replace the contents of `start.bat` with the following complete script:

```bat
@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title AgroSat Launcher

echo ========================================
echo   AgroSat - Windows Launcher
echo ========================================
echo.

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "BACKEND_DIR=%ROOT%\backend"
set "FRONTEND_DIR=%ROOT%\frontend"
set "PY_EXE=%BACKEND_DIR%\venv\Scripts\python.exe"

echo Project root: "%ROOT%"
echo.

if not exist "%BACKEND_DIR%\main.py" (
    echo [ERROR] Backend entrypoint not found: "%BACKEND_DIR%\main.py"
    echo Start this launcher from the AgroSat repository root.
    echo.
    pause
    exit /b 1
)

if not exist "%FRONTEND_DIR%\package.json" (
    echo [ERROR] Frontend package.json not found: "%FRONTEND_DIR%\package.json"
    echo.
    pause
    exit /b 1
)

if not exist "%PY_EXE%" (
    echo [ERROR] Python virtual environment not found: "%PY_EXE%"
    echo Create the backend virtual environment before using this launcher.
    echo.
    pause
    exit /b 1
)

where npm.cmd >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm.cmd not found in PATH.
    echo Install Node.js LTS and reopen Command Prompt.
    echo.
    pause
    exit /b 1
)

echo [1/4] Checking Redis on 127.0.0.1:6379...
call :check_port 6379
if errorlevel 1 (
    echo Redis is not listening on 127.0.0.1:6379.

    set "REDIS_EXE="
    if exist "%ROOT%\tools\redis\redis-server.exe" set "REDIS_EXE=%ROOT%\tools\redis\redis-server.exe"
    if not defined REDIS_EXE if exist "%ProgramFiles%\Redis\redis-server.exe" set "REDIS_EXE=%ProgramFiles%\Redis\redis-server.exe"
    if not defined REDIS_EXE if exist "%ProgramFiles(x86)%\Redis\redis-server.exe" set "REDIS_EXE=%ProgramFiles(x86)%\Redis\redis-server.exe"

    if not defined REDIS_EXE (
        echo [WARNING] redis-server.exe was not found in approved locations.
        echo Expected one of:
        echo   "%ROOT%\tools\redis\redis-server.exe"
        echo   "%ProgramFiles%\Redis\redis-server.exe"
        echo   "%ProgramFiles(x86)%\Redis\redis-server.exe"
        echo Backend can still start, but Redis cache will be unavailable.
    ) else (
        echo Starting Redis from: "%REDIS_EXE%"
        start "AgroSat Redis" /min "%REDIS_EXE%" --bind 127.0.0.1 --port 6379
        timeout /t 2 /nobreak >nul

        call :check_port 6379
        if errorlevel 1 (
            echo [WARNING] Redis was started but port 6379 is still not listening.
            echo Backend can still start, but cache may be unavailable.
        ) else (
            echo Redis is listening on 127.0.0.1:6379.
        )
    )
) else (
    echo Redis is already listening on 127.0.0.1:6379.
)

echo.
echo [2/4] Checking backend port 8000...
call :check_port 8000
if errorlevel 1 (
    set "UVICORN_RELOAD="
    findstr /c:"AGROSAT_DISABLE_SCHEDULER" "%BACKEND_DIR%\main.py" >nul 2>nul
    if errorlevel 1 (
        echo [WARNING] Backend does not expose AGROSAT_DISABLE_SCHEDULER gate.
        echo [WARNING] Starting without --reload to avoid duplicate APScheduler jobs.
    ) else (
        echo Backend scheduler-disable gate detected. Starting with --reload.
        set "AGROSAT_DISABLE_SCHEDULER=1"
        set "UVICORN_RELOAD=--reload"
    )

    start "AgroSat Backend" /min "%ComSpec%" /k "cd /d "%BACKEND_DIR%" && "%PY_EXE%" -m uvicorn main:app --host 127.0.0.1 --port 8000 %UVICORN_RELOAD%"
    timeout /t 3 /nobreak >nul
) else (
    echo Backend port 8000 is already listening. Not starting another backend process.
)

echo.
echo [3/4] Checking frontend port 5173...
call :check_port 5173
if errorlevel 1 (
    start "AgroSat Frontend" /min "%ComSpec%" /k "cd /d "%FRONTEND_DIR%" && npm run dev"
    timeout /t 3 /nobreak >nul
) else (
    echo Frontend port 5173 is already listening. Not starting another frontend process.
)

echo.
echo [4/4] Opening browser...
start "" "http://localhost:5173"

echo.
echo ========================================
echo   AgroSat launcher finished.
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:5173
echo   Redis:    127.0.0.1:6379
echo ========================================
echo.
echo If Redis cache is unavailable, backend logs may show:
echo "Redis not available, caching disabled"
echo.
pause
exit /b 0

:check_port
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort %~1 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
exit /b %ERRORLEVEL%
```

---

## Verification Criteria
- Run `start.bat` in one click.
- Verify 3 minimized command windows are spawned: Redis (if not running as a global service), FastAPI backend, and React frontend.
- Verify browser automatically opens `http://localhost:5173`.
- Verify backend logs show successful Redis connection (warning `Redis not available` must disappear).
- Verify frontend dashboard cards render instantaneously due to active cache.
```