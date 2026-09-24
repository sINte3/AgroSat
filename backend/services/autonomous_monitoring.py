"""Database boundary for TASK_219 monitoring cycles and operator review."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.dependencies import ALLOWED_ROLES, TENANT_ROLES
from database import SessionLocal, engine
from services import observation_quality
from services.autonomous_anomaly_engine import (
    RulePolicy, assess_candidate, freshness_status, is_robust_drop,
    plan_automatic_inspections, robust_signal,
)
from services.pixel_ndvi import geometry_hash


ADVISORY_LOCK_KEY = 871_320_219
RULE_VERSION = "r3-e-v1"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
STATES = {"NEW", "CONFIRMED", "DISMISSED", "INSPECTION_CREATED", "RESOLVED", "SUPERSEDED"}

# ── Canonical deterministic producer (TASK_225) ─────────────────────────────
# Accepted collector observations -> field-scope candidates. Detection reads
# persisted field statistics only; it never calls a provider.
OBSERVATION_PROVIDER = "sentinel2_field_statistics"
DETECTOR_VERSION = "observation_rolling_median_v1"
DETECTION_INDEX = "ndvi"
SUPPORTING_INDICES = ("savi", "evi", "ndmi", "ndre")
DETECTION_LOOKBACK_DAYS = 120
DETECTION_BASELINE_SCENES = 12
DETECTION_PERSISTENCE_WINDOW = 3
SUPPORT_WINDOW_DAYS = 3
CANDIDATE_PRIORITY = {"EXTREME": "urgent", "HIGH": "high", "MODERATE": "normal", "LOW": "low"}
FIELD_SCOPE_LIMITATION = (
    "Field-mean statistics of accepted Sentinel-2 observations: the zone is the whole field. "
    "The observation does not establish a cause; that requires field inspection."
)
# Detection is part of TASK_219 autonomous monitoring and uses that workflow's
# canonical valid-pixel tier.
_DETECTION_NDVI_ACCEPTED = observation_quality.accepted_observation_sql(
    value_column="n.mean_ndvi",
    valid_pixels_column="n.valid_pixels_pct",
    cloud_column="n.cloud_cover_pct",
    minimum_valid_pixels_pct=observation_quality.MIN_VALID_PIXELS_FRESHNESS_PCT,
)
_DETECTION_INDEX_ACCEPTED = observation_quality.accepted_observation_sql(
    value_column="s.mean_value",
    valid_pixels_column="s.valid_pixels_pct",
    cloud_column="s.cloud_cover_pct",
    minimum_valid_pixels_pct=observation_quality.MIN_VALID_PIXELS_FRESHNESS_PCT,
)

# Freshness accepts an observation through the canonical contract in
# services/observation_quality.py. A NULL cloud_cover_pct is neutral because the
# canonical collectors mask cloud via the Sentinel-2 SCL layer before computing
# valid_pixels_pct; see that module for the full reasoning.
_FRESHNESS_NDVI_ACCEPTED = observation_quality.accepted_observation_sql(
    value_column="n.mean_ndvi",
    valid_pixels_column="n.valid_pixels_pct",
    cloud_column="n.cloud_cover_pct",
    minimum_valid_pixels_pct=observation_quality.MIN_VALID_PIXELS_FRESHNESS_PCT,
)
_FRESHNESS_INDEX_ACCEPTED = observation_quality.accepted_observation_sql(
    value_column="s.mean_value",
    valid_pixels_column="s.valid_pixels_pct",
    cloud_column="s.cloud_cover_pct",
    minimum_valid_pixels_pct=observation_quality.MIN_VALID_PIXELS_FRESHNESS_PCT,
)

# One definition of the freshness computation, shared by the write path and by
# the read-only preview, so a dry run can never disagree with the write it
# previews. Age boundaries are unchanged: FRESH through 10 days, AGING through
# 20 days, STALE beyond that.
_FRESHNESS_COMPUTED_SQL = f"""
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
                 AND {_FRESHNESS_NDVI_ACCEPTED}
              UNION ALL
              SELECT 'satellite_index_record:'||s.id,s.captured_date::timestamptz
                FROM satellite_index_records s
               WHERE s.index_code=a.index_code AND s.field_id=a.field_id
                 AND {_FRESHNESS_INDEX_ACCEPTED}
              ORDER BY accepted_at DESC LIMIT 1
            ) o ON true
        ), computed AS (
          SELECT enterprise_id,field_id,index_code,
                 CASE WHEN accepted_at IS NULL THEN
                   CASE :outcome WHEN 'cloud_blocked' THEN 'CLOUD_BLOCKED'
                     WHEN 'provider_degraded' THEN 'PROVIDER_DEGRADED'
                     WHEN 'quality_blocked' THEN 'QUALITY_BLOCKED' ELSE 'NEVER_COLLECTED' END
                   WHEN current_date-accepted_at::date<=10 THEN 'FRESH'
                   WHEN current_date-accepted_at::date<=20 THEN 'AGING' ELSE 'STALE' END AS status,
                 scene_key,accepted_at
            FROM latest
        )
