"""Bounded command contracts for the closed-loop workspace."""
from datetime import datetime
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Category = Literal['irrigation','nutrition','crop_protection','drainage','reinspection','sampling','cultivation','other']
Priority = Literal['low','normal','high','urgent']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DraftRequest(StrictModel):
    inspection_id: int = Field(gt=0, strict=True)
    reason: str = Field(min_length=5, max_length=2000)


class VersionRequest(StrictModel):
    expected_version: int = Field(gt=0, strict=True)
    reason: str = Field(min_length=5, max_length=2000)


class PlanEdit(VersionRequest):
    decision: str = Field(min_length=5, max_length=4000)
    objective: str = Field(min_length=5, max_length=4000)
    expected_outcome: str = Field(min_length=5, max_length=4000)
    priority: Priority


class PlanCommand(VersionRequest):
    operation: Literal['approve','cancel','close','override_close','rework','reinspection','reopen','supersede']


class WorkSpec(StrictModel):
    category: Category
    instruction: str = Field(min_length=5, max_length=4000)
    assigned_to_id: int | None = Field(None, gt=0, strict=True)
    planned_start_at: datetime | None = None
    due_at: datetime | None = None
    geometry: dict | None = None

    @field_validator('planned_start_at','due_at')
    @classmethod
    def aware(cls, value):
        if value and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError('Timezone is required')
        return value

    @field_validator('geometry')
    @classmethod
    def bounded_geometry(cls, value):
        if value is not None:
            if value.get('type') not in {'Point','Polygon','MultiPolygon'} or len(json.dumps(value, allow_nan=False).encode())>65536:
                raise ValueError('A bounded EPSG:4326 point or polygon is required')
        return value

    @model_validator(mode='after')
    def dates(self):
        if self.due_at and self.planned_start_at and self.due_at<self.planned_start_at:
            raise ValueError('Deadline precedes planned start')
        return self


class WorkCreate(WorkSpec, VersionRequest):
    pass


class WorkCommand(VersionRequest):
    expected_plan_version: int = Field(gt=0, strict=True)
    operation: Literal['assign','start','complete','cancel']
    assigned_to_id: int | None = Field(None, gt=0, strict=True)
    planned_start_at: datetime | None = None
    due_at: datetime | None = None
    result_note: str | None = Field(None, min_length=5, max_length=4000)

    _aware = field_validator('planned_start_at','due_at')(WorkSpec.aware.__func__)


class Reevaluate(VersionRequest):
    observation_id: int | None = Field(None, gt=0, strict=True)


class MutationResponse(StrictModel):
    plan_id: int
    version: int
    status: str
    item_id: int | None = None
    item_version: int | None = None
    verification_id: int | None = None
    photo_id: int | None = None
    superseding_plan_id: int | None = None
    follow_up_inspection_id: int | None = None


class QueueResponse(StrictModel):
    items: list[dict]
    total: int
    limit: int
    offset: int
