"""Tenant-safe anomaly inspection workflow using explicit bounded SQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.dependencies import ALLOWED_ROLES, TENANT_ROLES
from config import settings
from services.cache import alert_mutation_cache_patterns, cache_delete_patterns
from services.pixel_ndvi import geometry_hash, resolve_scene_row


MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_PHOTOS = 10
MAX_TOTAL_PHOTO_BYTES = 50 * 1024 * 1024
MEDIA_TYPES = {
    "image/jpeg": (b"\xff\xd8\xff", ".jpg"),
    "image/png": (b"\x89PNG\r\n\x1a\n", ".png"),
    "image/webp": (b"RIFF", ".webp"),
}
STORAGE_KEY = re.compile(r"^[0-9a-f]{2}/[0-9a-f-]{36}\.(?:jpg|png|webp)$")


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: int
    role: str
    enterprise_id: int | None


def _actor(user, *, write: bool = False) -> Actor:
    role = str(getattr(getattr(user, "role", None), "value", getattr(user, "role", ""))).lower()
    actor = Actor(int(user.id), role, getattr(user, "enterprise_id", None))
    if actor.role not in ALLOWED_ROLES:
        raise HTTPException(403, "Unknown role")
    if actor.role in TENANT_ROLES and actor.enterprise_id is None:
        raise HTTPException(403, "User has no enterprise_id")
    if write and actor.role == "viewer":
        raise HTTPException(403, "Viewer is read-only")
    return actor


def _one(result):
    if hasattr(result, "mappings"):
        return result.mappings().first()
    row = result.fetchone()
    return row._mapping if row is not None and hasattr(row, "_mapping") else row


def _all(result):
    if hasattr(result, "mappings"):
        return list(result.mappings().all())
    return [row._mapping if hasattr(row, "_mapping") else row for row in result.fetchall()]


def _fingerprint(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _enum(value):
    return getattr(value, "value", value)


def _write_roles(actor: Actor, allowed: set[str]) -> None:
    if actor.role not in allowed:
        raise HTTPException(403, "Role is not authorized for this operation")


def _tenant_clause(actor: Actor, alias: str = "i") -> tuple[str, dict]:
    if actor.role in TENANT_ROLES:
        return f" AND {alias}.enterprise_id=:actor_enterprise_id", {
            "actor_enterprise_id": actor.enterprise_id,
        }
    return "", {}


def _field(db, actor: Actor, field_id: int):
    tenant, params = _tenant_clause(actor, "f")
    row = _one(db.execute(text(
        "SELECT f.id,f.enterprise_id,f.name,f.area_ha,ST_AsGeoJSON(f.geometry)::json AS geometry "
        "FROM fields f WHERE f.id=:field_id AND f.is_active=true" + tenant
    ), {"field_id": field_id, **params}))
    if not row:
        raise HTTPException(404, "Field not found")
    return row


def _eligible_user(db, enterprise_id: int, user_id: int, *, agronomist_only: bool = False):
    roles = "AND role='agronomist'" if agronomist_only else "AND role IN ('manager','agronomist')"
    row = _one(db.execute(text(
        "SELECT id,full_name,role FROM users WHERE id=:user_id AND enterprise_id=:enterprise_id "
        f"AND is_active=true {roles}"
    ), {"user_id": user_id, "enterprise_id": enterprise_id}))
    if not row:
        raise HTTPException(422, "User is not active and authorized in this enterprise")
    return row


def _location_sql(payload) -> tuple[str, str, dict]:
    params: dict = {}
    if payload.point is not None:
        params.update(longitude=payload.point.longitude, latitude=payload.point.latitude)
        return (
            "ST_SetSRID(ST_MakePoint(:longitude,:latitude),4326)",
            "NULL",
            params,
        )
    if payload.zone is not None:
        params["zone_json"] = json.dumps(payload.zone, separators=(",", ":"))
        return (
            "NULL",
            "ST_SetSRID(ST_GeomFromGeoJSON(:zone_json),4326)",
            params,
        )
    return "NULL", "NULL", params


def _validate_source(db, user, actor: Actor, field, payload) -> None:
    if payload.point is not None:
        covered = _one(db.execute(text(
            "SELECT ST_Covers(f.geometry,ST_SetSRID(ST_MakePoint(:lon,:lat),4326)) AS covered "
            "FROM fields f WHERE f.id=:field_id AND f.enterprise_id=:enterprise_id"
        ), {"lon": payload.point.longitude, "lat": payload.point.latitude,
            "field_id": field["id"], "enterprise_id": field["enterprise_id"]}))
        if not covered or not covered["covered"]:
            raise HTTPException(422, "Location is outside the authorized field")
    if payload.zone is not None:
        zone_json = json.dumps(payload.zone, separators=(",", ":"))
        covered = _one(db.execute(text(
            "SELECT ST_IsValid(z.g) AS valid,NOT ST_IsEmpty(z.g) AS nonempty,"
            "ST_Intersects(f.geometry,z.g) AS intersects,ST_Covers(f.geometry,z.g) AS covered "
            "FROM fields f CROSS JOIN LATERAL "
            "(SELECT ST_SetSRID(ST_GeomFromGeoJSON(:zone),4326) AS g) z "
            "WHERE f.id=:field_id AND f.enterprise_id=:enterprise_id"
        ), {"zone": zone_json, "field_id": field["id"], "enterprise_id": field["enterprise_id"]}))
        if not covered or not all(covered[key] for key in ("valid", "nonempty", "intersects", "covered")):
            raise HTTPException(422, "Zone must be valid and fully inside the authorized field")
    if _enum(payload.source_kind) == "alert":
        alert = _one(db.execute(text(
            "SELECT a.id,a.field_id FROM alerts a JOIN fields f ON f.id=a.field_id "
            "WHERE a.id=:alert_id AND a.field_id=:field_id AND f.enterprise_id=:enterprise_id "
            "AND a.is_active=true"
        ), {"alert_id": payload.source_alert_id, "field_id": field["id"],
            "enterprise_id": field["enterprise_id"]}))
        if not alert:
            raise HTTPException(404, "Alert not found")
    if _enum(payload.source_kind) == "pixel_ndvi":
        scene = resolve_scene_row(db, field["id"], user, payload.item_id)
        if scene.acquisition_time != payload.acquired_at:
            raise HTTPException(409, "Pixel NDVI scene snapshot is stale")
        if geometry_hash(scene.geometry) != payload.geometry_hash:
            raise HTTPException(409, "Field geometry changed; select the sample again")


def _audit(db, actor: Actor, base, event_type: str, version: int, metadata: dict,
           *, action_id: int | None = None, verification_id: int | None = None) -> None:
    safe = {
        key: value for key, value in metadata.items()
        if key in {"from_status", "to_status", "reason", "assignee_id", "photo_id",
                   "action_type", "result", "follow_up_inspection_id"}
    }
    key = f"wf-{uuid.uuid4()}"
    db.execute(text(
        """INSERT INTO operational_audit_events
        (enterprise_id,field_id,inspection_id,action_id,verification_id,actor_id,event_type,
         entity_version,idempotency_key,request_fingerprint,event_metadata)
        VALUES (:enterprise_id,:field_id,:inspection_id,:action_id,:verification_id,:actor_id,
        :event_type,:entity_version,:idempotency_key,:fingerprint,CAST(:metadata AS jsonb))"""
    ), {
        "enterprise_id": base["enterprise_id"], "field_id": base["field_id"],
        "inspection_id": base["id"], "action_id": action_id,
        "verification_id": verification_id, "actor_id": actor.user_id,
        "event_type": event_type, "entity_version": version, "idempotency_key": key,
        "fingerprint": _fingerprint({"event": event_type, "key": key, **safe}),
        "metadata": json.dumps(safe, sort_keys=True, default=str),
    })


INSPECTION_SELECT = """
SELECT i.*,f.name AS field_name,e.name AS enterprise_name,
 creator.full_name AS creator_name,assignee.full_name AS assignee_name,
 reviewer.full_name AS reviewer_name,
 ST_AsGeoJSON(i.source_point)::json AS source_point_geojson,
 ST_AsGeoJSON(i.source_zone)::json AS source_zone_geojson,
 (i.status IN ('new','assigned','in_progress','submitted') AND i.due_at < now()) AS is_overdue
