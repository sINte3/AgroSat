"""Insert one explicitly non-live temporal fixture into the isolated R1 database."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
import uuid

import psycopg2


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
    parser.add_argument("--field-id", type=int, default=1)
    parser.add_argument("--captured-date", type=date.fromisoformat, default=date(2026, 8, 5))
    parser.add_argument("--port", type=int, default=55439)
    args = parser.parse_args()

    credentials_path = args.credentials.resolve(strict=True)
    evidence_path = args.evidence.resolve(strict=False)
    if ALLOWED_TEMP_PREFIX not in credentials_path.parents:
        raise RuntimeError("Credentials must remain under the isolated temporary root.")
    if ALLOWED_EVIDENCE_PREFIX not in evidence_path.parents:
        raise RuntimeError("Evidence must remain under the completion evidence root.")
    if args.field_id <= 0 or args.port != 55439:
        raise RuntimeError("Unexpected isolated database target.")

    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
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
                """
                INSERT INTO ndvi_records
                  (field_id,captured_date,processed_at,mean_ndvi,min_ndvi,max_ndvi,
                   std_ndvi,p10_ndvi,p90_ndvi,cloud_cover_pct,valid_pixels_pct,
                   satellite,image_url,ndvi_change,ndvi_change_pct)
                VALUES
                  (%s,%s,%s,0.64,0.42,0.81,0.07,0.48,0.75,4.0,96.0,
                   'PROGRAM_R1_TEMPORAL_FIXTURE',NULL,0.18,39.13)
                ON CONFLICT (field_id,captured_date) DO NOTHING
                RETURNING id
                """,
                (args.field_id, args.captured_date, datetime.now(timezone.utc)),
            )
            row = cursor.fetchone()
            inserted = row is not None
            if inserted:
                record_id = row[0]
            else:
                cursor.execute(
                    "SELECT id,satellite FROM ndvi_records "
                    "WHERE field_id=%s AND captured_date=%s",
                    (args.field_id, args.captured_date),
                )
                record_id, satellite = cursor.fetchone()
                if satellite != "PROGRAM_R1_TEMPORAL_FIXTURE":
                    raise RuntimeError("Existing observation is not the qualification fixture.")
        connection.commit()

    evidence = {
        "schemaVersion": 1,
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "databaseClass": "isolated_ephemeral",
        "dataClass": "deterministic_non_live_temporal_qualification_fixture",
        "fieldId": args.field_id,
        "capturedDate": args.captured_date.isoformat(),
        "recordId": record_id,
        "inserted": inserted,
        "indexCode": "ndvi",
        "meanValue": 0.64,
        "cloudCoverPct": 4.0,
        "validPixelsPct": 96.0,
        "provenance": "PROGRAM_R1_TEMPORAL_FIXTURE",
        "liveSentinelEvidence": False,
        "credentialsPrinted": False,
        "productionContacted": False,
        "productionWrites": 0,
    }
    atomic_json(evidence_path, evidence)
    print(
        json.dumps(
            {
                "status": "inserted" if inserted else "reused",
                "recordId": record_id,
                "liveSentinelEvidence": False,
                "productionWrites": 0,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
