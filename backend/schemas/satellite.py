"""
Pydantic schemas for satellite index coverage endpoint.
"""

from datetime import date
from typing import Optional

from pydantic import BaseModel


class IndexCoverageDetail(BaseModel):
    """Per-index coverage detail for a field."""
    has_data: bool
    record_count: int
    date_count: int
    latest_captured_date: Optional[str] = None
    latest_mean_value: Optional[float] = None


class FieldCoverageItem(BaseModel):
    """Coverage info for one field."""
    field_id: int
    field_name: str
    field_code: Optional[str] = None
    enterprise_id: int
    enterprise_name: str
    area_ha: Optional[float] = None
    is_active: bool
    has_any_data: bool
    has_all_requested_indices: bool
    coverage_status: str  # none | partial | complete
    freshness_status: str  # no_data | fresh | stale | future_date
    latest_captured_date: Optional[str] = None
    days_since_latest: Optional[int] = None
    record_count_total: int
    date_count_total: int
    indices: dict[str, IndexCoverageDetail]


class IndexSummaryDetail(BaseModel):
    """Aggregate summary for one index code."""
    fields_with_data: int
    record_count: int
    latest_captured_date: Optional[str] = None


class CoverageSummary(BaseModel):
    """Top-level summary."""
    fields_total: int
    fields_with_any_data: int
    fields_without_data: int
    fields_with_all_requested_indices: int
    fields_with_partial_indices: int
    fields_stale: int
    latest_captured_date: Optional[str] = None
    record_count_total: int
    index_summary: dict[str, IndexSummaryDetail]


class CoverageFilters(BaseModel):
    """Reflected query parameters."""
    enterprise_id: Optional[int] = None
    field_ids: list[int] = []
    index_codes: list[str]
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    date_to_mode: str = "inclusive"
    active_only: bool = True
    include_empty: bool = True
    stale_after_days: int = 10
    as_of: str


class CoverageResponse(BaseModel):
    """Full coverage response."""
    filters: CoverageFilters
    summary: CoverageSummary
    fields: list[FieldCoverageItem]
