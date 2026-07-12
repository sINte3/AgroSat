@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title AgroSat Launcher

set "LAUNCHER_ROOT=%~dp0"
if "%LAUNCHER_ROOT:~-1%"=="\" set "LAUNCHER_ROOT=%LAUNCHER_ROOT:~0,-1%"
set "ROOT=%LAUNCHER_ROOT%"
set "MODE=runtime"

if /I "%~1"=="--preflight" (
    if not "%~2"=="" goto :usage_error
    set "MODE=preflight"
) else if /I "%~1"=="--preflight-root" (
    if "%~2"=="" goto :usage_error
    if not "%~3"=="" goto :usage_error
    for %%G in ("%~2") do set "ROOT=%%~fG"
    set "MODE=preflight"
) else if /I "%~1"=="--cleanup-frontend-link" (
    if "%~2"=="" goto :usage_error
    if "%~3"=="" goto :usage_error
    if not "%~4"=="" goto :usage_error
    call :cleanup_frontend_link "%~2" "%~3"
    if errorlevel 1 exit /b 1
    exit /b 0
) else if not "%~1"=="" goto :usage_error

set "BACKEND_DIR=%ROOT%\backend"
set "FRONTEND_DIR=%ROOT%\frontend"
set "MARKER=%FRONTEND_DIR%\.agrosat-launcher-node-modules.marker"
echo Source checkout root: "%ROOT%"

where git.exe >nul 2>nul || goto :git_error
if not exist "%BACKEND_DIR%\main.py" goto :checkout_error
if not exist "%FRONTEND_DIR%\package.json" goto :checkout_error
call :git_common "%LAUNCHER_ROOT%" LAUNCHER_COMMON
call :git_common "%ROOT%" SOURCE_COMMON
if not defined LAUNCHER_COMMON goto :identity_error
if not defined SOURCE_COMMON goto :identity_error
if /I not "%SOURCE_COMMON%"=="%LAUNCHER_COMMON%" goto :identity_error

set "PRIMARY_ROOT=C:\AgroSat"
call :git_common "%PRIMARY_ROOT%" PRIMARY_COMMON
if not defined PRIMARY_COMMON goto :identity_error
if /I not "%PRIMARY_COMMON%"=="%LAUNCHER_COMMON%" goto :identity_error
echo Verified dependency root: "%PRIMARY_ROOT%"

set "PY_EXE=%BACKEND_DIR%\venv\Scripts\python.exe"
if not exist "%PY_EXE%" set "PY_EXE=%PRIMARY_ROOT%\backend\venv\Scripts\python.exe"
if not exist "%PY_EXE%" (
    echo [ERROR] Backend Python runtime could not be resolved safely.
    exit /b 1
)
set "FRONTEND_DEP_MODE=local"
set "FRONTEND_TARGET=%FRONTEND_DIR%\node_modules"
if not exist "%FRONTEND_TARGET%\." (
    set "FRONTEND_DEP_MODE=shared"
    set "FRONTEND_TARGET=%PRIMARY_ROOT%\frontend\node_modules"
)
if not exist "%FRONTEND_TARGET%\." (
    echo [ERROR] Frontend dependencies could not be resolved safely.
    exit /b 1
)
where npm.cmd >nul 2>nul || (
    echo [ERROR] npm.cmd not found in PATH.
    exit /b 1
)
echo Selected Python: "%PY_EXE%"
echo Frontend dependencies: %FRONTEND_DEP_MODE% from "%FRONTEND_TARGET%"
if /I "%MODE%"=="preflight" (
    echo [PASS] Preflight completed. No services were started and no files were changed.
    exit /b 0
)

