"""Backward-compatible typed schemas for contextual agronomic interpretation."""
from datetime import date, datetime
from typing import Literal, Optional
from pydantic import BaseModel


class InterpretationField(BaseModel):
    id: int
    name: str
    field_id: int
    field_name: str
    enterprise_id: int
    crop_name: Optional[str] = None
    season_year: Optional[int] = None
    growth_stage: Optional[str] = None


class InterpretationRange(BaseModel):
    date_from: date
    date_to: date


class Context(BaseModel):
    crop_available: bool
    season_available: bool
    growth_stage_available: bool
    weather_available: bool
    soil_available: bool
    inspection_evidence_available: bool
    missing_context: list[str]


class Observation(BaseModel):
    captured_date: date
    observed_at: date
    value: float
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    valid_pixels_pct: Optional[float] = None
    cloud_cover_pct: Optional[float] = None
    satellite: Optional[str] = None
    freshness_days: Optional[int] = None


class Change(BaseModel):
    absolute: Optional[float] = None
    percent: Optional[float] = None
    direction: str
    days_between: Optional[int] = None
    statistical_signal: str


class Baseline(BaseModel):
    status: Literal["insufficient_history", "available"]
    position_status: Literal["insufficient_history", "within_field_range", "above_field_range", "below_field_range"]
    sample_count: int
    point_count: int
    median: Optional[float] = None
    p25: Optional[float] = None
    p75: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    latest_percentile: Optional[float] = None
    deviation_from_median: Optional[float] = None
    mad: Optional[float] = None


class Trend(BaseModel):
    status: str
    sample_count: int
    observation_count: int
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    duration_days: Optional[int] = None
    slope_per_day: Optional[float] = None
    slope_per_10_days: Optional[float] = None
    direction: str
    strength: str


class Heterogeneity(BaseModel):
    available: bool
    spread: Optional[float] = None
    relative_spread: Optional[float] = None
    classification: str
    evidence: list[str]


class DataQuality(BaseModel):
    observation_count: int
    valid_pixels_pct: Optional[float] = None
    cloud_cover_pct: Optional[float] = None
    freshness_days: Optional[int] = None
    quality_flags: list[str]


class Confidence(BaseModel):
    score: int
    level: Literal["insufficient", "low", "medium", "high"]
    contextual_level: Literal["none", "low", "medium", "high"]
    reasons: list[str]


class Hypothesis(BaseModel):
    code: str
    title: str
    label: str
    reason: str
    rationale: str
    supporting_signals: list[str]
    supporting_indices: list[str]
    contradicting_or_missing_context: list[str]
    confidence: str
    requires_field_check: bool
    recommended_checks: list[str]


class IndexInterpretation(BaseModel):
    index_code: str
    display_name: str
    plain_language_meaning: str
    code: str
    label: str
    meaning: str
    useful_for: list[str]
    data_status: str
    latest: Optional[Observation] = None
    latest_observation: Optional[Observation] = None
    previous: Optional[Observation] = None
    previous_valid_observation: Optional[Observation] = None
    change: Change
    baseline: Baseline
    trend: Trend
    heterogeneity: Heterogeneity
    data_quality: DataQuality
    confidence: Confidence
    hypotheses: list[Hypothesis]
    recommended_checks: list[str]
    limitations: list[str]


class Summary(BaseModel):
    status: str
    confidence: str
    confidence_score: int
    latest_observation_date: Optional[date] = None
    freshness_days: Optional[int] = None
    indices_with_data: int
    indices_with_sufficient_history: int
    primary_signals: list[str]
    recommended_next_checks: list[str]
    disclaimer: str


class AgronomicInterpretationResponse(BaseModel):
    field: InterpretationField
    range: InterpretationRange
    generated_at: datetime
    context: Context
    summary: Summary
    overall_confidence: str
    indices: list[IndexInterpretation]
    limitations: list[str]
