"""
AI агрономические рекомендации через Claude API.

Эндпоинт:
    POST /api/ai/recommend — агрономический анализ ситуации на поле
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from config import settings
import anthropic

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/recommend")
def get_ai_recommendation(payload: dict, db: Session = Depends(get_db)):
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
        SELECT alert_type, severity, title, description, recommendation,
               triggered_value, threshold_value
        FROM alerts
        WHERE id = :aid
    """), {"aid": alert_id}).fetchone()

    if not alert_row:
        raise HTTPException(status_code=404, detail="Alert not found")

    # --- Load NDVI history (last 10 records) ---
    ndvi_rows = db.execute(text("""
        SELECT captured_date, mean_ndvi, ndvi_change_pct
        FROM ndvi_records
        WHERE field_id = :fid
        ORDER BY captured_date DESC
        LIMIT 10
    """), {"fid": field_id}).fetchall()

    ndvi_history = [
        f"{row.captured_date}: NDVI={row.mean_ndvi:.4f}"
        + (f", изменение={row.ndvi_change_pct:+.1f}%" if row.ndvi_change_pct else "")
        for row in ndvi_rows
    ]

    # --- Load weather (current conditions from open-meteo, synchronous httpx) ---
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
- Базовая рекомендация системы: {alert_row.recommendation or 'нет'}
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
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        if not settings.anthropic_api_key:
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

    return {
        "alert_id": alert_id,
        "field_id": field_id,
        "field_name": field_row.name,
        "recommendation": recommendation,
        "model": "claude-sonnet-4-6",
    }
