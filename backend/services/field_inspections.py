"""Explicit-SQL field-inspection workflow with tenant and race safety."""
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.dependencies import ALLOWED_ROLES, TENANT_ROLES, normalize_role

TASHKENT = ZoneInfo("Asia/Tashkent")
ITEM_SELECT = """
SELECT i.id, i.field_id, f.name AS field_name, i.enterprise_id, e.name AS enterprise_name,
 i.created_by_id, creator.full_name AS created_by_name,
 i.assigned_to_id, assignee.full_name AS assigned_to_name,
 i.source, i.source_priority, i.source_attention_score, i.source_observation_date,
 i.source_reason_codes, i.title, i.instructions, i.due_date, i.status,
 (i.status IN ('pending','in_progress') AND i.due_date < :today) AS is_overdue,
 i.version, i.created_at, i.updated_at, i.started_at, i.completed_at, i.cancelled_at,
 i.completion_summary, i.cancellation_reason
FROM field_inspections i JOIN fields f ON f.id=i.field_id
JOIN enterprises e ON e.id=i.enterprise_id JOIN users creator ON creator.id=i.created_by_id
LEFT JOIN users assignee ON assignee.id=i.assigned_to_id
"""


def _role(user, write=False):
    role = normalize_role(user)
    if role not in ALLOWED_ROLES: raise HTTPException(403, "Unknown role")
    if role in TENANT_ROLES and user.enterprise_id is None: raise HTTPException(403, "User has no enterprise_id")
    if write and role == "viewer": raise HTTPException(403, "Viewer is read-only")
    return role


def _one(result):
    if hasattr(result, "mappings"):
        return result.mappings().first()
    row = result.fetchone()
    return row._mapping if row is not None and hasattr(row, "_mapping") else row


def _all(result):
    if hasattr(result, "mappings"): return list(result.mappings().all())
    return [r._mapping if hasattr(r, "_mapping") else r for r in result.fetchall()]


def _item(r):
    return {"id": r["id"], "field": {"id": r["field_id"], "name": r["field_name"], "enterprise_id": r["enterprise_id"], "enterprise_name": r["enterprise_name"]},
            "created_by": {"id": r["created_by_id"], "display_name": r["created_by_name"]},
            "assigned_to": None if r["assigned_to_id"] is None else {"id": r["assigned_to_id"], "display_name": r["assigned_to_name"]},
            **{k: r[k] for k in ("source","source_priority","source_attention_score","source_observation_date","source_reason_codes","title","instructions","due_date","status","is_overdue","version","created_at","updated_at","started_at","completed_at","cancelled_at","completion_summary","cancellation_reason")}}


def fingerprint(user_id, payload, assigned_to_id):
    data = payload.model_dump(mode="json")
    data["assigned_to_id"] = assigned_to_id
    data["current_user_id"] = user_id
    data["source_reason_codes"] = sorted(set(data["source_reason_codes"]))
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _due(value):
    if value is not None and value < datetime.now(TASHKENT).date(): raise HTTPException(422, "due_date cannot be in the past")


def _assignee(db, assignee_id, enterprise_id):
    if assignee_id is None: return
    row = _one(db.execute(text("SELECT id FROM users WHERE id=:uid AND enterprise_id=:eid AND role='agronomist' AND is_active=true"), {"uid": assignee_id, "eid": enterprise_id}))
    if not row: raise HTTPException(422, "Assignee is not an eligible agronomist")


def _reload(db, inspection_id, user):
    role = _role(user)
    tenant = " AND i.enterprise_id=:eid" if role in TENANT_ROLES else ""
    params = {"id": inspection_id, "today": datetime.now(TASHKENT).date()}
    if tenant: params["eid"] = user.enterprise_id
    row = _one(db.execute(text(ITEM_SELECT + " WHERE i.id=:id" + tenant), params))
    if not row: raise HTTPException(404, "Inspection not found")
    return _item(row)


