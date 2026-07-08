"""
Satellite collection service for multi-index Sentinel Hub calls.

Provides:
  - MultiIndexSentinelHubService: real Sentinel Hub Statistical API client
  - MockMultiIndexSatelliteService: test/mock service with realistic seasonal values

Reuses satellite_indices.py for evalscript building and response parsing.
Does NOT touch existing NDVI pipeline or satellite.py.
Does NOT write to DB -- caller's responsibility.
"""

import json
import logging
import math
import random
from datetime import date, timedelta
from typing import Optional, Any

import httpx
from shapely import wkt
from shapely.geometry import mapping

from config import settings
from services.satellite_indices import (
    SUPPORTED_INDEX_CODES,
    build_multi_index_evalscript,
    extract_intervals_metadata,
    normalize_index_code,
    parse_multi_index_stats_response,
    parse_multi_index_stats_response_flat,
    validate_index_quality,
)

logger = logging.getLogger(__name__)

STATISTICAL_API_URL = "https://services.sentinel-hub.com/api/v1/statistics"
TOKEN_URL = "https://services.sentinel-hub.com/auth/realms/main/protocol/openid-connect/token"

SATELLITE_INDEX_CODES = {c for c in SUPPORTED_INDEX_CODES}  # savi, evi, ndmi, ndre


# ─── Payload builder (testable, no IO) ──────────────────────────────────────


def build_statistical_payload(
    geometry_geojson: dict,
    evalscript: str,
    index_codes: list[str],
    date_from: date,
    date_to: date,
    aggregation_interval: str = "P1D",
    resolution: int = 20,
    max_cloud_coverage: int = 80,
) -> dict:
    """Build a Sentinel Hub Statistical API payload dict (no IO)."""
    return {
        "input": {
            "bounds": {
                "geometry": geometry_geojson,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [
                {
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {
                            "from": f"{date_from.isoformat()}T00:00:00Z",
                            "to": f"{date_to.isoformat()}T23:59:59Z",
                        },
                        "maxCloudCoverage": max_cloud_coverage,
                    },
                    "processing": {"harmonizeValues": True},
                }
            ],
        },
        "aggregation": {
            "timeRange": {
                "from": f"{date_from.isoformat()}T00:00:00Z",
                "to": f"{date_to.isoformat()}T23:59:59Z",
            },
            "aggregationInterval": {"of": aggregation_interval},
            "evalscript": evalscript,
            "resx": resolution,
            "resy": resolution,
        },
        "calculations": {
            code: {
                "statistics": {
                    "default": {
                        "percentiles": {"k": [10, 90]},
                    }
                }
            }
            for code in index_codes
        },
    }


# ─── Real Sentinel Hub multi-index service ─────────────────────────────────


