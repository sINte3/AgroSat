"""Secure, cached Sentinel-2 NDVI raster support for accepted observations."""

from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
import hashlib
import json
import struct
from typing import Any
import warnings
import zlib

import httpx
from rasterio.enums import ColorInterp
from rasterio.errors import NotGeoreferencedWarning, RasterioError
from rasterio.io import MemoryFile

from services.cache import cache_get_binary, cache_set_binary
from services.satellite import (
    NDVI_DENOMINATOR_EPSILON,
    NDVI_INVALID_SCL_CLASSES,
    NDVI_MASK_CONTRACT_ID,
    SENTINEL_CRS,
    SENTINEL_DATASET,
    SENTINEL_MAX_CLOUD_COVERAGE,
    format_utc_timestamp,
    get_satellite_service,
    parse_provider_utc_timestamp,
)
from services.satellite_safety import SatelliteConfigurationError
from services.raster_observations import validate_bbox

ALLOWED_SIZES = (256, 512, 768, 1024)
DEFAULT_SIZE = 512
PALETTE_VERSION = "ndvi-rgba-scl-v2"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_PNG_BYTES = 8 * 1024 * 1024
MAX_PIXEL_COUNT = max(ALLOWED_SIZES) * max(ALLOWED_SIZES)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_IHDR_TOTAL_BYTES = 33

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

