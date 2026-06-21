# TASK_017 — Telegram notifications for critical alerts

## Skills to load
- `/mnt/skills/user/full-output-enforcement/SKILL.md`

## Goal

Send a Telegram message to the agronomist's chat whenever a NEW critical alert is created.
A "Send test notification" button in the UI lets the user verify the bot works.

---

## How Telegram bots work (context for setup — user will do this part)

The user must first create a bot and get two values:
1. **Bot token** — from @BotFather in Telegram (command `/newbot`)
2. **Chat ID** — the ID of the chat/group/channel where messages go

We'll build the code now and the user fills in the credentials in `.env`.

---

## Step 1: Add env variables to `backend/.env`

Add these lines (user fills in real values later):
```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_NOTIFICATIONS_ENABLED=false
```

---

## Step 2: Update `backend/config.py`

Add these settings to the Settings class (match the existing style — pydantic BaseSettings):
```python
telegram_bot_token: str = ""
telegram_chat_id: str = ""
telegram_notifications_enabled: bool = False
```

---

## Step 3: Create `backend/services/telegram.py`

```python
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
    except Exception as e:
        logger.error(f"Telegram отправка не удалась: {e}")
        return {"ok": False, "error": str(e)}


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
```

---

## Step 4: Create backend endpoint `backend/api/telegram.py`

```python
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
```

---

## Step 5: Register router in `backend/main.py`

Add near the other routers:
```python
from api.telegram import router as telegram_router
app.include_router(telegram_router)
```

---

## Step 6: Frontend — "Send to Telegram" button on alert cards

In `frontend/src/pages/EnterpriseDetailPage.jsx`, add a small Telegram button next to the AI button
on each alert card.

Add state:
```jsx
const [tgSending, setTgSending] = useState({});  // keyed by alert.id
const [tgSent, setTgSent] = useState({});        // keyed by alert.id
```

Add handler:
```jsx
async function handleSendTelegram(alert) {
  setTgSending(prev => ({ ...prev, [alert.id]: true }));
  try {
    await apiClient.post('/api/telegram/send-alert', { alert_id: alert.id });
    setTgSent(prev => ({ ...prev, [alert.id]: true }));
    setTimeout(() => setTgSent(prev => ({ ...prev, [alert.id]: false })), 3000);
  } catch (err) {
    alert('Ошибка отправки в Telegram: ' + (err.response?.data?.detail || err.message));
  } finally {
    setTgSending(prev => ({ ...prev, [alert.id]: false }));
  }
}
```

Add button next to the AI button (in the same row):
```jsx
<button
  onClick={() => handleSendTelegram(alert)}
  disabled={tgSending[alert.id]}
  style={{
    background: tgSent[alert.id] ? '#15803d' : 'transparent',
    border: '1px solid #2d4a2d',
    borderRadius: 6,
    padding: '6px 12px',
    color: tgSent[alert.id] ? '#fff' : '#60a5fa',
    fontSize: 12,
    cursor: tgSending[alert.id] ? 'not-allowed' : 'pointer',
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  }}
>
  {tgSending[alert.id] ? '⏳ Отправка...' : tgSent[alert.id] ? '✓ Отправлено' : '✈️ В Telegram'}
</button>
```

---

## Step 7: Add "Test Telegram" button in enterprise header

Near the "Скачать отчёт" button in EnterpriseDetailPage header, add a test button:
```jsx
<button
  onClick={async () => {
    try {
      await apiClient.post('/api/telegram/test');
      alert('✅ Тестовое сообщение отправлено в Telegram');
    } catch (err) {
      alert('❌ ' + (err.response?.data?.detail || 'Telegram не настроен'));
    }
  }}
  style={{
    background: '#1a2818',
    border: '1px solid #2d4a2d',
    borderRadius: 8,
    padding: '8px 14px',
    color: '#60a5fa',
    cursor: 'pointer',
    fontSize: 12,
    whiteSpace: 'nowrap',
  }}
>
  ✈️ Тест Telegram
</button>
```

---

## Checklist
- [ ] `.env` — TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_NOTIFICATIONS_ENABLED added
- [ ] `config.py` — telegram settings added
- [ ] `backend/services/telegram.py` created
- [ ] `backend/api/telegram.py` created
- [ ] Router registered in `main.py`
- [ ] Frontend: "✈️ В Telegram" button on each alert card
- [ ] Frontend: "✈️ Тест Telegram" button in header
- [ ] tgSending / tgSent state added

## How user gets credentials (provide this to user after task done)
1. Open Telegram, search @BotFather
2. Send `/newbot`, follow prompts, copy the bot token
3. Start a chat with the new bot (send it any message)
4. To get chat_id: open `https://api.telegram.org/bot<TOKEN>/getUpdates` in browser, find "chat":{"id": ...}
5. Put both values in `.env`, restart backend, click "✈️ Тест Telegram"

## Important
- No external Telegram library — use httpx (already installed)
- All UI text in Russian
- Dark theme preserved
- Telegram buttons use blue accent (#60a5fa) to distinguish from green AI buttons
