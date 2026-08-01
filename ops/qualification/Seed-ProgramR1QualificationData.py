"""Seed deterministic, explicitly non-live data into the isolated R1 database."""

from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

from passlib.context import CryptContext
import psycopg2
from psycopg2.extras import Json
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from config import settings  # noqa: E402
from services.cache import cache_set_binary  # noqa: E402


EXPECTED_DATABASE_HOST = "127.0.0.1"
EXPECTED_DATABASE_PORT = 55439
EXPECTED_DATABASE_NAME = "agrosat_r1_completion"
EXPECTED_REDIS_PORT = 56381
SEED_VERSION = "program-r1-completion-fixture-v1"
PASSWORDS = CryptContext(schemes=["bcrypt"], deprecated="auto")
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNk+M/wHwAF/gL+ZPZLAAAAAElFTkSuQmCC"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    return parser.parse_args()


def assert_isolated_targets() -> None:
    database = make_url(settings.database_url)
    if (
        database.host != EXPECTED_DATABASE_HOST
        or database.port != EXPECTED_DATABASE_PORT
        or database.database != EXPECTED_DATABASE_NAME
    ):
        raise RuntimeError("Refusing to seed a non-qualification database target")

    redis_target = urlparse(settings.redis_url)
    if redis_target.hostname != "127.0.0.1" or redis_target.port != EXPECTED_REDIS_PORT:
        raise RuntimeError("Refusing to seed a non-qualification Redis target")


def insert_id(cursor, statement: str, values: tuple) -> int:
    cursor.execute(statement, values)
    return int(cursor.fetchone()[0])


