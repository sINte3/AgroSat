"""Validated contracts for weather and irrigation operational context."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


PositiveInt = Annotated[StrictInt, Field(gt=0)]
WaterAmount = Annotated[
    Decimal,
    Field(gt=0, le=1000, max_digits=8, decimal_places=2),
]


class IrrigationEventType(str, Enum):
    irrigation_applied = "irrigation_applied"
    irrigation_interrupted = "irrigation_interrupted"
    equipment_issue = "equipment_issue"
    field_observation = "field_observation"


class IrrigationMethodCode(str, Enum):
    canal = "canal"
    drip = "drip"
    sprinkler = "sprinkler"
    furrow = "furrow"
    manual = "manual"
    unknown = "unknown"


class IrrigationEvidenceSource(str, Enum):
    human_reported = "human_reported"


class CreateIrrigationEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inspection_id: PositiveInt | None = None
    event_type: IrrigationEventType
    occurred_at: datetime
    method_code: IrrigationMethodCode = IrrigationMethodCode.unknown
    water_amount_mm: WaterAmount | None = None
    evidence_source: IrrigationEvidenceSource = (
        IrrigationEvidenceSource.human_reported
    )
    note: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value

    @field_validator("note")
    @classmethod
    def note_bounded(cls, value):
        if value is None:
            return None
        normalized = value.strip()
        if not 3 <= len(normalized) <= 4000:
            raise ValueError("note length must be 3..4000")
        return normalized

    @model_validator(mode="after")
    def amount_matches_event(self):
        if (
            self.water_amount_mm is not None
            and self.event_type != IrrigationEventType.irrigation_applied
        ):
            raise ValueError(
                "water_amount_mm is supported only for irrigation_applied"
            )
        return self


class IrrigationRecorder(BaseModel):
    id: int
    display_name: str | None


class IrrigationEventItem(BaseModel):
    id: int
    enterprise_id: int
    field_id: int
    inspection_id: int | None
    recorded_by: IrrigationRecorder
    event_type: IrrigationEventType
    occurred_at: datetime
    method_code: IrrigationMethodCode
    water_amount_mm: Decimal | None
    evidence_source: IrrigationEvidenceSource
    note: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class IrrigationEventCreateResponse(BaseModel):
    created: bool
    event: IrrigationEventItem
