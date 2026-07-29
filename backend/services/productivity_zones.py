"""Explicit-SQL productivity-zone computation, persistence, and read models."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.dependencies import ALLOWED_ROLES, TENANT_ROLES
from services.productivity_zone_algorithm import (
    ALGORITHM,
    ALGORITHM_VERSION,
    MAX_SELECTED_SEASONS,
    ProductivityResult,
    YieldMeasurement,
    analyze_productivity,
)


RUN_COLUMNS = """
 r.id,r.enterprise_id,r.field_id,f.name AS field_name,r.algorithm,
 r.algorithm_version,r.run_key,r.run_uuid,r.selected_seasons,
 r.source_import_ids,r.source_sha256s,r.parameters,r.result_status,
 r.reason_codes,r.point_count,r.eligible_cell_count,r.field_area_ha,
 r.zoned_area_ha,r.area_delta_ha,r.confidence,r.provenance,
 r.started_at,r.finished_at,r.created_at
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


def _actor(user):
    role = str(getattr(user.role, "value", user.role) or "").strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(403, "Unknown role")
    enterprise_id = getattr(user, "enterprise_id", None)
    if role in TENANT_ROLES and enterprise_id is None:
        raise HTTPException(403, "User has no enterprise_id")
    return role, int(enterprise_id) if enterprise_id is not None else None


