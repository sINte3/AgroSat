"""Explicit-SQL weather and irrigation context integrated with inspections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.dependencies import ALLOWED_ROLES, TENANT_ROLES


MAX_EVENT_AGE_DAYS = 10 * 366
MAX_FUTURE_SKEW = timedelta(minutes=5)
CAUSALITY_LIMITATION = (
    "Weather, irrigation, and satellite context do not confirm an agronomic "
    "cause without inspection evidence."
)
EVENT_SELECT = """
SELECT ev.id, ev.enterprise_id, ev.field_id, ev.inspection_id,
 ev.recorded_by_id, recorder.full_name AS recorded_by_name,
 ev.event_type, ev.occurred_at, ev.method_code, ev.water_amount_mm,
 ev.evidence_source, ev.note, ev.version, ev.created_at, ev.updated_at
FROM irrigation_events ev
JOIN users recorder ON recorder.id = ev.recorded_by_id
"""


def _one(result):
    if hasattr(result, "mappings"):
        return result.mappings().first()
    row = result.fetchone()
    return row._mapping if row is not None and hasattr(row, "_mapping") else row


def _all(result):
    if hasattr(result, "mappings"):
        return list(result.mappings().all())
    return [
        row._mapping if hasattr(row, "_mapping") else row
        for row in result.fetchall()
    ]


def _actor(user, *, write=False):
    raw_role = getattr(user.role, "value", user.role)
    role = str(raw_role or "").strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(403, "Unknown role")
    if role in TENANT_ROLES and user.enterprise_id is None:
        raise HTTPException(403, "User has no enterprise_id")
    if write and role == "viewer":
        raise HTTPException(403, "Viewer is read-only")
    return {
        "role": role,
        "user_id": int(user.id),
        "enterprise_id": (
            int(user.enterprise_id)
            if user.enterprise_id is not None
            else None
        ),
    }


def irrigation_context_cache_key(
    enterprise_id: int,
    field_id: int,
    limit: int,
) -> str:
    return (
        f"irrigation-context:v1:enterprise:{int(enterprise_id)}:"
        f"field:{int(field_id)}:limit:{int(limit)}"
    )


def irrigation_context_cache_pattern(
    enterprise_id: int,
    field_id: int,
) -> str:
    return (
        f"irrigation-context:v1:enterprise:{int(enterprise_id)}:"
        f"field:{int(field_id)}:*"
    )


def event_fingerprint(user_id: int, field_id: int, payload) -> str:
    value = payload.model_dump(mode="json")
    value["field_id"] = int(field_id)
    value["recorded_by_id"] = int(user_id)
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event(row):
    return {
        "id": row["id"],
        "enterprise_id": row["enterprise_id"],
        "field_id": row["field_id"],
        "inspection_id": row["inspection_id"],
        "recorded_by": {
            "id": row["recorded_by_id"],
            "display_name": row["recorded_by_name"],
        },
        "event_type": row["event_type"],
        "occurred_at": row["occurred_at"],
        "method_code": row["method_code"],
        "water_amount_mm": row["water_amount_mm"],
        "evidence_source": row["evidence_source"],
        "note": row["note"],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _event_by_id(db, enterprise_id, event_id):
    row = _one(
        db.execute(
            text(
                EVENT_SELECT
                + " WHERE ev.id=:event_id AND ev.enterprise_id=:enterprise_id"
            ),
            {
                "event_id": event_id,
                "enterprise_id": enterprise_id,
            },
        )
    )
    if not row:
        raise HTTPException(404, "Irrigation event not found")
    return _event(row)


def _event_by_key(db, enterprise_id, key):
    return _one(
        db.execute(
            text(
                "SELECT id, request_fingerprint FROM irrigation_events "
                "WHERE enterprise_id=:enterprise_id "
                "AND client_request_id=:client_request_id"
            ),
            {
                "enterprise_id": enterprise_id,
                "client_request_id": key,
            },
        )
    )


def _validate_occurrence(value: datetime, now: datetime):
    observed = value.astimezone(timezone.utc)
    current = now.astimezone(timezone.utc)
    if observed > current + MAX_FUTURE_SKEW:
        raise HTTPException(422, "occurred_at is too far in the future")
    if observed < current - timedelta(days=MAX_EVENT_AGE_DAYS):
        raise HTTPException(422, "occurred_at is outside the supported range")
    return observed


def _validate_inspection_link(
    db,
    enterprise_id: int,
    field_id: int,
    inspection_id: int | None,
):
    if inspection_id is None:
        return
    row = _one(
        db.execute(
            text(
                "SELECT id FROM field_inspections "
                "WHERE id=:inspection_id AND field_id=:field_id "
                "AND enterprise_id=:enterprise_id"
            ),
            {
                "inspection_id": inspection_id,
                "field_id": field_id,
                "enterprise_id": enterprise_id,
            },
        )
    )
    if not row:
        raise HTTPException(404, "Inspection not found")


def create_event(
    db,
    user,
    field,
    payload,
    key: str,
    *,
    now: datetime | None = None,
):
    actor = _actor(user, write=True)
    enterprise_id = int(field.enterprise_id)
    if (
        actor["role"] in TENANT_ROLES
        and actor["enterprise_id"] != enterprise_id
    ):
        raise HTTPException(404, "Field not found")
    occurred_at = _validate_occurrence(
        payload.occurred_at,
        now or datetime.now(timezone.utc),
    )
    fingerprint = event_fingerprint(
        actor["user_id"],
        int(field.id),
        payload,
    )
    try:
        existing = _event_by_key(db, enterprise_id, key)
        if existing:
            if existing["request_fingerprint"] != fingerprint:
                raise HTTPException(409, "Idempotency key payload conflict")
            return False, _event_by_id(db, enterprise_id, existing["id"])

        _validate_inspection_link(
            db,
            enterprise_id,
            int(field.id),
            payload.inspection_id,
        )
        inserted = _one(
            db.execute(
                text(
                    """
                    INSERT INTO irrigation_events
                    (enterprise_id,field_id,inspection_id,recorded_by_id,
                     client_request_id,request_fingerprint,event_type,
                     occurred_at,method_code,water_amount_mm,evidence_source,note)
                    VALUES
                    (:enterprise_id,:field_id,:inspection_id,:recorded_by_id,
                     :client_request_id,:request_fingerprint,:event_type,
                     :occurred_at,:method_code,:water_amount_mm,
                     :evidence_source,:note)
                    RETURNING id
                    """
                ),
                {
                    "enterprise_id": enterprise_id,
                    "field_id": int(field.id),
                    "inspection_id": payload.inspection_id,
                    "recorded_by_id": actor["user_id"],
                    "client_request_id": key,
                    "request_fingerprint": fingerprint,
                    "event_type": payload.event_type.value,
                    "occurred_at": occurred_at,
                    "method_code": payload.method_code.value,
                    "water_amount_mm": payload.water_amount_mm,
                    "evidence_source": payload.evidence_source.value,
                    "note": payload.note,
                },
            )
        )
        db.commit()
        return True, _event_by_id(db, enterprise_id, inserted["id"])
    except IntegrityError:
        db.rollback()
        existing = _event_by_key(db, enterprise_id, key)
        if existing and existing["request_fingerprint"] == fingerprint:
            return False, _event_by_id(db, enterprise_id, existing["id"])
        if existing:
            raise HTTPException(409, "Idempotency key payload conflict")
        raise HTTPException(409, "Concurrent irrigation event conflict")
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def weather_context(field, loader):
    provider = "open_meteo"
    if field.centroid_lat is None or field.centroid_lon is None:
        return {
            "status": "unavailable",
            "provider": provider,
            "reason": "missing_coordinates",
        }
    try:
        value = loader(
            float(field.centroid_lat),
            float(field.centroid_lon),
        )
    except Exception:
        value = None
    if not isinstance(value, dict):
        return {
            "status": "unavailable",
            "provider": provider,
            "reason": "provider_unavailable",
        }
    return {
        **value,
        "status": "available",
        "provider": value.get("provider", provider),
    }


def field_context(db, user, field, *, limit: int, weather_loader):
    actor = _actor(user)
    enterprise_id = int(field.enterprise_id)
    if (
        actor["role"] in TENANT_ROLES
        and actor["enterprise_id"] != enterprise_id
    ):
        raise HTTPException(404, "Field not found")
    events = _all(
        db.execute(
            text(
                EVENT_SELECT
                + " WHERE ev.enterprise_id=:enterprise_id "
                "AND ev.field_id=:field_id "
                "ORDER BY ev.occurred_at DESC, ev.id DESC LIMIT :limit"
            ),
            {
                "enterprise_id": enterprise_id,
                "field_id": int(field.id),
                "limit": limit,
            },
        )
    )
    active = _one(
        db.execute(
            text(
                "SELECT id, status, source, source_priority, "
                "source_observation_date, source_reason_codes "
                "FROM field_inspections "
                "WHERE enterprise_id=:enterprise_id AND field_id=:field_id "
                "AND status IN ('pending','in_progress') "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {
                "enterprise_id": enterprise_id,
                "field_id": int(field.id),
            },
        )
    )
    return {
        "generated_at": datetime.now(timezone.utc),
        "field": {
            "id": int(field.id),
            "enterprise_id": enterprise_id,
            "name": field.name,
            "irrigation_type": field.irrigation_type,
        },
        "weather": weather_context(field, weather_loader),
        "events": [_event(row) for row in events],
        "event_limit": limit,
        "active_inspection": dict(active) if active else None,
        "supported_reason_codes": [
            "water_stress_suspicion",
            "weather_water_deficit",
            "irrigation_interruption",
            "irrigation_delivery_check",
            "irrigation_equipment_check",
        ],
        "causality_limitation": CAUSALITY_LIMITATION,
    }
