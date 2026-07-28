"""Metrics and JSON logs remain bounded and sanitized."""

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text

from api.operations import metrics
from services.logging_config import SanitizingJsonFormatter
from services.metrics import (
    API_DURATION_BUCKETS,
    MetricRegistry,
    MetricsMiddleware,
    collector_metrics,
    instrument_engine,
)


def test_registry_renders_bounded_counter_and_histogram_labels():
    registry = MetricRegistry()
    registry.increment(
        "agrosat_api_requests_total",
        labels={
            "method": "GET",
            "route": "/api/fields/{field_id}",
            "status_class": "2xx",
        },
    )
    registry.observe(
        "agrosat_api_request_duration_seconds",
        0.04,
        labels={"method": "GET", "route": "/api/fields/{field_id}"},
        buckets=API_DURATION_BUCKETS,
    )
    rendered = registry.render()
    assert "field_id" in rendered
    assert "email" not in rendered
    assert 'le="+Inf"' in rendered
    assert "_count" in rendered
    assert "_sum" in rendered


def test_database_metrics_use_operation_not_raw_sql():
    registry = MetricRegistry()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    instrument_engine(engine, registry=registry)
    with engine.connect() as connection:
        connection.execute(text("SELECT 1")).scalar_one()
    rendered = registry.render()
    assert 'operation="select"' in rendered
    assert "SELECT 1" not in rendered


def test_metrics_middleware_uses_route_template_not_requested_id():
    async def app(scope, receive, send):
        scope["route"] = SimpleNamespace(path="/api/fields/{field_id}")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = MetricsMiddleware(app)
    sent = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/fields/987654",
            },
            receive,
            send,
        )
    )
    from services.metrics import REGISTRY

    rendered = REGISTRY.render()
    assert "/api/fields/{field_id}" in rendered
    assert "987654" not in rendered


def test_collector_metrics_have_only_provider_and_outcome_labels():
    rendered = collector_metrics(
        {
            "latest": {
                "status": "succeeded",
                "failure_category": None,
                "duration_seconds": 1.25,
                "providers": [
                    {
                        "provider": "ndvi",
                        "counters": {
                            "success_count": 4,
                            "failure_count": 1,
                        },
                    }
                ],
            }
        }
    )
    assert 'provider="ndvi"' in rendered
    assert 'outcome="failure_count"' in rendered
    assert "field_id" not in rendered


def test_metrics_endpoint_denies_non_management_role():
    with pytest.raises(HTTPException) as exc:
        metrics(SimpleNamespace(role="agronomist"))
    assert exc.value.status_code == 403


def test_metrics_endpoint_allows_management_without_business_labels():
    response = metrics(SimpleNamespace(role="admin"))
    rendered = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "agrosat_" in rendered
    assert "field_name" not in rendered
    assert "email" not in rendered


def test_json_formatter_redacts_credentials_and_exception_detail():
    formatter = SanitizingJsonFormatter("abc123")
    credential_url = "postgresql://" + "user" + ":" + "value" + "@host/db"
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "Authorization: Bearer-value %s",
        (credential_url,),
        None,
    )
    rendered = formatter.format(record)
    payload = json.loads(rendered)
    assert payload["release_revision"] == "abc123"
    assert "Bearer-value" not in rendered
    assert credential_url not in rendered
    assert "[REDACTED" in rendered

    try:
        raise RuntimeError("private exception detail")
    except RuntimeError:
        import sys

        record.exc_info = sys.exc_info()
    rendered_exception = formatter.format(record)
    assert '"exception_type": "RuntimeError"' in rendered_exception
    assert "private exception detail" not in rendered_exception


def test_json_formatter_allows_only_bounded_request_context():
    formatter = SanitizingJsonFormatter("abc123")
    record = logging.LogRecord(
        "services.metrics",
        logging.INFO,
        __file__,
        1,
        "api_request_complete",
        (),
        None,
    )
    record.agrosat_context = {
        "method": "GET",
        "route": "/api/fields/{field_id}",
        "status_class": "2xx",
        "duration_ms": 12.3456,
        "user_email": "must-not-appear",
        "requested_path": "/api/fields/987654",
    }
    payload = json.loads(formatter.format(record))
    assert payload["context"] == {
        "duration_ms": 12.346,
        "method": "GET",
        "route": "/api/fields/{field_id}",
        "status_class": "2xx",
    }
    assert "must-not-appear" not in json.dumps(payload)
    assert "987654" not in json.dumps(payload)
