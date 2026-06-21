"""
API для управления Telegram уведомлениями.

Эндпоинты:
    POST /api/telegram/test       — отправить тестовое сообщение
    POST /api/telegram/send-alert — отправить уведомление по конкретному алерту
    GET  /api/telegram/status     — проверить настроен ли Telegram
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from config import settings
from services.telegram import send_telegram_message, format_alert_message

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.get("/status")
def telegram_status():
    """Проверить настроен ли Telegram."""
    configured = bool(settings.telegram_bot_token and settings.telegram_chat_id)
    return {
        "configured": configured,
        "enabled": settings.telegram_notifications_enabled,
    }


@router.post("/test")
def send_test_notification():
    """Отправить тестовое сообщение в Telegram."""
    result = send_telegram_message(
        "✅ <b>Тест AgroSat</b>\n\n"
        "Если вы видите это сообщение — Telegram уведомления настроены правильно!\n\n"
        "🛰 AgroSat — Мониторинг полей"
    )
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result["error"])
    return {"ok": True, "message": "Тестовое сообщение отправлено"}


@router.post("/send-alert")
def send_alert_notification(payload: dict, db: Session = Depends(get_db)):
    """
    Отправить Telegram уведомление по конкретному алерту.

    Payload: {alert_id: int}
    """
    alert_id = payload.get("alert_id")
    if not alert_id:
        raise HTTPException(status_code=400, detail="alert_id required")

    # Загрузить алерт + поле + предприятие одним запросом
    row = db.execute(text("""
        SELECT
            a.alert_type, a.severity, a.title, a.description,
            a.recommendation, a.triggered_value, a.threshold_value,
            f.name as field_name, f.code as field_code, f.area_ha,
            e.name as enterprise_name
        FROM alerts a
        JOIN fields f ON f.id = a.field_id
        JOIN enterprises e ON e.id = f.enterprise_id
        WHERE a.id = :aid
    """), {"aid": alert_id}).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")

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
