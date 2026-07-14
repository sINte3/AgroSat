"""Offline regression tests for the TASK_191 aggregate interpretation read model."""

import math
import os
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.agronomic_interpretation import (  # noqa: E402
    build_index_interpretation,
    percentile,
)


def row(day, value, identifier=1, **quality):
    return SimpleNamespace(
        id=identifier,
        captured_date=day,
        value=value,
        satellite=quality.get("satellite", "Sentinel-2"),
        cloud_cover_pct=quality.get("cloud_cover_pct"),
        valid_pixels_pct=quality.get("valid_pixels_pct"),
    )


class AgronomicInterpretationCalculationTests(unittest.TestCase):
    def test_percentiles_and_mad_are_deterministic(self):
        self.assertEqual(percentile([1, 2, 3, 4], 0.25), 1.75)
        result = build_index_interpretation(
            "ndvi", [row(date(2026, 1, day), value, day) for day, value in
                     [(1, .2), (4, .3), (7, .4), (10, .5)]], date(2026, 1, 12)
        )
        self.assertEqual(result["baseline"], {
            "status": "available", "point_count": 3, "median": .3,
            "p25": .25, "p75": .35, "mad": .1, "minimum": .2, "maximum": .4,
        })

    def test_latest_and_previous_use_distinct_dates(self):
        result = build_index_interpretation("ndvi", [
            row(date(2026, 1, 1), .2, 1), row(date(2026, 1, 4), .3, 2),
            row(date(2026, 1, 4), .9, 3), row(date(2026, 1, 8), .5, 4),
        ], date(2026, 1, 9))
        self.assertEqual(result["latest_observation"]["observed_at"], date(2026, 1, 8))
        self.assertEqual(result["previous_valid_observation"]["observed_at"], date(2026, 1, 4))
        self.assertEqual(result["previous_valid_observation"]["value"], .3)

    def test_signal_and_trend_rules(self):
        observations = [row(date(2026, 1, 1 + day * 5), value, day)
                        for day, value in enumerate([.2, .21, .22, .23, .24, .8])]
        result = build_index_interpretation("ndvi", observations, date(2026, 2, 1))
        self.assertEqual(result["change"]["statistical_signal"], "strong")
        self.assertEqual(result["trend"]["direction"], "rising")
        self.assertEqual(result["trend"]["observation_count"], 6)

    def test_no_data_one_point_and_quality_honesty(self):
        no_data = build_index_interpretation("ndvi", [], date(2026, 1, 1))
        self.assertEqual(no_data["data_status"], "no_data")
        one = build_index_interpretation("savi", [row(date(2026, 1, 1), .2)], date(2026, 1, 2))
        self.assertEqual(one["data_status"], "insufficient_history")
        self.assertIn("quality_metrics_unavailable_for_this_index", one["confidence"]["reasons"])

    def test_all_numbers_are_finite_and_unsafe_claims_are_absent(self):
        response = build_index_interpretation("ndmi", [
            row(date(2026, 1, 1), .1, 1), row(date(2026, 1, 5), .2, 2),
            row(date(2026, 1, 9), .3, 3), row(date(2026, 1, 13), .5, 4),
        ], date(2026, 1, 14))
        def visit(value):
            if isinstance(value, dict):
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)
            elif isinstance(value, float):
                self.assertTrue(math.isfinite(value))
        visit(response)
        text = str(response).lower()
        for unsafe in ("срочно полить", "немедленно полить", "внести азот", "обнаружена болезнь"):
            self.assertNotIn(unsafe, text)


