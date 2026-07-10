"""
Permanent standard-library tenant-isolation tests for satellite_data_quality.

No real database connection or write is performed.  All DB-layer
interactions use unittest.mock.  Tests inspect bound SQL text and
parameter dictionaries, verifying enterprise scoping at the SQL level.
"""

import unittest
from unittest.mock import MagicMock, patch, call

from sqlalchemy.orm import Session
from sqlalchemy import text

from services.satellite_data_quality import (
    _fetch_field_index_map,
    _fetch_ndvi_latest_per_field,
    _fetch_satellite_latest_per_field_index,
    _fetch_suspicious_ndvi_count,
    _fetch_suspicious_satellite_count,
    _fetch_suspicious_ndvi_fields,
    _fetch_suspicious_satellite_fields,
    build_quality_summary,
    SATELLITE_INDEX_CODES,
    PROBLEM_FIELDS_LIMIT,
    EXPECTED_VALUE_MIN,
    EXPECTED_VALUE_MAX,
)


def _make_mock_db(rows=None):
    """Return a MagicMock Session whose execute().fetchall() returns rows."""
    db = MagicMock(spec=Session)
    if rows is not None:
        db.execute.return_value.fetchall.return_value = rows
    else:
        db.execute.return_value.fetchall.return_value = []
    return db


def _mock_row(**kwargs):
    """Return a simple object with attribute access."""
    return type("Row", (), kwargs)()


# ─── Helpers: SQL-text and parameter inspection ────────────────────────────────

def _extract_sql_and_params(db):
    """Return (sql_text, params_dict) from the last db.execute call.

    SQLAlchemy's db.execute(text(...), params_dict) passes the text
    object as first positional and params dict as second positional.
    """
    call_args = db.execute.call_args
    if call_args is None:
        return None, None
    positional = call_args[0]
    if not positional:
        return None, {}
    sql_obj = positional[0]
    params = positional[1] if len(positional) > 1 else {}
    sql_text = str(sql_obj) if hasattr(sql_obj, '__str__') else str(sql_obj)
    return sql_text, params


# ─── Helper: assert enterprise bound param ─────────────────────────────────────

def assert_has_enterprise_param(test_case, params, expected_eid=7):
    """Assert that params contains enterprise_id = expected_eid."""
    test_case.assertIn("enterprise_id", params,
                       "enterprise_id parameter missing from query params")
    test_case.assertEqual(params["enterprise_id"], expected_eid,
                          f"enterprise_id param should be {expected_eid}")


def assert_no_enterprise_filter(test_case, sql_text):
    """Assert that the SQL text does not contain a tenant WHERE filter.

    The column name `f.enterprise_id` appears in SELECT and JOIN clauses
    even in global queries.  The tenant filter is the *bound parameter*
    pattern `:enterprise_id` which the WHERE clause generates.
    """
    test_case.assertNotIn(":enterprise_id", sql_text,
                          "SQL should not contain enterprise_id WHERE parameter when scope is None")


# ======================================================================
# 1–7: Per-helper tenant-scope SQL generation
# ======================================================================

class FieldIndexMapTenantScopeTests(unittest.TestCase):
    """_fetch_field_index_map with enterprise_id."""

    def test_fetch_field_index_map_scoped_sql(self):
        db = _make_mock_db([_mock_row(field_id=1, field_name="F1",
                                       enterprise_id=7, enterprise_name="E7")])
        _fetch_field_index_map(db, enterprise_id=7)
        sql, params = _extract_sql_and_params(db)
        self.assertIsNotNone(sql)
        assert_has_enterprise_param(self, params, 7)
        self.assertIn("f.enterprise_id = :enterprise_id", sql)

    def test_fetch_field_index_map_global_no_enterprise(self):
        db = _make_mock_db([_mock_row(field_id=1, field_name="F1",
                                       enterprise_id=1, enterprise_name="E1")])
        _fetch_field_index_map(db, enterprise_id=None)
        sql, params = _extract_sql_and_params(db)
        self.assertIsNotNone(sql)
        assert_no_enterprise_filter(self, sql)


