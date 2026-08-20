"""Tenant-bound, field-clipped Sentinel-2 pixel NDVI workspace artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import base64
import hashlib
import hmac
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Iterable

import httpx
import numpy as np
from fastapi import HTTPException
from rasterio.crs import CRS
from rasterio.features import geometry_mask
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds, rowcol
from rasterio.errors import NotGeoreferencedWarning
import warnings
from shapely.geometry import Point, shape
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.dependencies import ALLOWED_ROLES, is_tenant_role, normalize_role
from config import settings
from models.monitoring import User
from services.ndvi_raster import (
    RasterServiceUnavailable,
    RasterUpstreamInvalid,
    RasterUpstreamTimeout,
)
from services.raster_observations import local_today, validate_bbox
from services.satellite import (
    NDVI_DENOMINATOR_EPSILON,
    NDVI_INVALID_SCL_CLASSES,
    NDVI_MASK_CONTRACT_ID,
    SENTINEL_CRS,
    SENTINEL_DATASET,
    SENTINEL_MAX_CLOUD_COVERAGE,
    get_satellite_service,
)


SCHEMA_VERSION = "program_r3_pixel_ndvi_v1"
IMPLEMENTATION_VERSION = "pixel-ndvi-raster-v1"
PALETTE_VERSION = "agricultural-cvd-v1"
MASK_VERSION = f"{NDVI_MASK_CONTRACT_ID}-local-clip-v1"
SOURCE_RESOLUTION_METERS = 10
NDVI_EPSILON = float(NDVI_DENOMINATOR_EPSILON)
MAX_DIMENSION = 2048
MAX_PIXEL_COUNT = 4_194_304
MAX_RESPONSE_BYTES = 12 * 1024 * 1024
MAX_PROVIDER_BYTES = 64 * 1024 * 1024
MAX_SCENE_DAYS = 365
MAX_SCENE_COUNT = 50
DEFAULT_SCENE_DAYS = 180
STALE_AFTER_DAYS = 14
KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")

LEGEND = (
    {"from": -1.0, "to": 0.0, "color": "#5B4A9A", "label": "Вода, тень или поверхность без растительности"},
    {"from": 0.0, "to": 0.2, "color": "#B7793C", "label": "Почва или очень слабая растительность"},
    {"from": 0.2, "to": 0.4, "color": "#D5BE62", "label": "Слабая растительность"},
    {"from": 0.4, "to": 0.6, "color": "#8DBB68", "label": "Умеренная растительность"},
    {"from": 0.6, "to": 0.8, "color": "#3B8D66", "label": "Сильная растительность"},
    {"from": 0.8, "to": 1.0, "color": "#075A49", "label": "Очень густая растительность"},
)
NO_DATA_LEGEND = {"color": "transparent", "label": "Нет данных / облако"}
HISTOGRAM_EDGES = np.asarray([-1.0, 0.0, 0.2, 0.4, 0.6, 0.8, 1.000001], dtype=np.float32)
PALETTE_RGB = np.asarray(
    [[91, 74, 154], [183, 121, 60], [213, 190, 98], [141, 187, 104], [59, 141, 102], [7, 90, 73]],
    dtype=np.uint8,
)
INVALID_SCL_JS = json.dumps(list(NDVI_INVALID_SCL_CLASSES), separators=(",", ":"))
WGS84_WKT = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433],'
    'AXIS["Latitude",NORTH],AXIS["Longitude",EAST]]'
)

ENCODED_EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B08", "SCL", "dataMask"] }],
    output: { bands: 4, sampleType: "UINT8" }
  };
}
function evaluatePixel(sample) {
  let denominator = sample.B08 + sample.B04;
  let valid = sample.dataMask === 1 && !__INVALID_SCL__.includes(sample.SCL)
    && Math.abs(denominator) > __EPSILON__;
  if (!valid) return [0, 0, 0, 0];
  let ndvi = Math.max(-1.0, Math.min(1.0, (sample.B08 - sample.B04) / denominator));
  let encoded = Math.round((ndvi + 1.0) * 32767.5);
  return [Math.floor(encoded / 256), encoded % 256, 0, 255];
}""".replace("__INVALID_SCL__", INVALID_SCL_JS).replace(
    "__EPSILON__", str(NDVI_DENOMINATOR_EPSILON)
)


