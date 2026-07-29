"""Validated human-entered variable-rate recommendation contracts."""

from datetime import date
from enum import Enum
import math
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


PositiveInt = Annotated[StrictInt, Field(gt=0)]
CODE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class RecommendationKind(str, Enum):
    fertilizer = "fertilizer"
    seed = "seed"
    pesticide = "pesticide"
    irrigation = "irrigation"


class RateUnit(str, Enum):
    kg_ha = "kg_ha"
    seeds_ha = "seeds_ha"
    l_ha = "l_ha"
    mm = "mm"


class ZoneRates(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    low: float = Field(ge=0, le=10_000_000)
    medium: float = Field(ge=0, le=10_000_000)
    high: float = Field(ge=0, le=10_000_000)


class EquipmentCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    equipment_name: str = Field(min_length=2, max_length=128)
    supported_unit: RateUnit
    minimum_controllable_rate: float = Field(ge=0, le=10_000_000)
    maximum_controllable_rate: float = Field(ge=0, le=10_000_000)
    compatibility_note: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def ordered_bounds(self):
        if self.maximum_controllable_rate < self.minimum_controllable_rate:
            raise ValueError("equipment bounds are reversed")
        return self


class VariableRateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    field_id: PositiveInt
    productivity_run_id: PositiveInt
    parent_recommendation_id: PositiveInt | None = None
    crop_code: str
    season_year: StrictInt
    recommendation_kind: RecommendationKind
    rate_unit: RateUnit
    minimum_rate: float = Field(ge=0, le=10_000_000)
    maximum_rate: float = Field(ge=0, le=10_000_000)
    zone_rates: ZoneRates
    equipment_capability: EquipmentCapability
    notes: str | None = Field(None, max_length=2000)
    safety_acknowledged: bool

    @field_validator("crop_code")
    @classmethod
    def crop_code_valid(cls, value):
        normalized = value.strip().lower()
        if not CODE.fullmatch(normalized):
            raise ValueError("invalid crop_code")
        return normalized

    @field_validator("season_year")
    @classmethod
    def season_valid(cls, value):
        if not 2000 <= value <= date.today().year + 1:
            raise ValueError("season_year is outside supported range")
        return value

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value):
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def safety_and_rate_contract(self):
        units = {
            RecommendationKind.fertilizer: RateUnit.kg_ha,
            RecommendationKind.seed: RateUnit.seeds_ha,
            RecommendationKind.pesticide: RateUnit.l_ha,
            RecommendationKind.irrigation: RateUnit.mm,
        }
        if units[self.recommendation_kind] != self.rate_unit:
            raise ValueError("recommendation kind and rate unit do not match")
        if self.equipment_capability.supported_unit != self.rate_unit:
            raise ValueError("equipment unit does not match rate unit")
        if self.maximum_rate < self.minimum_rate:
            raise ValueError("recommendation bounds are reversed")
        for value in self.zone_rates.model_dump().values():
            if not math.isfinite(value) or not self.minimum_rate <= value <= self.maximum_rate:
                raise ValueError("zone rate is outside human-entered bounds")
            if not (
                self.equipment_capability.minimum_controllable_rate
                <= value
                <= self.equipment_capability.maximum_controllable_rate
            ):
                raise ValueError("zone rate is outside equipment capability")
        if self.safety_acknowledged is not True:
            raise ValueError("safety acknowledgement is required")
        return self


class VariableRateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: PositiveInt
    confirm: bool
    note: str = Field(min_length=3, max_length=2000)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def confirmation_required(self):
        if self.confirm is not True:
            raise ValueError("explicit confirmation is required")
        return self