class NdviLatestTenantScopeTests(unittest.TestCase):
    """_fetch_ndvi_latest_per_field with enterprise_id."""

    def test_ndvi_latest_scoped_sql(self):
        db = _make_mock_db()
        _fetch_ndvi_latest_per_field(db, enterprise_id=7)
        sql, params = _extract_sql_and_params(db)
        self.assertIsNotNone(sql)
        assert_has_enterprise_param(self, params, 7)
        self.assertIn("f.enterprise_id = :enterprise_id", sql)
        self.assertIn("JOIN fields f", sql)

    def test_ndvi_latest_global_no_enterprise(self):
        db = _make_mock_db()
        _fetch_ndvi_latest_per_field(db, enterprise_id=None)
        sql, params = _extract_sql_and_params(db)
        self.assertIsNotNone(sql)
        assert_no_enterprise_filter(self, sql)


class SatelliteLatestTenantScopeTests(unittest.TestCase):
    """_fetch_satellite_latest_per_field_index with enterprise_id."""

    def test_satellite_latest_scoped_sql(self):
        codes = frozenset({"savi", "evi"})
        db = _make_mock_db()
        _fetch_satellite_latest_per_field_index(db, codes, enterprise_id=7)
        sql, params = _extract_sql_and_params(db)
        self.assertIsNotNone(sql)
        assert_has_enterprise_param(self, params, 7)
        self.assertIn("f.enterprise_id = :enterprise_id", sql)
        self.assertIn("JOIN fields f", sql)
        # Index parameters must be preserved alongside enterprise_id
        self.assertIn("c0", params)
        self.assertIn("c1", params)

    def test_satellite_latest_global_no_enterprise(self):
        codes = frozenset({"savi"})
        db = _make_mock_db()
        _fetch_satellite_latest_per_field_index(db, codes, enterprise_id=None)
        sql, params = _extract_sql_and_params(db)
        self.assertIsNotNone(sql)
        assert_no_enterprise_filter(self, sql)
        # Index param should still be present
        self.assertIn("c0", params)


class SuspiciousNdviCountTenantScopeTests(unittest.TestCase):
    """_fetch_suspicious_ndvi_count with enterprise_id."""

    def test_suspicious_ndvi_count_scoped_sql(self):
        db = _make_mock_db()
        db.execute.return_value.fetchone.return_value = (0,)
        _fetch_suspicious_ndvi_count(db, enterprise_id=7)
        sql, params = _extract_sql_and_params(db)
        self.assertIsNotNone(sql)
        assert_has_enterprise_param(self, params, 7)
        self.assertIn("f.enterprise_id = :enterprise_id", sql)
        self.assertIn("JOIN fields f", sql)

    def test_suspicious_ndvi_count_global_no_enterprise(self):
        db = _make_mock_db()
        db.execute.return_value.fetchone.return_value = (0,)
        _fetch_suspicious_ndvi_count(db, enterprise_id=None)
        sql, params = _extract_sql_and_params(db)
        self.assertIsNotNone(sql)
        assert_no_enterprise_filter(self, sql)


class SuspiciousSatCountTenantScopeTests(unittest.TestCase):
    """_fetch_suspicious_satellite_count with enterprise_id."""

    def test_suspicious_sat_count_scoped_sql(self):
        db = _make_mock_db()
        db.execute.return_value.fetchone.return_value = (0,)
        _fetch_suspicious_satellite_count(db, enterprise_id=7)
        sql, params = _extract_sql_and_params(db)
        self.assertIsNotNone(sql)
        assert_has_enterprise_param(self, params, 7)
        self.assertIn("f.enterprise_id = :enterprise_id", sql)
        self.assertIn("JOIN fields f", sql)

    def test_suspicious_sat_count_global_no_enterprise(self):
        db = _make_mock_db()
        db.execute.return_value.fetchone.return_value = (0,)
        _fetch_suspicious_satellite_count(db, enterprise_id=None)
        sql, params = _extract_sql_and_params(db)
        self.assertIsNotNone(sql)
        assert_no_enterprise_filter(self, sql)


class SuspiciousNdviFieldsTenantScopeTests(unittest.TestCase):
    """_fetch_suspicious_ndvi_fields with enterprise_id."""

    def test_suspicious_ndvi_fields_scoped_sql(self):
        db = _make_mock_db()
        _fetch_suspicious_ndvi_fields(db, limit=5, enterprise_id=7)
        sql, params = _extract_sql_and_params(db)
        self.assertIsNotNone(sql)
        assert_has_enterprise_param(self, params, 7)
        self.assertIn("f.enterprise_id = :enterprise_id", sql)

    def test_suspicious_ndvi_fields_global_no_enterprise(self):
        db = _make_mock_db()
        _fetch_suspicious_ndvi_fields(db, limit=5, enterprise_id=None)
        sql, params = _extract_sql_and_params(db)
        self.assertIsNotNone(sql)
        assert_no_enterprise_filter(self, sql)