@dataclass(frozen=True, slots=True)
class FieldScope:
    field_id: int
    enterprise_id: int
    geometry: dict[str, Any]
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class SceneRow:
    field_id: int
    enterprise_id: int
    provider_scene_id: str
    acquisition_time: datetime
    cloud_cover_pct: float | None
    valid_pixels_pct: float | None
    satellite: str
    geometry: dict[str, Any]
    bbox: tuple[float, float, float, float]

    @property
    def observation_date(self) -> date:
        return self.acquisition_time.date()


@dataclass(slots=True)
class PixelArtifact:
    png: bytes
    ndvi: np.ndarray
    valid: np.ndarray
    metadata: dict[str, Any]
    cache_state: str


def compute_ndvi(
    red: np.ndarray,
    nir: np.ndarray,
    scl: np.ndarray,
    data_mask: np.ndarray,
    inside_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute finite NDVI and the canonical Sentinel-2 L2A validity mask."""
    red_f = np.asarray(red, dtype=np.float32)
    nir_f = np.asarray(nir, dtype=np.float32)
    scl_a = np.asarray(scl)
    data_a = np.asarray(data_mask)
    if not (red_f.shape == nir_f.shape == scl_a.shape == data_a.shape):
        raise ValueError("Raster bands must have identical shapes")
    denominator = nir_f + red_f
    finite = np.isfinite(red_f) & np.isfinite(nir_f) & np.isfinite(denominator)
    valid = finite & (data_a == 1) & ~np.isin(scl_a, NDVI_INVALID_SCL_CLASSES)
    valid &= np.abs(denominator) > NDVI_EPSILON
    if inside_mask is not None:
        inside = np.asarray(inside_mask, dtype=bool)
        if inside.shape != red_f.shape:
            raise ValueError("Inside mask must match raster shape")
        valid &= inside
    values = np.full(red_f.shape, np.nan, dtype=np.float32)
    np.divide(nir_f - red_f, denominator, out=values, where=valid)
    valid &= np.isfinite(values) & (values >= -1.0) & (values <= 1.0)
    values[~valid] = np.nan
    return values, valid


def classify_ndvi(value: float | None) -> dict[str, Any] | None:
    if value is None or not math.isfinite(value):
        return None
    for index, item in enumerate(LEGEND):
        upper_inclusive = index == len(LEGEND) - 1
        if value >= item["from"] and (value < item["to"] or upper_inclusive and value <= item["to"]):
            return {**item, "code": f"ndvi_{index}"}
    return None


def summarize_ndvi(ndvi: np.ndarray, valid: np.ndarray) -> dict[str, Any]:
    values = np.asarray(ndvi, dtype=np.float32)[np.asarray(valid, dtype=bool)]
    total = int(np.asarray(valid).size)
    valid_count = int(values.size)
    no_data_count = total - valid_count
    histogram_counts, _ = np.histogram(values, bins=HISTOGRAM_EDGES) if valid_count else (
        np.zeros(len(HISTOGRAM_EDGES) - 1, dtype=np.int64), HISTOGRAM_EDGES
    )
    histogram = [
        {
            "from": float(LEGEND[index]["from"]),
            "to": float(LEGEND[index]["to"]),
            "label": LEGEND[index]["label"],
            "color": LEGEND[index]["color"],
            "count": int(histogram_counts[index]),
        }
        for index in range(len(LEGEND))
    ]
    summary: dict[str, Any] = {
        "source": "pixel_raster",
        "valid_pixel_count": valid_count,
        "valid_pixel_pct": round(valid_count / total * 100, 2) if total else 0.0,
        "no_data_pixel_count": no_data_count,
        "no_data_pixel_pct": round(no_data_count / total * 100, 2) if total else 0.0,
        "histogram": histogram,
        "min": None,
        "max": None,
        "mean": None,
        "median": None,
        "p10": None,
        "p90": None,
    }
    if valid_count:
        summary.update(
            min=round(float(np.min(values)), 4),
            max=round(float(np.max(values)), 4),
            mean=round(float(np.mean(values)), 4),
            median=round(float(np.median(values)), 4),
            p10=round(float(np.percentile(values, 10)), 4),
            p90=round(float(np.percentile(values, 90)), 4),
        )
    return summary


def render_palette_png(ndvi: np.ndarray, valid: np.ndarray, transform, crs: CRS) -> bytes:
    values = np.asarray(ndvi, dtype=np.float32)
    mask = np.asarray(valid, dtype=bool)
    height, width = values.shape
    rgba = np.zeros((4, height, width), dtype=np.uint8)
    clipped = np.clip(np.nan_to_num(values, nan=-1.0), -1.0, 1.0)
    bins = np.digitize(clipped, HISTOGRAM_EDGES[1:-1], right=False)
    for index, color in enumerate(PALETTE_RGB):
        selected = mask & (bins == index)
        rgba[0, selected] = color[0]
        rgba[1, selected] = color[1]
        rgba[2, selected] = color[2]
    rgba[3, mask] = 255
    with MemoryFile() as memory:
        with memory.open(
            driver="PNG", width=width, height=height, count=4, dtype="uint8",
            transform=transform, crs=crs,
        ) as dataset:
            dataset.write(rgba)
        payload = memory.read()
    if not payload or len(payload) > MAX_RESPONSE_BYTES:
        raise RasterUpstreamInvalid("rendered_png_size_invalid")
    return payload


def _scene_secret() -> bytes:
    value = str(settings.secret_key or "")
    if len(value) < 16:
        raise RasterServiceUnavailable("Pixel NDVI signing is unavailable")
    return value.encode("utf-8")


def scene_id_for(row: SceneRow) -> str:
    claims = {
        "a": row.acquisition_time.isoformat().replace("+00:00", "Z"),
        "c": row.cloud_cover_pct,
        "f": row.field_id,
        "g": geometry_hash(row.geometry),
        "i": row.provider_scene_id,
        "t": row.enterprise_id,
        "v": 1,
    }
    payload = base64.urlsafe_b64encode(
        json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=")
    signature = base64.urlsafe_b64encode(
        hmac.new(_scene_secret(), payload, hashlib.sha256).digest()
    ).rstrip(b"=")
    return f"{payload.decode('ascii')}.{signature.decode('ascii')}"


def geometry_hash(geometry: dict[str, Any]) -> str:
    canonical = json.dumps(geometry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def artifact_cache_key(row: SceneRow, *, resolution: int = SOURCE_RESOLUTION_METERS) -> str:
    components = {
        "tenant": row.enterprise_id,
        "field": row.field_id,
        "geometry": geometry_hash(row.geometry),
        "scene": scene_id_for(row),
        "index": "ndvi",
        "resolution": resolution,
        "mask": MASK_VERSION,
        "palette": PALETTE_VERSION,
        "implementation": IMPLEMENTATION_VERSION,
    }
    return hashlib.sha256(
        json.dumps(components, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _tenant_sql(current_user: User) -> tuple[str, dict[str, Any]]:
    role = normalize_role(current_user)
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Unknown role")
    if not is_tenant_role(role):
        return "", {}
    if current_user.enterprise_id is None:
        raise HTTPException(status_code=403, detail="User has no enterprise_id")
    return " AND f.enterprise_id = :enterprise_id", {"enterprise_id": current_user.enterprise_id}


def _field_from_mapping(row: Any) -> FieldScope:
    geometry = row.geometry if isinstance(row.geometry, dict) else json.loads(row.geometry)
    bbox = tuple(validate_bbox((row.west, row.south, row.east, row.north)))
    return FieldScope(
        field_id=int(row.field_id), enterprise_id=int(row.enterprise_id), geometry=geometry, bbox=bbox,
    )


def get_field_scope(db: Session, field_id: int, current_user: User) -> FieldScope:
    tenant_clause, tenant_params = _tenant_sql(current_user)
    row = db.execute(
        text(
            f"""
            SELECT f.id AS field_id, f.enterprise_id AS enterprise_id,
                   ST_AsGeoJSON(f.geometry)::json AS geometry,
                   ST_XMin(Box2D(f.geometry)) AS west,
                   ST_YMin(Box2D(f.geometry)) AS south,
                   ST_XMax(Box2D(f.geometry)) AS east,
                   ST_YMax(Box2D(f.geometry)) AS north
            FROM fields f
            WHERE f.id = :field_id{tenant_clause}
            """
        ),
        {"field_id": field_id, **tenant_params},
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Field not found")
    try:
        return _field_from_mapping(row)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=502, detail="Field geometry is unavailable") from None


def _catalog_payload(field: FieldScope, date_from: date, date_to: date, limit: int) -> dict[str, Any]:
    return {
        "collections": [SENTINEL_DATASET],
        "datetime": f"{date_from.isoformat()}T00:00:00Z/{date_to.isoformat()}T23:59:59Z",
        "intersects": field.geometry,
        "limit": limit,
    }


def _parse_catalog_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing catalog datetime")
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("catalog datetime must be UTC")
    return parsed.astimezone(timezone.utc)


def list_scene_rows(
    db: Session,
    field_id: int,
    current_user: User,
    date_from: date,
    date_to: date,
    limit: int,
) -> list[SceneRow]:
    if date_to < date_from or (date_to - date_from).days > MAX_SCENE_DAYS:
        raise HTTPException(status_code=422, detail="Unsupported scene date range")
    if not 1 <= limit <= MAX_SCENE_COUNT:
        raise HTTPException(status_code=422, detail="Unsupported scene result limit")
    field = get_field_scope(db, field_id, current_user)
    if settings.sentinel_hub_provider != "cdse":
        raise RasterServiceUnavailable("CDSE provider is required")
    service = get_satellite_service()
    if getattr(service, "is_mock", False) or not hasattr(service, "get_catalog_response"):
        raise RasterServiceUnavailable("Real CDSE catalog is unavailable")
    try:
        response = service.get_catalog_response(_catalog_payload(field, date_from, date_to, limit))
    except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout):
        raise RasterUpstreamTimeout("Satellite catalog timeout") from None
    except (httpx.HTTPError, OSError, ValueError):
        raise RasterServiceUnavailable("Satellite catalog unavailable") from None
    if response.status_code in {408, 504}:
        raise RasterUpstreamTimeout("Satellite catalog timeout")
    if response.status_code == 429 or response.status_code >= 500:
        raise RasterServiceUnavailable("Satellite catalog unavailable")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if (
        response.status_code != 200
        or content_type not in {"application/json", "application/geo+json"}
        or len(response.content) > 2 * 1024 * 1024
    ):
        raise RasterUpstreamInvalid("invalid_catalog_response")
    try:
        document = response.json()
        features = document["features"]
        if not isinstance(features, list) or len(features) > limit:
            raise ValueError("invalid features")
        scenes: list[SceneRow] = []
        for feature in features:
            provider_id = feature.get("id")
            properties = feature.get("properties")
            if not isinstance(provider_id, str) or not 1 <= len(provider_id) <= 256 or not isinstance(properties, dict):
                continue
            acquired = _parse_catalog_time(properties.get("datetime"))
            if acquired.date() < date_from or acquired.date() > date_to:
                continue
            cloud_raw = properties.get("eo:cloud_cover")
            cloud = float(cloud_raw) if cloud_raw is not None else None
            if cloud is not None and (not math.isfinite(cloud) or not 0 <= cloud <= 100):
                cloud = None
            scenes.append(SceneRow(
                field_id=field.field_id, enterprise_id=field.enterprise_id,
                provider_scene_id=provider_id, acquisition_time=acquired,
                cloud_cover_pct=cloud, valid_pixels_pct=None,
                satellite="Sentinel-2", geometry=field.geometry, bbox=field.bbox,
            ))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise RasterUpstreamInvalid("invalid_catalog_response") from None
    scenes.sort(key=lambda item: item.acquisition_time, reverse=True)
    return scenes[:limit]


def _decode_scene_id(scene_id: str) -> dict[str, Any]:
    if not isinstance(scene_id, str) or not 80 <= len(scene_id) <= 768 or scene_id.count(".") != 1:
        raise HTTPException(status_code=404, detail="Scene not found")
    payload_text, signature_text = scene_id.split(".", 1)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", payload_text) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", signature_text):
        raise HTTPException(status_code=404, detail="Scene not found")
    payload = payload_text.encode("ascii")
    expected = base64.urlsafe_b64encode(hmac.new(_scene_secret(), payload, hashlib.sha256).digest()).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(expected, signature_text):
        raise HTTPException(status_code=404, detail="Scene not found")
    try:
        decoded = base64.urlsafe_b64decode(payload + b"=" * (-len(payload) % 4))
        claims = json.loads(decoded)
        if claims.get("v") != 1:
            raise ValueError("version")
        return claims
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=404, detail="Scene not found") from None


def resolve_scene_row(db: Session, field_id: int, current_user: User, scene_id: str) -> SceneRow:
    field = get_field_scope(db, field_id, current_user)
    claims = _decode_scene_id(scene_id)
    try:
        if (
            int(claims["f"]) != field.field_id
            or int(claims["t"]) != field.enterprise_id
            or not hmac.compare_digest(str(claims["g"]), geometry_hash(field.geometry))
        ):
            raise ValueError("scope")
        provider_id = str(claims["i"])
        acquired = _parse_catalog_time(claims["a"])
        cloud_raw = claims.get("c")
        cloud = float(cloud_raw) if cloud_raw is not None else None
        if cloud is not None and (not math.isfinite(cloud) or not 0 <= cloud <= 100):
            raise ValueError("cloud")
        if not 1 <= len(provider_id) <= 256:
            raise ValueError("provider id")
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Scene not found") from None
    return SceneRow(
        field_id=field.field_id, enterprise_id=field.enterprise_id,
        provider_scene_id=provider_id, acquisition_time=acquired,
        cloud_cover_pct=cloud, valid_pixels_pct=None, satellite="Sentinel-2",
        geometry=field.geometry, bbox=field.bbox,
    )


def scene_response(row: SceneRow, *, default: bool = False) -> dict[str, Any]:
    age_days = max(0, (local_today() - row.acquisition_time.date()).days)
    return {
        "scene_id": scene_id_for(row),
        "field_id": row.field_id,
        "acquired_at": row.acquisition_time,
        "provider": "cdse",
        "source": "Sentinel-2 L2A",
        "cloud_cover_pct": row.cloud_cover_pct,
        "valid_pixel_pct": row.valid_pixels_pct,
        "raster_available": True,
        "freshness": "fresh" if age_days <= STALE_AFTER_DAYS else "stale",
        "age_days": age_days,
        "is_default": default,
    }


def _dimensions(bbox: tuple[float, float, float, float], resolution: int) -> tuple[int, int, float]:
    west, south, east, north = bbox
    latitude = (south + north) / 2
    width_m = max(1.0, (east - west) * 111_320.0 * math.cos(math.radians(latitude)))
    height_m = max(1.0, (north - south) * 110_574.0)
    width = max(1, min(MAX_DIMENSION, math.ceil(width_m / resolution)))
    height = max(1, min(MAX_DIMENSION, math.ceil(height_m / resolution)))
    if width * height > MAX_PIXEL_COUNT:
        scale = math.sqrt(MAX_PIXEL_COUNT / (width * height))
        width = max(1, int(width * scale))
        height = max(1, int(height * scale))
    effective = max(width_m / width, height_m / height)
    return width, height, round(effective, 2)


def build_encoded_process_payload(row: SceneRow, width: int, height: int) -> dict[str, Any]:
    start = row.acquisition_time - timedelta(seconds=1)
    end = row.acquisition_time + timedelta(seconds=1)
    return {
        "input": {
            "bounds": {"geometry": row.geometry, "properties": {"crs": SENTINEL_CRS}},
            "data": [{
                "type": SENTINEL_DATASET,
                "dataFilter": {
                    "timeRange": {
                        "from": start.isoformat().replace("+00:00", "Z"),
                        "to": end.isoformat().replace("+00:00", "Z"),
                    },
                    "maxCloudCoverage": SENTINEL_MAX_CLOUD_COVERAGE,
                    "mosaickingOrder": "leastCC",
                },
                "processing": {"harmonizeValues": True},
            }],
        },
        "output": {
            "width": width, "height": height,
            "responses": [{"identifier": "default", "format": {"type": "image/png"}}],
        },
        "evalscript": ENCODED_EVALSCRIPT,
    }


def _request_encoded_png(row: SceneRow, width: int, height: int) -> tuple[bytes, dict[str, Any]]:
    if settings.sentinel_hub_provider != "cdse":
        raise RasterServiceUnavailable("CDSE provider is required")
    service = get_satellite_service()
    if getattr(service, "is_mock", False) or not hasattr(service, "get_process_response"):
        raise RasterServiceUnavailable("Real CDSE service is unavailable")
    started = time.monotonic()
    try:
        response = service.get_process_response(
            build_encoded_process_payload(row, width, height), accept="image/png"
        )
    except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout):
        raise RasterUpstreamTimeout("Satellite service timeout") from None
    except (httpx.HTTPError, OSError, ValueError):
        raise RasterServiceUnavailable("Satellite service unavailable") from None
    if time.monotonic() - started > 60:
        raise RasterUpstreamTimeout("Satellite service timeout")
    if response.status_code in {408, 504}:
        raise RasterUpstreamTimeout("Satellite service timeout")
    if response.status_code == 429 or response.status_code >= 500:
        raise RasterServiceUnavailable("Satellite service unavailable")
    if response.status_code != 200:
        raise RasterUpstreamInvalid("provider_rejected_request")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "image/png":
        raise RasterUpstreamInvalid("invalid_content_type")
    payload = bytes(response.content)
    if not payload or len(payload) > MAX_PROVIDER_BYTES:
        raise RasterUpstreamInvalid("invalid_provider_payload_size")
    return payload, {
        "http_status_class": "2xx",
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
    }


def _decode_and_clip(
    payload: bytes,
    row: SceneRow,
    width: int,
    height: int,
) -> tuple[np.ndarray, np.ndarray, Any, CRS]:
    target_crs = CRS.from_wkt(WGS84_WKT)
    target_transform = from_bounds(*row.bbox, width, height)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with MemoryFile(payload) as memory:
                with memory.open() as dataset:
                    if (
                        dataset.driver != "PNG" or dataset.count != 4
                        or dataset.width != width or dataset.height != height
                        or tuple(dataset.dtypes) != ("uint8",) * 4
                    ):
                        raise RasterUpstreamInvalid("unexpected_encoded_raster_shape")
                    high = dataset.read(1).astype(np.uint16)
                    low = dataset.read(2).astype(np.uint16)
                    alpha = dataset.read(4)
                    encoded = high * 256 + low
                    ndvi = encoded.astype(np.float32) / np.float32(32767.5) - np.float32(1.0)
                    valid_band = alpha
    except RasterUpstreamInvalid:
        raise
    except Exception:
        raise RasterUpstreamInvalid("invalid_float_raster") from None
    inside = geometry_mask(
        [row.geometry], out_shape=(height, width), transform=target_transform,
        invert=True, all_touched=False,
    )
    valid = inside & (valid_band > 0) & np.isfinite(ndvi) & (ndvi >= -1.0) & (ndvi <= 1.0)
    ndvi = ndvi.astype(np.float32, copy=False)
    ndvi[~valid] = np.nan
    if not np.any(valid):
        raise RasterUpstreamInvalid("no_valid_pixels")
    return ndvi, valid, target_transform, target_crs


def _cache_root() -> Path | None:
    raw = str(settings.pixel_ndvi_cache_directory or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise RasterServiceUnavailable("Pixel NDVI cache path must be absolute")
    try:
        path.mkdir(parents=True, exist_ok=True)
        resolved = path.resolve(strict=True)
    except OSError:
        raise RasterServiceUnavailable("Pixel NDVI cache is unavailable") from None
    if not resolved.is_dir():
        raise RasterServiceUnavailable("Pixel NDVI cache is unavailable")
    return resolved


def _cache_paths(root: Path, key: str) -> tuple[Path, Path, Path]:
    if not KEY_PATTERN.fullmatch(key):
        raise RasterServiceUnavailable("Invalid Pixel NDVI cache identity")
    directory = (root / key[:2]).resolve()
    if root != directory and root not in directory.parents:
        raise RasterServiceUnavailable("Invalid Pixel NDVI cache path")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{key}.png", directory / f"{key}.npz", directory / f"{key}.json"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_cache(root: Path, key: str) -> PixelArtifact | None:
    png_path, array_path, manifest_path = _cache_paths(root, key)
    try:
        manifest_bytes = manifest_path.read_bytes()
        if len(manifest_bytes) > 256 * 1024:
            return None
        manifest = json.loads(manifest_bytes)
        png = png_path.read_bytes()
        array_payload = array_path.read_bytes()
        if manifest.get("cache_key") != key:
            return None
        if _sha256(png) != manifest.get("png_sha256") or _sha256(array_payload) != manifest.get("array_sha256"):
            return None
        if len(png) > MAX_RESPONSE_BYTES or len(array_payload) > MAX_PROVIDER_BYTES:
            return None
        with np.load(io.BytesIO(array_payload), allow_pickle=False) as stored:
            ndvi = stored["ndvi"].astype(np.float32, copy=False)
            valid = stored["valid"].astype(bool, copy=False)
        expected = (int(manifest["height"]), int(manifest["width"]))
        if ndvi.shape != expected or valid.shape != expected:
            return None
        return PixelArtifact(png=png, ndvi=ndvi, valid=valid, metadata=manifest, cache_state="HIT")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _atomic_write(path: Path, payload: bytes) -> None:
    handle = tempfile.NamedTemporaryFile(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent, delete=False)
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def _prune_cache(root: Path) -> None:
    max_entries = max(1, min(int(settings.pixel_ndvi_cache_max_entries), 10_000))
    max_bytes = max(MAX_RESPONSE_BYTES, int(settings.pixel_ndvi_cache_max_bytes))
    max_age = max(1, min(int(settings.pixel_ndvi_cache_max_age_days), 365)) * 86400
    now = time.time()
    manifests = []
    for path in list(root.glob("[0-9a-f][0-9a-f]/*.json"))[:10_000]:
        try:
            stat = path.stat()
            key = path.stem
            png_path, array_path, _ = _cache_paths(root, key)
            size = stat.st_size + png_path.stat().st_size + array_path.stat().st_size
            manifests.append((stat.st_mtime, size, key, path, png_path, array_path))
        except (OSError, RasterServiceUnavailable):
            continue
    manifests.sort(reverse=True)
    total = sum(item[1] for item in manifests)
    for index, item in enumerate(manifests):
        modified, size, _key, manifest_path, png_path, array_path = item
        expired = now - modified > max_age
        over_limit = index >= max_entries or total > max_bytes
        if not expired and not over_limit:
            continue
        for candidate in (manifest_path, png_path, array_path):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
        total -= size


def _store_cache(root: Path, key: str, artifact: PixelArtifact) -> None:
    png_path, array_path, manifest_path = _cache_paths(root, key)
    buffer = io.BytesIO()
    np.savez_compressed(buffer, ndvi=artifact.ndvi.astype(np.float32), valid=artifact.valid.astype(np.uint8))
    array_payload = buffer.getvalue()
    manifest = {
        **artifact.metadata,
        "cache_key": key,
        "png_sha256": _sha256(artifact.png),
        "array_sha256": _sha256(array_payload),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write(png_path, artifact.png)
    _atomic_write(array_path, array_payload)
    _atomic_write(manifest_path, json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    artifact.metadata = manifest
    _prune_cache(root)


def get_artifact(row: SceneRow) -> PixelArtifact:
    key = artifact_cache_key(row)
    root = _cache_root()
    if root is not None:
        cached = _load_cache(root, key)
        if cached is not None:
            return cached
    width, height, effective_resolution = _dimensions(row.bbox, SOURCE_RESOLUTION_METERS)
    payload, provider_diagnostic = _request_encoded_png(row, width, height)
    ndvi, valid, transform, crs = _decode_and_clip(payload, row, width, height)
    png = render_palette_png(ndvi, valid, transform, crs)
    summary = summarize_ndvi(ndvi, valid)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "implementation_version": IMPLEMENTATION_VERSION,
        "palette_version": PALETTE_VERSION,
        "mask_version": MASK_VERSION,
        "field_id": row.field_id,
        "enterprise_id": row.enterprise_id,
        "scene_id": scene_id_for(row),
        "acquisition_date": row.acquisition_time.isoformat().replace("+00:00", "Z"),
        "provider": "cdse",
        "source": "Sentinel-2 L2A",
        "bounds": list(row.bbox),
        "corners": [
            [row.bbox[0], row.bbox[3]], [row.bbox[2], row.bbox[3]],
            [row.bbox[2], row.bbox[1]], [row.bbox[0], row.bbox[1]],
        ],
        "width": width, "height": height,
        "source_resolution_m": SOURCE_RESOLUTION_METERS,
        "effective_resolution_m": effective_resolution,
        "response_bytes": len(png),
        "provider_diagnostic": provider_diagnostic,
        "summary": summary,
    }
    artifact = PixelArtifact(png=png, ndvi=ndvi, valid=valid, metadata=metadata, cache_state="MISS" if root else "BYPASS")
    if root is not None:
        try:
            _store_cache(root, key, artifact)
        except OSError:
            artifact.cache_state = "BYPASS"
    return artifact


def validate_point(row: SceneRow, longitude: float, latitude: float) -> None:
    if not (math.isfinite(longitude) and math.isfinite(latitude)):
        raise HTTPException(status_code=422, detail="Invalid coordinate")
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise HTTPException(status_code=422, detail="Invalid coordinate")
    try:
        polygon = shape(row.geometry)
    except (TypeError, ValueError):
        raise HTTPException(status_code=502, detail="Field geometry is unavailable") from None
    if polygon.is_empty or not polygon.is_valid:
        raise HTTPException(status_code=502, detail="Field geometry is unavailable")
    if not polygon.covers(Point(longitude, latitude)):
        raise HTTPException(status_code=422, detail="Point is outside field")


def sample_artifact(artifact: PixelArtifact, longitude: float, latitude: float) -> tuple[float | None, dict[str, Any] | None]:
    bounds = artifact.metadata["bounds"]
    transform = from_bounds(*bounds, artifact.metadata["width"], artifact.metadata["height"])
    row_index, column_index = rowcol(transform, longitude, latitude)
    if not (0 <= row_index < artifact.ndvi.shape[0] and 0 <= column_index < artifact.ndvi.shape[1]):
        return None, None
    if not artifact.valid[row_index, column_index]:
        return None, None
    value = round(float(artifact.ndvi[row_index, column_index]), 4)
    return value, classify_ndvi(value)