class MultiIndexSentinelHubService:
    """Sentinel Hub Statistical API client for multi-index collection."""

    def __init__(self):
        self.client_id = settings.sentinel_hub_client_id
        self.client_secret = settings.sentinel_hub_client_secret
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._last_response_data: Optional[dict] = None  # raw JSON, for diagnostics

    def _get_access_token(self) -> str:
        """Obtain OAuth2 token from Sentinel Hub with caching."""
        import time

        if self._access_token and time.time() < self._token_expires_at - 300:
            return self._access_token

        if not self.client_id or not self.client_secret:
            raise ValueError(
                "Sentinel Hub credentials not configured. "
                "Set SENTINEL_HUB_CLIENT_ID and SENTINEL_HUB_CLIENT_SECRET in .env"
            )

        resp = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 3600)
        return self._access_token

    def collect_indices(
        self,
        geometry_wkt: str,
        index_codes: list[str],
        date_from: date,
        date_to: date,
        aggregation_interval: str = "P1D",
        resolution: int = 20,
        max_cloud_coverage: int = 80,
        timeout_s: float = 120.0,
    ) -> dict[str, dict]:
        """
        Call Sentinel Hub Statistical API with multi-index evalscript.

        Args:
            geometry_wkt: Field geometry in WKT format.
            index_codes: List of index codes (e.g. ["savi", "evi"]).
            date_from: Start date (inclusive).
            date_to: End date (inclusive).
            aggregation_interval: Sentinel Hub aggregation interval (e.g. "P1D", "P5D").
            resolution: Output resolution in meters.
            max_cloud_coverage: Max cloud cover percentage filter.
            timeout_s: HTTP timeout in seconds.

        Returns:
            Dict keyed by index_code, each value a parsed result dict
            with captured_date, mean_value, min_value, max_value, etc.

        Raises:
            ValueError: credentials missing.
            httpx.HTTPStatusError: API error response.
            httpx.RequestError: network error.
        """
        codes = [normalize_index_code(c) for c in index_codes if normalize_index_code(c) in SUPPORTED_INDEX_CODES]
        if not codes:
            raise ValueError("No supported index codes provided")

        token = self._get_access_token()
        evalscript = build_multi_index_evalscript(codes)

        geom = wkt.loads(geometry_wkt)
        geojson_geom = mapping(geom)

        payload = build_statistical_payload(
            geometry_geojson=geojson_geom,
            evalscript=evalscript,
            index_codes=codes,
            date_from=date_from,
            date_to=date_to,
            aggregation_interval=aggregation_interval,
            resolution=resolution,
            max_cloud_coverage=max_cloud_coverage,
        )

        resp = httpx.post(
            STATISTICAL_API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_s,
        )
        resp.raise_for_status()
        response_data = resp.json()

        # Preserve raw response for interval diagnostics (e.g. --debug-raw-intervals).
        # This is transient storage; the caller should extract intervals metadata
        # before the next collect_indices call overwrites it.
        self._last_response_data = response_data

        parsed = parse_multi_index_stats_response(response_data, codes)
        return parsed

    def collect_indices_flat(
        self,
        geometry_wkt: str,
        index_codes: list[str],
        date_from: date,
        date_to: date,
        aggregation_interval: str = "P1D",
        resolution: int = 20,
        max_cloud_coverage: int = 80,
        timeout_s: float = 120.0,
    ) -> list[dict]:
        """
        Call Sentinel Hub Statistical API and return flat per-interval records.

        Unlike ``collect_indices`` which collapses to one record per index,
        this returns one record per (interval, index_code) combo, preserving
        every non-ambiguous daily interval.

        Returns:
            List of per-interval, per-index result dicts.
        """
        codes = [normalize_index_code(c) for c in index_codes if normalize_index_code(c) in SUPPORTED_INDEX_CODES]
        if not codes:
            raise ValueError("No supported index codes provided")

        token = self._get_access_token()
        evalscript = build_multi_index_evalscript(codes)

        geom = wkt.loads(geometry_wkt)
        geojson_geom = mapping(geom)

        payload = build_statistical_payload(
            geometry_geojson=geojson_geom,
            evalscript=evalscript,
            index_codes=codes,
            date_from=date_from,
            date_to=date_to,
            aggregation_interval=aggregation_interval,
            resolution=resolution,
            max_cloud_coverage=max_cloud_coverage,
        )

        resp = httpx.post(
            STATISTICAL_API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_s,
        )
        resp.raise_for_status()
        response_data = resp.json()
        self._last_response_data = response_data

        return parse_multi_index_stats_response_flat(response_data, codes)

    def get_last_intervals_metadata(self, index_codes: list[str]) -> list[dict]:
        """Return sanitized interval metadata from the last API response.

        Calls ``extract_intervals_metadata`` on the cached raw response.
        Returns an empty list if no response has been collected yet.
        """
        if self._last_response_data is None:
            return []
        return extract_intervals_metadata(self._last_response_data, index_codes)


# ─── Mock multi-index service (for testability without credentials) ─────────


