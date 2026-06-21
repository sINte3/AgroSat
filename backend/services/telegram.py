"""
Telegram уведомления о критических алертах.
Использует Telegram Bot API через httpx (без внешних библиотек).
"""

import logging
import httpx
from config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


def send_telegram_message(text: str, parse_mode: str = "HTML") -> dict:
    """
    Отправить сообщение в Telegram чат.

    Returns:
        dict с результатом: {"ok": bool, "error": str | None}
    """
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        logger.warning("Telegram не настроен (отсутствует token или chat_id)")
        return {"ok": False, "error": "Telegram не настроен. Заполните TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env"}

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        resp = httpx.post(url, json=payload, timeout=10)
        data = resp.json()
        if resp.status_code == 200 and data.get("ok"):
            logger.info("Telegram сообщение отправлено успешно")
            return {"ok": True, "error": None}
        else:
            error_desc = data.get("description", f"HTTP {resp.status_code}")
            logger.error(f"Telegram API ошибка: {error_desc}")
            return {"ok": False, "error": error_desc}
    except httpx.TimeoutException:
        logger.error("Telegram send failed: timeout")
        return {"ok": False, "error": "Telegram API timeout"}
    except httpx.HTTPError as e:
        logger.error("Telegram send failed: %s", type(e).__name__)
        return {"ok": False, "error": "Telegram API request failed"}
    except Exception as e:
        logger.error("Telegram send failed: %s", type(e).__name__)
        return {"ok": False, "error": "Telegram send failed"}


def format_alert_message(alert: dict, field: dict, enterprise: dict) -> str:
    """
    Сформировать HTML-сообщение для критического алерта.

    alert: {alert_type, severity, title, description, recommendation, triggered_value, threshold_value}
    field: {name, code, area_ha}
    enterprise: {name}
    """
    severity_emoji = {
        "critical": "🔴",
        "warning": "🟡",
        "info": "🔵",
    }.get(alert.get("severity", "info"), "⚪")

    msg = (
        f"{severity_emoji} <b>{alert.get('title', 'Алерт')}</b>\n\n"
        f"🏢 Предприятие: {enterprise.get('name', '—')}\n"
        f"🌾 Поле: {field.get('name', '—')}"
    )
    if field.get('code'):
        msg += f" (код: {field['code']})"
    if field.get('area_ha'):
        msg += f"\n📐 Площадь: {field['area_ha']:.1f} га"

    msg += f"\n\n📊 {alert.get('description', '')}"

    if alert.get('triggered_value') is not None and alert.get('threshold_value') is not None:
        msg += f"\n\n⚠️ Значение: {alert['triggered_value']} (порог: {alert['threshold_value']})"

    if alert.get('recommendation'):
        msg += f"\n\n💡 <i>{alert['recommendation']}</i>"

    msg += "\n\n🛰 AgroSat — Мониторинг полей"
    return msg