_NDVI_INVALID_SCL_JS = json.dumps(list(NDVI_INVALID_SCL_CLASSES), separators=(",", ":"))

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
  let valid = sample.dataMask === 1 && !__INVALID_SCL__.includes(sample.SCL);
  if (!valid) return [0, 0, 0, 0];
  let ndvi = (sample.B08 - sample.B04) / (sample.B08 + sample.B04 + __EPSILON__);
  let rgb = ramp(ndvi);
  return [rgb[0], rgb[1], rgb[2], 255];
}""".replace("__INVALID_SCL__", _NDVI_INVALID_SCL_JS).replace(
    "__EPSILON__", NDVI_DENOMINATOR_EPSILON
)


class RasterServiceUnavailable(Exception):
    pass


class RasterUpstreamInvalid(Exception):
    def __init__(self, reason_code: str = "invalid_raster") -> None:
        super().__init__("Invalid satellite raster response")
        self.reason_code = reason_code


class RasterUpstreamTimeout(Exception):
    pass


class RasterContentValidationError(ValueError):
    """Sanitized, classified in-memory PNG validation failure."""

    def __init__(self, reason_code: str) -> None:
        super().__init__("Raster content validation failed")
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class RasterValidationResult:
    response_sha256: str
    byte_count: int
    width: int
    height: int
    decoded_format: str
    decoded_mode: str
    color_type: int
    total_pixel_count: int
    non_transparent_pixel_count: int
    visible_coverage_pct: float
    alpha_min: int
    alpha_max: int
    mask_contract_id: str

    def as_sanitized_dict(self) -> dict[str, Any]:
        return {
            "responseSha256": self.response_sha256,
            "byteCount": self.byte_count,
            "decodedFormat": self.decoded_format,
            "decodedWidth": self.width,
            "decodedHeight": self.height,
            "decodedMode": self.decoded_mode,
            "pngColorType": self.color_type,
            "totalPixelCount": self.total_pixel_count,
            "nonTransparentPixelCount": self.non_transparent_pixel_count,
            "visibleCoveragePct": self.visible_coverage_pct,
            "alphaMin": self.alpha_min,
            "alphaMax": self.alpha_max,
            "maskContractId": self.mask_contract_id,
            "contentValid": True,
            "rawRasterPersisted": False,
            "pixelArrayPersisted": False,
        }


@dataclass(frozen=True, slots=True)
class ValidatedRaster:
    payload: bytes
    validation: RasterValidationResult


def raster_cache_key(field_id: int, observation_date: date, size: int) -> str:
    return f"ndvi-raster:{PALETTE_VERSION}:{field_id}:{observation_date.isoformat()}:{size}"


def validate_size(size: int) -> int:
    if size not in ALLOWED_SIZES:
        raise ValueError("Unsupported raster size")
    return size


def _canonical_utc_bound(value: datetime | str) -> str:
    parsed = parse_provider_utc_timestamp(value) if isinstance(value, str) else value
    return format_utc_timestamp(parsed)


def build_process_payload_for_interval(
    geometry: dict[str, Any],
    interval_from_utc: datetime | str,
    interval_to_utc: datetime | str,
    size: int,
) -> dict[str, Any]:
    """Build a Process request for the exact selected Statistical interval."""
    validate_size(size)
    interval_from = _canonical_utc_bound(interval_from_utc)
    interval_to = _canonical_utc_bound(interval_to_utc)
    if parse_provider_utc_timestamp(interval_to) <= parse_provider_utc_timestamp(interval_from):
        raise ValueError("Process interval end must be after its start")
    return {
        "input": {
            "bounds": {"geometry": geometry, "properties": {"crs": SENTINEL_CRS}},
            "data": [{
                "type": SENTINEL_DATASET,
                "dataFilter": {
                    "timeRange": {"from": interval_from, "to": interval_to},
                    "maxCloudCoverage": SENTINEL_MAX_CLOUD_COVERAGE,
                },
                "processing": {"harmonizeValues": True},
            }],
        },
        "output": {"width": size, "height": size, "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
        "evalscript": EVALSCRIPT,
    }


def build_process_payload(
    geometry: dict[str, Any],
    observation_date: date,
    size: int,
) -> dict[str, Any]:
    """Compatibility path for an existing canonical date-only observation."""
    interval_from = datetime.combine(
        observation_date,
        datetime_time.min,
        tzinfo=timezone.utc,
    )
    return build_process_payload_for_interval(
        geometry,
        interval_from,
        interval_from + timedelta(days=1),
        size,
    )


def _preflight_png(
    payload: bytes,
    *,
    expected_width: int | None,
    expected_height: int | None,
    max_dimension: int,
) -> tuple[int, int, int]:
    if not isinstance(payload, bytes) or not payload:
        raise RasterContentValidationError("empty_payload")
    if len(payload) > MAX_PNG_BYTES:
        raise RasterContentValidationError("compressed_payload_too_large")
    if len(payload) < PNG_IHDR_TOTAL_BYTES or not payload.startswith(PNG_SIGNATURE):
        raise RasterContentValidationError("invalid_png_signature_or_header")
    ihdr_length = struct.unpack(">I", payload[8:12])[0]
    if ihdr_length != 13 or payload[12:16] != b"IHDR":
        raise RasterContentValidationError("invalid_png_ihdr")
    width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", payload[16:29]
    )
    if width <= 0 or height <= 0:
        raise RasterContentValidationError("invalid_dimensions")
    if width > max_dimension or height > max_dimension:
        raise RasterContentValidationError("dimensions_above_maximum")
    if width * height > MAX_PIXEL_COUNT:
        raise RasterContentValidationError("pixel_count_above_maximum")
    if width != height or width not in ALLOWED_SIZES:
        raise RasterContentValidationError("dimensions_not_allowlisted")
    if expected_width is not None and width != expected_width:
        raise RasterContentValidationError("dimension_mismatch")
    if expected_height is not None and height != expected_height:
        raise RasterContentValidationError("dimension_mismatch")
    if bit_depth != 8 or color_type != 6:
        raise RasterContentValidationError("unsupported_png_pixel_format")
    if compression != 0 or filter_method != 0:
        raise RasterContentValidationError("unsupported_png_encoding")
    if interlace != 0:
        raise RasterContentValidationError("interlaced_png_not_supported")
    _validate_png_chunk_structure(payload)
    return width, height, color_type


def _validate_png_chunk_structure(payload: bytes) -> None:
    """Boundedly verify PNG chunk framing/CRC before invoking the decoder."""
    offset = len(PNG_SIGNATURE)
    chunk_count = 0
    saw_ihdr = False
    saw_idat = False
    saw_iend = False
    while offset < len(payload):
        chunk_count += 1
        if chunk_count > 4096 or len(payload) - offset < 12:
            raise RasterContentValidationError("invalid_png_chunk_structure")
        length = struct.unpack(">I", payload[offset:offset + 4])[0]
        chunk_type = payload[offset + 4:offset + 8]
        chunk_end = offset + 12 + length
        if length > MAX_PNG_BYTES or chunk_end > len(payload):
            raise RasterContentValidationError("invalid_png_chunk_structure")
        chunk_data = payload[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", payload[offset + 8 + length:chunk_end])[0]
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise RasterContentValidationError("invalid_png_chunk_crc")
        if chunk_count == 1:
            if chunk_type != b"IHDR" or length != 13:
                raise RasterContentValidationError("invalid_png_ihdr")
            saw_ihdr = True
        elif chunk_type == b"IHDR":
            raise RasterContentValidationError("duplicate_png_ihdr")
        if chunk_type == b"IDAT":
            saw_idat = True
        if chunk_type == b"IEND":
            if length != 0 or chunk_end != len(payload):
                raise RasterContentValidationError("invalid_png_iend")
            saw_iend = True
            offset = chunk_end
            break
        offset = chunk_end
    if not (saw_ihdr and saw_idat and saw_iend) or offset != len(payload):
        raise RasterContentValidationError("invalid_png_chunk_structure")


def validate_raster_content(
    payload: bytes,
    *,
    expected_width: int | None = None,
    expected_height: int | None = None,
    max_dimension: int | None = None,
) -> RasterValidationResult:
    """Decode and validate a bounded RGBA PNG entirely in memory."""
    dimension_limit = max(ALLOWED_SIZES) if max_dimension is None else max_dimension
    if dimension_limit not in ALLOWED_SIZES:
        raise RasterContentValidationError("invalid_dimension_limit")
    width, height, color_type = _preflight_png(
        payload,
        expected_width=expected_width,
        expected_height=expected_height,
        max_dimension=dimension_limit,
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with MemoryFile(payload) as memory_file:
                with memory_file.open() as dataset:
                    if dataset.driver != "PNG":
                        raise RasterContentValidationError("decoded_format_not_png")
                    if dataset.width != width or dataset.height != height:
                        raise RasterContentValidationError("decoded_dimension_mismatch")
                    if dataset.count != 4 or tuple(dataset.dtypes) != ("uint8",) * 4:
                        raise RasterContentValidationError("decoded_mode_not_rgba_uint8")
                    if tuple(dataset.colorinterp) != (
                        ColorInterp.red,
                        ColorInterp.green,
                        ColorInterp.blue,
                        ColorInterp.alpha,
                    ):
                        raise RasterContentValidationError("decoded_alpha_contract_missing")
                    decoded = dataset.read()
    except RasterContentValidationError:
        raise
    except (RasterioError, OSError, ValueError, MemoryError):
        raise RasterContentValidationError("png_decode_failed") from None

    alpha = decoded[3]
    alpha_min = int(alpha.min())
    alpha_max = int(alpha.max())
    non_transparent = int((alpha > 0).sum())
    total_pixels = width * height
    del decoded, alpha
    if non_transparent <= 0:
        raise RasterContentValidationError("fully_transparent")
    return RasterValidationResult(
        response_sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
        width=width,
        height=height,
        decoded_format="PNG",
        decoded_mode="RGBA",
        color_type=color_type,
        total_pixel_count=total_pixels,
        non_transparent_pixel_count=non_transparent,
        visible_coverage_pct=round(non_transparent / total_pixels * 100, 6),
        alpha_min=alpha_min,
        alpha_max=alpha_max,
        mask_contract_id=NDVI_MASK_CONTRACT_ID,
    )


def validate_png(
    payload: bytes,
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> bool:
    """Compatibility boolean around the strict decoded-content validator."""
    try:
        validate_raster_content(
            payload,
            expected_width=expected_width,
            expected_height=expected_height,
            max_dimension=expected_width or max(ALLOWED_SIZES),
        )
        return True
    except RasterContentValidationError:
        return False


def request_process_png_for_interval(
    geometry: dict[str, Any],
    interval_from_utc: datetime | str,
    interval_to_utc: datetime | str,
    size: int,
) -> ValidatedRaster:
    """Request and strictly validate one exact-interval Process API raster."""
    validate_size(size)
    try:
        service = get_satellite_service()
        if getattr(service, "is_mock", False):
            raise RasterServiceUnavailable("Satellite service unavailable")
        response = service.get_process_png(
            build_process_payload_for_interval(
                geometry,
                interval_from_utc,
                interval_to_utc,
                size,
            )
        )
    except SatelliteConfigurationError as exc:
        raise RasterServiceUnavailable("Satellite service unavailable") from exc
    except httpx.TimeoutException as exc:
        raise RasterUpstreamTimeout("Satellite service timeout") from exc
    except httpx.HTTPError as exc:
        raise RasterServiceUnavailable("Satellite service unavailable") from exc
    if response.status_code != 200:
        raise RasterServiceUnavailable("Satellite service unavailable")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "image/png":
        raise RasterUpstreamInvalid("invalid_content_type")
    try:
        validation = validate_raster_content(
            response.content,
            expected_width=size,
            expected_height=size,
            max_dimension=size,
        )
    except RasterContentValidationError as error:
        raise RasterUpstreamInvalid(error.reason_code) from error
    return ValidatedRaster(payload=response.content, validation=validation)


def request_process_png(
    geometry: dict[str, Any],
    observation_date: date,
    size: int,
) -> bytes:
    interval_from = datetime.combine(
        observation_date,
        datetime_time.min,
        tzinfo=timezone.utc,
    )
    return request_process_png_for_interval(
        geometry,
        interval_from,
        interval_from + timedelta(days=1),
        size,
    ).payload


def get_raster_png(field_id: int, geometry: dict[str, Any], observation_date: date, size: int) -> tuple[bytes, str]:
    validate_size(size)
    key = raster_cache_key(field_id, observation_date, size)
    cached = cache_get_binary(key)
    if cached and validate_png(cached, size, size):
        return cached, "HIT"
    image = request_process_png(geometry, observation_date, size)
    cached_ok = cache_set_binary(key, image, CACHE_TTL_SECONDS)
    return image, "MISS" if cached_ok else "BYPASS"
