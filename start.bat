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
    findstr /c:"start_scheduler" "%BACKEND_DIR%\main.py" >nul 2>nul
    if errorlevel 1 (
        echo Backend does not start scheduler in web process. Enabling --reload for development.
        start "AgroSat Backend" /D "%BACKEND_DIR%" /min "%ComSpec%" /k ""%PY_EXE%" -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload"
    ) else (
        echo [WARNING] start_scheduler detected in "%BACKEND_DIR%\main.py".
        echo [WARNING] Starting without --reload to avoid duplicate APScheduler jobs.
        start "AgroSat Backend" /D "%BACKEND_DIR%" /min "%ComSpec%" /k ""%PY_EXE%" -m uvicorn main:app --host 127.0.0.1 --port 8000"
    )
    timeout /t 3 /nobreak >nul
) else (
    echo Backend port 8000 is already listening. Not starting another backend process.
)

echo.
echo [3/4] Checking frontend port 5173...
call :check_port 5173
if errorlevel 1 (
    start "AgroSat Frontend" /D "%FRONTEND_DIR%" /min "%ComSpec%" /k "npm run dev"
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
