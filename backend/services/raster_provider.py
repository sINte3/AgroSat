"""Provider-neutral raster registry backed by real, fail-closed adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from config import settings
from services import ndvi_raster
from services.sentinel_provider import resolve_sentinel_provider


SCHEMA_VERSION = "task209_raster_provider_v1"
PROVIDER_CODE = "sentinel_process"
PROCESSING_VERSION = ndvi_raster.PALETTE_VERSION
SUPPORTED_INDEX_CODES = ("ndvi",)
DEFAULT_SIZE = ndvi_raster.DEFAULT_SIZE
QUALITY_MASK = "Sentinel-2 L2A dataMask and SCL exclusions"
REQUEST_TIMEOUT_CLASS = "bounded_httpx_provider_timeout"

RasterServiceUnavailable = ndvi_raster.RasterServiceUnavailable
RasterUpstreamInvalid = ndvi_raster.RasterUpstreamInvalid
RasterUpstreamTimeout = ndvi_raster.RasterUpstreamTimeout


class UnsupportedRasterIndex(Exception):
    pass


@dataclass(frozen=True)
class RasterRenderRequest:
    field_id: int
    geometry: dict[str, Any]
    index_code: str
    observation_date: date
    size: int


class RasterProvider(Protocol):
    code: str
    supported_indices: tuple[str, ...]

    def cache_identity(self, request: RasterRenderRequest) -> str: ...

    def render(self, request: RasterRenderRequest) -> tuple[bytes, str]: ...

    def metadata(self, index_code: str) -> dict[str, Any]: ...


class SentinelProcessProvider:
    """Real Sentinel Process API adapter; never substitutes mock output."""

    code = PROVIDER_CODE
    supported_indices = SUPPORTED_INDEX_CODES

    def cache_identity(self, request: RasterRenderRequest) -> str:
        self._require_supported(request.index_code)
        return ndvi_raster.raster_cache_key(
            request.field_id,
            request.observation_date,
            request.size,
        )

    def render(self, request: RasterRenderRequest) -> tuple[bytes, str]:
        self._require_supported(request.index_code)
        ndvi_raster.validate_size(request.size)
        return ndvi_raster.get_raster_png(
            request.field_id,
            request.geometry,
            request.observation_date,
            request.size,
        )

    def metadata(self, index_code: str) -> dict[str, Any]:
        self._require_supported(index_code)
        sentinel_endpoints = resolve_sentinel_provider(settings.sentinel_hub_provider)
        return {
            "provider": self.code,
            "sentinel_provider": sentinel_endpoints.name,
            "sentinel_endpoint_class": sentinel_endpoints.endpoint_class,
            "processing_version": PROCESSING_VERSION,
            "quality_mask": QUALITY_MASK,
            "request_timeout_class": REQUEST_TIMEOUT_CLASS,
            "default_size": ndvi_raster.DEFAULT_SIZE,
            "allowed_sizes": list(ndvi_raster.ALLOWED_SIZES),
            "legend": list(ndvi_raster.LEGEND),
            "limitations": list(ndvi_raster.LIMITATIONS),
        }

    def _require_supported(self, index_code: str) -> None:
        if index_code not in self.supported_indices:
            raise UnsupportedRasterIndex(index_code)


_PROVIDERS: tuple[RasterProvider, ...] = (SentinelProcessProvider(),)


def normalize_index_code(index_code: str) -> str:
    normalized = str(index_code or "").strip().lower()
    if not normalized:
        raise UnsupportedRasterIndex(normalized)
    return normalized


def get_provider(index_code: str) -> RasterProvider:
    normalized = normalize_index_code(index_code)
    for provider in _PROVIDERS:
        if normalized in provider.supported_indices:
            return provider
    raise UnsupportedRasterIndex(normalized)


def provider_metadata(index_code: str) -> dict[str, Any]:
    normalized = normalize_index_code(index_code)
    return get_provider(normalized).metadata(normalized)


def validate_provider_size(index_code: str, size: int) -> int:
    metadata = provider_metadata(index_code)
    if size not in metadata["allowed_sizes"]:
        raise ValueError("Unsupported raster size")
    return size


def render_raster(
    field_id: int,
    geometry: dict[str, Any],
    index_code: str,
    observation_date: date,
    size: int,
) -> tuple[bytes, str, dict[str, Any]]:
    normalized = normalize_index_code(index_code)
    provider = get_provider(normalized)
    request = RasterRenderRequest(
        field_id=field_id,
        geometry=geometry,
        index_code=normalized,
        observation_date=observation_date,
        size=size,
    )
    image, cache_state = provider.render(request)
    return image, cache_state, provider.metadata(normalized)


def get_raster_png(
    field_id: int,
    geometry: dict[str, Any],
    observation_date: date,
    size: int,
) -> tuple[bytes, str]:
    """Compatibility signature used by the legacy NDVI API."""
    image, cache_state, _metadata = render_raster(
        field_id,
        geometry,
        "ndvi",
        observation_date,
        size,
    )
    return image, cache_state
