"""Insert one explicitly non-live anomaly fixture into the isolated R1 database."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid

import psycopg2
from psycopg2.extras import Json


ALLOWED_EVIDENCE_PREFIX = Path(r"C:\AgroSat_backups\PROGRAM_R1_COMPLETION_RUN")
ALLOWED_TEMP_PREFIX = Path(r"C:\tmp")


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--field-id", type=int, default=2)
    parser.add_argument("--port", type=int, default=55439)
    args = parser.parse_args()

    credentials_path = args.credentials.resolve(strict=True)
    evidence_path = args.evidence.resolve(strict=False)
    if ALLOWED_TEMP_PREFIX not in credentials_path.parents:
        raise RuntimeError("Credentials must remain under the isolated temporary root.")
    if ALLOWED_EVIDENCE_PREFIX not in evidence_path.parents:
        raise RuntimeError("Evidence must remain under the completion evidence root.")
    if args.field_id != 2 or args.port != 55439:
        raise RuntimeError("Unexpected isolated anomaly fixture target.")

    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    fixture_id = uuid.uuid4().hex
    with psycopg2.connect(
        host="127.0.0.1",
        port=args.port,
        dbname="agrosat_r1_completion",
        user=credentials["dbUser"],
        password=credentials["dbPassword"],
        connect_timeout=5,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), inet_server_addr()::text, "
                "inet_server_port(), (SELECT version_num FROM alembic_version)"
            )
            database_name, address, port, migration_head = cursor.fetchone()
            if (
                database_name != "agrosat_r1_completion"
                or str(address).split("/", 1)[0] != "127.0.0.1"
                or port != args.port
                or migration_head != "0012_commercial_tenant_boundary"
            ):
                raise RuntimeError("Database target identity check failed.")

            cursor.execute(
                "SELECT enterprise_id FROM fields WHERE id=%s",
                (args.field_id,),
            )
            field_row = cursor.fetchone()
            if field_row is None:
                raise RuntimeError("Qualification field is unavailable.")
            enterprise_id = field_row[0]

            cursor.execute(
                "SELECT id,captured_date FROM ndvi_records "
                "WHERE field_id=%s ORDER BY captured_date DESC LIMIT 2",
                (args.field_id,),
            )
            observations = cursor.fetchall()
            if len(observations) != 2:
                raise RuntimeError("Two accepted qualification observations are required.")
            current_id, current_date = observations[0]
            comparison_id, comparison_date = observations[1]

            run_key = hashlib.sha256(
                f"program-r1-anomaly-run:{fixture_id}".encode("utf-8")
            ).hexdigest()
            threshold_hash = hashlib.sha256(
                b"program-r1-anomaly-qualification-thresholds-v1"
            ).hexdigest()
            cursor.execute(
                """
                INSERT INTO pixel_anomaly_runs
                  (enterprise_id,field_id,index_code,current_record_type,
                   current_ndvi_record_id,current_observation_date,
                   comparison_record_type,comparison_ndvi_record_id,
                   comparison_observation_date,algorithm_version,run_key,
                   threshold_hash,thresholds,quality_summary,result_status,
                   reason_codes,provenance,started_at,finished_at)
                VALUES
                  (%s,%s,'ndvi','ndvi_record',%s,%s,'ndvi_record',%s,%s,
                   'program-r1-browser-fixture-v1',%s,%s,%s,%s,'detected',%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    enterprise_id,
                    args.field_id,
                    current_id,
                    current_date,
                    comparison_id,
                    comparison_date,
                    run_key,
                    threshold_hash,
                    Json({"classification": "qualification_fixture"}),
                    Json({"valid_pixels_pct": 96.0, "cloud_cover_pct": 5.0}),
                    Json([]),
                    Json({"source": "program_r1_browser_fixture", "live_provider": False}),
                    datetime.now(timezone.utc),
                    datetime.now(timezone.utc),
                ),
            )
            run_id = cursor.fetchone()[0]

            zone_key = hashlib.sha256(
                f"program-r1-anomaly-zone:{fixture_id}".encode("utf-8")
            ).hexdigest()
            cursor.execute(
                """
                INSERT INTO pixel_anomalies
                  (run_id,enterprise_id,field_id,index_code,algorithm_version,
                   zone_key,geometry,area_ha,score,severity,persistence_count,
                   classification,confidence,status,quality_summary,provenance)
                VALUES
                  (%s,%s,%s,'ndvi','program-r1-browser-fixture-v1',%s,
                   ST_GeomFromText(%s,4326),4.1,0.81,'high',2,'persistent',
                   0.87,'open',%s,%s)
                RETURNING id
                """,
                (
                    run_id,
                    enterprise_id,
                    args.field_id,
                    zone_key,
                    "POLYGON((64.4450 39.7610,64.4480 39.7610,64.4480 39.7640,64.4450 39.7640,64.4450 39.7610))",
                    Json({"valid_pixels_pct": 96.0}),
                    Json({"source": "program_r1_browser_fixture", "live_provider": False}),
                ),
            )
            anomaly_id = cursor.fetchone()[0]
        connection.commit()

    evidence = {
        "schemaVersion": 1,
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "databaseClass": "isolated_ephemeral",
        "dataClass": "deterministic_non_live_browser_anomaly_fixture",
        "fieldId": args.field_id,
        "runId": run_id,
        "anomalyId": anomaly_id,
        "indexCode": "ndvi",
        "currentObservationDate": current_date.isoformat(),
        "comparisonObservationDate": comparison_date.isoformat(),
        "liveSentinelEvidence": False,
        "credentialsPrinted": False,
        "productionContacted": False,
        "productionWrites": 0,
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(evidence_path, evidence)
    print(
        json.dumps(
            {
                "status": "inserted",
                "anomalyId": anomaly_id,
                "liveSentinelEvidence": False,
                "productionWrites": 0,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
