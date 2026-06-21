# TASK_014 — AI Agronomic Recommendations via Claude API

## Skills to load before starting
- `/mnt/skills/user/full-output-enforcement/SKILL.md`
- `/mnt/skills/public/product-self-knowledge/SKILL.md`

---

## Goal

Add an "🤖 AI Analysis" button to each alert card in the Recommendations tab of EnterpriseDetailPage.
When clicked, it calls Claude API (via backend) and returns a detailed agronomic recommendation
specific to that field's situation: crop type, NDVI trend, weather, irrigation type.

---

## Step 1: Install Anthropic SDK in backend

```bash
cd C:\AgroSat\backend
pip install anthropic
```

---

## Step 2: Add backend endpoint

Create file `backend/api/ai.py`:

```python
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
import anthropic
import os

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai"])

def get_anthropic_client():
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=api_key)


@router.post("/recommend")
async def get_ai_recommendation(payload: dict, db: Session = Depends(get_db)):
    """
    Generate AI agronomic recommendation for a specific alert + field.
    
    Payload:
        alert_id: int
        field_id: int
    """
    alert_id = payload.get("alert_id")
    field_id = payload.get("field_id")

    if not alert_id or not field_id:
        raise HTTPException(status_code=400, detail="alert_id and field_id required")

    # --- Load field data ---
    field_row = db.execute(text("""
        SELECT
            f.name, f.code, f.area_ha, f.irrigation_type,
            f.centroid_lat, f.centroid_lon,
            ct.name_ru as crop_name,
            e.name as enterprise_name
        FROM fields f
        LEFT JOIN crop_seasons cs ON cs.field_id = f.id AND cs.season_year = 2026
        LEFT JOIN crop_types ct ON ct.id = cs.crop_type_id
        LEFT JOIN enterprises e ON e.id = f.enterprise_id
        WHERE f.id = :fid
    """), {"fid": field_id}).fetchone()

    if not field_row:
        raise HTTPException(status_code=404, detail="Field not found")

    # --- Load alert data ---
    alert_row = db.execute(text("""
        SELECT alert_type, severity, title, description,
               triggered_value, threshold_value, created_at
        FROM alerts
        WHERE id = :aid
    """), {"aid": alert_id}).fetchone()

    if not alert_row:
        raise HTTPException(status_code=404, detail="Alert not found")

    # --- Load NDVI history (last 30 days) ---
    ndvi_rows = db.execute(text("""
        SELECT captured_date, mean_ndvi, change_pct
        FROM ndvi_records
        WHERE field_id = :fid
        ORDER BY captured_date DESC
        LIMIT 10
    """), {"fid": field_id}).fetchall()

    ndvi_history = [
        f"{row.captured_date}: NDVI={row.mean_ndvi:.4f}"
        + (f", изменение={row.change_pct:+.1f}%" if row.change_pct else "")
        for row in ndvi_rows
    ]

    # --- Load weather (last 3 days from open-meteo via weather service) ---
    weather_summary = "Данные о погоде недоступны"
    try:
        from services.weather import WeatherService
        weather_svc = WeatherService()
        if field_row.centroid_lat and field_row.centroid_lon:
            weather = await weather_svc.get_current_weather(
                field_row.centroid_lat, field_row.centroid_lon
            )
            if weather:
                weather_summary = (
                    f"Температура: {weather.get('temperature_2m', '?')}°C, "
                    f"Влажность: {weather.get('relative_humidity_2m', '?')}%, "
                    f"Осадки: {weather.get('precipitation', 0)} мм, "
                    f"Скорость ветра: {weather.get('wind_speed_10m', '?')} км/ч"
                )
    except Exception as e:
        logger.warning(f"Weather fetch failed: {e}")

    # --- Build prompt ---
    irrigation_map = {
        "drip": "капельное орошение (томчи)",
        "furrow": "полив по бороздам (очик)",
        "sprinkler": "дождевание",
        "flood": "затопление",
    }
    irrigation = irrigation_map.get(
        str(field_row.irrigation_type).lower(),
        field_row.irrigation_type or "не указано"
    )

    prompt = f"""Ты — опытный агроном-консультант для Бухарской области Узбекистана.
Проанализируй ситуацию на поле и дай конкретные практические рекомендации.

## Данные поля
- Предприятие: {field_row.enterprise_name}
- Поле: {field_row.name} (код: {field_row.code})
- Площадь: {field_row.area_ha:.1f} га
- Культура: {field_row.crop_name or 'не указана'}
- Тип орошения: {irrigation}

## Алерт (проблема)
- Тип: {alert_row.alert_type}
- Критичность: {alert_row.severity}
- Заголовок: {alert_row.title}
- Описание: {alert_row.description}
- Значение NDVI при алерте: {alert_row.triggered_value}
- Пороговое значение: {alert_row.threshold_value}

## История NDVI (последние 10 измерений, новые сначала)
{chr(10).join(ndvi_history) if ndvi_history else 'Нет данных'}

## Текущая погода
{weather_summary}

## Задача
Дай структурированный агрономический анализ и рекомендации. Будь конкретным и практичным.
Учитывай что сейчас июнь 2026 года — активная вегетация хлопка/уборка пшеницы.

Ответ дай строго в следующем формате (используй именно эти заголовки):

### 🔍 Диагноз
[1-2 предложения: что происходит с посевом и почему]

### ⚠️ Основные риски
[маркированный список из 2-3 конкретных рисков для данной культуры и региона]

### ✅ Рекомендуемые действия
[нумерованный список из 3-5 конкретных действий с указанием сроков]

### 💧 Режим орошения
[конкретная рекомендация по поливу с нормой воды для данной культуры]

### 📅 Контрольная точка
[когда и что проверить через N дней]
"""

    # --- Call Claude API ---
    try:
        client = get_anthropic_client()
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        recommendation = message.content[0].text
    except anthropic.APIError as e:
        logger.error(f"Claude API error: {e}")
        raise HTTPException(status_code=502, detail=f"Claude API error: {str(e)}")

    return {
        "alert_id": alert_id,
        "field_id": field_id,
        "field_name": field_row.name,
        "recommendation": recommendation,
        "model": "claude-sonnet-4-6",
    }
```