"""

_REFRESH_FRESHNESS_SQL = _FRESHNESS_COMPUTED_SQL + """
        INSERT INTO satellite_field_freshness
          (enterprise_id,field_id,index_code,status,last_accepted_scene,last_accepted_at,
           last_failure_reason,last_run_id,updated_at)
        SELECT enterprise_id,field_id,index_code,status,scene_key,accepted_at,
               :outcome,:run_id,now()
          FROM computed
        ON CONFLICT (field_id,index_code) DO UPDATE SET
          enterprise_id=excluded.enterprise_id,status=excluded.status,
          last_accepted_scene=excluded.last_accepted_scene,last_accepted_at=excluded.last_accepted_at,
          last_failure_reason=excluded.last_failure_reason,last_run_id=excluded.last_run_id,updated_at=now()
        RETURNING id
"""

# ── Manual recovery write path (scripts/recompute_satellite_freshness.py) ───
#
# refresh_freshness() above is the COLLECTOR's write path: it rewrites every
# freshness row and stamps the run that produced it. A manual recovery is not a
# collection run, so it must neither claim nor erase that provenance. The
# recovery path below is therefore separate, and deliberately narrower:
#
#   * it reuses _FRESHNESS_COMPUTED_SQL, so the accepted-observation predicate
#     stays defined in exactly one place;
#   * it considers ONLY (field, index) pairs that have an accepted persisted
#     observation. Absence of evidence is not evidence: a pair with no accepted
#     observation is left exactly as the collector left it, including a
#     PROVIDER_DEGRADED / QUALITY_BLOCKED / CLOUD_BLOCKED status that a manual
#     command has no authority to reinterpret;
#   * it writes only rows whose stored state actually differs (IS DISTINCT
#     FROM), so a second apply writes zero rows and updated_at does not move;
#   * it updates only the columns it has authoritative new information for --
#     status, last_accepted_scene, last_accepted_at -- plus updated_at. It
#     leaves last_run_id, last_failure_reason, last_quality_reason,
#     last_attempted_scene, last_attempted_at and next_eligible_at untouched.
#
# The :outcome bind is inherited from the shared CTE. It is unreachable here:
# it only feeds the branch for rows with no accepted scene, which this path
# excludes. It is bound to NULL so the statement stays valid.
_RECOVERY_CHANGES_SQL = _FRESHNESS_COMPUTED_SQL + """
        , recovery AS (
          SELECT c.enterprise_id,c.field_id,c.index_code,
                 c.status AS computed_status,c.scene_key,c.accepted_at,
                 stored.id AS stored_id,stored.status AS stored_status,
                 stored.last_accepted_scene AS stored_scene,
                 stored.last_accepted_at AS stored_accepted_at
            FROM computed c
            LEFT JOIN satellite_field_freshness stored
              ON stored.field_id=c.field_id AND stored.index_code=c.index_code
           WHERE c.accepted_at IS NOT NULL
        ), changes AS (
          SELECT * FROM recovery
           WHERE stored_id IS NULL
              OR stored_status IS DISTINCT FROM computed_status
              OR stored_scene IS DISTINCT FROM scene_key
              OR stored_accepted_at IS DISTINCT FROM accepted_at
        )
"""

_RECOVERY_PREVIEW_SQL = _RECOVERY_CHANGES_SQL + """
        SELECT computed_status,
               COALESCE(stored_status,'(absent)') AS stored_status,
               count(*)::bigint AS row_count
          FROM changes
         GROUP BY 1,2
         ORDER BY 1,2
"""

_RECOVERY_APPLY_SQL = _RECOVERY_CHANGES_SQL + """
        INSERT INTO satellite_field_freshness
          (enterprise_id,field_id,index_code,status,last_accepted_scene,
           last_accepted_at,updated_at)
        SELECT enterprise_id,field_id,index_code,computed_status,scene_key,
               accepted_at,now()
          FROM changes
        ON CONFLICT (field_id,index_code) DO UPDATE SET
          status=excluded.status,
          last_accepted_scene=excluded.last_accepted_scene,
          last_accepted_at=excluded.last_accepted_at,
          updated_at=now()
        RETURNING id
