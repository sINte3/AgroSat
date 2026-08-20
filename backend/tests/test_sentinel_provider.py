"""Focused offline tests for the allowlisted Sentinel Hub provider presets."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch

import httpx
from pydantic import ValidationError
import pytest


BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.sentinel_provider import (  # noqa: E402
    provider_endpoint_matrix,
    resolve_sentinel_provider,
)


PLANET = {
    "token": (
        "https://services.sentinel-hub.com/auth/realms/main/"
        "protocol/openid-connect/token"
    ),
    "statistical": "https://services.sentinel-hub.com/api/v1/statistics",
    "process": "https://services.sentinel-hub.com/api/v1/process",
    "catalog": "https://services.sentinel-hub.com/api/v1/catalog/1.0.0/search",
}
CDSE = {
    "token": (
        "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
        "protocol/openid-connect/token"
    ),
    "statistical": "https://sh.dataspace.copernicus.eu/statistics/v1",
    "process": "https://sh.dataspace.copernicus.eu/process/v1",
    "catalog": "https://sh.dataspace.copernicus.eu/catalog/v1/search",
}


def _response(url: str, *, json_value=None, content: bytes = b"") -> httpx.Response:
    request = httpx.Request("POST", url)
    if json_value is not None:
        return httpx.Response(200, request=request, json=json_value)
    return httpx.Response(
        200,
        request=request,
        content=content,
        headers={"content-type": "image/png"},
    )


def _stats_body() -> dict:
    return {
        "data": [{
            "interval": {
                "from": "2026-07-25T00:00:00Z",
                "to": "2026-07-26T00:00:00Z",
            },
            "outputs": {
                "ndvi": {
                    "bands": {
                        "B0": {
                            "stats": {
                                "sampleCount": 12,
                                "noDataCount": 1,
                                "mean": 0.5,
                                "min": 0.2,
                                "max": 0.8,
                                "stDev": 0.1,
                                "percentiles": {"10.0": 0.3, "90.0": 0.7},
                            }
                        }
                    }
                }
            },
        }]
    }


def test_planet_preset_resolves_exact_existing_endpoints():
    endpoints = resolve_sentinel_provider(" planet ")
    assert endpoints.name == "planet"
    assert endpoints.token_url == PLANET["token"]
    assert endpoints.statistical_url == PLANET["statistical"]
    assert endpoints.process_url == PLANET["process"]
    assert endpoints.catalog_url == PLANET["catalog"]


def test_cdse_preset_resolves_exact_official_endpoints():
    endpoints = resolve_sentinel_provider("CDSE")
    assert endpoints.name == "cdse"
    assert endpoints.token_url == CDSE["token"]
    assert endpoints.statistical_url == CDSE["statistical"]
    assert endpoints.process_url == CDSE["process"]
    assert endpoints.catalog_url == CDSE["catalog"]
    assert set(provider_endpoint_matrix()) == {"planet", "cdse"}


def test_provider_endpoints_are_frozen_and_unknown_values_fail_fast(monkeypatch):
    from config import Settings

    monkeypatch.delenv("SENTINEL_HUB_PROVIDER", raising=False)
    assert Settings(_env_file=None).sentinel_hub_provider == "planet"
    assert Settings(sentinel_hub_provider=" CDSE ", _env_file=None).sentinel_hub_provider == "cdse"
    with pytest.raises(ValidationError):
        Settings(sentinel_hub_provider="https://example.invalid", _env_file=None)
    with pytest.raises(ValueError):
        resolve_sentinel_provider("unknown")
    endpoints = resolve_sentinel_provider("cdse")
    with pytest.raises(FrozenInstanceError):
        endpoints.process_url = "https://example.invalid"  # type: ignore[misc]


@pytest.mark.parametrize(("provider", "expected"), [("planet", PLANET), ("cdse", CDSE)])
def test_token_statistics_and_process_share_one_provider_and_cached_token(
    provider,
    expected,
):
    from services.satellite import SentinelHubService

    calls: list[str] = []
    statistical_payloads: list[dict] = []

    def fake_post(url: str, **kwargs):
        calls.append(url)
        if url == expected["token"]:
            return _response(url, json_value={"access_token": "fixture-token", "expires_in": 3600})
        if url == expected["statistical"]:
            statistical_payloads.append(kwargs["json"])
            return _response(url, json_value=_stats_body())
        if url == expected["process"]:
            return _response(url, content=b"\x89PNG\r\n\x1a\nfixture")
        raise AssertionError("unexpected endpoint")

    service = SentinelHubService("fixture-id", "fixture-secret", provider=provider)
    with patch("services.satellite.httpx.post", side_effect=fake_post):
        assert service._get_access_token() == "fixture-token"
        assert service._get_access_token() == "fixture-token"
        process_response = service.get_process_png({"bounded": True})
        stats = service.get_ndvi_stats(
            "POLYGON ((64.42 39.76, 64.43 39.76, 64.43 39.77, 64.42 39.76))",
            date(2026, 7, 19),
            date(2026, 8, 1),
            aggregation_interval="P1D",
        )

    assert process_response.status_code == 200
    assert stats and stats["captured_date"] == "2026-07-25"
    assert stats["captured_date_semantics"] == "aggregation_interval_start_utc_date_not_acquisition_date"
    assert stats["interval_from_utc"] == "2026-07-25T00:00:00Z"
    assert stats["interval_to_utc"] == "2026-07-26T00:00:00Z"
    assert stats["acquisition_timestamp_available"] is False
    requested_range = statistical_payloads[0]["aggregation"]["timeRange"]
    assert requested_range == {
        "from": "2026-07-19T00:00:00Z",
        "to": "2026-08-02T00:00:00Z",
    }
    assert calls.count(expected["token"]) == 1
    assert calls.count(expected["statistical"]) == 1
    assert calls.count(expected["process"]) == 1
    other = CDSE if provider == "planet" else PLANET
    assert not set(calls).intersection(other.values())


def _interval(from_value, to_value, *, sample_count=12, no_data_count=1, mean=0.5):
    return {
        "interval": {"from": from_value, "to": to_value},
        "outputs": {
            "ndvi": {
                "bands": {
                    "B0": {
                        "stats": {
                            "sampleCount": sample_count,
                            "noDataCount": no_data_count,
                            "mean": mean,
                            "min": 0.2,
                            "max": 0.8,
                            "stDev": 0.1,
                            "percentiles": {"10.0": 0.3, "90.0": 0.7},
                        }
                    }
                }
            }
        },
    }


def test_daily_half_open_interval_uses_start_date_not_exclusive_end_date():
    from services.satellite import SentinelHubService

    service = SentinelHubService("fixture-id", "fixture-secret", provider="cdse")
    result = service._parse_stats_response({
        "data": [_interval(
            "2026-07-30T00:00:00Z",
            "2026-07-31T00:00:00Z",
        )]
    })
    assert result
    assert result["captured_date"] == "2026-07-30"
    assert result["captured_date"] != "2026-07-31"
    assert result["interval_is_daily"] is True
    assert result["aggregation_interval_semantics"] == "half_open_utc_aggregation_bucket_[from,to)"


def test_unsorted_intervals_are_selected_by_parsed_utc_time_and_valid_pixel_count():
    from services.satellite import SentinelHubService

    service = SentinelHubService("fixture-id", "fixture-secret", provider="cdse")
    result = service._parse_stats_response({
        "data": [
            _interval("2026-07-30T00:00:00Z", "2026-07-31T00:00:00Z", mean=0.7),
            _interval("2026-07-28T00:00:00Z", "2026-07-29T00:00:00Z", mean=0.3),
            _interval(
                "2026-08-01T00:00:00Z",
                "2026-08-02T00:00:00Z",
                sample_count=12,
                no_data_count=12,
                mean=0.9,
            ),
        ]
    })
    assert result
    assert result["captured_date"] == "2026-07-30"
    assert result["mean_ndvi"] == 0.7
    assert result["valid_pixel_count"] == 11
    assert result["valid_pixels_pct"] == pytest.approx(91.7)


@pytest.mark.parametrize(
    ("from_value", "to_value"),
    [
        (None, "2026-07-31T00:00:00Z"),
        ("invalid", "2026-07-31T00:00:00Z"),
        ("2026-07-30T00:00:00", "2026-07-31T00:00:00Z"),
        ("2026-07-30T01:00:00+01:00", "2026-07-31T00:00:00Z"),
        ("2026-07-31T00:00:00Z", "2026-07-30T00:00:00Z"),
    ],
)
def test_invalid_missing_or_non_utc_interval_bounds_fail_safely(from_value, to_value):
    from services.satellite import SentinelHubService

    service = SentinelHubService("fixture-id", "fixture-secret", provider="cdse")
    assert service._parse_stats_response({
        "data": [_interval(from_value, to_value)]
    }) is None


def test_multi_day_interval_is_not_claimed_as_exact_acquisition_date():
    from services.satellite import SentinelHubService

    service = SentinelHubService("fixture-id", "fixture-secret", provider="cdse")
    result = service._parse_stats_response(
        {"data": [_interval(
            "2026-07-20T00:00:00Z",
            "2026-07-25T00:00:00Z",
        )]},
        aggregation_interval="P5D",
    )
    assert result
    assert result["captured_date"] == "2026-07-20"
    assert result["interval_is_daily"] is False
    assert result["acquisition_timestamp_available"] is False
    assert "acquisition" in result["captured_date_semantics"]


def test_utc_parser_returns_aware_utc_and_rejects_naive_or_non_utc_values():
    from services.satellite import parse_provider_utc_timestamp

    parsed = parse_provider_utc_timestamp("2026-07-30T00:00:00Z")
    assert parsed == datetime(2026, 7, 30, tzinfo=timezone.utc)
    for value in ("2026-07-30T00:00:00", "2026-07-30T01:00:00+01:00"):
        with pytest.raises(ValueError):
            parse_provider_utc_timestamp(value)


def test_no_real_ndvi_caller_reintroduces_interval_to_date_slicing():
    files = [
        BACKEND / "services" / "satellite.py",
        ROOT / "ops" / "qualification" / "Run-ProgramR1LiveSentinelQualification.py",
    ]
    forbidden = ('["to"][:10]', "['to'][:10]", "interval.to[:10]")
    for path in files:
        source = path.read_text(encoding="utf-8")
        assert not any(value in source for value in forbidden), path


def test_raster_provider_metadata_uses_selected_preset_without_urls():
    from config import settings
    from services.raster_provider import provider_metadata

    with patch.object(settings, "sentinel_hub_provider", "cdse"):
        metadata = provider_metadata("ndvi")
    assert metadata["sentinel_provider"] == "cdse"
    assert metadata["sentinel_endpoint_class"] == "official_cdse_sentinel_hub_https"
    assert not any("url" in key.casefold() for key in metadata)


def test_real_backend_request_paths_do_not_embed_separate_endpoint_strings():
    files = [
        BACKEND / "services" / "satellite.py",
        BACKEND / "services" / "satellite_collection.py",
        BACKEND / "scripts" / "collect_satellite_indices.py",
        BACKEND / "scripts" / "dry_run_multi_index_single_field.py",
        BACKEND / "scripts" / "preview_multi_index_batch.py",
        BACKEND / "scripts" / "write_multi_index_batch.py",
        BACKEND / "scripts" / "write_multi_index_single_field.py",
    ]
    endpoint_hosts = ("services.sentinel-hub.com", "dataspace.copernicus.eu")
    for path in files:
        source = path.read_text(encoding="utf-8")
        assert not any(host in source for host in endpoint_hosts), path
