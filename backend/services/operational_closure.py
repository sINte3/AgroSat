"""Explicit-SQL operational closure workflow with tenant and conflict safety."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.dependencies import ALLOWED_ROLES, TENANT_ROLES
from services.operational_verification import (
    ALGORITHM_VERSION,
    LIMITATION,
    Observation,
    rounded,
    resolve_direction,
)


TASHKENT = ZoneInfo("Asia/Tashkent")
ACTIVE_ACTION_STATUSES = ("open", "in_progress", "blocked")


@dataclass(frozen=True, slots=True)
class ActorScope:
    role: str
    user_id: int
    enterprise_id: int | None


def _actor(user, *, write=False, management=False) -> ActorScope:
    raw_role = getattr(user.role, "value", user.role)
    role = str(raw_role or "").strip().lower()
    actor = ActorScope(role, user.id, user.enterprise_id)
    if role not in ALLOWED_ROLES:
        raise HTTPException(403, "Unknown role")
    if role in TENANT_ROLES and actor.enterprise_id is None:
        raise HTTPException(403, "User has no enterprise_id")
    if write and role == "viewer":
        raise HTTPException(403, "Viewer is read-only")
    if management and role not in {"admin", "manager"}:
        raise HTTPException(403, "Management role required")
    return actor


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


def _tenant(actor: ActorScope, alias: str) -> tuple[str, dict]:
    if actor.role in TENANT_ROLES:
        return f" AND {alias}.enterprise_id=:eid", {"eid": actor.enterprise_id}
    return "", {}


def _fingerprint(operation: str, actor: ActorScope, object_id: int, payload) -> str:
    document = payload.model_dump(mode="json")
    document.update(
        {
            "operation": operation,
            "actor_id": actor.user_id,
            "object_id": object_id,
        }
    )
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _idempotency_event(
    db,
    actor: ActorScope,
    key: str,
    request_fingerprint: str,
):
    tenant_sql, tenant_params = _tenant(actor, "a")
    row = _one(
        db.execute(
            text(
                "SELECT a.event_type, a.inspection_id, a.action_id, "
                "a.verification_id, a.request_fingerprint, a.event_metadata "
                "FROM operational_audit_events a "
                "WHERE a.actor_id=:actor_id AND a.idempotency_key=:key"
                + tenant_sql
            ),
            {
                "actor_id": actor.user_id,
                "key": key,
                **tenant_params,
            },
        )
    )
    if row and row["request_fingerprint"] != request_fingerprint:
        raise HTTPException(409, "Idempotency key payload conflict")
    return row


def _audit(
    db,
    *,
    actor: ActorScope,
    key: str,
    request_fingerprint: str,
    event_type: str,
    inspection,
    entity_version: int,
    action_id: int | None = None,
    verification_id: int | None = None,
    metadata: dict | None = None,
):
    db.execute(
        text(
            "INSERT INTO operational_audit_events "
            "(enterprise_id,field_id,inspection_id,action_id,verification_id,"
            "actor_id,event_type,entity_version,idempotency_key,"
            "request_fingerprint,event_metadata) "
            "VALUES (:eid,:fid,:inspection_id,:action_id,:verification_id,"
            ":actor_id,:event_type,:entity_version,:key,:fingerprint,"
            "CAST(:metadata AS jsonb))"
        ),
        {
            "eid": inspection["enterprise_id"],
            "fid": inspection["field_id"],
            "inspection_id": inspection["id"],
            "action_id": action_id,
            "verification_id": verification_id,
            "actor_id": actor.user_id,
            "event_type": event_type,
            "entity_version": entity_version,
            "key": key,
            "fingerprint": request_fingerprint,
            "metadata": json.dumps(metadata or {}, sort_keys=True),
        },
    )


def _inspection_lock(db, actor: ActorScope, inspection_id: int):
    tenant_sql, tenant_params = _tenant(actor, "i")
    ownership = (
        " AND i.assigned_to_id=:actor_id"
        if actor.role == "agronomist"
        else ""
    )
    row = _one(
        db.execute(
            text(
                "SELECT i.id, i.field_id, i.enterprise_id, i.assigned_to_id, "
                "i.status, i.version FROM field_inspections i "
                "WHERE i.id=:inspection_id"
                + tenant_sql
                + ownership
                + " FOR UPDATE"
            ),
            {
                "inspection_id": inspection_id,
                "actor_id": actor.user_id,
                **tenant_params,
            },
        )
    )
    if not row:
        raise HTTPException(404, "Inspection not found")
    return row


def _result_item(db, actor: ActorScope, result_id: int):
    tenant_sql, tenant_params = _tenant(actor, "r")
    row = _one(
        db.execute(
            text(
                "SELECT r.id, r.inspection_id, r.field_id, r.enterprise_id, "
                "r.recorded_by_id, r.cause_code, r.cause_details, "
                "r.evidence_note, ST_Y(r.evidence_location) AS latitude, "
                "ST_X(r.evidence_location) AS longitude, r.version, "
                "r.created_at, r.updated_at FROM inspection_results r "
                "WHERE r.id=:result_id" + tenant_sql
            ),
            {"result_id": result_id, **tenant_params},
        )
    )
    if not row:
        raise HTTPException(404, "Inspection result not found")
    return dict(row)


def _evidence_item(db, actor: ActorScope, evidence_id: int):
    tenant_sql, tenant_params = _tenant(actor, "v")
    row = _one(
        db.execute(
            text(
                "SELECT v.id, v.inspection_id, v.result_id, v.field_id, "
                "v.enterprise_id, v.created_by_id, v.evidence_type, v.provider, "
                "v.provider_reference, v.original_filename, v.media_type, "
                "v.byte_size, v.sha256, v.captured_at, "
                "ST_Y(v.location) AS latitude, ST_X(v.location) AS longitude, "
                "v.provider_metadata, v.created_at FROM inspection_evidence v "
                "WHERE v.id=:evidence_id" + tenant_sql
            ),
            {"evidence_id": evidence_id, **tenant_params},
        )
    )
    if not row:
        raise HTTPException(404, "Inspection evidence not found")
    return dict(row)


ACTION_SELECT = """
SELECT a.id, a.inspection_id, a.result_id, a.field_id, a.enterprise_id,
 a.created_by_id, a.owner_id, owner.full_name AS owner_name,
 a.description, a.due_date, a.status,
 (a.status IN ('open','in_progress','blocked') AND a.due_date < :today)
   AS is_overdue,
 a.closure_reason, a.closed_by_id, a.closed_at, a.reopen_reason,
 a.reopened_by_id, a.reopened_at, a.version, a.created_at, a.updated_at,
 verification.id AS latest_verification_id,
 verification.status AS verification_status,
 verification.result AS verification_result,
 verification.confidence AS verification_confidence,
 verification.version AS verification_version