---

## Step 3: Register router in `backend/main.py`

Find where other routers are registered and add:

```python
from api.ai import router as ai_router
app.include_router(ai_router)
```

---

## Step 4: Add ANTHROPIC_API_KEY to `backend/.env`

Add this line to `.env`:
```
ANTHROPIC_API_KEY=your_key_here
```

The user will fill in the actual key. For now leave it as placeholder.

---

## Step 5: Frontend — AI button in alert cards

Edit `frontend/src/pages/EnterpriseDetailPage.jsx` (or wherever alert cards are rendered in the Рекомендации tab).

Add state for AI results:
```jsx
const [aiResults, setAiResults] = useState({});   // keyed by alert.id
const [aiLoading, setAiLoading] = useState({});   // keyed by alert.id
```

Add handler:
```jsx
async function handleAIRecommend(alert) {
  if (aiResults[alert.id]) return; // already loaded, don't re-fetch
  setAiLoading(prev => ({ ...prev, [alert.id]: true }));
  try {
    const res = await apiClient.post('/api/ai/recommend', {
      alert_id: alert.id,
      field_id: alert.field_id,
    });
    setAiResults(prev => ({ ...prev, [alert.id]: res.data.recommendation }));
  } catch (err) {
    setAiResults(prev => ({ ...prev, [alert.id]: '⚠️ Ошибка получения рекомендации. Попробуйте позже.' }));
  } finally {
    setAiLoading(prev => ({ ...prev, [alert.id]: false }));
  }
}
```

In each alert card, add the AI button at the bottom:
```jsx
{/* AI Button */}
<div style={{ marginTop: 10 }}>
  {!aiResults[alert.id] ? (
    <button
      onClick={() => handleAIRecommend(alert)}
      disabled={aiLoading[alert.id]}
      style={{
        background: aiLoading[alert.id] ? '#1a2818' : 'transparent',
        border: '1px solid #4ade80',
        borderRadius: 6,
        padding: '6px 14px',
        color: '#4ade80',
        fontSize: 12,
        cursor: aiLoading[alert.id] ? 'not-allowed' : 'pointer',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        transition: 'all 0.15s',
      }}
    >
      {aiLoading[alert.id] ? (
        <>⏳ Анализирую...</>
      ) : (
        <>🤖 Углублённый AI анализ</>
      )}
    </button>
  ) : (
    /* AI Result Card */
    <div style={{
      marginTop: 8,
      background: '#0a1a0a',
      border: '1px solid #2d4a2d',
      borderRadius: 8,
      padding: '12px 14px',
      fontSize: 13,
      color: '#c8e6c9',
      lineHeight: 1.6,
      whiteSpace: 'pre-wrap',
    }}>
      <div style={{
        fontSize: 11,
        color: '#4ade80',
        fontWeight: 600,
        marginBottom: 8,
        display: 'flex',
        alignItems: 'center',
        gap: 6,
      }}>
        🤖 AI Анализ (Claude)
      </div>
      {aiResults[alert.id]}
    </div>
  )}
</div>
```

---

## Step 6: Test the endpoint manually

After restarting the backend (`uvicorn main:app --reload --port 8000`), test:

```bash
curl -X POST http://localhost:8000/api/ai/recommend \
  -H "Content-Type: application/json" \
  -d '{"alert_id": 1, "field_id": 1}'
```

Should return JSON with `recommendation` field containing Claude's analysis in Russian.

---

## Checklist
- [ ] `pip install anthropic` done
- [ ] `backend/api/ai.py` created
- [ ] Router registered in `backend/main.py`
- [ ] `ANTHROPIC_API_KEY=placeholder` added to `.env`
- [ ] Frontend AI button added to alert cards in Рекомендации tab
- [ ] `aiResults` and `aiLoading` state added
- [ ] Clicking button shows spinner then AI text
- [ ] Already-loaded results are cached (no duplicate API calls)

---

## Important notes
- All UI text in Russian
- Do not use Docker
- The ANTHROPIC_API_KEY value will be filled in by the user manually
- Model: always use `claude-sonnet-4-6`
- Max tokens: 1024 (enough for structured recommendation)
- If API key is missing → return clear error message in UI, do not crash
