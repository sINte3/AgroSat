"""Secure, cached Sentinel-2 NDVI raster support for accepted observations."""

import json
import math
from datetime import date
from typing import Any

import httpx

from services.cache import cache_get_binary, cache_set_binary
from services.satellite import get_satellite_service
from services.satellite_safety import SatelliteConfigurationError

ALLOWED_SIZES = (256, 512, 768, 1024)
DEFAULT_SIZE = 512
PALETTE_VERSION = "ndvi-rgba-scl-v1"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_PNG_BYTES = 8 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

LEGEND = (
    {"from": None, "to": 0.1, "color": "#8c510a", "label": "Очень низкий спектральный сигнал"},
    {"from": 0.1, "to": 0.3, "color": "#d8b365", "label": "Низкий спектральный сигнал"},
    {"from": 0.3, "to": 0.5, "color": "#f6e8c3", "label": "Средний спектральный сигнал"},
    {"from": 0.5, "to": 0.7, "color": "#5ab4ac", "label": "Повышенный спектральный сигнал"},
    {"from": 0.7, "to": 1.0, "color": "#01665e", "label": "Высокий спектральный сигнал"},
)

LIMITATIONS = (
    "Растр показывает спектральный NDVI-сигнал, а не агрономический диагноз.",
    "Облака, тени и снег маскируются и отображаются прозрачными участками.",
    "Сравнение следует выполнять для одного поля и сопоставимых дат.",
    "Изображение требует подтверждения фактическим осмотром поля.",
)

EVALSCRIPT = """//VERSION=3
// RGBA output; alpha is zero for dataMask/SCL-rejected pixels.
function setup() {
  return {
    input: [{ bands: [\"B04\", \"B08\", \"SCL\", \"dataMask\"] }],
    output: { bands: 4, sampleType: \"UINT8\" }
  };
}
function ramp(value) {
  if (value < 0.1) return [140, 81, 10];
  if (value < 0.3) return [216, 179, 101];
  if (value < 0.5) return [246, 232, 195];
  if (value < 0.7) return [90, 180, 172];
  return [1, 102, 94];
}
function evaluatePixel(sample) {
  let valid = sample.dataMask === 1 && ![0, 1, 3, 8, 9, 10, 11].includes(sample.SCL);
  if (!valid) return [0, 0, 0, 0];
  let ndvi = (sample.B08 - sample.B04) / (sample.B08 + sample.B04 + 0.000001);
  let rgb = ramp(ndvi);
  return [rgb[0], rgb[1], rgb[2], 255];
}"""


class RasterServiceUnavailable(Exception):
    pass


class RasterUpstreamInvalid(Exception):
    pass


class RasterUpstreamTimeout(Exception):
    pass


def raster_cache_key(field_id: int, observation_date: date, size: int) -> str:
    return f"ndvi-raster:{PALETTE_VERSION}:{field_id}:{observation_date.isoformat()}:{size}"


def validate_size(size: int) -> int:
    if size not in ALLOWED_SIZES:
        raise ValueError("Unsupported raster size")
    return size


def validate_bbox(values: Any) -> list[float]:
    try:
        bbox = [float(value) for value in values]
    except (TypeError, ValueError):
        raise ValueError("Field geometry is unavailable") from None
    if len(bbox) != 4 or not all(math.isfinite(value) for value in bbox):
        raise ValueError("Field geometry is unavailable")
    west, south, east, north = bbox
    if west >= east or south >= north:
        raise ValueError("Field geometry is unavailable")
    return bbox


def build_process_payload(geometry: dict[str, Any], observation_date: date, size: int) -> dict[str, Any]:
    next_day = date.fromordinal(observation_date.toordinal() + 1)
    return {
        "input": {
            "bounds": {"geometry": geometry, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {"timeRange": {"from": f"{observation_date.isoformat()}T00:00:00Z", "to": f"{next_day.isoformat()}T00:00:00Z"}},
                "processing": {"harmonizeValues": True},
                "evalscript": EVALSCRIPT,
            }],
        },
        "output": {"width": size, "height": size, "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
    }


def validate_png(payload: bytes) -> bool:
    return bool(payload) and len(payload) <= MAX_PNG_BYTES and payload.startswith(PNG_SIGNATURE)


def request_process_png(geometry: dict[str, Any], observation_date: date, size: int) -> bytes:
    try:
        service = get_satellite_service()
        if getattr(service, "is_mock", False):
            raise RasterServiceUnavailable("Satellite service unavailable")
        response = service.get_process_png(build_process_payload(geometry, observation_date, size))
    except SatelliteConfigurationError as exc:
        raise RasterServiceUnavailable("Satellite service unavailable") from exc
    except httpx.TimeoutException as exc:
        raise RasterUpstreamTimeout("Satellite service timeout") from exc
    except httpx.HTTPError as exc:
        raise RasterServiceUnavailable("Satellite service unavailable") from exc
    if response.status_code != 200:
        raise RasterServiceUnavailable("Satellite service unavailable")
    if "image/png" not in response.headers.get("content-type", "").lower() or not validate_png(response.content):
        raise RasterUpstreamInvalid("Invalid satellite raster response")
    return response.content


def get_raster_png(field_id: int, geometry: dict[str, Any], observation_date: date, size: int) -> tuple[bytes, str]:
    validate_size(size)
    key = raster_cache_key(field_id, observation_date, size)
    cached = cache_get_binary(key)
    if cached and validate_png(cached):
        return cached, "HIT"
    image = request_process_png(geometry, observation_date, size)
    cached_ok = cache_set_binary(key, image, CACHE_TTL_SECONDS)
    return image, "MISS" if cached_ok else "BYPASS"