class MockMultiIndexSatelliteService:
    """
    Mock that generates realistic seasonal satellite index values.

    Used in --mock-sentinel mode for validation without Sentinel Hub credentials.
    Does NOT make network calls.

    Generates pseudo-interval metadata so ``get_last_intervals_metadata()``
    returns realistic interval diagnostics for mock-mode testing.
    """

    SEASONAL_BASE: dict[str, dict[int, float]] = {
        "savi": {
            1: 0.15, 2: 0.18, 3: 0.25, 4: 0.35,
            5: 0.48, 6: 0.60, 7: 0.62, 8: 0.55,
            9: 0.42, 10: 0.28, 11: 0.20, 12: 0.15,
        },
        "evi": {
            1: 0.12, 2: 0.15, 3: 0.22, 4: 0.32,
            5: 0.45, 6: 0.55, 7: 0.58, 8: 0.52,
            9: 0.38, 10: 0.25, 11: 0.18, 12: 0.12,
        },
        "ndmi": {
            1: -0.05, 2: -0.02, 3: 0.05, 4: 0.12,
            5: 0.18, 6: 0.22, 7: 0.20, 8: 0.15,
            9: 0.08, 10: 0.00, 11: -0.03, 12: -0.05,
        },
        "ndre": {
            1: 0.08, 2: 0.10, 3: 0.15, 4: 0.22,
            5: 0.32, 6: 0.40, 7: 0.42, 8: 0.38,
            9: 0.28, 10: 0.18, 11: 0.12, 12: 0.08,
        },
    }

    def __init__(self):
        self._last_mock_response: Optional[dict] = None

    def collect_indices(
        self,
        geometry_wkt: str,
        index_codes: list[str],
        date_from: date,
        date_to: date,
        aggregation_interval: str = "P1D",
        resolution: int = 20,
        max_cloud_coverage: int = 80,
        timeout_s: float = 120.0,
    ) -> dict[str, dict]:
        """
        Generate mock satellite index values for the given date range.

        The geometry and other parameters are accepted for interface compatibility
        but not used -- this is a pure mock.

        Returns one result per index.  ``captured_date`` is derived from the
        mock interval ``from`` field (the first day of the sub-interval), not
        from ``date_to``.  This mirrors the corrected real-service semantics.
        """
        codes = [normalize_index_code(c) for c in index_codes if normalize_index_code(c) in SUPPORTED_INDEX_CODES]
        if not codes:
            raise ValueError("No supported index codes provided")

        mock_month = date_to.month
        noise_range = 0.02

        # Generate multi-interval mock response if range > 15 days
        intervals: list[dict[str, Any]] = []
        step_days = min((date_to - date_from).days, 5)
        if step_days < 1:
            step_days = 1

        cursor = date_from
        while cursor <= date_to:
            month = cursor.month
            interval_end = min(cursor + timedelta(days=step_days), date_to)
            outputs: dict[str, Any] = {}

            for code in codes:
                base = self.SEASONAL_BASE.get(code, {}).get(month, 0.3)
                noise = random.uniform(-noise_range, noise_range)
                mean_val = max(-0.99, min(0.99, base + noise))
                spread = abs(mean_val) * 0.2 + 0.03

                outputs[code] = {
                    "bands": {
                        "B0": {
                            "stats": {
                                "sampleCount": 1000,
                                "noDataCount": 50,
                                "mean": str(round(mean_val, 4)),
                                "min": str(round(mean_val - spread, 4)),
                                "max": str(round(mean_val + spread, 4)),
                                "stDev": str(round(spread * 0.4, 4)),
                                "percentiles": {
                                    "10.0": str(round(mean_val - spread * 0.6, 4)),
                                    "90.0": str(round(mean_val + spread * 0.6, 4)),
                                },
                            }
                        }
                    }
                }

            intervals.append({
                "interval": {
                    "from": cursor.strftime("%Y-%m-%d") + "T00:00:00Z",
                    "to": interval_end.strftime("%Y-%m-%d") + "T00:00:00Z",
                },
                "outputs": outputs,
            })
            cursor = interval_end + timedelta(days=1)

        mock_response = {"data": intervals}
        self._last_mock_response = mock_response
        parsed = parse_multi_index_stats_response(mock_response, codes)
        return parsed

    def collect_indices_flat(
        self,
        geometry_wkt: str,
        index_codes: list[str],
        date_from: date,
        date_to: date,
        aggregation_interval: str = "P1D",
        resolution: int = 20,
        max_cloud_coverage: int = 80,
        timeout_s: float = 120.0,
    ) -> list[dict]:
        """
        Generate mock satellite index values and return flat per-interval records.

        Mirrors ``collect_indices`` but returns flat list preserving all intervals.
        """
        codes = [normalize_index_code(c) for c in index_codes if normalize_index_code(c) in SUPPORTED_INDEX_CODES]
        if not codes:
            raise ValueError("No supported index codes provided")

        mock_month = date_to.month
        noise_range = 0.02

        intervals: list[dict[str, Any]] = []
        step_days = min((date_to - date_from).days, 5)
        if step_days < 1:
            step_days = 1

        cursor = date_from
        while cursor <= date_to:
            month = cursor.month
            interval_end = min(cursor + timedelta(days=step_days), date_to)
            outputs: dict[str, Any] = {}

            for code in codes:
                base = self.SEASONAL_BASE.get(code, {}).get(month, 0.3)
                noise = random.uniform(-noise_range, noise_range)
                mean_val = max(-0.99, min(0.99, base + noise))
                spread = abs(mean_val) * 0.2 + 0.03

                outputs[code] = {
                    "bands": {
                        "B0": {
                            "stats": {
                                "sampleCount": 1000,
                                "noDataCount": 50,
                                "mean": str(round(mean_val, 4)),
                                "min": str(round(mean_val - spread, 4)),
                                "max": str(round(mean_val + spread, 4)),
                                "stDev": str(round(spread * 0.4, 4)),
                                "percentiles": {
                                    "10.0": str(round(mean_val - spread * 0.6, 4)),
                                    "90.0": str(round(mean_val + spread * 0.6, 4)),
                                },
                            }
                        }
                    }
                }

            intervals.append({
                "interval": {
                    "from": cursor.strftime("%Y-%m-%d") + "T00:00:00Z",
                    "to": interval_end.strftime("%Y-%m-%d") + "T00:00:00Z",
                },
                "outputs": outputs,
            })
            cursor = interval_end + timedelta(days=1)

        mock_response = {"data": intervals}
        self._last_mock_response = mock_response
        return parse_multi_index_stats_response_flat(mock_response, codes)

    def get_last_intervals_metadata(self, index_codes: list[str]) -> list[dict]:
        """Return sanitized interval metadata from the last mock response."""
        if self._last_mock_response is None:
            return []
        return extract_intervals_metadata(self._last_mock_response, index_codes)