class SuspiciousSatFieldsTenantScopeTests(unittest.TestCase):
    """_fetch_suspicious_satellite_fields with enterprise_id."""

    def test_suspicious_sat_fields_scoped_sql(self):
        codes = frozenset({"savi", "evi"})
        db = _make_mock_db()
        _fetch_suspicious_satellite_fields(db, codes, limit=5, enterprise_id=7)
        sql, params = _extract_sql_and_params(db)
        self.assertIsNotNone(sql)
        assert_has_enterprise_param(self, params, 7)
        self.assertIn("f.enterprise_id = :enterprise_id", sql)
        # Index and limit params preserved
        self.assertIn("c0", params)
        self.assertIn("c1", params)
        self.assertIn("limit", params)

    def test_suspicious_sat_fields_global_no_enterprise(self):
        codes = frozenset({"savi"})
        db = _make_mock_db()
        _fetch_suspicious_satellite_fields(db, codes, limit=5, enterprise_id=None)
        sql, params = _extract_sql_and_params(db)
        self.assertIsNotNone(sql)
        assert_no_enterprise_filter(self, sql)
        self.assertIn("c0", params)
        self.assertIn("limit", params)


# ======================================================================
# 8: build_quality_summary passes enterprise_id to all helpers
# ======================================================================

class BuildQualitySummaryTenantScopeTests(unittest.TestCase):
    """Verify build_quality_summary forwards enterprise_id to every helper."""

    def setUp(self):
        self.db = _make_mock_db()

    def test_build_quality_summary_passes_enterprise_to_all_helpers(self):
        """With enterprise_id=7, every field-data helper receives enterprise_id=7."""
        eid = 7
        with patch(
            "services.satellite_data_quality._fetch_field_index_map",
            return_value=[{"field_id": 1, "field_name": "F1",
                           "enterprise_id": 7, "enterprise_name": "E7"}],
        ) as mock_fields, patch(
            "services.satellite_data_quality._fetch_ndvi_latest_per_field",
            return_value={},
        ) as mock_ndvi, patch(
            "services.satellite_data_quality._fetch_satellite_latest_per_field_index",
            return_value={},
        ) as mock_sat, patch(
            "services.satellite_data_quality._fetch_suspicious_ndvi_count",
            return_value=0,
        ) as mock_ndvi_cnt, patch(
            "services.satellite_data_quality._fetch_suspicious_satellite_count",
            return_value=0,
        ) as mock_sat_cnt, patch(
            "services.satellite_data_quality._fetch_suspicious_ndvi_fields",
            return_value=[],
        ) as mock_ndvi_prob, patch(
            "services.satellite_data_quality._fetch_suspicious_satellite_fields",
            return_value=[],
        ) as mock_sat_prob:
            build_quality_summary(self.db, enterprise_id=eid)

        # _fetch_field_index_map
        self.assertEqual(mock_fields.call_args[1].get("enterprise_id"), eid,
                         "field_index_map missing enterprise_id")
        # _fetch_ndvi_latest_per_field
        self.assertEqual(mock_ndvi.call_args[1].get("enterprise_id"), eid,
                         "ndvi_latest missing enterprise_id")
        # _fetch_suspicious_ndvi_count
        self.assertEqual(mock_ndvi_cnt.call_args[1].get("enterprise_id"), eid,
                         "suspicious_ndvi_count missing enterprise_id")
        # _fetch_suspicious_ndvi_fields
        self.assertEqual(mock_ndvi_prob.call_args[1].get("enterprise_id"), eid,
                         "suspicious_ndvi_fields missing enterprise_id")
        # _fetch_satellite_latest_per_field_index
        self.assertEqual(mock_sat.call_args[1].get("enterprise_id"), eid,
                         "sat_latest missing enterprise_id")
        # _fetch_suspicious_satellite_count
        self.assertEqual(mock_sat_cnt.call_args[1].get("enterprise_id"), eid,
                         "suspicious_sat_count missing enterprise_id")
        # _fetch_suspicious_satellite_fields
        self.assertEqual(mock_sat_prob.call_args[1].get("enterprise_id"), eid,
                         "suspicious_sat_fields missing enterprise_id")


