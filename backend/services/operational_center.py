"""Tenant-safe Operational Command Center read model and notification state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.dependencies import ALLOWED_ROLES, TENANT_ROLES
from services.irrigation_context import CAUSALITY_LIMITATION, weather_context
from services.telematics import UnsupportedTelematicsProvider, read_field_telematics
from services.weather import get_field_weather


CASE_KEY = re.compile(
    r"^(inspection|candidate|alert):[1-9][0-9]*$"
    r"|^freshness:[1-9][0-9]*:(ndvi|savi|evi|ndmi|ndre)$"
    r"|^external:[1-9][0-9]*:[0-9a-f-]{32,36}$"
)
def _row(result):
    if hasattr(result, "mappings"):
        value = result.mappings().first()
    else:
        value = result.fetchone()
        value = value._mapping if value is not None and hasattr(value, "_mapping") else value
    return dict(value) if value is not None else None


def _rows(result):
    if hasattr(result, "mappings"):
        values = result.mappings().all()
    else:
        values = result.fetchall()
    return [dict(value._mapping if hasattr(value, "_mapping") else value) for value in values]


def _actor(user, *, write: bool = False) -> dict[str, Any]:
    raw_role = getattr(user.role, "value", user.role)
    role = str(raw_role or "").strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(403, "Unknown role")
    enterprise_id = getattr(user, "enterprise_id", None)
    if role in TENANT_ROLES and enterprise_id is None:
        raise HTTPException(403, "User has no enterprise_id")
    if write and role == "viewer":
        raise HTTPException(403, "Viewer is read-only")
    return {
        "user_id": int(user.id),
        "role": role,
        "enterprise_id": int(enterprise_id) if enterprise_id is not None else None,
    }


CASES_CTE = r"""
WITH inspection_cases AS (
  SELECT
    'inspection:' || i.id::text AS case_key,
    'inspection'::text AS root_source,
    i.id::text AS source_id,
    i.enterprise_id,
    e.name AS enterprise_name,
    i.field_id,
    f.name AS field_name,
    crop.crop_type_id,
    crop.crop_name,
    COALESCE(p.priority, i.priority, i.source_priority, 'normal') AS source_priority,
    CASE lower(COALESCE(p.priority, i.priority, i.source_priority, 'normal'))
      WHEN 'critical' THEN 0 WHEN 'extreme' THEN 0 WHEN 'urgent' THEN 0
      WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'moderate' THEN 2
      WHEN 'normal' THEN 2 WHEN 'warning' THEN 2 ELSE 3 END AS priority_rank,
    CASE
      WHEN p.status='closed' AND p.closed_at >= :as_of - interval '30 days' THEN 'improved_closed'
      WHEN p.status='pending_verification' THEN 'awaiting_verification'
      WHEN work.status='in_progress' THEN 'awaiting_evidence'
      WHEN p.status IN ('draft','rework') OR work.status='planned' OR i.status='confirmed' THEN 'awaiting_work'
      WHEN i.status='submitted' THEN 'awaiting_review'
      ELSE 'awaiting_inspection'
    END AS operational_status,
    COALESCE(work.assigned_to_id, i.assigned_to_id) AS assignee_id,
    assignee.full_name AS assignee_name,
    COALESCE(work.due_at, CASE WHEN p.id IS NULL THEN COALESCE(i.due_at,i.due_date) END) AS due_at,
    CASE
      WHEN work.id IS NOT NULL THEN work.due_at < :as_of
      WHEN p.id IS NULL THEN COALESCE(i.due_at,i.due_date) < :as_of
        AND i.status IN ('pending','new','assigned','in_progress','submitted')
      ELSE false
    END AS is_overdue,
    (EXISTS (
       SELECT 1 FROM corrective_actions ca
       WHERE ca.inspection_id=i.id AND ca.enterprise_id=i.enterprise_id AND ca.status='blocked'
     ) OR p.verification_status IN ('CLOUD_BLOCKED','QUALITY_BLOCKED','PROVIDER_DEGRADED')) AS blocked,
    (p.status='pending_verification') AS awaiting_verification,
    CASE WHEN p.verification_status IN ('CLOUD_BLOCKED','QUALITY_BLOCKED','PROVIDER_DEGRADED')
      THEN lower(p.verification_status) ELSE NULL END AS external_state,
    GREATEST(
      i.updated_at,
      COALESCE(p.closed_at,p.completed_at,p.started_at,p.approved_at,p.created_at,i.updated_at),
      COALESCE(work.started_at,work.created_at,i.updated_at)
    ) AS source_time,
    i.id AS inspection_id,
    p.id AS plan_id,
    p.candidate_id,
    COALESCE(p.alert_id, i.source_alert_id) AS alert_id,
    COALESCE(NULLIF(i.title,''), 'Осмотр поля') AS title,
    jsonb_build_object(
      'source_kind',i.source_kind,'inspection_status',i.status,
      'plan_status',p.status,'verification_status',p.verification_status,
      'work_status',work.status
    ) AS provenance
  FROM field_inspections i
  JOIN fields f ON f.id=i.field_id AND f.enterprise_id=i.enterprise_id
  JOIN enterprises e ON e.id=i.enterprise_id
  LEFT JOIN LATERAL (
    SELECT cs.crop_type_id, ct.name_ru AS crop_name
    FROM crop_seasons cs JOIN crop_types ct ON ct.id=cs.crop_type_id
    WHERE cs.field_id=f.id
      AND cs.season_year <= extract(year from timezone('Asia/Tashkent', :as_of))::integer
    ORDER BY cs.season_year DESC, cs.id DESC LIMIT 1
  ) crop ON true
  LEFT JOIN LATERAL (
    SELECT p.* FROM agronomy_plans p
    WHERE p.inspection_id=i.id AND p.enterprise_id=i.enterprise_id
      AND (p.status NOT IN ('closed','cancelled','superseded') OR
           (p.status='closed' AND p.closed_at >= :as_of - interval '30 days'))
    ORDER BY CASE WHEN p.status NOT IN ('closed','cancelled','superseded') THEN 0 ELSE 1 END,
             p.id DESC LIMIT 1
  ) p ON true
  LEFT JOIN LATERAL (
    SELECT w.id,w.status,w.assigned_to_id,w.due_at,w.started_at,w.created_at
    FROM agronomy_work_items w
    WHERE w.plan_id=p.id AND w.cycle=p.cycle AND w.status IN ('planned','in_progress')
    ORDER BY CASE w.status WHEN 'in_progress' THEN 0 ELSE 1 END,
             w.due_at NULLS LAST,w.id LIMIT 1
  ) work ON true
  LEFT JOIN users assignee ON assignee.id=COALESCE(work.assigned_to_id,i.assigned_to_id)
  WHERE i.status IN ('pending','new','assigned','in_progress','submitted')
     OR (
       i.status='confirmed' AND (
         p.id IS NOT NULL OR NOT EXISTS (
           SELECT 1 FROM agronomy_plans historical_plan
           WHERE historical_plan.inspection_id=i.id
             AND historical_plan.enterprise_id=i.enterprise_id
         )
       )
     )
),
candidate_cases AS (
  SELECT
    'candidate:' || a.id::text,
    'candidate'::text,
    a.id::text,
    a.enterprise_id,e.name,a.field_id,f.name,crop.crop_type_id,crop.crop_name,
    lower(a.severity),
    CASE a.severity WHEN 'EXTREME' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MODERATE' THEN 2 ELSE 3 END,
    'needs_review'::text,NULL::integer,NULL::text,NULL::timestamptz,false,false,false,NULL::text,
    a.acquired_at,NULL::integer,NULL::bigint,a.id,NULL::integer,
    'Спутниковая аномалия ' || upper(a.index_code),
    jsonb_build_object('state',a.state,'severity',a.severity,'confidence',a.confidence,
      'index_code',a.index_code,'provider',a.provider,'rule_version',a.rule_version)
  FROM autonomous_anomaly_candidates a
  JOIN fields f ON f.id=a.field_id AND f.enterprise_id=a.enterprise_id
  JOIN enterprises e ON e.id=a.enterprise_id
  LEFT JOIN LATERAL (
    SELECT cs.crop_type_id,ct.name_ru AS crop_name
    FROM crop_seasons cs JOIN crop_types ct ON ct.id=cs.crop_type_id
    WHERE cs.field_id=f.id
      AND cs.season_year <= extract(year from timezone('Asia/Tashkent', :as_of))::integer
    ORDER BY cs.season_year DESC,cs.id DESC LIMIT 1
  ) crop ON true
  WHERE a.inspection_id IS NULL AND a.state IN ('NEW','CONFIRMED')
),
alert_cases AS (
  SELECT
    'alert:' || a.id::text,'alert'::text,a.id::text,
    f.enterprise_id,e.name,a.field_id,f.name,crop.crop_type_id,crop.crop_name,
    lower(a.severity),
    CASE lower(a.severity) WHEN 'critical' THEN 0 WHEN 'warning' THEN 2 ELSE 3 END,
    'needs_review'::text,NULL::integer,NULL::text,NULL::timestamptz,false,false,false,NULL::text,
    a.triggered_at,NULL::integer,NULL::bigint,NULL::bigint,a.id,
    a.title,
    jsonb_build_object('alert_type',a.alert_type,'severity',a.severity,'source',a.source)
  FROM alerts a
  JOIN fields f ON f.id=a.field_id
  JOIN enterprises e ON e.id=f.enterprise_id
  LEFT JOIN LATERAL (
    SELECT cs.crop_type_id,ct.name_ru AS crop_name
    FROM crop_seasons cs JOIN crop_types ct ON ct.id=cs.crop_type_id
    WHERE cs.field_id=f.id
      AND cs.season_year <= extract(year from timezone('Asia/Tashkent', :as_of))::integer
    ORDER BY cs.season_year DESC,cs.id DESC LIMIT 1
  ) crop ON true
  WHERE a.is_active=true AND NOT EXISTS (
    SELECT 1 FROM field_inspections i
    WHERE i.source_alert_id=a.id AND i.field_id=a.field_id
      AND i.status IN ('new','assigned','in_progress','submitted','confirmed')
  )
),
freshness_cases AS (
  SELECT
    'freshness:' || s.field_id::text || ':' || s.index_code,
    'freshness'::text,s.field_id::text || ':' || s.index_code,
    s.enterprise_id,e.name,s.field_id,f.name,crop.crop_type_id,crop.crop_name,
    CASE WHEN s.status='AGING' THEN 'info' ELSE 'warning' END,
    CASE WHEN s.status='AGING' THEN 3 ELSE 2 END,
    CASE WHEN s.status IN ('AGING','STALE') THEN 'stale' ELSE 'external_unavailable' END,
    NULL::integer,NULL::text,NULL::timestamptz,false,
    (s.status NOT IN ('AGING','STALE')),
    false,lower(s.status),s.updated_at,
    NULL::integer,NULL::bigint,NULL::bigint,NULL::integer,
    upper(s.index_code) || ': состояние спутниковых данных',
    jsonb_build_object('freshness_status',s.status,'index_code',s.index_code,
      'last_accepted_at',s.last_accepted_at,'failure_reason',s.last_failure_reason,
      'quality_reason',s.last_quality_reason)
  FROM satellite_field_freshness s
  JOIN fields f ON f.id=s.field_id AND f.enterprise_id=s.enterprise_id
  JOIN enterprises e ON e.id=s.enterprise_id
  LEFT JOIN LATERAL (
    SELECT cs.crop_type_id,ct.name_ru AS crop_name
    FROM crop_seasons cs JOIN crop_types ct ON ct.id=cs.crop_type_id
    WHERE cs.field_id=f.id
      AND cs.season_year <= extract(year from timezone('Asia/Tashkent', :as_of))::integer
    ORDER BY cs.season_year DESC,cs.id DESC LIMIT 1
  ) crop ON true
  WHERE s.status <> 'FRESH'
    AND NOT EXISTS (
      SELECT 1 FROM field_inspections active_inspection
      WHERE active_inspection.enterprise_id=s.enterprise_id
        AND active_inspection.field_id=s.field_id
        AND lower(COALESCE(active_inspection.source_index_name,''))=s.index_code
        AND (
          active_inspection.status IN ('pending','new','assigned','in_progress','submitted') OR
          (
            active_inspection.status='confirmed' AND (
              NOT EXISTS (
                SELECT 1 FROM agronomy_plans historical_plan
                WHERE historical_plan.inspection_id=active_inspection.id
                  AND historical_plan.enterprise_id=active_inspection.enterprise_id
              ) OR EXISTS (
                SELECT 1 FROM agronomy_plans current_plan
                WHERE current_plan.inspection_id=active_inspection.id
                  AND current_plan.enterprise_id=active_inspection.enterprise_id
                  AND (
                    current_plan.status NOT IN ('closed','cancelled','superseded') OR
                    (current_plan.status='closed' AND current_plan.closed_at >= :as_of - interval '30 days')
                  )
              )
            )
          )
        )
    )
    AND NOT EXISTS (
      SELECT 1 FROM autonomous_anomaly_candidates active_candidate
      WHERE active_candidate.enterprise_id=s.enterprise_id
        AND active_candidate.field_id=s.field_id
        AND active_candidate.index_code=s.index_code
        AND active_candidate.state IN ('NEW','CONFIRMED','INSPECTION_CREATED')
    )
),
latest_collection_run AS (
  SELECT r.* FROM satellite_collection_runs r
  ORDER BY r.started_at DESC,r.id DESC LIMIT 1
),
external_cases AS (
  SELECT
    'external:' || e.id::text || ':' || r.id::text,
    'external'::text,r.id::text,e.id,e.name,
    NULL::integer,NULL::text,NULL::integer,NULL::text,
    CASE WHEN r.status='failed' THEN 'critical' ELSE 'warning' END,
    CASE WHEN r.status='failed' THEN 0 ELSE 2 END,
    'external_unavailable'::text,
    NULL::integer,NULL::text,NULL::timestamptz,false,true,false,
    COALESCE(r.failure_category,r.provider_status,r.status),
    COALESCE(r.finished_at,r.heartbeat_at,r.started_at),
    NULL::integer,NULL::bigint,NULL::bigint,NULL::integer,
    'Внешний источник спутникового цикла недоступен',
    jsonb_build_object('run_id',r.id,'run_key',r.run_key,'run_status',r.status,
      'provider_status',r.provider_status,'failure_category',r.failure_category,
      'release_commit',r.release_commit,'rule_version',r.rule_version)
  FROM latest_collection_run r
  CROSS JOIN enterprises e
  WHERE (
    r.status IN ('degraded','failed') OR
    (r.status='running' AND r.heartbeat_at < :as_of - interval '6 hours')
  )
    AND EXISTS (SELECT 1 FROM fields field_scope WHERE field_scope.enterprise_id=e.id AND field_scope.is_active=true)
),
cases AS (
  SELECT * FROM inspection_cases
  UNION ALL SELECT * FROM candidate_cases
  UNION ALL SELECT * FROM alert_cases
  UNION ALL SELECT * FROM freshness_cases
  UNION ALL SELECT * FROM external_cases
),
notification_counts AS (
  SELECT enterprise_id,case_key,
    count(*) FILTER (WHERE status='unread')::integer AS unread_notifications,
    count(*) FILTER (WHERE status IN ('unread','read'))::integer AS active_notifications
  FROM operational_notifications
  WHERE recipient_user_id=:actor_user_id
  GROUP BY enterprise_id,case_key
)
"""


def _scope_conditions(actor: dict[str, Any], filters: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    requested_enterprise = filters.get("enterprise_id")
    if actor["role"] in TENANT_ROLES:
        if requested_enterprise is not None and requested_enterprise != actor["enterprise_id"]:
            raise HTTPException(404, "Enterprise not found")
        conditions.append("c.enterprise_id=:actor_enterprise_id")
        params["actor_enterprise_id"] = actor["enterprise_id"]
    elif requested_enterprise is not None:
        conditions.append("c.enterprise_id=:enterprise_id")
        params["enterprise_id"] = requested_enterprise
    if actor["role"] == "agronomist":
        requested_assignee = filters.get("assignee_id")
        if requested_assignee is not None and requested_assignee != actor["user_id"]:
            raise HTTPException(404, "Assignee not found")
        conditions.append(
            "(c.assignee_id=:actor_user_id OR EXISTS ("
            "SELECT 1 FROM agronomy_work_items scope_work "
            "WHERE scope_work.plan_id=c.plan_id AND scope_work.assigned_to_id=:actor_user_id "
            "AND scope_work.status IN ('planned','in_progress')))"
        )
    for key, column in (
        ("field_id", "c.field_id"),
        ("crop_type_id", "c.crop_type_id"),
        ("operational_status", "c.operational_status"),
    ):
        value = filters.get(key)
        if value is not None:
            conditions.append(f"{column}=:{key}")
            params[key] = value
    assignee_id = filters.get("assignee_id")
    if assignee_id is not None and actor["role"] != "agronomist":
        conditions.append(
            "(c.assignee_id=:assignee_id OR EXISTS ("
            "SELECT 1 FROM agronomy_work_items assignee_work "
            "WHERE assignee_work.plan_id=c.plan_id AND assignee_work.assigned_to_id=:assignee_id "
            "AND assignee_work.status IN ('planned','in_progress')))"
        )
        params["assignee_id"] = assignee_id
    source = filters.get("source")
    if source == "notification":
        conditions.append("COALESCE(n.active_notifications,0)>0")
    elif source:
        conditions.append("c.root_source=:root_source")
        params["root_source"] = source
    if filters.get("overdue") is not None:
        conditions.append("c.is_overdue=:overdue")
        params["overdue"] = filters["overdue"]
    if filters.get("blocked") is not None:
        conditions.append("c.blocked=:blocked")
        params["blocked"] = filters["blocked"]
    if filters.get("awaiting_verification") is not None:
        conditions.append("c.awaiting_verification=:awaiting_verification")
        params["awaiting_verification"] = filters["awaiting_verification"]
    if filters.get("external_state") == "stale":
        conditions.append("c.operational_status='stale'")
    elif filters.get("external_state") == "unavailable":
        conditions.append("c.operational_status='external_unavailable'")
    if filters.get("due_from") is not None:
        conditions.append("c.due_at>=:due_from")
        params["due_from"] = filters["due_from"]
    if filters.get("due_to") is not None:
        conditions.append("c.due_at<=:due_to")
        params["due_to"] = filters["due_to"]
    return conditions, params


def _priority_reasons(item: dict[str, Any], as_of: datetime) -> list[str]:
    reasons: list[str] = []
    if item["is_overdue"]:
        reasons.append("overdue_work")
    if item["priority_rank"] == 0:
        reasons.append("critical_source")
    due_at = item.get("due_at")
    if due_at is not None and not item["is_overdue"] and due_at <= as_of + timedelta(hours=24):
        reasons.append("due_within_24h")
    if item["operational_status"] == "awaiting_evidence":
        reasons.append("awaiting_assignee_evidence")
    if item["operational_status"] == "awaiting_verification":
        reasons.append("awaiting_satellite_observation")
    if item["operational_status"] in {"stale", "external_unavailable"}:
        reasons.append(item.get("external_state") or "external_context_unavailable")
    if item["blocked"] and "external_context_unavailable" not in reasons:
        reasons.append("blocked_context")
    return reasons or ["accepted_source_state"]


def _case_item(row: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    return {
        "case_key": row["case_key"],
        "source": row["root_source"],
        "source_id": str(row["source_id"]),
        "enterprise_id": row["enterprise_id"],
        "enterprise_name": row["enterprise_name"],
        "field_id": row["field_id"],
        "field_name": row["field_name"],
        "crop_type_id": row["crop_type_id"],
        "crop_name": row["crop_name"],
        "title": row["title"],
        "priority": row["source_priority"],
        "operational_status": row["operational_status"],
        "assignee_id": row["assignee_id"],
        "assignee_name": row["assignee_name"],
        "due_at": row["due_at"],
        "is_overdue": bool(row["is_overdue"]),
        "blocked": bool(row["blocked"]),
        "awaiting_verification": bool(row["awaiting_verification"]),
        "external_state": row["external_state"],
        "source_time": row["source_time"],
        "priority_reasons": _priority_reasons(row, as_of),
        "unread_notifications": int(row.get("unread_notifications") or 0),
        "active_notifications": int(row.get("active_notifications") or 0),
        "inspection_id": row["inspection_id"],
        "plan_id": row["plan_id"],
        "candidate_id": row["candidate_id"],
        "alert_id": row["alert_id"],
        "provenance": row.get("provenance") or {},
    }


def list_queue(db, user, filters: dict[str, Any]) -> dict[str, Any]:
    actor = _actor(user)
    as_of = datetime.now(timezone.utc)
    conditions, filter_params = _scope_conditions(actor, filters)
    where = " AND ".join(conditions) if conditions else "true"
    params = {"as_of": as_of, "actor_user_id": actor["user_id"], **filter_params}
    total = db.execute(
        text(CASES_CTE + "SELECT count(*) FROM cases c LEFT JOIN notification_counts n USING (enterprise_id,case_key) WHERE " + where),
        params,
    ).scalar_one()
    rows = _rows(
        db.execute(
            text(
                CASES_CTE
                + "SELECT c.*,COALESCE(n.unread_notifications,0) AS unread_notifications,"
                "COALESCE(n.active_notifications,0) AS active_notifications "
                "FROM cases c LEFT JOIN notification_counts n USING (enterprise_id,case_key) WHERE "
                + where
                + " ORDER BY c.is_overdue DESC,c.priority_rank,c.due_at NULLS LAST,"
                "CASE WHEN c.operational_status IN ('stale','external_unavailable') THEN 1 ELSE 0 END,"
                "c.source_time DESC,c.case_key LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": filters["limit"], "offset": filters["offset"]},
        )
    )
    return {
        "as_of": as_of,
        "items": [_case_item(row, as_of) for row in rows],
        "total": int(total),
        "limit": filters["limit"],
        "offset": filters["offset"],
    }


def summary(db, user, filters: dict[str, Any]) -> dict[str, Any]:
    actor = _actor(user)
    as_of = datetime.now(timezone.utc)
    conditions, filter_params = _scope_conditions(actor, filters)
    where = " AND ".join(conditions) if conditions else "true"
    row = _row(
        db.execute(
            text(
                CASES_CTE
                + "SELECT "
                "count(*) FILTER (WHERE c.operational_status<>'improved_closed')::integer AS active_situations,"
                "count(*) FILTER (WHERE c.is_overdue)::integer AS overdue_work,"
                "count(*) FILTER (WHERE c.blocked OR c.operational_status='external_unavailable')::integer AS blocked_or_external_unavailable,"
                "count(*) FILTER (WHERE c.operational_status IN ('awaiting_inspection','awaiting_review'))::integer AS awaiting_field_inspection,"
                "count(*) FILTER (WHERE c.operational_status='awaiting_work')::integer AS awaiting_work,"
                "count(*) FILTER (WHERE c.operational_status='awaiting_evidence')::integer AS awaiting_evidence,"
                "count(*) FILTER (WHERE c.operational_status='awaiting_verification')::integer AS awaiting_satellite_verification,"
                "count(*) FILTER (WHERE c.operational_status='improved_closed')::integer AS improved_or_closed_recent "
                "FROM cases c LEFT JOIN notification_counts n USING (enterprise_id,case_key) WHERE "
                + where
            ),
            {"as_of": as_of, "actor_user_id": actor["user_id"], **filter_params},
        )
    )
    return {"as_of": as_of, **(row or {})}


def filter_options(db, user, enterprise_id: int | None = None) -> dict[str, Any]:
    actor = _actor(user)
    if actor["role"] in TENANT_ROLES:
        if enterprise_id is not None and enterprise_id != actor["enterprise_id"]:
            raise HTTPException(404, "Enterprise not found")
        enterprise_id = actor["enterprise_id"]
    tenant = " WHERE id=:enterprise_id" if enterprise_id is not None else ""
    params = {"enterprise_id": enterprise_id}
    enterprises = _rows(db.execute(text("SELECT id,name AS label,NULL::integer AS enterprise_id FROM enterprises" + tenant + " ORDER BY name,id LIMIT 500"), params))
    field_conditions = []
    if enterprise_id is not None:
        field_conditions.append("f.enterprise_id=:enterprise_id")
    if actor["role"] == "agronomist":
        field_conditions.append(_agronomist_field_condition("f"))
        params["actor_user_id"] = actor["user_id"]
    field_where = " WHERE " + " AND ".join(field_conditions) if field_conditions else ""
    fields = _rows(db.execute(text("SELECT f.id,f.name AS label,f.enterprise_id FROM fields f" + field_where + " ORDER BY f.name,f.id LIMIT 500"), params))
    crop_conditions = list(field_conditions)
    crop_conditions.append("cs.season_year=extract(year from timezone('Asia/Tashkent',now()))::integer")
    crop_where = " WHERE " + " AND ".join(crop_conditions)
    crops = _rows(db.execute(text("SELECT DISTINCT ct.id,ct.name_ru AS label,NULL::integer AS enterprise_id FROM crop_types ct JOIN crop_seasons cs ON cs.crop_type_id=ct.id JOIN fields f ON f.id=cs.field_id" + crop_where + " ORDER BY label,ct.id LIMIT 200"), params))
    assignee_where = "AND u.enterprise_id=:enterprise_id" if enterprise_id is not None else ""
    assignees = _rows(db.execute(text("SELECT u.id,u.full_name AS label,u.enterprise_id FROM users u WHERE u.is_active=true AND u.role IN ('manager','agronomist') " + assignee_where + " ORDER BY u.full_name,u.id LIMIT 200"), params))
    if actor["role"] == "agronomist":
        assignees = [item for item in assignees if item["id"] == actor["user_id"]]
    return {"enterprises": enterprises, "fields": fields, "crops": crops, "assignees": assignees}


def _case_row(db, actor: dict[str, Any], case_key: str) -> tuple[dict[str, Any], datetime]:
    if not CASE_KEY.fullmatch(case_key):
        raise HTTPException(404, "Operational case not found")
    as_of = datetime.now(timezone.utc)
    conditions, params = _scope_conditions(actor, {})
    conditions.append("c.case_key=:case_key")
    row = _row(
        db.execute(
            text(
                CASES_CTE
                + "SELECT c.*,COALESCE(n.unread_notifications,0) AS unread_notifications,"
                "COALESCE(n.active_notifications,0) AS active_notifications "
                "FROM cases c LEFT JOIN notification_counts n USING (enterprise_id,case_key) WHERE "
                + " AND ".join(conditions)
            ),
            {"as_of": as_of, "actor_user_id": actor["user_id"], "case_key": case_key, **params},
        )
    )
    if not row:
        raise HTTPException(404, "Operational case not found")
    return row, as_of


def _source_snapshot(db, case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    source = case["root_source"]
    source_id = case["source_id"]
    if source == "inspection":
        row = _row(db.execute(text("""
          SELECT i.id,i.status,i.priority,i.source_kind,i.source_alert_id,i.source_provider,
            i.source_item_id,i.source_acquired_at,i.source_index_name,i.source_sampled_value,
            i.source_comparison_value,i.source_delta,i.source_geometry_hash,i.source_reason,
            i.assigned_to_id,i.due_at,i.created_at,i.updated_at,
            r.cause_code,r.cause_details,r.severity AS finding_severity,r.observations,
            r.recommended_action,r.affected_area_ha,r.affected_area_pct
          FROM field_inspections i LEFT JOIN inspection_results r ON r.inspection_id=i.id
          WHERE i.id=:id AND i.enterprise_id=:enterprise_id
        """), {"id": int(source_id), "enterprise_id": case["enterprise_id"]}))
        return row or {}, row
    if source == "candidate":
        row = _row(db.execute(text("""
          SELECT id,state,provider,scene_id,acquired_at,index_code,score,confidence,severity,
            magnitude,robust_deviation,affected_area_ha,affected_area_fraction,
            persistence_scenes,multi_index_agreement,data_quality,evidence,explanation,provenance,
            rule_version,version,created_at,updated_at
          FROM autonomous_anomaly_candidates
          WHERE id=:id AND enterprise_id=:enterprise_id
        """), {"id": int(source_id), "enterprise_id": case["enterprise_id"]}))
        return row or {}, None
    if source == "alert":
        row = _row(db.execute(text("""
          SELECT id,alert_type,severity,title,description,recommendation,triggered_value,
            threshold_value,triggered_at,acknowledged_at,is_active,source,source_key
          FROM alerts WHERE id=:id AND field_id=:field_id
        """), {"id": int(source_id), "field_id": case["field_id"]}))
        return row or {}, None
    if source == "freshness":
        index_code = str(source_id).split(":", 1)[1]
        row = _row(db.execute(text("""
          SELECT index_code,status,last_attempted_scene,last_attempted_at,last_accepted_scene,
            last_accepted_at,last_failure_reason,last_quality_reason,next_eligible_at,updated_at
          FROM satellite_field_freshness
          WHERE field_id=:field_id AND enterprise_id=:enterprise_id AND index_code=:index_code
        """), {"field_id": case["field_id"], "enterprise_id": case["enterprise_id"], "index_code": index_code}))
        return row or {}, None
    return case.get("provenance") or {}, None


def _case_timeline(db, case: dict[str, Any], actor: dict[str, Any]) -> list[dict[str, Any]]:
    params = {
        "enterprise_id": case["enterprise_id"],
        "inspection_id": case["inspection_id"],
        "plan_id": case["plan_id"],
        "candidate_id": case["candidate_id"],
        "alert_id": case["alert_id"],
        "case_key": case["case_key"],
        "actor_user_id": actor["user_id"],
    }
    return _rows(db.execute(text("""
      WITH timeline AS (
        SELECT 'inspection'::text AS source_kind,ev.inspection_id::text AS source_id,
          ev.event_type,ev.occurred_at,ev.actor_id,u.full_name AS actor_name,
          ev.event_metadata AS metadata
        FROM operational_audit_events ev LEFT JOIN users u ON u.id=ev.actor_id
        WHERE :inspection_id IS NOT NULL AND ev.inspection_id=:inspection_id
          AND ev.enterprise_id=:enterprise_id
        UNION ALL
        SELECT 'agronomy_plan',ev.plan_id::text,ev.event_type,ev.occurred_at,
          ev.actor_id,u.full_name,ev.detail
        FROM agronomy_events ev LEFT JOIN users u ON u.id=ev.actor_id
        WHERE :plan_id IS NOT NULL AND ev.plan_id=:plan_id AND ev.enterprise_id=:enterprise_id
        UNION ALL
        SELECT 'candidate',tr.candidate_id::text,'candidate_'||lower(tr.to_state),tr.created_at,
          tr.actor_id,u.full_name,jsonb_build_object('from_state',tr.from_state,'to_state',tr.to_state,'reason',tr.reason)
        FROM autonomous_anomaly_transitions tr LEFT JOIN users u ON u.id=tr.actor_id
        WHERE :candidate_id IS NOT NULL AND tr.candidate_id=:candidate_id AND tr.enterprise_id=:enterprise_id
        UNION ALL
        SELECT 'alert',a.id::text,'alert_triggered',a.triggered_at,NULL::integer,NULL::text,
          jsonb_build_object('severity',a.severity,'alert_type',a.alert_type)
        FROM alerts a WHERE :alert_id IS NOT NULL AND a.id=:alert_id
        UNION ALL
        SELECT 'notification',n.id::text,ev.event_type,ev.occurred_at,ev.actor_id,u.full_name,
          ev.event_metadata
        FROM operational_notification_events ev
        JOIN operational_notifications n ON n.id=ev.notification_id AND n.enterprise_id=ev.enterprise_id
        LEFT JOIN users u ON u.id=ev.actor_id
        WHERE n.case_key=:case_key AND n.enterprise_id=:enterprise_id
          AND n.recipient_user_id=:actor_user_id
      )
      SELECT * FROM timeline ORDER BY occurred_at DESC,source_kind,source_id DESC LIMIT 200
    """), params))


def detail(db, user, case_key: str, *, weather_loader=get_field_weather) -> dict[str, Any]:
    actor = _actor(user)
    case, as_of = _case_row(db, actor, case_key)
    field = None
    if case["field_id"] is not None:
        field = _row(db.execute(text("""
          SELECT f.id,f.enterprise_id,f.name,f.code,f.area_ha,f.centroid_lat,f.centroid_lon,
            f.irrigation_type,f.soil_type,ST_AsGeoJSON(f.geometry)::json AS geometry,
            cs.season_year,cs.variety,ct.id AS crop_type_id,ct.name_ru AS crop_name
          FROM fields f
          LEFT JOIN LATERAL (
            SELECT * FROM crop_seasons value WHERE value.field_id=f.id
              AND value.season_year<=extract(year from timezone('Asia/Tashkent',:as_of))::integer
            ORDER BY value.season_year DESC,value.id DESC LIMIT 1
          ) cs ON true
          LEFT JOIN crop_types ct ON ct.id=cs.crop_type_id
          WHERE f.id=:field_id AND f.enterprise_id=:enterprise_id
        """), {"as_of": as_of, "field_id": case["field_id"], "enterprise_id": case["enterprise_id"]}))
    snapshot, inspection = _source_snapshot(db, case)
    plan = None
    work_items: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    verifications: list[dict[str, Any]] = []
    if case["plan_id"] is not None:
        plan = _row(db.execute(text("""
          SELECT id,inspection_id,enterprise_id,field_id,decision,objective,expected_outcome,
            priority,policy_version,input_snapshot,recommendation,status,version,baseline,
            verification_status,cycle,created_at,approved_at,started_at,completed_at,closed_at,
            resolution_reason
          FROM agronomy_plans WHERE id=:plan_id AND enterprise_id=:enterprise_id
        """), {"plan_id": case["plan_id"], "enterprise_id": case["enterprise_id"]}))
        work_items = _rows(db.execute(text("""
          SELECT w.id,w.category,w.instruction,w.assigned_to_id,u.full_name AS assignee_name,
            w.planned_start_at,w.due_at,w.status,w.version,w.started_at,w.completed_at,
            w.result_note,ST_AsGeoJSON(w.geometry)::json AS geometry
          FROM agronomy_work_items w LEFT JOIN users u ON u.id=w.assigned_to_id
          WHERE w.plan_id=:plan_id AND w.enterprise_id=:enterprise_id
          ORDER BY w.cycle,w.id LIMIT 200
        """), {"plan_id": case["plan_id"], "enterprise_id": case["enterprise_id"]}))
        evidence = _rows(db.execute(text("""
          SELECT ev.id,ev.agronomy_work_item_id,ev.evidence_type,ev.provider,
            ev.original_filename,ev.media_type,ev.byte_size,ev.sha256,ev.captured_at,ev.created_at
          FROM inspection_evidence ev JOIN agronomy_work_items w ON w.id=ev.agronomy_work_item_id
          WHERE w.plan_id=:plan_id AND ev.enterprise_id=:enterprise_id AND ev.deleted_at IS NULL
          ORDER BY ev.id LIMIT 200
        """), {"plan_id": case["plan_id"], "enterprise_id": case["enterprise_id"]}))
        verifications = _rows(db.execute(text("""
          SELECT id,cycle,status,measurements,completed_at,post_date,created_at
          FROM agronomy_verifications WHERE plan_id=:plan_id AND enterprise_id=:enterprise_id
          ORDER BY id DESC LIMIT 100
        """), {"plan_id": case["plan_id"], "enterprise_id": case["enterprise_id"]}))
    notifications = _rows(db.execute(text("""
      SELECT id,notification_type,severity,title,message,status,version,available_at,due_at,
        created_at,updated_at,provenance
      FROM operational_notifications
      WHERE enterprise_id=:enterprise_id AND case_key=:case_key AND recipient_user_id=:user_id
      ORDER BY created_at DESC,id DESC LIMIT 100
    """), {"enterprise_id": case["enterprise_id"], "case_key": case_key, "user_id": actor["user_id"]}))
    field_object = SimpleNamespace(**field) if field else None
    weather = weather_context(field_object, weather_loader) if field_object else {
        "status": "unavailable", "provider": "open_meteo", "reason": "missing_field"
    }
    weather["causality_limitation"] = CAUSALITY_LIMITATION
    telematics = read_field_telematics(
        UnsupportedTelematicsProvider(),
        None,
        started_at=as_of - timedelta(hours=24),
        ended_at=as_of,
        limit=50,
    )
    item = _case_item(case, as_of)
    links = {
        "field": f"/fields/{case['field_id']}" if case["field_id"] else "",
        "inspection": f"/inspections/{case['inspection_id']}" if case["inspection_id"] else "",
        "agronomy_plan": f"/agronomy-plans/{case['plan_id']}" if case["plan_id"] else "",
        "monitoring": "/monitoring",
        "alerts": "/alerts",
    }
    return {
        "as_of": as_of,
        "case": item,
        "field": field,
        "source_snapshot": snapshot,
        "inspection": inspection,
        "agronomy_plan": plan,
        "work_items": work_items,
        "evidence": evidence,
        "verifications": verifications,
        "weather": weather,
        "telematics": telematics,
        "notifications": notifications,
        "timeline": _case_timeline(db, case, actor),
        "links": links,
        "causality_limitation": CAUSALITY_LIMITATION,
    }


def _agronomist_field_condition(alias: str) -> str:
    return (
        "(EXISTS (SELECT 1 FROM field_inspections scope_inspection "
        f"WHERE scope_inspection.enterprise_id={alias}.enterprise_id "
        f"AND scope_inspection.field_id={alias}.id "
        "AND scope_inspection.assigned_to_id=:actor_user_id "
        "AND (scope_inspection.status IN ('pending','new','assigned','in_progress','submitted') "
        "OR (scope_inspection.status='confirmed' AND (NOT EXISTS ("
        "SELECT 1 FROM agronomy_plans historical_plan "
        "WHERE historical_plan.inspection_id=scope_inspection.id "
        "AND historical_plan.enterprise_id=scope_inspection.enterprise_id) OR EXISTS ("
        "SELECT 1 FROM agronomy_plans current_plan "
        "WHERE current_plan.inspection_id=scope_inspection.id "
        "AND current_plan.enterprise_id=scope_inspection.enterprise_id "
        "AND (current_plan.status NOT IN ('closed','cancelled','superseded') "
        "OR (current_plan.status='closed' AND current_plan.closed_at>=now()-interval '30 days')))))) "
        "OR EXISTS (SELECT 1 FROM agronomy_work_items scope_work "
        f"WHERE scope_work.enterprise_id={alias}.enterprise_id "
        f"AND scope_work.field_id={alias}.id "
        "AND scope_work.assigned_to_id=:actor_user_id "
        "AND scope_work.status IN ('planned','in_progress')))"
    )


def _authorized_field(db, actor: dict[str, Any], field_id: int) -> dict[str, Any]:
    conditions = ["f.id=:field_id"]
    if actor["role"] in TENANT_ROLES:
        conditions.append("f.enterprise_id=:enterprise_id")
    if actor["role"] == "agronomist":
        conditions.append(_agronomist_field_condition("f"))
    params = {"field_id": field_id, "enterprise_id": actor["enterprise_id"]}
    params["actor_user_id"] = actor["user_id"]
    row = _row(db.execute(text(
        "SELECT f.id,f.enterprise_id FROM fields f WHERE " + " AND ".join(conditions)
    ), params))
    if not row:
        raise HTTPException(404, "Field not found")
    return row


FIELD_TIMELINE_SQL = r"""
WITH timeline AS (
  SELECT 'inspection'::text AS source_kind,ev.inspection_id::text AS source_id,
    ev.event_type,ev.occurred_at,ev.actor_id,u.full_name AS actor_name,ev.event_metadata AS metadata
  FROM operational_audit_events ev LEFT JOIN users u ON u.id=ev.actor_id
  WHERE ev.enterprise_id=:enterprise_id AND ev.field_id=:field_id
  UNION ALL
  SELECT 'agronomy_plan',ev.plan_id::text,ev.event_type,ev.occurred_at,ev.actor_id,u.full_name,ev.detail
  FROM agronomy_events ev LEFT JOIN users u ON u.id=ev.actor_id
  WHERE ev.enterprise_id=:enterprise_id AND ev.field_id=:field_id
  UNION ALL
  SELECT 'candidate',tr.candidate_id::text,'candidate_'||lower(tr.to_state),tr.created_at,
    tr.actor_id,u.full_name,jsonb_build_object('from_state',tr.from_state,'to_state',tr.to_state,'reason',tr.reason)
  FROM autonomous_anomaly_transitions tr
  JOIN autonomous_anomaly_candidates c ON c.id=tr.candidate_id
  LEFT JOIN users u ON u.id=tr.actor_id
  WHERE tr.enterprise_id=:enterprise_id AND c.field_id=:field_id
  UNION ALL
  SELECT 'alert',a.id::text,'alert_triggered',a.triggered_at,NULL::integer,NULL::text,
    jsonb_build_object('severity',a.severity,'alert_type',a.alert_type)
  FROM alerts a JOIN fields f ON f.id=a.field_id
  WHERE f.enterprise_id=:enterprise_id AND a.field_id=:field_id
  UNION ALL
  SELECT 'irrigation',ev.id::text,ev.event_type,ev.occurred_at,ev.recorded_by_id,u.full_name,
    jsonb_build_object('method_code',ev.method_code,'evidence_source',ev.evidence_source,
      'water_amount_mm',ev.water_amount_mm)
  FROM irrigation_events ev LEFT JOIN users u ON u.id=ev.recorded_by_id
  WHERE ev.enterprise_id=:enterprise_id AND ev.field_id=:field_id
  UNION ALL
  SELECT 'freshness',s.field_id::text||':'||s.index_code,'freshness_'||lower(s.status),s.updated_at,
    NULL::integer,NULL::text,jsonb_build_object('index_code',s.index_code,'status',s.status,
      'last_accepted_at',s.last_accepted_at)
  FROM satellite_field_freshness s
  WHERE s.enterprise_id=:enterprise_id AND s.field_id=:field_id
)
SELECT * FROM timeline
ORDER BY occurred_at DESC,source_kind,source_id DESC LIMIT :limit OFFSET :offset
"""


def field_timeline(db, user, field_id: int, *, limit: int, offset: int) -> dict[str, Any]:
    actor = _actor(user)
    field = _authorized_field(db, actor, field_id)
    items = _rows(db.execute(text(FIELD_TIMELINE_SQL), {
        "enterprise_id": field["enterprise_id"], "field_id": field_id,
        "limit": limit, "offset": offset,
    }))
    return {"field_id": field_id, "items": items, "limit": limit, "offset": offset}


def list_notifications(
    db,
    user,
    *,
    status: str | None,
    notification_type: str | None,
    case_key: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    actor = _actor(user)
    conditions = ["recipient_user_id=:user_id"]
    params: dict[str, Any] = {"user_id": actor["user_id"]}
    if status:
        conditions.append("status=:status")
        params["status"] = status
    else:
        conditions.append("status IN ('unread','read')")
    if notification_type:
        conditions.append("notification_type=:notification_type")
        params["notification_type"] = notification_type
    if case_key:
        if not CASE_KEY.fullmatch(case_key):
            raise HTTPException(404, "Operational case not found")
        conditions.append("case_key=:case_key")
        params["case_key"] = case_key
    where = " AND ".join(conditions)
    total = db.execute(text("SELECT count(*) FROM operational_notifications WHERE " + where), params).scalar_one()
    items = _rows(db.execute(text("""
      SELECT id,enterprise_id,field_id,case_key,source_kind,source_id,notification_type,
        severity,title,message,provenance,status,version,available_at,due_at,created_at,updated_at
      FROM operational_notifications WHERE """ + where + " ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset"), {**params, "limit": limit, "offset": offset}))
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


def _transition_fingerprint(notification_id: int, payload) -> str:
    value = {"notification_id": notification_id, **payload.model_dump(mode="json")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def transition_notification(db, user, notification_id: int, payload, command_key: str) -> dict[str, Any]:
    actor = _actor(user, write=True)
    actor_key = f"user:{actor['user_id']}"
    fingerprint = _transition_fingerprint(notification_id, payload)
    try:
        replay = _row(db.execute(text("""
          SELECT ev.fingerprint,n.id,n.status,n.version
          FROM operational_notification_events ev
          JOIN operational_notifications n ON n.id=ev.notification_id
          WHERE ev.actor_key=:actor_key AND ev.command_key=:command_key
            AND n.recipient_user_id=:user_id
        """), {"actor_key": actor_key, "command_key": command_key, "user_id": actor["user_id"]}))
        if replay:
            if replay["fingerprint"] != fingerprint:
                raise HTTPException(409, "Idempotency key payload conflict")
            return {"notification_id": replay["id"], "status": replay["status"], "version": replay["version"], "replayed": True}
        current = _row(db.execute(text("""
          SELECT * FROM operational_notifications
          WHERE id=:notification_id AND recipient_user_id=:user_id FOR UPDATE
        """), {"notification_id": notification_id, "user_id": actor["user_id"]}))
        if not current:
            raise HTTPException(404, "Notification not found")
        if current["version"] != payload.expected_version:
            raise HTTPException(409, "Version conflict")
        if payload.action == "read":
            if current["status"] != "unread":
                raise HTTPException(409, "Notification cannot be read in its current state")
            target = "read"
        else:
            if current["status"] not in {"unread", "read"}:
                raise HTTPException(409, "Notification cannot be dismissed in its current state")
            if not payload.reason:
                raise HTTPException(422, "Dismissal reason is required")
            target = "dismissed"
        now = datetime.now(timezone.utc)
        timestamps = {
            "read_at": now if target == "read" else current["read_at"],
            "dismissed_at": now if target == "dismissed" else None,
        }
        updated = _row(db.execute(text("""
          UPDATE operational_notifications SET status=:target,version=version+1,updated_at=:now,
            read_at=:read_at,dismissed_at=:dismissed_at
          WHERE id=:id AND recipient_user_id=:user_id AND version=:expected_version
          RETURNING id,enterprise_id,status,version
        """), {"target": target, "now": now, "read_at": timestamps["read_at"],
                 "dismissed_at": timestamps["dismissed_at"], "id": notification_id,
                 "user_id": actor["user_id"], "expected_version": payload.expected_version}))
        if not updated:
            raise HTTPException(409, "Version conflict")
        db.execute(text("""
          INSERT INTO operational_notification_events
            (notification_id,enterprise_id,actor_id,actor_key,command_key,fingerprint,event_type,
             from_status,to_status,reason,event_metadata,notification_version)
          VALUES (:notification_id,:enterprise_id,:actor_id,:actor_key,:command_key,:fingerprint,
            :event_type,:from_status,:to_status,:reason,'{}'::jsonb,:version)
        """), {"notification_id": notification_id, "enterprise_id": updated["enterprise_id"],
                 "actor_id": actor["user_id"], "actor_key": actor_key, "command_key": command_key,
                 "fingerprint": fingerprint, "event_type": target, "from_status": current["status"],
                 "to_status": target, "reason": payload.reason, "version": updated["version"]})
        db.commit()
        return {"notification_id": updated["id"], "status": updated["status"], "version": updated["version"], "replayed": False}
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Concurrent notification command conflict") from None
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