def main() -> int:
    args = parse_args()
    assert_isolated_targets()
    credentials = json.loads(args.credentials.read_text(encoding="utf-8"))
    users = credentials["users"]

    connection = psycopg2.connect(settings.database_url)
    connection.autocommit = False
    ids: dict[str, object] = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT (SELECT count(*) FROM enterprises), "
                "(SELECT count(*) FROM users), (SELECT count(*) FROM fields)"
            )
            if tuple(cursor.fetchone()) != (0, 0, 0):
                raise RuntimeError("Qualification database is not empty")

            primary_enterprise = insert_id(
                cursor,
                "INSERT INTO enterprises "
                "(name,name_uz,code,region,total_area_ha,is_active,notes,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,true,%s,%s,%s) RETURNING id",
                (
                    "R1 Qualification Tenant",
                    "R1 Qualification Tenant",
                    "R1Q-PRIMARY",
                    "Bukhara",
                    72.5,
                    "Deterministic isolated qualification fixture; not production data.",
                    datetime(2026, 7, 1),
                    datetime(2026, 7, 1),
                ),
            )
            isolation_enterprise = insert_id(
                cursor,
                "INSERT INTO enterprises "
                "(name,code,region,total_area_ha,is_active,notes,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,true,%s,%s,%s) RETURNING id",
                (
                    "R1 Isolation Tenant",
                    "R1Q-ISOLATION",
                    "Bukhara",
                    18.0,
                    "Cross-tenant denial fixture; not production data.",
                    datetime(2026, 7, 1),
                    datetime(2026, 7, 1),
                ),
            )
            ids["enterprises"] = {
                "primary": primary_enterprise,
                "isolation": isolation_enterprise,
            }

            crop_type = insert_id(
                cursor,
                "INSERT INTO crop_types "
                "(code,name_ru,name_uz,name_en,typical_sowing_month,typical_harvest_month,growth_stages,alerts_config) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (
                    "r1_cotton",
                    "Хлопчатник — квалификационный набор",
                    "Paxta — qualification",
                    "Cotton qualification fixture",
                    4,
                    10,
                    Json([]),
                    Json({}),
                ),
            )
            ids["cropType"] = crop_type

            field_specs = (
                (
                    primary_enterprise,
                    "R1 Field Alpha",
                    "R1-A",
                    "POLYGON((64.4200 39.7600,64.4300 39.7600,64.4300 39.7700,64.4200 39.7700,64.4200 39.7600))",
                    48.5,
                    39.765,
                    64.425,
                ),
                (
                    primary_enterprise,
                    "R1 Field Beta",
                    "R1-B",
                    "POLYGON((64.4400 39.7600,64.4480 39.7600,64.4480 39.7680,64.4400 39.7680,64.4400 39.7600))",
                    24.0,
                    39.764,
                    64.444,
                ),
                (
                    isolation_enterprise,
                    "R1 Hidden Tenant Field",
                    "R1-X",
                    "POLYGON((64.5000 39.7600,64.5060 39.7600,64.5060 39.7660,64.5000 39.7660,64.5000 39.7600))",
                    18.0,
                    39.763,
                    64.503,
                ),
            )
            field_ids: list[int] = []
            for enterprise_id, name, code, geometry, area, latitude, longitude in field_specs:
                field_id = insert_id(
                    cursor,
                    "INSERT INTO fields "
                    "(enterprise_id,name,code,geometry,area_ha,centroid_lat,centroid_lon,soil_type,irrigation_type,is_active,notes,created_at,updated_at) "
                    "VALUES (%s,%s,%s,ST_GeomFromText(%s,4326),%s,%s,%s,%s,%s,true,%s,%s,%s) RETURNING id",
                    (
                        enterprise_id,
                        name,
                        code,
                        geometry,
                        area,
                        latitude,
                        longitude,
                        "qualification loam",
                        "canal",
                        "Deterministic isolated geometry in EPSG:4326.",
                        datetime(2026, 7, 1),
                        datetime(2026, 7, 1),
                    ),
                )
                field_ids.append(field_id)
                cursor.execute(
                    "INSERT INTO crop_seasons "
                    "(field_id,crop_type_id,season_year,variety,planting_date,expected_harvest_date,planned_yield_tha,fertilizer_plan,notes,created_at) "
                    "VALUES (%s,%s,2026,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        field_id,
                        crop_type,
                        "R1 fixture variety",
                        datetime(2026, 4, 10),
                        datetime(2026, 10, 10),
                        3.2,
                        Json({"status": "human_review_required"}),
                        "Qualification fixture only.",
                        datetime(2026, 7, 1),
                    ),
                )
            ids["fields"] = {
                "attention": field_ids[0],
                "anomaly": field_ids[1],
                "crossTenant": field_ids[2],
            }

            role_ids: dict[str, int] = {}
            role_enterprises = {
                "admin": None,
                "manager": primary_enterprise,
                "agronomist": primary_enterprise,
                "viewer": primary_enterprise,
                "disabled": primary_enterprise,
            }
            for role in ("admin", "manager", "agronomist", "viewer", "disabled"):
                account = users[role]
                role_value = "viewer" if role == "disabled" else role
                role_ids[role] = insert_id(
                    cursor,
                    "INSERT INTO users "
                    "(enterprise_id,email,full_name,role,hashed_password,is_active,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (
                        role_enterprises[role],
                        account["email"].strip().lower(),
                        f"R1 {role.capitalize()} Qualification",
                        role_value,
                        PASSWORDS.hash(account["password"]),
                        role != "disabled",
                        datetime(2026, 7, 1),
                    ),
                )
            ids["users"] = role_ids

            membership_roles = {
                "admin": ("owner", "active"),
                "manager": ("manager", "active"),
                "agronomist": ("agronomist", "active"),
                "viewer": ("viewer", "active"),
                "disabled": ("viewer", "suspended"),
            }
            for role, (membership_role, status) in membership_roles.items():
                cursor.execute(
                    "INSERT INTO enterprise_memberships "
                    "(enterprise_id,user_id,membership_role,status,created_by_id) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (
                        primary_enterprise,
                        role_ids[role],
                        membership_role,
                        status,
                        role_ids["admin"],
                    ),
                )

            for enterprise_id in (primary_enterprise, isolation_enterprise):
                cursor.execute(
                    "INSERT INTO enterprise_commercial_profiles "
                    "(enterprise_id,plan_code,subscription_state,feature_flags,quota_limits,retention_policy,branding,namespaces,updated_by_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        enterprise_id,
                        "first_pilot",
                        "active",
                        Json(
                            {
                                "offline_scouting": True,
                                "pixel_anomalies": True,
                                "wialon": False,
                            }
                        ),
                        Json({"fields": 2000, "users": 100}),
                        Json({"classification": "qualification_fixture"}),
                        Json({"product": "AgroSat"}),
                        Json({"environment": "qualification"}),
                        role_ids["admin"],
                    ),
                )

            ndvi_ids: dict[str, int] = {}
            ndvi_specs = (
                ("attention_reference", field_ids[0], date(2026, 7, 12), 0.62, 0.08, 96.0),
                ("attention_current", field_ids[0], date(2026, 7, 28), 0.38, 0.04, 97.0),
                ("anomaly_reference", field_ids[1], date(2026, 7, 12), 0.66, 0.07, 95.0),
                ("anomaly_current", field_ids[1], date(2026, 7, 28), 0.41, 0.05, 96.0),
                ("cross_tenant", field_ids[2], date(2026, 7, 28), 0.55, 0.03, 98.0),
            )
            for label, field_id, captured, mean_value, cloud, valid in ndvi_specs:
                ndvi_ids[label] = insert_id(
                    cursor,
                    "INSERT INTO ndvi_records "
                    "(field_id,captured_date,processed_at,mean_ndvi,min_ndvi,max_ndvi,std_ndvi,p10_ndvi,p90_ndvi,cloud_cover_pct,valid_pixels_pct,satellite,ndvi_change,ndvi_change_pct) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (
                        field_id,
                        captured,
                        datetime(2026, 7, 29),
                        mean_value,
                        max(-1.0, mean_value - 0.12),
                        min(1.0, mean_value + 0.12),
                        0.07,
                        max(-1.0, mean_value - 0.08),
                        min(1.0, mean_value + 0.08),
                        cloud,
                        valid,
                        "Sentinel-2",
                        None,
                        None,
                    ),
                )
            ids["ndviRecords"] = ndvi_ids

            for field_id, baseline, current in (
                (field_ids[0], 0.58, 0.34),
                (field_ids[1], 0.61, 0.39),
            ):
                for index_code, adjustment in (
                    ("savi", -0.03),
                    ("evi", -0.05),
                    ("ndmi", -0.10),
                    ("ndre", -0.07),
                ):
                    for captured, value in (
                        (date(2026, 7, 12), baseline + adjustment),
                        (date(2026, 7, 28), current + adjustment),
                    ):
                        cursor.execute(
                            "INSERT INTO satellite_index_records "
                            "(field_id,captured_date,index_code,mean_value,min_value,max_value,std_value,p10_value,p90_value,valid_pixels_pct,cloud_cover_pct,satellite,created_at) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (
                                field_id,
                                captured,
                                index_code,
                                value,
                                value - 0.1,
                                value + 0.1,
                                0.06,
                                value - 0.07,
                                value + 0.07,
                                96.0,
                                4.0,
                                "Sentinel-2",
                                datetime(2026, 7, 29),
                            ),
                        )

            for field_id, ndvi_id, severity, source_key, title in (
                (
                    field_ids[0],
                    ndvi_ids["attention_current"],
                    "critical",
                    "r1-fixture-attention",
                    "Field requires human inspection",
                ),
                (
                    field_ids[1],
                    ndvi_ids["anomaly_current"],
                    "warning",
                    "r1-fixture-anomaly",
                    "Anomaly zone requires review",
                ),
            ):
                cursor.execute(
                    "INSERT INTO alerts "
                    "(field_id,ndvi_record_id,alert_type,severity,title,description,recommendation,triggered_value,threshold_value,triggered_at,is_active,source,source_key) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s)",
                    (
                        field_id,
                        ndvi_id,
                        "ndvi_drop",
                        severity,
                        title,
                        "Qualification signal only; this is not an automatic diagnosis.",
                        "Assign a human field inspection.",
                        0.38,
                        0.45,
                        datetime(2026, 7, 29, 8, 0),
                        "qualification_fixture",
                        source_key,
                    ),
                )

            anomaly_run = insert_id(
                cursor,
                "INSERT INTO pixel_anomaly_runs "
                "(enterprise_id,field_id,index_code,current_record_type,current_ndvi_record_id,current_observation_date,comparison_record_type,comparison_ndvi_record_id,comparison_observation_date,algorithm_version,run_key,threshold_hash,thresholds,quality_summary,result_status,reason_codes,provenance,started_at,finished_at) "
                "VALUES (%s,%s,'ndvi','ndvi_record',%s,%s,'ndvi_record',%s,%s,%s,%s,%s,%s,%s,'detected',%s,%s,%s,%s) RETURNING id",
                (
                    primary_enterprise,
                    field_ids[1],
                    ndvi_ids["anomaly_current"],
                    date(2026, 7, 28),
                    ndvi_ids["anomaly_reference"],
                    date(2026, 7, 12),
                    "qualification-fixture-v1",
                    hashlib.sha256(b"r1-anomaly-run").hexdigest(),
                    hashlib.sha256(b"r1-anomaly-thresholds").hexdigest(),
                    Json({"classification": "qualification_fixture"}),
                    Json({"valid_pixels_pct": 96.0, "cloud_cover_pct": 5.0}),
                    Json([]),
                    Json({"source": "qualification_fixture", "live_provider": False}),
                    datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc),
                    datetime(2026, 7, 29, 9, 1, tzinfo=timezone.utc),
                ),
            )
            anomaly_id = insert_id(
                cursor,
                "INSERT INTO pixel_anomalies "
                "(run_id,enterprise_id,field_id,index_code,algorithm_version,zone_key,geometry,area_ha,score,severity,persistence_count,classification,confidence,status,quality_summary,provenance) "
                "VALUES (%s,%s,%s,'ndvi',%s,%s,ST_GeomFromText(%s,4326),%s,%s,'high',%s,'persistent',%s,'open',%s,%s) RETURNING id",
                (
                    anomaly_run,
                    primary_enterprise,
                    field_ids[1],
                    "qualification-fixture-v1",
                    hashlib.sha256(b"r1-anomaly-zone").hexdigest(),
                    "POLYGON((64.4410 39.7610,64.4440 39.7610,64.4440 39.7640,64.4410 39.7640,64.4410 39.7610))",
                    4.2,
                    0.82,
                    2,
                    0.88,
                    Json({"valid_pixels_pct": 96.0}),
                    Json({"source": "qualification_fixture", "live_provider": False}),
                ),
            )
            ids["pixelAnomaly"] = {"run": anomaly_run, "zone": anomaly_id}

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    cached_rasters = 0
    for field_id in (field_ids[0], field_ids[1]):
        for size in (256, 512, 768, 1024):
            key = f"ndvi-raster:ndvi-rgba-scl-v1:{field_id}:2026-07-28:{size}"
            if cache_set_binary(key, PNG, 7 * 24 * 60 * 60):
                cached_rasters += 1

    summary = {
        "schemaVersion": 1,
        "seedVersion": SEED_VERSION,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "databaseClass": "isolated_ephemeral",
        "dataClass": "deterministic_sanitized_qualification_fixture",
        "satelliteFixtureIsLiveEvidence": False,
        "providerCalls": 0,
        "ids": ids,
        "counts": {
            "enterprises": 2,
            "fields": 3,
            "users": 5,
            "roles": ["admin", "manager", "agronomist", "viewer", "disabled"],
            "ndviRecords": len(ndvi_ids),
            "multiIndexRecords": 16,
            "alerts": 2,
            "pixelAnomalyRuns": 1,
            "pixelAnomalies": 1,
            "cachedRasterFixtures": cached_rasters,
        },
        "credentials": "protected_ephemeral_file_excluded_from_evidence",
        "passwordHashesIncluded": False,
        "productionContacted": False,
        "productionWrites": 0,
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "seeded",
                "users": 5,
                "fields": 3,
                "credentialValuesPrinted": False,
                "providerCalls": 0,
                "productionWrites": 0,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
