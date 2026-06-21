@echo off
title AgroSat Stop
echo Останавливаем AgroSat...
taskkill /FI "WINDOWTITLE eq AgroSat Backend*" /F > nul 2>&1
taskkill /FI "WINDOWTITLE eq AgroSat Frontend*" /F > nul 2>&1
echo Готово. AgroSat остановлен.
pause