class AgronomicInterpretationContractTests(unittest.TestCase):
    def test_metadata_order_is_deterministic(self):
        from services.agronomic_interpretation import INDEX_CODES
        self.assertEqual(INDEX_CODES, ("ndvi", "savi", "evi", "ndmi", "ndre"))

    def test_route_is_registered_in_openapi_without_network(self):
        os.environ.setdefault("SECRET_KEY", "task191_test_only_secret_key_0123456789abcdef")
        with patch("redis.Redis.from_url", return_value=MagicMock()):
            from main import app
        self.assertIn("/api/agronomic-interpretation/fields/{field_id}", app.openapi()["paths"])

    def test_authorized_lookup_applies_tenant_scope_and_hides_foreign_fields(self):
        from api.agronomic_interpretation import _field_context
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = None
        tenant = SimpleNamespace(role="viewer", enterprise_id=8)
        with self.assertRaisesRegex(Exception, "Field not found"):
            _field_context(db, 99, tenant, 2026)
        sql, params = db.execute.call_args.args
        self.assertIn("f.enterprise_id = :enterprise_id", str(sql))
        self.assertEqual(params["enterprise_id"], 8)

    def test_global_role_uses_the_existing_unrestricted_convention(self):
        from api.agronomic_interpretation import _field_context
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = SimpleNamespace(id=2, name="Synthetic", enterprise_id=9, crop_name=None, season_year=None)
        _field_context(db, 2, SimpleNamespace(role="manager", enterprise_id=None), 2026)
        sql, params = db.execute.call_args.args
        self.assertNotIn("f.enterprise_id = :enterprise_id", str(sql))
        self.assertNotIn("enterprise_id", params)

    def test_range_validation_and_query_budget_are_read_only(self):
        from api.agronomic_interpretation import get_agronomic_interpretation
        user = SimpleNamespace(role="admin", enterprise_id=None)
        db = MagicMock()
        db.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value=SimpleNamespace(id=1, name="Synthetic", enterprise_id=2, crop_name=None, season_year=None))),
            MagicMock(fetchall=MagicMock(return_value=[])),
            MagicMock(fetchall=MagicMock(return_value=[])),
        ]
        payload = get_agronomic_interpretation(1, date(2026, 1, 1), date(2026, 1, 2), db, user)
        self.assertEqual([item["code"] for item in payload["indices"]], ["ndvi", "savi", "evi", "ndmi", "ndre"])
        self.assertEqual(db.execute.call_count, 3)
        self.assertFalse(any(word in " ".join(str(call) for call in db.execute.call_args_list).lower() for word in ("insert ", "update ", "delete ")))
        db.reset_mock()
        with self.assertRaisesRegex(Exception, "<="):
            get_agronomic_interpretation(1, date(2026, 1, 3), date(2026, 1, 2), db, user)
        with self.assertRaisesRegex(Exception, "730"):
            get_agronomic_interpretation(1, date(2023, 1, 1), date(2026, 1, 2), db, user)

    def test_quality_mapping_and_confidence_levels(self):
        high = build_index_interpretation("ndvi", [row(date(2026, 1, day), .1 + day / 100, day, cloud_cover_pct=5, valid_pixels_pct=95) for day in (1, 3, 5, 7, 9, 11)], date(2026, 1, 12))
        medium = build_index_interpretation("evi", [row(date(2026, 1, day), .1 + day / 100, day) for day in (1, 3, 5)], date(2026, 1, 12))
        low = build_index_interpretation("ndre", [row(date(2025, 12, 1), .1, 1), row(date(2025, 12, 3), .2, 2)], date(2026, 1, 12))
        self.assertEqual(high["confidence"]["level"], "high")
        self.assertEqual(medium["confidence"]["level"], "medium")
        self.assertEqual(low["confidence"]["level"], "low")
        self.assertEqual(high["latest_observation"]["valid_pixels_pct"], 95.0)

    def test_hypotheses_need_cross_index_evidence(self):
        from services.agronomic_interpretation import add_cross_index_hypotheses
        items = [build_index_interpretation(code, [row(date(2026, 1, day), value, identifier) for identifier, (day, value) in enumerate(((1, .1), (3, .11), (5, .12), (7, .9)), 1)], date(2026, 1, 8)) for code in ("ndvi", "savi")]
        add_cross_index_hypotheses(items)
        self.assertTrue(items[0]["hypotheses"])
        self.assertEqual(items[0]["hypotheses"][0]["supporting_indices"], ["ndvi", "savi"])


if __name__ == "__main__":
    unittest.main()
