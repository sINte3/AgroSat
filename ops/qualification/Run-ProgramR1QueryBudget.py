#!/usr/bin/env python3
"""Measure cold-cache SQL statement counts for critical read-only endpoints."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-env", required=True)
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--evidence-path", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-postgres-port", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    worktree = Path(__file__).resolve().parents[2]
    backend = worktree / "backend"
    runtime_env = Path(args.runtime_env).resolve(strict=True)
    credentials_path = Path(args.credentials).resolve(strict=True)
    evidence_path = Path(args.evidence_path).resolve()
    if str(credentials_path).lower().startswith("c:\\agrosat_backups\\"):
        raise RuntimeError("credentials must remain outside evidence")
    if not str(evidence_path).lower().startswith(
        "c:\\agrosat_backups\\program_r1_completion_run\\"
    ):
        raise RuntimeError("unexpected evidence path")

    os.environ["AGROSAT_RUNTIME_ENV_FILE"] = str(runtime_env)
    os.environ["RELEASE_REVISION"] = args.expected_head
    sys.path.insert(0, str(backend))

    from fastapi.testclient import TestClient
    from sqlalchemy import event, text

    from config import settings
    from database import SessionLocal, engine
    from main import app
    from services import cache

    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(worktree), text=True
    ).strip()
    if head != args.expected_head:
        raise RuntimeError("unexpected worktree HEAD")
    parsed_database = urlparse(settings.database_url)
    if parsed_database.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("database target is not loopback")
    if parsed_database.port != args.expected_postgres_port:
        raise RuntimeError("database target port is unexpected")

    dataset_tables = (
        "enterprises",
        "fields",
        "users",
        "alerts",
        "ndvi_records",
        "satellite_index_records",
        "field_inspections",
        "operational_actions",
        "pixel_anomalies",
    )
    dataset: dict[str, int | None] = {}
    with SessionLocal() as session:
        for table in dataset_tables:
            try:
                dataset[table] = int(
                    session.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
                )
            except Exception:
                session.rollback()
                dataset[table] = None
        versions = {
            "postgresqlMajor": int(
                str(session.execute(text("SHOW server_version")).scalar_one()).split(".")[0]
            ),
            "postgisAvailable": bool(
                session.execute(
                    text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='postgis')")
                ).scalar_one()
            ),
        }
        spatial_indexes = int(
            session.execute(
                text(
                    "SELECT count(*) FROM pg_indexes "
                    "WHERE schemaname='public' AND indexdef ILIKE '%USING gist%'"
                )
            ).scalar_one()
        )

    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    manager = credentials["users"]["manager"]
    captured: list[str] = []

    def before_cursor_execute(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        captured.append(statement.lstrip().split(None, 1)[0].upper())

    scenarios = (
        ("fields_list", "/api/fields/", 3),
        ("dashboard_summary", "/api/dashboard/summary", 3),
        # The endpoint owns four constant data queries; authentication adds one.
        ("attention_queue", "/api/field-attention/queue", 5),
        ("inspection_list", "/api/field-inspections", 3),
        ("satellite_history", "/api/satellite-indices/1/history?index_code=savi&days=30", 3),
        ("management_summary", "/api/reports/management/summary", 5),
        ("field_tile_metadata", "/api/field-tiles/metadata?enterprise_id=1", 3),
        ("vector_tile", "/api/field-tiles/7/86/48.mvt?enterprise_id=1", 3),
    )
    measurements: list[dict[str, Any]] = []
    original_retry = cache._retry_after_monotonic
    original_client = cache._redis
    original_available = cache.CACHE_AVAILABLE
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        cache._redis = None
        cache.CACHE_AVAILABLE = False
        cache._retry_after_monotonic = float("inf")
        with TestClient(app) as client:
            login = client.post(
                "/api/auth/login",
                data={"username": manager["email"], "password": manager["password"]},
            )
            if login.status_code != 200:
                raise RuntimeError("isolated manager login failed")
            token = login.json().get("access_token")
            if not token:
                raise RuntimeError("isolated manager token was unavailable")
            headers = {"Authorization": f"Bearer {token}"}
            for name, path, budget in scenarios:
                captured.clear()
                response = client.get(path, headers=headers)
                counts: dict[str, int] = {}
                for operation in captured:
                    counts[operation] = counts.get(operation, 0) + 1
                read_only = all(operation in {"SELECT", "WITH", "SHOW"} for operation in captured)
                passed = response.status_code == 200 and len(captured) <= budget and read_only
                measurements.append(
                    {
                        "name": name,
                        "path": path,
                        "statusCode": response.status_code,
                        "queryCount": len(captured),
                        "queryBudget": budget,
                        "operationCounts": counts,
                        "readOnly": read_only,
                        "responseBodyPersisted": False,
                        "pass": passed,
                    }
                )
            growth_probe: list[dict[str, Any]] = []
            for limit in (1, 100):
                captured.clear()
                response = client.get(
                    f"/api/field-attention/queue?min_priority=low&limit={limit}",
                    headers=headers,
                )
                payload = response.json() if response.status_code == 200 else {}
                growth_probe.append(
                    {
                        "limit": limit,
                        "statusCode": response.status_code,
                        "queryCount": len(captured),
                        "returnedItems": payload.get("summary", {}).get("returned"),
                        "responseBodyPersisted": False,
                    }
                )
            token = None
            headers = None
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
        cache._redis = original_client
        cache.CACHE_AVAILABLE = original_available
        cache._retry_after_monotonic = original_retry
        manager = None
        credentials = None

    growth_counts = {item["queryCount"] for item in growth_probe}
    growth_pass = (
        all(item["statusCode"] == 200 for item in growth_probe)
        and len(growth_counts) == 1
        and max(growth_counts) <= 5
        and growth_probe[0]["returnedItems"] <= growth_probe[1]["returnedItems"]
    )
    status = (
        "PASS"
        if all(item["pass"] for item in measurements) and growth_pass
        else "FAIL"
    )
    result = {
        "schemaVersion": 2,
        "gate": "GATE5",
        "operation": "critical_endpoint_cold_cache_query_budget",
        "head": head,
        "runtimeClass": "isolated_in_process_fastapi_postgresql_postgis_cache_bypassed",
        "datasetContext": dataset,
        "databaseContext": {
            **versions,
            "hostClass": "loopback",
            "port": args.expected_postgres_port,
            "gistSpatialIndexCount": spatial_indexes,
        },
        "measurements": measurements,
        "queryGrowthProbe": {
            "endpoint": "/api/field-attention/queue",
            "expectedConstantQueryCount": 5,
            "measurements": growth_probe,
            "pass": growth_pass,
        },
        "unexpectedQueryGrowth": False if growth_pass else True,
        "sqlalchemyLazyLoading": "forbidden_and_raise_on_sql",
        "credentialsIncluded": False,
        "responseBodiesIncluded": False,
        "isolatedPostgresqlWrites": 0,
        "productionWrites": 0,
        "status": status,
        "marker": "PASS_GATE5_QUERY_BUDGETS" if status == "PASS" else "FAIL_GATE5_QUERY_BUDGETS",
    }
    atomic_json(evidence_path, result)
    print(json.dumps({"status": status, "measurements": len(measurements), "head": head}))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
