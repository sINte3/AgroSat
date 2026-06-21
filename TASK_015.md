# TASK_015 — Fix AI error + Automate startup (no open terminals)

## Skills to load
- `/mnt/skills/user/full-output-enforcement/SKILL.md`

---

## Part 1: Fix AI recommendation error in `backend/api/ai.py`

There are two likely bugs. Fix both:

### Bug 1: Async/sync mismatch with weather service

The weather call uses `await` but the weather service method might be synchronous.
Replace the entire weather block with a safe synchronous version:

```python
# Replace the weather block with this safe version:
weather_summary = "Данные о погоде недоступны"
try:
    import httpx
    if field_row.centroid_lat and field_row.centroid_lon:
        lat = field_row.centroid_lat
        lon = field_row.centroid_lon
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m"
            f"&timezone=Asia/Tashkent"
        )
        resp = httpx.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json().get("current", {})
            weather_summary = (
                f"Температура: {data.get('temperature_2m', '?')}°C, "
                f"Влажность: {data.get('relative_humidity_2m', '?')}%, "
                f"Осадки: {data.get('precipitation', 0)} мм, "
                f"Ветер: {data.get('wind_speed_10m', '?')} км/ч"
            )
except Exception as e:
    logger.warning(f"Weather fetch failed: {e}")
```

Install httpx if not already installed:
```bash
pip install httpx
```

### Bug 2: Better error logging

Wrap the entire Claude API call with detailed error logging so we can see what's failing:

```python
try:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ValueError("ANTHROPIC_API_KEY is empty or not set in .env")
    
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    recommendation = message.content[0].text
    logger.info(f"AI recommendation generated for field {field_id}, alert {alert_id}")
    
except anthropic.AuthenticationError as e:
    logger.error(f"Claude API auth error — check ANTHROPIC_API_KEY in .env: {e}")
    raise HTTPException(status_code=502, detail="Ошибка авторизации Claude API. Проверьте ANTHROPIC_API_KEY в .env")
except anthropic.RateLimitError as e:
    logger.error(f"Claude API rate limit: {e}")
    raise HTTPException(status_code=429, detail="Превышен лимит запросов Claude API. Попробуйте через минуту.")
except Exception as e:
    logger.error(f"Claude API unexpected error: {type(e).__name__}: {e}")
    raise HTTPException(status_code=502, detail=f"Ошибка Claude API: {str(e)[:200]}")
```

### Bug 3: Make endpoint synchronous (remove async for Claude call)

Change the endpoint signature to avoid async issues with the sync anthropic client:

```python
# CHANGE:
@router.post("/recommend")
async def get_ai_recommendation(payload: dict, db: Session = Depends(get_db)):

# TO:
@router.post("/recommend")
def get_ai_recommendation(payload: dict, db: Session = Depends(get_db)):
```

Remove all `await` keywords inside this function since it's now synchronous.

---

## Part 2: Automate startup — no open terminals

Create file `C:\AgroSat\start.bat`:

```bat
@echo off
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
```

Create file `C:\AgroSat\stop.bat`:

```bat
@echo off
title AgroSat Stop
echo Останавливаем AgroSat...
taskkill /FI "WINDOWTITLE eq AgroSat Backend*" /F > nul 2>&1
taskkill /FI "WINDOWTITLE eq AgroSat Frontend*" /F > nul 2>&1
echo Готово. AgroSat остановлен.
pause
```

Create shortcut on Desktop:
```bat
:: Add this to the end of start.bat (before final pause) to create desktop shortcut:
set SHORTCUT="%USERPROFILE%\Desktop\AgroSat.lnk"
set TARGET="C:\AgroSat\start.bat"
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut(%SHORTCUT%); $s.TargetPath = %TARGET%; $s.IconLocation = 'C:\AgroSat\frontend\public\favicon.ico'; $s.Description = 'Запустить AgroSat'; $s.Save()"
echo Ярлык создан на рабочем столе!
```

---

## Checklist
- [ ] `httpx` installed in venv
- [ ] `backend/api/ai.py` — weather block replaced with httpx version
- [ ] `backend/api/ai.py` — endpoint changed to sync (`def` not `async def`)
- [ ] `backend/api/ai.py` — detailed error logging added
- [ ] `C:\AgroSat\start.bat` created
- [ ] `C:\AgroSat\stop.bat` created
- [ ] Desktop shortcut created

## Testing AI fix
After restarting backend, open browser console (F12) and click "🤖 Углублённый AI анализ".
If still failing, check backend terminal for the detailed error log line starting with "Claude API".

## Important
- venv must be activated before uvicorn
- ANTHROPIC_API_KEY must be set in `C:\AgroSat\backend\.env`
- Do not use Docker