"""


@dataclass
class ApplyRun:
    session: object
    run_id: uuid.UUID
    run_key: str
    # The connection the run's advisory lock is held on. Kept checked out for
    # the whole cycle; see begin_apply_run for why the run cannot use a pooled
    # session. Optional so a caller that supplies its own session still works.
    connection: object = None


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
    # A session-scoped advisory lock belongs to the PostgreSQL backend that took
    # it, not to the SQLAlchemy Session. A pooled Session releases its
    # connection on every commit, and this run commits many times, so the lock
    # would be stranded on a backend the run no longer uses: the pool hands that
    # backend to unrelated callers, a second cycle given the same connection
    # re-acquires the lock re-entrantly and sees no contention, and the run's
    # own unlock releases nothing. Hold one connection for the whole cycle.
    connection = engine.connect()
    session = SessionLocal(bind=connection)
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
        return ApplyRun(
            session=session,
            run_id=existing["id"],
            run_key=run_key,
            connection=connection,
        )
    except Exception:
        session.rollback()
        try:
            session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
            session.commit()
        except Exception:
            pass
        session.close()
        connection.close()
        raise


def heartbeat(run: ApplyRun, counters: dict | None = None) -> None:
    run.session.execute(text("""
        UPDATE satellite_collection_runs
           SET heartbeat_at=now(), counters=COALESCE(CAST(:counters AS jsonb),counters), updated_at=now()
         WHERE id=:id AND status='running'
    """), {"id": run.run_id, "counters": json.dumps(counters) if counters is not None else None})
    run.session.commit()


def _freshness_outcome(last_outcome: str | None) -> str | None:
    if last_outcome in {"cloud_blocked", "provider_degraded", "quality_blocked"}:
        return last_outcome
    return None


def refresh_freshness(run: ApplyRun, *, last_outcome: str | None = None) -> int:
    result = run.session.execute(
        text(_REFRESH_FRESHNESS_SQL),
        {"run_id": run.run_id, "outcome": _freshness_outcome(last_outcome)},
    )
    count = len(result.fetchall())
    run.session.commit()
    return count


def freshness_recovery_preview(session) -> list[dict]:
    """Report the exact rows a manual freshness recovery would write.

    Read-only: writes nothing and takes no lock. The result is the same
    ``changes`` set that :func:`apply_freshness_recovery` writes, so the
    preview count and the write count cannot disagree when both run inside one
    locked transaction. Returns one row per (computed status, stored status)
    pair with a count; ``stored_status`` is ``'(absent)'`` where no freshness
    row exists yet.
    """
    return _rows(session.execute(text(_RECOVERY_PREVIEW_SQL), {"outcome": None}))


def apply_freshness_recovery(session) -> int:
    """Write the bounded recovery change set and return the rows written.

    Does **not** commit: the caller owns the transaction, so the advisory lock
    it holds covers this write. Only rows whose accepted-observation-derived
    state actually differs are touched, so calling this twice writes zero rows
    the second time. Collector provenance columns are preserved; see
    ``_RECOVERY_CHANGES_SQL`` above.
    """
    result = session.execute(text(_RECOVERY_APPLY_SQL), {"outcome": None})
    return len(result.fetchall())


def _detection_series(session, *, as_of_date: date, lookback_from: date) -> dict[int, dict]:
    """Bounded accepted NDVI series for every active field, oldest first."""
    rows = _rows(session.execute(text(f"""
        WITH scope AS (
          SELECT f.id,f.enterprise_id,
                 COALESCE(f.area_ha,ST_Area(f.geometry::geography)/10000.0) AS area_ha
            FROM fields f
           WHERE f.is_active=true AND f.geometry IS NOT NULL
             AND NOT ST_IsEmpty(f.geometry) AND ST_IsValid(f.geometry)
        )
        SELECT s.id AS field_id,s.enterprise_id,s.area_ha,
               obs.id,obs.captured_date,obs.value,obs.valid_pixels_pct,obs.cloud_cover_pct
          FROM scope s
          CROSS JOIN LATERAL (
            SELECT n.id,n.captured_date,n.mean_ndvi AS value,n.valid_pixels_pct,n.cloud_cover_pct
              FROM ndvi_records n
             WHERE n.field_id=s.id AND n.captured_date<=:as_of_date
               AND n.captured_date>=:lookback_from AND {_DETECTION_NDVI_ACCEPTED}
             ORDER BY n.captured_date DESC,n.id DESC
             LIMIT :per_field
          ) obs
         ORDER BY s.id,obs.captured_date,obs.id
    """), {
        "as_of_date": as_of_date, "lookback_from": lookback_from,
        "per_field": DETECTION_BASELINE_SCENES + DETECTION_PERSISTENCE_WINDOW,
    }))
    series: dict[int, dict] = {}
    for row in rows:
        entry = series.setdefault(int(row["field_id"]), {
            "enterprise_id": int(row["enterprise_id"]),
            "area_ha": float(row["area_ha"]) if row["area_ha"] is not None else 0.0,
            "scenes": [],
        })
        entry["scenes"].append({
            "id": int(row["id"]), "date": row["captured_date"], "value": float(row["value"]),
            "valid_pixels_pct": float(row["valid_pixels_pct"]),
            "cloud_cover_pct": row["cloud_cover_pct"],
        })
    return series


def _persistence(values: list[float], policy: RulePolicy) -> int:
    """Consecutive trailing scenes that are each a robust drop.

    The baseline for ``k`` trailing scenes is the history *before* them, so a
    sustained anomaly cannot dilute its own reference.
    """
    persistence = 0
    for trailing in range(1, min(DETECTION_PERSISTENCE_WINDOW, len(values)) + 1):
        history = values[:len(values) - trailing][-DETECTION_BASELINE_SCENES:]
        if len(history) < policy.min_baseline_scenes:
            break
        if all(is_robust_drop(robust_signal(history, value, policy))
               for value in values[len(values) - trailing:]):
            persistence = trailing
        else:
            break
    return persistence


def _supporting_agreement(session, *, field_id: int, anchor: date, lookback_from: date,
                          policy: RulePolicy) -> tuple[int, dict]:
    """Count the other indices whose nearest accepted scene is also a robust drop."""
    rows = _rows(session.execute(text(f"""
        SELECT s.index_code,s.id,s.captured_date,s.mean_value AS value
          FROM satellite_index_records s
         WHERE s.field_id=:field_id AND s.index_code IN ('savi','evi','ndmi','ndre')
           AND s.captured_date>=:lookback_from AND s.captured_date<=:window_to
           AND {_DETECTION_INDEX_ACCEPTED}
         ORDER BY s.index_code,s.captured_date,s.id
    """), {"field_id": field_id, "lookback_from": lookback_from,
           "window_to": anchor + timedelta(days=SUPPORT_WINDOW_DAYS)}))
    by_index: dict[str, list[dict]] = {}
    for row in rows:
        by_index.setdefault(row["index_code"], []).append(row)
    agreement, detail = 0, {}
    for index_code in SUPPORTING_INDICES:
        scenes = by_index.get(index_code, [])
        near = [scene for scene in scenes
                if abs((scene["captured_date"] - anchor).days) <= SUPPORT_WINDOW_DAYS]
        if not near:
            detail[index_code] = {"agrees": False, "reason": "no_accepted_scene_in_window"}
            continue
        support = min(near, key=lambda scene: (abs((scene["captured_date"] - anchor).days),
                                               -scene["captured_date"].toordinal(), scene["id"]))
        history = [float(scene["value"]) for scene in scenes
                   if scene["captured_date"] < support["captured_date"]][-DETECTION_BASELINE_SCENES:]
        if len(history) < policy.min_baseline_scenes:
            detail[index_code] = {"agrees": False, "reason": "insufficient_history",
                                  "record_id": support["id"]}
            continue
        signal = robust_signal(history, float(support["value"]), policy)
        agrees = is_robust_drop(signal)
        agreement += int(agrees)
        detail[index_code] = {
            "agrees": agrees, "record_id": support["id"],
            "captured_date": support["captured_date"].isoformat(),
            "value": float(support["value"]), "baseline_median": signal.baseline_median,
            "robust_deviation": signal.robust_deviation,
        }
    return agreement, detail


def _observation_assessments(session, *, as_of: datetime) -> tuple[list[dict], dict[str, int]]:
    """The detection decision for every active field, without writing.

    One definition shared by the write path and the read-only preview, so a
    preview can never disagree with the cycle it previews. For every active
    field it reads a bounded window of accepted NDVI observations (never whole
    history), tests the newest scene against the field's own rolling
    median/MAD baseline and proposes a field-scope candidate when the drop is
    robust. The zone is the whole field polygon, stated as such in the
    evidence, because the persisted statistics are field statistics.
    """
    policy = RulePolicy()
    as_of_date = as_of.astimezone(timezone.utc).date()
    lookback_from = as_of_date - timedelta(days=DETECTION_LOOKBACK_DAYS)
    counters = {key: 0 for key in (
        "assessed_fields", "observation_candidates", "suppressed_active_case",
        "insufficient_history", "stale_observation", "no_signal", "replayed_scene",
    )}
    proposals: list[dict] = []
    series = _detection_series(session, as_of_date=as_of_date, lookback_from=lookback_from)
    for field_id, entry in sorted(series.items()):
        scenes = entry["scenes"]
        counters["assessed_fields"] += 1
        current = scenes[-1]
        if (as_of_date - current["date"]).days > policy.fresh_days:
            counters["stale_observation"] += 1
            continue
        values = [scene["value"] for scene in scenes]
        if len(values) <= policy.min_baseline_scenes:
            counters["insufficient_history"] += 1
            continue
        persistence = _persistence(values, policy)
        if persistence == 0:
            counters["no_signal"] += 1
            continue
        baseline_scenes = scenes[:len(scenes) - persistence][-DETECTION_BASELINE_SCENES:]
        history = [scene["value"] for scene in baseline_scenes]
        geometry = _row(session.execute(text(
            "SELECT ST_AsGeoJSON(geometry)::json AS geometry FROM fields "
            "WHERE id=:field_id AND enterprise_id=:enterprise_id"
        ), {"field_id": field_id, "enterprise_id": entry["enterprise_id"]}))
        field_hash = geometry_hash(geometry["geometry"])
        agreement, supporting = _supporting_agreement(
            session, field_id=field_id, anchor=current["date"],
            lookback_from=lookback_from, policy=policy,
        )
        acquired_at = datetime(current["date"].year, current["date"].month,
                               current["date"].day, tzinfo=timezone.utc)
        assessment = assess_candidate(
            enterprise_id=entry["enterprise_id"], field_id=field_id,
            provider=OBSERVATION_PROVIDER, index_code=DETECTION_INDEX,
            geometry_hash=field_hash, history=history, current=current["value"],
            affected_area_ha=entry["area_ha"], field_area_ha=entry["area_ha"],
            persistence_scenes=persistence, supporting_agreement=agreement,
            data_quality=min(current["valid_pixels_pct"] / 100.0, 1.0),
            acquired_at=acquired_at, policy=policy,
        ) if entry["area_ha"] > 0 else None
        if assessment is None:
            counters["no_signal"] += 1
            continue
        scene_id = f"ndvi_record:{current['id']}"
        prior = _row(session.execute(text("""
            SELECT scene_id FROM autonomous_anomaly_candidates
             WHERE enterprise_id=:enterprise_id AND source_key=:source_key AND zone_key=:zone_key
               AND (scene_id=:scene_id OR state IN ('NEW','CONFIRMED','INSPECTION_CREATED'))
             ORDER BY (scene_id=:scene_id) DESC, id
             LIMIT 1
        """), {"enterprise_id": entry["enterprise_id"], "source_key": assessment.source_key,
               "zone_key": assessment.zone_key, "scene_id": scene_id}))
        if prior is not None:
            counters["replayed_scene" if prior["scene_id"] == scene_id else "suppressed_active_case"] += 1
            continue
        signal = robust_signal(history, current["value"], policy)
        evidence = {
            "detector": DETECTOR_VERSION, "rule_version": RULE_VERSION, "scope": "field",
            "field_geometry_hash": field_hash, "index_code": DETECTION_INDEX,
            "metric": "mean_ndvi", "ndvi_record_id": current["id"],
            "captured_date": current["date"].isoformat(),
            "source_snapshot": {"sampled_value": current["value"],
                                "comparison_value": signal.baseline_median},
            "baseline": {"median": signal.baseline_median, "mad": signal.mad,
                         "record_ids": [scene["id"] for scene in baseline_scenes],
                         "dates": [scene["date"].isoformat() for scene in baseline_scenes]},
            "persistence": {"scenes": persistence,
                            "record_ids": [scene["id"] for scene in scenes[len(scenes) - persistence:]]},
            "supporting": supporting,
            "quality": {"valid_pixels_pct": current["valid_pixels_pct"],
                        "cloud_cover_pct": current["cloud_cover_pct"],
                        "minimum_valid_pixels_pct": observation_quality.MIN_VALID_PIXELS_FRESHNESS_PCT,
                        "contract": "services.observation_quality"},
            "contract": "non_diagnostic",
            "limitation": FIELD_SCOPE_LIMITATION,
        }
        proposals.append({
            "enterprise_id": entry["enterprise_id"], "field_id": field_id, "scene_id": scene_id,
            "acquired_at": acquired_at, "source_key": assessment.source_key,
            "zone_key": assessment.zone_key, "score": assessment.score,
            "confidence": assessment.confidence, "severity": assessment.severity,
            "automatic": assessment.eligible_for_automatic_inspection,
            "magnitude": signal.magnitude, "deviation": signal.robust_deviation,
            "area_ha": entry["area_ha"], "persistence": persistence, "agreement": agreement,
            "quality": min(current["valid_pixels_pct"] / 100.0, 1.0), "evidence": evidence,
            "explanation": f"{assessment.explanation} {FIELD_SCOPE_LIMITATION}",
            "cooldown_until": assessment.cooldown_until,
        })
    return proposals, counters


def detect_observation_candidates(run: ApplyRun, *, as_of: datetime | None = None) -> dict[str, int]:
    """Canonical deterministic producer: accepted observations -> candidates.

    Runs inside the collector's apply cycle, under its advisory lock, after
    freshness is refreshed; nothing here calls a provider. Replay-safe: a scene
    is recorded once (the candidate unique key backs the read-side check), and
    a field that already has an active case for the same zone is skipped.
    """
    proposals, counters = _observation_assessments(
        run.session, as_of=as_of or datetime.now(timezone.utc),
    )
    for proposal in proposals:
        inserted = run.session.execute(text("""
            INSERT INTO autonomous_anomaly_candidates
              (enterprise_id,field_id,run_id,rule_version,provider,scene_id,acquired_at,index_code,
               source_key,zone_key,geometry,score,confidence,severity,magnitude,robust_deviation,
               affected_area_ha,affected_area_fraction,persistence_scenes,multi_index_agreement,
               data_quality,evidence,explanation,state,cooldown_until)
            SELECT f.enterprise_id,f.id,:run_id,:rule,:provider,:scene_id,:acquired_at,:index_code,
                   :source_key,:zone_key,ST_Multi(f.geometry),:score,:confidence,:severity,:magnitude,
                   :deviation,:area_ha,1.0,:persistence,:agreement,:quality,CAST(:evidence AS jsonb),
                   :explanation,'NEW',:cooldown_until
              FROM fields f WHERE f.id=:field_id AND f.enterprise_id=:enterprise_id
            ON CONFLICT (enterprise_id,field_id,index_code,scene_id,zone_key,rule_version) DO NOTHING
            RETURNING id
        """), {
            **{key: value for key, value in proposal.items() if key != "automatic"},
            "run_id": run.run_id, "rule": RULE_VERSION, "provider": OBSERVATION_PROVIDER,
            "index_code": DETECTION_INDEX, "evidence": json.dumps(proposal["evidence"], sort_keys=True),
        }).first()
        counters["observation_candidates" if inserted else "replayed_scene"] += 1
    run.session.commit()
    return counters


def preview_observation_candidates(session, *, as_of: datetime | None = None) -> dict:
    """Read-only preview of what the next apply cycle's detection would record.

    Uses the same decision function as :func:`detect_observation_candidates`
    and writes nothing; the caller's transaction is rolled back. Automatic
    inspection eligibility is reported per candidate, before the cap and the
    spike guard that promotion applies.
    """
    try:
        proposals, counters = _observation_assessments(
            session, as_of=as_of or datetime.now(timezone.utc),
        )
    finally:
        session.rollback()
    counters["observation_candidates"] = len(proposals)
    return {
        "counters": counters,
        "candidates": [
            {key: proposal[key] for key in (
                "enterprise_id", "field_id", "scene_id", "acquired_at", "severity", "confidence",
                "score", "persistence", "agreement", "automatic")}
            | {"current_value": proposal["evidence"]["source_snapshot"]["sampled_value"],
               "baseline_median": proposal["evidence"]["source_snapshot"]["comparison_value"]}
            for proposal in proposals
        ],
    }


class CandidatePromotionError(RuntimeError):
    """A candidate cannot open an inspection; ``status`` is the HTTP meaning."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _candidate_source(item) -> "InspectionSource":
    from services.anomaly_inspections import InspectionSource

    evidence = item["evidence"] or {}
    snapshot = evidence.get("source_snapshot") or {}
    sampled = snapshot.get("sampled_value")
    comparison = snapshot.get("comparison_value")
    if sampled is None:
        raise CandidatePromotionError(
            409, "Candidate evidence has no sampled value snapshot; it needs operator review"
        )
    scope = ("Verification scope: the whole field."
             if evidence.get("scope") == "field" else "Verification scope: the detected zone.")
    return InspectionSource(
        kind="pixel_ndvi",
        reason=f"{item['explanation']} {scope}"[:2000],
        provider=str(item["provider"])[:40],
        item_id=item["scene_id"],
        acquired_at=item["acquired_at"],
        index_name=item["index_code"],
        sampled_value=float(sampled),
        comparison_value=float(comparison) if comparison is not None else None,
        delta=round(float(sampled) - float(comparison), 6) if comparison is not None else None,
        geometry_hash=geometry_hash(item["field_geometry"]),
        zone_ewkb=item["geometry_ewkb"],
    )


