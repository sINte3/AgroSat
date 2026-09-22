"""Bounded, deterministic reconciliation for Operational Center notifications.

The reconciler is intentionally not registered with FastAPI.  A standalone
process owns scheduling and calls :func:`reconcile_notifications` with an
explicit dry-run or apply mode.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


ADVISORY_LOCK_KEY = 2_210_016_221
DEFAULT_LIMIT = 200
MAX_LIMIT = 500


# One definition of a freshness notification's source cycle.
#
# Candidate generation and stale resolution MUST derive this identically. When
# they drift, a freshness status change leaves the old notification active
# (because the row is merely still non-FRESH) while candidate generation opens
# a second one for the new cycle, and the operator sees both. The H0-A
# production dry-run surfaced exactly that: three fields moving
# NEVER_COLLECTED -> AGING would have produced a stale critical alongside a new
# warning for the same field and index.
#
# Substituted into both statements below through @freshness_source_cycle@, so
# a typo fails loudly as a SQL syntax error rather than silently forking the
# definition. Both statements alias satellite_field_freshness as `s`.
FRESHNESS_SOURCE_CYCLE_SQL = (
    "'status:'||s.status||':accepted:'||"
    "COALESCE(extract(epoch from s.last_accepted_at)::bigint::text,'never')"
)
_FRESHNESS_CYCLE_TOKEN = "@freshness_source_cycle@"


NOTIFICATION_CANDIDATES_SQL = r"""
WITH facts AS (
  SELECT i.enterprise_id,i.field_id,'inspection:'||i.id::text AS case_key,
    'inspection'::text AS source_kind,i.id::text AS source_id,
    'new_critical'::text AS notification_type,'critical'::text AS severity,
    'oversight'::text AS recipient_mode,i.assigned_to_id,
    'urgent:v'||i.version::text AS source_cycle,NULL::timestamptz AS due_at,
    'Критическая ситуация: '||f.name AS title,
    'Срочный осмотр требует управленческого внимания.'::text AS message,
    jsonb_build_object('inspection_status',i.status,'priority',i.priority,
      'field_name',f.name,'source_time',i.updated_at) AS provenance
  FROM field_inspections i JOIN fields f ON f.id=i.field_id AND f.enterprise_id=i.enterprise_id
  WHERE i.status IN ('pending','new','assigned','in_progress','submitted')
    AND lower(COALESCE(i.priority,i.source_priority,'')) IN ('urgent','critical')

  UNION ALL
  SELECT a.enterprise_id,a.field_id,'candidate:'||a.id::text,'candidate',a.id::text,
    'new_critical','critical','oversight',NULL::integer,'extreme:v'||a.version::text,NULL::timestamptz,
    'Критическая спутниковая ситуация: '||f.name,
    'Кандидат EXTREME ожидает решения ответственного специалиста.',
    jsonb_build_object('candidate_state',a.state,'severity',a.severity,
      'index_code',a.index_code,'provider',a.provider,'acquired_at',a.acquired_at)
  FROM autonomous_anomaly_candidates a
  JOIN fields f ON f.id=a.field_id AND f.enterprise_id=a.enterprise_id
  WHERE a.inspection_id IS NULL AND a.state IN ('NEW','CONFIRMED') AND a.severity='EXTREME'

  UNION ALL
  SELECT f.enterprise_id,a.field_id,'alert:'||a.id::text,'alert',a.id::text,
    'new_critical','critical','oversight',NULL::integer,
    'triggered:'||extract(epoch from a.triggered_at)::bigint::text,NULL::timestamptz,
    'Критическое оповещение: '||f.name,
    'Активное критическое оповещение требует проверки.',
    jsonb_build_object('alert_type',a.alert_type,'severity',a.severity,
      'triggered_at',a.triggered_at,'source',a.source)
  FROM alerts a JOIN fields f ON f.id=a.field_id
  WHERE a.is_active=true AND lower(a.severity)='critical'
    AND NOT EXISTS (SELECT 1 FROM field_inspections i WHERE i.source_alert_id=a.id
      AND i.field_id=a.field_id AND i.status IN ('pending','new','assigned','in_progress','submitted','confirmed'))

  UNION ALL
  SELECT i.enterprise_id,i.field_id,'inspection:'||i.id::text,'inspection',i.id::text,
    'assignment','info','assignee',i.assigned_to_id,'assignment:v'||i.version::text,
    COALESCE(i.due_at,i.due_date::timestamptz),
    'Назначен осмотр: '||f.name,'Вы назначены ответственным за полевой осмотр.',
    jsonb_build_object('inspection_status',i.status,'priority',i.priority,'field_name',f.name)
  FROM field_inspections i JOIN fields f ON f.id=i.field_id AND f.enterprise_id=i.enterprise_id
  WHERE i.assigned_to_id IS NOT NULL AND i.status IN ('assigned','in_progress','submitted')

  UNION ALL
  SELECT w.enterprise_id,w.field_id,'inspection:'||w.inspection_id::text,'agronomy_work_item',w.id::text,
    'assignment','info','assignee',w.assigned_to_id,'assignment:v'||w.version::text,w.due_at,
    'Назначена работа: '||f.name,'Вы назначены ответственным за агрономическую работу.',
    jsonb_build_object('work_status',w.status,'plan_id',w.plan_id,'cycle',w.cycle,
      'category',w.category,'field_name',f.name)
  FROM agronomy_work_items w JOIN fields f ON f.id=w.field_id AND f.enterprise_id=w.enterprise_id
  WHERE w.assigned_to_id IS NOT NULL AND w.status IN ('planned','in_progress')

  UNION ALL
  SELECT i.enterprise_id,i.field_id,'inspection:'||i.id::text,'inspection',i.id::text,
    'due_soon','warning','both',i.assigned_to_id,
    'due:'||extract(epoch from COALESCE(i.due_at,i.due_date::timestamptz))::bigint::text,
    COALESCE(i.due_at,i.due_date::timestamptz),
    'Срок осмотра приближается: '||f.name,'До срока полевого осмотра осталось не более 24 часов.',
    jsonb_build_object('inspection_status',i.status,'field_name',f.name)
  FROM field_inspections i JOIN fields f ON f.id=i.field_id AND f.enterprise_id=i.enterprise_id
  WHERE i.status IN ('pending','new','assigned','in_progress','submitted')
    AND COALESCE(i.due_at,i.due_date::timestamptz)>:as_of
    AND COALESCE(i.due_at,i.due_date::timestamptz)<=:as_of+interval '24 hours'

  UNION ALL
  SELECT w.enterprise_id,w.field_id,'inspection:'||w.inspection_id::text,'agronomy_work_item',w.id::text,
    'due_soon','warning','both',w.assigned_to_id,
    'due:'||extract(epoch from w.due_at)::bigint::text,w.due_at,
    'Срок работы приближается: '||f.name,'До срока назначенной работы осталось не более 24 часов.',
    jsonb_build_object('work_status',w.status,'plan_id',w.plan_id,'cycle',w.cycle,
      'category',w.category,'field_name',f.name)
  FROM agronomy_work_items w JOIN fields f ON f.id=w.field_id AND f.enterprise_id=w.enterprise_id
  WHERE w.status IN ('planned','in_progress') AND w.due_at>:as_of
    AND w.due_at<=:as_of+interval '24 hours'

  UNION ALL
  SELECT i.enterprise_id,i.field_id,'inspection:'||i.id::text,'inspection',i.id::text,
    'overdue','critical','both',i.assigned_to_id,
    'due:'||extract(epoch from COALESCE(i.due_at,i.due_date::timestamptz))::bigint::text,
    COALESCE(i.due_at,i.due_date::timestamptz),
    'Просрочен осмотр: '||f.name,'Срок полевого осмотра истёк.',
    jsonb_build_object('inspection_status',i.status,'field_name',f.name)
  FROM field_inspections i JOIN fields f ON f.id=i.field_id AND f.enterprise_id=i.enterprise_id
  WHERE i.status IN ('pending','new','assigned','in_progress','submitted')
    AND COALESCE(i.due_at,i.due_date::timestamptz)<:as_of

  UNION ALL
  SELECT w.enterprise_id,w.field_id,'inspection:'||w.inspection_id::text,'agronomy_work_item',w.id::text,
    'overdue','critical','both',w.assigned_to_id,
    'due:'||extract(epoch from w.due_at)::bigint::text,w.due_at,
    'Просрочена работа: '||f.name,'Срок назначенной агрономической работы истёк.',
    jsonb_build_object('work_status',w.status,'plan_id',w.plan_id,'cycle',w.cycle,
      'category',w.category,'field_name',f.name)
  FROM agronomy_work_items w JOIN fields f ON f.id=w.field_id AND f.enterprise_id=w.enterprise_id
  WHERE w.status IN ('planned','in_progress') AND w.due_at<:as_of

  UNION ALL
  SELECT w.enterprise_id,w.field_id,'inspection:'||w.inspection_id::text,'agronomy_work_item',w.id::text,
    'missing_execution_evidence','critical','both',w.assigned_to_id,
    'evidence:v'||w.version::text||':due:'||extract(epoch from w.due_at)::bigint::text,w.due_at,
    'Нет результата выполнения: '||f.name,
    'Работа просрочена, а обязательная итоговая заметка ещё не зафиксирована.',
    jsonb_build_object('work_status',w.status,'plan_id',w.plan_id,'cycle',w.cycle,
      'category',w.category,'field_name',f.name)
  FROM agronomy_work_items w JOIN fields f ON f.id=w.field_id AND f.enterprise_id=w.enterprise_id
  WHERE w.status='in_progress' AND w.due_at<:as_of
    AND (w.result_note IS NULL OR btrim(w.result_note)='')

  UNION ALL
  SELECT p.enterprise_id,p.field_id,'inspection:'||p.inspection_id::text,'agronomy_plan',p.id::text,
    'awaiting_satellite_verification','info','oversight',NULL::integer,
    'cycle:'||p.cycle::text,NULL::timestamptz,
    'Ожидается спутниковая проверка: '||f.name,
    'Выполненная мера ожидает следующего допустимого спутникового наблюдения.',
    jsonb_build_object('plan_status',p.status,'verification_status',p.verification_status,
      'cycle',p.cycle,'completed_at',p.completed_at,'field_name',f.name)
  FROM agronomy_plans p JOIN fields f ON f.id=p.field_id AND f.enterprise_id=p.enterprise_id
  WHERE p.status='pending_verification'

  UNION ALL
  SELECT s.enterprise_id,s.field_id,'freshness:'||s.field_id::text||':'||s.index_code,
    'freshness',s.field_id::text||':'||s.index_code,'external_source_unavailable',
    CASE WHEN s.status IN ('AGING','STALE') THEN 'warning' ELSE 'critical' END,
    'oversight',NULL::integer,
    @freshness_source_cycle@,
    NULL::timestamptz,'Спутниковый контекст ограничен: '||f.name,
    'Актуальность спутниковых данных требует внимания; это состояние источника, а не агрономический диагноз.',
    jsonb_build_object('freshness_status',s.status,'index_code',s.index_code,
      'last_accepted_at',s.last_accepted_at,'failure_reason',s.last_failure_reason,
      'quality_reason',s.last_quality_reason,'field_name',f.name)
  FROM satellite_field_freshness s
  JOIN fields f ON f.id=s.field_id AND f.enterprise_id=s.enterprise_id
  WHERE s.status<>'FRESH'

  UNION ALL
  SELECT e.id,NULL::integer,'external:'||e.id::text||':'||r.id::text,
    'collection_run',r.id::text,'external_source_unavailable',
    CASE WHEN r.status='failed' THEN 'critical' ELSE 'warning' END,
    'oversight',NULL::integer,'run:'||r.id::text,NULL::timestamptz,
    'Сбой спутникового цикла: '||e.name,
    'Последний цикл сбора недоступен или не завершил heartbeat в допустимый срок.',
    jsonb_build_object('run_status',r.status,'provider_status',r.provider_status,
      'failure_category',r.failure_category,'started_at',r.started_at,
      'finished_at',r.finished_at,'release_commit',r.release_commit)
  FROM (SELECT value.* FROM satellite_collection_runs value ORDER BY value.started_at DESC,value.id DESC LIMIT 1) r
  CROSS JOIN enterprises e
  WHERE (r.status IN ('degraded','failed') OR
    (r.status='running' AND r.heartbeat_at<:as_of-interval '6 hours'))
    AND EXISTS (SELECT 1 FROM fields f WHERE f.enterprise_id=e.id AND f.is_active=true)
), recipients AS (
  SELECT facts.*,u.id AS recipient_user_id,u.role AS recipient_role_snapshot
  FROM facts JOIN users u ON u.is_active=true AND u.role IN ('admin','manager','agronomist')
    AND (u.role='admin' OR u.enterprise_id=facts.enterprise_id)
    AND (
      (facts.recipient_mode='assignee' AND u.id=facts.assigned_to_id) OR
      (facts.recipient_mode='oversight' AND (u.role='admin' OR u.role='manager')) OR
      (facts.recipient_mode='both' AND
        (u.id=facts.assigned_to_id OR u.role='admin' OR u.role='manager'))
    )
)
SELECT recipients.* FROM recipients
WHERE NOT EXISTS (
  SELECT 1 FROM operational_notifications existing
  WHERE existing.enterprise_id=recipients.enterprise_id
    AND existing.recipient_user_id=recipients.recipient_user_id
    AND existing.notification_type=recipients.notification_type
    AND existing.source_kind=recipients.source_kind
    AND existing.source_id=recipients.source_id
    AND existing.provenance->>'source_cycle'=recipients.source_cycle
)
ORDER BY enterprise_id,notification_type,source_kind,source_id,recipient_user_id
LIMIT :limit
""".replace(_FRESHNESS_CYCLE_TOKEN, FRESHNESS_SOURCE_CYCLE_SQL)


STALE_ACTIVE_SQL = r"""
SELECT n.* FROM operational_notifications n
WHERE n.status IN ('unread','read') AND NOT (
  (n.notification_type='new_critical' AND n.source_kind='inspection' AND EXISTS (
    SELECT 1 FROM field_inspections i WHERE i.id::text=n.source_id
      AND i.enterprise_id=n.enterprise_id
      AND i.status IN ('pending','new','assigned','in_progress','submitted')
      AND lower(COALESCE(i.priority,i.source_priority,'')) IN ('urgent','critical'))) OR
  (n.notification_type='new_critical' AND n.source_kind='candidate' AND EXISTS (
    SELECT 1 FROM autonomous_anomaly_candidates a WHERE a.id::text=n.source_id
      AND a.enterprise_id=n.enterprise_id AND a.inspection_id IS NULL
      AND a.state IN ('NEW','CONFIRMED') AND a.severity='EXTREME')) OR
  (n.notification_type='new_critical' AND n.source_kind='alert' AND EXISTS (
    SELECT 1 FROM alerts a JOIN fields f ON f.id=a.field_id
    WHERE a.id::text=n.source_id AND f.enterprise_id=n.enterprise_id
      AND a.is_active=true AND lower(a.severity)='critical'
      AND NOT EXISTS (SELECT 1 FROM field_inspections i WHERE i.source_alert_id=a.id
        AND i.field_id=a.field_id AND i.status IN ('pending','new','assigned','in_progress','submitted','confirmed')))) OR
  (n.notification_type='assignment' AND n.source_kind='inspection' AND EXISTS (
    SELECT 1 FROM field_inspections i WHERE i.id::text=n.source_id
      AND i.enterprise_id=n.enterprise_id AND i.assigned_to_id=n.recipient_user_id
      AND i.status IN ('assigned','in_progress','submitted'))) OR
  (n.notification_type='assignment' AND n.source_kind='agronomy_work_item' AND EXISTS (
    SELECT 1 FROM agronomy_work_items w WHERE w.id::text=n.source_id
      AND w.enterprise_id=n.enterprise_id AND w.assigned_to_id=n.recipient_user_id
      AND w.status IN ('planned','in_progress'))) OR
  (n.notification_type='due_soon' AND n.source_kind='inspection' AND EXISTS (
    SELECT 1 FROM field_inspections i WHERE i.id::text=n.source_id AND i.enterprise_id=n.enterprise_id
      AND i.status IN ('pending','new','assigned','in_progress','submitted')
      AND COALESCE(i.due_at,i.due_date::timestamptz)>:as_of
      AND COALESCE(i.due_at,i.due_date::timestamptz)<=:as_of+interval '24 hours')) OR
  (n.notification_type='due_soon' AND n.source_kind='agronomy_work_item' AND EXISTS (
    SELECT 1 FROM agronomy_work_items w WHERE w.id::text=n.source_id AND w.enterprise_id=n.enterprise_id
      AND w.status IN ('planned','in_progress') AND w.due_at>:as_of
      AND w.due_at<=:as_of+interval '24 hours')) OR
  (n.notification_type='overdue' AND n.source_kind='inspection' AND EXISTS (
    SELECT 1 FROM field_inspections i WHERE i.id::text=n.source_id AND i.enterprise_id=n.enterprise_id
      AND i.status IN ('pending','new','assigned','in_progress','submitted')
      AND COALESCE(i.due_at,i.due_date::timestamptz)<:as_of)) OR
  (n.notification_type='overdue' AND n.source_kind='agronomy_work_item' AND EXISTS (
    SELECT 1 FROM agronomy_work_items w WHERE w.id::text=n.source_id AND w.enterprise_id=n.enterprise_id
      AND w.status IN ('planned','in_progress') AND w.due_at<:as_of)) OR
  (n.notification_type='missing_execution_evidence' AND n.source_kind='agronomy_work_item' AND EXISTS (
    SELECT 1 FROM agronomy_work_items w WHERE w.id::text=n.source_id AND w.enterprise_id=n.enterprise_id
      AND w.status='in_progress' AND w.due_at<:as_of
      AND (w.result_note IS NULL OR btrim(w.result_note)=''))) OR
  (n.notification_type='awaiting_satellite_verification' AND n.source_kind='agronomy_plan' AND EXISTS (
    SELECT 1 FROM agronomy_plans p WHERE p.id::text=n.source_id
      AND p.enterprise_id=n.enterprise_id AND p.status='pending_verification')) OR
  (n.notification_type='external_source_unavailable' AND n.source_kind='freshness' AND EXISTS (
    SELECT 1 FROM satellite_field_freshness s
    WHERE s.enterprise_id=n.enterprise_id
      AND s.field_id::text||':'||s.index_code=n.source_id AND s.status<>'FRESH'
      AND n.provenance->>'source_cycle'=@freshness_source_cycle@)) OR
  (n.notification_type='external_source_unavailable' AND n.source_kind='collection_run' AND EXISTS (
    SELECT 1 FROM satellite_collection_runs r
    WHERE r.id::text=n.source_id AND (r.status IN ('degraded','failed') OR
      (r.status='running' AND r.heartbeat_at<:as_of-interval '6 hours'))
      AND r.id=(SELECT latest.id FROM satellite_collection_runs latest
        ORDER BY latest.started_at DESC,latest.id DESC LIMIT 1)))
)
ORDER BY n.id
LIMIT :limit
FOR UPDATE SKIP LOCKED
""".replace(_FRESHNESS_CYCLE_TOKEN, FRESHNESS_SOURCE_CYCLE_SQL)


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


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _notification_record(fact: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    identity = {
        "enterprise_id": fact["enterprise_id"],
        "recipient_user_id": fact["recipient_user_id"],
        "notification_type": fact["notification_type"],
        "source_kind": fact["source_kind"],
        "source_id": str(fact["source_id"]),
        "source_cycle": fact["source_cycle"],
    }
    provenance = dict(fact.get("provenance") or {})
    provenance.update(
        {
            "policy": "task221-operational-notifications-v1",
            "source_cycle": fact["source_cycle"],
        }
    )
    record = {
        "enterprise_id": int(fact["enterprise_id"]),
        "field_id": fact.get("field_id"),
        "recipient_user_id": int(fact["recipient_user_id"]),
        "recipient_role_snapshot": str(fact["recipient_role_snapshot"]),
        "case_key": str(fact["case_key"]),
        "source_kind": str(fact["source_kind"]),
        "source_id": str(fact["source_id"]),
        "notification_type": str(fact["notification_type"]),
        "severity": str(fact["severity"]),
        "title": str(fact["title"]).strip()[:255],
        "message": str(fact["message"]).strip()[:2000],
        "provenance": provenance,
        "dedupe_key": _hash(identity),
        "available_at": as_of,
        "due_at": fact.get("due_at"),
    }
    record["payload_fingerprint"] = _hash(record)
    return record


def _insert_notification(db, record: dict[str, Any]) -> dict[str, Any] | None:
    params = {**record, "provenance": json.dumps(record["provenance"], ensure_ascii=False, default=_json_default)}
    return _row(db.execute(text(r"""
      INSERT INTO operational_notifications
        (enterprise_id,field_id,recipient_user_id,recipient_role_snapshot,case_key,
         source_kind,source_id,notification_type,severity,title,message,provenance,
         dedupe_key,payload_fingerprint,status,version,available_at,due_at)
      VALUES
        (:enterprise_id,:field_id,:recipient_user_id,:recipient_role_snapshot,:case_key,
         :source_kind,:source_id,:notification_type,:severity,:title,:message,
         CAST(:provenance AS jsonb),:dedupe_key,:payload_fingerprint,'unread',1,
         :available_at,:due_at)
      ON CONFLICT (enterprise_id,dedupe_key) DO NOTHING
      RETURNING id,enterprise_id,status,version
    """), params))


def _append_created_event(db, inserted: dict[str, Any], record: dict[str, Any]) -> None:
    db.execute(text(r"""
      INSERT INTO operational_notification_events
        (notification_id,enterprise_id,actor_id,actor_key,command_key,fingerprint,
         event_type,from_status,to_status,reason,event_metadata,notification_version)
      VALUES (:notification_id,:enterprise_id,NULL,'system:reconciler',:command_key,
        :fingerprint,'created',NULL,'unread',NULL,CAST(:metadata AS jsonb),1)
    """), {
        "notification_id": inserted["id"],
        "enterprise_id": inserted["enterprise_id"],
        "command_key": record["dedupe_key"],
        "fingerprint": record["payload_fingerprint"],
        "metadata": json.dumps(
            {"notification_type": record["notification_type"], "case_key": record["case_key"]},
            ensure_ascii=False,
        ),
    })


def _resolve_notification(db, current: dict[str, Any], as_of: datetime) -> bool:
    command_key = _hash(
        {
            "action": "resolve",
            "notification_id": current["id"],
            "from_status": current["status"],
            "version": current["version"],
        }
    )
    fingerprint = _hash({"command_key": command_key, "reason": "source_not_actionable"})
    updated = _row(db.execute(text(r"""
      UPDATE operational_notifications
      SET status='resolved',version=version+1,updated_at=:as_of,resolved_at=:as_of
      WHERE id=:id AND version=:version AND status IN ('unread','read')
      RETURNING id,enterprise_id,version
    """), {"id": current["id"], "version": current["version"], "as_of": as_of}))
    if not updated:
        return False
    db.execute(text(r"""
      INSERT INTO operational_notification_events
        (notification_id,enterprise_id,actor_id,actor_key,command_key,fingerprint,
         event_type,from_status,to_status,reason,event_metadata,notification_version)
      VALUES (:notification_id,:enterprise_id,NULL,'system:reconciler',:command_key,
        :fingerprint,'resolved',:from_status,'resolved','source_not_actionable',
        '{}'::jsonb,:version)
    """), {
        "notification_id": current["id"],
        "enterprise_id": current["enterprise_id"],
        "command_key": command_key,
        "fingerprint": fingerprint,
        "from_status": current["status"],
        "version": updated["version"],
    })
    return True


def reconcile_notifications(
    db,
    *,
    apply: bool,
    limit: int = DEFAULT_LIMIT,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Plan or apply one bounded reconciliation transaction."""
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    candidates = _rows(db.execute(
        text(NOTIFICATION_CANDIDATES_SQL), {"as_of": as_of, "limit": limit}
    ))
    records = [_notification_record(value, as_of) for value in candidates]
    stale = _rows(db.execute(text(STALE_ACTIVE_SQL), {"as_of": as_of, "limit": limit}))
    summary = {
        "mode": "apply" if apply else "dry-run",
        "as_of": as_of.isoformat(),
        "limit": limit,
        "candidates": len(records),
        "stale_active": len(stale),
        "would_create": len(records),
        "would_resolve": len(stale),
        "created": 0,
        "duplicates": 0,
        "resolved": 0,
    }
    if not apply:
        db.rollback()
        return summary
    try:
        for record in records:
            inserted = _insert_notification(db, record)
            if inserted is None:
                summary["duplicates"] += 1
                continue
            _append_created_event(db, inserted, record)
            summary["created"] += 1
        for current in stale:
            if _resolve_notification(db, current, as_of):
                summary["resolved"] += 1
        db.commit()
        return summary
    except IntegrityError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def acquire_advisory_lock(db) -> bool:
    return bool(db.execute(
        text("SELECT pg_try_advisory_xact_lock(:lock_key)"), {"lock_key": ADVISORY_LOCK_KEY}
    ).scalar_one())
