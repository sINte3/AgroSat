"""
Сервис получения спутниковых данных NDVI из Sentinel-2 через Sentinel Hub API.

Регистрация (бесплатно): https://www.sentinel-hub.com/
Бесплатный план: 30,000 processing units / месяц
При 100 полях размером ~50 га: ~5,000 PU/месяц — хватает с запасом.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Optional
import json

import httpx
import numpy as np
from shapely.geometry import shape, mapping
from shapely import wkt

from config import settings
from services.sentinel_provider import resolve_sentinel_provider
from services.satellite_safety import (
    MOCK_SATELLITE_SOURCE,
    REAL_SATELLITE_SOURCE,
    SatelliteConfigurationError,
    require_payload_provenance,
    require_real_service,
    safe_provider_error_summary,
    validate_credentials,
)

logger = logging.getLogger(__name__)


def safe_float(v, default=0.0):
    """Convert to float, returning default for None/invalid."""
    try:
        val = float(v)
        import math
        if math.isnan(val) or math.isinf(val):
            return default
        return val
    except (TypeError, ValueError):
        return default


# ─── Quality Gate ────────────────────────────────────────────────────────────────

def validate_ndvi_quality(mean_ndvi, cloud_cover_pct=None, min_ndvi=None, max_ndvi=None, field_name=""):
    """
    Validate NDVI data quality before saving to database.
    Returns (is_valid, rejection_reason).

    Rejection criteria for agricultural fields:
    - mean_ndvi < 0: water/cloud/shadow artifact (impossible for crops)
    - mean_ndvi > 1.0: sensor error
    - cloud_cover_pct > 30: too cloudy for reliable reading
    - min_ndvi < -0.5 AND max_ndvi > 0.5: mixed pixel (cloud edge + vegetation)
    - mean_ndvi very close to 0 (< 0.02) with low std: bare sensor noise
    """
    if mean_ndvi is None:
        return False, "mean_ndvi is None"

    try:
        mean_ndvi = float(mean_ndvi)
    except (TypeError, ValueError):
        return False, f"mean_ndvi not numeric: {mean_ndvi}"

    # NaN / Inf / zero — sensor noise, no vegetation
    import math
    if math.isnan(mean_ndvi) or math.isinf(mean_ndvi):
        logger.warning(
            f"NDVI quality gate REJECT [{field_name}]: NaN/Inf NDVI"
        )
        return False, f"NaN/Inf NDVI"

    if mean_ndvi <= 0.0:
        logger.warning(
            f"NDVI quality gate REJECT [{field_name}]: non-positive NDVI {mean_ndvi:.4f} "
            f"(no vegetation signal)"
        )
        return False, f"non-positive NDVI ({mean_ndvi:.4f})"

    if mean_ndvi > 1.0:
        logger.warning(
            f"NDVI quality gate REJECT [{field_name}]: NDVI > 1.0 ({mean_ndvi:.4f})"
        )
        return False, f"NDVI exceeds 1.0 ({mean_ndvi:.4f})"

    if cloud_cover_pct is not None:
        try:
            cloud_cover_pct = float(cloud_cover_pct)
        except (TypeError, ValueError):
            cloud_cover_pct = None

    if cloud_cover_pct is not None and cloud_cover_pct > 30:
        logger.info(
            f"NDVI quality gate REJECT [{field_name}]: cloud cover {cloud_cover_pct:.1f}% > 30%"
        )
        return False, f"high cloud cover ({cloud_cover_pct:.1f}%)"

    # Mixed pixel detection
    if min_ndvi is not None and max_ndvi is not None:
        try:
            min_val = float(min_ndvi)
            max_val = float(max_ndvi)
            if min_val < -0.3 and max_val > 0.4:
                logger.warning(
                    f"NDVI quality gate REJECT [{field_name}]: mixed pixel "
                    f"(min={min_val:.4f}, max={max_val:.4f})"
                )
                return False, f"mixed pixel (min={min_val:.4f}, max={max_val:.4f})"
        except (TypeError, ValueError):
            pass

    return True, "ok"


# ─── Sentinel Hub temporal and evalscript contracts ───────────────────────────

SENTINEL_DATASET = "sentinel-2-l2a"
SENTINEL_CRS = "http://www.opengis.net/def/crs/EPSG/0/4326"
SENTINEL_MAX_CLOUD_COVERAGE = 80
NDVI_INDEX_CODE = "ndvi"
NDVI_INVALID_SCL_CLASSES = (0, 1, 3, 8, 9, 10, 11)
NDVI_DENOMINATOR_EPSILON = "0.000001"
NDVI_MASK_CONTRACT_ID = "s2l2a-datamask-scl-0-1-3-8-9-10-11-v1"
STATISTICAL_INTERVAL_SEMANTICS = "half_open_utc_aggregation_bucket_[from,to)"
CANONICAL_DATE_SEMANTICS = "aggregation_interval_start_utc_date_not_acquisition_date"


def parse_provider_utc_timestamp(value: object) -> datetime:
    """Parse a provider timestamp and require an explicit UTC offset."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Provider interval timestamp is missing")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError("Provider interval timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("Provider interval timestamp must be timezone-aware UTC")
    return parsed.astimezone(timezone.utc)


def format_utc_timestamp(value: datetime) -> str:
    """Return a canonical ISO-8601 UTC timestamp with a ``Z`` suffix."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Timestamp must be timezone-aware UTC")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class SentinelObservationInterval:
    """Truthful in-memory Statistical API observation-bucket contract."""

    interval_from_utc: datetime
    interval_to_utc: datetime
    provider: str
    source: str
    dataset: str
    index_code: str
    aggregation_interval: str

    def __post_init__(self) -> None:
        if (
            self.interval_from_utc.tzinfo is None
            or self.interval_from_utc.utcoffset() != timedelta(0)
            or self.interval_to_utc.tzinfo is None
            or self.interval_to_utc.utcoffset() != timedelta(0)
        ):
            raise ValueError("Observation interval bounds must be timezone-aware UTC")
        if self.interval_to_utc <= self.interval_from_utc:
            raise ValueError("Observation interval end must be after its start")

    @property
    def canonical_date(self) -> date:
        return self.interval_from_utc.date()

    @property
    def is_daily(self) -> bool:
        return self.interval_to_utc - self.interval_from_utc == timedelta(days=1)

    def as_result_metadata(self) -> dict[str, object]:
        return {
            "captured_date": self.canonical_date.isoformat(),
            "captured_date_semantics": CANONICAL_DATE_SEMANTICS,
            "interval_from_utc": format_utc_timestamp(self.interval_from_utc),
            "interval_to_utc": format_utc_timestamp(self.interval_to_utc),
            "aggregation_interval": self.aggregation_interval,
            "aggregation_interval_semantics": STATISTICAL_INTERVAL_SEMANTICS,
            "interval_is_daily": self.is_daily,
            "provider": self.provider,
            "source": self.source,
            "dataset": self.dataset,
            "index_code": self.index_code,
            "acquisition_timestamp_available": False,
        }


def _utc_midnight(day: date) -> datetime:
    return datetime.combine(day, datetime_time.min, tzinfo=timezone.utc)


_NDVI_INVALID_SCL_JS = json.dumps(list(NDVI_INVALID_SCL_CLASSES), separators=(",", ":"))

# NDVI with one shared provider data-mask and SCL contract for statistics/raster.
NDVI_EVALSCRIPT = """
//VERSION=3

function setup() {
  return {
    input: [{ bands: ["B04", "B08", "SCL", "dataMask"] }],
    output: [
      { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ]
  };
}

function evaluatePixel(sample) {
  let isValid = sample.dataMask === 1 && !__INVALID_SCL__.includes(sample.SCL);
  let ndvi = (sample.B08 - sample.B04) / (sample.B08 + sample.B04 + __EPSILON__);
  return {
    ndvi: [ndvi],
    dataMask: [isValid ? 1 : 0]
  };
}
""".replace("__INVALID_SCL__", _NDVI_INVALID_SCL_JS).replace(
    "__EPSILON__", NDVI_DENOMINATOR_EPSILON
)


class SentinelHubService:
    """Клиент для Sentinel Hub Statistical API."""

    source = REAL_SATELLITE_SOURCE
    is_mock = False

    def __init__(self, client_id=None, client_secret=None, *, provider=None):
        self.client_id, self.client_secret = validate_credentials(
            settings.sentinel_hub_client_id if client_id is None else client_id,
            settings.sentinel_hub_client_secret if client_secret is None else client_secret,
        )
        self._provider_endpoints = resolve_sentinel_provider(
            settings.sentinel_hub_provider if provider is None else provider
        )
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    @property
    def provider(self) -> str:
        return self._provider_endpoints.name

    @property
    def provider_metadata(self) -> dict[str, str]:
        return self._provider_endpoints.sanitized_metadata()

    def _get_access_token(self) -> str:
        """Получить OAuth2 токен от Sentinel Hub с кешированием."""
        import time

        # Return cached token if still valid (tokens last ~60 min, refresh at 55)
        if self._access_token and time.time() < self._token_expires_at - 300:
            return self._access_token

        if not self.client_id or not self.client_secret:
            raise ValueError(
                "Sentinel Hub credentials не настроены. "
                "Зарегистрируйтесь на https://www.sentinel-hub.com/ и добавьте "
                "SENTINEL_HUB_CLIENT_ID и SENTINEL_HUB_CLIENT_SECRET в .env"
            )

        import time

        response = httpx.post(
            self._provider_endpoints.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        )
        response.raise_for_status()
        token_data = response.json()
        self._access_token = token_data["access_token"]
        self._token_expires_at = time.time() + token_data.get("expires_in", 3600)
        return self._access_token

    def get_process_png(self, payload: dict):
        """Request a Process API PNG using the existing OAuth token path."""
        return self.get_process_response(payload, accept="image/png")

    def get_process_response(self, payload: dict, *, accept: str):
        """Request one bounded Process API representation from the allowlist."""
        token = self._get_access_token()
        timeout = httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0)
        return httpx.post(
            self._provider_endpoints.process_url,
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Accept": accept},
            timeout=timeout,
            follow_redirects=False,
        )

    def get_catalog_response(self, payload: dict):
        """Request one bounded STAC search from the immutable provider allowlist."""
        token = self._get_access_token()
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
        return httpx.post(
            self._provider_endpoints.catalog_url,
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/geo+json"},
            timeout=timeout,
            follow_redirects=False,
        )

    def get_ndvi_stats(
        self,
        geometry_wkt: str,
        date_from: date,
        date_to: date,
        aggregation_interval: str = "P5D",
    ) -> Optional[dict]:
        """
        Получить статистику NDVI для поля за период.

        Args:
            geometry_wkt: Геометрия поля в формате WKT (POLYGON)
            date_from: Начало периода
            date_to: Конец периода (включительно)

        Returns:
            dict with statistics plus explicit half-open UTC interval metadata.
        """
        if not isinstance(date_from, date) or not isinstance(date_to, date) or date_to < date_from:
            logger.error("Invalid Sentinel Statistical API application date window")
            return None
        try:
            token = self._get_access_token()
        except Exception as error:
            logger.error(safe_provider_error_summary(error))
            return None

        # Преобразуем WKT в GeoJSON
        geom = wkt.loads(geometry_wkt)
        geojson_geom = mapping(geom)
        request_from_utc = format_utc_timestamp(_utc_midnight(date_from))
        request_to_utc = format_utc_timestamp(_utc_midnight(date_to + timedelta(days=1)))

        payload = {
            "input": {
                "bounds": {
                    "geometry": geojson_geom,
                    "properties": {"crs": SENTINEL_CRS}
                },
                "data": [{
                    "type": SENTINEL_DATASET,
                    "dataFilter": {
                        "timeRange": {
                            "from": request_from_utc,
                            "to": request_to_utc,
                        },
                        "maxCloudCoverage": SENTINEL_MAX_CLOUD_COVERAGE,
                    },
                    "processing": {
                        "harmonizeValues": True
                    }
                }]
            },
            "aggregation": {
                "timeRange": {
                    "from": request_from_utc,
                    "to": request_to_utc,
                },
                "aggregationInterval": {"of": aggregation_interval},
                "evalscript": NDVI_EVALSCRIPT,
                "resx": 10,
                "resy": 10
            },
            "calculations": {
                "default": {
                    "statistics": {
                        "default": {
                            "percentiles": {"k": [10, 25, 75, 90]}
                        }
                    }
                }
            }
        }

        try:
            response = httpx.post(
                self._provider_endpoints.statistical_url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=60.0
            )
            response.raise_for_status()
            data = response.json()

            return self._parse_stats_response(
                data,
                aggregation_interval=aggregation_interval,
            )

        except httpx.HTTPStatusError as error:
            logger.error(safe_provider_error_summary(error))
            return None
        except Exception as error:
            logger.error(safe_provider_error_summary(error))
            return None

    def _parse_stats_response(
        self,
        response_data: dict,
        *,
        aggregation_interval: str = "P1D",
    ) -> Optional[dict]:
        """Return the latest usable Statistical aggregation interval by UTC time."""
        try:
            intervals = response_data.get("data", [])
            if not isinstance(intervals, list) or not intervals:
                logger.warning("Sentinel Hub вернул пустой ответ (возможно, нет снимков за период)")
                return None

            usable: list[
                tuple[SentinelObservationInterval, dict, int, int]
            ] = []
            for interval in intervals:
                if not isinstance(interval, dict):
                    continue
                raw_bounds = interval.get("interval")
                if not isinstance(raw_bounds, dict):
                    continue
                try:
                    observation_interval = SentinelObservationInterval(
                        interval_from_utc=parse_provider_utc_timestamp(raw_bounds.get("from")),
                        interval_to_utc=parse_provider_utc_timestamp(raw_bounds.get("to")),
                        provider=self.provider,
                        source=REAL_SATELLITE_SOURCE,
                        dataset=SENTINEL_DATASET,
                        index_code=NDVI_INDEX_CODE,
                        aggregation_interval=aggregation_interval,
                    )
                except (TypeError, ValueError):
                    continue
                stats = (
                    interval.get("outputs", {})
                    .get(NDVI_INDEX_CODE, {})
                    .get("bands", {})
                    .get("B0", {})
                    .get("stats", {})
                )
                if not isinstance(stats, dict):
                    continue
                try:
                    sample_count = int(stats.get("sampleCount", 0))
                    no_data_count = int(stats.get("noDataCount", 0))
                except (TypeError, ValueError, OverflowError):
                    continue
                if (
                    sample_count <= 0
                    or no_data_count < 0
                    or no_data_count > sample_count
                    or sample_count - no_data_count <= 0
                ):
                    continue
                usable.append(
                    (observation_interval, stats, sample_count, no_data_count)
                )

            if not usable:
                return None

            observation_interval, stats, sample_count, no_data_count = max(
                usable,
                key=lambda candidate: (
                    candidate[0].interval_from_utc,
                    candidate[0].interval_to_utc,
                ),
            )
            valid_pixel_count = sample_count - no_data_count
            valid_pct = valid_pixel_count / sample_count * 100

            percentiles = stats.get("percentiles", {})

            return {
                **observation_interval.as_result_metadata(),
                "mean_ndvi": round(safe_float(stats.get("mean", 0)), 4),
                "min_ndvi": round(safe_float(stats.get("min", 0)), 4),
                "max_ndvi": round(safe_float(stats.get("max", 0)), 4),
                "std_ndvi": round(safe_float(stats.get("stDev", 0)), 4),
                "p10_ndvi": round(safe_float(percentiles.get("10.0", 0)), 4),
                "p90_ndvi": round(safe_float(percentiles.get("90.0", 0)), 4),
                "valid_pixels_pct": round(valid_pct, 1),
                "sample_count": sample_count,
                "no_data_count": no_data_count,
                "valid_pixel_count": valid_pixel_count,
                "mask_contract_id": NDVI_MASK_CONTRACT_ID,
                "satellite": REAL_SATELLITE_SOURCE,
            }

        except (KeyError, IndexError, TypeError, ValueError) as error:
            logger.error(safe_provider_error_summary(error))
            return None

    def get_latest_ndvi(self, geometry_wkt: str) -> Optional[dict]:
        """Получить самый свежий NDVI (за последние 15 дней)."""
        today = date.today()
        date_from = today - timedelta(days=15)
        return self.get_ndvi_stats(geometry_wkt, date_from, today)

    def get_ndvi_stats_for_date(self, geometry_wkt: str, target_date: date) -> Optional[dict]:
        """
        Получить NDVI для конкретной даты (±2 дня поисковое окно).
        Использует 1-дневный aggregationInterval для точного попадания.
        """
        time_from = target_date - timedelta(days=2)
        time_to = target_date + timedelta(days=2)
        return self.get_ndvi_stats(geometry_wkt, time_from, time_to, aggregation_interval="P1D")


# ─── Mock режим (для разработки без API ключей) ───────────────────────────────

class MockSatelliteService:
    """
    Заглушка для разработки без ключей Sentinel Hub.
    Генерирует реалистичные NDVI данные на основе культуры и месяца.
    Замените на SentinelHubService когда получите ключи.
    """
    source = MOCK_SATELLITE_SOURCE
    is_mock = True

    def get_ndvi_stats(self, geometry_wkt: str, date_from: date, date_to: date) -> dict:
        import random
        from datetime import datetime

        # Базовое NDVI зависит от месяца (сезонность Узбекистана)
        month = date_to.month
        seasonal_base = {
            1: 0.18, 2: 0.22, 3: 0.30, 4: 0.38,
            5: 0.52, 6: 0.65, 7: 0.68, 8: 0.60,
            9: 0.45, 10: 0.30, 11: 0.22, 12: 0.18
        }
        base = seasonal_base.get(month, 0.35)
        noise = random.uniform(-0.05, 0.05)
        mean = max(0.05, min(0.95, base + noise))

        return {
            "captured_date": date_to.isoformat(),
            "mean_ndvi": round(mean, 4),
            "min_ndvi": round(mean - 0.12, 4),
            "max_ndvi": round(mean + 0.12, 4),
            "std_ndvi": round(random.uniform(0.03, 0.08), 4),
            "p10_ndvi": round(mean - 0.10, 4),
            "p90_ndvi": round(mean + 0.10, 4),
            "valid_pixels_pct": round(random.uniform(75, 98), 1),
            "satellite": MOCK_SATELLITE_SOURCE,
        }

    def get_latest_ndvi(self, geometry_wkt: str) -> dict:
        today = date.today()
        return self.get_ndvi_stats(geometry_wkt, today - timedelta(days=5), today)

    def _mock_historical_ndvi(self, field, target_date: date) -> dict:
        """Generate realistic mock NDVI for a historical date based on crop."""
        import random
        import math

        name_lower = (field.name or "").lower()

        if "пшеница" in name_lower or "галла" in name_lower:
            # Winter wheat: dormant Jan-Feb, active growth Mar-May, harvest/senescence Jun+
            day_of_year = target_date.timetuple().tm_yday
            if day_of_year < 60:
                base = 0.25 + random.uniform(-0.05, 0.05)
            elif day_of_year < 120:
                base = 0.25 + (day_of_year - 60) * 0.007 + random.uniform(-0.05, 0.05)
            elif day_of_year < 152:
                base = 0.65 + random.uniform(-0.08, 0.08)
            else:
                base = 0.65 - (day_of_year - 152) * 0.02 + random.uniform(-0.05, 0.05)
        elif "пахта" in name_lower or "хлопок" in name_lower:
            # Cotton: bare soil Jan-Apr, emergence May, vegetative Jun-Jul, peak Aug+
            day_of_year = target_date.timetuple().tm_yday
            if day_of_year < 100:
                base = 0.10 + random.uniform(-0.03, 0.05)
            elif day_of_year < 140:
                base = 0.10 + (day_of_year - 100) * 0.005 + random.uniform(-0.03, 0.03)
            elif day_of_year < 200:
                base = 0.30 + (day_of_year - 140) * 0.005 + random.uniform(-0.05, 0.05)
            else:
                base = 0.55 + random.uniform(-0.08, 0.08)
        else:
            # Generic seasonal crop
            day_of_year = target_date.timetuple().tm_yday
            base = 0.30 + 0.15 * math.sin((day_of_year - 90) * math.pi / 180) + random.uniform(-0.05, 0.05)

        mean_ndvi = max(0.05, min(0.85, base))

        return {
            "captured_date": target_date.isoformat(),
            "mean_ndvi": round(mean_ndvi, 4),
            "min_ndvi": round(max(0.01, mean_ndvi - random.uniform(0.05, 0.15)), 4),
            "max_ndvi": round(min(0.95, mean_ndvi + random.uniform(0.05, 0.15)), 4),
            "std_ndvi": round(random.uniform(0.02, 0.08), 4),
            "cloud_cover_pct": round(random.uniform(0, 20), 1),
            "valid_pixels_pct": round(random.uniform(75, 98), 1),
            "satellite": MOCK_SATELLITE_SOURCE,
        }

    def get_ndvi_stats_for_date(self, geometry_wkt: str, target_date: date) -> Optional[dict]:
        """
        For Mock mode: generate historical NDVI data with seasonal patterns.
        Falls back to _mock_historical_ndvi when mock mode is active.
        """
        # MockSatelliteService can't get real data — this will be handled
        # by the fetch_ndvi_for_field_date wrapper which calls _mock_historical_ndvi
        return None


def fetch_ndvi_for_field_date(db, field, target_date: date, *, service) -> Optional[dict]:
    """
    Fetch NDVI for a specific target date (±2 day window).
    Requires an explicitly real SentinelHubService identity.

    Returns the NDVI data dict if valid data was found, None otherwise.
    Does NOT save to DB — caller is responsible for that.
    """
    require_real_service(service)

    from sqlalchemy import text

    # Get geometry WKT
    result = db.execute(
        text("SELECT ST_AsText(geometry) FROM fields WHERE id = :id"),
        {"id": field.id}
    ).fetchone()
    if not result:
        logger.warning(f"fetch_ndvi_for_field_date: field {field.id} has no geometry")
        return None

    geometry_wkt = result[0]

    # The caller supplied an explicitly constructed real service.
    ndvi_data = service.get_ndvi_stats_for_date(geometry_wkt, target_date)

    if ndvi_data is None:
        return None

    # Quality gate
    is_valid, reason = validate_ndvi_quality(
        mean_ndvi=ndvi_data["mean_ndvi"],
        cloud_cover_pct=ndvi_data.get("cloud_cover_pct"),
        min_ndvi=ndvi_data.get("min_ndvi"),
        max_ndvi=ndvi_data.get("max_ndvi"),
        field_name=field.name,
    )
    if not is_valid:
        logger.debug(f"Quality gate rejected {field.name} @ {target_date}: {reason}")
        return None

    require_payload_provenance(ndvi_data)
    return ndvi_data


def get_satellite_service(*, allow_mock: bool = False):
    """Build an explicitly real service, or an explicit no-write mock."""
    client_id = settings.sentinel_hub_client_id
    client_secret = settings.sentinel_hub_client_secret
    both_missing = not (isinstance(client_id, str) and client_id.strip()) and not (
        isinstance(client_secret, str) and client_secret.strip()
    )
    if allow_mock and both_missing:
        return MockSatelliteService()
    validate_credentials(client_id, client_secret)
    return SentinelHubService(client_id, client_secret)
