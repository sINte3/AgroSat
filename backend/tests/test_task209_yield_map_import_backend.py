"""Yield import normalization, authorization, query, and idempotency tests."""

import csv
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from api import yield_map_imports as api
from schemas.yield_map_import import (
    YieldImportAcceptRequest,
    YieldImportPreviewRequest,
)
from services import yield_map_imports as service


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "task209_yield_point_csv_v1.csv"
)
HASH = "a" * 64


def user(role="manager", identifier=7, enterprise=5):
    return SimpleNamespace(
        role=role,
        id=identifier,
        enterprise_id=enterprise,
    )


def field(identifier=3, enterprise=5):
    return SimpleNamespace(
        id=identifier,
        enterprise_id=enterprise,
        name="Fixture Field",
    )


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class Session:
    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        if not self.outcomes:
            raise AssertionError("unexpected SQL statement")
        return Result(self.outcomes.pop(0))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def rows_from_fixture():
    with FIXTURE.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        {
            "longitude": float(row["longitude"]),
            "latitude": float(row["latitude"]),
            "yield_value": float(row["yield_value"]),
            "observed_at": row["observed_at"],
            "machine_point_id": row["machine_point_id"],
            "speed_kph": float(row["speed_kph"]),
            "moisture_pct": float(row["moisture_pct"]),
        }
        for row in rows
    ]


def request(**changes):
    value = {
        "field_id": 3,
        "season_year": 2026,
        "crop_code": "cotton",
        "schema_code": "yield_point_csv_v1",
        "source_filename": "fixture-yield.csv",
        "source_sha256": HASH,
        "source_provider": "deterministic_fixture",
        "machine_id": "fixture-combine",
        "machine_model": "fixture-model",
        "yield_unit": "t_ha",
        "rows": rows_from_fixture(),
    }
    value.update(changes)
    return YieldImportPreviewRequest(**value)


def intersection_rows(payload, outside=()):
    outside = set(outside)
    return [
        {
            "source_row": index,
            "inside_field": index not in outside,
        }
        for index in range(1, len(payload.rows) + 1)
    ]


def import_row(**changes):
    value = {
        "id": 91,
        "enterprise_id": 5,
        "field_id": 3,
        "field_name": "Fixture Field",
        "season_year": 2026,
        "crop_code": "cotton",
        "schema_code": "yield_point_csv_v1",
        "source_filename": "fixture-yield.csv",
        "source_sha256": HASH,
        "source_provider": "deterministic_fixture",
        "machine_id": "fixture-combine",
        "machine_model": "fixture-model",
        "input_unit": "t_ha",
        "normalized_unit": "t_ha",
        "total_rows": 8,
        "accepted_rows": 8,
        "rejected_rows": 0,
        "yield_min_t_ha": 4.1,
        "yield_max_t_ha": 4.35,
        "yield_mean_t_ha": 4.23625,
        "bounds": {"min_longitude": 64.4201},
        "provenance": {"schema_code": "yield_point_csv_v1"},
        "status": "accepted",
        "created_by_id": 7,
        "created_by_name": "Fixture User",
        "created_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
    }
    value.update(changes)
    return value


def test_fixture_normalizes_reproducibly_and_preserves_provenance():
    payload = request()
    first = service.normalize_rows(payload)
    second = service.normalize_rows(payload)
    assert first == second
    assert first["total_rows"] == 8
    assert first["accepted_rows"] == 8
    assert first["rejected_rows"] == 0
    assert first["normalized_unit"] == "t_ha"
    assert first["summary"]["yield_mean_t_ha"] == 4.23625
    assert first["preview_fingerprint"] == second["preview_fingerprint"]
    assert len(first["preview_fingerprint"]) == 64


def test_kg_ha_conversion_outlier_duplicate_and_invalid_rows_are_explicit():
    fixture = rows_from_fixture()
    for row in fixture:
        row["yield_value"] *= 1000
    fixture[-1]["yield_value"] = 50_000
    fixture[1]["machine_point_id"] = fixture[0]["machine_point_id"]
    fixture[2]["observed_at"] = "2026-07-20T09:00:20"
    fixture.extend([
        {
            **fixture[0],
            "longitude": 64.4209,
            "latitude": 39.7709,
            "yield_value": 4210,
            "machine_point_id": "p-009",
        },
        {
            **fixture[0],
            "longitude": 64.4210,
            "latitude": 39.7710,
            "yield_value": 4270,
            "machine_point_id": "p-010",
        },
    ])
    payload = request(yield_unit="kg_ha", rows=fixture)
    result = service.normalize_rows(payload)
    reasons = {item["source_row"]: item["reason_code"] for item in result["rejected"]}
    assert reasons[2] == "duplicate_machine_point_id"
    assert reasons[3] == "invalid_observed_at"
    assert reasons[8] == "yield_statistical_outlier"
    assert result["accepted"][0]["yield_t_ha"] == 4.2


def test_schema_rejects_unsafe_metadata_and_unsupported_unit():
    with pytest.raises(ValidationError):
        request(source_filename="../yield.csv")
    with pytest.raises(ValidationError):
        request(source_sha256="not-a-hash")
    with pytest.raises(ValidationError):
        request(yield_unit="bu_acre")


def test_field_intersection_uses_one_tenant_scoped_postgis_statement():
    payload = request()
    session = Session([intersection_rows(payload, outside={8})])
    result = service.preview_import(session, user(), field(), payload)
    assert len(session.calls) == 1
    sql, params = session.calls[0]
    assert "jsonb_to_recordset" in sql
    assert "ST_Covers" in sql
    assert "enterprise_id=:enterprise_id" in sql
    assert params["field_id"] == 3
    assert params["enterprise_id"] == 5
    assert result["accepted_rows"] == 7
    assert result["rejected_rows"] == 1
    assert result["rejected"][0]["reason_code"] == "outside_field"


