"""Validated request contracts for the operational closure workflow."""

from datetime import date, datetime
from enum import Enum
import json
import re
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)


PositiveInt = Annotated[StrictInt, Field(gt=0)]
MAX_EVIDENCE_BYTES = 25 * 1024 * 1024
CODE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CauseCode(str, Enum):
    irrigation = "irrigation"
    pest = "pest"
    disease = "disease"
    nutrient = "nutrient"
    weather = "weather"
    soil = "soil"
    mechanical = "mechanical"
    crop_stage = "crop_stage"
    no_issue = "no_issue"
    other = "other"
    unconfirmed = "unconfirmed"


class EvidenceType(str, Enum):
    photo = "photo"
    geolocation = "geolocation"


class ActionStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    blocked = "blocked"
    closed = "closed"


class IndexCode(str, Enum):
    ndvi = "ndvi"
    savi = "savi"
    evi = "evi"
    ndmi = "ndmi"
    ndre = "ndre"


def bounded_text(value: str | None, minimum: int, maximum: int):
    if value is None:
        return None
    normalized = value.strip()
    if not minimum <= len(normalized) <= maximum:
        raise ValueError(f"length must be {minimum}..{maximum}")
    return normalized


class LocationContract(BaseModel):
    latitude: float | None = None
    longitude: float | None = None

    @model_validator(mode="after")
    def location_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        if self.latitude is not None and not -90 <= self.latitude <= 90:
            raise ValueError("latitude is outside EPSG:4326")
        if self.longitude is not None and not -180 <= self.longitude <= 180:
            raise ValueError("longitude is outside EPSG:4326")
        return self


class RecordInspectionResultRequest(LocationContract):
    model_config = ConfigDict(extra="forbid")
    expected_version: PositiveInt
    cause_code: CauseCode
    cause_details: str | None = None
    evidence_note: str | None = None

    @field_validator("cause_details")
    @classmethod
    def cause_details_valid(cls, value):
        return bounded_text(value, 3, 2000)

    @field_validator("evidence_note")
    @classmethod
    def evidence_note_valid(cls, value):
        return bounded_text(value, 3, 4000)

    @model_validator(mode="after")
    def other_has_details(self):
        if self.cause_code == CauseCode.other and not self.cause_details:
            raise ValueError("other cause requires cause_details")
        return self


class EvidenceMetadataRequest(LocationContract):
    model_config = ConfigDict(extra="forbid")
    expected_version: PositiveInt
    result_id: PositiveInt | None = None
    evidence_type: EvidenceType
    provider: str = "metadata_only"
    provider_reference: str | None = None
    original_filename: str | None = None
    media_type: str | None = None
    byte_size: Annotated[StrictInt, Field(ge=0, le=MAX_EVIDENCE_BYTES)] | None = None
    sha256: str | None = None
    captured_at: datetime | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider")
    @classmethod
    def provider_valid(cls, value):
        value = bounded_text(value, 2, 40)
        if not CODE.fullmatch(value):
            raise ValueError("invalid provider code")
        return value

    @field_validator("provider_reference")
    @classmethod
    def provider_reference_valid(cls, value):
        return bounded_text(value, 1, 255)

    @field_validator("original_filename")
    @classmethod
    def original_filename_valid(cls, value):
        value = bounded_text(value, 1, 255)
        if value and ("/" in value or "\\" in value or "\x00" in value):
            raise ValueError("original_filename must not contain a path")
        return value

    @field_validator("media_type")
    @classmethod
    def media_type_valid(cls, value):
        return bounded_text(value, 3, 100)

    @field_validator("sha256")
    @classmethod
    def sha256_valid(cls, value):
        if value is not None and not SHA256.fullmatch(value):
            raise ValueError("sha256 must be lowercase hexadecimal")
        return value

    @field_validator("provider_metadata")
    @classmethod
    def provider_metadata_valid(cls, value):
        if len(value) > 20 or any(not CODE.fullmatch(str(key)) for key in value):
            raise ValueError("provider metadata keys are invalid")
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True)
        if len(encoded.encode("utf-8")) > 4096:
            raise ValueError("provider metadata exceeds 4 KiB")
        return value

    @model_validator(mode="after")
    def evidence_shape(self):
        if self.evidence_type == EvidenceType.photo:
            required = (
                self.original_filename,
                self.media_type,
                self.byte_size,
                self.sha256,
            )
            if any(value is None for value in required):
                raise ValueError("photo requires complete bounded metadata")
        if self.evidence_type == EvidenceType.geolocation and self.latitude is None:
            raise ValueError("geolocation evidence requires coordinates")
        return self


class CreateCorrectiveActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_inspection_version: PositiveInt
    result_id: PositiveInt
    owner_id: PositiveInt
    description: str
    due_date: date

    @field_validator("description")
    @classmethod
    def description_valid(cls, value):
        return bounded_text(value, 5, 4000)


class UpdateCorrectiveActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: PositiveInt
    owner_id: PositiveInt | None = None
    description: str | None = None
    due_date: date | None = None
    status: ActionStatus | None = None

    @field_validator("description")
    @classmethod
    def description_valid(cls, value):
        return bounded_text(value, 5, 4000)

    @model_validator(mode="after")
    def mutable_fields(self):
        changed = self.model_fields_set - {"expected_version"}
        if not changed:
            raise ValueError("one mutable field is required")
        if any(getattr(self, name) is None for name in changed):
            raise ValueError("mutable action fields cannot be null")
        if self.status == ActionStatus.closed:
            raise ValueError("closed status requires the close endpoint")
        return self


class CloseCorrectiveActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: PositiveInt
    closure_reason: str

    @field_validator("closure_reason")
    @classmethod
    def closure_reason_valid(cls, value):
        return bounded_text(value, 5, 4000)


class ReopenCorrectiveActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: PositiveInt
    reopen_reason: str

    @field_validator("reopen_reason")
    @classmethod
    def reopen_reason_valid(cls, value):
        return bounded_text(value, 5, 4000)


class RequestVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_action_version: PositiveInt
    index_code: IndexCode
    minimum_separation_days: Annotated[StrictInt, Field(ge=1, le=30)] = 3


class ResolveVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: PositiveInt
    notes: str | None = None

    @field_validator("notes")
    @classmethod
    def notes_valid(cls, value):
        return bounded_text(value, 3, 2000)