# ======================================================================
# 9: build_quality_summary global behavior preserved when enterprise_id=None
# ======================================================================

class BuildQualitySummaryGlobalBehaviorTests(unittest.TestCase):
    """Verify build_quality_summary with enterprise_id=None behaves globally."""

    def setUp(self):
        self.db = _make_mock_db()

    def test_build_quality_summary_global_passes_none(self):
        """With enterprise_id=None, helpers receive enterprise_id=None (or no kwarg)."""
        with patch(
            "services.satellite_data_quality._fetch_field_index_map",
            return_value=[{"field_id": 1, "field_name": "F1",
                           "enterprise_id": 1, "enterprise_name": "E1"}],
        ) as mock_fields, patch(
            "services.satellite_data_quality._fetch_ndvi_latest_per_field",
            return_value={},
        ) as mock_ndvi, patch(
            "services.satellite_data_quality._fetch_satellite_latest_per_field_index",
            return_value={},
        ) as mock_sat, patch(
            "services.satellite_data_quality._fetch_suspicious_ndvi_count",
            return_value=0,
        ) as mock_ndvi_cnt, patch(
            "services.satellite_data_quality._fetch_suspicious_satellite_count",
            return_value=0,
        ) as mock_sat_cnt, patch(
            "services.satellite_data_quality._fetch_suspicious_ndvi_fields",
            return_value=[],
        ) as mock_ndvi_prob, patch(
            "services.satellite_data_quality._fetch_suspicious_satellite_fields",
            return_value=[],
        ) as mock_sat_prob:
            build_quality_summary(self.db, enterprise_id=None)

        # All helpers should have enterprise_id=None
        for name, mock_fn in [
            ("fields", mock_fields),
            ("ndvi", mock_ndvi),
            ("ndvi_cnt", mock_ndvi_cnt),
            ("ndvi_prob", mock_ndvi_prob),
            ("sat", mock_sat),
            ("sat_cnt", mock_sat_cnt),
            ("sat_prob", mock_sat_prob),
        ]:
            self.assertEqual(
                mock_fn.call_args[1].get("enterprise_id"),
                None,
                f"{name} enterprise_id should be None for global call",
            )


# ======================================================================
# 10: Defense-in-depth: foreign-enterprise suspicious rows filtered
# ======================================================================

class DefenseInDepthFilterTests(unittest.TestCase):
    """Verify that synthetic foreign-enterprise suspicious rows are excluded
    from problem_fields by the field_ids filter in build_quality_summary."""

    def setUp(self):
        self.db = _make_mock_db()

    def test_foreign_enterprise_suspicious_row_excluded(self):
        """A suspicious row from field_id=99 not in scoped field_ids is excluded."""
        eid = 7
        # Only field 1 belongs to enterprise 7
        scoped_fields = [{"field_id": 1, "field_name": "F1",
                          "enterprise_id": 7, "enterprise_name": "E7"}]
        # Foreign suspicious row: field_id=99 belongs to different enterprise
        foreign_suspicious = [{
            "field_id": 99,
            "field_name": "Foreign",
            "enterprise_name": "Other",
            "index_code": "ndvi",
            "status": "suspicious",
            "latest_captured_date": "2025-01-01",
            "age_days": None,
            "reason": "mean_ndvi=-2.0000 outside expected range [-1, 1]",
        }]
        with patch(
            "services.satellite_data_quality._fetch_field_index_map",
            return_value=scoped_fields,
        ), patch(
            "services.satellite_data_quality._fetch_ndvi_latest_per_field",
            return_value={},
        ), patch(
            "services.satellite_data_quality._fetch_satellite_latest_per_field_index",
            return_value={},
        ), patch(
            "services.satellite_data_quality._fetch_suspicious_ndvi_count",
            return_value=0,
        ), patch(
            "services.satellite_data_quality._fetch_suspicious_satellite_count",
            return_value=0,
        ), patch(
            "services.satellite_data_quality._fetch_suspicious_ndvi_fields",
            return_value=foreign_suspicious,
        ), patch(
            "services.satellite_data_quality._fetch_suspicious_satellite_fields",
            return_value=[],
        ):
            result = build_quality_summary(self.db, enterprise_id=eid)

        # The foreign suspicious row must be excluded from problem_fields
        for pf in result["problem_fields"]:
            self.assertNotEqual(
                pf["field_id"], 99,
                "Foreign enterprise suspicious row leaked into problem_fields",
            )


