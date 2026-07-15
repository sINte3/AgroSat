from datetime import date, datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

PositiveInt = Annotated[StrictInt, Field(gt=0)]


class InspectionStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class InspectionSource(str, Enum):
    attention_queue = "attention_queue"
    manual = "manual"


class InspectionPriority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


def _strip(value, minimum, maximum):
    if value is None:
        return None
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise ValueError(f"length must be {minimum}..{maximum}")
    return value


class CreateInspectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_id: PositiveInt
    assigned_to_id: PositiveInt | None = None
    source: InspectionSource = InspectionSource.attention_queue
    source_priority: InspectionPriority | None = None
    source_attention_score: Annotated[StrictInt, Field(ge=0, le=100)] | None = None
    source_observation_date: date | None = None
    source_reason_codes: list[str] = Field(default_factory=list, max_length=20)
    title: str
    instructions: str | None = None
    due_date: date | None = None

    @field_validator("title")
    @classmethod
    def title_valid(cls, v): return _strip(v, 3, 255)

    @field_validator("instructions")
    @classmethod
    def instructions_valid(cls, v): return _strip(v, 3, 2000)

    @field_validator("source_reason_codes")
    @classmethod
    def reasons_valid(cls, values):
        import re
        normalized = sorted(set(values))
        if len(normalized) > 20 or any(not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", x) for x in normalized):
            raise ValueError("invalid source reason code")
        return normalized

    @model_validator(mode="after")
    def source_valid(self):
        snapshot = (self.source_priority, self.source_attention_score, self.source_observation_date)
        if self.source == InspectionSource.attention_queue and (snapshot[0] is None or snapshot[1] is None or not self.source_reason_codes):
            raise ValueError("attention_queue requires priority, score and reason codes")
        if self.source == InspectionSource.manual and (any(x is not None for x in snapshot) or self.source_reason_codes):
            raise ValueError("manual source forbids attention snapshot")
        return self


class UpdateInspectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: PositiveInt
    assigned_to_id: PositiveInt | None = None
    title: str | None = None
    instructions: str | None = None
    due_date: date | None = None

    @field_validator("title")
    @classmethod
    def title_valid(cls, v): return _strip(v, 3, 255)
    @field_validator("instructions")
    @classmethod
    def instructions_valid(cls, v): return _strip(v, 3, 2000)
    @model_validator(mode="after")
    def mutable_present(self):
        if not (self.model_fields_set - {"expected_version"}): raise ValueError("one mutable field is required")
        return self


class TransitionRequest(BaseModel): expected_version: PositiveInt
class CompleteInspectionRequest(TransitionRequest):
    completion_summary: str
    @field_validator("completion_summary")
    @classmethod
    def valid(cls, v): return _strip(v, 10, 4000)
class CancelInspectionRequest(TransitionRequest):
    cancellation_reason: str
    @field_validator("cancellation_reason")
    @classmethod
    def valid(cls, v): return _strip(v, 5, 2000)


class FieldSummary(BaseModel): id: int; name: str; enterprise_id: int; enterprise_name: str
class UserSummary(BaseModel): id: int; display_name: str | None
class InspectionItem(BaseModel):
    id: int; field: FieldSummary; created_by: UserSummary; assigned_to: UserSummary | None
    source: InspectionSource; source_priority: InspectionPriority | None; source_attention_score: int | None
    source_observation_date: date | None; source_reason_codes: list[str]; title: str; instructions: str | None
    due_date: date | None; status: InspectionStatus; is_overdue: bool; version: int
    created_at: datetime; updated_at: datetime; started_at: datetime | None; completed_at: datetime | None
    cancelled_at: datetime | None; completion_summary: str | None; cancellation_reason: str | None
class InspectionCreateResponse(BaseModel): created: bool; inspection: InspectionItem
class InspectionListSummary(BaseModel): total: int; pending: int; in_progress: int; completed: int; cancelled: int; overdue: int
class InspectionListResponse(BaseModel): generated_at: datetime; summary: InspectionListSummary; limit: int; offset: int; items: list[InspectionItem]
