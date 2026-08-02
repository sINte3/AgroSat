"""Focused offline tests for the allowlisted Sentinel Hub provider presets."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path
import sys
from unittest.mock import patch

import httpx
from pydantic import ValidationError
import pytest


BACKEND = Path(__file__).resolve().parents[1]
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
}
CDSE = {
    "token": (
        "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
        "protocol/openid-connect/token"
    ),
    "statistical": "https://sh.dataspace.copernicus.eu/statistics/v1",
    "process": "https://sh.dataspace.copernicus.eu/process/v1",
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
                "to": "2026-07-25T23:59:59Z",
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


def test_cdse_preset_resolves_exact_official_endpoints():
    endpoints = resolve_sentinel_provider("CDSE")
    assert endpoints.name == "cdse"
    assert endpoints.token_url == CDSE["token"]
    assert endpoints.statistical_url == CDSE["statistical"]
    assert endpoints.process_url == CDSE["process"]
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

    def fake_post(url: str, **_kwargs):
        calls.append(url)
        if url == expected["token"]:
            return _response(url, json_value={"access_token": "fixture-token", "expires_in": 3600})
        if url == expected["statistical"]:
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
    assert calls.count(expected["token"]) == 1
    assert calls.count(expected["statistical"]) == 1
    assert calls.count(expected["process"]) == 1
    other = CDSE if provider == "planet" else PLANET
    assert not set(calls).intersection(other.values())


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
