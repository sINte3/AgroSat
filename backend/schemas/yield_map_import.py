"""Validated provider-neutral yield-map import contracts."""

from datetime import date
from enum import Enum
import re
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)


PositiveInt = Annotated[StrictInt, Field(gt=0)]
HASH = re.compile(r"^[0-9a-f]{64}$")
CODE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class YieldSchemaCode(str, Enum):
    yield_point_csv_v1 = "yield_point_csv_v1"


class YieldUnit(str, Enum):
    t_ha = "t_ha"
    kg_ha = "kg_ha"


class YieldPointInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    longitude: float
    latitude: float
    yield_value: float
    observed_at: str = Field(min_length=1, max_length=64)
    machine_point_id: str | None = Field(None, max_length=128)
    speed_kph: float | None = None
    moisture_pct: float | None = None

    @field_validator("machine_point_id")
    @classmethod
    def machine_point_id_bounded(cls, value):
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", normalized):
            raise ValueError("invalid machine_point_id")
        return normalized


class YieldImportPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: PositiveInt
    season_year: StrictInt
    crop_code: str
    schema_code: YieldSchemaCode = YieldSchemaCode.yield_point_csv_v1
    source_filename: str
    source_sha256: str
    source_provider: str
    machine_id: str | None = Field(None, max_length=128)
    machine_model: str | None = Field(None, max_length=128)
    yield_unit: YieldUnit
    rows: list[YieldPointInput] = Field(min_length=1, max_length=5000)

    @field_validator("season_year")
    @classmethod
    def season_supported(cls, value):
        if not 2000 <= value <= date.today().year + 1:
            raise ValueError("season_year is outside supported range")
        return value

    @field_validator("crop_code")
    @classmethod
    def crop_code_supported(cls, value):
        normalized = value.strip().lower()
        if not CODE.fullmatch(normalized):
            raise ValueError("invalid crop_code")
        return normalized

    @field_validator("source_filename")
    @classmethod
    def safe_filename(cls, value):
        normalized = value.strip()
        if (
            not 1 <= len(normalized) <= 255
            or "/" in normalized
            or "\\" in normalized
            or normalized in {".", ".."}
        ):
            raise ValueError("source_filename must be a safe basename")
        return normalized

    @field_validator("source_sha256")
    @classmethod
    def source_hash(cls, value):
        normalized = value.strip().lower()
        if not HASH.fullmatch(normalized):
            raise ValueError("invalid source_sha256")
        return normalized

    @field_validator("source_provider")
    @classmethod
    def source_provider_bounded(cls, value):
        normalized = value.strip()
        if not 2 <= len(normalized) <= 100:
            raise ValueError("source_provider length must be 2..100")
        return normalized

    @field_validator("machine_id", "machine_model")
    @classmethod
    def machine_metadata_bounded(cls, value):
        if value is None:
            return None
        normalized = value.strip()
        if not 1 <= len(normalized) <= 128:
            raise ValueError("machine metadata length must be 1..128")
        return normalized


class YieldImportAcceptRequest(YieldImportPreviewRequest):
    preview_fingerprint: str
    confirm: bool

    @field_validator("preview_fingerprint")
    @classmethod
    def preview_hash(cls, value):
        normalized = value.strip().lower()
        if not HASH.fullmatch(normalized):
            raise ValueError("invalid preview_fingerprint")
        return normalized

    @model_validator(mode="after")
    def confirmation_required(self):
        if self.confirm is not True:
            raise ValueError("confirm must be true")
        return self