def create(db, user, payload, key):
    try:
        role = _role(user, True); _due(payload.due_date)
        assigned = user.id if role == "agronomist" and payload.assigned_to_id is None else payload.assigned_to_id
        if role == "agronomist" and assigned != user.id: raise HTTPException(403, "Agronomists may assign only themselves")
        fp = fingerprint(user.id, payload, assigned)
        existing = _one(db.execute(text("SELECT id, request_fingerprint FROM field_inspections WHERE client_request_id=:key"), {"key": key}))
        if existing:
            if existing["request_fingerprint"] != fp: raise HTTPException(409, "Idempotency key payload conflict")
            db.rollback(); return False, _reload(db, existing["id"], user)
        tenant = " AND f.enterprise_id=:eid" if role in TENANT_ROLES else ""
        params = {"fid": payload.field_id}
        if tenant: params["eid"] = user.enterprise_id
        field = _one(db.execute(text("SELECT f.id, f.enterprise_id FROM fields f WHERE f.id=:fid AND f.is_active=true" + tenant), params))
        if not field: raise HTTPException(404, "Field not found")
        _assignee(db, assigned, field["enterprise_id"])
        active = _one(db.execute(text("SELECT id FROM field_inspections WHERE field_id=:fid AND status IN ('pending','in_progress')"), {"fid": payload.field_id}))
        if active: raise HTTPException(409, f"Active inspection exists: {active['id']}")
        values = payload.model_dump(); values.update({"eid": field["enterprise_id"], "uid": user.id, "assigned": assigned, "key": key, "fp": fp})
        result = db.execute(text("""INSERT INTO field_inspections
          (field_id,enterprise_id,created_by_id,assigned_to_id,client_request_id,request_fingerprint,source,source_priority,source_attention_score,source_observation_date,source_reason_codes,title,instructions,due_date)
          VALUES (:field_id,:eid,:uid,:assigned,:key,:fp,:source,:source_priority,:source_attention_score,:source_observation_date,CAST(:reason_json AS jsonb),:title,:instructions,:due_date) RETURNING id"""), {**values, "source": payload.source.value, "source_priority": payload.source_priority.value if payload.source_priority else None, "reason_json": json.dumps(payload.source_reason_codes)})
        inserted = _one(result); db.commit(); return True, _reload(db, inserted["id"], user)
    except IntegrityError:
        db.rollback()
        same = _one(db.execute(text("SELECT id, request_fingerprint FROM field_inspections WHERE client_request_id=:key"), {"key": key}))
        if same:
            if same["request_fingerprint"] != fp:
                db.rollback(); raise HTTPException(409, "Idempotency key payload conflict")
            db.rollback()
            return False, _reload(db, same["id"], user)
        active = _one(db.execute(text("SELECT id FROM field_inspections WHERE field_id=:fid AND status IN ('pending','in_progress')"), {"fid": payload.field_id}))
        db.rollback()
        if active: raise HTTPException(409, f"Active inspection exists: {active['id']}")
        raise HTTPException(409, "Concurrent inspection conflict")
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise


def list_items(db, user, filters):
    role = _role(user); conditions=[]; params={"today": datetime.now(TASHKENT).date(), "lim": filters["limit"], "off": filters["offset"]}
    eid = filters.get("enterprise_id")
    if role in TENANT_ROLES:
        if eid is not None and eid != user.enterprise_id: raise HTTPException(403, "Foreign enterprise filter")
        eid = user.enterprise_id
    if eid is not None: conditions.append("i.enterprise_id=:eid"); params["eid"] = eid
    for key in ("field_id","assigned_to_id","status"):
        if filters.get(key) is not None: conditions.append(f"i.{key}=:{key}"); params[key]=filters[key].value if hasattr(filters[key], "value") else filters[key]
    if filters.get("overdue_only"): conditions.append("i.status IN ('pending','in_progress') AND i.due_date < :today")
    if filters.get("due_before"): conditions.append("i.due_date <= :due_before"); params["due_before"]=filters["due_before"]
    if filters.get("created_after"): conditions.append("i.created_at >= :created_after"); params["created_after"]=filters["created_after"]
    where = " AND ".join(conditions) or "true"
    order = "CASE status WHEN 'in_progress' THEN 1 WHEN 'pending' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END, CASE WHEN status IN ('pending','in_progress') AND due_date < :today THEN 0 ELSE 1 END, due_date ASC NULLS LAST, created_at DESC, id DESC"
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
    summary = {"total":summary_row["total_count"],"pending":summary_row["pending_count"],"in_progress":summary_row["in_progress_count"],"completed":summary_row["completed_count"],"cancelled":summary_row["cancelled_count"],"overdue":summary_row["overdue_count"]}
    return {"generated_at": datetime.now(TASHKENT), "summary": summary, "limit":filters["limit"], "offset":filters["offset"], "items":[_item(r) for r in rows if r["id"] is not None]}