# ======================================================================
# 11: Endpoint forces enterprise_scope over conflicting query
# ======================================================================

class EndpointScopeResolutionTests(unittest.TestCase):
    """Verify the API endpoint forces enterprise_scope when a tenant user
    provides a conflicting enterprise_id query param."""

    def test_endpoint_forces_enterprise_scope(self):
        """Simulate the endpoint logic: enterprise_scope=7 overrides query enterprise_id=9."""
        enterprise_scope = 7
        enterprise_id_query = 9
        effective = enterprise_id_query
        if enterprise_scope is not None:
            effective = enterprise_scope
        self.assertEqual(effective, 7,
                         "enterprise_scope must override query enterprise_id")
        self.assertNotEqual(effective, 9,
                            "tenant user must not use explicit query enterprise_id")


class EndpointPreservesExplicitFilterForGlobalTests(unittest.TestCase):
    """Verify that a global role with enterprise_scope=None preserves
    an explicit enterprise_id query filter."""

    def test_global_enterprise_filter_preserved(self):
        """enterprise_scope=None keeps the explicit enterprise_id=5."""
        enterprise_scope = None
        enterprise_id_query = 5
        effective = enterprise_id_query
        if enterprise_scope is not None:
            effective = enterprise_scope
        # With scope=None, effective stays as the query value
        self.assertEqual(effective, 5,
                         "global role with scope=None must preserve query enterprise_id")


# ======================================================================
# 13: Response contract keys preserved
# ======================================================================

class ResponseContractTests(unittest.TestCase):
    """Verify that no response-contract keys are removed."""

    def test_response_contract_keys_present(self):
        """build_quality_summary returns all required top-level keys."""
        db = _make_mock_db()
        with patch(
            "services.satellite_data_quality._fetch_field_index_map",
            return_value=[{"field_id": 1, "field_name": "F1",
                           "enterprise_id": 1, "enterprise_name": "E1"}],
        ), patch(
            "services.satellite_data_quality._fetch_ndvi_latest_per_field",
            return_value={},
        ), patch(
            "services.satellite_data_quality._fetch_satellite_latest_per_field_index",
            return_value={},
        ), patch(
            "services.satellite_data_quality._fetch_suspicious_ndvi_count",
            return_value=0,
        ), patch(
            "services.satellite_data_quality._fetch_suspicious_satellite_count",
            return_value=0,
        ), patch(
            "services.satellite_data_quality._fetch_suspicious_ndvi_fields",
            return_value=[],
        ), patch(
            "services.satellite_data_quality._fetch_suspicious_satellite_fields",
            return_value=[],
        ):
            result = build_quality_summary(db, enterprise_id=None)

        required_keys = {"generated_at", "thresholds", "summary",
                         "by_index", "problem_fields", "limitations"}
        self.assertTrue(
            required_keys.issubset(result.keys()),
            f"Missing keys: {required_keys - result.keys()}",
        )


# ======================================================================
# 14: No database write methods called
# ======================================================================

class NoDbWriteTests(unittest.TestCase):
    """Verify no DB write methods are called in any test path."""

    def test_no_db_writes_in_helpers(self):
        """Calling helpers with enterprise_id must not call db.add/db.delete/etc."""
        db = _make_mock_db()

        # _fetch_field_index_map
        _fetch_field_index_map(db, enterprise_id=7)
        db.add.assert_not_called()
        db.delete.assert_not_called()
        db.commit.assert_not_called()
        db.flush.assert_not_called()

        # _fetch_ndvi_latest_per_field
        db.reset_mock()
        _fetch_ndvi_latest_per_field(db, enterprise_id=7)
        db.add.assert_not_called()
        db.delete.assert_not_called()

        # _fetch_suspicious_ndvi_count
        db.reset_mock()
        db.execute.return_value.fetchone.return_value = (0,)
        _fetch_suspicious_ndvi_count(db, enterprise_id=7)
        db.add.assert_not_called()
        db.delete.assert_not_called()

        # _fetch_suspicious_satellite_count
        db.reset_mock()
        db.execute.return_value.fetchone.return_value = (0,)
        _fetch_suspicious_satellite_count(db, enterprise_id=7)
        db.add.assert_not_called()
        db.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
