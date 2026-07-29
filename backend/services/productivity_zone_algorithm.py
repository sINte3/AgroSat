"""Deterministic measured-yield productivity-zone algorithm."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from statistics import median


ALGORITHM = "yield_grid_stability"
ALGORITHM_VERSION = "yield_grid_stability_v1"
GRID_METRES = 30.0
MIN_SEASONS = 3
MIN_POINTS_PER_SEASON = 20
MIN_TOTAL_POINTS = 60
MIN_CELL_SEASONS = 2
MAX_SELECTED_SEASONS = 5
MAX_POINTS = 25_000


@dataclass(frozen=True, slots=True)
class YieldMeasurement:
    import_id: int
    source_sha256: str
    season_year: int
    longitude: float
    latitude: float
    yield_t_ha: float


@dataclass(frozen=True, slots=True)
class ProductivityCell:
    column: int
    row: int
    score: float
    zone_class: str
    seasons: tuple[int, ...]
    polygon: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class ProductivityResult:
    run_key: str
    status: str
    reason_codes: tuple[str, ...]
    selected_seasons: tuple[int, ...]
    source_import_ids: tuple[int, ...]
    source_sha256s: tuple[str, ...]
    point_count: int
    eligible_cell_count: int
    confidence: float
    cells: tuple[ProductivityCell, ...]
    parameters: dict


def _canonical_hash(value) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _run_key(
    field_id: int,
    seasons: tuple[int, ...],
    imports: tuple[int, ...],
    hashes: tuple[str, ...],
    parameters: dict,
) -> str:
    return _canonical_hash({
        "algorithm_version": ALGORITHM_VERSION,
        "field_id": field_id,
        "parameters": parameters,
        "seasons": seasons,
        "source_import_ids": imports,
        "source_sha256s": hashes,
    })


def _result(
    *,
    field_id,
    seasons,
    imports,
    hashes,
    point_count,
    parameters,
    reasons=(),
    cells=(),
    confidence=0.0,
):
    return ProductivityResult(
        run_key=_run_key(field_id, seasons, imports, hashes, parameters),
        status="insufficient_data" if reasons else "ready",
        reason_codes=tuple(sorted(set(reasons))),
        selected_seasons=seasons,
        source_import_ids=imports,
        source_sha256s=hashes,
        point_count=point_count,
        eligible_cell_count=len(cells),
        confidence=round(max(0.0, min(1.0, confidence)), 4),
        cells=tuple(cells),
        parameters=parameters,
    )


def _polygon(
    column: int,
    row: int,
    anchor_lon: float,
    anchor_lat: float,
) -> tuple[tuple[float, float], ...]:
    metres_per_latitude = 111_320.0
    metres_per_longitude = max(
        1.0,
        metres_per_latitude * math.cos(math.radians(anchor_lat)),
    )
    west = anchor_lon + column * GRID_METRES / metres_per_longitude
    east = anchor_lon + (column + 1) * GRID_METRES / metres_per_longitude
    south = anchor_lat + row * GRID_METRES / metres_per_latitude
    north = anchor_lat + (row + 1) * GRID_METRES / metres_per_latitude
    return (
        (round(west, 8), round(south, 8)),
        (round(east, 8), round(south, 8)),
        (round(east, 8), round(north, 8)),
        (round(west, 8), round(north, 8)),
        (round(west, 8), round(south, 8)),
    )


def analyze_productivity(
    field_id: int,
    measurements: list[YieldMeasurement],
) -> ProductivityResult:
    """Create reproducible cells without claiming interpolation or causality."""
    if field_id <= 0:
        raise ValueError("field_id must be positive")
    if len(measurements) > MAX_POINTS:
        raise ValueError("measurement batch exceeds 25000 points")
    if any(
        not (
            item.import_id > 0
            and len(item.source_sha256) == 64
            and 2000 <= item.season_year <= 2200
            and -180 <= item.longitude <= 180
            and -90 <= item.latitude <= 90
            and math.isfinite(item.yield_t_ha)
            and item.yield_t_ha > 0
        )
        for item in measurements
    ):
        raise ValueError("invalid measured-yield input")

    parameters = {
        "grid_metres": GRID_METRES,
        "minimum_cell_seasons": MIN_CELL_SEASONS,
        "minimum_points_per_season": MIN_POINTS_PER_SEASON,
        "minimum_seasons": MIN_SEASONS,
        "minimum_total_points": MIN_TOTAL_POINTS,
        "normalization": "median_mad_1.4826_clamp_3_v1",
        "smoothing": "self_weight_2_eight_neighbours_weight_1_v1",
        "classification": "deterministic_rank_terciles_v1",
    }
    seasons = tuple(sorted({item.season_year for item in measurements}))
    imports = tuple(sorted({item.import_id for item in measurements}))
    hashes = tuple(sorted({item.source_sha256 for item in measurements}))
    reasons = []
    if len(seasons) < MIN_SEASONS:
        reasons.append("minimum_seasons_not_met")
    counts = {
        season: sum(item.season_year == season for item in measurements)
        for season in seasons
    }
    if any(count < MIN_POINTS_PER_SEASON for count in counts.values()):
        reasons.append("minimum_points_per_season_not_met")
    if len(measurements) < MIN_TOTAL_POINTS:
        reasons.append("minimum_total_points_not_met")
    if reasons:
        return _result(
            field_id=field_id,
            seasons=seasons,
            imports=imports,
            hashes=hashes,
            point_count=len(measurements),
            parameters=parameters,
            reasons=reasons,
        )

    normalized = []
    for season in seasons:
        season_points = [
            item for item in measurements if item.season_year == season
        ]
        center = median(item.yield_t_ha for item in season_points)
        mad = median(abs(item.yield_t_ha - center) for item in season_points)
        if mad == 0:
            reasons.append(f"zero_mad_season_{season}")
            continue
        scale = 1.4826 * mad
        for item in season_points:
            robust_z = max(-3.0, min(3.0, (item.yield_t_ha - center) / scale))
            normalized.append((item, round((robust_z + 3.0) / 6.0, 8)))
    if reasons:
        return _result(
            field_id=field_id,
            seasons=seasons,
            imports=imports,
            hashes=hashes,
            point_count=len(measurements),
            parameters=parameters,
            reasons=reasons,
        )

    anchor_lon = sum(item.longitude for item in measurements) / len(measurements)
    anchor_lat = sum(item.latitude for item in measurements) / len(measurements)
    metres_per_longitude = max(
        1.0,
        111_320.0 * math.cos(math.radians(anchor_lat)),
    )
    grouped: dict[tuple[int, int], list[tuple[int, float]]] = {}
    for item, score in normalized:
        column = math.floor(
            (item.longitude - anchor_lon) * metres_per_longitude / GRID_METRES
        )
        row = math.floor(
            (item.latitude - anchor_lat) * 111_320.0 / GRID_METRES
        )
        grouped.setdefault((column, row), []).append((item.season_year, score))

    raw_scores = {}
    cell_seasons = {}
    for key, values in grouped.items():
        present = tuple(sorted({season for season, _ in values}))
        if len(present) >= MIN_CELL_SEASONS:
            raw_scores[key] = median(score for _, score in values)
            cell_seasons[key] = present
    if not raw_scores:
        return _result(
            field_id=field_id,
            seasons=seasons,
            imports=imports,
            hashes=hashes,
            point_count=len(measurements),
            parameters=parameters,
            reasons=("no_multi_season_cells",),
        )

    smoothed = {}
    for key, score in raw_scores.items():
        weighted = [score, score]
        column, row = key
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == dy == 0:
                    continue
                neighbour = raw_scores.get((column + dx, row + dy))
                if neighbour is not None:
                    weighted.append(neighbour)
        smoothed[key] = round(sum(weighted) / len(weighted), 8)

    ranked = sorted(smoothed, key=lambda key: (smoothed[key], key[1], key[0]))
    total = len(ranked)
    cells = []
    for rank, key in enumerate(ranked):
        position = rank * 3 // total
        zone_class = ("low", "medium", "high")[min(position, 2)]
        cells.append(ProductivityCell(
            column=key[0],
            row=key[1],
            score=smoothed[key],
            zone_class=zone_class,
            seasons=cell_seasons[key],
            polygon=_polygon(key[0], key[1], anchor_lon, anchor_lat),
        ))
    all_season_fraction = sum(
        len(cell.seasons) == len(seasons) for cell in cells
    ) / len(cells)
    confidence = (
        min(len(seasons), 5) / 5 * 0.30
        + min(len(measurements), 200) / 200 * 0.30
        + all_season_fraction * 0.40
    )
    return _result(
        field_id=field_id,
        seasons=seasons,
        imports=imports,
        hashes=hashes,
        point_count=len(measurements),
        parameters=parameters,
        cells=tuple(sorted(cells, key=lambda cell: (cell.row, cell.column))),
        confidence=confidence,
    )


def result_payload(result: ProductivityResult) -> dict:
    return {
        **asdict(result),
        "cells": [asdict(cell) for cell in result.cells],
    }
