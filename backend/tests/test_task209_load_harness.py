"""Bounded load harness contract and deterministic statistics."""

import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "load" / "task209_load.py"
SPEC = importlib.util.spec_from_file_location("task209_load", SCRIPT)
load = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(load)


def scenarios():
    return [
        {
            "name": "live",
            "method": "GET",
            "path": "/health/live",
            "expected_statuses": [200],
            "enabled": True,
            "write": False,
            "json": None,
            "blocked_reason": "",
        }
    ]


def test_base_url_is_loopback_and_credential_free():
    assert load.validate_base_url("http://127.0.0.1:45212") == (
        "http://127.0.0.1:45212"
    )
    for value in (
        "https://example.com",
        "http://" + "user" + ":" + "value" + "@127.0.0.1",
        "file:///tmp/data",
    ):
        with pytest.raises(load.LoadContractError):
            load.validate_base_url(value)


def test_scenario_manifest_validates_full_required_inventory():
    full = load.load_scenarios(
        ROOT / "ops" / "load" / "full_scenarios.example.json"
    )
    assert len(full) == 11
    assert all(not item["enabled"] for item in full)
    assert {item["name"] for item in full} >= {
        "fields_list",
        "dashboard",
        "attention",
        "inspection_list",
        "field_analytics",
        "reports",
        "vector_tile",
        "raster_metadata",
        "pixel_anomaly",
        "inspection_create_idempotent",
        "closure_workflow",
    }


def test_write_scenario_requires_explicit_authorization():
    write = [{**scenarios()[0], "method": "POST", "write": True}]
    with pytest.raises(load.LoadContractError):
        asyncio.run(
            load.execute_load(
                base_url="http://127.0.0.1",
                scenarios=write,
                concurrency=1,
                request_count=1,
                timeout_seconds=1,
                allow_writes=False,
                token=None,
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200)
                ),
            )
        )


def test_write_scenario_reuses_one_idempotency_key_per_run():
    keys = []

    def handler(request):
        keys.append(request.headers["Idempotency-Key"])
        return httpx.Response(200)

    write = [{**scenarios()[0], "method": "POST", "write": True}]
    report = asyncio.run(
        load.execute_load(
            base_url="http://127.0.0.1",
            scenarios=write,
            concurrency=2,
            request_count=4,
            timeout_seconds=1,
            allow_writes=True,
            token=None,
            transport=httpx.MockTransport(handler),
        )
    )
    assert len(set(keys)) == 1
    assert report["run_id"] in keys[0]


def test_deterministic_mock_load_reports_percentiles_without_bodies():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"private_business_data": "must not enter report"},
        )
    )
    report = asyncio.run(
        load.execute_load(
            base_url="http://127.0.0.1",
            scenarios=scenarios(),
            concurrency=2,
            request_count=8,
            timeout_seconds=1,
            allow_writes=False,
            token=None,
            transport=transport,
        )
    )
    assert report["requests_completed"] == 8
    assert report["error_rate"] == 0
    assert report["p50_ms"] is not None
    assert report["p95_ms"] is not None
    assert report["p99_ms"] is not None
    assert "private_business_data" not in json.dumps(report)
    assert report["authorization_configured"] is False
    assert report["client_resource_metrics"]["cpu_seconds"] >= 0
    assert report["client_resource_metrics"]["python_peak_memory_bytes"] >= 0


def test_cli_validation_is_no_request_and_atomic():
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "validation.json"
        code = load.main(
            [
                "--base-url",
                "http://127.0.0.1:45212",
                "--scenarios",
                str((ROOT / "ops" / "load" / "readiness_scenarios.json").resolve()),
                "--output",
                str(output.resolve()),
                "--validate-only",
            ]
        )
        report = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert report == {
        "enabled_count": 2,
        "scenario_count": 2,
        "schema_version": 1,
        "status": "validated",
        "write_count": 0,
    }
