"""Response schemas for secure field-level NDVI rasters."""

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class NDVIRasterLegendItem(BaseModel):
    from_: Optional[float] = Field(default=None, alias="from")
    to: float
    color: str
    label: str

    class Config:
        populate_by_name = True


class NDVIRasterMetadataResponse(BaseModel):
    field_id: int
    observation_date: date
    satellite: str
    bbox: list[float]
    default_size: int
    allowed_sizes: list[int]
    legend: list[NDVIRasterLegendItem]
    limitations: list[str]