FROM field_inspections i JOIN fields f ON f.id=i.field_id
JOIN enterprises e ON e.id=i.enterprise_id JOIN users creator ON creator.id=i.created_by_id
LEFT JOIN users assignee ON assignee.id=i.assigned_to_id
LEFT JOIN users reviewer ON reviewer.id=i.reviewed_by_id
"""


def _inspection_row(db, actor: Actor, inspection_id: int, *, assigned_only: bool = False):
    tenant, params = _tenant_clause(actor)
    assigned = " AND i.assigned_to_id=:actor_user_id" if assigned_only and actor.role == "agronomist" else ""
    row = _one(db.execute(text(
        INSPECTION_SELECT + " WHERE i.id=:inspection_id" + tenant + assigned
    ), {"inspection_id": inspection_id, "actor_user_id": actor.user_id, **params}))
    if not row:
        raise HTTPException(404, "Inspection not found")
    return row


def _inspection_item(row) -> dict:
    return {
        "id": row["id"], "enterprise_id": row["enterprise_id"],
        "enterprise_name": row["enterprise_name"], "field_id": row["field_id"],
        "field_name": row["field_name"], "created_by_id": row["created_by_id"],
        "creator_name": row["creator_name"], "assigned_to_id": row["assigned_to_id"],
        "assignee_name": row["assignee_name"], "status": row["status"],
        "priority": row["priority"], "due_at": row["due_at"], "is_overdue": bool(row["is_overdue"]),
        "version": row["version"], "source": {
            "kind": row["source_kind"], "alert_id": row["source_alert_id"],
            "provider": row["source_provider"], "item_id": row["source_item_id"],
            "acquired_at": row["source_acquired_at"], "index_name": row["source_index_name"],
            "sampled_value": row["source_sampled_value"],
            "comparison_value": row["source_comparison_value"], "delta": row["source_delta"],
            "geometry_hash": row["source_geometry_hash"], "point": row["source_point_geojson"],
            "zone": row["source_zone_geojson"], "reason": row["source_reason"],
        },
        "review": {"reviewed_by_id": row["reviewed_by_id"], "reviewer_name": row["reviewer_name"],
                   "reviewed_at": row["reviewed_at"], "reason": row["review_reason"]},
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "started_at": row["started_at"], "submitted_at": row["submitted_at"],
        "confirmed_at": row["confirmed_at"], "rejected_at": row["rejected_at"],
        "cancelled_at": row["cancelled_at"], "cancellation_reason": row["cancellation_reason"],
        "follow_up_of_id": row["follow_up_of_id"],
    }


def _invalidate(base) -> None:
    cache_delete_patterns(alert_mutation_cache_patterns(base["enterprise_id"], base["field_id"]))


def create(db, user, payload, idempotency_key: str):
    actor = _actor(user, write=True)
    _write_roles(actor, {"admin", "manager"})
    try:
        existing = _one(db.execute(text(
            "SELECT id,request_fingerprint FROM field_inspections WHERE client_request_id=:key"
        ), {"key": idempotency_key}))
        fingerprint = _fingerprint({"actor": actor.user_id, **payload.model_dump(mode="json")})
        if existing:
            if existing["request_fingerprint"] != fingerprint:
                raise HTTPException(409, "Idempotency key payload conflict")
            return {"created": False, "inspection": _inspection_item(_inspection_row(db, actor, existing["id"]))}
        field = _field(db, actor, payload.field_id)
        if payload.assigned_to_id is not None:
            _eligible_user(db, field["enterprise_id"], payload.assigned_to_id, agronomist_only=True)
        _validate_source(db, user, actor, field, payload)
        point_sql, zone_sql, location_params = _location_sql(payload)
        status = "assigned" if payload.assigned_to_id is not None else "new"
        result = _one(db.execute(text(f"""
            INSERT INTO field_inspections
            (field_id,enterprise_id,created_by_id,updated_by_id,assigned_to_id,client_request_id,
             request_fingerprint,source,source_priority,source_reason_codes,title,instructions,due_date,
             status,source_kind,source_alert_id,source_provider,source_item_id,source_acquired_at,
             source_index_name,source_sampled_value,source_comparison_value,source_delta,
             source_geometry_hash,source_point,source_zone,source_reason,priority,due_at)
            VALUES (:field_id,:enterprise_id,:actor_id,:actor_id,:assignee_id,:key,:fingerprint,
             :source_kind,:priority,'[]'::jsonb,:title,:reason,(:due_at AT TIME ZONE 'Asia/Tashkent')::date,
             :status,:source_kind,:alert_id,:provider,:item_id,:acquired_at,:index_name,:sampled_value,
             :comparison_value,:delta,:geometry_hash,{point_sql},{zone_sql},:reason,:priority,:due_at)
            RETURNING id,field_id,enterprise_id,version,status
        """), {
            "field_id": field["id"], "enterprise_id": field["enterprise_id"],
            "actor_id": actor.user_id, "assignee_id": payload.assigned_to_id,
            "key": idempotency_key, "fingerprint": fingerprint,
            "source_kind": _enum(payload.source_kind), "priority": _enum(payload.priority),
            "title": payload.reason[:255], "reason": payload.reason, "due_at": payload.due_at,
            "status": status, "alert_id": payload.source_alert_id, "provider": payload.provider,
            "item_id": payload.item_id, "acquired_at": payload.acquired_at,
            "index_name": payload.index_name, "sampled_value": payload.sampled_value,
            "comparison_value": payload.comparison_value, "delta": payload.delta,
            "geometry_hash": payload.geometry_hash, **location_params,
        }))
        _audit(db, actor, result, "inspection_created", result["version"], {"to_status": status})
        if payload.assigned_to_id is not None:
            _audit(db, actor, result, "inspection_assigned", result["version"],
                   {"to_status": status, "assignee_id": payload.assigned_to_id})
        db.commit()
        _invalidate(result)
        return {"created": True, "inspection": _inspection_item(_inspection_row(db, actor, result["id"]))}
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Concurrent inspection conflict") from None
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def list_queue(db, user, filters: dict):
    actor = _actor(user)
    conditions: list[str] = []
    params: dict = {"limit": filters["limit"], "offset": filters["offset"]}
    if actor.role in TENANT_ROLES:
        conditions.append("i.enterprise_id=:actor_enterprise_id")
        params["actor_enterprise_id"] = actor.enterprise_id
    if actor.role == "agronomist":
        conditions.append("i.assigned_to_id=:actor_user_id")
        params["actor_user_id"] = actor.user_id
    for key, column in (
        ("enterprise_id", "i.enterprise_id"), ("field_id", "i.field_id"),
        ("assigned_to_id", "i.assigned_to_id"), ("status", "i.status"),
        ("priority", "i.priority"), ("source_kind", "i.source_kind"),
    ):
        value = filters.get(key)
        if value is not None:
            if key == "enterprise_id" and actor.role in TENANT_ROLES and value != actor.enterprise_id:
                raise HTTPException(404, "Enterprise not found")
            conditions.append(f"{column}=:{key}")
            params[key] = _enum(value)
    due_state = filters.get("due_state")
    if due_state == "overdue":
        conditions.append("i.status IN ('new','assigned','in_progress','submitted') AND i.due_at < now()")
    elif due_state == "due":
        conditions.append("i.status IN ('new','assigned','in_progress','submitted') AND i.due_at >= now()")
    search = (filters.get("search") or "").strip()
    if search:
        conditions.append("(f.name ILIKE :search OR i.source_reason ILIKE :search)")
        params["search"] = f"%{search}%"
    where = " AND ".join(conditions) or "true"
    order_by = {
        "priority": "CASE i.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'normal' THEN 3 ELSE 4 END, i.due_at, i.id DESC",
        "due_at": "i.due_at ASC, i.id DESC",
        "created_at": "i.created_at DESC, i.id DESC",
    }[filters.get("sort", "priority")]
    rows = _all(db.execute(text(
        INSPECTION_SELECT + f" WHERE {where} ORDER BY {order_by} LIMIT :limit OFFSET :offset"
    ), params))
    summary = _one(db.execute(text(f"""
        SELECT count(*) AS total,
          count(*) FILTER (WHERE i.status IN ('new','assigned','in_progress','submitted')) AS open,
          count(*) FILTER (WHERE i.status IN ('new','assigned','in_progress','submitted') AND i.due_at < now()) AS overdue,
          count(*) FILTER (WHERE i.status='submitted') AS awaiting_review,
          count(DISTINCT a.id) FILTER (WHERE a.status IN ('planned','in_progress')) AS active_actions,
          count(DISTINCT a.id) FILTER (WHERE a.status='completed') AS verification_due
        FROM field_inspections i JOIN fields f ON f.id=i.field_id
        LEFT JOIN corrective_actions a ON a.inspection_id=i.id
        WHERE {where}
    """), params))
    return {
        "generated_at": datetime.now(timezone.utc),
        "summary": {key: int(summary[key] or 0) for key in
                    ("total", "open", "overdue", "awaiting_review", "active_actions", "verification_due")},
        "limit": filters["limit"], "offset": filters["offset"],
        "items": [_inspection_item(row) for row in rows],
    }


def list_assignees(db, user, enterprise_id: int | None, field_id: int | None):
    actor = _actor(user)
    _write_roles(actor, {"admin", "manager"})
    field = _field(db, actor, field_id) if field_id is not None else None
    target = field["enterprise_id"] if field is not None else (
        actor.enterprise_id if actor.role == "manager" else enterprise_id
    )
    if target is None:
        raise HTTPException(422, "enterprise_id is required")
    if actor.role == "manager" and enterprise_id is not None and enterprise_id != actor.enterprise_id:
        raise HTTPException(404, "Enterprise not found")
    rows = _all(db.execute(text(
        "SELECT id,full_name,email,enterprise_id FROM users WHERE enterprise_id=:enterprise_id "
        "AND role='agronomist' AND is_active=true ORDER BY full_name,id LIMIT 200"
    ), {"enterprise_id": target}))
    return [dict(row) for row in rows]


def detail(db, user, inspection_id: int):
    actor = _actor(user)
    row = _inspection_row(db, actor, inspection_id, assigned_only=True)
    field_geometry = _one(db.execute(text(
        "SELECT ST_AsGeoJSON(geometry)::json AS geometry FROM fields "
        "WHERE id=:field_id AND enterprise_id=:enterprise_id"
    ), {"field_id": row["field_id"], "enterprise_id": row["enterprise_id"]}))
    finding = _one(db.execute(text(
        "SELECT r.*,ST_AsGeoJSON(r.evidence_location)::json AS gps_point FROM inspection_results r "
        "WHERE r.inspection_id=:inspection_id"
    ), {"inspection_id": inspection_id}))
    photos = _all(db.execute(text(
        "SELECT id,original_filename,media_type,byte_size,sha256,captured_at,created_at,version "
        "FROM inspection_evidence WHERE inspection_id=:inspection_id AND evidence_type='photo' "
        "AND deleted_at IS NULL ORDER BY created_at,id"
    ), {"inspection_id": inspection_id}))
    actions = _all(db.execute(text(
        "SELECT a.*,u.full_name AS owner_name FROM corrective_actions a JOIN users u ON u.id=a.owner_id "
        "WHERE a.inspection_id=:inspection_id ORDER BY a.created_at,a.id"
    ), {"inspection_id": inspection_id}))
    timeline = _all(db.execute(text(
        "SELECT ev.id,ev.event_type,ev.entity_version,ev.event_metadata,ev.occurred_at,"
        "ev.actor_id,u.full_name AS actor_name,ev.action_id,ev.verification_id "
        "FROM operational_audit_events ev JOIN users u ON u.id=ev.actor_id "
        "WHERE ev.inspection_id=:inspection_id ORDER BY ev.occurred_at,ev.id"
    ), {"inspection_id": inspection_id}))
    return {**_inspection_item(row), "field_geometry": field_geometry["geometry"],
            "finding": dict(finding) if finding else None,
            "photos": [dict(item) for item in photos], "actions": [dict(item) for item in actions],
            "timeline": [dict(item) for item in timeline]}


def assign(db, user, inspection_id: int, payload):
    actor = _actor(user)
    try:
        base = _inspection_row(db, actor, inspection_id)
        _write_roles(actor, {"admin", "manager"})
        if base["status"] not in {"new", "assigned"}:
            raise HTTPException(409, "Inspection cannot be assigned in its current state")
        if base["assigned_to_id"] is not None and not payload.reason:
            raise HTTPException(422, "Reassignment requires a reason")
        _eligible_user(db, base["enterprise_id"], payload.assigned_to_id, agronomist_only=True)
        result = _one(db.execute(text(
            "UPDATE field_inspections SET assigned_to_id=:assignee,status='assigned',"
            "reassignment_reason=:reason,updated_by_id=:actor,updated_at=now(),version=version+1 "
            "WHERE id=:id AND version=:version AND status IN ('new','assigned') RETURNING id,field_id,enterprise_id,version"
        ), {"assignee": payload.assigned_to_id, "reason": payload.reason, "actor": actor.user_id,
            "id": inspection_id, "version": payload.expected_version}))
        if not result:
            raise HTTPException(409, "Version or state conflict")
        event = "inspection_reassigned" if base["assigned_to_id"] is not None else "inspection_assigned"
        _audit(db, actor, result, event, result["version"], {
            "from_status": base["status"], "to_status": "assigned",
            "assignee_id": payload.assigned_to_id, "reason": payload.reason,
        })
        db.commit(); _invalidate(result)
        return {"inspection": _inspection_item(_inspection_row(db, actor, inspection_id))}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise


def start(db, user, inspection_id: int, payload):
    actor = _actor(user)
    try:
        base = _inspection_row(db, actor, inspection_id, assigned_only=True)
        _write_roles(actor, {"agronomist"})
        result = _one(db.execute(text(
            "UPDATE field_inspections SET status='in_progress',started_at=now(),updated_by_id=:actor,"
            "updated_at=now(),version=version+1 WHERE id=:id AND assigned_to_id=:actor "
            "AND version=:version AND status='assigned' RETURNING id,field_id,enterprise_id,version"
        ), {"actor": actor.user_id, "id": inspection_id, "version": payload.expected_version}))
        if not result:
            raise HTTPException(409, "Version or state conflict")
        _audit(db, actor, result, "inspection_started", result["version"],
               {"from_status": base["status"], "to_status": "in_progress"})
        db.commit(); _invalidate(result)
        return {"inspection": _inspection_item(_inspection_row(db, actor, inspection_id, assigned_only=True))}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise


def save_finding(db, user, inspection_id: int, payload):
    actor = _actor(user)
    try:
        base = _inspection_row(db, actor, inspection_id, assigned_only=True)
        _write_roles(actor, {"agronomist"})
        if base["status"] != "in_progress":
            raise HTTPException(409, "Finding can be saved only while inspection is in progress")
        if payload.gps_point is not None:
            covered = _one(db.execute(text(
                "SELECT ST_Covers(f.geometry,ST_SetSRID(ST_MakePoint(:lon,:lat),4326)) AS covered "
                "FROM fields f WHERE f.id=:field_id AND f.enterprise_id=:enterprise_id"
            ), {"lon": payload.gps_point.longitude, "lat": payload.gps_point.latitude,
                "field_id": base["field_id"], "enterprise_id": base["enterprise_id"]}))
            if not covered or not covered["covered"]:
                raise HTTPException(422, "GPS point is outside the authorized field")
        gps_sql = ("ST_SetSRID(ST_MakePoint(:longitude,:latitude),4326)"
                   if payload.gps_point is not None else "NULL")
        params = {
            "inspection_id": inspection_id, "field_id": base["field_id"],
            "enterprise_id": base["enterprise_id"], "actor": actor.user_id,
            "cause": _enum(payload.cause), "details": payload.other_explanation,
            "observations": payload.observations, "inspected_at": payload.inspected_at,
            "accuracy": payload.gps_accuracy_m, "severity": _enum(payload.severity),
            "area_ha": payload.affected_area_ha, "area_pct": payload.affected_area_pct,
            "recommended": payload.recommended_action, "sync_state": payload.sync_state,
            "longitude": payload.gps_point.longitude if payload.gps_point else None,
            "latitude": payload.gps_point.latitude if payload.gps_point else None,
        }
        db.execute(text(f"""
            INSERT INTO inspection_results
            (inspection_id,field_id,enterprise_id,recorded_by_id,cause_code,cause_details,
             evidence_note,evidence_location,actual_inspected_at,gps_accuracy_m,severity,
             affected_area_ha,affected_area_pct,observations,recommended_action,sync_state)
            VALUES (:inspection_id,:field_id,:enterprise_id,:actor,:cause,:details,:observations,
             {gps_sql},:inspected_at,:accuracy,:severity,:area_ha,:area_pct,:observations,:recommended,:sync_state)
            ON CONFLICT (inspection_id) DO UPDATE SET
             recorded_by_id=EXCLUDED.recorded_by_id,cause_code=EXCLUDED.cause_code,
             cause_details=EXCLUDED.cause_details,evidence_note=EXCLUDED.evidence_note,
             evidence_location=EXCLUDED.evidence_location,actual_inspected_at=EXCLUDED.actual_inspected_at,
             gps_accuracy_m=EXCLUDED.gps_accuracy_m,severity=EXCLUDED.severity,
             affected_area_ha=EXCLUDED.affected_area_ha,affected_area_pct=EXCLUDED.affected_area_pct,
             observations=EXCLUDED.observations,recommended_action=EXCLUDED.recommended_action,
             sync_state=EXCLUDED.sync_state,version=inspection_results.version+1,updated_at=now()
        """), params)
        result = _one(db.execute(text(
            "UPDATE field_inspections SET updated_by_id=:actor,updated_at=now(),version=version+1 "
            "WHERE id=:id AND assigned_to_id=:actor AND version=:version AND status='in_progress' "
            "RETURNING id,field_id,enterprise_id,version"
        ), {"actor": actor.user_id, "id": inspection_id, "version": payload.expected_version}))
        if not result:
            raise HTTPException(409, "Version or state conflict")
        _audit(db, actor, result, "finding_saved", result["version"], {"to_status": "in_progress"})
        db.commit(); _invalidate(result)
        return {"inspection": detail(db, user, inspection_id)}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise


def submit(db, user, inspection_id: int, payload):
    actor = _actor(user)
    try:
        base = _inspection_row(db, actor, inspection_id, assigned_only=True)
        _write_roles(actor, {"agronomist"})
        finding = _one(db.execute(text(
            "SELECT id,observations,recommended_action,severity,actual_inspected_at "
            "FROM inspection_results WHERE inspection_id=:id"
        ), {"id": inspection_id}))
        if not finding or not all(finding[key] is not None for key in
                                  ("observations", "recommended_action", "severity", "actual_inspected_at")):
            raise HTTPException(422, "Complete structured finding is required before submission")
        result = _one(db.execute(text(
            "UPDATE field_inspections SET status='submitted',submitted_at=now(),updated_by_id=:actor,"
            "updated_at=now(),version=version+1 WHERE id=:id AND assigned_to_id=:actor "
            "AND version=:version AND status='in_progress' RETURNING id,field_id,enterprise_id,version"
        ), {"actor": actor.user_id, "id": inspection_id, "version": payload.expected_version}))
        if not result:
            raise HTTPException(409, "Version or state conflict")
        _audit(db, actor, result, "inspection_submitted", result["version"],
               {"from_status": base["status"], "to_status": "submitted"})
        db.commit(); _invalidate(result)
        return {"inspection": _inspection_item(_inspection_row(db, actor, inspection_id))}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise


def review(db, user, inspection_id: int, payload):
    actor = _actor(user)
    try:
        base = _inspection_row(db, actor, inspection_id)
        _write_roles(actor, {"admin", "manager"})
        decision = payload.decision
        timestamp_column = "confirmed_at" if decision == "confirmed" else "rejected_at"
        result = _one(db.execute(text(f"""
            UPDATE field_inspections SET status=:decision,reviewed_by_id=:actor,reviewed_at=now(),
            review_reason=:reason,{timestamp_column}=now(),updated_by_id=:actor,updated_at=now(),
            version=version+1 WHERE id=:id AND version=:version AND status='submitted'
            RETURNING id,field_id,enterprise_id,version
        """), {"decision": decision, "actor": actor.user_id, "reason": payload.reason,
            "id": inspection_id, "version": payload.expected_version}))
        if not result:
            raise HTTPException(409, "Version or state conflict")
        _audit(db, actor, result, f"inspection_{decision}", result["version"],
               {"from_status": base["status"], "to_status": decision, "reason": payload.reason})
        db.commit(); _invalidate(result)
        return {"inspection": _inspection_item(_inspection_row(db, actor, inspection_id))}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise


def cancel(db, user, inspection_id: int, payload):
    actor = _actor(user)
    try:
        base = _inspection_row(db, actor, inspection_id)
        _write_roles(actor, {"admin", "manager"})
        result = _one(db.execute(text(
            "UPDATE field_inspections SET status='cancelled',cancellation_reason=:reason,cancelled_at=now(),"
            "updated_by_id=:actor,updated_at=now(),version=version+1 WHERE id=:id AND version=:version "
            "AND status IN ('new','assigned','in_progress','submitted') "
            "RETURNING id,field_id,enterprise_id,version"
        ), {"reason": payload.reason, "actor": actor.user_id, "id": inspection_id,
            "version": payload.expected_version}))
        if not result:
            raise HTTPException(409, "Version or state conflict")
        _audit(db, actor, result, "inspection_cancelled", result["version"],
               {"from_status": base["status"], "to_status": "cancelled", "reason": payload.reason})
        db.commit(); _invalidate(result)
        return {"inspection": _inspection_item(_inspection_row(db, actor, inspection_id))}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise


def _media_root() -> Path:
    raw = str(settings.inspection_media_directory or "").strip()
    if not raw:
        raise HTTPException(503, "Inspection media storage is not configured")
    root = Path(raw)
    if not root.is_absolute():
        raise HTTPException(503, "Inspection media storage is invalid")
    root = root.resolve()
    repository = Path(__file__).resolve().parents[2]
    if root == repository or repository in root.parents:
        raise HTTPException(503, "Inspection media storage must be outside the repository")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _photo_content_type(data: bytes, declared: str) -> tuple[str, str]:
    normalized = declared.split(";", 1)[0].strip().lower()
    expected = MEDIA_TYPES.get(normalized)
    if expected is None:
        raise HTTPException(422, "Only JPEG, PNG, or WebP photos are allowed")
    magic, extension = expected
    valid = data.startswith(magic)
    if normalized == "image/webp":
        valid = valid and len(data) >= 12 and data[8:12] == b"WEBP"
    if not valid:
        raise HTTPException(422, "Photo MIME type does not match file content")
    return normalized, extension


def upload_photo(db, user, inspection_id: int, expected_version: int, filename: str,
                 content_type: str, data: bytes, captured_at: datetime | None):
    actor = _actor(user)
    try:
        base = _inspection_row(db, actor, inspection_id, assigned_only=True)
        _write_roles(actor, {"agronomist"})
        if not data or len(data) > MAX_PHOTO_BYTES:
            raise HTTPException(413, "Photo exceeds the 8 MiB limit")
        media_type, extension = _photo_content_type(data, content_type)
        safe_original = Path(filename or "photo").name
        if safe_original != filename or "\x00" in safe_original or not 1 <= len(safe_original) <= 255:
            raise HTTPException(422, "Invalid original filename")
        root = _media_root()
        if base["status"] != "in_progress":
            raise HTTPException(409, "Photos can be uploaded only while inspection is in progress")
        totals = _one(db.execute(text(
            "SELECT count(*) AS count,COALESCE(sum(byte_size),0) AS bytes FROM inspection_evidence "
            "WHERE inspection_id=:id AND evidence_type='photo' AND deleted_at IS NULL"
        ), {"id": inspection_id}))
        if int(totals["count"]) >= MAX_PHOTOS or int(totals["bytes"]) + len(data) > MAX_TOTAL_PHOTO_BYTES:
            raise HTTPException(413, "Inspection photo count or total-size limit exceeded")
        identifier = str(uuid.uuid4())
        storage_key = f"{identifier[:2]}/{identifier}{extension}"
        target = (root / storage_key).resolve()
        if root not in target.parents:
            raise HTTPException(500, "Media path validation failed")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        try:
            photo = _one(db.execute(text(
                """INSERT INTO inspection_evidence
                (inspection_id,result_id,field_id,enterprise_id,created_by_id,evidence_type,provider,
                 provider_reference,original_filename,media_type,byte_size,sha256,captured_at,
                 provider_metadata,storage_key)
                VALUES (:inspection_id,(SELECT id FROM inspection_results WHERE inspection_id=:inspection_id),
                 :field_id,:enterprise_id,:actor,'photo','private_runtime',NULL,:filename,:media_type,
                 :byte_size,:sha256,:captured_at,'{}'::jsonb,:storage_key)
                RETURNING id,original_filename,media_type,byte_size,sha256,captured_at,created_at,version"""
            ), {"inspection_id": inspection_id, "field_id": base["field_id"],
                "enterprise_id": base["enterprise_id"], "actor": actor.user_id,
                "filename": safe_original, "media_type": media_type, "byte_size": len(data),
                "sha256": digest, "captured_at": captured_at, "storage_key": storage_key}))
            result = _one(db.execute(text(
                "UPDATE field_inspections SET updated_by_id=:actor,updated_at=now(),version=version+1 "
                "WHERE id=:id AND assigned_to_id=:actor AND version=:version AND status='in_progress' "
                "RETURNING id,field_id,enterprise_id,version"
            ), {"actor": actor.user_id, "id": inspection_id, "version": expected_version}))
            if not result:
                raise HTTPException(409, "Version or state conflict")
            _audit(db, actor, result, "photo_uploaded", result["version"], {"photo_id": photo["id"]})
            db.commit(); _invalidate(result)
            return {"inspection": _inspection_item(_inspection_row(db, actor, inspection_id, assigned_only=True)),
                    "photo": dict(photo)}
        except Exception:
            target.unlink(missing_ok=True)
            raise
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise


def photo_download(db, user, inspection_id: int, photo_id: int):
    actor = _actor(user)
    _inspection_row(db, actor, inspection_id, assigned_only=True)
    row = _one(db.execute(text(
        "SELECT id,storage_key,original_filename,media_type,byte_size,sha256 FROM inspection_evidence "
        "WHERE id=:photo_id AND inspection_id=:inspection_id AND evidence_type='photo' AND deleted_at IS NULL"
    ), {"photo_id": photo_id, "inspection_id": inspection_id}))
    if not row or not row["storage_key"] or not STORAGE_KEY.fullmatch(row["storage_key"]):
        raise HTTPException(404, "Photo not found")
    root = _media_root()
    path = (root / row["storage_key"]).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "Photo not found")
    return row, path


def delete_photo(db, user, inspection_id: int, photo_id: int, expected_version: int):
    actor = _actor(user)
    try:
        base = _inspection_row(db, actor, inspection_id, assigned_only=True)
        _write_roles(actor, {"agronomist", "admin", "manager"})
        if base["status"] not in {"in_progress", "submitted"}:
            raise HTTPException(409, "Photo cannot be deleted in the current state")
        photo = _one(db.execute(text(
            "UPDATE inspection_evidence SET deleted_at=now(),deleted_by_id=:actor,version=version+1 "
            "WHERE id=:photo_id AND inspection_id=:inspection_id AND deleted_at IS NULL "
            "RETURNING id,storage_key"
        ), {"actor": actor.user_id, "photo_id": photo_id, "inspection_id": inspection_id}))
        if not photo:
            raise HTTPException(404, "Photo not found")
        result = _one(db.execute(text(
            "UPDATE field_inspections SET updated_by_id=:actor,updated_at=now(),version=version+1 "
            "WHERE id=:id AND version=:version RETURNING id,field_id,enterprise_id,version"
        ), {"actor": actor.user_id, "id": inspection_id, "version": expected_version}))
        if not result:
            raise HTTPException(409, "Version conflict")
        _audit(db, actor, result, "photo_deleted", result["version"], {"photo_id": photo_id})
        db.commit(); _invalidate(result)
        if photo["storage_key"] and STORAGE_KEY.fullmatch(photo["storage_key"]):
            root = _media_root(); target = (root / photo["storage_key"]).resolve()
            if root in target.parents:
                target.unlink(missing_ok=True)
        return {"inspection": _inspection_item(_inspection_row(db, actor, inspection_id))}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise


def create_action(db, user, inspection_id: int, payload):
    actor = _actor(user)
    try:
        base = _inspection_row(db, actor, inspection_id)
        _write_roles(actor, {"admin", "manager"})
        if base["status"] != "confirmed":
            raise HTTPException(409, "Actions require a confirmed inspection")
        owner = _eligible_user(db, base["enterprise_id"], payload.owner_id)
        result_id = _one(db.execute(text(
            "SELECT id FROM inspection_results WHERE inspection_id=:id"
        ), {"id": inspection_id}))
        if not result_id:
            raise HTTPException(422, "Inspection finding is missing")
        action = _one(db.execute(text(
            """INSERT INTO corrective_actions
            (inspection_id,result_id,field_id,enterprise_id,created_by_id,owner_id,description,due_date,
             status,action_type,planned_start_at,due_at)
            VALUES (:inspection_id,:result_id,:field_id,:enterprise_id,:actor,:owner,:instructions,
             (:due_at AT TIME ZONE 'Asia/Tashkent')::date,'planned',:action_type,:planned_start,:due_at)
            RETURNING *"""
        ), {"inspection_id": inspection_id, "result_id": result_id["id"], "field_id": base["field_id"],
            "enterprise_id": base["enterprise_id"], "actor": actor.user_id, "owner": owner["id"],
            "instructions": payload.instructions, "due_at": payload.due_at,
            "action_type": payload.action_type, "planned_start": payload.planned_start_at}))
        updated = _one(db.execute(text(
            "UPDATE field_inspections SET updated_by_id=:actor,updated_at=now(),version=version+1 "
            "WHERE id=:id AND version=:version AND status='confirmed' "
            "RETURNING id,field_id,enterprise_id,version"
        ), {"actor": actor.user_id, "id": inspection_id, "version": payload.expected_inspection_version}))
        if not updated:
            raise HTTPException(409, "Version or state conflict")
        _audit(db, actor, updated, "action_created", action["version"],
               {"to_status": "planned", "action_type": payload.action_type}, action_id=action["id"])
        db.commit(); _invalidate(updated)
        return {"inspection": _inspection_item(_inspection_row(db, actor, inspection_id)),
                "action": dict(action)}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise


def action_transition(db, user, action_id: int, payload):
    actor = _actor(user)
    try:
        tenant, params = _tenant_clause(actor, "a")
        action = _one(db.execute(text(
            "SELECT a.*,i.id AS inspection_object_id FROM corrective_actions a "
            "JOIN field_inspections i ON i.id=a.inspection_id WHERE a.id=:action_id" + tenant
        ), {"action_id": action_id, **params}))
        if not action:
            raise HTTPException(404, "Action not found")
        if actor.role == "viewer":
            raise HTTPException(403, "Viewer is read-only")
        if actor.role not in {"admin", "manager"} and not (
            actor.role == "agronomist" and action["owner_id"] == actor.user_id
        ):
            raise HTTPException(404, "Action not found")
        transitions = {
            "start": ("planned", "in_progress", "started_at=now()", "action_started"),
            "complete": ("in_progress", "completed", "completion_note=:note,completed_at=now()", "action_completed"),
            "cancel": ("planned", "cancelled", "cancelled_reason=:note,cancelled_at=now()", "action_cancelled"),
        }
        source, target, extra, event = transitions[payload.transition]
        if payload.transition == "cancel" and action["status"] == "in_progress":
            source = "in_progress"
        updated = _one(db.execute(text(f"""
            UPDATE corrective_actions SET status=:target,{extra},updated_at=now(),version=version+1
            WHERE id=:action_id AND version=:version AND status=:source RETURNING *
        """), {"target": target, "note": payload.note, "action_id": action_id,
            "version": payload.expected_version, "source": source}))
        if not updated:
            raise HTTPException(409, "Version or state conflict")
        base = {"id": action["inspection_id"], "field_id": action["field_id"],
                "enterprise_id": action["enterprise_id"]}
        _audit(db, actor, base, event, updated["version"],
               {"from_status": action["status"], "to_status": target, "reason": payload.note},
               action_id=action_id)
        db.commit(); _invalidate(base)
        return {"action": dict(updated)}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise


def verify_action(db, user, action_id: int, payload):
    actor = _actor(user)
    try:
        tenant, params = _tenant_clause(actor, "a")
        action = _one(db.execute(text(
            "SELECT a.*,i.source_reason,i.priority FROM corrective_actions a "
            "JOIN field_inspections i ON i.id=a.inspection_id WHERE a.id=:action_id" + tenant
        ), {"action_id": action_id, **params}))
        if not action:
            raise HTTPException(404, "Action not found")
        _write_roles(actor, {"admin", "manager"})
        target = "verified_effective" if _enum(payload.result) == "effective" else "verified_ineffective"
        follow_up = None
        if payload.create_follow_up:
            _eligible_user(db, action["enterprise_id"], payload.follow_up_assignee_id, agronomist_only=True)
            key = f"follow-up-{uuid.uuid4()}"
            follow_up = _one(db.execute(text(
                """INSERT INTO field_inspections
                (field_id,enterprise_id,created_by_id,updated_by_id,assigned_to_id,client_request_id,
                 request_fingerprint,source,source_priority,source_reason_codes,title,instructions,due_date,
                 status,source_kind,source_reason,priority,due_at,follow_up_of_id)
                VALUES (:field_id,:enterprise_id,:actor,:actor,:assignee,:key,:fingerprint,'manual',
                 :priority,'[]'::jsonb,:title,:reason,(:due_at AT TIME ZONE 'Asia/Tashkent')::date,
                 'assigned','manual',:reason,:priority,:due_at,:parent)
                RETURNING id,field_id,enterprise_id,version,status"""
            ), {"field_id": action["field_id"], "enterprise_id": action["enterprise_id"],
                "actor": actor.user_id, "assignee": payload.follow_up_assignee_id, "key": key,
                "fingerprint": _fingerprint({"action": action_id, "key": key}),
                "priority": action["priority"], "title": f"Повторный осмотр: {action['source_reason']}"[:255],
                "reason": f"Повторный цикл после действия #{action_id}: {payload.notes}"[:2000],
                "due_at": payload.follow_up_due_at, "parent": action["inspection_id"]}))
        updated = _one(db.execute(text(
            """UPDATE corrective_actions SET status=:status,verified_by_id=:actor,verified_at=now(),
            verification_result=:result,verification_notes=:notes,verification_index_name=:index_name,
            verification_sample_value=:sample,follow_up_inspection_id=:follow_up,updated_at=now(),
            version=version+1 WHERE id=:action_id AND version=:version AND status='completed'
            RETURNING *"""
        ), {"status": target, "actor": actor.user_id, "result": _enum(payload.result),
            "notes": payload.notes, "index_name": payload.index_name, "sample": payload.sampled_value,
            "follow_up": follow_up["id"] if follow_up else None, "action_id": action_id,
            "version": payload.expected_version}))
        if not updated:
            raise HTTPException(409, "Version or state conflict")
        base = {"id": action["inspection_id"], "field_id": action["field_id"],
                "enterprise_id": action["enterprise_id"]}
        event = "action_verified_effective" if target == "verified_effective" else "action_verified_ineffective"
        _audit(db, actor, base, event, updated["version"],
               {"from_status": "completed", "to_status": target, "result": _enum(payload.result),
                "follow_up_inspection_id": follow_up["id"] if follow_up else None}, action_id=action_id)
        if follow_up:
            _audit(db, actor, follow_up, "follow_up_created", follow_up["version"],
                   {"to_status": follow_up["status"], "follow_up_inspection_id": follow_up["id"]})
        db.commit(); _invalidate(base)
        return {"action": dict(updated),
                "follow_up_inspection": (_inspection_item(_inspection_row(db, actor, follow_up["id"]))
                                         if follow_up else None)}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise


def field_timeline(db, user, field_id: int, limit: int, offset: int):
    actor = _actor(user)
    _field(db, actor, field_id)
    tenant, params = _tenant_clause(actor, "ev")
    rows = _all(db.execute(text(
        "SELECT ev.id,ev.inspection_id,ev.action_id,ev.event_type,ev.entity_version,"
        "ev.event_metadata,ev.occurred_at,ev.actor_id,u.full_name AS actor_name "
        "FROM operational_audit_events ev JOIN users u ON u.id=ev.actor_id "
        "WHERE ev.field_id=:field_id" + tenant +
        " ORDER BY ev.occurred_at DESC,ev.id DESC LIMIT :limit OFFSET :offset"
    ), {"field_id": field_id, "limit": limit, "offset": offset, **params}))
    summary = _one(db.execute(text(
        "SELECT count(*) FILTER (WHERE status IN ('new','assigned','in_progress','submitted')) AS open,"
        "count(*) FILTER (WHERE status='confirmed') AS confirmed,"
        "count(*) FILTER (WHERE status='rejected') AS rejected "
        "FROM field_inspections WHERE field_id=:field_id" +
        (" AND enterprise_id=:actor_enterprise_id" if actor.role in TENANT_ROLES else "")
    ), {"field_id": field_id, **params}))
    return {"field_id": field_id, "summary": {key: int(summary[key] or 0) for key in
                                               ("open", "confirmed", "rejected")},
            "limit": limit, "offset": offset, "events": [dict(row) for row in rows]}