def promote_candidate(session, *, candidate_id: int, actor, reason: str, title: str,
                      origin: str, allowed_states: frozenset[str],
                      expected_version: int | None = None) -> dict:
    """Open the canonical inspection for one candidate, in the caller's transaction.

    The candidate row is locked, its state and version checked, the INSERT is
    the canonical one (services.anomaly_inspections.insert_inspection) and the
    candidate transition is recorded, so all three commit together or not at
    all. A candidate derived from a pixel zone that already has its own
    inspection is refused rather than duplicated.
    """
    from services.anomaly_inspections import InspectionSourceError, insert_inspection

    item = _row(session.execute(text("""
        SELECT a.*,encode(ST_AsEWKB(a.geometry),'hex') AS geometry_ewkb,f.name AS field_name,
               ST_AsGeoJSON(f.geometry)::json AS field_geometry
          FROM autonomous_anomaly_candidates a
          JOIN fields f ON f.id=a.field_id AND f.enterprise_id=a.enterprise_id
         WHERE a.id=:id FOR UPDATE OF a
    """), {"id": candidate_id}))
    if not item:
        raise CandidatePromotionError(404, "Candidate not found")
    if item["state"] not in allowed_states or (
        expected_version is not None and item["version"] != expected_version
    ):
        raise CandidatePromotionError(409, "Candidate version or state conflict")
    duplicate = session.execute(text("""
        SELECT 1 FROM autonomous_anomaly_candidates
         WHERE enterprise_id=:enterprise AND source_key=:source AND zone_key=:zone
           AND inspection_id IS NOT NULL AND state='INSPECTION_CREATED' AND id<>:id
         LIMIT 1
    """), {"enterprise": item["enterprise_id"], "source": item["source_key"],
           "zone": item["zone_key"], "id": candidate_id}).first()
    pixel_reference = (item["evidence"] or {}).get("pixel_anomaly_id")
    covered = pixel_reference is not None and session.execute(text("""
        SELECT 1 FROM pixel_anomaly_inspections
         WHERE anomaly_id=:anomaly AND enterprise_id=:enterprise LIMIT 1
    """), {"anomaly": int(pixel_reference), "enterprise": item["enterprise_id"]}).first()
    if duplicate or covered:
        raise CandidatePromotionError(409, "An inspection already exists for this anomaly source and zone")
    source = _candidate_source(item)
    priority = CANDIDATE_PRIORITY.get(item["severity"], "normal")
    fingerprint = hashlib.sha256(
        f"{candidate_id}|{item['source_key']}|{item['zone_key']}".encode()
    ).hexdigest()
    try:
        inspection = insert_inspection(
            session, actor=actor, field_id=item["field_id"], enterprise_id=item["enterprise_id"],
            source=source, client_request_id=f"candidate-{candidate_id}",
            request_fingerprint=fingerprint, title=f"{title}: {item['field_name']}"[:255],
            priority=priority, origin=origin,
        )
    except InspectionSourceError as error:
        raise CandidatePromotionError(409, f"Candidate snapshot is invalid: {error}") from None
    updated = session.execute(text("""
        UPDATE autonomous_anomaly_candidates SET state='INSPECTION_CREATED',inspection_id=:inspection,
          version=version+1,updated_at=now()
         WHERE id=:id AND state=:state AND version=:version
        RETURNING version
    """), {"inspection": inspection["id"], "id": candidate_id, "state": item["state"],
           "version": item["version"]}).first()
    if updated is None:
        raise CandidatePromotionError(409, "Candidate version or state conflict")
    session.execute(text("""
        INSERT INTO autonomous_anomaly_transitions
          (candidate_id,enterprise_id,from_state,to_state,actor_id,reason,expected_version)
        VALUES (:id,:enterprise,:old,'INSPECTION_CREATED',:actor,:reason,:version)
    """), {"id": candidate_id, "enterprise": item["enterprise_id"], "old": item["state"],
           "actor": actor.user_id, "reason": reason, "version": item["version"]})
    return {"candidate_id": candidate_id, "inspection_id": inspection["id"],
            "version": int(updated[0]), "state": "INSPECTION_CREATED"}