call :require_free_port 8000 || exit /b 1
call :require_free_port 5173 || exit /b 1
set "FRONTEND_LINK_CREATED=0"
if /I "%FRONTEND_DEP_MODE%"=="shared" (
    for /f "delims=" %%G in ('powershell -NoProfile -Command "[guid]::NewGuid().ToString('N')"') do set "RUN_ID=%%G"
    if not defined RUN_ID exit /b 1
    call :create_frontend_link "%FRONTEND_TARGET%" "%RUN_ID%" || exit /b 1
)
echo Starting backend from "%BACKEND_DIR%"
start "AgroSat Backend" /D "%BACKEND_DIR%" /min "%ComSpec%" /k ""%PY_EXE%" -m uvicorn main:app --host 127.0.0.1 --port 8000"
echo Starting frontend from "%FRONTEND_DIR%"
if "%FRONTEND_LINK_CREATED%"=="1" (
    start "AgroSat Frontend" /D "%FRONTEND_DIR%" /min "%ComSpec%" /c "call npm run dev ^& call ^"%LAUNCHER_ROOT%\start.bat^" --cleanup-frontend-link ^"%FRONTEND_TARGET%^" ^"%RUN_ID%^""
) else (
    start "AgroSat Frontend" /D "%FRONTEND_DIR%" /min "%ComSpec%" /k "npm run dev"
)
echo Backend:  http://127.0.0.1:8000
echo Frontend: http://127.0.0.1:5173
exit /b 0

:git_common
set "%~2="
for /f "usebackq delims=" %%G in (`git -C "%~1" rev-parse --path-format^=absolute --git-common-dir 2^>nul`) do for %%H in ("%%G") do set "%~2=%%~fH"
exit /b 0

:require_free_port
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %~1 -State Listen -ErrorAction SilentlyContinue; if(-not $c){exit 0}; $c|ForEach-Object{$p=Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue; Write-Host ('[ERROR] Port %~1 is occupied by PID {0} ({1}).' -f $_.OwningProcess,$p.ProcessName)}; exit 1"
exit /b %ERRORLEVEL%

:create_frontend_link
if exist "%FRONTEND_DIR%\node_modules" goto :ownership_error
if exist "%MARKER%" goto :ownership_error
>"%MARKER%" echo %~f1
>>"%MARKER%" echo %~2
cmd.exe /d /c mklink /J "%FRONTEND_DIR%\node_modules" "%~f1" >nul
if errorlevel 1 (
    del /q "%MARKER%" >nul 2>nul
    echo [ERROR] Could not create the frontend dependency junction.
    exit /b 1
)
set "FRONTEND_LINK_CREATED=1"
echo Shared frontend dependency junction created: yes
exit /b 0

:cleanup_frontend_link
set "FRONTEND_DIR=%LAUNCHER_ROOT%\frontend"
set "MARKER=%FRONTEND_DIR%\.agrosat-launcher-node-modules.marker"
if not exist "%MARKER%" goto :ownership_error
set "MARKER_TARGET="
set "MARKER_RUN_ID="
set /p "MARKER_TARGET=" <"%MARKER%"
for /f "usebackq skip=1 delims=" %%G in ("%MARKER%") do if not defined MARKER_RUN_ID set "MARKER_RUN_ID=%%G"
for %%G in ("%~1") do set "EXPECTED_TARGET=%%~fG"
if /I not "%MARKER_TARGET%"=="%EXPECTED_TARGET%" goto :ownership_error
if not "%MARKER_RUN_ID%"=="%~2" goto :ownership_error
powershell -NoProfile -Command "$i=Get-Item -LiteralPath '%FRONTEND_DIR%\node_modules' -Force -ErrorAction Stop; if(-not ($i.Attributes -band [IO.FileAttributes]::ReparsePoint)){exit 1}; $t=[IO.Path]::GetFullPath(($i.Target -join '')); $e=[IO.Path]::GetFullPath('%EXPECTED_TARGET%'); if($t -ine $e){exit 1}" >nul 2>nul
if errorlevel 1 goto :ownership_error
rmdir "%FRONTEND_DIR%\node_modules" >nul 2>nul || exit /b 1
if exist "%FRONTEND_DIR%\node_modules" exit /b 1
del /q "%MARKER%" >nul 2>nul || exit /b 1
echo Removed launcher-owned frontend dependency junction.
exit /b 0

:usage_error
echo [ERROR] Invalid launcher arguments.
exit /b 1
:git_error
echo [ERROR] Git is required to verify repository identity.
exit /b 1
:checkout_error
echo [ERROR] Source path is not a valid AgroSat checkout.
exit /b 1
:identity_error
echo [ERROR] Repository identity could not be verified.
exit /b 1
:ownership_error
echo [ERROR] Frontend dependency-link ownership is ambiguous; nothing was deleted.
exit /b 1
