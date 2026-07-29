"""Tenant-scoped explicit-SQL variable-rate draft workflow."""

from datetime import datetime, timezone
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.dependencies import ALLOWED_ROLES, TENANT_ROLES


SELECT_COLUMNS = """
 r.id,r.enterprise_id,r.field_id,f.name AS field_name,
 r.productivity_run_id,r.parent_recommendation_id,r.version,r.crop_code,
 r.season_year,r.recommendation_kind,r.rate_unit,r.minimum_rate,
 r.maximum_rate,r.zone_rates,r.equipment_capability,r.source_confidence,
 r.source_unzoned_area_ha,r.status,r.safety_acknowledged,r.notes,
 r.created_by_id,creator.full_name AS created_by_name,r.approved_by_id,
 approver.full_name AS approved_by_name,r.approved_at,r.rejected_by_id,
 rejector.full_name AS rejected_by_name,r.rejected_at,r.decision_note,
 r.created_at,r.updated_at,p.algorithm_version,p.selected_seasons,
 p.source_import_ids
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


def _actor(user, *, write=False, management=False):
    role = str(getattr(user.role, "value", user.role) or "").strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(403, "Unknown role")
    enterprise_id = getattr(user, "enterprise_id", None)
    if role in TENANT_ROLES and enterprise_id is None:
        raise HTTPException(403, "User has no enterprise_id")
    if write and role == "viewer":
        raise HTTPException(403, "Viewer is read-only")
    if management and role not in {"admin", "manager"}:
        raise HTTPException(403, "Management role required")
    return role, int(user.id), (
        int(enterprise_id) if enterprise_id is not None else None
    )


def _fingerprint(actor_id, payload):
    value = {
        "actor_id": actor_id,
        "payload": payload.model_dump(mode="json"),
    }
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()).hexdigest()


def _item(row):
    return {
        "id": row["id"],
        "enterprise_id": row["enterprise_id"],
        "field": {"id": row["field_id"], "name": row["field_name"]},
        "productivity_run_id": row["productivity_run_id"],
        "parent_recommendation_id": row["parent_recommendation_id"],
        "version": row["version"],
        "crop_code": row["crop_code"],
        "season_year": row["season_year"],
        "recommendation_kind": row["recommendation_kind"],
        "rate_unit": row["rate_unit"],
        "minimum_rate": row["minimum_rate"],
        "maximum_rate": row["maximum_rate"],
        "zone_rates": row["zone_rates"],
        "equipment_capability": row["equipment_capability"],
        "source_confidence": row["source_confidence"],
        "source_unzoned_area_ha": row["source_unzoned_area_ha"],
        "status": row["status"],
        "safety_acknowledged": row["safety_acknowledged"],
        "notes": row["notes"],
        "created_by": {
            "id": row["created_by_id"],
            "display_name": row["created_by_name"],
        },
        "approved_by": (
            {
                "id": row["approved_by_id"],
                "display_name": row["approved_by_name"],
            }
            if row["approved_by_id"] is not None else None
        ),
        "approved_at": row["approved_at"],
        "rejected_by": (
            {
                "id": row["rejected_by_id"],
                "display_name": row["rejected_by_name"],
            }
            if row["rejected_by_id"] is not None else None
        ),
        "rejected_at": row["rejected_at"],
        "decision_note": row["decision_note"],
        "source": {
            "algorithm_version": row["algorithm_version"],
            "selected_seasons": row["selected_seasons"],
            "source_import_ids": row["source_import_ids"],
        },
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "safety_statement": (
            "Requires agronomist validation; not an autonomous prescription."
        ),
    }


def _select(where):
    return text(
        f"""
        SELECT {SELECT_COLUMNS}
        FROM variable_rate_recommendations r
        JOIN fields f ON f.id=r.field_id AND f.enterprise_id=r.enterprise_id
        JOIN productivity_zone_runs p
          ON p.id=r.productivity_run_id
          AND p.enterprise_id=r.enterprise_id AND p.field_id=r.field_id
        JOIN users creator ON creator.id=r.created_by_id
        LEFT JOIN users approver ON approver.id=r.approved_by_id
        LEFT JOIN users rejector ON rejector.id=r.rejected_by_id
        WHERE {where}
        """
    )


def get_recommendation(db, user, recommendation_id):
    role, _, enterprise_id = _actor(user)
    where = "r.id=:recommendation_id"
    params = {"recommendation_id": recommendation_id}
    if role in TENANT_ROLES:
        where += " AND r.enterprise_id=:enterprise_id"
        params["enterprise_id"] = enterprise_id
    row = _one(db.execute(_select(where), params))
    if not row:
        raise HTTPException(404, "Variable-rate recommendation not found")
    return _item(row)


def list_recommendations(db, user, field, *, limit, offset):
    role, _, enterprise_id = _actor(user)
    if role in TENANT_ROLES and enterprise_id != int(field.enterprise_id):
        raise HTTPException(404, "Field not found")
    rows = _all(db.execute(
        _select(
            "r.enterprise_id=:enterprise_id AND r.field_id=:field_id "
            "ORDER BY r.created_at DESC,r.id DESC LIMIT :limit OFFSET :offset"
        ),
        {
            "enterprise_id": int(field.enterprise_id),
            "field_id": int(field.id),
            "limit": limit,
            "offset": offset,
        },
    ))
    return {"limit": limit, "offset": offset, "items": [_item(row) for row in rows]}


def create_recommendation(db, user, field, payload, key):
    role, actor_id, actor_enterprise = _actor(user, write=True)
    enterprise_id = int(field.enterprise_id)
    field_id = int(field.id)
    if role in TENANT_ROLES and actor_enterprise != enterprise_id:
        raise HTTPException(404, "Field not found")
    fingerprint = _fingerprint(actor_id, payload)
    try:
        existing = _one(db.execute(
            text(
                "SELECT id,request_fingerprint "
                "FROM variable_rate_recommendations "
                "WHERE enterprise_id=:enterprise_id "
                "AND created_by_id=:actor_id AND client_request_id=:key"
            ),
            {"enterprise_id": enterprise_id, "actor_id": actor_id, "key": key},
        ))
        if existing:
            if existing["request_fingerprint"] != fingerprint:
                raise HTTPException(409, "Idempotency key payload conflict")
            return False, get_recommendation(db, user, existing["id"])

        source = _one(db.execute(
            text(
                """
                SELECT p.id,p.confidence,p.area_delta_ha,p.result_status,
                  p.algorithm_version,count(z.id) AS zone_count
                FROM productivity_zone_runs p
                LEFT JOIN productivity_zones z
                  ON z.run_id=p.id AND z.enterprise_id=p.enterprise_id
                  AND z.field_id=p.field_id
                WHERE p.id=:run_id AND p.enterprise_id=:enterprise_id
                  AND p.field_id=:field_id
                GROUP BY p.id
                """
            ),
            {
                "run_id": payload.productivity_run_id,
                "enterprise_id": enterprise_id,
                "field_id": field_id,
            },
        ))
        if not source:
            raise HTTPException(404, "Productivity run not found")
        if (
            source["result_status"] != "ready"
            or source["algorithm_version"] != "yield_grid_stability_v1"
            or int(source["zone_count"]) < 1
        ):
            raise HTTPException(422, "Productivity run is not exportable")

        latest = _one(db.execute(
            text(
                """
                SELECT id,version,status
                FROM variable_rate_recommendations
                WHERE enterprise_id=:enterprise_id AND field_id=:field_id
                  AND recommendation_kind=:kind AND season_year=:season_year
                ORDER BY version DESC LIMIT 1 FOR UPDATE
                """
            ),
            {
                "enterprise_id": enterprise_id,
                "field_id": field_id,
                "kind": payload.recommendation_kind.value,
                "season_year": payload.season_year,
            },
        ))
        if payload.parent_recommendation_id:
            if not latest or latest["id"] != payload.parent_recommendation_id:
                raise HTTPException(409, "Parent is not the latest recommendation")
            if latest["status"] not in {"approved", "rejected"}:
                raise HTTPException(409, "Only a decided recommendation can be superseded")
        elif latest:
            raise HTTPException(409, "A new version must reference the latest recommendation")
        version = int(latest["version"]) + 1 if latest else 1
        if latest:
            db.execute(
                text(
                    "UPDATE variable_rate_recommendations "
                    "SET status='superseded',updated_at=:now "
                    "WHERE id=:parent_id AND enterprise_id=:enterprise_id"
                ),
                {
                    "parent_id": latest["id"],
                    "enterprise_id": enterprise_id,
                    "now": datetime.now(timezone.utc),
                },
            )
        inserted = _one(db.execute(
            text(
                """
                INSERT INTO variable_rate_recommendations
                (enterprise_id,field_id,productivity_run_id,
                 parent_recommendation_id,version,crop_code,season_year,
                 recommendation_kind,rate_unit,minimum_rate,maximum_rate,
                 zone_rates,equipment_capability,source_confidence,
                 source_unzoned_area_ha,status,safety_acknowledged,notes,
                 created_by_id,client_request_id,request_fingerprint)
                VALUES
                (:enterprise_id,:field_id,:run_id,:parent_id,:version,
                 :crop_code,:season_year,:kind,:unit,:minimum_rate,
                 :maximum_rate,CAST(:zone_rates AS jsonb),
                 CAST(:equipment AS jsonb),:source_confidence,
                 :source_unzoned_area_ha,'draft',true,:notes,:actor_id,:key,
                 :fingerprint)
                RETURNING id
                """
            ),
            {
                "enterprise_id": enterprise_id,
                "field_id": field_id,
                "run_id": payload.productivity_run_id,
                "parent_id": payload.parent_recommendation_id,
                "version": version,
                "crop_code": payload.crop_code,
                "season_year": payload.season_year,
                "kind": payload.recommendation_kind.value,
                "unit": payload.rate_unit.value,
                "minimum_rate": payload.minimum_rate,
                "maximum_rate": payload.maximum_rate,
                "zone_rates": json.dumps(payload.zone_rates.model_dump()),
                "equipment": json.dumps(
                    payload.equipment_capability.model_dump(mode="json")
                ),
                "source_confidence": source["confidence"],
                "source_unzoned_area_ha": source["area_delta_ha"],
                "notes": payload.notes,
                "actor_id": actor_id,
                "key": key,
                "fingerprint": fingerprint,
            },
        ))
        if latest:
            _event(
                db, latest["id"], enterprise_id, field_id, actor_id,
                "superseded", latest["status"], "superseded",
                {"replacement_id": inserted["id"]},
            )
        _event(
            db, inserted["id"], enterprise_id, field_id, actor_id,
            "created", None, "draft", {"version": version},
        )
        db.commit()
        return True, get_recommendation(db, user, inserted["id"])
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Concurrent recommendation conflict")
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def _event(db, recommendation_id, enterprise_id, field_id, actor_id,
           event_type, from_status, to_status, details):
    db.execute(
        text(
            """
            INSERT INTO variable_rate_recommendation_events
            (recommendation_id,enterprise_id,field_id,actor_id,event_type,
             from_status,to_status,details)
            VALUES
            (:recommendation_id,:enterprise_id,:field_id,:actor_id,:event_type,
             :from_status,:to_status,CAST(:details AS jsonb))
            """
        ),
        {
            "recommendation_id": recommendation_id,
            "enterprise_id": enterprise_id,
            "field_id": field_id,
            "actor_id": actor_id,
            "event_type": event_type,
            "from_status": from_status,
            "to_status": to_status,
            "details": json.dumps(details, sort_keys=True),
        },
    )


def decide(db, user, recommendation_id, payload, decision):
    _, actor_id, enterprise_id = _actor(user, write=True, management=True)
    now = datetime.now(timezone.utc)
    if decision not in {"approved", "rejected"}:
        raise ValueError("invalid decision")
    actor_column = "approved_by_id" if decision == "approved" else "rejected_by_id"
    time_column = "approved_at" if decision == "approved" else "rejected_at"
    tenant = "true" if enterprise_id is None else "enterprise_id=:enterprise_id"
    params = {
        "recommendation_id": recommendation_id,
        "actor_id": actor_id,
        "now": now,
        "note": payload.note,
        "expected_version": payload.expected_version,
    }
    if enterprise_id is not None:
        params["enterprise_id"] = enterprise_id
    try:
        row = _one(db.execute(
            text(
                f"""
                UPDATE variable_rate_recommendations
                SET status=:decision,{actor_column}=:actor_id,{time_column}=:now,
                    decision_note=:note,updated_at=:now
                WHERE id=:recommendation_id AND {tenant}
                  AND status='draft' AND version=:expected_version
                RETURNING id,enterprise_id,field_id
                """
            ),
            {**params, "decision": decision},
        ))
        if not row:
            existing = get_recommendation(db, user, recommendation_id)
            raise HTTPException(
                409,
                f"Recommendation is {existing['status']} at version {existing['version']}",
            )
        _event(
            db, row["id"], row["enterprise_id"], row["field_id"], actor_id,
            decision, "draft", decision, {"note": payload.note},
        )
        db.commit()
        return get_recommendation(db, user, recommendation_id)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def export_geojson(db, user, recommendation_id):
    item = get_recommendation(db, user, recommendation_id)
    rows = _all(db.execute(
        text(
            """
            SELECT z.id,z.zone_class,ST_AsGeoJSON(z.geometry)::json AS geometry,
              z.area_ha,z.mean_score
            FROM productivity_zones z
            WHERE z.run_id=:run_id AND z.enterprise_id=:enterprise_id
              AND z.field_id=:field_id
            ORDER BY CASE z.zone_class
              WHEN 'low' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END
            """
        ),
        {
            "run_id": item["productivity_run_id"],
            "enterprise_id": item["enterprise_id"],
            "field_id": item["field"]["id"],
        },
    ))
    features = [{
        "type": "Feature",
        "id": row["id"],
        "geometry": row["geometry"],
        "properties": {
            "zone_class": row["zone_class"],
            "rate": item["zone_rates"][row["zone_class"]],
            "rate_unit": item["rate_unit"],
            "area_ha": row["area_ha"],
            "mean_score": row["mean_score"],
            "recommendation_id": item["id"],
            "recommendation_version": item["version"],
            "recommendation_status": item["status"],
            "algorithm_version": item["source"]["algorithm_version"],
            "source_confidence": item["source_confidence"],
            "safety_statement": item["safety_statement"],
        },
    } for row in rows]
    return {
        "type": "FeatureCollection",
        "name": f"variable-rate-{item['id']}-v{item['version']}",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": features,
    }
