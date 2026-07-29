"""Authorization, idempotency, query-shape, and weather provenance tests."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from api import irrigation_context as api
from schemas.field_inspection import CreateInspectionRequest
from schemas.irrigation_context import CreateIrrigationEventRequest
from services import irrigation_context as service
from services.weather import get_field_weather


NOW = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)


def user(role="manager", identifier=7, enterprise=5):
    return SimpleNamespace(
        role=role,
        id=identifier,
        enterprise_id=enterprise,
        full_name="Fixture User",
    )


def field(identifier=3, enterprise=5):
    return SimpleNamespace(
        id=identifier,
        enterprise_id=enterprise,
        name="Fixture Field",
        irrigation_type="drip",
        centroid_lat=39.77,
        centroid_lon=64.42,
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
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return Result(outcome)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def payload(**changes):
    value = {
        "event_type": "irrigation_applied",
        "occurred_at": NOW,
        "method_code": "drip",
        "water_amount_mm": Decimal("18.50"),
        "note": "Observed bounded irrigation application.",
    }
    value.update(changes)
    return CreateIrrigationEventRequest(**value)


def event_row(**changes):
    value = {
        "id": 41,
        "enterprise_id": 5,
        "field_id": 3,
        "inspection_id": None,
        "recorded_by_id": 7,
        "recorded_by_name": "Fixture User",
        "event_type": "irrigation_applied",
        "occurred_at": NOW,
        "method_code": "drip",
        "water_amount_mm": Decimal("18.50"),
        "evidence_source": "human_reported",
        "note": "Observed bounded irrigation application.",
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    value.update(changes)
    return value


def test_schema_requires_timezone_and_event_specific_amount():
    with pytest.raises(ValidationError):
        payload(occurred_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        payload(event_type="equipment_issue")
    with pytest.raises(ValidationError):
        payload(water_amount_mm=Decimal("1000.01"))
    assert payload(note="  Field evidence.  ").note == "Field evidence."


def test_irrigation_context_inspection_contract_is_structured():
    request = CreateInspectionRequest(
        field_id=3,
        assigned_to_id=7,
        source="irrigation_context",
        source_priority="high",
        source_attention_score=None,
        source_observation_date=date(2026, 7, 29),
        source_reason_codes=["water_stress_suspicion"],
        title="Verify possible water stress in the field",
    )
    assert request.source.value == "irrigation_context"
    with pytest.raises(ValidationError):
        CreateInspectionRequest(
            field_id=3,
            source="irrigation_context",
            source_priority="high",
            source_attention_score=50,
            source_observation_date=date(2026, 7, 29),
            source_reason_codes=["confirmed_irrigation_failure"],
            title="Invalid diagnosis",
        )


def test_cache_key_and_pattern_are_tenant_and_field_safe():
    key = service.irrigation_context_cache_key(5, 3, 20)
    assert "enterprise:5" in key
    assert "field:3" in key
    assert key != service.irrigation_context_cache_key(6, 3, 20)
    assert key != service.irrigation_context_cache_key(5, 4, 20)
    assert service.irrigation_context_cache_pattern(5, 3).endswith("*")


def test_event_create_is_idempotent_and_commits_before_reload():
    request = payload()
    session = Session([
        [],
        [{"id": 41}],
        [event_row()],
    ])
    created, item = service.create_event(
        session,
        user(),
        field(),
        request,
        "task209-irrigation-create",
        now=NOW,
    )
    assert created is True
    assert item["id"] == 41
    assert item["water_amount_mm"] == Decimal("18.50")
    assert session.commits == 1
    assert session.rollbacks == 0
    assert len(session.calls) == 3
    assert "enterprise_id=:enterprise_id" in session.calls[0][0]
    assert "INSERT INTO irrigation_events" in session.calls[1][0]
    assert "ev.enterprise_id=:enterprise_id" in session.calls[2][0]

    fingerprint = service.event_fingerprint(7, 3, request)
    repeated = Session([
        [{"id": 41, "request_fingerprint": fingerprint}],
        [event_row()],
    ])
    created, item = service.create_event(
        repeated,
        user(),
        field(),
        request,
        "task209-irrigation-create",
        now=NOW,
    )
    assert created is False
    assert item["id"] == 41
    assert repeated.commits == 0


def test_idempotency_conflict_and_foreign_inspection_are_rejected():
    conflict = Session([
        [{"id": 41, "request_fingerprint": "0" * 64}],
    ])
    with pytest.raises(HTTPException) as error:
        service.create_event(
            conflict,
            user(),
            field(),
            payload(),
            "task209-irrigation-conflict",
            now=NOW,
        )
    assert error.value.status_code == 409
    assert conflict.rollbacks == 1

    linked = Session([[], []])
    with pytest.raises(HTTPException) as error:
        service.create_event(
            linked,
            user(),
            field(),
            payload(inspection_id=99),
            "task209-irrigation-link",
            now=NOW,
        )
    assert error.value.status_code == 404
    assert "field_id=:field_id" in linked.calls[1][0]
    assert "enterprise_id=:enterprise_id" in linked.calls[1][0]
    assert linked.rollbacks == 1


def test_viewer_cross_tenant_and_bad_time_fail_before_writes():
    with pytest.raises(HTTPException) as error:
        service.create_event(
            Session(),
            user("viewer"),
            field(),
            payload(),
            "task209-viewer",
            now=NOW,
        )
    assert error.value.status_code == 403

    with pytest.raises(HTTPException) as error:
        service.create_event(
            Session(),
            user("manager", enterprise=6),
            field(enterprise=5),
            payload(),
            "task209-foreign",
            now=NOW,
        )
    assert error.value.status_code == 404

    with pytest.raises(HTTPException) as error:
        service.create_event(
            Session(),
            user(),
            field(),
            payload(occurred_at=NOW + timedelta(minutes=6)),
            "task209-future",
            now=NOW,
        )
    assert error.value.status_code == 422


def test_field_context_uses_two_constant_queries_and_explicit_provenance():
    session = Session([
        [event_row()],
        [{
            "id": 51,
            "status": "pending",
            "source": "irrigation_context",
            "source_priority": "high",
            "source_observation_date": date(2026, 7, 29),
            "source_reason_codes": ["water_stress_suspicion"],
        }],
    ])
    weather = {
        "status": "available",
        "provider": "fixture_weather",
        "provenance": {
            "provider": "fixture_weather",
            "fetched_at": NOW.isoformat(),
            "provider_observed_at": NOW.isoformat(),
            "timezone": "Asia/Tashkent",
        },
        "current": {"temperature": 35.0},
        "forecast": [],
        "risks": [],
    }
    result = service.field_context(
        session,
        user(),
        field(),
        limit=20,
        weather_loader=lambda *_: weather,
    )
    assert len(session.calls) == 2
    assert session.calls[0][1]["limit"] == 20
    assert "ORDER BY ev.occurred_at DESC" in session.calls[0][0]
    assert result["weather"]["status"] == "available"
    assert result["weather"]["provenance"]["timezone"] == "Asia/Tashkent"
    assert result["active_inspection"]["id"] == 51
    assert "do not confirm" in result["causality_limitation"]
    assert "water_stress_suspicion" in result["supported_reason_codes"]


def test_weather_context_has_explicit_unavailable_states():
    missing = field()
    missing.centroid_lat = None
    assert service.weather_context(missing, lambda *_: {}) == {
        "status": "unavailable",
        "provider": "open_meteo",
        "reason": "missing_coordinates",
    }
    assert service.weather_context(field(), lambda *_: None) == {
        "status": "unavailable",
        "provider": "open_meteo",
        "reason": "provider_unavailable",
    }


def test_open_meteo_response_adds_bounded_provenance_without_diagnosis():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "timezone": "Asia/Tashkent",
        "latitude": 39.77,
        "longitude": 64.42,
        "current": {
            "time": "2026-07-29T15:00",
            "temperature_2m": 35.0,
            "relative_humidity_2m": 28,
            "apparent_temperature": 36.0,
            "wind_speed_10m": 12.0,
            "wind_direction_10m": 180,
            "precipitation": 0.0,
            "weather_code": 0,
            "is_day": 1,
        },
        "daily": {
            "time": ["2026-07-29"],
            "temperature_2m_max": [39.0],
            "temperature_2m_min": [24.0],
            "precipitation_sum": [0.0],
            "et0_fao_evapotranspiration": [7.0],
            "wind_speed_10m_max": [16.0],
            "weather_code": [0],
            "sunrise": ["2026-07-29T05:30"],
            "sunset": ["2026-07-29T20:00"],
        },
    }
    with patch("services.weather.httpx.get", return_value=response) as request:
        result = get_field_weather(39.77, 64.42)
    assert result["status"] == "available"
    assert result["provider"] == "open_meteo"
    assert result["provenance"]["provider_observed_at"] == "2026-07-29T15:00"
    assert result["provenance"]["timezone"] == "Asia/Tashkent"
    assert "irrigation failure" not in str(result).lower()
    assert request.call_args.kwargs["timeout"] == 10.0
    assert request.call_args.kwargs["params"]["forecast_days"] == 7


def test_api_invalidates_only_after_created_event():
    request = payload()
    response = Response()
    with (
        patch.object(api, "get_authorized_field_row_for_write", return_value=field()),
        patch.object(api.service, "create_event", return_value=(True, event_row())),
        patch.object(api, "cache_delete_pattern") as invalidate,
    ):
        result = api.create_irrigation_event(
            request,
            response,
            3,
            "task209-api-event",
            Session(),
            user(),
        )
    assert result["created"] is True
    assert response.status_code == 201
    invalidate.assert_called_once_with(
        "irrigation-context:v1:enterprise:5:field:3:*"
    )

    duplicate_response = Response()
    with (
        patch.object(api, "get_authorized_field_row_for_write", return_value=field()),
        patch.object(api.service, "create_event", return_value=(False, event_row())),
        patch.object(api, "cache_delete_pattern") as duplicate_invalidate,
    ):
        result = api.create_irrigation_event(
            request,
            duplicate_response,
            3,
            "task209-api-event",
            Session(),
            user(),
        )
    assert result["created"] is False
    assert duplicate_response.status_code == 200
    duplicate_invalidate.assert_not_called()


def test_both_irrigation_context_routes_require_authentication():
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    assert client.get("/api/irrigation-context/fields/3").status_code == 401
    assert client.post(
        "/api/irrigation-context/fields/3/events",
        headers={"Idempotency-Key": "task209-auth"},
        json={},
    ).status_code == 401
