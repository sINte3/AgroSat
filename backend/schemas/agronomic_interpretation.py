"""Typed response schemas for the aggregate agronomic interpretation endpoint."""
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class InterpretationField(BaseModel):
    id: int
    name: str
    enterprise_id: int
    crop_name: Optional[str] = None
    season_year: Optional[int] = None


class InterpretationRange(BaseModel):
    date_from: date
    date_to: date


class Observation(BaseModel):
    observed_at: date
    value: float
    satellite: Optional[str] = None
    freshness_days: Optional[int] = None
    cloud_cover_pct: Optional[float] = None
    valid_pixels_pct: Optional[float] = None


class PreviousObservation(BaseModel):
    observed_at: date
    value: float


class Change(BaseModel):
    absolute: Optional[float] = None
    direction: str
    days_between: Optional[int] = None
    statistical_signal: str


class Baseline(BaseModel):
    status: str
    point_count: int
    median: Optional[float] = None
    p25: Optional[float] = None
    p75: Optional[float] = None
    mad: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None


class Trend(BaseModel):
    status: str
    direction: str
    observation_count: int
    duration_days: Optional[int] = None
    slope_per_10_days: Optional[float] = None


class Confidence(BaseModel):
    level: str
    reasons: list[str]


class Hypothesis(BaseModel):
    code: str
    label: str
    rationale: str
    supporting_indices: list[str]
    confidence: str
    recommended_checks: list[str]


class IndexInterpretation(BaseModel):
    code: str
    label: str
    meaning: str
    useful_for: list[str]
    data_status: str
    latest_observation: Optional[Observation] = None
    previous_valid_observation: Optional[PreviousObservation] = None
    change: Change
    baseline: Baseline
    trend: Trend
    confidence: Confidence
    hypotheses: list[Hypothesis]
    recommended_checks: list[str]
    limitations: list[str]


class AgronomicInterpretationResponse(BaseModel):
    field: InterpretationField
    range: InterpretationRange
    generated_at: datetime
    overall_confidence: str
    indices: list[IndexInterpretation]
    limitations: list[str]
