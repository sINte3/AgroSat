# TASK_029: Persist Swagger Authorization & Secure AI Recommender

## Context
`POST /api/ai/recommend` in `backend/api/ai.py` currently generates Anthropic/Claude agronomic recommendations without authentication. This allows anonymous users to consume paid AI quota and may expose field/alert data across tenants.

This task secures the AI endpoint with the JWT auth dependency created in TASK_028, enforces strict RBAC, prevents cross-enterprise access, and optionally persists Swagger authorization during local development.

## Critical Safety Rules
1. Do not replace the whole file.
2. Read `backend/api/ai.py` and `backend/main.py` first.
3. Patch only the required blocks.
4. Do not log JWT tokens, Authorization headers, passwords, or Anthropic API keys.
5. Do not use SQLAlchemy lazy loading for tenant checks.
6. Use explicit SQL queries / JOINs.
7. Deny by default for any unknown role.
8. Validate `alert_id` and `field_id` before any database or external API work.
9. Perform all authorization checks before calling Open-Meteo or Anthropic.

## Files to Modify
* `backend/main.py`
* `backend/api/ai.py`

No database migration is required.

---

## Part 1 — Update Swagger UI Authorization Persistence

In `backend/main.py`, update the existing `FastAPI(...)` constructor.

Current block:
```python
app = FastAPI(
    title="AgroSat API",
    description="Система интеллектуального мониторинга полей — Бухоро Агрокластер",
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)
```

Change it to:
```python
app = FastAPI(
    title="AgroSat API",
    description="Система интеллектуального мониторинга полей — Бухоро Агрокластер",
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    swagger_ui_parameters={
        "persistAuthorization": settings.environment != "production"
    },
)
```

---

## Part 2 — Secure `POST /api/ai/recommend`

Open `backend/api/ai.py`.

At the top of the file, update imports carefully. Preserve existing imports and add only missing imports.

Required imports:
```python
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from config import settings
from models.monitoring import User
from api.auth import get_current_active_user
```

Do not duplicate imports unnecessarily.

---

## Part 3 — Update Function Signature

Replace the current function signature:
```python
@router.post("/recommend")
def get_ai_recommendation(payload: dict, db: Session = Depends(get_db)):
```

with:
```python
@router.post("/recommend")
def get_ai_recommendation(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
```

---

## Part 4 — Validate Payload Before Any Expensive Work

At the start of `get_ai_recommendation`, before any field, alert, weather, or Anthropic work, normalize and validate IDs:

```python
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
```

Remove or replace the old loose check:
```python
    if not alert_id or not field_id:
        raise HTTPException(status_code=400, detail="alert_id and field_id required")
```

---

## Part 5 — Enforce Strict Role Matrix

Immediately after payload validation, normalize the role and deny unknown roles by default:

```python
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
```

---

## Part 6 — Replace Separate Field and Alert Loading with One Joined Query

Replace the separate field query and alert query with one explicit JOIN query that verifies:
- the field exists;
- the alert exists;
- the alert belongs to the provided field;
- the field enterprise is known;
- crop and enterprise names are loaded without lazy loading.

Replace those old separated db queries with:
```python
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
```

Then enforce tenant boundary for agronomists:
```python
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
```

After this, replace the old variables in `api/ai.py` with `context_row`:
* `field_row.enterprise_name` -> `context_row.enterprise_name`
* `field_row.name` -> `context_row.name`
* `field_row.code` -> `context_row.code`
* `field_row.area_ha` -> `context_row.area_ha`
* `field_row.irrigation_type` -> `context_row.irrigation_type`
* `field_row.crop_name` -> `context_row.crop_name`
* `field_row.centroid_lat` -> `context_row.centroid_lat`
* `field_row.centroid_lon` -> `context_row.centroid_lon`
* `alert_row.alert_type` -> `context_row.alert_type`
* `alert_row.severity` -> `context_row.severity`
* `alert_row.title` -> `context_row.title`
* `alert_row.description` -> `context_row.description`
* `alert_row.recommendation` -> `context_row.recommendation`
* `alert_row.triggered_value` -> `context_row.triggered_value`
* `alert_row.threshold_value` -> `context_row.threshold_value`

The return block at the end must use:
```python
    return {
        "alert_id": alert_id,
        "field_id": field_id,
        "field_name": context_row.name,
        "recommendation": recommendation,
        "model": "claude-sonnet-4-6",
    }
```

---

## Part 7 — Preserve NDVI Query

The existing NDVI query must remain an explicit SQL query:
```python
    ndvi_rows = db.execute(text("""
        SELECT captured_date, mean_ndvi, ndvi_change_pct
        FROM ndvi_records
        WHERE field_id = :fid
        ORDER BY captured_date DESC
        LIMIT 10
    """), {"fid": field_id}).fetchall()
```

Do not use any relationship-based lazy loading.

---

## Part 8 — External API Calls Placement
Ensure that Open-Meteo and Anthropic calls are executed only after all validation and authorization checks are completed successfully.

---

## Verification Criteria
- **Anonymous Request Blocked**: Verify request without token returns `401 Unauthorized`.
- **Viewer Blocked**: Verify `viewer` JWT token returns `403 Forbidden`.
- **Unknown Role Blocked**: Any role not in `admin, manager, agronomist` returns `403 Forbidden`.
- **Agronomist Same Enterprise Allowed**: Verify agronomist with matching `enterprise_id` succeeds.
- **Agronomist Cross-Enterprise Blocked**: Verify agronomist trying to access a field from another enterprise receives `403 Forbidden`.
- **Alert/Field Mismatch Blocked**: Passing a valid `field_id` but an `alert_id` belonging to another field must return `404 Not Found`.
- **Swagger Authorization Persistence**: Refreshing the `/api/docs` page in development environment preserves the green closed padlock authorization state.
```