FROM corrective_actions a
JOIN users owner ON owner.id=a.owner_id
LEFT JOIN LATERAL (
  SELECT v.id, v.status, v.result, v.confidence, v.version
  FROM action_verification_requests v
  WHERE v.action_id=a.id
  ORDER BY v.requested_at DESC, v.id DESC
  LIMIT 1
) verification ON true
"""


def _action_item(row):
    return {
        "id": row["id"],
        "inspection_id": row["inspection_id"],
        "result_id": row["result_id"],
        "field_id": row["field_id"],
        "enterprise_id": row["enterprise_id"],
        "created_by_id": row["created_by_id"],
        "owner": {"id": row["owner_id"], "display_name": row["owner_name"]},
        "description": row["description"],
        "due_date": row["due_date"],
        "status": row["status"],
        "is_overdue": row["is_overdue"],
        "closure_reason": row["closure_reason"],
        "closed_by_id": row["closed_by_id"],
        "closed_at": row["closed_at"],
        "reopen_reason": row["reopen_reason"],
        "reopened_by_id": row["reopened_by_id"],
        "reopened_at": row["reopened_at"],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "latest_verification": (
            None
            if row["latest_verification_id"] is None
            else {
                "id": row["latest_verification_id"],
                "status": row["verification_status"],
                "result": row["verification_result"],
                "confidence": row["verification_confidence"],
                "version": row["verification_version"],
            }
        ),
    }


def _reload_action(db, actor: ActorScope, action_id: int):
    tenant_sql, tenant_params = _tenant(actor, "a")
    row = _one(
        db.execute(
            text(ACTION_SELECT + " WHERE a.id=:action_id" + tenant_sql),
            {
                "action_id": action_id,
                "today": datetime.now(TASHKENT).date(),
                **tenant_params,
            },
        )
    )
    if not row:
        raise HTTPException(404, "Corrective action not found")
    return _action_item(row)


def _verification_item(db, actor: ActorScope, verification_id: int):
    tenant_sql, tenant_params = _tenant(actor, "v")
    row = _one(
        db.execute(
            text(
                "SELECT v.* FROM action_verification_requests v "
                "WHERE v.id=:verification_id" + tenant_sql
            ),
            {"verification_id": verification_id, **tenant_params},
        )
    )
    if not row:
        raise HTTPException(404, "Verification request not found")
    item = dict(row)
    item["limitation"] = LIMITATION
    return item


def _replay(db, actor: ActorScope, event):
    metadata = event["event_metadata"] or {}
    if event["event_type"] == "inspection_result_recorded":
        return _result_item(db, actor, int(metadata["result_id"]))
    if event["event_type"] == "evidence_attached":
        return _evidence_item(db, actor, int(metadata["evidence_id"]))
    if event["event_type"].startswith("action_"):
        return _reload_action(db, actor, event["action_id"])
    return _verification_item(db, actor, event["verification_id"])


def _owner(db, owner_id: int, enterprise_id: int):
    row = _one(
        db.execute(
            text(
                "SELECT id FROM users WHERE id=:owner_id "
                "AND enterprise_id=:eid AND is_active=true "
                "AND role IN ('manager','agronomist')"
            ),
            {"owner_id": owner_id, "eid": enterprise_id},
        )
    )
    if not row:
        raise HTTPException(422, "Action owner is not eligible")


def _future_due_date(value):
    if value < datetime.now(TASHKENT).date():
        raise HTTPException(422, "due_date cannot be in the past")


def record_result(db, user, inspection_id, payload, key):
    actor = _actor(user, write=True)
    request_fingerprint = _fingerprint(
        "inspection_result_recorded", actor, inspection_id, payload
    )
    try:
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        inspection = _inspection_lock(db, actor, inspection_id)
        if (
            inspection["version"] != payload.expected_version
            or inspection["status"] != "in_progress"
        ):
            raise HTTPException(409, "Inspection version or state conflict")
        inserted = _one(
            db.execute(
                text(
                    "INSERT INTO inspection_results "
                    "(inspection_id,field_id,enterprise_id,recorded_by_id,"
                    "cause_code,cause_details,evidence_note,evidence_location) "
                    "VALUES (:inspection_id,:fid,:eid,:actor_id,:cause_code,"
                    ":cause_details,:evidence_note,"
                    "CASE WHEN :latitude IS NULL THEN NULL ELSE "
                    "ST_SetSRID(ST_MakePoint(:longitude,:latitude),4326) END) "
                    "RETURNING id"
                ),
                {
                    "inspection_id": inspection_id,
                    "fid": inspection["field_id"],
                    "eid": inspection["enterprise_id"],
                    "actor_id": actor.user_id,
                    "cause_code": payload.cause_code.value,
                    "cause_details": payload.cause_details,
                    "evidence_note": payload.evidence_note,
                    "latitude": payload.latitude,
                    "longitude": payload.longitude,
                },
            )
        )
        summary = (
            payload.cause_details
            or payload.evidence_note
            or payload.cause_code.value
        )
        updated = _one(
            db.execute(
                text(
                    "UPDATE field_inspections SET status='completed', "
                    "completion_summary=:summary, completed_at=now(), "
                    "version=version+1, updated_at=now() "
                    "WHERE id=:inspection_id AND version=:expected_version "
                    "AND status='in_progress' RETURNING version"
                ),
                {
                    "inspection_id": inspection_id,
                    "expected_version": payload.expected_version,
                    "summary": summary,
                },
            )
        )
        if not updated:
            raise HTTPException(409, "Inspection version or state conflict")
        _audit(
            db,
            actor=actor,
            key=key,
            request_fingerprint=request_fingerprint,
            event_type="inspection_result_recorded",
            inspection=inspection,
            entity_version=updated["version"],
            metadata={
                "result_id": inserted["id"],
                "cause_code": payload.cause_code.value,
            },
        )
        db.commit()
        return _result_item(db, actor, inserted["id"])
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        raise HTTPException(409, "Concurrent inspection result conflict")
    except Exception:
        db.rollback()
        raise


def attach_evidence(db, user, inspection_id, payload, key):
    actor = _actor(user, write=True)
    request_fingerprint = _fingerprint(
        "evidence_attached", actor, inspection_id, payload
    )
    try:
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        inspection = _inspection_lock(db, actor, inspection_id)
        if inspection["version"] != payload.expected_version:
            raise HTTPException(409, "Inspection version conflict")
        if inspection["status"] not in {"in_progress", "completed"}:
            raise HTTPException(409, "Inspection state does not accept evidence")
        if payload.result_id is not None:
            result = _one(
                db.execute(
                    text(
                        "SELECT id FROM inspection_results "
                        "WHERE id=:result_id AND inspection_id=:inspection_id "
                        "AND enterprise_id=:eid"
                    ),
                    {
                        "result_id": payload.result_id,
                        "inspection_id": inspection_id,
                        "eid": inspection["enterprise_id"],
                    },
                )
            )
            if not result:
                raise HTTPException(404, "Inspection result not found")
        inserted = _one(
            db.execute(
                text(
                    "INSERT INTO inspection_evidence "
                    "(inspection_id,result_id,field_id,enterprise_id,"
                    "created_by_id,evidence_type,provider,provider_reference,"
                    "original_filename,media_type,byte_size,sha256,captured_at,"
                    "location,provider_metadata) "
                    "VALUES (:inspection_id,:result_id,:fid,:eid,:actor_id,"
                    ":evidence_type,:provider,:provider_reference,"
                    ":original_filename,:media_type,:byte_size,:sha256,"
                    ":captured_at,CASE WHEN :latitude IS NULL THEN NULL ELSE "
                    "ST_SetSRID(ST_MakePoint(:longitude,:latitude),4326) END,"
                    "CAST(:provider_metadata AS jsonb)) RETURNING id"
                ),
                {
                    "inspection_id": inspection_id,
                    "result_id": payload.result_id,
                    "fid": inspection["field_id"],
                    "eid": inspection["enterprise_id"],
                    "actor_id": actor.user_id,
                    "evidence_type": payload.evidence_type.value,
                    "provider": payload.provider,
                    "provider_reference": payload.provider_reference,
                    "original_filename": payload.original_filename,
                    "media_type": payload.media_type,
                    "byte_size": payload.byte_size,
                    "sha256": payload.sha256,
                    "captured_at": payload.captured_at,
                    "latitude": payload.latitude,
                    "longitude": payload.longitude,
                    "provider_metadata": json.dumps(
                        payload.provider_metadata,
                        sort_keys=True,
                    ),
                },
            )
        )
        updated = _one(
            db.execute(
                text(
                    "UPDATE field_inspections SET version=version+1, "
                    "updated_at=now() WHERE id=:inspection_id "
                    "AND version=:expected_version RETURNING version"
                ),
                {
                    "inspection_id": inspection_id,
                    "expected_version": payload.expected_version,
                },
            )
        )
        if not updated:
            raise HTTPException(409, "Inspection version conflict")
        _audit(
            db,
            actor=actor,
            key=key,
            request_fingerprint=request_fingerprint,
            event_type="evidence_attached",
            inspection=inspection,
            entity_version=updated["version"],
            metadata={
                "evidence_id": inserted["id"],
                "evidence_type": payload.evidence_type.value,
            },
        )
        db.commit()
        return _evidence_item(db, actor, inserted["id"])
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        raise HTTPException(409, "Concurrent evidence conflict")
    except Exception:
        db.rollback()
        raise


def create_action(db, user, inspection_id, payload, key):
    actor = _actor(user, write=True)
    request_fingerprint = _fingerprint("action_created", actor, inspection_id, payload)
    try:
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        _future_due_date(payload.due_date)
        inspection = _inspection_lock(db, actor, inspection_id)
        if (
            inspection["version"] != payload.expected_inspection_version
            or inspection["status"] != "completed"
        ):
            raise HTTPException(409, "Inspection version or state conflict")
        if actor.role == "agronomist" and payload.owner_id != actor.user_id:
            raise HTTPException(403, "Agronomists may own only their own action")
        result = _one(
            db.execute(
                text(
                    "SELECT id FROM inspection_results "
                    "WHERE id=:result_id AND inspection_id=:inspection_id "
                    "AND enterprise_id=:eid"
                ),
                {
                    "result_id": payload.result_id,
                    "inspection_id": inspection_id,
                    "eid": inspection["enterprise_id"],
                },
            )
        )
        if not result:
            raise HTTPException(404, "Inspection result not found")
        _owner(db, payload.owner_id, inspection["enterprise_id"])
        inserted = _one(
            db.execute(
                text(
                    "INSERT INTO corrective_actions "
                    "(inspection_id,result_id,field_id,enterprise_id,"
                    "created_by_id,owner_id,description,due_date) "
                    "VALUES (:inspection_id,:result_id,:fid,:eid,:actor_id,"
                    ":owner_id,:description,:due_date) RETURNING id"
                ),
                {
                    "inspection_id": inspection_id,
                    "result_id": payload.result_id,
                    "fid": inspection["field_id"],
                    "eid": inspection["enterprise_id"],
                    "actor_id": actor.user_id,
                    "owner_id": payload.owner_id,
                    "description": payload.description,
                    "due_date": payload.due_date,
                },
            )
        )
        updated = _one(
            db.execute(
                text(
                    "UPDATE field_inspections SET version=version+1, "
                    "updated_at=now() WHERE id=:inspection_id "
                    "AND version=:expected_version RETURNING version"
                ),
                {
                    "inspection_id": inspection_id,
                    "expected_version": payload.expected_inspection_version,
                },
            )
        )
        if not updated:
            raise HTTPException(409, "Inspection version conflict")
        _audit(
            db,
            actor=actor,
            key=key,
            request_fingerprint=request_fingerprint,
            event_type="action_created",
            inspection=inspection,
            action_id=inserted["id"],
            entity_version=1,
            metadata={"owner_id": payload.owner_id},
        )
        db.commit()
        return _reload_action(db, actor, inserted["id"])
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        raise HTTPException(409, "Concurrent corrective action conflict")
    except Exception:
        db.rollback()
        raise


def _action_lock(db, actor: ActorScope, action_id: int):
    tenant_sql, tenant_params = _tenant(actor, "a")
    ownership = " AND a.owner_id=:actor_id" if actor.role == "agronomist" else ""
    row = _one(
        db.execute(
            text(
                "SELECT a.id, a.inspection_id, a.field_id, a.enterprise_id, "
                "a.owner_id, a.status, a.version, a.closed_at "
                "FROM corrective_actions a WHERE a.id=:action_id"
                + tenant_sql
                + ownership
                + " FOR UPDATE"
            ),
            {
                "action_id": action_id,
                "actor_id": actor.user_id,
                **tenant_params,
            },
        )
    )
    if not row:
        raise HTTPException(404, "Corrective action not found")
    return row


def _inspection_ref(action):
    return {
        "id": action["inspection_id"],
        "field_id": action["field_id"],
        "enterprise_id": action["enterprise_id"],
    }


def update_action(db, user, action_id, payload, key):
    actor = _actor(user, write=True)
    request_fingerprint = _fingerprint("action_updated", actor, action_id, payload)
    try:
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        if payload.due_date is not None:
            _future_due_date(payload.due_date)
        action = _action_lock(db, actor, action_id)
        if (
            action["version"] != payload.expected_version
            or action["status"] == "closed"
        ):
            raise HTTPException(409, "Action version or state conflict")
        if "owner_id" in payload.model_fields_set:
            if actor.role == "agronomist":
                raise HTTPException(403, "Agronomists cannot reassign actions")
            _owner(db, payload.owner_id, action["enterprise_id"])
        fields = []
        params = {
            "action_id": action_id,
            "expected_version": payload.expected_version,
        }
        for name in ("owner_id", "description", "due_date", "status"):
            if name in payload.model_fields_set:
                fields.append(f"{name}=:{name}")
                value = getattr(payload, name)
                params[name] = getattr(value, "value", value)
        updated = _one(
            db.execute(
                text(
                    "UPDATE corrective_actions SET "
                    + ", ".join(fields)
                    + ", version=version+1, updated_at=now() "
                    "WHERE id=:action_id AND version=:expected_version "
                    "AND status IN ('open','in_progress','blocked') "
                    "RETURNING version"
                ),
                params,
            )
        )
        if not updated:
            raise HTTPException(409, "Action version or state conflict")
        _audit(
            db,
            actor=actor,
            key=key,
            request_fingerprint=request_fingerprint,
            event_type="action_updated",
            inspection=_inspection_ref(action),
            action_id=action_id,
            entity_version=updated["version"],
            metadata={"changed_fields": sorted(payload.model_fields_set - {"expected_version"})},
        )
        db.commit()
        return _reload_action(db, actor, action_id)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        raise HTTPException(409, "Concurrent action update conflict")
    except Exception:
        db.rollback()
        raise


def close_action(db, user, action_id, payload, key):
    actor = _actor(user, write=True)
    request_fingerprint = _fingerprint("action_closed", actor, action_id, payload)
    try:
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        action = _action_lock(db, actor, action_id)
        if (
            action["version"] != payload.expected_version
            or action["status"] not in ACTIVE_ACTION_STATUSES
        ):
            raise HTTPException(409, "Action version or state conflict")
        updated = _one(
            db.execute(
                text(
                    "UPDATE corrective_actions SET status='closed', "
                    "closure_reason=:reason, closed_by_id=:actor_id, "
                    "closed_at=now(), version=version+1, updated_at=now() "
                    "WHERE id=:action_id AND version=:expected_version "
                    "AND status IN ('open','in_progress','blocked') "
                    "RETURNING version"
                ),
                {
                    "action_id": action_id,
                    "expected_version": payload.expected_version,
                    "actor_id": actor.user_id,
                    "reason": payload.closure_reason,
                },
            )
        )
        if not updated:
            raise HTTPException(409, "Action version or state conflict")
        _audit(
            db,
            actor=actor,
            key=key,
            request_fingerprint=request_fingerprint,
            event_type="action_closed",
            inspection=_inspection_ref(action),
            action_id=action_id,
            entity_version=updated["version"],
        )
        db.commit()
        return _reload_action(db, actor, action_id)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        raise HTTPException(409, "Concurrent action close conflict")
    except Exception:
        db.rollback()
        raise


def reopen_action(db, user, action_id, payload, key):
    actor = _actor(user, write=True, management=True)
    request_fingerprint = _fingerprint("action_reopened", actor, action_id, payload)
    try:
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        action = _action_lock(db, actor, action_id)
        if (
            action["version"] != payload.expected_version
            or action["status"] != "closed"
        ):
            raise HTTPException(409, "Action version or state conflict")
        updated = _one(
            db.execute(
                text(
                    "UPDATE corrective_actions SET status='open', "
                    "closure_reason=NULL, closed_by_id=NULL, closed_at=NULL, "
                    "reopen_reason=:reason, reopened_by_id=:actor_id, "
                    "reopened_at=now(), version=version+1, updated_at=now() "
                    "WHERE id=:action_id AND version=:expected_version "
                    "AND status='closed' RETURNING version"
                ),
                {
                    "action_id": action_id,
                    "expected_version": payload.expected_version,
                    "actor_id": actor.user_id,
                    "reason": payload.reopen_reason,
                },
            )
        )
        if not updated:
            raise HTTPException(409, "Action version or state conflict")
        _audit(
            db,
            actor=actor,
            key=key,
            request_fingerprint=request_fingerprint,
            event_type="action_reopened",
            inspection=_inspection_ref(action),
            action_id=action_id,
            entity_version=updated["version"],
        )
        db.commit()
        return _reload_action(db, actor, action_id)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        raise HTTPException(409, "Concurrent action reopen conflict")
    except Exception:
        db.rollback()
        raise


def request_verification(db, user, action_id, payload, key):
    actor = _actor(user, write=True, management=True)
    request_fingerprint = _fingerprint(
        "verification_requested", actor, action_id, payload
    )
    try:
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        action = _action_lock(db, actor, action_id)
        if (
            action["version"] != payload.expected_action_version
            or action["status"] != "closed"
            or action["closed_at"] is None
        ):
            raise HTTPException(409, "Action version or state conflict")
        reference_date = action["closed_at"].astimezone(TASHKENT).date()
        inserted = _one(
            db.execute(
                text(
                    "INSERT INTO action_verification_requests "
                    "(action_id,field_id,enterprise_id,requested_by_id,"
                    "index_code,reference_date,minimum_separation_days) "
                    "VALUES (:action_id,:fid,:eid,:actor_id,:index_code,"
                    ":reference_date,:minimum_days) RETURNING id"
                ),
                {
                    "action_id": action_id,
                    "fid": action["field_id"],
                    "eid": action["enterprise_id"],
                    "actor_id": actor.user_id,
                    "index_code": payload.index_code.value,
                    "reference_date": reference_date,
                    "minimum_days": payload.minimum_separation_days,
                },
            )
        )
        updated = _one(
            db.execute(
                text(
                    "UPDATE corrective_actions SET version=version+1, "
                    "updated_at=now() WHERE id=:action_id "
                    "AND version=:expected_version AND status='closed' "
                    "RETURNING version"
                ),
                {
                    "action_id": action_id,
                    "expected_version": payload.expected_action_version,
                },
            )
        )
        if not updated:
            raise HTTPException(409, "Action version or state conflict")
        _audit(
            db,
            actor=actor,
            key=key,
            request_fingerprint=request_fingerprint,
            event_type="verification_requested",
            inspection=_inspection_ref(action),
            action_id=action_id,
            verification_id=inserted["id"],
            entity_version=1,
            metadata={"index_code": payload.index_code.value},
        )
        db.commit()
        return _verification_item(db, actor, inserted["id"])
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        raise HTTPException(409, "Awaiting verification already exists")
    except Exception:
        db.rollback()
        raise


def _observation_from_row(row, *, field_id: int, index_code: str, source: str):
    if not row:
        return None
    return Observation(
        record_id=row["id"],
        source=source,
        field_id=field_id,
        index_code=index_code,
        observed_at=row["captured_date"],
        value=row["value"],
        valid_pixels_pct=row["valid_pixels_pct"],
        cloud_cover_pct=row["cloud_cover_pct"],
        satellite=row["satellite"],
    )


def _eligible_observations(db, verification):
    code = verification["index_code"]
    field_id = verification["field_id"]
    reference_date = verification["reference_date"]
    minimum_date = reference_date + timedelta(
        days=verification["minimum_separation_days"]
    )
    common_quality = (
        "valid_pixels_pct IS NOT NULL AND valid_pixels_pct >= 50 "
        "AND cloud_cover_pct IS NOT NULL AND cloud_cover_pct <= 30 "
    )
    if code == "ndvi":
        reference_sql = (
            "SELECT id,captured_date,mean_ndvi AS value,valid_pixels_pct,"
            "cloud_cover_pct,satellite FROM ndvi_records "
            "WHERE field_id=:field_id AND captured_date<=:reference_date "
            "AND mean_ndvi IS NOT NULL AND "
            + common_quality
            + "ORDER BY captured_date DESC,id DESC LIMIT 1"
        )
        candidate_sql = (
            "SELECT id,captured_date,mean_ndvi AS value,valid_pixels_pct,"
            "cloud_cover_pct,satellite FROM ndvi_records "
            "WHERE field_id=:field_id AND captured_date>:reference_date "
            "AND captured_date>=:minimum_date AND mean_ndvi IS NOT NULL AND "
            + common_quality
            + "ORDER BY captured_date ASC,id ASC LIMIT 1"
        )
        params = {
            "field_id": field_id,
            "reference_date": reference_date,
            "minimum_date": minimum_date,
        }
        source = "ndvi_records"
    else:
        reference_sql = (
            "SELECT id,captured_date,mean_value AS value,valid_pixels_pct,"
            "cloud_cover_pct,satellite FROM satellite_index_records "
            "WHERE field_id=:field_id AND index_code=:index_code "
            "AND captured_date<=:reference_date AND mean_value IS NOT NULL AND "
            + common_quality
            + "ORDER BY captured_date DESC,id DESC LIMIT 1"
        )
        candidate_sql = (
            "SELECT id,captured_date,mean_value AS value,valid_pixels_pct,"
            "cloud_cover_pct,satellite FROM satellite_index_records "
            "WHERE field_id=:field_id AND index_code=:index_code "
            "AND captured_date>:reference_date "
            "AND captured_date>=:minimum_date AND mean_value IS NOT NULL AND "
            + common_quality
            + "ORDER BY captured_date ASC,id ASC LIMIT 1"
        )
        params = {
            "field_id": field_id,
            "index_code": code,
            "reference_date": reference_date,
            "minimum_date": minimum_date,
        }
        source = "satellite_index_records"
    reference_row = _one(db.execute(text(reference_sql), params))
    candidate_params = dict(params)
    if reference_row and reference_row["captured_date"] > reference_date:
        candidate_params["reference_date"] = reference_row["captured_date"]
    candidate_row = _one(db.execute(text(candidate_sql), candidate_params))
    return (
        _observation_from_row(
            reference_row,
            field_id=field_id,
            index_code=code,
            source=source,
        ),
        _observation_from_row(
            candidate_row,
            field_id=field_id,
            index_code=code,
            source=source,
        ),
    )


def resolve_verification(db, user, verification_id, payload, key):
    actor = _actor(user, write=True, management=True)
    request_fingerprint = _fingerprint(
        "verification_resolved", actor, verification_id, payload
    )
    try:
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        tenant_sql, tenant_params = _tenant(actor, "v")
        verification = _one(
            db.execute(
                text(
                    "SELECT v.id,v.action_id,v.field_id,v.enterprise_id,"
                    "v.index_code,v.reference_date,v.minimum_separation_days,"
                    "v.status,v.version,a.inspection_id "
                    "FROM action_verification_requests v "
                    "JOIN corrective_actions a ON a.id=v.action_id "
                    "WHERE v.id=:verification_id"
                    + tenant_sql
                    + " FOR UPDATE"
                ),
                {"verification_id": verification_id, **tenant_params},
            )
        )
        if not verification:
            raise HTTPException(404, "Verification request not found")
        if (
            verification["version"] != payload.expected_version
            or verification["status"] != "awaiting_observation"
        ):
            raise HTTPException(409, "Verification version or state conflict")
        reference, candidate = _eligible_observations(db, verification)
        outcome = resolve_direction(
            reference,
            candidate,
            field_id=verification["field_id"],
            index_code=verification["index_code"],
            reference_date=verification["reference_date"],
            minimum_separation_days=verification["minimum_separation_days"],
        )
        is_ndvi = verification["index_code"] == "ndvi"
        persisted_candidate = (
            candidate
            if reference is not None
            and outcome["result"] != "insufficient_data"
            else None
        )
        provenance = {
            "algorithm_version": ALGORITHM_VERSION,
            "reference_source": reference.source if reference else None,
            "reference_record_id": reference.record_id if reference else None,
            "observation_source": (
                persisted_candidate.source if persisted_candidate else None
            ),
            "observation_record_id": (
                persisted_candidate.record_id if persisted_candidate else None
            ),
            "limitation": LIMITATION,
        }
        params = {
            "verification_id": verification_id,
            "expected_version": payload.expected_version,
            "result": outcome["result"],
            "confidence": outcome["confidence"],
            "notes": payload.notes,
            "reference_ndvi": reference.record_id if reference and is_ndvi else None,
            "reference_satellite": (
                reference.record_id if reference and not is_ndvi else None
            ),
            "observation_ndvi": (
                persisted_candidate.record_id
                if persisted_candidate and is_ndvi
                else None
            ),
            "observation_satellite": (
                persisted_candidate.record_id
                if persisted_candidate and not is_ndvi
                else None
            ),
            "reference_value": (
                outcome.get("reference_value")
                if reference is None
                else float(rounded(reference.value))
            ),
            "observation_value": (
                outcome.get("observation_value")
                if persisted_candidate
                else None
            ),
            "delta_value": outcome.get("delta_value"),
            "reference_observed_at": reference.observed_at if reference else None,
            "observation_observed_at": (
                persisted_candidate.observed_at if persisted_candidate else None
            ),
            "reference_valid": reference.valid_pixels_pct if reference else None,
            "reference_cloud": reference.cloud_cover_pct if reference else None,
            "reference_satellite_name": reference.satellite if reference else None,
            "observation_valid": (
                persisted_candidate.valid_pixels_pct if persisted_candidate else None
            ),
            "observation_cloud": (
                persisted_candidate.cloud_cover_pct if persisted_candidate else None
            ),
            "observation_satellite_name": (
                persisted_candidate.satellite if persisted_candidate else None
            ),
            "provenance": json.dumps(provenance, sort_keys=True),
        }
        updated = _one(
            db.execute(
                text(
                    "UPDATE action_verification_requests SET status='resolved',"
                    "reference_ndvi_record_id=:reference_ndvi,"
                    "reference_satellite_record_id=:reference_satellite,"
                    "observation_ndvi_record_id=:observation_ndvi,"
                    "observation_satellite_record_id=:observation_satellite,"
                    "reference_value=:reference_value,"
                    "observation_value=:observation_value,"
                    "delta_value=:delta_value,"
                    "reference_observed_at=:reference_observed_at,"
                    "observation_observed_at=:observation_observed_at,"
                    "reference_valid_pixels_pct=:reference_valid,"
                    "reference_cloud_cover_pct=:reference_cloud,"
                    "reference_satellite=:reference_satellite_name,"
                    "observation_valid_pixels_pct=:observation_valid,"
                    "observation_cloud_cover_pct=:observation_cloud,"
                    "observation_satellite=:observation_satellite_name,"
                    "result=:result,confidence=:confidence,notes=:notes,"
                    "verification_provenance=CAST(:provenance AS jsonb),"
                    "resolved_at=now(),version=version+1 "
                    "WHERE id=:verification_id AND version=:expected_version "
                    "AND status='awaiting_observation' RETURNING version"
                ),
                params,
            )
        )
        if not updated:
            raise HTTPException(409, "Verification version or state conflict")
        inspection = {
            "id": verification["inspection_id"],
            "field_id": verification["field_id"],
            "enterprise_id": verification["enterprise_id"],
        }
        _audit(
            db,
            actor=actor,
            key=key,
            request_fingerprint=request_fingerprint,
            event_type="verification_resolved",
            inspection=inspection,
            action_id=verification["action_id"],
            verification_id=verification_id,
            entity_version=updated["version"],
            metadata={
                "result": outcome["result"],
                "confidence": outcome["confidence"],
                "algorithm_version": ALGORITHM_VERSION,
            },
        )
        db.commit()
        return _verification_item(db, actor, verification_id)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        replay = _idempotency_event(db, actor, key, request_fingerprint)
        if replay:
            return _replay(db, actor, replay)
        raise HTTPException(409, "Concurrent verification conflict")
    except Exception:
        db.rollback()
        raise


def list_actions(db, user, filters):
    actor = _actor(user)
    try:
        conditions = []
        params = {"today": datetime.now(TASHKENT).date()}
        requested_enterprise = filters.get("enterprise_id")
        if actor.role in TENANT_ROLES:
            if (
                requested_enterprise is not None
                and requested_enterprise != actor.enterprise_id
            ):
                raise HTTPException(403, "Foreign enterprise filter")
            requested_enterprise = actor.enterprise_id
        if requested_enterprise is not None:
            conditions.append("a.enterprise_id=:eid")
            params["eid"] = requested_enterprise
        for name in ("inspection_id", "owner_id", "status"):
            value = filters.get(name)
            if value is not None:
                conditions.append(f"a.{name}=:{name}")
                params[name] = getattr(value, "value", value)
        if filters.get("overdue_only"):
            conditions.append(
                "a.status IN ('open','in_progress','blocked') "
                "AND a.due_date<:today"
            )
        if filters.get("awaiting_verification"):
            conditions.append(
                "EXISTS (SELECT 1 FROM action_verification_requests pending "
                "WHERE pending.action_id=a.id "
                "AND pending.status='awaiting_observation')"
            )
        where = " AND ".join(conditions) or "true"
        summary = _one(
            db.execute(
                text(
                    "SELECT count(*) AS total,"
                    "count(*) FILTER (WHERE a.status IN "
                    "('open','in_progress','blocked') "
                    "AND a.due_date<:today) AS overdue,"
                    "count(*) FILTER (WHERE EXISTS "
                    "(SELECT 1 FROM action_verification_requests pending "
                    "WHERE pending.action_id=a.id "
                    "AND pending.status='awaiting_observation')) "
                    "AS awaiting_verification "
                    "FROM corrective_actions a WHERE " + where
                ),
                params,
            )
        )
        page_params = {
            **params,
            "limit": filters["limit"],
            "offset": filters["offset"],
        }
        rows = _all(
            db.execute(
                text(
                    ACTION_SELECT
                    + " WHERE "
                    + where
                    + " ORDER BY is_overdue DESC,a.due_date ASC,"
                    "a.updated_at DESC,a.id DESC LIMIT :limit OFFSET :offset"
                ),
                page_params,
            )
        )
        return {
            "generated_at": datetime.now(TASHKENT),
            "summary": dict(summary),
            "limit": filters["limit"],
            "offset": filters["offset"],
            "items": [_action_item(row) for row in rows],
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def closure_detail(db, user, inspection_id, *, evidence_limit):
    actor = _actor(user)
    try:
        tenant_sql, tenant_params = _tenant(actor, "i")
        inspection = _one(
            db.execute(
                text(
                    "SELECT i.id,i.field_id,i.enterprise_id,i.assigned_to_id,"
                    "i.status,i.version FROM field_inspections i "
                    "WHERE i.id=:inspection_id" + tenant_sql
                ),
                {"inspection_id": inspection_id, **tenant_params},
            )
        )
        if not inspection:
            raise HTTPException(404, "Inspection not found")
        result_tenant, result_params = _tenant(actor, "r")
        result = _one(
            db.execute(
                text(
                    "SELECT r.id,r.inspection_id,r.field_id,r.enterprise_id,"
                    "r.recorded_by_id,r.cause_code,r.cause_details,"
                    "r.evidence_note,ST_Y(r.evidence_location) AS latitude,"
                    "ST_X(r.evidence_location) AS longitude,r.version,"
                    "r.created_at,r.updated_at FROM inspection_results r "
                    "WHERE r.inspection_id=:inspection_id"
                    + result_tenant
                ),
                {"inspection_id": inspection_id, **result_params},
            )
        )
        evidence_tenant, evidence_params = _tenant(actor, "v")
        evidence_rows = _all(
            db.execute(
                text(
                    "SELECT v.id,v.inspection_id,v.result_id,v.field_id,"
                    "v.enterprise_id,v.created_by_id,v.evidence_type,"
                    "v.provider,v.provider_reference,v.original_filename,"
                    "v.media_type,v.byte_size,v.sha256,v.captured_at,"
                    "ST_Y(v.location) AS latitude,"
                    "ST_X(v.location) AS longitude,v.provider_metadata,"
                    "v.created_at FROM inspection_evidence v "
                    "WHERE v.inspection_id=:inspection_id"
                    + evidence_tenant
                    + " ORDER BY v.created_at DESC,v.id DESC LIMIT :limit"
                ),
                {
                    "inspection_id": inspection_id,
                    "limit": evidence_limit,
                    **evidence_params,
                },
            )
        )
        action_tenant, action_params = _tenant(actor, "a")
        action_rows = _all(
            db.execute(
                text(
                    ACTION_SELECT
                    + " WHERE a.inspection_id=:inspection_id"
                    + action_tenant
                    + " ORDER BY a.created_at DESC,a.id DESC LIMIT 200"
                ),
                {
                    "inspection_id": inspection_id,
                    "today": datetime.now(TASHKENT).date(),
                    **action_params,
                },
            )
        )
        return {
            "inspection": dict(inspection),
            "result": dict(result) if result else None,
            "evidence": [dict(row) for row in evidence_rows],
            "actions": [_action_item(row) for row in action_rows],
            "evidence_limit": evidence_limit,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def timeline(db, user, inspection_id, *, limit, offset):
    actor = _actor(user)
    try:
        tenant_sql, tenant_params = _tenant(actor, "i")
        rows = _all(
            db.execute(
                text(
                    "WITH scoped AS ("
                    " SELECT i.id,i.enterprise_id,i.created_at,i.started_at,"
                    "i.completed_at,i.cancelled_at,i.created_by_id "
                    " FROM field_inspections i WHERE i.id=:inspection_id"
                    + tenant_sql
                    + "), events AS ("
                    " SELECT created_at AS occurred_at,0::bigint AS sort_id,"
                    "'inspection_created'::text AS event_type,"
                    "created_by_id AS actor_id,'{}'::jsonb AS event_metadata "
                    "FROM scoped UNION ALL "
                    "SELECT started_at,1,'inspection_started',NULL,"
                    "'{}'::jsonb FROM scoped WHERE started_at IS NOT NULL "
                    "UNION ALL SELECT completed_at,2,'inspection_completed',"
                    "NULL,'{}'::jsonb FROM scoped WHERE completed_at IS NOT NULL "
                    "UNION ALL SELECT cancelled_at,3,'inspection_cancelled',"
                    "NULL,'{}'::jsonb FROM scoped WHERE cancelled_at IS NOT NULL "
                    "UNION ALL SELECT a.occurred_at,a.id,a.event_type,a.actor_id,"
                    "a.event_metadata FROM operational_audit_events a "
                    "JOIN scoped s ON s.id=a.inspection_id"
                    ") SELECT occurred_at,sort_id AS id,event_type,actor_id,"
                    "event_metadata FROM events ORDER BY occurred_at,id "
                    "LIMIT :limit OFFSET :offset"
                ),
                {
                    "inspection_id": inspection_id,
                    "limit": limit,
                    "offset": offset,
                    **tenant_params,
                },
            )
        )
        if not rows:
            tenant_sql, tenant_params = _tenant(actor, "i")
            exists = _one(
                db.execute(
                    text(
                        "SELECT i.id FROM field_inspections i "
                        "WHERE i.id=:inspection_id" + tenant_sql
                    ),
                    {"inspection_id": inspection_id, **tenant_params},
                )
            )
            if not exists:
                raise HTTPException(404, "Inspection not found")
        return {
            "inspection_id": inspection_id,
            "limit": limit,
            "offset": offset,
            "items": [dict(row) for row in rows],
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
