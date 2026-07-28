from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


PositiveInt = Annotated[StrictInt, Field(gt=0)]


class PixelAnomalyIndex(str, Enum):
    ndvi = "ndvi"
    savi = "savi"
    evi = "evi"
    ndmi = "ndmi"
    ndre = "ndre"


class PixelAnomalyClassification(str, Enum):
    single_scene = "single_scene"
    persistent = "persistent"
    recovering = "recovering"


class PixelAnomalyStatus(str, Enum):
    open = "open"
    inspection_created = "inspection_created"
    dismissed = "dismissed"
    resolved = "resolved"


class PixelAnomalyField(BaseModel):
    id: int
    name: str
    enterprise_id: int
    enterprise_name: str


class PixelAnomalyInspection(BaseModel):
    id: int
    status: str
    assigned_to_id: int | None
    due_date: date | None


class PixelAnomalyListItem(BaseModel):
    id: int
    field: PixelAnomalyField
    index_code: PixelAnomalyIndex
    current_observation_date: date
    comparison_observation_date: date | None
    area_ha: float
    score: float
    severity: str
    persistence_count: int
    classification: PixelAnomalyClassification
    confidence: float
    status: PixelAnomalyStatus
    created_at: datetime
    inspection: PixelAnomalyInspection | None


class PixelAnomalyListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[PixelAnomalyListItem]


class PixelAnomalySummaryResponse(BaseModel):
    field: PixelAnomalyField
    total: int
    open: int
    inspection_created: int
    persistent: int
    recovering: int
    latest_observation_date: date | None
    latest_confidence: float | None
    insufficient_data_runs: int


class PixelAnomalyDetail(PixelAnomalyListItem):
    algorithm_version: str
    run_key: str
    threshold_hash: str
    thresholds: dict[str, Any]
    quality_summary: dict[str, Any]
    provenance: dict[str, Any]
    reason_codes: list[str]


class PixelAnomalyGeometryResponse(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: int
    geometry: dict[str, Any]
    properties: dict[str, Any]


def _strip(value: str | None, minimum: int, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not minimum <= len(normalized) <= maximum:
        raise ValueError(f"length must be {minimum}..{maximum}")
    return normalized


class CreateInspectionFromAnomalyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assigned_to_id: PositiveInt | None = None
    title: str | None = None
    instructions: str | None = None
    due_date: date | None = None

    @field_validator("title")
    @classmethod
    def title_valid(cls, value):
        return _strip(value, 3, 255)

    @field_validator("instructions")
    @classmethod
    def instructions_valid(cls, value):
        return _strip(value, 3, 2000)


class PixelAnomalyInspectionResponse(BaseModel):
    created: bool
    anomaly_id: int
    anomaly_status: PixelAnomalyStatus
    inspection: PixelAnomalyInspection