# ─── Factory ─────────────────────────────────────────────────────────────────


def get_multi_index_satellite_service() -> MultiIndexSentinelHubService:
    """Return a real Sentinel Hub service (credentials required at call time)."""
    return MultiIndexSentinelHubService()


def get_mock_multi_index_satellite_service() -> MockMultiIndexSatelliteService:
    """Return a mock service that never calls the network."""
    return MockMultiIndexSatelliteService()


# ─── Response validation pass-through ───────────────────────────────────────


def filter_by_quality(
    parsed: dict[str, dict],
    index_codes: list[str],
    max_cloud_cover: Optional[float] = None,
    min_valid_pixels: Optional[float] = None,
) -> dict[str, dict]:
    """
    Filter parsed results through quality gates. Returns only passing items.

    Args:
        parsed: Result from parse_multi_index_stats_response().
        index_codes: Expected index codes.
        max_cloud_cover: Max allowed cloud cover % (None = skip cloud check).
        min_valid_pixels: Min required valid pixels % (None = skip valid check).

    Returns:
        Filtered dict with only quality-passing results.
    """
    passed: dict[str, dict] = {}
    for code in index_codes:
        norm = normalize_index_code(code)
        if norm not in parsed:
            continue
        data = parsed[norm]
        cc = data.get("cloud_cover_pct")
        vp = data.get("valid_pixels_pct")
        valid, reason = validate_index_quality(
            norm,
            data.get("mean_value"),
            cloud_cover_pct=cc,
            min_value=data.get("min_value"),
            max_value=data.get("max_value"),
            valid_pixels_pct=vp,
        )
        if valid:
            passed[norm] = data
        else:
            logger.info("Quality gate rejected %s: %s", norm, reason)
    return passed

