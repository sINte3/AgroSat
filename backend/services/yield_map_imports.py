"""Deterministic validation and explicit-SQL persistence for yield maps."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from statistics import median

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.dependencies import ALLOWED_ROLES, TENANT_ROLES


MAX_ROWS = 5000
MIN_YIELD_T_HA = 0.01
MAX_YIELD_T_HA = 100.0
MAD_Z_LIMIT = 6.0
IMPORT_SELECT = """
SELECT i.id, i.enterprise_id, i.field_id, f.name AS field_name,
 i.season_year, i.crop_code, i.schema_code, i.source_filename,
 i.source_sha256, i.source_provider, i.machine_id, i.machine_model,
 i.input_unit, i.normalized_unit, i.total_rows, i.accepted_rows,
 i.rejected_rows, i.yield_min_t_ha, i.yield_max_t_ha,
 i.yield_mean_t_ha, i.bounds, i.provenance, i.status,
 i.created_by_id, creator.full_name AS created_by_name, i.created_at
FROM yield_map_imports i
JOIN fields f
 ON f.id=i.field_id AND f.enterprise_id=i.enterprise_id
JOIN users creator ON creator.id=i.created_by_id
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


def _actor(user, *, write=False):
    role = str(getattr(user.role, "value", user.role) or "").strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(403, "Unknown role")
    enterprise_id = getattr(user, "enterprise_id", None)
    if role in TENANT_ROLES and enterprise_id is None:
        raise HTTPException(403, "User has no enterprise_id")
    if write and role == "viewer":
        raise HTTPException(403, "Viewer is read-only")
    return {
        "role": role,
        "user_id": int(user.id),
        "enterprise_id": int(enterprise_id) if enterprise_id is not None else None,
    }


