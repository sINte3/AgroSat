"""Shared hard bounds for legacy collection responses.

These guards preserve the existing response shapes while preventing an
unbounded database result from being serialized. Larger spatial workloads
must use the bounded spatial delivery contract introduced by TASK_209.
"""

from fastapi import HTTPException, status


ENTERPRISE_LIST_ROW_CAP = 1000
FIELD_LIST_ROW_CAP = 5000
GEOJSON_FIELD_ROW_CAP = 1000
ALERT_EXPORT_ROW_CAP = 5000
SATELLITE_HISTORY_ROW_CAP = 3650


def fetch_limit(row_cap: int) -> int:
    """Fetch one sentinel row so overflow is detected without a count query."""
    return row_cap + 1


def ensure_within_row_cap(rows, *, row_cap: int, resource: str) -> None:
    """Fail explicitly instead of silently truncating an oversized response."""
    if len(rows) > row_cap:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "result_too_large",
                "resource": resource,
                "row_cap": row_cap,
            },
        )