def get(db, user, inspection_id): return _reload(db, inspection_id, user)


def update(db, user, inspection_id, payload):
    try:
        role=_role(user, True); _due(payload.due_date) if "due_date" in payload.model_fields_set else None
        if role == "agronomist" and "assigned_to_id" in payload.model_fields_set: raise HTTPException(403, "Agronomists cannot reassign")
        tenant=" AND enterprise_id=:eid" if role in TENANT_ROLES else ""; owner=" AND assigned_to_id=:uid" if role=="agronomist" else ""
        data={"id":inspection_id,"ver":payload.expected_version,"uid":user.id}
        if tenant: data["eid"]=user.enterprise_id
        if "assigned_to_id" in payload.model_fields_set:
            base=_one(db.execute(text("SELECT enterprise_id FROM field_inspections WHERE id=:id" + tenant), data))
            if not base: raise HTTPException(404,"Inspection not found")
            _assignee(db,payload.assigned_to_id,base["enterprise_id"])
        fields=[]
        for k in ("assigned_to_id","title","instructions","due_date"):
            if k in payload.model_fields_set: fields.append(f"{k}=:{k}"); data[k]=getattr(payload,k)
        result=db.execute(text("UPDATE field_inspections SET "+", ".join(fields)+", version=version+1, updated_at=now() WHERE id=:id AND version=:ver AND status IN ('pending','in_progress')"+tenant+owner+" RETURNING id"),data)
        row=_one(result)
        if not row: _classify(db,user,inspection_id,payload.expected_version,"assigned")
        db.commit(); return _reload(db,inspection_id,user)
    except HTTPException: db.rollback(); raise
    except Exception: db.rollback(); raise


def _classify(db,user,i,version,ownership=None):
    role = _role(user)
    clauses = ["id=:id"]
    params = {"id": i, "uid": user.id}
    if role in TENANT_ROLES:
        clauses.append("enterprise_id=:eid"); params["eid"] = user.enterprise_id
    if role == "agronomist" and ownership == "assigned": clauses.append("assigned_to_id=:uid")
    if role == "agronomist" and ownership == "created": clauses.append("created_by_id=:uid")
    row = _one(db.execute(text("SELECT id, version, status FROM field_inspections WHERE " + " AND ".join(clauses)), params))
    if not row: raise HTTPException(404, "Inspection not found")
    raise HTTPException(409, "Version or state conflict")


def transition(db,user,i,payload,action):
    try:
        role=_role(user,True)
        if action=="cancel": state="status IN ('pending','in_progress')"; target="cancelled"; extra="cancellation_reason=:value, cancelled_at=now()"
        elif action=="complete": state="status='in_progress'"; target="completed"; extra="completion_summary=:value, completed_at=now()"
        else: state="status='pending'"; target="in_progress"; extra="started_at=now()"
        tenant=" AND enterprise_id=:eid" if role in TENANT_ROLES else ""; ownership=""
        if role=="agronomist":
            ownership=" AND assigned_to_id=:uid" if action!="cancel" else " AND created_by_id=:uid AND status='pending'"
        if action=="start": ownership += " AND assigned_to_id IS NOT NULL"
        params={"id":i,"ver":payload.expected_version,"uid":user.id,"value":getattr(payload,"completion_summary",getattr(payload,"cancellation_reason",None))}
        if tenant: params["eid"]=user.enterprise_id
        row=_one(db.execute(text(f"UPDATE field_inspections SET status='{target}', {extra}, version=version+1, updated_at=now() WHERE id=:id AND version=:ver AND {state}{tenant}{ownership} RETURNING id"),params))
        if not row:
            ownership = "created" if action == "cancel" else "assigned"
            _classify(db,user,i,payload.expected_version,ownership)
        db.commit(); return _reload(db,i,user)
    except HTTPException: db.rollback(); raise
    except Exception: db.rollback(); raise
