@echo off
chcp 65001 >nul
title AgroSat Launcher
echo ========================================
echo   AgroSat — Запуск системы
echo ========================================
echo.

:: --- Start Backend (minimized window) ---
echo [1/2] Запуск Backend (FastAPI)...
start "AgroSat Backend" /min cmd /k "cd /d C:\AgroSat\backend && venv\Scripts\activate && python -m uvicorn main:app --port 8000"

:: Wait 4 seconds for backend to initialize
timeout /t 4 /nobreak > nul

:: --- Start Frontend (minimized window) ---
echo [2/2] Запуск Frontend (React)...
start "AgroSat Frontend" /min cmd /k "cd /d C:\AgroSat\frontend && npm run dev"

:: Wait 4 seconds for frontend to initialize
timeout /t 4 /nobreak > nul

:: --- Open browser ---
echo Открываем браузер...
start http://localhost:5173

:: --- Create desktop shortcut (run once) ---
if not exist "%USERPROFILE%\Desktop\AgroSat.lnk" (
    set SHORTCUT="%USERPROFILE%\Desktop\AgroSat.lnk"
    set TARGET="C:\AgroSat\start.bat"
    powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut(%SHORTCUT%); $s.TargetPath = %TARGET%; $s.IconLocation = 'C:\AgroSat\frontend\public\favicon.ico'; $s.Description = 'Запустить AgroSat'; $s.Save()"
    echo Ярлык создан на рабочем столе!
)

echo.
echo ========================================
echo   AgroSat запущен!
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:5173
echo ========================================
echo.
echo Для остановки закройте окна "AgroSat Backend" и "AgroSat Frontend"
echo (они свёрнуты в панели задач)
echo.
pause
