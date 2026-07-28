"""Explicit-SQL, tenant-scoped pixel anomaly read and inspection workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.dependencies import ALLOWED_ROLES, TENANT_ROLES


TASHKENT = ZoneInfo("Asia/Tashkent")
MAX_DATE_RANGE_DAYS = 366

LIST_COLUMNS = """
 a.id,a.field_id,f.name AS field_name,a.enterprise_id,
 e.name AS enterprise_name,a.index_code,r.current_observation_date,
 r.comparison_observation_date,a.area_ha,a.score,a.severity,
 a.persistence_count,a.classification,a.confidence,a.status,a.created_at,
 i.id AS inspection_id,i.status AS inspection_status,
 i.assigned_to_id AS inspection_assigned_to_id,
 i.due_date AS inspection_due_date
"""


@dataclass(frozen=True, slots=True)
class ActorScope:
    role: str
    user_id: int
    enterprise_id: int | None


def _actor(user, *, write: bool = False) -> ActorScope:
    role = (user.role or "").lower()
    actor = ActorScope(role, user.id, user.enterprise_id)
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
    return [
        row._mapping if hasattr(row, "_mapping") else row
        for row in result.fetchall()
    ]


def _tenant(actor: ActorScope, alias: str = "a") -> tuple[str, dict]:
    if actor.role in TENANT_ROLES:
        return f"{alias}.enterprise_id=:actor_enterprise_id", {
            "actor_enterprise_id": actor.enterprise_id
        }
    return "true", {}


def _field(row):
    return {
        "id": row["field_id"],
        "name": row["field_name"],
        "enterprise_id": row["enterprise_id"],
        "enterprise_name": row["enterprise_name"],
    }


def _inspection(row):
    if row["inspection_id"] is None:
        return None
    return {
        "id": row["inspection_id"],
        "status": row["inspection_status"],
        "assigned_to_id": row["inspection_assigned_to_id"],
        "due_date": row["inspection_due_date"],
    }


def _item(row):
    return {
        "id": row["id"],
        "field": _field(row),
        "index_code": row["index_code"],
        "current_observation_date": row["current_observation_date"],
        "comparison_observation_date": row["comparison_observation_date"],
        "area_ha": row["area_ha"],
        "score": row["score"],
        "severity": row["severity"],
        "persistence_count": row["persistence_count"],
        "classification": row["classification"],
        "confidence": row["confidence"],
        "status": row["status"],
        "created_at": row["created_at"],
        "inspection": _inspection(row),
    }


def _validate_enterprise_filter(actor: ActorScope, enterprise_id: int | None) -> int | None:
    if actor.role in TENANT_ROLES:
        if enterprise_id is not None and enterprise_id != actor.enterprise_id:
            raise HTTPException(403, "Foreign enterprise filter")
        return actor.enterprise_id
    return enterprise_id


def _validate_dates(date_from: date | None, date_to: date | None) -> None:
    if date_from and date_to:
        if date_from > date_to:
            raise HTTPException(422, "date_from must not exceed date_to")
        if (date_to - date_from).days > MAX_DATE_RANGE_DAYS:
            raise HTTPException(422, "date range exceeds 366 days")


def list_items(db, user, filters):
    actor = _actor(user)
    enterprise_id = _validate_enterprise_filter(actor, filters.get("enterprise_id"))
    _validate_dates(filters.get("date_from"), filters.get("date_to"))
    conditions = []
    params = {"limit": filters["limit"], "offset": filters["offset"]}
    if enterprise_id is not None:
        conditions.append("a.enterprise_id=:enterprise_id")
        params["enterprise_id"] = enterprise_id
    for column in ("field_id", "index_code", "status", "classification"):
        value = filters.get(column)
        if value is not None:
            conditions.append(f"a.{column}=:{column}")
            params[column] = value.value if hasattr(value, "value") else value
    if filters.get("date_from") is not None:
        conditions.append("r.current_observation_date>=:date_from")
        params["date_from"] = filters["date_from"]
    if filters.get("date_to") is not None:
        conditions.append("r.current_observation_date<=:date_to")
        params["date_to"] = filters["date_to"]
    where = " AND ".join(conditions) or "true"
    sql = text(
        f"""
        WITH filtered AS (
          SELECT {LIST_COLUMNS}
            FROM pixel_anomalies a
            JOIN pixel_anomaly_runs r ON r.id=a.run_id
            JOIN fields f ON f.id=a.field_id
            JOIN enterprises e ON e.id=a.enterprise_id
            LEFT JOIN pixel_anomaly_inspections link ON link.anomaly_id=a.id
            LEFT JOIN field_inspections i ON i.id=link.inspection_id
           WHERE {where}
        ),
        counted AS (SELECT count(*) AS total FROM filtered),
        paged AS (
          SELECT * FROM filtered
           ORDER BY current_observation_date DESC,id DESC
           LIMIT :limit OFFSET :offset
        )
        SELECT paged.*,counted.total
          FROM counted LEFT JOIN paged ON true
         ORDER BY current_observation_date DESC NULLS LAST,id DESC NULLS LAST
        """
    )
    try:
        rows = _all(db.execute(sql, params))
        total = int(rows[0]["total"]) if rows else 0
        return {
            "total": total,
            "limit": filters["limit"],
            "offset": filters["offset"],
            "items": [_item(row) for row in rows if row["id"] is not None],
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def field_summary(db, user, field_id: int):
    actor = _actor(user)
    tenant, params = _tenant(actor, "f")
    params["field_id"] = field_id
    sql = text(
        f"""
        WITH scoped_field AS (
          SELECT f.id AS field_id,f.name AS field_name,f.enterprise_id,
                 e.name AS enterprise_name
            FROM fields f JOIN enterprises e ON e.id=f.enterprise_id
           WHERE f.id=:field_id AND {tenant}
        ),
        anomaly_summary AS (
          SELECT count(a.id) AS total,
                 count(a.id) FILTER (WHERE a.status='open') AS open,
                 count(a.id) FILTER (WHERE a.status='inspection_created')
                   AS inspection_created,
                 count(a.id) FILTER (WHERE a.classification='persistent')
                   AS persistent,
                 count(a.id) FILTER (WHERE a.classification='recovering')
                   AS recovering,
                 max(r.current_observation_date) AS latest_observation_date
            FROM scoped_field sf
            LEFT JOIN pixel_anomalies a ON a.field_id=sf.field_id
              AND a.enterprise_id=sf.enterprise_id
            LEFT JOIN pixel_anomaly_runs r ON r.id=a.run_id
        ),
        latest AS (
          SELECT a.confidence
            FROM pixel_anomalies a JOIN pixel_anomaly_runs r ON r.id=a.run_id
            JOIN scoped_field sf ON sf.field_id=a.field_id
              AND sf.enterprise_id=a.enterprise_id
           ORDER BY r.current_observation_date DESC,a.id DESC LIMIT 1
        ),
        insufficient AS (
          SELECT count(r.id) AS count
            FROM pixel_anomaly_runs r JOIN scoped_field sf
              ON sf.field_id=r.field_id AND sf.enterprise_id=r.enterprise_id
           WHERE r.result_status='insufficient_data'
        )
        SELECT sf.*,s.*,latest.confidence AS latest_confidence,
               insufficient.count AS insufficient_data_runs
          FROM scoped_field sf CROSS JOIN anomaly_summary s
          CROSS JOIN insufficient LEFT JOIN latest ON true
        """
    )
    try:
        row = _one(db.execute(sql, params))
        if not row:
            raise HTTPException(404, "Field not found")
        return {
            "field": _field(row),
            "total": row["total"],
            "open": row["open"],
            "inspection_created": row["inspection_created"],
            "persistent": row["persistent"],
            "recovering": row["recovering"],
            "latest_observation_date": row["latest_observation_date"],
            "latest_confidence": row["latest_confidence"],
            "insufficient_data_runs": row["insufficient_data_runs"],
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def _detail_row(db, actor: ActorScope, anomaly_id: int, *, geometry: bool = False):
    tenant, params = _tenant(actor)
    params["anomaly_id"] = anomaly_id
    extra = ",ST_AsGeoJSON(a.geometry)::json AS geometry" if geometry else """
      ,a.algorithm_version,r.run_key,r.threshold_hash,r.thresholds,
       a.quality_summary,a.provenance,r.reason_codes
    """
    row = _one(
        db.execute(
            text(
                f"""
                SELECT {LIST_COLUMNS}{extra}
                  FROM pixel_anomalies a
                  JOIN pixel_anomaly_runs r ON r.id=a.run_id
                  JOIN fields f ON f.id=a.field_id
                  JOIN enterprises e ON e.id=a.enterprise_id
                  LEFT JOIN pixel_anomaly_inspections link ON link.anomaly_id=a.id
                  LEFT JOIN field_inspections i ON i.id=link.inspection_id
                 WHERE a.id=:anomaly_id AND {tenant}
                """
            ),
            params,
        )
    )
    if not row:
        raise HTTPException(404, "Pixel anomaly not found")
    return row


def detail(db, user, anomaly_id: int):
    actor = _actor(user)
    try:
        row = _detail_row(db, actor, anomaly_id)
        return {
            **_item(row),
            "algorithm_version": row["algorithm_version"],
            "run_key": row["run_key"],
            "threshold_hash": row["threshold_hash"],
            "thresholds": row["thresholds"],
            "quality_summary": row["quality_summary"],
            "provenance": row["provenance"],
            "reason_codes": row["reason_codes"],
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def geometry(db, user, anomaly_id: int):
    actor = _actor(user)
    try:
        row = _detail_row(db, actor, anomaly_id, geometry=True)
        return {
            "type": "Feature",
            "id": row["id"],
            "geometry": row["geometry"],
            "properties": {
                "field_id": row["field_id"],
                "index_code": row["index_code"],
                "classification": row["classification"],
                "status": row["status"],
                "area_ha": row["area_ha"],
                "score": row["score"],
                "confidence": row["confidence"],
            },
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def _fingerprint(actor: ActorScope, anomaly_id: int, payload, assigned_to_id: int | None):
    value = payload.model_dump(mode="json")
    value.update(
        {
            "actor_id": actor.user_id,
            "anomaly_id": anomaly_id,
            "assigned_to_id": assigned_to_id,
        }
    )
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _inspection_key(actor: ActorScope, idempotency_key: str) -> str:
    digest = hashlib.sha256(
        f"{actor.user_id}:{idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"anomaly-{digest[:56]}"


def _due(value: date | None) -> None:
    if value is not None and value < datetime.now(TASHKENT).date():
        raise HTTPException(422, "due_date cannot be in the past")


def _assignee(db, assignee_id: int | None, enterprise_id: int) -> None:
    if assignee_id is None:
        return
    row = _one(
        db.execute(
            text(
                "SELECT id FROM users WHERE id=:user_id "
                "AND enterprise_id=:enterprise_id "
                "AND role='agronomist' AND is_active=true"
            ),
            {"user_id": assignee_id, "enterprise_id": enterprise_id},
        )
    )
    if not row:
        raise HTTPException(422, "Assignee is not an eligible agronomist")


def _link_replay(db, actor: ActorScope, anomaly_id: int, key: str, fingerprint: str):
    tenant, params = _tenant(actor, "link")
    params.update({"anomaly_id": anomaly_id, "actor_id": actor.user_id, "key": key})
    row = _one(
        db.execute(
            text(
                f"""
                SELECT link.anomaly_id,link.request_fingerprint,
                       i.id AS inspection_id,i.status AS inspection_status,
                       i.assigned_to_id AS inspection_assigned_to_id,
                       i.due_date AS inspection_due_date
                  FROM pixel_anomaly_inspections link
                  JOIN field_inspections i ON i.id=link.inspection_id
                 WHERE link.anomaly_id=:anomaly_id
                   AND link.created_by_id=:actor_id
                   AND link.idempotency_key=:key AND {tenant}
                """
            ),
            params,
        )
    )
    if not row:
        return None
    if row["request_fingerprint"] != fingerprint:
        raise HTTPException(409, "Idempotency key payload conflict")
    return {
        "created": False,
        "anomaly_id": row["anomaly_id"],
        "anomaly_status": "inspection_created",
        "inspection": {
            "id": row["inspection_id"],
            "status": row["inspection_status"],
            "assigned_to_id": row["inspection_assigned_to_id"],
            "due_date": row["inspection_due_date"],
        },
    }


def create_inspection(db, user, anomaly_id: int, payload, idempotency_key: str):
    actor = _actor(user, write=True)
    _due(payload.due_date)
    assigned_to_id = (
        actor.user_id
        if actor.role == "agronomist" and payload.assigned_to_id is None
        else payload.assigned_to_id
    )
    if actor.role == "agronomist" and assigned_to_id != actor.user_id:
        raise HTTPException(403, "Agronomists may assign only themselves")
    fingerprint = _fingerprint(actor, anomaly_id, payload, assigned_to_id)
    try:
        replay = _link_replay(
            db,
            actor,
            anomaly_id,
            idempotency_key,
            fingerprint,
        )
        if replay:
            return replay
        tenant, params = _tenant(actor)
        params["anomaly_id"] = anomaly_id
        anomaly = _one(
            db.execute(
                text(
                    f"""
                    SELECT a.id,a.field_id,a.enterprise_id,a.status,a.severity,
                           r.current_observation_date
                      FROM pixel_anomalies a
                      JOIN pixel_anomaly_runs r ON r.id=a.run_id
                     WHERE a.id=:anomaly_id AND {tenant}
                     FOR UPDATE
                    """
                ),
                params,
            )
        )
        if not anomaly:
            raise HTTPException(404, "Pixel anomaly not found")
        if anomaly["status"] != "open":
            raise HTTPException(409, "Pixel anomaly is not open")
        _assignee(db, assigned_to_id, anomaly["enterprise_id"])
        active = _one(
            db.execute(
                text(
                    "SELECT id FROM field_inspections "
                    "WHERE field_id=:field_id AND enterprise_id=:enterprise_id "
                    "AND status IN ('pending','in_progress')"
                ),
                {
                    "field_id": anomaly["field_id"],
                    "enterprise_id": anomaly["enterprise_id"],
                },
            )
        )
        if active:
            raise HTTPException(409, f"Active inspection exists: {active['id']}")
        inspection_key = _inspection_key(actor, idempotency_key)
        title = payload.title or "Inspect satellite anomaly"
        inserted = _one(
            db.execute(
                text(
                    """
                    INSERT INTO field_inspections (
                      field_id,enterprise_id,created_by_id,assigned_to_id,
                      client_request_id,request_fingerprint,source,
                      source_reason_codes,title,instructions,due_date
                    ) VALUES (
                      :field_id,:enterprise_id,:actor_id,:assigned_to_id,
                      :inspection_key,:fingerprint,'manual','[]'::jsonb,
                      :title,:instructions,:due_date
                    ) RETURNING id,status,assigned_to_id,due_date
                    """
                ),
                {
                    "field_id": anomaly["field_id"],
                    "enterprise_id": anomaly["enterprise_id"],
                    "actor_id": actor.user_id,
                    "assigned_to_id": assigned_to_id,
                    "inspection_key": inspection_key,
                    "fingerprint": fingerprint,
                    "title": title,
                    "instructions": payload.instructions,
                    "due_date": payload.due_date,
                },
            )
        )
        db.execute(
            text(
                """
                INSERT INTO pixel_anomaly_inspections (
                  anomaly_id,inspection_id,enterprise_id,field_id,created_by_id,
                  idempotency_key,request_fingerprint
                ) VALUES (
                  :anomaly_id,:inspection_id,:enterprise_id,:field_id,:actor_id,
                  :idempotency_key,:fingerprint
                )
                """
            ),
            {
                "anomaly_id": anomaly_id,
                "inspection_id": inserted["id"],
                "enterprise_id": anomaly["enterprise_id"],
                "field_id": anomaly["field_id"],
                "actor_id": actor.user_id,
                "idempotency_key": idempotency_key,
                "fingerprint": fingerprint,
            },
        )
        updated = _one(
            db.execute(
                text(
                    "UPDATE pixel_anomalies SET status='inspection_created',"
                    "updated_at=now() WHERE id=:anomaly_id "
                    "AND enterprise_id=:enterprise_id AND status='open' "
                    "RETURNING id"
                ),
                {
                    "anomaly_id": anomaly_id,
                    "enterprise_id": anomaly["enterprise_id"],
                },
            )
        )
        if not updated:
            raise HTTPException(409, "Concurrent anomaly state conflict")
        db.commit()
        return {
            "created": True,
            "anomaly_id": anomaly_id,
            "anomaly_status": "inspection_created",
            "inspection": {
                "id": inserted["id"],
                "status": inserted["status"],
                "assigned_to_id": inserted["assigned_to_id"],
                "due_date": inserted["due_date"],
            },
        }
    except IntegrityError:
        db.rollback()
        replay = _link_replay(
            db,
            actor,
            anomaly_id,
            idempotency_key,
            fingerprint,
        )
        if replay:
            return replay
        raise HTTPException(409, "Concurrent anomaly inspection conflict")
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
