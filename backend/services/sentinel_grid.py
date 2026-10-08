"""Metric request grid shared by every Sentinel Hub Statistical API request (TASK_246).

Why this module exists
----------------------
Sentinel Hub reads ``resx``/``resy`` in the units of the request CRS. Field
geometries are stored in EPSG:4326, whose units are degrees, so a request that
sent EPSG:4326 together with ``resx = 10`` asked for a 10-degree grid: every
agricultural parcel collapsed to a single output pixel (TASK_245, blocker B7).

The fix is to keep the canonical EPSG:4326 polygon as the source of truth and to
send the provider the *same polygon* re-projected into the WGS 84 / UTM zone
that contains it, together with a resolution in metres. Nothing here replaces a
field with its centroid, its bounding box or any representative point: the
whole polygon, vertex for vertex, is transformed.

Both the NDVI request (``services.satellite``) and the supporting-index request
(``services.satellite_collection``) build their bounds and grid here, so the two
products always describe the same field footprint; only their resolutions
differ (10 m and 20 m, matching the native band resolutions they use).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry

#: Canonical CRS of persisted field geometries.
SOURCE_EPSG = 4326
#: Resolution of NDVI statistics. B04 and B08 are native 10 m bands.
NDVI_RESOLUTION_M = 10
#: Resolution of SAVI/EVI/NDMI/NDRE statistics. B05, B8A and B11 are native
#: 20 m bands, so 20 m is the finest grid on which all four are defined.
SUPPORTING_INDEX_RESOLUTION_M = 20
#: Accepted request resolutions, in metres. Anything else is a programming error.
ALLOWED_RESOLUTIONS_M = frozenset({NDVI_RESOLUTION_M, SUPPORTING_INDEX_RESOLUTION_M})
#: Latitude band in which the UTM projection is defined.
UTM_MIN_LATITUDE = -80.0
UTM_MAX_LATITUDE = 84.0

_EPSG_URI = "http://www.opengis.net/def/crs/EPSG/0/{code}"
_POLYGONAL = frozenset({"Polygon", "MultiPolygon"})


class SentinelGridError(ValueError):
    """The field geometry cannot be turned into a trustworthy metric request grid."""


def crs_uri(epsg: int) -> str:
    """The OGC CRS URI form Sentinel Hub expects, e.g. ``.../EPSG/0/32641``."""
    return _EPSG_URI.format(code=int(epsg))


def utm_epsg_for_lon_lat(lon: float, lat: float) -> int:
    """Return the WGS 84 / UTM EPSG code of the standard 6-degree zone at a point.

    Zone ``n`` covers ``[-180 + 6(n-1), -180 + 6n)`` degrees of longitude; a point
    exactly on a boundary belongs to the zone east of it, and 180 belongs to zone
    60. North of the equator the code is 326nn, south of it 327nn. The regional
    Norway/Svalbard exceptions are not applied: the standard zone is still a
    metric CRS there, only with slightly more scale distortion.
    """
    try:
        lon_value, lat_value = float(lon), float(lat)
    except (TypeError, ValueError):
        raise SentinelGridError("centroid coordinates are not numeric") from None
    if not (math.isfinite(lon_value) and math.isfinite(lat_value)):
        raise SentinelGridError("centroid coordinates are not finite")
    if not -180.0 <= lon_value <= 180.0:
        raise SentinelGridError("longitude is outside [-180, 180]; the geometry is not EPSG:4326")
    if not UTM_MIN_LATITUDE <= lat_value <= UTM_MAX_LATITUDE:
        raise SentinelGridError("latitude is outside the UTM band [-80, 84]")
    zone = min(int((lon_value + 180.0) // 6.0) + 1, 60)
    return (32600 if lat_value >= 0.0 else 32700) + zone


@dataclass(frozen=True, slots=True)
class MetricRequestGrid:
    """One field polygon, re-projected for a metric Statistical API grid."""

    epsg: int
    resolution_m: int
    geometry: dict[str, Any]
    area_m2: float
    source_vertex_count: int

    @property
    def crs(self) -> str:
        return crs_uri(self.epsg)

    @property
    def expected_field_pixels(self) -> float:
        """Polygon area divided by one pixel's area (a geometric estimate only)."""
        return self.area_m2 / float(self.resolution_m * self.resolution_m)

    def bounds(self) -> dict[str, Any]:
        """The ``input.bounds`` block of a Statistical API request."""
        return {"geometry": self.geometry, "properties": {"crs": self.crs}}

    def resolution(self) -> dict[str, int]:
        """The ``resx``/``resy`` pair of a Statistical API aggregation, in metres."""
        return {"resx": self.resolution_m, "resy": self.resolution_m}

    def sanitized_metadata(self) -> dict[str, Any]:
        """Request-grid facts safe for evidence (no coordinates)."""
        return {
            "crs": self.crs,
            "epsg": self.epsg,
            "resolution_m": self.resolution_m,
            "resolution_units": "metre",
            "geometry_type": self.geometry.get("type"),
            "area_m2": round(self.area_m2, 1),
            "expected_field_pixels": round(self.expected_field_pixels, 1),
            "source_vertex_count": self.source_vertex_count,
        }


def _vertex_count(geometry: BaseGeometry) -> int:
    polygons = getattr(geometry, "geoms", (geometry,))
    return sum(
        len(polygon.exterior.coords) + sum(len(ring.coords) for ring in polygon.interiors)
        for polygon in polygons
    )


def _as_json_geometry(value: Any) -> Any:
    """``shapely.mapping`` returns tuples; JSON payloads and fingerprints want lists."""
    if isinstance(value, dict):
        return {key: _as_json_geometry(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_json_geometry(item) for item in value]
    return value


def metric_request_grid(geometry: Any, resolution_m: int) -> MetricRequestGrid:
    """Re-project one EPSG:4326 field polygon onto its UTM zone at a metric resolution.

    ``geometry`` is a GeoJSON mapping or a Shapely geometry in EPSG:4326
    (longitude, latitude). Only valid, non-empty Polygon/MultiPolygon fields are
    accepted; there is deliberately no fallback to an EPSG:4326 request.
    """
    if resolution_m not in ALLOWED_RESOLUTIONS_M or isinstance(resolution_m, bool):
        raise SentinelGridError(f"unsupported request resolution {resolution_m!r} m")
    try:
        source = geometry if isinstance(geometry, BaseGeometry) else shape(geometry)
    except Exception:
        raise SentinelGridError("field geometry is not valid GeoJSON") from None
    if source.is_empty or source.geom_type not in _POLYGONAL:
        raise SentinelGridError("field geometry must be a non-empty Polygon or MultiPolygon")
    if not source.is_valid:
        raise SentinelGridError("field geometry is not a valid polygon")
    west, south, east, north = source.bounds
    if not (-180.0 <= west <= east <= 180.0 and -90.0 <= south <= north <= 90.0):
        raise SentinelGridError("field geometry coordinates are not EPSG:4326 longitude/latitude")
    centroid = source.centroid
    epsg = utm_epsg_for_lon_lat(centroid.x, centroid.y)
    transformer = Transformer.from_crs(SOURCE_EPSG, epsg, always_xy=True)
    projected = transform_geometry(transformer.transform, source)
    if projected.is_empty or not projected.is_valid or not math.isfinite(projected.area) or projected.area <= 0:
        raise SentinelGridError("re-projected field geometry is empty or invalid")
    return MetricRequestGrid(
        epsg=epsg,
        resolution_m=int(resolution_m),
        geometry=_as_json_geometry(mapping(projected)),
        area_m2=float(projected.area),
        source_vertex_count=_vertex_count(source),
    )
