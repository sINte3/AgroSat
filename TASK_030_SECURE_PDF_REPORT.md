# TASK_030: Secure PDF Reports with JWT & Multi-Tenant Access Control

## Context
`GET /api/reports/enterprise/{enterprise_id}/pdf` in `backend/api/reports.py` currently allows anonymous users to download sensitive enterprise PDF reports.

This task secures the PDF endpoint with JWT authentication and strict multi-tenant RBAC. The existing SQL report generation logic must remain explicit SQL / JOIN based. Do not introduce SQLAlchemy lazy loading.

## Critical Safety Rules
1. Do not replace the whole file.
2. Read `backend/api/reports.py` first.
3. Patch imports separately from the function body.
4. Do not log JWT tokens, Authorization headers, passwords, JWT payloads, or full `current_user` objects.
5. Do not use SQLAlchemy relationship lazy loading.
6. Do not access `current_user.enterprise`, `enterprise.fields`, `field.alerts`, or `field.ndvi_records`.
7. Validate and authorize access before reading report data and before generating the PDF.
8. Keep existing PDF generation, StreamingResponse, filename handling, and SQL report queries intact unless a minimal change is required for security.

## File to Modify
* `backend/api/reports.py`

No database migration is required.

---

## Part 1 — Update Imports

Open `backend/api/reports.py`.

Current imports include:
```python
from fastapi import APIRouter, Depends, HTTPException
```

Change it to:
```python
from fastapi import APIRouter, Depends, HTTPException, status
```

Add these imports near the existing imports:
```python
from models.monitoring import User
from api.auth import get_current_active_user
```

Do not duplicate existing imports.

---

## Part 2 — Secure the PDF Endpoint Signature

Find the existing endpoint:
```python
@router.get("/enterprise/{enterprise_id}/pdf")
def download_enterprise_pdf(enterprise_id: int, db: Session = Depends(get_db)):
    """Сгенерировать и вернуть PDF отчёт по предприятию."""
```

Change only the function signature and docstring to:
```python
@router.get("/enterprise/{enterprise_id}/pdf")
def download_enterprise_pdf(
    enterprise_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Сгенерировать и вернуть PDF отчёт по предприятию с проверкой прав доступа."""
```

---

## Part 3 — Add Validation and RBAC Before Existing SQL Queries

Immediately after the docstring, before the existing `ent_row = db.execute(...)` query, insert:

```python
    if enterprise_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="enterprise_id must be positive",
        )

    role = str(current_user.role or "").lower()

    if role not in {"admin", "manager", "agronomist", "viewer"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для скачивания PDF-отчёта",
        )

    if role in {"agronomist", "viewer"}:
        if current_user.enterprise_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="У пользователя не указано предприятие",
            )

        if current_user.enterprise_id != enterprise_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Вы можете скачивать отчёты только для своего предприятия",
            )
```

---

## Part 4 — Preserve Existing Enterprise Existence Check
Keep the existing query intact and ensure it executes only AFTER the RBAC check has successfully passed:
```python
    ent_row = db.execute(text("""
        SELECT id, name, code, region FROM enterprises WHERE id = :eid
    """), {"eid": enterprise_id}).fetchone()
    if not ent_row:
        raise HTTPException(status_code=404, detail="Enterprise not found")
```

---

## Part 5 — Preserve Existing Explicit SQL Report Queries
Ensure that the existing SQL-based data extraction logic remains completely intact and is not replaced by ORM relationships. No lazy loading is allowed.

---

## Part 6 — Do Not Change PDF Generation and Response Logic
Keep the existing ReportLab rendering, `StreamingResponse` transmission, and URL-encoding logic completely untouched.

---

## Verification Criteria
- **Anonymous Download Blocked**: Requesting the PDF without a Bearer token returns `401 Unauthorized`.
- **Invalid Enterprise ID Blocked**: Requesting an ID of `<= 0` returns `400 Bad Request`.
- **Unknown Role Blocked**: Any role not in `admin, manager, agronomist, viewer` returns `403 Forbidden`.
- **Agronomist Cross-Tenant Download Blocked**: An agronomist registered to Enterprise A attempting to download Enterprise B's PDF report receives `403 Forbidden` (must fail before checking if Enterprise B exists!).
- **Viewer Cross-Tenant Download Blocked**: Same as above for viewers.
- **Agronomist Same-Tenant Download Allowed**: An agronomist registered to Enterprise A successfully downloads Enterprise A's report (returns `200 OK`).
- **Admin and Manager Master Override**: Admins and Managers successfully download any PDF report.
- **No Token Leakage**: Backend logs do not contain sensitive tokens, keys, passwords, or full JWT payloads.
```