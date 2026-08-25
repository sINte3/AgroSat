"""Read-only verification of TASK_219 isolated monitoring state."""

from __future__ import annotations

import json
import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


PREFIX = "agrosat_r3_task219_"


def main() -> int:
    url = make_url(os.environ["DATABASE_URL"])
    if url.database == "agrosat" or not (url.database or "").startswith(PREFIX):
        raise RuntimeError("TASK219_DATABASE_IDENTITY_REJECTED")
    engine = create_engine(url)
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))

        def count(sql: str) -> int:
            return int(connection.execute(text(sql)).scalar_one())

        result = {
            "database_identity": url.database,
            "production_database": False,
            "runs": count("SELECT count(*) FROM satellite_collection_runs"),
            "running_runs": count(
                "SELECT count(*) FROM satellite_collection_runs WHERE status='running'"
            ),
            "degraded_auth_runs": count(
                "SELECT count(*) FROM satellite_collection_runs "
                "WHERE status='degraded' AND provider_status='degraded' "
                "AND failure_category='auth'"
            ),
            "freshness_rows": count("SELECT count(*) FROM satellite_field_freshness"),
            "freshness_distinct_keys": count(
                "SELECT count(*) FROM (SELECT field_id,index_code FROM "
                "satellite_field_freshness GROUP BY field_id,index_code) x"
            ),
            "freshness_tenant_mismatches": count(
                "SELECT count(*) FROM satellite_field_freshness s JOIN fields f "
                "ON f.id=s.field_id WHERE s.enterprise_id<>f.enterprise_id"
            ),
            "provider_degraded_freshness": count(
                "SELECT count(*) FROM satellite_field_freshness "
                "WHERE status='PROVIDER_DEGRADED'"
            ),
            "candidates": count("SELECT count(*) FROM autonomous_anomaly_candidates"),
            "candidate_duplicate_keys": count(
                "SELECT count(*) FROM (SELECT enterprise_id,field_id,index_code,scene_id,"
                "zone_key,rule_version FROM autonomous_anomaly_candidates "
                "GROUP BY 1,2,3,4,5,6 HAVING count(*)>1) x"
            ),
            "automatic_inspection_links": count(
                "SELECT count(*) FROM autonomous_anomaly_candidates "
                "WHERE inspection_id IS NOT NULL"
            ),
        }
        connection.rollback()
    engine.dispose()
    result["pass"] = (
        result["running_runs"] == 0
        and result["degraded_auth_runs"] >= 1
        and result["freshness_rows"] == 1375
        and result["freshness_distinct_keys"] == 1375
        and result["freshness_tenant_mismatches"] == 0
        and result["candidate_duplicate_keys"] == 0
        and result["automatic_inspection_links"] == 0
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
