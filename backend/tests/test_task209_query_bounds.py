"""Bounded legacy collection regression tests for TASK_209."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from api.fields import get_all_fields_geojson, get_fields
from api.query_bounds import (
    ALERT_EXPORT_ROW_CAP,
    ENTERPRISE_LIST_ROW_CAP,
    FIELD_LIST_ROW_CAP,
    GEOJSON_FIELD_ROW_CAP,
    SATELLITE_HISTORY_ROW_CAP,
    ensure_within_row_cap,
    fetch_limit,
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


def test_caps_are_finite_and_fetch_one_overflow_sentinel():
    assert 0 < ENTERPRISE_LIST_ROW_CAP < FIELD_LIST_ROW_CAP
    assert 0 < GEOJSON_FIELD_ROW_CAP <= FIELD_LIST_ROW_CAP
    assert 0 < ALERT_EXPORT_ROW_CAP <= FIELD_LIST_ROW_CAP
    assert 0 < SATELLITE_HISTORY_ROW_CAP <= FIELD_LIST_ROW_CAP
    assert fetch_limit(FIELD_LIST_ROW_CAP) == FIELD_LIST_ROW_CAP + 1


def test_overflow_is_explicit_and_not_silently_truncated():
    rows = [None] * (GEOJSON_FIELD_ROW_CAP + 1)
    with pytest.raises(HTTPException) as exc:
        ensure_within_row_cap(
            rows,
            row_cap=GEOJSON_FIELD_ROW_CAP,
            resource="field_geojson",
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == {
        "code": "result_too_large",
        "resource": "field_geojson",
        "row_cap": GEOJSON_FIELD_ROW_CAP,
    }


@pytest.mark.parametrize(
    ("endpoint", "expected_cap", "cache_prefix"),
    [
        (get_fields, FIELD_LIST_ROW_CAP, "fields:list:v2:"),
        (get_all_fields_geojson, GEOJSON_FIELD_ROW_CAP, "fields:geojson:v2:"),
    ],
)
def test_field_collections_use_sql_limit_and_versioned_cache(
    endpoint,
    expected_cap,
    cache_prefix,
):
    db = RecordingSession()
    with (
        patch("api.fields.cache_get", return_value=None) as cache_get,
        patch("api.fields.cache_set"),
    ):
        result = endpoint(enterprise_id=17, db=db, _scope=17)

    statement, params = db.calls[0]
    assert "LIMIT :row_limit" in statement
    assert params["row_limit"] == expected_cap + 1
    assert cache_get.call_args.args[0].startswith(cache_prefix)
    if endpoint is get_fields:
        assert result == []
    else:
        assert result == {"type": "FeatureCollection", "features": [], "total": 0}


def test_field_caps_do_not_depend_on_row_contents():
    row = SimpleNamespace(id=1)
    ensure_within_row_cap(
        [row] * FIELD_LIST_ROW_CAP,
        row_cap=FIELD_LIST_ROW_CAP,
        resource="fields",
    )