def test_viewer_and_cross_tenant_preview_are_denied_before_queries():
    payload = request()
    with pytest.raises(HTTPException) as error:
        service.preview_import(Session(), user("viewer"), field(), payload)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        service.preview_import(
            Session(),
            user("manager", enterprise=6),
            field(),
            payload,
        )
    assert error.value.status_code == 404


def test_accept_recomputes_preview_and_persists_points_in_one_transaction():
    preview_payload = request()
    preview_session = Session([intersection_rows(preview_payload)])
    preview = service.preview_import(
        preview_session,
        user(),
        field(),
        preview_payload,
    )
    payload = YieldImportAcceptRequest(
        **preview_payload.model_dump(),
        preview_fingerprint=preview["preview_fingerprint"],
        confirm=True,
    )
    session = Session([
        intersection_rows(payload),
        [],
        [],
        [{"id": 91}],
        [],
        [import_row()],
    ])
    created, item = service.accept_import(
        session,
        user(),
        field(),
        payload,
        "task209-yield-import",
    )
    assert created is True
    assert item["id"] == 91
    assert session.commits == 1
    assert session.rollbacks == 0
    assert len(session.calls) == 6
    assert "ST_Covers" in session.calls[0][0]
    assert "INSERT INTO yield_map_imports" in session.calls[3][0]
    assert "INSERT INTO yield_map_points" in session.calls[4][0]
    assert "jsonb_to_recordset" in session.calls[4][0]
    assert "i.enterprise_id=:enterprise_id" in session.calls[5][0]


def test_accept_rejects_stale_preview_and_rows_with_rejections():
    preview_payload = request()
    payload = YieldImportAcceptRequest(
        **preview_payload.model_dump(),
        preview_fingerprint="b" * 64,
        confirm=True,
    )
    session = Session([intersection_rows(payload)])
    with pytest.raises(HTTPException) as error:
        service.accept_import(
            session,
            user(),
            field(),
            payload,
            "task209-yield-stale",
        )
    assert error.value.status_code == 409
    assert session.rollbacks == 0

    invalid = request(rows=[
        {
            **rows_from_fixture()[0],
            "longitude": 180.1,
        }
    ])
    normalized = service.normalize_rows(invalid)
    invalid_payload = YieldImportAcceptRequest(
        **invalid.model_dump(),
        preview_fingerprint=normalized["preview_fingerprint"],
        confirm=True,
    )
    with pytest.raises(HTTPException) as error:
        service.accept_import(
            Session(),
            user(),
            field(),
            invalid_payload,
            "task209-yield-rejected",
        )
    assert error.value.status_code == 422


def test_idempotent_repeat_returns_existing_without_insert():
    preview_payload = request()
    preview = service.normalize_rows(preview_payload)
    payload = YieldImportAcceptRequest(
        **preview_payload.model_dump(),
        preview_fingerprint=preview["preview_fingerprint"],
        confirm=True,
    )
    fingerprint = service.request_fingerprint(7, payload)
    session = Session([
        intersection_rows(payload),
        [{"id": 91, "request_fingerprint": fingerprint}],
        [import_row()],
    ])
    created, item = service.accept_import(
        session,
        user(),
        field(),
        payload,
        "task209-yield-repeat",
    )
    assert created is False
    assert item["id"] == 91
    assert session.commits == 0
    assert len(session.calls) == 3


def test_list_and_points_are_bounded_tenant_queries_without_n_plus_one():
    list_session = Session([[import_row()]])
    result = service.list_imports(
        list_session,
        user(),
        field(),
        limit=50,
        offset=0,
    )
    assert len(list_session.calls) == 1
    assert result["items"][0]["id"] == 91
    assert list_session.calls[0][1]["limit"] == 50
    assert "i.enterprise_id=:enterprise_id" in list_session.calls[0][0]

    points = [{
        "id": 1,
        "source_row": 1,
        "machine_point_id": "p-001",
        "observed_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
        "longitude": 64.42,
        "latitude": 39.77,
        "yield_t_ha": 4.2,
        "speed_kph": 6.5,
        "moisture_pct": 11.2,
        "quality_flags": [],
    }]
    point_session = Session([[import_row()], points])
    response = service.list_points(
        point_session,
        user(),
        91,
        limit=500,
        offset=0,
    )
    assert len(point_session.calls) == 2
    assert response["items"][0]["source_row"] == 1
    assert point_session.calls[1][1]["limit"] == 500
    assert "p.enterprise_id=:enterprise_id" in point_session.calls[1][0]


def test_all_yield_import_routes_require_authentication():
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    assert client.post("/api/yield-map-imports/preview", json={}).status_code == 401
    assert client.post(
        "/api/yield-map-imports",
        headers={"Idempotency-Key": "task209-yield"},
        json={},
    ).status_code == 401
    assert client.get("/api/yield-map-imports?field_id=3").status_code == 401
    assert client.get("/api/yield-map-imports/1").status_code == 401
    assert client.get("/api/yield-map-imports/1/points").status_code == 401


def test_api_accept_status_distinguishes_created_and_idempotent_repeat():
    preview_payload = request()
    preview = service.normalize_rows(preview_payload)
    payload = YieldImportAcceptRequest(
        **preview_payload.model_dump(),
        preview_fingerprint=preview["preview_fingerprint"],
        confirm=True,
    )
    response = Response()
    with (
        patch.object(api, "get_authorized_field_row_for_write", return_value=field()),
        patch.object(service, "accept_import", return_value=(False, import_row())),
    ):
        result = api.accept_yield_map_import(
            payload,
            response,
            "task209-yield-api",
            Session(),
            user(),
        )
    assert response.status_code == 200
    assert result["created"] is False