def promote_automatic_candidates(run: ApplyRun) -> dict[str, int | bool]:
    """Open canonical inspections for this run's automatic candidates.

    Bounded by the rule's cap and spike guard. Each promotion runs in its own
    savepoint, so one refused candidate never undoes another.
    """
    from services.anomaly_inspections import Actor

    eligible = _rows(run.session.execute(text("""
      SELECT a.id,a.field_id,a.source_key,a.zone_key,a.confidence,a.cooldown_until,
             ((a.confidence>=0.85 AND a.persistence_scenes>=2 AND a.multi_index_agreement>=2)
               OR (a.confidence>=0.85 AND a.severity='EXTREME')) AS automatic,
             EXISTS (SELECT 1 FROM autonomous_anomaly_candidates prior
               WHERE prior.enterprise_id=a.enterprise_id AND prior.source_key=a.source_key
                 AND prior.zone_key=a.zone_key AND prior.id<>a.id
                 AND prior.cooldown_until>a.acquired_at) AS replay
        FROM autonomous_anomaly_candidates a WHERE a.run_id=:run AND a.state='NEW'
    """), {"run": run.run_id}))
    active_fields = run.session.execute(text("SELECT count(*) FROM fields WHERE is_active=true")).scalar_one()
    existing = {
        (row["source_key"], row["zone_key"]) for row in _rows(run.session.execute(text("""
          SELECT source_key,zone_key FROM autonomous_anomaly_candidates
           WHERE inspection_id IS NOT NULL AND state='INSPECTION_CREATED'
        """)))
    }
    plan = plan_automatic_inspections(
        eligible, active_field_count=max(int(active_fields), 1), open_source_zone_keys=existing,
    )
    automatic = refused = 0
    if plan.candidate_ids:
        admin_id = run.session.execute(text("""
          SELECT id FROM users WHERE lower(role::text)='admin' AND is_active=true ORDER BY id LIMIT 1
        """)).scalar_one_or_none()
        if admin_id is None:
            raise RuntimeError("automatic inspection requires one active administrator audit identity")
        actor = Actor(int(admin_id), "admin", None)
        for candidate_id in plan.candidate_ids:
            savepoint = run.session.begin_nested()
            try:
                promote_candidate(
                    run.session, candidate_id=candidate_id, actor=actor,
                    reason="Automatic high-confidence persistent anomaly policy.",
                    title="Автоматическая проверка спутниковой аномалии",
                    origin=f"automatic_monitoring:{RULE_VERSION}",
                    allowed_states=frozenset({"NEW"}),
                )
                savepoint.commit()
                automatic += 1
            except (CandidatePromotionError, IntegrityError):
                savepoint.rollback()
                refused += 1
    return {"spike_guard_triggered": plan.spike_guard_triggered,
            "automatic_inspections": automatic, "automatic_cap_suppressed": len(plan.suppressed_cap_ids),
            "cooldown_or_open_suppressed": len(plan.suppressed_duplicate_ids),
            "automatic_refused": refused}


