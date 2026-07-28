"""Provider-neutral field raster response contracts."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RasterLegendItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: float | None = Field(default=None, alias="from")
    to: float
    color: str
    label: str


class RasterQuality(BaseModel):
    accepted_observation: bool
    mask: str


class RasterProvenance(BaseModel):
    provider: Literal["sentinel_process"]
    satellite: str
    observation_id: int
    processing_version: str
    request_timeout_class: str


class RasterMetadataResponse(BaseModel):
    schema_version: Literal["task209_raster_provider_v1"]
    field_id: int
    enterprise_id: int
    index_code: Literal["ndvi"]
    observation_date: date
    bbox: list[float]
    default_size: int
    allowed_sizes: list[int]
    legend: list[RasterLegendItem]
    limitations: list[str]
    quality: RasterQuality
    provenance: RasterProvenance
    image_template: str
