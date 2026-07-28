"""Deterministic, non-diagnostic pixel anomaly analysis."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import date
import hashlib
import json
import math
from statistics import median
from typing import Any, Iterable

from shapely.geometry import MultiPolygon, box, mapping
from shapely.ops import unary_union


ALGORITHM_VERSION = "paired_within_field_drop_v1"
MAX_SCENE_PIXELS = 4_000_000
MAX_ANOMALY_ZONES = 500


class AnomalyContractError(ValueError):
    """A scene or threshold violates the bounded analytical contract."""


@dataclass(frozen=True)
class AnomalyThresholds:
    within_field_delta: float = 0.12
    comparison_drop: float = 0.10
    minimum_connected_pixels: int = 4
    minimum_area_ha: float = 0.05
    maximum_cloud_cover_pct: float = 30.0
    minimum_valid_pixels_pct: float = 60.0
    minimum_eligible_pixels: int = 25
    minimum_separation_days: int = 5
    maximum_separation_days: int = 60

    def validate(self) -> None:
        if not 0 < self.within_field_delta <= 2:
            raise AnomalyContractError("within_field_delta must be in (0, 2]")
        if not 0 < self.comparison_drop <= 2:
            raise AnomalyContractError("comparison_drop must be in (0, 2]")
        if not 1 <= self.minimum_connected_pixels <= MAX_SCENE_PIXELS:
            raise AnomalyContractError("minimum_connected_pixels is out of range")
        if not 0 < self.minimum_area_ha <= 1_000_000:
            raise AnomalyContractError("minimum_area_ha is out of range")
        if not 0 <= self.maximum_cloud_cover_pct <= 100:
            raise AnomalyContractError("maximum_cloud_cover_pct is out of range")
        if not 0 <= self.minimum_valid_pixels_pct <= 100:
            raise AnomalyContractError("minimum_valid_pixels_pct is out of range")
        if not 1 <= self.minimum_eligible_pixels <= MAX_SCENE_PIXELS:
            raise AnomalyContractError("minimum_eligible_pixels is out of range")
        if not 1 <= self.minimum_separation_days <= 365:
            raise AnomalyContractError("minimum_separation_days is out of range")
        if not self.minimum_separation_days <= self.maximum_separation_days <= 365:
            raise AnomalyContractError("maximum_separation_days is out of range")

    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PixelScene:
    enterprise_id: int
    field_id: int
    index_code: str
    record_type: str
    record_id: int
    observed_at: date
    values: tuple[tuple[float, ...], ...]
    quality_mask: tuple[tuple[bool, ...], ...]
    field_mask: tuple[tuple[bool, ...], ...]
    bbox: tuple[float, float, float, float]
    cloud_cover_pct: float
    valid_pixels_pct: float
    provider: str
    provenance: dict[str, Any]

    @property
    def height(self) -> int:
        return len(self.values)

    @property
    def width(self) -> int:
        return len(self.values[0]) if self.values else 0


@dataclass(frozen=True)
class AnomalyZone:
    zone_key: str
    geometry: dict[str, Any]
    area_ha: float
    score: float
    severity: str
    confidence: float
    persistence_count: int
    classification: str
    pixel_count: int
    median_drop: float
    median_deficit: float


@dataclass(frozen=True)
class AnomalyRunResult:
    algorithm_version: str
    run_key: str
    threshold_hash: str
    status: str
    reason_codes: tuple[str, ...]
    current: PixelScene
    comparison: PixelScene | None
    eligible_pixels: int
    current_median: float | None
    quality_summary: dict[str, Any]
    zones: tuple[AnomalyZone, ...]


@dataclass(frozen=True)
class HistoricalZone:
    geometry: dict[str, Any]
    area_ha: float
    median_drop: float
    persistence_count: int


def _validate_matrix(
    name: str,
    matrix: tuple[tuple[Any, ...], ...],
    *,
    height: int | None = None,
    width: int | None = None,
) -> tuple[int, int]:
    if not isinstance(matrix, tuple) or not matrix:
        raise AnomalyContractError(f"{name} must be a non-empty tuple matrix")
    if not all(isinstance(row, tuple) and row for row in matrix):
        raise AnomalyContractError(f"{name} rows must be non-empty tuples")
    actual_height = len(matrix)
    actual_width = len(matrix[0])
    if any(len(row) != actual_width for row in matrix):
        raise AnomalyContractError(f"{name} must be rectangular")
    if height is not None and (actual_height, actual_width) != (height, width):
        raise AnomalyContractError(f"{name} shape does not match values")
    if actual_height * actual_width > MAX_SCENE_PIXELS:
        raise AnomalyContractError("scene exceeds maximum pixel count")
    return actual_height, actual_width


def validate_scene(scene: PixelScene) -> None:
    if scene.enterprise_id <= 0 or scene.field_id <= 0 or scene.record_id <= 0:
        raise AnomalyContractError("scene identifiers must be positive")
    if scene.index_code not in {"ndvi", "savi", "evi", "ndmi", "ndre"}:
        raise AnomalyContractError("unsupported index code")
    if scene.record_type not in {"ndvi_record", "satellite_index_record"}:
        raise AnomalyContractError("unsupported observation record type")
    if scene.record_type == "ndvi_record" and scene.index_code != "ndvi":
        raise AnomalyContractError("ndvi_record can only provide ndvi")
    height, width = _validate_matrix("values", scene.values)
    _validate_matrix("quality_mask", scene.quality_mask, height=height, width=width)
    _validate_matrix("field_mask", scene.field_mask, height=height, width=width)
    for row in scene.values:
        for value in row:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AnomalyContractError("pixel values must be numeric")
            if not math.isfinite(value) or not -1 <= value <= 1:
                raise AnomalyContractError("pixel values must be finite and in [-1, 1]")
    for name, mask in (
        ("quality_mask", scene.quality_mask),
        ("field_mask", scene.field_mask),
    ):
        if any(type(value) is not bool for row in mask for value in row):
            raise AnomalyContractError(f"{name} values must be boolean")
    min_lon, min_lat, max_lon, max_lat = scene.bbox
    if not (
        -180 <= min_lon < max_lon <= 180
        and -90 <= min_lat < max_lat <= 90
    ):
        raise AnomalyContractError("bbox must be valid EPSG:4326 bounds")
    if not 0 <= scene.cloud_cover_pct <= 100:
        raise AnomalyContractError("cloud_cover_pct must be in [0, 100]")
    if not 0 <= scene.valid_pixels_pct <= 100:
        raise AnomalyContractError("valid_pixels_pct must be in [0, 100]")
    if not scene.provider.strip():
        raise AnomalyContractError("provider is required")
    if not isinstance(scene.provenance, dict):
        raise AnomalyContractError("provenance must be an object")


def _reason_codes(
    current: PixelScene,
    comparison: PixelScene | None,
    thresholds: AnomalyThresholds,
) -> list[str]:
    reasons: list[str] = []
    if current.cloud_cover_pct > thresholds.maximum_cloud_cover_pct:
        reasons.append("current_cloud_cover")
    if current.valid_pixels_pct < thresholds.minimum_valid_pixels_pct:
        reasons.append("current_valid_pixels")
    if comparison is None:
        reasons.append("comparison_missing")
        return reasons
    if comparison.cloud_cover_pct > thresholds.maximum_cloud_cover_pct:
        reasons.append("comparison_cloud_cover")
    if comparison.valid_pixels_pct < thresholds.minimum_valid_pixels_pct:
        reasons.append("comparison_valid_pixels")
    if (
        current.enterprise_id,
        current.field_id,
        current.index_code,
        current.height,
        current.width,
        current.bbox,
    ) != (
        comparison.enterprise_id,
        comparison.field_id,
        comparison.index_code,
        comparison.height,
        comparison.width,
        comparison.bbox,
    ):
        reasons.append("scene_mismatch")
        return reasons
    separation = (current.observed_at - comparison.observed_at).days
    if separation < thresholds.minimum_separation_days:
        reasons.append("temporal_separation_too_short")
    elif separation > thresholds.maximum_separation_days:
        reasons.append("temporal_separation_too_long")
    return reasons


def _paired_coordinates(
    current: PixelScene,
    comparison: PixelScene,
) -> list[tuple[int, int]]:
    return [
        (row, column)
        for row in range(current.height)
        for column in range(current.width)
        if current.field_mask[row][column]
        and comparison.field_mask[row][column]
        and current.quality_mask[row][column]
        and comparison.quality_mask[row][column]
    ]


def _components(candidates: set[tuple[int, int]]) -> Iterable[list[tuple[int, int]]]:
    remaining = set(candidates)
    while remaining:
        first = min(remaining)
        remaining.remove(first)
        queue = deque([first])
        component: list[tuple[int, int]] = []
        while queue:
            row, column = queue.popleft()
            component.append((row, column))
            for neighbor in (
                (row - 1, column),
                (row + 1, column),
                (row, column - 1),
                (row, column + 1),
            ):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        yield sorted(component)


def _cell_polygon(scene: PixelScene, row: int, column: int):
    min_lon, min_lat, max_lon, max_lat = scene.bbox
    cell_width = (max_lon - min_lon) / scene.width
    cell_height = (max_lat - min_lat) / scene.height
    left = min_lon + column * cell_width
    right = left + cell_width
    top = max_lat - row * cell_height
    bottom = top - cell_height
    return box(left, bottom, right, top)


def _cell_area_ha(scene: PixelScene, row: int) -> float:
    min_lon, min_lat, max_lon, max_lat = scene.bbox
    lon_width = (max_lon - min_lon) / scene.width
    lat_height = (max_lat - min_lat) / scene.height
    latitude = max_lat - (row + 0.5) * lat_height
    width_km = abs(lon_width) * 111.320 * math.cos(math.radians(latitude))
    height_km = abs(lat_height) * 110.574
    return width_km * height_km * 100


def _bounded(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 6)


def _severity(score: float) -> str:
    if score >= 0.85:
        return "critical"
    if score >= 0.65:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def _run_key(
    current: PixelScene,
    comparison: PixelScene | None,
    threshold_hash: str,
) -> str:
    payload = {
        "algorithm": ALGORITHM_VERSION,
        "comparison_record_id": comparison.record_id if comparison else None,
        "comparison_record_type": comparison.record_type if comparison else None,
        "current_record_id": current.record_id,
        "current_record_type": current.record_type,
        "enterprise_id": current.enterprise_id,
        "field_id": current.field_id,
        "index_code": current.index_code,
        "threshold_hash": threshold_hash,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def analyze_pixel_scenes(
    current: PixelScene,
    comparison: PixelScene | None,
    thresholds: AnomalyThresholds | None = None,
) -> AnomalyRunResult:
    """Analyze paired scenes without inferring an agronomic cause."""
    thresholds = thresholds or AnomalyThresholds()
    thresholds.validate()
    validate_scene(current)
    if comparison is not None:
        validate_scene(comparison)
    threshold_hash = thresholds.fingerprint()
    run_key = _run_key(current, comparison, threshold_hash)
    reasons = _reason_codes(current, comparison, thresholds)
    quality_summary: dict[str, Any] = {
        "current_cloud_cover_pct": current.cloud_cover_pct,
        "current_valid_pixels_pct": current.valid_pixels_pct,
        "comparison_cloud_cover_pct": (
            comparison.cloud_cover_pct if comparison else None
        ),
        "comparison_valid_pixels_pct": (
            comparison.valid_pixels_pct if comparison else None
        ),
    }
    if reasons or comparison is None:
        return AnomalyRunResult(
            ALGORITHM_VERSION,
            run_key,
            threshold_hash,
            "insufficient_data",
            tuple(reasons),
            current,
            comparison,
            0,
            None,
            quality_summary,
            (),
        )

    coordinates = _paired_coordinates(current, comparison)
    quality_summary["eligible_pixels"] = len(coordinates)
    if len(coordinates) < thresholds.minimum_eligible_pixels:
        return AnomalyRunResult(
            ALGORITHM_VERSION,
            run_key,
            threshold_hash,
            "insufficient_data",
            ("eligible_pixel_count",),
            current,
            comparison,
            len(coordinates),
            None,
            quality_summary,
            (),
        )

    current_median = median(current.values[row][column] for row, column in coordinates)
    candidates = {
        (row, column)
        for row, column in coordinates
        if current.values[row][column]
        <= current_median - thresholds.within_field_delta
        and current.values[row][column] - comparison.values[row][column]
        <= -thresholds.comparison_drop
    }
    separation = (current.observed_at - comparison.observed_at).days
    zones: list[AnomalyZone] = []
    for component in _components(candidates):
        if len(component) < thresholds.minimum_connected_pixels:
            continue
        area_ha = sum(_cell_area_ha(current, row) for row, _ in component)
        if area_ha < thresholds.minimum_area_ha:
            continue
        geometry = unary_union(
            [_cell_polygon(current, row, column) for row, column in component]
        )
        if geometry.geom_type == "Polygon":
            geometry = MultiPolygon([geometry])
        elif geometry.geom_type != "MultiPolygon":
            polygons = [part for part in geometry.geoms if part.geom_type == "Polygon"]
            geometry = MultiPolygon(polygons)
        if geometry.is_empty or not geometry.is_valid:
            raise AnomalyContractError("generated anomaly geometry is invalid")
        drops = [
            comparison.values[row][column] - current.values[row][column]
            for row, column in component
        ]
        deficits = [
            current_median - current.values[row][column]
            for row, column in component
        ]
        median_drop = median(drops)
        median_deficit = median(deficits)
        score = _bounded(
            0.5 * median_drop / max(thresholds.comparison_drop * 3, 0.001)
            + 0.5
            * median_deficit
            / max(thresholds.within_field_delta * 3, 0.001)
        )
        valid_share = min(
            current.valid_pixels_pct,
            comparison.valid_pixels_pct,
        ) / 100
        temporal_quality = 1 - (
            (separation - thresholds.minimum_separation_days)
            / max(
                1,
                thresholds.maximum_separation_days
                - thresholds.minimum_separation_days,
            )
        ) * 0.35
        size_quality = min(
            1.0,
            len(component) / max(thresholds.minimum_connected_pixels * 3, 1),
        )
        confidence = _bounded(
            0.45 * valid_share + 0.30 * temporal_quality + 0.25 * size_quality
        )
        cell_signature = ";".join(f"{row}:{column}" for row, column in component)
        zone_key = hashlib.sha256(
            f"{run_key}|{cell_signature}".encode("utf-8")
        ).hexdigest()
        zones.append(
            AnomalyZone(
                zone_key=zone_key,
                geometry=mapping(geometry),
                area_ha=round(area_ha, 6),
                score=score,
                severity=_severity(score),
                confidence=confidence,
                persistence_count=1,
                classification="single_scene",
                pixel_count=len(component),
                median_drop=round(median_drop, 6),
                median_deficit=round(median_deficit, 6),
            )
        )
        if len(zones) >= MAX_ANOMALY_ZONES:
            break
    zones.sort(key=lambda zone: zone.zone_key)
    return AnomalyRunResult(
        ALGORITHM_VERSION,
        run_key,
        threshold_hash,
        "detected" if zones else "no_anomaly",
        (),
        current,
        comparison,
        len(coordinates),
        round(current_median, 6),
        quality_summary,
        tuple(zones),
    )


def classify_zones_with_history(
    zones: tuple[AnomalyZone, ...],
    history: tuple[HistoricalZone, ...],
) -> tuple[AnomalyZone, ...]:
    """Classify overlap without assigning an agronomic meaning."""
    historical = [
        (item, _shape_geometry(item.geometry))
        for item in history
        if item.area_ha > 0 and item.persistence_count >= 1
    ]
    classified: list[AnomalyZone] = []
    for zone in zones:
        current_geometry = _shape_geometry(zone.geometry)
        matches: list[tuple[float, HistoricalZone]] = []
        for prior, prior_geometry in historical:
            intersection = current_geometry.intersection(prior_geometry)
            if intersection.is_empty:
                continue
            denominator = min(current_geometry.area, prior_geometry.area)
            overlap = intersection.area / denominator if denominator > 0 else 0
            if overlap >= 0.30:
                matches.append((overlap, prior))
        if not matches:
            classified.append(zone)
            continue
        _, prior = max(matches, key=lambda item: item[0])
        recovering = (
            zone.area_ha <= prior.area_ha * 0.70
            or zone.median_drop <= prior.median_drop * 0.70
        )
        classified.append(
            replace(
                zone,
                classification="recovering" if recovering else "persistent",
                persistence_count=prior.persistence_count + 1,
            )
        )
    return tuple(classified)


def _shape_geometry(value: dict[str, Any]):
    from shapely.geometry import shape

    geometry = shape(value)
    if geometry.is_empty or not geometry.is_valid:
        raise AnomalyContractError("historical anomaly geometry is invalid")
    return geometry