def _parse_observed_at(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _reject(source_row: int, code: str, detail: str):
    return {
        "source_row": source_row,
        "reason_code": code,
        "detail": detail,
    }


def _normalized_yield(value: float, unit: str) -> float:
    return float(value) / 1000.0 if unit == "kg_ha" else float(value)


def _canonical_hash(value) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_rows(payload) -> dict:
    """Normalize rows without database access; invalid rows stay inspectable."""
    accepted = []
    rejected = []
    seen_machine_points = set()
    unit = payload.yield_unit.value

    for source_row, row in enumerate(payload.rows, 1):
        lon = float(row.longitude)
        lat = float(row.latitude)
        value = _normalized_yield(row.yield_value, unit)
        observed_at = _parse_observed_at(row.observed_at)
        code = None
        detail = None
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            code, detail = "invalid_coordinate", "Coordinate is outside EPSG:4326"
        elif not math.isfinite(value) or not MIN_YIELD_T_HA <= value <= MAX_YIELD_T_HA:
            code, detail = "yield_out_of_range", "Normalized yield must be 0.01..100 t/ha"
        elif observed_at is None:
            code, detail = "invalid_observed_at", "Timestamp must be timezone-aware ISO 8601"
        elif row.speed_kph is not None and not 0 <= float(row.speed_kph) <= 200:
            code, detail = "speed_out_of_range", "Speed must be 0..200 km/h"
        elif row.moisture_pct is not None and not 0 <= float(row.moisture_pct) <= 100:
            code, detail = "moisture_out_of_range", "Moisture must be 0..100 percent"
        elif row.machine_point_id and row.machine_point_id in seen_machine_points:
            code, detail = "duplicate_machine_point_id", "Duplicate point identity"

        if code:
            rejected.append(_reject(source_row, code, detail))
            continue
        if row.machine_point_id:
            seen_machine_points.add(row.machine_point_id)
        accepted.append({
            "source_row": source_row,
            "longitude": lon,
            "latitude": lat,
            "yield_t_ha": round(value, 6),
            "observed_at": observed_at.isoformat(),
            "machine_point_id": row.machine_point_id,
            "speed_kph": (
                round(float(row.speed_kph), 3)
                if row.speed_kph is not None
                else None
            ),
            "moisture_pct": (
                round(float(row.moisture_pct), 3)
                if row.moisture_pct is not None
                else None
            ),
            "quality_flags": [],
        })

    if len(accepted) >= 7:
        center = median(item["yield_t_ha"] for item in accepted)
        mad = median(
            abs(item["yield_t_ha"] - center)
            for item in accepted
        )
        if mad > 0:
            retained = []
            for item in accepted:
                robust_z = abs(item["yield_t_ha"] - center) / (1.4826 * mad)
                if robust_z > MAD_Z_LIMIT:
                    rejected.append(_reject(
                        item["source_row"],
                        "yield_statistical_outlier",
                        "Yield exceeds deterministic MAD threshold",
                    ))
                else:
                    retained.append(item)
            accepted = retained

    return _build_preview(payload, accepted, rejected)


def _build_preview(payload, accepted, rejected):
    yields = [item["yield_t_ha"] for item in accepted]
    bounds = (
        {
            "min_longitude": min(item["longitude"] for item in accepted),
            "min_latitude": min(item["latitude"] for item in accepted),
            "max_longitude": max(item["longitude"] for item in accepted),
            "max_latitude": max(item["latitude"] for item in accepted),
        }
        if accepted
        else None
    )
    summary = {
        "yield_min_t_ha": round(min(yields), 6) if yields else None,
        "yield_max_t_ha": round(max(yields), 6) if yields else None,
        "yield_mean_t_ha": (
            round(sum(yields) / len(yields), 6)
            if yields
            else None
        ),
    }
    base = {
        "schema_code": payload.schema_code.value,
        "input_unit": payload.yield_unit.value,
        "normalized_unit": "t_ha",
        "source_sha256": payload.source_sha256,
        "total_rows": len(payload.rows),
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "accepted": sorted(accepted, key=lambda item: item["source_row"]),
        "rejected": sorted(rejected, key=lambda item: item["source_row"]),
        "bounds": bounds,
        "summary": summary,
        "warnings": (
            ["Review rejection report before creating a new source file."]
            if rejected
            else []
        ),
    }
    base["preview_fingerprint"] = preview_fingerprint(payload, base)
    return base


def preview_fingerprint(payload, preview):
    metadata = payload.model_dump(
        mode="json",
        exclude={"rows", "preview_fingerprint", "confirm"},
    )
    return _canonical_hash({
        "metadata": metadata,
        "accepted": preview["accepted"],
        "rejected": preview["rejected"],
        "summary": preview["summary"],
        "bounds": preview["bounds"],
    })


def apply_field_intersection(db, field, payload, normalized):
    """Mark all otherwise valid rows using one tenant-qualified PostGIS query."""
    if not normalized["accepted"]:
        return normalized
    points = [
        {
            "source_row": item["source_row"],
            "longitude": item["longitude"],
            "latitude": item["latitude"],
        }
        for item in normalized["accepted"]
    ]
    results = _all(db.execute(
        text(
            """
            WITH candidate AS (
              SELECT p.source_row, p.longitude, p.latitude
              FROM jsonb_to_recordset(CAST(:points AS jsonb))
                AS p(source_row integer, longitude double precision,
                     latitude double precision)
            ),
            authorized_field AS (
              SELECT geometry FROM fields
              WHERE id=:field_id AND enterprise_id=:enterprise_id
            )
            SELECT c.source_row,
              ST_Covers(
                f.geometry,
                ST_SetSRID(ST_MakePoint(c.longitude,c.latitude),4326)
              ) AS inside_field
            FROM candidate c CROSS JOIN authorized_field f
            ORDER BY c.source_row
            """
        ),
        {
            "points": json.dumps(points, separators=(",", ":")),
            "field_id": int(field.id),
            "enterprise_id": int(field.enterprise_id),
        },
    ))
    inside = {
        int(row["source_row"]): bool(row["inside_field"])
        for row in results
    }
    if len(inside) != len(points):
        raise HTTPException(409, "Field intersection result is incomplete")
    retained = []
    rejected = list(normalized["rejected"])
    for item in normalized["accepted"]:
        if inside.get(item["source_row"]):
            retained.append(item)
        else:
            rejected.append(_reject(
                item["source_row"],
                "outside_field",
                "Point is outside the authorized field geometry",
            ))
    return _build_preview(payload, retained, rejected)


def preview_import(db, user, field, payload):
    actor = _actor(user, write=True)
    if (
        actor["role"] in TENANT_ROLES
        and actor["enterprise_id"] != int(field.enterprise_id)
    ):
        raise HTTPException(404, "Field not found")
    normalized = normalize_rows(payload)
    return apply_field_intersection(db, field, payload, normalized)


def request_fingerprint(user_id, payload):
    return _canonical_hash({
        "actor": int(user_id),
        "payload": payload.model_dump(mode="json"),
    })


def _existing_by_key(db, enterprise_id, user_id, key):
    return _one(db.execute(
        text(
            "SELECT id, request_fingerprint FROM yield_map_imports "
            "WHERE enterprise_id=:enterprise_id "
            "AND created_by_id=:user_id AND client_request_id=:key"
        ),
        {
            "enterprise_id": enterprise_id,
            "user_id": user_id,
            "key": key,
        },
    ))


def _existing_by_source(db, enterprise_id, field_id, payload):
    return _one(db.execute(
        text(
            "SELECT id FROM yield_map_imports "
            "WHERE enterprise_id=:enterprise_id AND field_id=:field_id "
            "AND season_year=:season_year AND schema_code=:schema_code "
            "AND source_sha256=:source_sha256"
        ),
        {
            "enterprise_id": enterprise_id,
            "field_id": field_id,
            "season_year": payload.season_year,
            "schema_code": payload.schema_code.value,
            "source_sha256": payload.source_sha256,
        },
    ))


def _import_item(row):
    return {
        "id": row["id"],
        "enterprise_id": row["enterprise_id"],
        "field": {
            "id": row["field_id"],
            "name": row["field_name"],
        },
        "season_year": row["season_year"],
        "crop_code": row["crop_code"],
        "schema_code": row["schema_code"],
        "source_filename": row["source_filename"],
        "source_sha256": row["source_sha256"],
        "source_provider": row["source_provider"],
        "machine_id": row["machine_id"],
        "machine_model": row["machine_model"],
        "input_unit": row["input_unit"],
        "normalized_unit": row["normalized_unit"],
        "total_rows": row["total_rows"],
        "accepted_rows": row["accepted_rows"],
        "rejected_rows": row["rejected_rows"],
        "yield_min_t_ha": row["yield_min_t_ha"],
        "yield_max_t_ha": row["yield_max_t_ha"],
        "yield_mean_t_ha": row["yield_mean_t_ha"],
        "bounds": row["bounds"],
        "provenance": row["provenance"],
        "status": row["status"],
        "created_by": {
            "id": row["created_by_id"],
            "display_name": row["created_by_name"],
        },
        "created_at": row["created_at"],
    }


def get_import(db, user, import_id):
    actor = _actor(user)
    clause = "i.id=:import_id"
    params = {"import_id": import_id}
    if actor["role"] in TENANT_ROLES:
        clause += " AND i.enterprise_id=:enterprise_id"
        params["enterprise_id"] = actor["enterprise_id"]
    row = _one(db.execute(text(IMPORT_SELECT + f" WHERE {clause}"), params))
    if not row:
        raise HTTPException(404, "Yield map import not found")
    return _import_item(row)


def accept_import(db, user, field, payload, key):
    actor = _actor(user, write=True)
    enterprise_id = int(field.enterprise_id)
    if (
        actor["role"] in TENANT_ROLES
        and actor["enterprise_id"] != enterprise_id
    ):
        raise HTTPException(404, "Field not found")
    preview = preview_import(db, user, field, payload)
    if preview["preview_fingerprint"] != payload.preview_fingerprint:
        raise HTTPException(409, "Preview fingerprint mismatch")
    if preview["rejected_rows"]:
        raise HTTPException(
            422,
            {
                "message": "Import has rejected rows",
                "rejected_rows": preview["rejected_rows"],
            },
        )
    fingerprint = request_fingerprint(actor["user_id"], payload)
    try:
        existing = _existing_by_key(
            db,
            enterprise_id,
            actor["user_id"],
            key,
        )
        if existing:
            if existing["request_fingerprint"] != fingerprint:
                raise HTTPException(409, "Idempotency key payload conflict")
            return False, get_import(db, user, existing["id"])
        duplicate = _existing_by_source(
            db,
            enterprise_id,
            int(field.id),
            payload,
        )
        if duplicate:
            raise HTTPException(409, "Yield source was already imported")

        provenance = {
            "schema_code": payload.schema_code.value,
            "source_provider": payload.source_provider,
            "source_sha256": payload.source_sha256,
            "normalization": f"{payload.yield_unit.value}_to_t_ha_v1",
            "preview_fingerprint": payload.preview_fingerprint,
        }
        inserted = _one(db.execute(
            text(
                """
                INSERT INTO yield_map_imports
                (enterprise_id,field_id,season_year,crop_code,schema_code,
                 source_filename,source_sha256,source_provider,machine_id,
                 machine_model,input_unit,normalized_unit,total_rows,
                 accepted_rows,rejected_rows,yield_min_t_ha,yield_max_t_ha,
                 yield_mean_t_ha,bounds,provenance,status,created_by_id,
                 client_request_id,request_fingerprint)
                VALUES
                (:enterprise_id,:field_id,:season_year,:crop_code,:schema_code,
                 :source_filename,:source_sha256,:source_provider,:machine_id,
                 :machine_model,:input_unit,'t_ha',:total_rows,:accepted_rows,
                 0,:yield_min_t_ha,:yield_max_t_ha,:yield_mean_t_ha,
                 CAST(:bounds AS jsonb),CAST(:provenance AS jsonb),'accepted',
                 :created_by_id,:client_request_id,:request_fingerprint)
                RETURNING id
                """
            ),
            {
                "enterprise_id": enterprise_id,
                "field_id": int(field.id),
                "season_year": payload.season_year,
                "crop_code": payload.crop_code,
                "schema_code": payload.schema_code.value,
                "source_filename": payload.source_filename,
                "source_sha256": payload.source_sha256,
                "source_provider": payload.source_provider,
                "machine_id": payload.machine_id,
                "machine_model": payload.machine_model,
                "input_unit": payload.yield_unit.value,
                "total_rows": preview["total_rows"],
                "accepted_rows": preview["accepted_rows"],
                "yield_min_t_ha": preview["summary"]["yield_min_t_ha"],
                "yield_max_t_ha": preview["summary"]["yield_max_t_ha"],
                "yield_mean_t_ha": preview["summary"]["yield_mean_t_ha"],
                "bounds": json.dumps(preview["bounds"], separators=(",", ":")),
                "provenance": json.dumps(provenance, separators=(",", ":")),
                "created_by_id": actor["user_id"],
                "client_request_id": key,
                "request_fingerprint": fingerprint,
            },
        ))
        db.execute(
            text(
                """
                INSERT INTO yield_map_points
                (import_id,enterprise_id,field_id,source_row,machine_point_id,
                 observed_at,geometry,yield_t_ha,speed_kph,moisture_pct,
                 quality_flags)
                SELECT :import_id,:enterprise_id,:field_id,p.source_row,
                  p.machine_point_id,CAST(p.observed_at AS timestamptz),
                  ST_SetSRID(ST_MakePoint(p.longitude,p.latitude),4326),
                  p.yield_t_ha,p.speed_kph,p.moisture_pct,
                  CAST(p.quality_flags AS jsonb)
                FROM jsonb_to_recordset(CAST(:points AS jsonb))
                  AS p(source_row integer,machine_point_id text,
                       observed_at text,longitude double precision,
                       latitude double precision,yield_t_ha double precision,
                       speed_kph double precision,moisture_pct double precision,
                       quality_flags text)
                ORDER BY p.source_row
                """
            ),
            {
                "import_id": inserted["id"],
                "enterprise_id": enterprise_id,
                "field_id": int(field.id),
                "points": json.dumps([
                    {
                        **item,
                        "quality_flags": json.dumps(
                            item["quality_flags"],
                            separators=(",", ":"),
                        ),
                    }
                    for item in preview["accepted"]
                ], separators=(",", ":")),
            },
        )
        db.commit()
        return True, get_import(db, user, inserted["id"])
    except IntegrityError:
        db.rollback()
        existing = _existing_by_key(
            db,
            enterprise_id,
            actor["user_id"],
            key,
        )
        if existing and existing["request_fingerprint"] == fingerprint:
            return False, get_import(db, user, existing["id"])
        if existing:
            raise HTTPException(409, "Idempotency key payload conflict")
        raise HTTPException(409, "Concurrent yield import conflict")
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def list_imports(db, user, field, *, limit, offset):
    actor = _actor(user)
    if (
        actor["role"] in TENANT_ROLES
        and actor["enterprise_id"] != int(field.enterprise_id)
    ):
        raise HTTPException(404, "Field not found")
    rows = _all(db.execute(
        text(
            IMPORT_SELECT
            + " WHERE i.enterprise_id=:enterprise_id AND i.field_id=:field_id "
            "ORDER BY i.season_year DESC, i.created_at DESC, i.id DESC "
            "LIMIT :limit OFFSET :offset"
        ),
        {
            "enterprise_id": int(field.enterprise_id),
            "field_id": int(field.id),
            "limit": limit,
            "offset": offset,
        },
    ))
    return {
        "limit": limit,
        "offset": offset,
        "items": [_import_item(row) for row in rows],
    }


def list_points(db, user, import_id, *, limit, offset):
    item = get_import(db, user, import_id)
    rows = _all(db.execute(
        text(
            """
            SELECT p.id,p.source_row,p.machine_point_id,p.observed_at,
             ST_X(p.geometry) AS longitude,ST_Y(p.geometry) AS latitude,
             p.yield_t_ha,p.speed_kph,p.moisture_pct,p.quality_flags
            FROM yield_map_points p
            WHERE p.import_id=:import_id AND p.enterprise_id=:enterprise_id
            ORDER BY p.source_row LIMIT :limit OFFSET :offset
            """
        ),
        {
            "import_id": import_id,
            "enterprise_id": item["enterprise_id"],
            "limit": limit,
            "offset": offset,
        },
    ))
    return {
        "import_id": import_id,
        "limit": limit,
        "offset": offset,
        "items": [dict(row) for row in rows],
    }
