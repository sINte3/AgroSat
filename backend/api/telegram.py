"""
API для управления Telegram уведомлениями.

Эндпоинты:
    POST /api/telegram/test       — отправить тестовое сообщение
    POST /api/telegram/send-alert — отправить уведомление по конкретному алерту
    GET  /api/telegram/status     — проверить настроен ли Telegram
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from config import settings
from services.telegram import send_telegram_message, format_alert_message
from models.monitoring import User
from api.auth import get_current_active_user
from api.dependencies import is_tenant_role, normalize_role
from services.cache import cache_get, cache_set

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/telegram", tags=["telegram"])


def _enforce_telegram_enabled() -> None:
    if not settings.telegram_notifications_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram уведомления отключены",
        )


def _enforce_cooldown(key: str, seconds: int, message: str) -> None:
    if cache_get(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=message,
        )
    cache_set(key, {"locked": True}, ttl_seconds=seconds)


@router.get("/status")
def telegram_status(current_user: User = Depends(get_current_active_user)):
    """Проверить настроен ли Telegram для авторизованного пользователя."""
    configured = bool(settings.telegram_bot_token and settings.telegram_chat_id)
    return {
        "configured": configured,
        "enabled": settings.telegram_notifications_enabled,
    }


@router.post("/test")
def send_test_notification(current_user: User = Depends(get_current_active_user)):
    """Отправить тестовое сообщение в Telegram. Только admin/manager."""
    role = normalize_role(current_user)

    if role not in {"admin", "manager"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только администраторы и менеджеры могут отправлять тестовые уведомления",
        )

    _enforce_telegram_enabled()
    _enforce_cooldown(
        key=f"telegram:test:user:{current_user.id}",
        seconds=60,
        message="Слишком частая отправка тестовых Telegram-уведомлений",
    )
    result = send_telegram_message(
        "✅ <b>Тест AgroSat</b>\n\n"
        "Если вы видите это сообщение — Telegram уведомления настроены правильно!\n\n"
        "🛰 AgroSat — Мониторинг полей"
    )
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result["error"])
    return {"ok": True, "message": "Тестовое сообщение отправлено"}


@router.post("/send-alert")
def send_alert_notification(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Отправить Telegram уведомление по конкретному алерту.

    Payload: {alert_id: int}
    """
    try:
        alert_id = int(payload.get("alert_id"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="alert_id must be integer",
        )

    if alert_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="alert_id must be positive",
        )

    role = normalize_role(current_user)

    if role == "viewer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Пользователям с правами гостя запрещено отправлять алерты в Telegram",
        )

    if role not in {"admin", "manager", "agronomist"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для отправки Telegram-уведомлений",
        )

    query_params = {"aid": alert_id}
    tenant_clause = ""
    if is_tenant_role(role):
        if current_user.enterprise_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant user has no enterprise_id",
            )
        tenant_clause = " AND f.enterprise_id = :eid"
        query_params["eid"] = current_user.enterprise_id

    _enforce_telegram_enabled()

    row = db.execute(text(f"""
        SELECT
            a.id,
            a.alert_type,
            a.severity,
            a.title,
            a.description,
            a.recommendation,
            a.triggered_value,
            a.threshold_value,
            a.is_active,
            f.name AS field_name,
            f.code AS field_code,
            f.area_ha,
            f.enterprise_id,
            e.name AS enterprise_name
        FROM alerts a
        JOIN fields f ON f.id = a.field_id
        JOIN enterprises e ON e.id = f.enterprise_id
        WHERE a.id = :aid
          {tenant_clause}
    """), query_params).fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Алерт не найден",
        )

    if role == "agronomist":
        if current_user.enterprise_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="У пользователя-агронома не указано предприятие",
            )

        if row.enterprise_id != current_user.enterprise_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Агроном может отправлять алерты только для своего предприятия",
            )

    _enforce_cooldown(
        key=f"telegram:send_alert:alert:{alert_id}:user:{current_user.id}",
        seconds=300,
        message="Этот алерт уже недавно отправлялся в Telegram этим пользователем",
    )

    alert = {
        "alert_type": row.alert_type,
        "severity": row.severity,
        "title": row.title,
        "description": row.description,
        "recommendation": row.recommendation,
        "triggered_value": row.triggered_value,
        "threshold_value": row.threshold_value,
    }
    field = {"name": row.field_name, "code": row.field_code, "area_ha": row.area_ha}
    enterprise = {"name": row.enterprise_name}

    message = format_alert_message(alert, field, enterprise)
    result = send_telegram_message(message)

    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result["error"])
    return {"ok": True, "message": "Уведомление отправлено"}
