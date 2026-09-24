"""Read model for legacy TASK_209 field inspections (source_kind='legacy').

The legacy write workflow is retired (TASK_225): creation, edits and
transitions answer 410 Gone at the API. The canonical lifecycle lives in
services/anomaly_inspections.py, which is also the only path that can close
out a still-active legacy row.
"""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

from api.dependencies import ALLOWED_ROLES, TENANT_ROLES

TASHKENT = ZoneInfo("Asia/Tashkent")
ITEM_SELECT = """
SELECT i.id, i.field_id, f.name AS field_name, i.enterprise_id, e.name AS enterprise_name,
 i.created_by_id, creator.full_name AS created_by_name,
 i.assigned_to_id, assignee.full_name AS assigned_to_name,
 i.source, i.source_priority, i.source_attention_score, i.source_observation_date,
 i.source_reason_codes, i.title, i.instructions, i.due_date, i.status,
 COALESCE(i.status IN ('pending','in_progress') AND i.due_date < :today, false) AS is_overdue,
 i.version, i.created_at, i.updated_at, i.started_at, i.completed_at, i.cancelled_at,
 i.completion_summary, i.cancellation_reason
FROM field_inspections i JOIN fields f ON f.id=i.field_id
JOIN enterprises e ON e.id=i.enterprise_id JOIN users creator ON creator.id=i.created_by_id
LEFT JOIN users assignee ON assignee.id=i.assigned_to_id
"""


@dataclass(frozen=True, slots=True)
class ActorScope:
    role: str
    user_id: int
    enterprise_id: int | None


def _actor(user, write=False):
    """Copy all authorization data before any transaction boundary."""
    raw_role = user.role
    role = raw_role.lower() if raw_role else ""
    user_id = user.id
    enterprise_id = user.enterprise_id
    if role not in ALLOWED_ROLES:
        raise HTTPException(403, "Unknown role")
    if role in TENANT_ROLES and enterprise_id is None:
        raise HTTPException(403, "User has no enterprise_id")
    if write and role == "viewer":
        raise HTTPException(403, "Viewer is read-only")
    return ActorScope(role, user_id, enterprise_id)


def _one(result):
    if hasattr(result, "mappings"):
        return result.mappings().first()
    row = result.fetchone()
    return row._mapping if row is not None and hasattr(row, "_mapping") else row


def _all(result):
    if hasattr(result, "mappings"):
        return list(result.mappings().all())
    return [r._mapping if hasattr(r, "_mapping") else r for r in result.fetchall()]


def _item(row):
    return {
        "id": row["id"],
        "field": {"id": row["field_id"], "name": row["field_name"],
                  "enterprise_id": row["enterprise_id"], "enterprise_name": row["enterprise_name"]},
        "created_by": {"id": row["created_by_id"], "display_name": row["created_by_name"]},
        "assigned_to": None if row["assigned_to_id"] is None else {
            "id": row["assigned_to_id"], "display_name": row["assigned_to_name"]},
        **{key: row[key] for key in (
            "source", "source_priority", "source_attention_score", "source_observation_date",
            "source_reason_codes", "title", "instructions", "due_date", "status", "is_overdue",
            "version", "created_at", "updated_at", "started_at", "completed_at", "cancelled_at",
            "completion_summary", "cancellation_reason")},
    }


def _tenant_clause(actor, alias="i"):
    return f" AND {alias}.enterprise_id=:eid" if actor.role in TENANT_ROLES else ""


def _reload(db, inspection_id, actor):
    tenant = _tenant_clause(actor)
    params = {"id": inspection_id, "today": datetime.now(TASHKENT).date()}
    if tenant:
        params["eid"] = actor.enterprise_id
    row = _one(db.execute(text(ITEM_SELECT + " WHERE i.id=:id AND i.source_kind='legacy'" + tenant), params))
    if not row:
        raise HTTPException(404, "Inspection not found")
    return _item(row)


def _list_items(db, actor, filters):
    conditions = ["i.source_kind='legacy'"]
    params = {"today": datetime.now(TASHKENT).date(), "lim": filters["limit"], "off": filters["offset"]}
    enterprise_id = filters.get("enterprise_id")
    if actor.role in TENANT_ROLES:
        if enterprise_id is not None and enterprise_id != actor.enterprise_id:
            raise HTTPException(403, "Foreign enterprise filter")
        enterprise_id = actor.enterprise_id
    if enterprise_id is not None:
        conditions.append("i.enterprise_id=:eid")
        params["eid"] = enterprise_id
    for key in ("field_id", "assigned_to_id", "status"):
        if filters.get(key) is not None:
            conditions.append(f"i.{key}=:{key}")
            value = filters[key]
            params[key] = value.value if hasattr(value, "value") else value
    if filters.get("overdue_only"):
        conditions.append("i.status IN ('pending','in_progress') AND i.due_date < :today")
    if filters.get("due_before"):
        conditions.append("i.due_date <= :due_before")
        params["due_before"] = filters["due_before"]
    if filters.get("created_after"):
        conditions.append("i.created_at >= :created_after")
        params["created_after"] = filters["created_after"]
    where = " AND ".join(conditions) or "true"
    order = ("CASE status WHEN 'in_progress' THEN 1 WHEN 'pending' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END, "
             "CASE WHEN status IN ('pending','in_progress') AND due_date < :today THEN 0 ELSE 1 END, "
             "due_date ASC NULLS LAST, created_at DESC, id DESC")
    sql = f"""WITH filtered AS ({ITEM_SELECT} WHERE {where}),
summary AS (SELECT count(*) AS total_count,
 count(*) FILTER (WHERE status='pending') AS pending_count,
 count(*) FILTER (WHERE status='in_progress') AS in_progress_count,
 count(*) FILTER (WHERE status='completed') AS completed_count,
 count(*) FILTER (WHERE status='cancelled') AS cancelled_count,
 count(*) FILTER (WHERE status IN ('pending','in_progress') AND due_date < :today) AS overdue_count
 FROM filtered),
paged AS (SELECT * FROM filtered ORDER BY {order} LIMIT :lim OFFSET :off)
SELECT paged.*, summary.* FROM summary LEFT JOIN paged ON true
ORDER BY {order}"""
    rows = _all(db.execute(text(sql), params))
    summary_row = rows[0]
    summary = {"total": summary_row["total_count"], "pending": summary_row["pending_count"],
               "in_progress": summary_row["in_progress_count"], "completed": summary_row["completed_count"],
               "cancelled": summary_row["cancelled_count"], "overdue": summary_row["overdue_count"]}
    return {"generated_at": datetime.now(TASHKENT), "summary": summary, "limit": filters["limit"],
            "offset": filters["offset"], "items": [_item(row) for row in rows if row["id"] is not None]}


def list_items(db, user, filters):
    try:
        actor = _actor(user)
        return _list_items(db, actor, filters)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def get(db, user, inspection_id):
    try:
        actor = _actor(user)
        return _reload(db, inspection_id, actor)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
