"""Transactional persistence boundary for deterministic anomaly results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from typing import Any
import uuid

from sqlalchemy import text

from services.pixel_anomaly_algorithm import (
    AnomalyRunResult,
    AnomalyThresholds,
    HistoricalZone,
    classify_zones_with_history,
)


MAX_HISTORY_ZONES = 1000


class AnomalyPersistenceError(RuntimeError):
    """A persistence invariant failed before commit."""


@dataclass(frozen=True)
class PersistenceOutcome:
    run_id: int
    inserted: bool
    zone_count: int
    run_key: str


HISTORY_SQL = text(
    """
    SELECT ST_AsGeoJSON(a.geometry)::json AS geometry,
           a.area_ha,
           COALESCE((a.provenance->>'median_drop')::double precision, 0)
             AS median_drop,
           a.persistence_count
      FROM pixel_anomalies a
      JOIN pixel_anomaly_runs r ON r.id=a.run_id
     WHERE a.enterprise_id=:enterprise_id
       AND a.field_id=:field_id
       AND a.index_code=:index_code
       AND a.algorithm_version=:algorithm_version
       AND a.status <> 'dismissed'
       AND r.current_observation_date < :current_observation_date
     ORDER BY r.current_observation_date DESC, a.id DESC
     LIMIT :limit
    """
)

INSERT_RUN_SQL = text(
    """
    INSERT INTO pixel_anomaly_runs (
      enterprise_id,field_id,index_code,
      current_record_type,current_ndvi_record_id,
      current_satellite_index_record_id,current_observation_date,
      comparison_record_type,comparison_ndvi_record_id,
      comparison_satellite_index_record_id,comparison_observation_date,
      algorithm_version,run_key,threshold_hash,thresholds,quality_summary,
      result_status,reason_codes,provenance,started_at,finished_at
    ) VALUES (
      :enterprise_id,:field_id,:index_code,
      :current_record_type,:current_ndvi_record_id,
      :current_satellite_index_record_id,:current_observation_date,
      :comparison_record_type,:comparison_ndvi_record_id,
      :comparison_satellite_index_record_id,:comparison_observation_date,
      :algorithm_version,:run_key,:threshold_hash,
      CAST(:thresholds AS jsonb),CAST(:quality_summary AS jsonb),
      :result_status,CAST(:reason_codes AS jsonb),CAST(:provenance AS jsonb),
      :started_at,:finished_at
    )
    ON CONFLICT (run_key) DO NOTHING
    RETURNING id
    """
)

SELECT_RUN_SQL = text(
    """
    SELECT id
      FROM pixel_anomaly_runs
     WHERE run_key=:run_key
       AND enterprise_id=:enterprise_id
       AND field_id=:field_id
    """
)

INSERT_ZONE_SQL = text(
    """
    INSERT INTO pixel_anomalies (
      run_id,enterprise_id,field_id,index_code,algorithm_version,zone_key,
      geometry,area_ha,score,severity,persistence_count,classification,
      confidence,status,quality_summary,provenance
    ) VALUES (
      :run_id,:enterprise_id,:field_id,:index_code,:algorithm_version,:zone_key,
      ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geometry),4326)),
      :area_ha,:score,:severity,:persistence_count,:classification,
      :confidence,'open',CAST(:quality_summary AS jsonb),
      CAST(:provenance AS jsonb)
    )
    ON CONFLICT (run_id,zone_key) DO NOTHING
    """
)


def _rows(result) -> list[dict[str, Any]]:
    mappings = result.mappings()
    return [dict(row) for row in mappings.all()]


def _scalar(result) -> int | None:
    value = result.scalar_one_or_none()
    return int(value) if value is not None else None


def _history(db, result: AnomalyRunResult) -> tuple[HistoricalZone, ...]:
    rows = _rows(
        db.execute(
            HISTORY_SQL,
            {
                "enterprise_id": result.current.enterprise_id,
                "field_id": result.current.field_id,
                "index_code": result.current.index_code,
                "algorithm_version": result.algorithm_version,
                "current_observation_date": result.current.observed_at,
                "limit": MAX_HISTORY_ZONES,
            },
        )
    )
    return tuple(
        HistoricalZone(
            geometry=row["geometry"],
            area_ha=float(row["area_ha"]),
            median_drop=float(row["median_drop"]),
            persistence_count=int(row["persistence_count"]),
        )
        for row in rows
    )


def persist_anomaly_result(
    db,
    result: AnomalyRunResult,
    thresholds: AnomalyThresholds,
    *,
    started_at: datetime,
    processing_run_id: str,
    finished_at: datetime | None = None,
) -> PersistenceOutcome:
    """Persist exactly one field result and commit exactly once."""
    if result.current.provider == "deterministic_fixture" or (
        result.comparison is not None
        and result.comparison.provider == "deterministic_fixture"
    ):
        raise AnomalyPersistenceError("fixture scenes cannot be persisted")
    thresholds.validate()
    if thresholds.fingerprint() != result.threshold_hash:
        raise AnomalyPersistenceError("threshold fingerprint mismatch")
    if started_at.tzinfo is None:
        raise AnomalyPersistenceError("started_at must be timezone-aware")
    try:
        uuid.UUID(processing_run_id)
    except (ValueError, AttributeError) as exc:
        raise AnomalyPersistenceError("processing_run_id must be a UUID") from exc
    finished_at = finished_at or datetime.now(timezone.utc)
    if finished_at.tzinfo is None or finished_at < started_at:
        raise AnomalyPersistenceError("finished_at is invalid")

    current = result.current
    comparison = result.comparison
    try:
        history = _history(db, result) if result.zones else ()
        zones = classify_zones_with_history(result.zones, history)
        params = {
            "enterprise_id": current.enterprise_id,
            "field_id": current.field_id,
            "index_code": current.index_code,
            "current_record_type": current.record_type,
            "current_ndvi_record_id": (
                current.record_id if current.record_type == "ndvi_record" else None
            ),
            "current_satellite_index_record_id": (
                current.record_id
                if current.record_type == "satellite_index_record"
                else None
            ),
            "current_observation_date": current.observed_at,
            "comparison_record_type": comparison.record_type if comparison else None,
            "comparison_ndvi_record_id": (
                comparison.record_id
                if comparison and comparison.record_type == "ndvi_record"
                else None
            ),
            "comparison_satellite_index_record_id": (
                comparison.record_id
                if comparison and comparison.record_type == "satellite_index_record"
                else None
            ),
            "comparison_observation_date": (
                comparison.observed_at if comparison else None
            ),
            "algorithm_version": result.algorithm_version,
            "run_key": result.run_key,
            "threshold_hash": result.threshold_hash,
            "thresholds": json.dumps(
                asdict(thresholds),
                sort_keys=True,
                separators=(",", ":"),
            ),
            "quality_summary": json.dumps(
                result.quality_summary,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "result_status": result.status,
            "reason_codes": json.dumps(result.reason_codes, separators=(",", ":")),
            "provenance": json.dumps(
                {
                    "current": current.provenance,
                    "comparison": comparison.provenance if comparison else None,
                    "provider": current.provider,
                    "processing_run_id": processing_run_id,
                    "contract": "non_diagnostic",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            "started_at": started_at,
            "finished_at": finished_at,
        }
        run_id = _scalar(db.execute(INSERT_RUN_SQL, params))
        if run_id is None:
            run_id = _scalar(
                db.execute(
                    SELECT_RUN_SQL,
                    {
                        "run_key": result.run_key,
                        "enterprise_id": current.enterprise_id,
                        "field_id": current.field_id,
                    },
                )
            )
            if run_id is None:
                raise AnomalyPersistenceError("idempotent run lookup failed")
            db.commit()
            return PersistenceOutcome(run_id, False, 0, result.run_key)

        if zones:
            zone_params = [
                {
                    "run_id": run_id,
                    "enterprise_id": current.enterprise_id,
                    "field_id": current.field_id,
                    "index_code": current.index_code,
                    "algorithm_version": result.algorithm_version,
                    "zone_key": zone.zone_key,
                    "geometry": json.dumps(
                        zone.geometry,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "area_ha": zone.area_ha,
                    "score": zone.score,
                    "severity": zone.severity,
                    "persistence_count": zone.persistence_count,
                    "classification": zone.classification,
                    "confidence": zone.confidence,
                    "quality_summary": params["quality_summary"],
                    "provenance": json.dumps(
                        {
                            "median_drop": zone.median_drop,
                            "median_deficit": zone.median_deficit,
                            "pixel_count": zone.pixel_count,
                            "contract": "non_diagnostic",
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
                for zone in zones
            ]
            db.execute(INSERT_ZONE_SQL, zone_params)
        db.commit()
        return PersistenceOutcome(run_id, True, len(zones), result.run_key)
    except Exception:
        db.rollback()
        raise
