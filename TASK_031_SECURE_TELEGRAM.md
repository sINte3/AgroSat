# TASK_031: Secure Telegram Notifications & Prevent Cross-Tenant Alert Spam

## Context
`backend/api/telegram.py` currently exposes Telegram endpoints without authentication:
* `GET /api/telegram/status`
* `POST /api/telegram/test`
* `POST /api/telegram/send-alert`

Anonymous users can trigger Telegram messages or broadcast sensitive alert information. This task secures these endpoints with JWT authentication, strict RBAC, tenant checks, cooldown-based spam protection, and Telegram token leakage prevention.

## Critical Safety Rules
1. Do not replace whole files.
2. Read `backend/api/telegram.py` and `backend/services/telegram.py` first.
3. Patch imports separately from function bodies.
4. Do not log Telegram bot token, Telegram chat_id, Authorization header, JWT token, JWT payload, passwords, or full `current_user` objects.
5. Do not return raw `httpx` exception strings to API clients if they may contain Telegram Bot API URLs.
6. Do not use SQLAlchemy lazy loading for tenant checks.
7. Use explicit SQL JOIN for alert -> field -> enterprise lookup.
8. Validate payload before any database lookup or Telegram API call.
9. Enforce RBAC and tenant checks before calling Telegram Bot API.
10. Add cooldown protection to prevent repeated spam.
11. Do not expose Telegram bot token or chat_id in `/status`.

## Files to Modify
* `backend/api/telegram.py`
* `backend/services/telegram.py`

No database migration is required.

---

## Part 1 — Patch Telegram Service to Prevent Token Leakage

Open `backend/services/telegram.py`.

Current code constructs a Telegram URL containing the bot token. That URL must never appear in logs or HTTP responses.

Keep:
```python
url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
```

But update error handling so neither `logger.error(...)` nor returned `error` exposes raw exception strings that may contain the URL.

Replace the broad exception block:
```python
except Exception as e:
    logger.error(f"Telegram отправка не удалась: {e}")
    return {"ok": False, "error": str(e)}
```

with:
```python
except httpx.TimeoutException:
    logger.error("Telegram send failed: timeout")
    return {"ok": False, "error": "Telegram API timeout"}
except httpx.HTTPError as e:
    logger.error("Telegram send failed: %s", type(e).__name__)
    return {"ok": False, "error": "Telegram API request failed"}
except Exception as e:
    logger.error("Telegram send failed: %s", type(e).__name__)
    return {"ok": False, "error": "Telegram send failed"}
```

Keep Telegram API non-200 handling, but never include token/chat_id in logs.

---

## Part 2 — Update Imports in `backend/api/telegram.py`

Open `backend/api/telegram.py`.

Change:
```python
from fastapi import APIRouter, Depends, HTTPException
```

to:
```python
from fastapi import APIRouter, Depends, HTTPException, status
```

Add these imports near the existing imports:
```python
from models.monitoring import User
from api.auth import get_current_active_user
from services.cache import cache_get, cache_set
```

Do not duplicate imports.

---

## Part 3 — Add Small Local Helper Functions

In `backend/api/telegram.py`, after router initialization, add helper functions:

```python
def _normalized_role(user: User) -> str:
    return str(user.role or "").lower()


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
```

---

## Part 4 — Secure `/status`

Find the existing `telegram_status` endpoint and replace it with:

```python
@router.get("/status")
def telegram_status(current_user: User = Depends(get_current_active_user)):
    """Проверить настроен ли Telegram для авторизованного пользователя."""
    configured = bool(settings.telegram_bot_token and settings.telegram_chat_id)
    return {
        "configured": configured,
        "enabled": settings.telegram_notifications_enabled,
    }
```

---

## Part 5 — Secure `/test`

Find:
```python
@router.post("/test")
def send_test_notification():
    """Отправить тестовое сообщение в Telegram."""
```

Change to:
```python
@router.post("/test")
def send_test_notification(current_user: User = Depends(get_current_active_user)):
    """Отправить тестовое сообщение в Telegram. Только admin/manager."""
    role = _normalized_role(current_user)

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
```

---

## Part 6 — Secure `/send-alert`

Find:
```python
@router.post("/send-alert")
def send_alert_notification(payload: dict, db: Session = Depends(get_db)):
```

Change to:
```python
@router.post("/send-alert")
def send_alert_notification(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
```

At the start of the function, before any database lookup or Telegram call, replace the loose payload check with:

```python
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

    role = _normalized_role(current_user)

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

    _enforce_telegram_enabled()
```

---

## Part 7 — Use One Explicit JOIN for Alert Tenant Check

Replace the old separate database row loading logic in `/send-alert` with an explicit JOIN check:

```python
    row = db.execute(text("""
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
    """), {"aid": alert_id}).fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Алерт не найден",
        )
```

Then enforce the tenant boundary check:
```python
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
```

Do not use SQLAlchemy relationships here.

---

## Part 8 — Add Send-Alert Cooldown Before Telegram API Call

After RBAC and tenant checks, but before formatting and sending, add the alert cooldown to prevent spamming:

```python
    _enforce_cooldown(
        key=f"telegram:send_alert:alert:{alert_id}:user:{current_user.id}",
        seconds=300,
        message="Этот алерт уже недавно отправлялся в Telegram этим пользователем",
    )
```

---

## Part 9 — Preserve Message Formatting and Send Logic

Keep the existing message dispatching block as is:
```python
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

## Verification Criteria
- **Anonymous Requests Blocked**: Requesting any secure route without authorization header yields `401 Unauthorized`.
- **Test Cooldown Enforced**: Calling `/test` twice within 60s from the same user yields `429 Too Many Requests`.
- **Alert Cooldown Enforced**: Dispatching the same alert twice within 300s yields `429 Too Many Requests`.
- **Viewer Blocked**: Any request by a viewer yields `403 Forbidden`.
- **Agronomist Multi-Tenant Restriction**: An agronomist attempting to dispatch an alert from another enterprise receives `403 Forbidden` (verified before any Telegram API dispatch is executed).
- **Sanitized Errors**: Telegram service errors do not leak secret URL bot tokens.
```