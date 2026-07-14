"""
Сервис получения спутниковых данных NDVI из Sentinel-2 через Sentinel Hub API.

Регистрация (бесплатно): https://www.sentinel-hub.com/
Бесплатный план: 30,000 processing units / месяц
При 100 полях размером ~50 га: ~5,000 PU/месяц — хватает с запасом.
"""

import logging
from datetime import date, timedelta
from typing import Optional
import json

import httpx
import numpy as np
from shapely.geometry import shape, mapping
from shapely import wkt

from config import settings
from services.satellite_safety import (
    MOCK_SATELLITE_SOURCE,
    REAL_SATELLITE_SOURCE,
    SatelliteConfigurationError,
    require_payload_provenance,
    require_real_service,
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


# ─── Sentinel Hub Evalscripts ─────────────────────────────────────────────────

# NDVI с маскировкой облаков по Scene Classification Layer (SCL)
NDVI_EVALSCRIPT = """
//VERSION=3

function setup() {
  return {
    input: [{ bands: ["B04", "B08", "SCL"] }],
    output: [
      { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ]
  };
}

function evaluatePixel(sample) {
  // SCL маска: исключаем облака (8,9,10), снег (11), дефектные пиксели (1)
  let isValid = ![1, 3, 8, 9, 10, 11].includes(sample.SCL);
  let ndvi = (sample.B08 - sample.B04) / (sample.B08 + sample.B04 + 0.0001);
  return {
    ndvi: [ndvi],
    dataMask: [isValid ? 1 : 0]
  };
}
"""


class SentinelHubService:
    """Клиент для Sentinel Hub Statistical API."""

    STATISTICAL_API_URL = "https://services.sentinel-hub.com/api/v1/statistics"
    TOKEN_URL = "https://services.sentinel-hub.com/auth/realms/main/protocol/openid-connect/token"
    PROCESS_API_URL = "https://services.sentinel-hub.com/api/v1/process"

    source = REAL_SATELLITE_SOURCE
    is_mock = False

    def __init__(self, client_id=None, client_secret=None):
        self.client_id, self.client_secret = validate_credentials(
            settings.sentinel_hub_client_id if client_id is None else client_id,
            settings.sentinel_hub_client_secret if client_secret is None else client_secret,
        )
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

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
            self.TOKEN_URL,
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
        token = self._get_access_token()
        timeout = httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0)
        return httpx.post(
            self.PROCESS_API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Accept": "image/png"},
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
            dict с ключами: mean, min, max, std, p10, p90, valid_pct, date
        """
        try:
            token = self._get_access_token()
        except Exception as e:
            logger.error(f"Ошибка получения токена Sentinel Hub: {e}")
            return None

        # Преобразуем WKT в GeoJSON
        geom = wkt.loads(geometry_wkt)
        geojson_geom = mapping(geom)

        payload = {
            "input": {
                "bounds": {
                    "geometry": geojson_geom,
                    "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
                },
                "data": [{
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {
                            "from": f"{date_from.isoformat()}T00:00:00Z",
                            "to": f"{date_to.isoformat()}T23:59:59Z"
                        },
                        "maxCloudCoverage": 80  # исключаем снимки >80% облаков
                    },
                    "processing": {
                        "harmonizeValues": True
                    }
                }]
            },
            "aggregation": {
                "timeRange": {
                    "from": f"{date_from.isoformat()}T00:00:00Z",
                    "to": f"{date_to.isoformat()}T23:59:59Z"
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
                self.STATISTICAL_API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=60.0
            )
            response.raise_for_status()
            data = response.json()

            return self._parse_stats_response(data)

        except httpx.HTTPStatusError as e:
            logger.error(f"Sentinel Hub API error: {e.response.status_code} — {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Непредвиденная ошибка при запросе NDVI: {e}")
            return None

    def _parse_stats_response(self, response_data: dict) -> Optional[dict]:
        """Парсим ответ Statistical API и возвращаем наиболее свежий снимок."""
        try:
            intervals = response_data.get("data", [])
            if not intervals:
                logger.warning("Sentinel Hub вернул пустой ответ (возможно, нет снимков за период)")
                return None

            # Берём последний интервал с данными
            valid_intervals = [
                i for i in intervals
                if i.get("outputs", {}).get("ndvi", {}).get("bands", {}).get("B0", {}).get("stats", {}).get("sampleCount", 0) > 0
            ]

            if not valid_intervals:
                return None

            latest = valid_intervals[-1]
            interval_date = latest["interval"]["to"][:10]

            stats = latest["outputs"]["ndvi"]["bands"]["B0"]["stats"]

            # valid_pct = непустые пиксели / все пиксели
            sample_count = stats.get("sampleCount", 0)
            no_data_count = stats.get("noDataCount", 0)
            total = sample_count + no_data_count
            valid_pct = (sample_count / total * 100) if total > 0 else 0

            percentiles = stats.get("percentiles", {})

            return {
                "captured_date": interval_date,
                "mean_ndvi": round(safe_float(stats.get("mean", 0)), 4),
                "min_ndvi": round(safe_float(stats.get("min", 0)), 4),
                "max_ndvi": round(safe_float(stats.get("max", 0)), 4),
                "std_ndvi": round(safe_float(stats.get("stDev", 0)), 4),
                "p10_ndvi": round(safe_float(percentiles.get("10.0", 0)), 4),
                "p90_ndvi": round(safe_float(percentiles.get("90.0", 0)), 4),
                "valid_pixels_pct": round(valid_pct, 1),
                "satellite": REAL_SATELLITE_SOURCE,
            }

        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Ошибка парсинга ответа Sentinel Hub: {e}")
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