def reconcile_pixel_candidates(run: ApplyRun) -> dict[str, int | bool]:
    """Record accepted pixel zones as candidates, then promote this run's automatic ones.

    Candidates recorded earlier in the same run by
    :func:`detect_observation_candidates` are promoted by the same bounded,
    capped policy. Replay is protected by the candidate and inspection unique
    keys.
    """
    inserted = run.session.execute(text("""
        INSERT INTO autonomous_anomaly_candidates
          (enterprise_id,field_id,run_id,rule_version,provider,scene_id,acquired_at,index_code,
           source_key,zone_key,geometry,score,confidence,severity,magnitude,robust_deviation,
           affected_area_ha,affected_area_fraction,persistence_scenes,multi_index_agreement,
           data_quality,evidence,explanation,state,cooldown_until)
        SELECT a.enterprise_id,a.field_id,:collection_run,:rule,
               COALESCE(r.provenance->>'provider','sentinel'),
               r.current_record_type||':'||COALESCE(r.current_ndvi_record_id,r.current_satellite_index_record_id)::text,
               (r.current_observation_date::timestamp AT TIME ZONE 'UTC'),r.index_code,
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
                 'classification',a.classification,'quality',a.quality_summary,'provenance',a.provenance,
                 'scope','zone',
                 'source_snapshot',jsonb_build_object(
                   'sampled_value',(a.provenance->>'median_current_value')::float,
                   'comparison_value',(a.provenance->>'median_comparison_value')::float)),
               'Pixel anomaly retained from deterministic raster analysis; automatic action requires persistence and high confidence.',
               'NEW',(r.current_observation_date::timestamp AT TIME ZONE 'UTC')+interval '14 days'
          FROM pixel_anomalies a
          JOIN pixel_anomaly_runs r ON r.id=a.run_id
          JOIN fields f ON f.id=a.field_id AND f.enterprise_id=a.enterprise_id
         WHERE a.area_ha>=GREATEST(0.25,f.area_ha*0.01)
           AND a.status IN ('open','inspection_created')
        ON CONFLICT (enterprise_id,field_id,index_code,scene_id,zone_key,rule_version) DO NOTHING
        RETURNING id
    """), {"collection_run": run.run_id, "rule": RULE_VERSION}).fetchall()
    promotion = promote_automatic_candidates(run)
    run.session.commit()
    return {"inserted_candidates": len(inserted), **promotion}


