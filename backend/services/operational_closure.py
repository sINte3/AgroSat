"""Read model for the retired TASK_209 operational-closure lifecycle.

Results, evidence, corrective actions and verification requests written by the
TASK_209 workflow stay readable here. Every write endpoint is retired with 410
Gone (TASK_225): inspection findings and photos belong to the canonical
inspection workflow (services/anomaly_inspections.py) and remediation to the
TASK_220 agronomy lifecycle (services/closed_loop_agronomy.py).
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

from api.dependencies import ALLOWED_ROLES, TENANT_ROLES


TASHKENT = ZoneInfo("Asia/Tashkent")


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
