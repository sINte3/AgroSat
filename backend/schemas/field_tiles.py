"""Typed contracts for bounded field vector-tile delivery."""

from typing import Literal

from pydantic import BaseModel


class FieldTileScope(BaseModel):
    role: Literal["admin", "manager", "agronomist", "viewer"]
    enterprise_id: int | None


class FieldTileMetadataResponse(BaseModel):
    schema_version: Literal["task209_field_mvt_v1"]
    source_layer: Literal["fields"]
    min_zoom: int
    max_zoom: int
    extent: int
    buffer: int
    scope: FieldTileScope
    field_count: int
    bounds: list[float] | None
    properties: list[str]
    tile_template: str