def _run_item(row):
    return {
        "id": row["id"],
        "enterprise_id": row["enterprise_id"],
        "field": {"id": row["field_id"], "name": row["field_name"]},
        "algorithm": row["algorithm"],
        "algorithm_version": row["algorithm_version"],
        "run_key": row["run_key"],
        "run_uuid": row["run_uuid"],
        "selected_seasons": row["selected_seasons"],
        "source_import_ids": row["source_import_ids"],
        "source_sha256s": row["source_sha256s"],
        "parameters": row["parameters"],
        "result_status": row["result_status"],
        "reason_codes": row["reason_codes"],
        "point_count": row["point_count"],
        "eligible_cell_count": row["eligible_cell_count"],
        "field_area_ha": row["field_area_ha"],
        "zoned_area_ha": row["zoned_area_ha"],
        "area_delta_ha": row["area_delta_ha"],
        "confidence": row["confidence"],
        "provenance": row["provenance"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "created_at": row["created_at"],
        "safety_statement": (
            "Historical measured-yield evidence; not an agronomic prescription."
        ),
    }


def get_run(db, user, run_id: int):
    role, enterprise_id = _actor(user)
    clause = "r.id=:run_id"
    params = {"run_id": run_id}
    if role in TENANT_ROLES:
        clause += " AND r.enterprise_id=:enterprise_id"
        params["enterprise_id"] = enterprise_id
    row = _one(db.execute(
        text(
            f"""
            SELECT {RUN_COLUMNS}
            FROM productivity_zone_runs r
            JOIN fields f
              ON f.id=r.field_id AND f.enterprise_id=r.enterprise_id
            WHERE {clause}
            """
        ),
        params,
    ))
    if not row:
        raise HTTPException(404, "Productivity zone run not found")
    return _run_item(row)


def latest_for_field(db, user, field):
    role, enterprise_id = _actor(user)
    if role in TENANT_ROLES and enterprise_id != int(field.enterprise_id):
        raise HTTPException(404, "Field not found")
    row = _one(db.execute(
        text(
            f"""
            SELECT {RUN_COLUMNS}
            FROM productivity_zone_runs r
            JOIN fields f
              ON f.id=r.field_id AND f.enterprise_id=r.enterprise_id
            WHERE r.enterprise_id=:enterprise_id AND r.field_id=:field_id
            ORDER BY r.created_at DESC,r.id DESC
            LIMIT 1
            """
        ),
        {
            "enterprise_id": int(field.enterprise_id),
            "field_id": int(field.id),
        },
    ))
    return {
        "field": {"id": int(field.id), "name": field.name},
        "latest_run": _run_item(row) if row else None,
    }


def list_zones(db, user, run_id: int, *, limit: int, offset: int):
    run = get_run(db, user, run_id)
    rows = _all(db.execute(
        text(
            """
            SELECT z.id,z.zone_class,z.area_ha,z.mean_score,z.confidence,
             z.provenance,ST_AsGeoJSON(z.geometry)::json AS geometry,
             z.created_at
            FROM productivity_zones z
            WHERE z.run_id=:run_id AND z.enterprise_id=:enterprise_id
            ORDER BY
              CASE z.zone_class
                WHEN 'low' THEN 1 WHEN 'medium' THEN 2 ELSE 3
              END,z.id
            LIMIT :limit OFFSET :offset
            """
        ),
        {
            "run_id": run_id,
            "enterprise_id": run["enterprise_id"],
            "limit": limit,
            "offset": offset,
        },
    ))
    return {
        "run": run,
        "limit": limit,
        "offset": offset,
        "items": [dict(row) for row in rows],
    }


def load_inputs(db, enterprise_id: int, field_id: int, seasons=()):
    """Load at most one accepted import per season and at most five seasons."""
    selected = tuple(sorted(set(int(value) for value in seasons)))
    if len(selected) > MAX_SELECTED_SEASONS:
        raise ValueError("at most five seasons may be selected")
    rows = _all(db.execute(
        text(
            """
            WITH requested AS (
              SELECT value::integer AS season_year
              FROM jsonb_array_elements_text(CAST(:seasons AS jsonb))
            ),
            ranked_imports AS (
              SELECT i.id,i.season_year,i.source_sha256,
                row_number() OVER (
                  PARTITION BY i.season_year
                  ORDER BY i.created_at DESC,i.id DESC
                ) AS position
              FROM yield_map_imports i
              WHERE i.enterprise_id=:enterprise_id
                AND i.field_id=:field_id
                AND i.status='accepted'
                AND (
                  jsonb_array_length(CAST(:seasons AS jsonb))=0
                  OR i.season_year IN (SELECT season_year FROM requested)
                )
            ),
            chosen AS (
              SELECT id,season_year,source_sha256
              FROM ranked_imports WHERE position=1
              ORDER BY season_year DESC LIMIT 5
            )
            SELECT c.id AS import_id,c.source_sha256,c.season_year,
              ST_X(p.geometry) AS longitude,ST_Y(p.geometry) AS latitude,
              p.yield_t_ha,
              ST_Area(f.geometry::geography)/10000.0 AS field_area_ha
            FROM chosen c
            JOIN yield_map_points p
              ON p.import_id=c.id AND p.enterprise_id=:enterprise_id
              AND p.field_id=:field_id
            JOIN fields f
              ON f.id=p.field_id AND f.enterprise_id=p.enterprise_id
            ORDER BY c.season_year,p.source_row
            """
        ),
        {
            "enterprise_id": enterprise_id,
            "field_id": field_id,
            "seasons": json.dumps(selected),
        },
    ))
    measurements = [
        YieldMeasurement(
            import_id=int(row["import_id"]),
            source_sha256=row["source_sha256"],
            season_year=int(row["season_year"]),
            longitude=float(row["longitude"]),
            latitude=float(row["latitude"]),
            yield_t_ha=float(row["yield_t_ha"]),
        )
        for row in rows
    ]
    field_area_ha = float(rows[0]["field_area_ha"]) if rows else None
    return measurements, field_area_ha


def _zone_geometries(db, enterprise_id, field_id, result):
    cells = [
        {
            "zone_class": cell.zone_class,
            "score": cell.score,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[list(point) for point in cell.polygon]],
            },
        }
        for cell in result.cells
    ]
    return _all(db.execute(
        text(
            """
            WITH source_cells AS (
              SELECT c.zone_class,c.score,
                ST_SetSRID(ST_GeomFromGeoJSON(c.geometry),4326) AS geometry
              FROM jsonb_to_recordset(CAST(:cells AS jsonb))
                AS c(zone_class text,score double precision,geometry text)
            ),
            authorized_field AS (
              SELECT geometry FROM fields
              WHERE id=:field_id AND enterprise_id=:enterprise_id
            ),
            clipped AS (
              SELECT c.zone_class,c.score,
                ST_Intersection(c.geometry,f.geometry) AS geometry
              FROM source_cells c CROSS JOIN authorized_field f
            ),
            grouped AS (
              SELECT zone_class,avg(score) AS mean_score,
                ST_Multi(ST_CollectionExtract(
                  ST_UnaryUnion(ST_Collect(geometry)),3
                )) AS geometry
              FROM clipped WHERE NOT ST_IsEmpty(geometry)
              GROUP BY zone_class
            )
            SELECT zone_class,mean_score,
              ST_AsGeoJSON(geometry)::json AS geometry,
              ST_Area(geometry::geography)/10000.0 AS area_ha
            FROM grouped
            WHERE NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry)
            ORDER BY zone_class
            """
        ),
        {
            "cells": json.dumps(cells, separators=(",", ":")),
            "enterprise_id": enterprise_id,
            "field_id": field_id,
        },
    ))


