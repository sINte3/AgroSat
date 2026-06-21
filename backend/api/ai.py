"""
AI агрономические рекомендации через Claude API.

Эндпоинт:
    POST /api/ai/recommend — агрономический анализ ситуации на поле
"""

import logging
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from config import settings
from models.monitoring import User
from api.auth import get_current_active_user
import anthropic

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/recommend")
def get_ai_recommendation(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Generate AI agronomic recommendation for a specific alert + field.

    Payload:
        alert_id: int
        field_id: int
    """
    try:
        alert_id = int(payload.get("alert_id"))
        field_id = int(payload.get("field_id"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="alert_id and field_id must be integer",
        )

    if alert_id <= 0 or field_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="alert_id and field_id must be positive",
        )

    role = str(current_user.role or "").lower()

    if role == "viewer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Пользователям с правами гостя запрещено генерировать ИИ-рекомендации",
        )

    if role not in {"admin", "manager", "agronomist"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для генерации ИИ-рекомендации",
        )

    # --- Load field + alert + crop + enterprise in one joined query ---
    context_row = db.execute(text("""
        SELECT
            f.id AS field_id,
            f.enterprise_id,
            f.name,
            f.code,
            f.area_ha,
            f.irrigation_type,
            f.centroid_lat,
            f.centroid_lon,
            ct.name_ru AS crop_name,
            e.name AS enterprise_name,
            a.id AS alert_id,
            a.alert_type,
            a.severity,
            a.title,
            a.description,
            a.recommendation,
            a.triggered_value,
            a.threshold_value
        FROM alerts a
        JOIN fields f ON f.id = a.field_id
        JOIN enterprises e ON e.id = f.enterprise_id
        LEFT JOIN crop_seasons cs ON cs.field_id = f.id AND cs.season_year = :year
        LEFT JOIN crop_types ct ON ct.id = cs.crop_type_id
        WHERE a.id = :aid
          AND f.id = :fid
        LIMIT 1
    """), {
        "aid": alert_id,
        "fid": field_id,
        "year": date.today().year,
    }).fetchone()

    if not context_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Field or alert not found",
        )

    if role == "agronomist":
        if current_user.enterprise_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="У пользователя-агронома не указано предприятие",
            )

        if context_row.enterprise_id != current_user.enterprise_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Агроном может запрашивать ИИ-рекомендации только для полей своего предприятия",
            )

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
        if context_row.centroid_lat and context_row.centroid_lon:
            lat = context_row.centroid_lat
            lon = context_row.centroid_lon
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
        str(context_row.irrigation_type).lower(),
        context_row.irrigation_type or "не указано"
    )

    prompt = f"""Ты — опытный агроном-консультант для Бухарской области Узбекистана.
Проанализируй ситуацию на поле и дай конкретные практические рекомендации.

## Данные поля
- Предприятие: {context_row.enterprise_name}
- Поле: {context_row.name} (код: {context_row.code})
- Площадь: {context_row.area_ha:.1f} га
- Культура: {context_row.crop_name or 'не указана'}
- Тип орошения: {irrigation}

## Алерт (проблема)
- Тип: {context_row.alert_type}
- Критичность: {context_row.severity}
- Заголовок: {context_row.title}
- Описание: {context_row.description}
- Базовая рекомендация системы: {context_row.recommendation or 'нет'}
- Значение NDVI при алерте: {context_row.triggered_value}
- Пороговое значение: {context_row.threshold_value}

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
        "field_name": context_row.name,
        "recommendation": recommendation,
        "model": "claude-sonnet-4-6",
    }
