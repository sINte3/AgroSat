"""Health endpoints distinguish required DB readiness from optional cache."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.health import router
from services import health


class ScalarResult:
    def __init__(self, values):
        self.values = values

    def scalar_one(self):
        return self.values[0]

    def scalars(self):
        return iter(self.values)


class Connection:
    def __init__(self, revision="0005_field_inspections"):
        self.revision = revision
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement):
        sql = str(statement)
        self.calls.append(sql)
        return (
            ScalarResult([1])
            if sql == "SELECT 1"
            else ScalarResult([self.revision])
        )


def _collector_payload(status="succeeded", **overrides):
    payload = {
        "run_id": "a" * 32,
        "mode": "apply",
        "status": status,
        "started_at": "2026-07-28T00:00:00+00:00",
        "finished_at": "2026-07-28T00:01:00+00:00",
        "exit_code": 0,
        "failure_category": None,
        "stdout": "must not escape",
    }
    payload.update(overrides)
    return payload


def test_liveness_does_not_probe_dependencies():
    with patch("services.health.database_readiness") as database_probe:
        snapshot = health.liveness_snapshot()
    assert snapshot["status"] == "alive"
    database_probe.assert_not_called()


def test_database_readiness_uses_two_bounded_statements():
    connection = Connection()
    engine = Mock()
    engine.connect.return_value = connection
    result = health.database_readiness(engine)
    assert result["status"] == "ready"
    assert result["migration_revision"] == "0005_field_inspections"
    assert connection.calls == [
        "SELECT 1",
        "SELECT version_num FROM alembic_version",
    ]


def test_database_error_is_sanitized():
    engine = Mock()
    engine.connect.side_effect = RuntimeError("credential and host detail")
    result = health.database_readiness(engine)
    serialized = json.dumps(result)
    assert result["status"] == "unavailable"
    assert "credential" not in serialized
    assert "host detail" not in serialized


def test_cache_outage_does_not_override_database_readiness():
    connection = Connection()
    engine = Mock()
    engine.connect.return_value = connection
    snapshot = health.readiness_snapshot(
        engine=engine,
        cache_check=lambda: False,
        collector_directory="",
        collector_stale_after_seconds=3600,
    )
    assert snapshot["status"] == "ready"
    assert snapshot["components"]["cache"] == {
        "status": "unavailable",
        "required_for_api_readiness": False,
        "source_of_truth": False,
    }


def test_collector_status_is_bounded_sanitized_and_stale_aware():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        payload = _collector_payload()
        for filename in health.STATUS_FILES.values():
            (root / filename).write_text(json.dumps(payload), encoding="utf-8")
        snapshot = health.collector_readiness(
            directory,
            3600,
            now=datetime(2026, 7, 28, 2, 0, tzinfo=timezone.utc),
        )
    assert snapshot["status"] == "stale"
    assert snapshot["age_seconds"] == 7140
    serialized = json.dumps(snapshot)
    assert "must not escape" not in serialized
    assert "stdout" not in serialized
    assert directory not in serialized


def test_running_collector_uses_started_timestamp():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        payload = _collector_payload(
            status="running",
            finished_at=None,
            exit_code=None,
        )
        (root / health.STATUS_FILES["latest"]).write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        snapshot = health.collector_readiness(
            directory,
            3600,
            now=datetime(2026, 7, 28, 0, 10, tzinfo=timezone.utc),
        )
    assert snapshot["status"] == "running"
    assert snapshot["age_seconds"] == 600


def test_invalid_or_oversized_collector_status_is_not_exposed():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / health.STATUS_FILES["latest"]).write_text(
            "x" * (health.MAX_STATUS_FILE_BYTES + 1),
            encoding="utf-8",
        )
        snapshot = health.collector_readiness(directory, 3600)
    assert snapshot["status"] == "missing"
    assert snapshot["latest"] is None


def test_http_contract_keeps_compatibility_health_200():
    app = FastAPI()
    app.include_router(router)
    not_ready = {
        "status": "not_ready",
        "components": {"database": {"status": "unavailable"}},
    }
    with patch("api.health.current_readiness_snapshot", return_value=not_ready):
        client = TestClient(app)
        live = client.get("/health/live")
        ready = client.get("/health/ready")
        compatibility = client.get("/health")
    assert live.status_code == 200
    assert ready.status_code == 503
    assert compatibility.status_code == 200
    assert compatibility.json()["status"] == "degraded"