def _insert_run(
    db,
    enterprise_id: int,
    field_id: int,
    run_uuid: UUID,
    result: ProductivityResult,
    *,
    field_area_ha,
    zoned_area_ha,
    confidence,
):
    return _one(db.execute(
        text(
            """
            INSERT INTO productivity_zone_runs
            (enterprise_id,field_id,algorithm,algorithm_version,run_key,
             run_uuid,selected_seasons,source_import_ids,source_sha256s,
             parameters,result_status,reason_codes,point_count,
             eligible_cell_count,field_area_ha,zoned_area_ha,area_delta_ha,
             confidence,provenance,finished_at)
            VALUES
            (:enterprise_id,:field_id,:algorithm,:algorithm_version,:run_key,
             :run_uuid,CAST(:selected_seasons AS jsonb),
             CAST(:source_import_ids AS jsonb),CAST(:source_sha256s AS jsonb),
             CAST(:parameters AS jsonb),:result_status,
             CAST(:reason_codes AS jsonb),:point_count,:eligible_cell_count,
             :field_area_ha,:zoned_area_ha,
             CASE WHEN :zoned_area_ha IS NULL THEN NULL
                  ELSE :field_area_ha - :zoned_area_ha END,:confidence,
             CAST(:provenance AS jsonb),:finished_at)
            ON CONFLICT (enterprise_id,field_id,run_key) DO NOTHING
            RETURNING id
            """
        ),
        {
            "enterprise_id": enterprise_id,
            "field_id": field_id,
            "algorithm": ALGORITHM,
            "algorithm_version": ALGORITHM_VERSION,
            "run_key": result.run_key,
            "run_uuid": run_uuid,
            "selected_seasons": json.dumps(result.selected_seasons),
            "source_import_ids": json.dumps(result.source_import_ids),
            "source_sha256s": json.dumps(result.source_sha256s),
            "parameters": json.dumps(result.parameters, sort_keys=True),
            "result_status": result.status,
            "reason_codes": json.dumps(result.reason_codes),
            "point_count": result.point_count,
            "eligible_cell_count": result.eligible_cell_count,
            "field_area_ha": field_area_ha,
            "zoned_area_ha": zoned_area_ha,
            "confidence": confidence,
            "provenance": json.dumps({
                "algorithm_version": ALGORITHM_VERSION,
                "input_kind": "accepted_measured_yield",
                "source_sha256s": result.source_sha256s,
            }, sort_keys=True),
            "finished_at": datetime.now(timezone.utc),
        },
    ))


def compute_field(
    db,
    enterprise_id: int,
    field_id: int,
    *,
    seasons=(),
    run_uuid: UUID,
    write: bool = False,
):
    """Compute one authorized field; write mode is atomic and idempotent."""
    measurements, field_area_ha = load_inputs(
        db,
        enterprise_id,
        field_id,
        seasons,
    )
    result = analyze_productivity(field_id, measurements)
    if not write:
        return {"created": False, "result": asdict(result)}
    try:
        zone_rows = []
        zoned_area_ha = None
        confidence = result.confidence
        if result.status == "ready":
            zone_rows = _zone_geometries(db, enterprise_id, field_id, result)
            if not zone_rows:
                result = replace(
                    result,
                    status="insufficient_data",
                    reason_codes=("no_cells_intersect_field",),
                    cells=(),
                    eligible_cell_count=0,
                    confidence=0,
                )
            else:
                zoned_area_ha = round(
                    sum(float(row["area_ha"]) for row in zone_rows),
                    4,
                )
                if not field_area_ha or zoned_area_ha > field_area_ha + 0.01:
                    raise RuntimeError("productivity zone area reconciliation failed")
                coverage = min(1.0, zoned_area_ha / field_area_ha)
                confidence = round(result.confidence * 0.75 + coverage * 0.25, 4)
        inserted = _insert_run(
            db,
            enterprise_id,
            field_id,
            run_uuid,
            result,
            field_area_ha=field_area_ha,
            zoned_area_ha=zoned_area_ha,
            confidence=confidence,
        )
        if not inserted:
            existing = _one(db.execute(
                text(
                    "SELECT id FROM productivity_zone_runs "
                    "WHERE enterprise_id=:enterprise_id "
                    "AND field_id=:field_id AND run_key=:run_key"
                ),
                {
                    "enterprise_id": enterprise_id,
                    "field_id": field_id,
                    "run_key": result.run_key,
                },
            ))
            db.rollback()
            return {
                "created": False,
                "run_id": existing["id"],
                "run_key": result.run_key,
                "status": result.status,
            }
        if zone_rows:
            db.execute(
                text(
                    """
                    INSERT INTO productivity_zones
                    (run_id,enterprise_id,field_id,zone_class,geometry,area_ha,
                     mean_score,confidence,provenance)
                    SELECT :run_id,:enterprise_id,:field_id,z.zone_class,
                      ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(z.geometry),4326)),
                      z.area_ha,z.mean_score,:confidence,
                      jsonb_build_object(
                        'algorithm_version',:algorithm_version,
                        'run_key',:run_key
                      )
                    FROM jsonb_to_recordset(CAST(:zones AS jsonb))
                      AS z(zone_class text,geometry text,area_ha numeric,
                           mean_score numeric)
                    """
                ),
                {
                    "run_id": inserted["id"],
                    "enterprise_id": enterprise_id,
                    "field_id": field_id,
                    "confidence": confidence,
                    "algorithm_version": ALGORITHM_VERSION,
                    "run_key": result.run_key,
                    "zones": json.dumps([
                        {
                            "zone_class": row["zone_class"],
                            "geometry": json.dumps(row["geometry"]),
                            "area_ha": float(row["area_ha"]),
                            "mean_score": float(row["mean_score"]),
                        }
                        for row in zone_rows
                    ], separators=(",", ":")),
                },
            )
        db.commit()
        return {
            "created": True,
            "run_id": inserted["id"],
            "run_key": result.run_key,
            "status": result.status,
        }
    except (IntegrityError, HTTPException):
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