def release_apply_run(run: ApplyRun) -> Exception | None:
    """Release the run's advisory lock and connection.

    Returns the first failure instead of raising it: a release failure must
    never replace the caller's original exception, which is the only record of
    why the cycle ended. The rollback is what makes the unlock reachable after
    a failed write — an aborted transaction rejects every further statement,
    including the unlock itself.
    """
    failure: Exception | None = None
    try:
        run.session.rollback()
        run.session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
        run.session.commit()
    except Exception as exc:
        failure = exc
    finally:
        try:
            run.session.close()
        except Exception as exc:
            failure = failure if failure is not None else exc
        connection = getattr(run, "connection", None)
        if connection is not None:
            try:
                connection.close()
            except Exception as exc:
                failure = failure if failure is not None else exc
    return failure


def finish_apply_run(run: ApplyRun, *, exit_code: int, provider_status: str, counters: dict, failure_category: str | None) -> None:
    status = "succeeded" if exit_code == 0 and provider_status != "degraded" else "degraded" if exit_code in {0, 1} else "failed"
    primary_error: Exception | None = None
    try:
        run.session.execute(text("""
            UPDATE satellite_collection_runs SET status=:status,provider_status=:provider,
              failure_category=:failure,counters=CAST(:counters AS jsonb),heartbeat_at=now(),
              finished_at=now(),updated_at=now() WHERE id=:id
        """), {"id": run.run_id, "status": status, "provider": provider_status,
               "failure": failure_category, "counters": json.dumps(counters)})
        run.session.commit()
    except Exception as exc:
        primary_error = exc
        # A row left 'running' is read as an in-flight cycle: the reconciler
        # raises an external-source alert on it six hours later and the next
        # cycle refuses to start against the same run key. Record the terminal
        # status without the counters payload, which may itself be why the
        # write failed.
        try:
            run.session.rollback()
            run.session.execute(text("""
                UPDATE satellite_collection_runs SET status=:status,provider_status=:provider,
                  failure_category=:failure,heartbeat_at=now(),finished_at=now(),updated_at=now()
                 WHERE id=:id AND status='running'
            """), {"id": run.run_id, "status": status, "provider": provider_status,
                   "failure": failure_category})
            run.session.commit()
        except Exception:
            try:
                run.session.rollback()
            except Exception:
                pass
    finally:
        release_error = release_apply_run(run)
    if primary_error is not None:
        raise primary_error
    if release_error is not None:
        raise release_error


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
    """Operator promotion of one candidate through the canonical INSERT."""
    from services.anomaly_inspections import Actor, is_unique_violation

    role, actor_id, enterprise_id = _actor(user, write=True)
    try:
        result = promote_candidate(
            db, candidate_id=candidate_id, actor=Actor(int(actor_id), role, enterprise_id),
            reason=reason.strip(), title="Проверить спутниковую аномалию",
            origin="monitoring_review", allowed_states=frozenset({"NEW", "CONFIRMED"}),
            expected_version=expected_version,
        )
        db.commit()
        return {"id": candidate_id, "state": result["state"], "version": result["version"],
                "inspection_id": result["inspection_id"]}
    except CandidatePromotionError as error:
        db.rollback(); raise HTTPException(error.status, error.detail) from None
    except IntegrityError as error:
        db.rollback()
        if not is_unique_violation(error):
            raise
        raise HTTPException(409, "Concurrent inspection conflict") from None
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise
