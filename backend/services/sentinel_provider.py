"""Immutable allowlisted Sentinel Hub provider presets.

Only the named presets in this module can supply Sentinel Hub endpoints. No
runtime URL override is accepted, which keeps provider selection fail-closed
and prevents a mixed-provider or arbitrary-endpoint request chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping


PLANET_PROVIDER: Final = "planet"
CDSE_PROVIDER: Final = "cdse"


@dataclass(frozen=True, slots=True)
class SentinelProviderEndpoints:
    name: str
    token_url: str
    statistical_url: str
    process_url: str
    catalog_url: str
    endpoint_class: str

    def sanitized_metadata(self) -> dict[str, str]:
        return {
            "provider": self.name,
            "endpointClass": self.endpoint_class,
        }


_PROVIDER_ENDPOINTS: Final[Mapping[str, SentinelProviderEndpoints]] = MappingProxyType(
    {
        PLANET_PROVIDER: SentinelProviderEndpoints(
            name=PLANET_PROVIDER,
            token_url=(
                "https://services.sentinel-hub.com/auth/realms/main/"
                "protocol/openid-connect/token"
            ),
            statistical_url="https://services.sentinel-hub.com/api/v1/statistics",
            process_url="https://services.sentinel-hub.com/api/v1/process",
            catalog_url="https://services.sentinel-hub.com/api/v1/catalog/1.0.0/search",
            endpoint_class="official_planet_sentinel_hub_https",
        ),
        CDSE_PROVIDER: SentinelProviderEndpoints(
            name=CDSE_PROVIDER,
            token_url=(
                "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
                "protocol/openid-connect/token"
            ),
            statistical_url="https://sh.dataspace.copernicus.eu/statistics/v1",
            process_url="https://sh.dataspace.copernicus.eu/process/v1",
            catalog_url="https://sh.dataspace.copernicus.eu/catalog/v1/search",
            endpoint_class="official_cdse_sentinel_hub_https",
        ),
    }
)

ALLOWED_SENTINEL_PROVIDERS: Final = frozenset(_PROVIDER_ENDPOINTS)


def normalize_sentinel_provider(value: object) -> str:
    """Normalize and validate a provider name without a silent fallback."""
    if not isinstance(value, str):
        raise ValueError("SENTINEL_HUB_PROVIDER must be one of: planet, cdse")
    normalized = value.strip().casefold()
    if normalized not in ALLOWED_SENTINEL_PROVIDERS:
        raise ValueError("SENTINEL_HUB_PROVIDER must be one of: planet, cdse")
    return normalized


def resolve_sentinel_provider(value: object) -> SentinelProviderEndpoints:
    """Resolve a provider name exclusively through the immutable allowlist."""
    return _PROVIDER_ENDPOINTS[normalize_sentinel_provider(value)]


def provider_endpoint_matrix() -> dict[str, dict[str, str]]:
    """Return a sanitized copy suitable for tests and qualification evidence."""
    return {
        name: {
            "token": endpoints.token_url,
            "statistical": endpoints.statistical_url,
            "process": endpoints.process_url,
            "catalog": endpoints.catalog_url,
            "endpointClass": endpoints.endpoint_class,
        }
        for name, endpoints in _PROVIDER_ENDPOINTS.items()
    }
