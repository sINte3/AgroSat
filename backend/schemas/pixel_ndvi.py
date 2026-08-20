"""Versioned API contracts for the pixel NDVI field workspace."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PixelNDVIScene(BaseModel):
    scene_id: str
    field_id: int
    acquired_at: datetime
    provider: Literal["cdse"]
    source: Literal["Sentinel-2 L2A"]
    cloud_cover_pct: float | None
    valid_pixel_pct: float | None
    raster_available: bool
    freshness: Literal["fresh", "stale"]
    age_days: int
    is_default: bool


class PixelNDVISceneCatalog(BaseModel):
    schema_version: Literal["program_r3_pixel_ndvi_v1"]
    field_id: int
    date_from: str
    date_to: str
    scenes: list[PixelNDVIScene]


class PixelNDVILegendItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: float = Field(alias="from")
    to: float
    color: str
    label: str


class PixelNDVIHistogramBin(PixelNDVILegendItem):
    count: int


class PixelNDVISummary(BaseModel):
    source: Literal["pixel_raster"]
    valid_pixel_count: int
    valid_pixel_pct: float
    no_data_pixel_count: int
    no_data_pixel_pct: float
    min: float | None
    max: float | None
    mean: float | None
    median: float | None
    p10: float | None
    p90: float | None
    histogram: list[PixelNDVIHistogramBin]


class PixelNDVIWorkspace(BaseModel):
    schema_version: Literal["program_r3_pixel_ndvi_v1"]
    field_id: int
    scene: PixelNDVIScene
    bounds: list[float]
    corners: list[list[float]]
    width: int
    height: int
    source_resolution_m: int
    effective_resolution_m: float
    response_bytes: int
    mask: str
    legend: list[PixelNDVILegendItem]
    no_data_legend: dict[str, str]
    summary: PixelNDVISummary
    image_url: str
    disclaimer: str


class PixelNDVIClass(BaseModel):
    code: str
    model_config = ConfigDict(populate_by_name=True)
    from_: float = Field(alias="from")
    to: float
    color: str
    label: str


class PixelNDVISample(BaseModel):
    schema_version: Literal["program_r3_pixel_ndvi_v1"]
    field_id: int
    scene_id: str
    acquired_at: datetime
    longitude: float
    latitude: float
    resolution_m: float
    status: Literal["value", "no_data"]
    ndvi: float | None
    classification: PixelNDVIClass | None
