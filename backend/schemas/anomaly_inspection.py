"""Typed contracts for the anomaly-to-verification workflow."""

from datetime import datetime
from enum import Enum
import json
import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


PositiveInt = Annotated[StrictInt, Field(gt=0)]
CODE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
HASH = re.compile(r"^[0-9a-f]{64}$")


class SourceKind(str, Enum):
    pixel_ndvi = "pixel_ndvi"
    alert = "alert"
    manual = "manual"


class InspectionPriority(str, Enum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class InspectionStatus(str, Enum):
    new = "new"
    assigned = "assigned"
    in_progress = "in_progress"
    submitted = "submitted"
    confirmed = "confirmed"
    rejected = "rejected"
    cancelled = "cancelled"


class CauseCategory(str, Enum):
    water_stress = "water_stress"
    irrigation_failure = "irrigation_failure"
    pest = "pest"
    disease = "disease"
    nutrient_deficiency = "nutrient_deficiency"
    weed_pressure = "weed_pressure"
    mechanical_damage = "mechanical_damage"
    soil_salinity = "soil_salinity"
    weather_damage = "weather_damage"
    false_positive = "false_positive"
    other = "other"
    unconfirmed = "unconfirmed"


class FindingSeverity(str, Enum):
    none = "none"
    low = "low"
    moderate = "moderate"
    high = "high"
    critical = "critical"


class ActionStatus(str, Enum):
    planned = "planned"
    in_progress = "in_progress"
    completed = "completed"
    verified_effective = "verified_effective"
    verified_ineffective = "verified_ineffective"
    cancelled = "cancelled"


class VerificationResult(str, Enum):
    effective = "effective"
    ineffective = "ineffective"
    another_cycle = "another_cycle"


def bounded_text(value: str | None, minimum: int, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not minimum <= len(normalized) <= maximum:
        raise ValueError(f"length must be {minimum}..{maximum}")
    return normalized


def aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("timezone-aware timestamp required")
    return value


def finite(value: float | None, lower: float, upper: float) -> float | None:
    if value is not None and (not math.isfinite(value) or not lower <= value <= upper):
        raise ValueError(f"finite value in {lower}..{upper} required")
    return value


class Point4326(BaseModel):
    model_config = ConfigDict(extra="forbid")
    longitude: float
    latitude: float

    @field_validator("longitude")
    @classmethod
    def longitude_valid(cls, value):
        return finite(value, -180, 180)

    @field_validator("latitude")
    @classmethod
    def latitude_valid(cls, value):
        return finite(value, -90, 90)


class CreateInspectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_id: PositiveInt
    source_kind: SourceKind
    source_alert_id: PositiveInt | None = None
    provider: str | None = None
    item_id: str | None = None
    acquired_at: datetime | None = None
    index_name: str | None = None
    sampled_value: float | None = None
    comparison_value: float | None = None
    delta: float | None = None
    geometry_hash: str | None = None
    point: Point4326 | None = None
    zone: dict[str, Any] | None = None
    reason: str
    priority: InspectionPriority = InspectionPriority.normal
    assigned_to_id: PositiveInt | None = None
    due_at: datetime

    @field_validator("provider")
    @classmethod
    def provider_valid(cls, value):
        value = bounded_text(value, 2, 40)
        if value is not None and not CODE.fullmatch(value):
            raise ValueError("invalid provider code")
        return value

    @field_validator("item_id")
    @classmethod
    def item_valid(cls, value):
        return bounded_text(value, 3, 768)

    @field_validator("index_name")
    @classmethod
    def index_valid(cls, value):
        value = bounded_text(value, 2, 20)
        if value is not None and not CODE.fullmatch(value):
            raise ValueError("invalid index name")
        return value

    @field_validator("geometry_hash")
    @classmethod
    def hash_valid(cls, value):
        if value is not None and not HASH.fullmatch(value):
            raise ValueError("geometry_hash must be lowercase SHA-256")
        return value

    @field_validator("reason")
    @classmethod
    def reason_valid(cls, value):
        return bounded_text(value, 5, 2000)

    @field_validator("due_at", "acquired_at")
    @classmethod
    def timestamp_valid(cls, value):
        return aware(value)

    @field_validator("sampled_value", "comparison_value")
    @classmethod
    def index_value_valid(cls, value):
        return finite(value, -1, 1)

    @field_validator("delta")
    @classmethod
    def delta_valid(cls, value):
        return finite(value, -2, 2)

    @field_validator("zone")
    @classmethod
    def zone_valid(cls, value):
        if value is None:
            return None
        if value.get("type") not in {"Polygon", "MultiPolygon"}:
            raise ValueError("zone must be Polygon or MultiPolygon GeoJSON")
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=True)
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError("zone exceeds 64 KiB")
        return value

    @model_validator(mode="after")
    def source_shape(self):
        if self.point is not None and self.zone is not None:
            raise ValueError("point and zone are mutually exclusive")
        pixel = (self.provider, self.item_id, self.acquired_at, self.index_name,
                 self.sampled_value, self.geometry_hash)
        if self.source_kind == SourceKind.pixel_ndvi:
            if any(value is None for value in pixel) or (self.point is None and self.zone is None):
                raise ValueError("pixel_ndvi requires complete scene, sample, geometry and location snapshot")
            if self.source_alert_id is not None:
                raise ValueError("pixel_ndvi forbids source_alert_id")
        elif self.source_kind == SourceKind.alert:
            if self.source_alert_id is None:
                raise ValueError("alert requires source_alert_id")
            if any(value is not None for value in pixel[:4]):
                raise ValueError("alert forbids provider scene fields")
        else:
            if self.source_alert_id is not None or any(value is not None for value in pixel):
                raise ValueError("manual source forbids alert and provider snapshot fields")
        return self


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: PositiveInt


class AssignInspectionRequest(VersionRequest):
    assigned_to_id: PositiveInt
    reason: str | None = None

    @field_validator("reason")
    @classmethod
    def reason_valid(cls, value):
        return bounded_text(value, 5, 1000)


class CancelInspectionRequest(VersionRequest):
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_valid(cls, value):
        return bounded_text(value, 5, 2000)


class FindingRequest(VersionRequest):
    inspected_at: datetime
    gps_point: Point4326 | None = None
    gps_accuracy_m: float | None = None
    cause: CauseCategory
    other_explanation: str | None = None
    severity: FindingSeverity
    affected_area_ha: float | None = None
    affected_area_pct: float | None = None
    observations: str
    recommended_action: str
    sync_state: Literal["server", "pending_sync"] = "server"

    @field_validator("inspected_at")
    @classmethod
    def inspected_at_valid(cls, value):
        return aware(value)

    @field_validator("gps_accuracy_m")
    @classmethod
    def gps_accuracy_valid(cls, value):
        return finite(value, 0, 10000)

    @field_validator("affected_area_ha")
    @classmethod
    def area_ha_valid(cls, value):
        return finite(value, 0, 1_000_000)

    @field_validator("affected_area_pct")
    @classmethod
    def area_pct_valid(cls, value):
        return finite(value, 0, 100)

    @field_validator("other_explanation")
    @classmethod
    def other_valid(cls, value):
        return bounded_text(value, 3, 2000)

    @field_validator("observations", "recommended_action")
    @classmethod
    def narrative_valid(cls, value):
        return bounded_text(value, 3, 4000)

    @model_validator(mode="after")
    def finding_shape(self):
        if self.cause == CauseCategory.other and not self.other_explanation:
            raise ValueError("other cause requires explanation")
        if self.affected_area_ha is not None and self.affected_area_pct is not None:
            raise ValueError("provide affected area in hectares or percent, not both")
        if self.affected_area_ha is None and self.affected_area_pct is None:
            raise ValueError("affected area is required")
        return self


class ReviewInspectionRequest(VersionRequest):
    decision: Literal["confirmed", "rejected"]
    reason: str | None = None

    @field_validator("reason")
    @classmethod
    def reason_valid(cls, value):
        return bounded_text(value, 5, 2000)

    @model_validator(mode="after")
    def rejection_reason(self):
        if self.decision == "rejected" and not self.reason:
            raise ValueError("rejection requires reason")
        return self


class CreateActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_inspection_version: PositiveInt
    action_type: str
    owner_id: PositiveInt
    instructions: str
    planned_start_at: datetime | None = None
    due_at: datetime

    @field_validator("action_type")
    @classmethod
    def type_valid(cls, value):
        value = bounded_text(value, 2, 50)
        if not CODE.fullmatch(value):
            raise ValueError("invalid action type")
        return value

    @field_validator("instructions")
    @classmethod
    def instructions_valid(cls, value):
        return bounded_text(value, 5, 4000)

    @field_validator("planned_start_at", "due_at")
    @classmethod
    def timestamp_valid(cls, value):
        return aware(value)


class ActionTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: PositiveInt
    transition: Literal["start", "complete", "cancel"]
    note: str | None = None

    @field_validator("note")
    @classmethod
    def note_valid(cls, value):
        return bounded_text(value, 5, 4000)

    @model_validator(mode="after")
    def note_required(self):
        if self.transition in {"complete", "cancel"} and not self.note:
            raise ValueError("completion and cancellation require a note")
        return self


class VerifyActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: PositiveInt
    result: VerificationResult
    notes: str
    index_name: str | None = None
    sampled_value: float | None = None
    create_follow_up: bool = False
    follow_up_assignee_id: PositiveInt | None = None
    follow_up_due_at: datetime | None = None

    @field_validator("notes")
    @classmethod
    def notes_valid(cls, value):
        return bounded_text(value, 5, 4000)

    @field_validator("index_name")
    @classmethod
    def index_valid(cls, value):
        value = bounded_text(value, 2, 20)
        if value is not None and not CODE.fullmatch(value):
            raise ValueError("invalid index name")
        return value

    @field_validator("sampled_value")
    @classmethod
    def sample_valid(cls, value):
        return finite(value, -1, 1)

    @field_validator("follow_up_due_at")
    @classmethod
    def due_valid(cls, value):
        return aware(value)

    @model_validator(mode="after")
    def follow_up_shape(self):
        if self.create_follow_up:
            if self.result == VerificationResult.effective:
                raise ValueError("effective verification cannot create a follow-up")
            if self.follow_up_assignee_id is None or self.follow_up_due_at is None:
                raise ValueError("follow-up assignee and due_at are required")
        elif self.follow_up_assignee_id is not None or self.follow_up_due_at is not None:
            raise ValueError("follow-up fields require create_follow_up")
        return self


class PhotoItem(BaseModel):
    id: int
    original_filename: str
    media_type: str
    byte_size: int
    sha256: str
    captured_at: datetime | None
    created_at: datetime
    version: int


class WorkflowMutationResponse(BaseModel):
    inspection: dict[str, Any] | None = None
    action: dict[str, Any] | None = None
    photo: PhotoItem | None = None
    follow_up_inspection: dict[str, Any] | None = None


class QueueResponse(BaseModel):
    generated_at: datetime
    summary: dict[str, int]
    limit: int
    offset: int
    items: list[dict[str, Any]]
