"""Database boundary for TASK_219 monitoring cycles and operator review."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.dependencies import ALLOWED_ROLES, TENANT_ROLES
from database import SessionLocal
from services.autonomous_anomaly_engine import RulePolicy, freshness_status, plan_automatic_inspections


ADVISORY_LOCK_KEY = 871_320_219
RULE_VERSION = "r3-e-v1"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
STATES = {"NEW", "CONFIRMED", "DISMISSED", "INSPECTION_CREATED", "RESOLVED", "SUPERSEDED"}


@dataclass
class ApplyRun:
    session: object
    run_id: uuid.UUID
    run_key: str


def _row(result):
    if hasattr(result, "mappings"):
        return result.mappings().first()
    value = result.fetchone()
    return value._mapping if value is not None and hasattr(value, "_mapping") else value


def _rows(result):
    if hasattr(result, "mappings"):
        return list(result.mappings().all())
    return [value._mapping if hasattr(value, "_mapping") else value for value in result.fetchall()]


def _actor(user, *, write: bool = False):
    role = str(getattr(getattr(user, "role", None), "value", getattr(user, "role", ""))).lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(403, "Unknown role")
    if role in TENANT_ROLES and user.enterprise_id is None:
        raise HTTPException(403, "Tenant user has no enterprise_id")
    if write and role != "admin":
        raise HTTPException(403, "Administrator role required")
    return role, user.id, user.enterprise_id


def _tenant(role: str, enterprise_id: int | None, alias: str) -> tuple[str, dict]:
    if role in TENANT_ROLES:
        return f" AND {alias}.enterprise_id=:actor_enterprise_id", {"actor_enterprise_id": enterprise_id}
    return "", {}


def begin_apply_run(*, run_key: str, release_commit: str, audit_identity: str) -> ApplyRun:
    if not SHA40.fullmatch(release_commit):
        raise RuntimeError("apply requires an exact 40-character release commit")
    session = SessionLocal()
    try:
        acquired = session.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
        ).scalar()
        if acquired is not True:
            raise RuntimeError("postgresql advisory lock contention")
        run_id = uuid.uuid4()
        session.execute(text("""
            INSERT INTO satellite_collection_runs
              (id,run_key,mode,status,release_commit,rule_version,audit_identity,
               started_at,heartbeat_at,provider_status,retry_state,counters)
            VALUES (:id,:run_key,'apply','running',:release,:rule,:identity,
                    now(),now(),'pending','{}'::jsonb,'{}'::jsonb)
            ON CONFLICT (run_key) DO NOTHING
        """), {
            "id": run_id, "run_key": run_key, "release": release_commit,
            "rule": RULE_VERSION, "identity": audit_identity[:255],
        })
        existing = _row(session.execute(text(
            "SELECT id,status FROM satellite_collection_runs WHERE run_key=:run_key"
        ), {"run_key": run_key}))
        if existing["id"] != run_id and existing["status"] == "running":
            raise RuntimeError("collection run identity is already active")
        session.commit()
        return ApplyRun(session=session, run_id=existing["id"], run_key=run_key)
    except Exception:
        session.rollback()
        try:
            session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
        except Exception:
            pass
        session.close()
        raise


def heartbeat(run: ApplyRun, counters: dict | None = None) -> None:
    run.session.execute(text("""
        UPDATE satellite_collection_runs
           SET heartbeat_at=now(), counters=COALESCE(CAST(:counters AS jsonb),counters), updated_at=now()
         WHERE id=:id AND status='running'
    """), {"id": run.run_id, "counters": json.dumps(counters) if counters is not None else None})
    run.session.commit()


def refresh_freshness(run: ApplyRun, *, last_outcome: str | None = None) -> int:
    outcome = last_outcome if last_outcome in {"cloud_blocked", "provider_degraded", "quality_blocked"} else None
    result = run.session.execute(text("""
        WITH codes(index_code) AS (VALUES ('ndvi'),('savi'),('evi'),('ndmi'),('ndre')),
        active AS (
          SELECT f.id AS field_id,f.enterprise_id,c.index_code
            FROM fields f CROSS JOIN codes c WHERE f.is_active=true
        ), latest AS (
          SELECT a.*,o.scene_key,o.accepted_at
            FROM active a
            LEFT JOIN LATERAL (
              SELECT 'ndvi_record:'||n.id AS scene_key,n.captured_date::timestamptz AS accepted_at
                FROM ndvi_records n
               WHERE a.index_code='ndvi' AND n.field_id=a.field_id
                 AND n.mean_ndvi BETWEEN -1 AND 1
                 AND COALESCE(n.valid_pixels_pct,0)>=60 AND COALESCE(n.cloud_cover_pct,101)<=30
              UNION ALL
              SELECT 'satellite_index_record:'||s.id,s.captured_date::timestamptz
                FROM satellite_index_records s
               WHERE s.index_code=a.index_code AND s.field_id=a.field_id
                 AND s.mean_value BETWEEN -1 AND 1
                 AND COALESCE(s.valid_pixels_pct,0)>=60 AND COALESCE(s.cloud_cover_pct,101)<=30
              ORDER BY accepted_at DESC LIMIT 1
            ) o ON true
        )
        INSERT INTO satellite_field_freshness
          (enterprise_id,field_id,index_code,status,last_accepted_scene,last_accepted_at,
           last_failure_reason,last_run_id,updated_at)
        SELECT enterprise_id,field_id,index_code,
               CASE WHEN accepted_at IS NULL THEN
                 CASE :outcome WHEN 'cloud_blocked' THEN 'CLOUD_BLOCKED'
                   WHEN 'provider_degraded' THEN 'PROVIDER_DEGRADED'
                   WHEN 'quality_blocked' THEN 'QUALITY_BLOCKED' ELSE 'NEVER_COLLECTED' END
                 WHEN current_date-accepted_at::date<=10 THEN 'FRESH'
                 WHEN current_date-accepted_at::date<=20 THEN 'AGING' ELSE 'STALE' END,
               scene_key,accepted_at,:outcome,:run_id,now()
          FROM latest
        ON CONFLICT (field_id,index_code) DO UPDATE SET
          enterprise_id=excluded.enterprise_id,status=excluded.status,
          last_accepted_scene=excluded.last_accepted_scene,last_accepted_at=excluded.last_accepted_at,
          last_failure_reason=excluded.last_failure_reason,last_run_id=excluded.last_run_id,updated_at=now()
        RETURNING id
    """), {"run_id": run.run_id, "outcome": outcome})
    count = len(result.fetchall())
    run.session.commit()
    return count


def reconcile_pixel_candidates(run: ApplyRun) -> dict[str, int | bool]:
    """Promote eligible accepted pixel zones; replay is protected by a DB unique key."""
    inserted = run.session.execute(text("""
        INSERT INTO autonomous_anomaly_candidates
          (enterprise_id,field_id,run_id,rule_version,provider,scene_id,acquired_at,index_code,
           source_key,zone_key,geometry,score,confidence,severity,magnitude,robust_deviation,
           affected_area_ha,affected_area_fraction,persistence_scenes,multi_index_agreement,
           data_quality,evidence,explanation,state,cooldown_until)
        SELECT a.enterprise_id,a.field_id,:collection_run,:rule,
               COALESCE(r.provenance->>'provider','sentinel'),
               r.current_record_type||':'||COALESCE(r.current_ndvi_record_id,r.current_satellite_index_record_id)::text,
               r.current_observation_date::timestamptz,r.index_code,
               a.zone_key,
               a.zone_key,a.geometry,a.score,a.confidence,
               CASE a.severity WHEN 'critical' THEN 'EXTREME' WHEN 'high' THEN 'HIGH' WHEN 'medium' THEN 'MODERATE' ELSE 'LOW' END,
               COALESCE((a.provenance->>'median_drop')::float,0),
               GREATEST(0,COALESCE((a.provenance->>'median_drop')::float,0)/0.02),
               a.area_ha,a.area_ha/NULLIF(f.area_ha,0),a.persistence_count,
               LEAST(4,(SELECT count(DISTINCT r2.index_code) FROM pixel_anomalies a2
                 JOIN pixel_anomaly_runs r2 ON r2.id=a2.run_id
                 WHERE a2.field_id=a.field_id AND a2.enterprise_id=a.enterprise_id
                   AND a2.id<>a.id AND r2.index_code<>r.index_code
                   AND ABS(r2.current_observation_date-r.current_observation_date)<=3
                   AND ST_Intersects(a2.geometry,a.geometry))),
               LEAST(COALESCE((r.quality_summary->>'current_valid_pixels_pct')::float,0)/100.0,1),
               jsonb_build_object('pixel_anomaly_id',a.id,'pixel_run_id',r.id,
                 'classification',a.classification,'quality',a.quality_summary,'provenance',a.provenance),
               'Pixel anomaly retained from deterministic raster analysis; automatic action requires persistence and high confidence.',
               'NEW',r.current_observation_date::timestamptz+interval '14 days'
          FROM pixel_anomalies a
          JOIN pixel_anomaly_runs r ON r.id=a.run_id
          JOIN fields f ON f.id=a.field_id AND f.enterprise_id=a.enterprise_id
         WHERE a.area_ha>=GREATEST(0.25,f.area_ha*0.01)
           AND a.status IN ('open','inspection_created')
        ON CONFLICT (enterprise_id,field_id,index_code,scene_id,zone_key,rule_version) DO NOTHING
        RETURNING id
    """), {"collection_run": run.run_id, "rule": RULE_VERSION}).fetchall()
    eligible = _rows(run.session.execute(text("""
      SELECT a.id,a.field_id,a.source_key,a.zone_key,a.confidence,a.cooldown_until,
             ((a.confidence>=0.85 AND a.persistence_scenes>=2 AND a.multi_index_agreement>=2)
               OR (a.confidence>=0.85 AND a.severity='EXTREME')) AS automatic,
             EXISTS (SELECT 1 FROM autonomous_anomaly_candidates prior
               WHERE prior.enterprise_id=a.enterprise_id AND prior.source_key=a.source_key
                 AND prior.zone_key=a.zone_key AND prior.id<>a.id
                 AND prior.cooldown_until>a.acquired_at) AS replay
        FROM autonomous_anomaly_candidates a WHERE a.run_id=:run AND a.state='NEW'
    """), {"run":run.run_id}))
    active_fields = run.session.execute(text("SELECT count(*) FROM fields WHERE is_active=true")).scalar_one()
    existing = {
        (row["source_key"],row["zone_key"]) for row in _rows(run.session.execute(text("""
          SELECT source_key,zone_key FROM autonomous_anomaly_candidates
           WHERE inspection_id IS NOT NULL AND state='INSPECTION_CREATED'
        """)))
    }
    plan = plan_automatic_inspections(
        eligible, active_field_count=active_fields, open_source_zone_keys=existing,
    )
    automatic = 0
    if plan.candidate_ids:
        admin_id = run.session.execute(text("""
          SELECT id FROM users WHERE lower(role::text)='admin' AND is_active=true ORDER BY id LIMIT 1
        """)).scalar_one_or_none()
        if admin_id is None:
            raise RuntimeError("automatic inspection requires one active administrator audit identity")
        for candidate_id in plan.candidate_ids:
            item = _row(run.session.execute(text("""
              SELECT a.*,f.name AS field_name FROM autonomous_anomaly_candidates a
              JOIN fields f ON f.id=a.field_id WHERE a.id=:id FOR UPDATE
            """), {"id":candidate_id}))
            key = f"auto-monitoring-{candidate_id}"
            fingerprint = hashlib.sha256(f"{candidate_id}|{item['source_key']}|automatic".encode()).hexdigest()
            inspection = _row(run.session.execute(text("""
              INSERT INTO field_inspections
                (field_id,enterprise_id,created_by_id,updated_by_id,client_request_id,request_fingerprint,
                 source,source_priority,source_reason_codes,title,instructions,status,source_kind,
                 source_reason,priority,source_snapshot_locked)
              VALUES (:field,:enterprise,:actor,:actor,:key,:fingerprint,'manual','high','[]'::jsonb,
                 :title,:instructions,'new','manual',:reason,'high',true)
              ON CONFLICT (client_request_id) DO NOTHING RETURNING id
            """), {"field":item["field_id"],"enterprise":item["enterprise_id"],"actor":admin_id,
                     "key":key,"fingerprint":fingerprint,
                     "title":f"Автоматическая проверка спутниковой аномалии: {item['field_name']}"[:255],
                     "instructions":item["explanation"],
                     "reason":"Высокая уверенность, подтверждённая устойчивость и согласие индексов."}))
            if inspection:
                run.session.execute(text("""
                  UPDATE autonomous_anomaly_candidates SET state='INSPECTION_CREATED',inspection_id=:inspection,
                    version=version+1,updated_at=now() WHERE id=:id AND state='NEW'
                """), {"inspection":inspection["id"],"id":candidate_id})
                run.session.execute(text("""
                  INSERT INTO autonomous_anomaly_transitions
                    (candidate_id,enterprise_id,from_state,to_state,actor_id,reason,expected_version)
                  VALUES (:id,:enterprise,'NEW','INSPECTION_CREATED',:actor,:reason,1)
                """), {"id":candidate_id,"enterprise":item["enterprise_id"],"actor":admin_id,
                         "reason":"Automatic high-confidence persistent anomaly policy."})
                automatic += 1
    run.session.commit()
    return {"inserted_candidates": len(inserted), "spike_guard_triggered": plan.spike_guard_triggered,
            "automatic_inspections": automatic, "automatic_cap_suppressed": len(plan.suppressed_cap_ids),
            "cooldown_or_open_suppressed": len(plan.suppressed_duplicate_ids)}


def finish_apply_run(run: ApplyRun, *, exit_code: int, provider_status: str, counters: dict, failure_category: str | None) -> None:
    status = "succeeded" if exit_code == 0 and provider_status != "degraded" else "degraded" if exit_code in {0, 1} else "failed"
    try:
        run.session.execute(text("""
            UPDATE satellite_collection_runs SET status=:status,provider_status=:provider,
              failure_category=:failure,counters=CAST(:counters AS jsonb),heartbeat_at=now(),
              finished_at=now(),updated_at=now() WHERE id=:id
        """), {"id": run.run_id, "status": status, "provider": provider_status,
               "failure": failure_category, "counters": json.dumps(counters)})
        run.session.commit()
    finally:
        try:
            run.session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
        finally:
            run.session.close()


def status_summary(db, user):
    role, _, enterprise_id = _actor(user)
    if role != "admin":
        raise HTTPException(403, "Administrator role required")
    latest = _row(db.execute(text("""
      SELECT id,run_key,mode,status,release_commit,rule_version,started_at,heartbeat_at,
             finished_at,provider_status,failure_category,counters
        FROM satellite_collection_runs ORDER BY started_at DESC LIMIT 1
    """)))
    freshness = _rows(db.execute(text("""
      SELECT status,count(*)::int AS count FROM satellite_field_freshness GROUP BY status ORDER BY status
    """)))
    return {"latest_run": dict(latest) if latest else None,
            "freshness": {row["status"]: row["count"] for row in freshness},
            "provider_affects_web_readiness": False}


def list_freshness(db, user, *, enterprise_filter=None, field_id=None, status=None, index_code=None, limit=200, offset=0):
    role, _, enterprise_id = _actor(user)
    if role in TENANT_ROLES and enterprise_filter not in {None, enterprise_id}:
        raise HTTPException(404, "Enterprise not found")
    conditions, params = ["true"], {"limit": limit, "offset": offset}
    effective = enterprise_id if role in TENANT_ROLES else enterprise_filter
    for value, column, key in ((effective,"s.enterprise_id","enterprise_id"),(field_id,"s.field_id","field_id"),(status,"s.status","status"),(index_code,"s.index_code","index_code")):
        if value is not None:
            conditions.append(f"{column}=:{key}"); params[key] = value
    rows = _rows(db.execute(text(f"""
      SELECT s.*,f.name AS field_name,e.name AS enterprise_name
        FROM satellite_field_freshness s JOIN fields f ON f.id=s.field_id
        JOIN enterprises e ON e.id=s.enterprise_id
       WHERE {' AND '.join(conditions)} ORDER BY s.status,f.name,s.index_code LIMIT :limit OFFSET :offset
    """), params))
    return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}


def list_candidates(db, user, *, enterprise_filter=None, field_id=None, state=None, limit=100, offset=0):
    role, _, enterprise_id = _actor(user)
    if role in TENANT_ROLES and enterprise_filter not in {None, enterprise_id}:
        raise HTTPException(404, "Enterprise not found")
    effective = enterprise_id if role in TENANT_ROLES else enterprise_filter
    conditions, params = ["true"], {"limit": limit, "offset": offset}
    for value, column, key in ((effective,"a.enterprise_id","enterprise_id"),(field_id,"a.field_id","field_id"),(state,"a.state","state")):
        if value is not None:
            conditions.append(f"{column}=:{key}"); params[key] = value
    rows = _rows(db.execute(text(f"""
      SELECT a.id,a.enterprise_id,e.name AS enterprise_name,a.field_id,f.name AS field_name,
             a.rule_version,a.provider,a.scene_id,a.acquired_at,a.index_code,a.score,a.confidence,
             a.severity,a.magnitude,a.robust_deviation,a.affected_area_ha,a.affected_area_fraction,
             a.persistence_scenes,a.multi_index_agreement,a.data_quality,a.evidence,a.explanation,
             a.state,a.cooldown_until,a.inspection_id,a.version,a.created_at,a.updated_at,
             ST_AsGeoJSON(a.geometry)::json AS geometry
        FROM autonomous_anomaly_candidates a JOIN fields f ON f.id=a.field_id
        JOIN enterprises e ON e.id=a.enterprise_id
       WHERE {' AND '.join(conditions)} ORDER BY a.created_at DESC,a.id DESC LIMIT :limit OFFSET :offset
    """), params))
    return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}


def transition_candidate(db, user, candidate_id: int, *, action: str, reason: str, expected_version: int):
    role, actor_id, _ = _actor(user, write=True)
    target = {"confirm":"CONFIRMED","dismiss":"DISMISSED","resolve":"RESOLVED"}.get(action)
    if target is None:
        raise HTTPException(422, "Unsupported transition")
    if not 3 <= len(reason.strip()) <= 2000:
        raise HTTPException(422, "Reason must contain 3 to 2000 characters")
    try:
        current = _row(db.execute(text("SELECT id,enterprise_id,state,version FROM autonomous_anomaly_candidates WHERE id=:id FOR UPDATE"), {"id": candidate_id}))
        if not current:
            raise HTTPException(404, "Candidate not found")
        allowed = {"confirm":{"NEW"},"dismiss":{"NEW","CONFIRMED"},"resolve":{"CONFIRMED","INSPECTION_CREATED"}}[action]
        if current["state"] not in allowed or current["version"] != expected_version:
            raise HTTPException(409, "Candidate version or state conflict")
        db.execute(text("""
          UPDATE autonomous_anomaly_candidates SET state=:target,version=version+1,updated_at=now(),
            resolved_at=CASE WHEN :target IN ('DISMISSED','RESOLVED') THEN now() ELSE NULL END
          WHERE id=:id
        """), {"id": candidate_id, "target": target})
        db.execute(text("""
          INSERT INTO autonomous_anomaly_transitions
            (candidate_id,enterprise_id,from_state,to_state,actor_id,reason,expected_version)
          VALUES (:id,:enterprise,:old,:target,:actor,:reason,:version)
        """), {"id":candidate_id,"enterprise":current["enterprise_id"],"old":current["state"],
                 "target":target,"actor":actor_id,"reason":reason.strip(),"version":expected_version})
        db.commit()
        return {"id": candidate_id, "state": target, "version": expected_version + 1}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise


def create_inspection(db, user, candidate_id: int, *, reason: str, expected_version: int):
    _, actor_id, _ = _actor(user, write=True)
    try:
        item = _row(db.execute(text("""
          SELECT a.*,f.name AS field_name FROM autonomous_anomaly_candidates a
          JOIN fields f ON f.id=a.field_id WHERE a.id=:id FOR UPDATE
        """), {"id":candidate_id}))
        if not item:
            raise HTTPException(404,"Candidate not found")
        if item["state"] not in {"NEW","CONFIRMED"} or item["version"] != expected_version:
            raise HTTPException(409,"Candidate version or state conflict")
        existing = _row(db.execute(text("""
          SELECT inspection_id FROM autonomous_anomaly_candidates
           WHERE enterprise_id=:enterprise AND source_key=:source AND zone_key=:zone
             AND inspection_id IS NOT NULL LIMIT 1
        """), {"enterprise":item["enterprise_id"],"source":item["source_key"],"zone":item["zone_key"]}))
        if existing:
            raise HTTPException(409,"An inspection already exists for this anomaly source and zone")
        key = f"autonomous-{candidate_id}"
        fingerprint = hashlib.sha256(f"{candidate_id}|{actor_id}|{item['source_key']}".encode()).hexdigest()
        inspection = _row(db.execute(text("""
          INSERT INTO field_inspections
            (field_id,enterprise_id,created_by_id,updated_by_id,client_request_id,request_fingerprint,
             source,source_priority,source_reason_codes,title,instructions,status,source_kind,
             source_reason,priority,source_snapshot_locked)
          VALUES (:field,:enterprise,:actor,:actor,:key,:fingerprint,'manual','high','[]'::jsonb,
             :title,:instructions,'new','manual',:reason,'high',true)
          RETURNING id
        """), {"field":item["field_id"],"enterprise":item["enterprise_id"],"actor":actor_id,
                 "key":key,"fingerprint":fingerprint,"title":f"Проверить спутниковую аномалию: {item['field_name']}"[:255],
                 "instructions":item["explanation"],"reason":reason.strip()}))
        db.execute(text("""
          UPDATE autonomous_anomaly_candidates SET state='INSPECTION_CREATED',inspection_id=:inspection,
            version=version+1,updated_at=now() WHERE id=:id
        """), {"inspection":inspection["id"],"id":candidate_id})
        db.execute(text("""
          INSERT INTO autonomous_anomaly_transitions
            (candidate_id,enterprise_id,from_state,to_state,actor_id,reason,expected_version)
          VALUES (:id,:enterprise,:old,'INSPECTION_CREATED',:actor,:reason,:version)
        """), {"id":candidate_id,"enterprise":item["enterprise_id"],"old":item["state"],
                 "actor":actor_id,"reason":reason.strip(),"version":expected_version})
        db.commit()
        return {"id":candidate_id,"state":"INSPECTION_CREATED","version":expected_version+1,"inspection_id":inspection["id"]}
    except IntegrityError:
        db.rollback(); raise HTTPException(409,"Concurrent inspection conflict") from None
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise
