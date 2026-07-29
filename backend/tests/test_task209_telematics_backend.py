from datetime import datetime, timedelta, timezone

import pytest

from services.telematics import (
    FieldTelematicsMapping,
    TenantRequestLimiter,
    TelematicsProviderError,
    read_field_telematics,
    telematics_cache_key,
    validate_request_bounds,
)


NOW = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
START = NOW - timedelta(hours=24)


class FixtureProvider:
    name = "fixture"

    def __init__(self, values=None, error=None):
        self.values = values or []
        self.error = error
        self.calls = []

    def read(self, mapping, **kwargs):
        self.calls.append((mapping, kwargs))
        if self.error:
            raise self.error
        return self.values


MAPPING = FieldTelematicsMapping(
    enterprise_id=5,
    field_id=11,
    unit_ids=("unit-1",),
    geofence_ids=("zone-1",),
    allowed_sensors=("fuel",),
    provenance="fixture_mapping",
)


def test_cache_key_is_tenant_and_field_safe():
    first = telematics_cache_key(5, 11, START, NOW)
    assert "enterprise:5" in first
    assert "field:11" in first
    assert first != telematics_cache_key(6, 11, START, NOW)
    assert first != telematics_cache_key(5, 12, START, NOW)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"started_at": NOW, "ended_at": START, "limit": 1, "page_limit": 1, "timeout_seconds": 1},
        {"started_at": START, "ended_at": NOW, "limit": 101, "page_limit": 1, "timeout_seconds": 1},
        {"started_at": START, "ended_at": NOW, "limit": 1, "page_limit": 21, "timeout_seconds": 1},
        {"started_at": START, "ended_at": NOW, "limit": 1, "page_limit": 1, "timeout_seconds": 31},
    ],
)
def test_request_bounds_are_enforced(kwargs):
    with pytest.raises(ValueError):
        validate_request_bounds(**kwargs)


def test_missing_mapping_fails_closed_without_provider_call():
    provider = FixtureProvider()
    result = read_field_telematics(provider, None, started_at=START, ended_at=NOW)
    assert result == {
        "status": "unsupported",
        "provider": "fixture",
        "reason": "mapping_unavailable",
        "units": [],
    }
    assert provider.calls == []


def test_fixture_response_is_allowlisted_and_staleness_is_explicit():
    provider = FixtureProvider([{
        "unit_id": "unit-1",
        "label": "Tractor 1",
        "observed_at": (NOW - timedelta(minutes=5)).isoformat(),
        "latitude": 39.77,
        "longitude": 64.42,
        "movement": True,
        "ignition": True,
        "field_intersection": True,
        "geofence_intersection": True,
        "sensors": {
            "fuel": {"value": 71.5, "unit": "%"},
            "private_sensor": {"value": 999, "unit": "secret"},
        },
        "token": "must-not-leak",
    }])
    result = read_field_telematics(
        provider, MAPPING, started_at=START, ended_at=NOW, now=NOW
    )
    assert result["status"] == "available"
    assert len(result["units"]) == 1
    unit = result["units"][0]
    assert unit["sensors"] == [{"code": "fuel", "value": 71.5, "unit": "%"}]
    assert "token" not in str(result).lower()
    assert unit["stale"] is False


def test_foreign_or_invalid_units_are_discarded():
    provider = FixtureProvider([
        {"unit_id": "other", "observed_at": NOW.isoformat(), "latitude": 0, "longitude": 0},
        {"unit_id": "unit-1", "observed_at": "invalid", "latitude": 0, "longitude": 0},
    ])
    result = read_field_telematics(
        provider, MAPPING, started_at=START, ended_at=NOW, now=NOW
    )
    assert result["units"] == []


def test_provider_errors_are_sanitized():
    provider = FixtureProvider(
        error=TelematicsProviderError("authentication", retryable=False)
    )
    result = read_field_telematics(
        provider, MAPPING, started_at=START, ended_at=NOW, now=NOW
    )
    assert result["status"] == "unavailable"
    assert result["reason"] == "authentication"
    assert result["retryable"] is False


def test_rate_limiter_is_partitioned_by_tenant_and_bounded():
    current = [100.0]
    limiter = TenantRequestLimiter(2, 10, clock=lambda: current[0])
    assert limiter.allow(5) is True
    assert limiter.allow(5) is True
    assert limiter.allow(5) is False
    assert limiter.allow(6) is True
    current[0] = 111.0
    assert limiter.allow(5) is True
