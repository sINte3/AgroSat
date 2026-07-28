"""High-value history, coverage, and export bounds for TASK_209."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.ndvi import get_ndvi_history
from api.query_bounds import FIELD_LIST_ROW_CAP, SATELLITE_HISTORY_ROW_CAP
from api.reports import download_enterprise_pdf
from api.satellite_indices import (
    MAX_REQUESTED_FIELD_IDS,
    get_coverage,
    get_history,
)


class EmptyResult:
    @staticmethod
    def fetchall():
        return []


class RecordingSession:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return EmptyResult()


class RowResult:
    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class SequenceSession:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return self.results.pop(0)


def auth_field():
    return SimpleNamespace(name="Bounded field")


def user(role="admin", enterprise_id=None):
    return SimpleNamespace(role=role, enterprise_id=enterprise_id)


@pytest.mark.parametrize("days", [0, SATELLITE_HISTORY_ROW_CAP + 1])
def test_legacy_ndvi_history_rejects_unbounded_days(days):
    db = RecordingSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            get_ndvi_history(
                field_id=4,
                days=days,
                include_cloudy=False,
                db=db,
                _auth_field=auth_field(),
            )
        )

    assert exc.value.status_code == 422
    assert db.calls == []


def test_legacy_ndvi_history_query_has_explicit_limit():
    db = RecordingSession()
    result = asyncio.run(
        get_ndvi_history(
            field_id=4,
            days=90,
            include_cloudy=False,
            db=db,
            _auth_field=auth_field(),
        )
    )

    statement, params = db.calls[0]
    assert "LIMIT :row_limit" in statement
    assert params["row_limit"] == SATELLITE_HISTORY_ROW_CAP + 1
    assert result["records"] == []


def test_multi_index_history_query_has_explicit_limit():
    db = RecordingSession()
    result = asyncio.run(
        get_history(
            field_id=4,
            index_code="savi",
            days=90,
            include_cloudy=False,
            db=db,
            _auth_field=auth_field(),
        )
    )

    statement, params = db.calls[0]
    assert "LIMIT :row_limit" in statement
    assert params["row_limit"] == SATELLITE_HISTORY_ROW_CAP + 1
    assert result["records"] == []


def test_coverage_field_scope_query_has_explicit_limit():
    db = RecordingSession()
    result = asyncio.run(
        get_coverage(
            enterprise_id=None,
            field_ids=None,
            index_codes=None,
            date_from=None,
            date_to=None,
            active_only=True,
            include_empty=True,
            stale_after_days=10,
            as_of="2026-07-28",
            db=db,
            current_user=user(),
        )
    )

    statement, params = db.calls[0]
    assert "LIMIT :row_limit" in statement
    assert params["row_limit"] == FIELD_LIST_ROW_CAP + 1
    assert result.summary.fields_total == 0


def test_coverage_rejects_excessive_field_id_filter_before_query():
    db = RecordingSession()
    field_ids = ",".join(str(value) for value in range(1, MAX_REQUESTED_FIELD_IDS + 2))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            get_coverage(
                enterprise_id=None,
                field_ids=field_ids,
                index_codes=None,
                date_from=None,
                date_to=None,
                active_only=True,
                include_empty=True,
                stale_after_days=10,
                as_of="2026-07-28",
                db=db,
                current_user=user(),
            )
        )

    assert exc.value.status_code == 422
    assert db.calls == []


def test_enterprise_report_fails_before_render_when_field_export_overflows():
    enterprise = SimpleNamespace(id=17, name="Enterprise", code="E", region="B")
    db = SequenceSession(
        [
            RowResult(row=enterprise),
            RowResult(rows=[None] * (FIELD_LIST_ROW_CAP + 1)),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        download_enterprise_pdf(
            enterprise_id=17,
            db=db,
            current_user=user(),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["resource"] == "enterprise_report_fields"
    assert len(db.calls) == 2
