"""Comprehensive offline tests for TASK_197 field attention queue."""
import os
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from api.field_attention import MAX_SCOPE_FIELDS, get_field_attention_queue  # noqa: E402
from services.agronomic_interpretation import INDEX_CODES  # noqa: E402
from services.field_attention import LIMITATIONS, queue_sort_key, score_field  # noqa: E402


def interpretation(code, freshness=0, confidence="medium", direction="stable",
                   signal="normal", trend="stable", status="valid", checks=None):
    observed = None if freshness is None else {
        "observed_at": date(2026, 7, 15) - timedelta(days=freshness),
        "freshness_days": freshness,
    }
    return {"code": code, "latest_observation": observed, "data_status": status,
            "confidence": {"level": confidence},
            "change": {"direction": direction, "statistical_signal": signal},
            "trend": {"direction": trend}, "recommended_checks": checks or []}


def five(**overrides):
    return [interpretation(code, **overrides) for code in INDEX_CODES]


def alert(identifier=1, severity="critical", when=None):
    return SimpleNamespace(id=identifier, field_id=1, alert_type="ndvi_drop",
                           severity=severity, title="Synthetic alert",
                           triggered_at=when or datetime(2026, 7, 15, 3))


def field(identifier=1, enterprise=9):
    return SimpleNamespace(id=identifier, name=f"Field {identifier}", enterprise_id=enterprise,
                           enterprise_name="Synthetic enterprise", crop_type_id=4,
                           crop_name="Хлопчатник", season_year=2026)


class Result:
    def __init__(self, rows): self.rows = rows
    def fetchall(self): return self.rows


class FakeDB:
    def __init__(self, batches): self.batches, self.calls = list(batches), []
    def execute(self, sql, params):
        self.calls.append((str(sql), params))
        return Result(self.batches.pop(0))


def endpoint(db, user=None, **kwargs):
    values = dict(enterprise_id=None, crop_type_id=None, date_to=date(2026, 7, 15),
                  lookback_days=180, min_priority="medium", limit=100)
    values.update(kwargs)
    return get_field_attention_queue(db=db, current_user=user or SimpleNamespace(role="admin", enterprise_id=None), **values)


class ScoringTests(unittest.TestCase):
    def test_critical_warning_and_total_caps(self):
        critical = score_field([alert()], five(freshness=0), date(2026, 7, 15))
        self.assertEqual(critical["priority"], "critical")
        self.assertGreaterEqual(critical["attention_score"], 70)
        warning = score_field([alert(severity="warning")], five(freshness=0), date(2026, 7, 15))
        self.assertEqual(warning["priority"], "high")
        self.assertGreaterEqual(warning["attention_score"], 45)
        many = [alert(i, "critical") for i in range(20)] + [alert(100 + i, "warning") for i in range(20)]
        capped = score_field(many, five(freshness=None), date(2026, 7, 15))
        self.assertEqual(capped["attention_score"], 100)

    def test_freshness_points_at_all_boundaries(self):
        expected = {None: 35, 7: 0, 8: 10, 14: 10, 15: 20, 30: 20, 31: 30}
        for freshness, points in expected.items():
            with self.subTest(freshness=freshness):
                result = score_field([], five(freshness=freshness), date(2026, 7, 15))
                self.assertEqual(sum(r["points"] for r in result["reasons"]), points)

    def test_spectral_rules_confidence_cap_and_order(self):
        low = score_field([], five(freshness=0, confidence="low", direction="down", signal="strong", trend="falling"), date(2026, 7, 15))
        self.assertEqual(low["attention_score"], 0)
        strong = score_field([], five(freshness=0, direction="down", signal="strong"), date(2026, 7, 15))
        self.assertEqual(strong["attention_score"], 30)
        self.assertEqual(strong["reasons"][0]["points"], 30)
        notable = score_field([], [interpretation("ndvi", direction="down", signal="notable")] +
                              [interpretation(c) for c in INDEX_CODES[1:]], date(2026, 7, 15))
        self.assertEqual(notable["attention_score"], 7)
        falling = score_field([], [interpretation("ndvi", direction="down", trend="falling")] +
                              [interpretation(c) for c in INDEX_CODES[1:]], date(2026, 7, 15))
        self.assertEqual(falling["attention_score"], 4)
        mixed = score_field([alert(severity="info"), alert(2, "warning"), alert(3, "critical")],
                            five(freshness=31, direction="down", signal="strong", trend="falling"), date(2026, 7, 15))
        self.assertEqual([r["code"] for r in mixed["reasons"]], [
            "active_critical_alert", "active_warning_alert", "active_info_alert",
            "ndvi_stale_over_30_days", "strong_downward_spectral_signal",
            "falling_spectral_trend"])
        self.assertEqual(mixed["reasons"][-1]["points"], 0)

    def test_summaries_top_alerts_checks_and_safe_limitations(self):
        items = five(freshness=20, direction="down", signal="notable", checks=["Повторяемая проверка"])
        result = score_field([alert(i, "warning", datetime(2026, 7, 15, i)) for i in range(1, 6)], items, date(2026, 7, 15))
        self.assertEqual(len(result["alert_summary"]["top_alerts"]), 3)
        self.assertNotIn("description", result["alert_summary"]["top_alerts"][0])
        self.assertLessEqual(len(result["recommended_checks"]), 5)
        self.assertEqual(len(result["recommended_checks"]), len(set(result["recommended_checks"])))
        self.assertEqual(result["spectral_summary"]["downward_indices"], ["NDVI", "SAVI", "EVI", "NDMI", "NDRE"])
        self.assertEqual(result["limitations"], LIMITATIONS)
        unsafe = ("confirmed disease", "nitrogen deficiency", "must irrigate", "apply fertilizer", "apply pesticide", "yield loss confirmed", "urgent treatment")
        self.assertFalse(any(word in str(result).lower() for word in unsafe))

    def test_sort_null_freshness_first_then_field_id(self):
        def item(fid, freshness):
            return {"priority": "medium", "attention_score": 20,
                    "alert_summary": {"critical": 0, "warning": 0},
                    "spectral_summary": {"freshness_days": freshness}, "field": {"id": fid}}
        ordered = sorted([item(3, 10), item(2, None), item(1, 10)], key=queue_sort_key)
        self.assertEqual([x["field"]["id"] for x in ordered], [2, 1, 3])


class EndpointTests(unittest.TestCase):
    def test_route_and_auth_dependency_are_present(self):
        os.environ.setdefault("SECRET_KEY", "task197_test_only_secret_key_0123456789abcdef")
        with patch("redis.Redis.from_url", return_value=MagicMock()):
            from main import app
        path = app.openapi()["paths"]["/api/field-attention/queue"]["get"]
        self.assertTrue(path["security"])

    def test_tenant_predicate_filters_and_foreign_request(self):
        db = FakeDB([[]])
        tenant = SimpleNamespace(role="viewer", enterprise_id=8)
        endpoint(db, tenant)
        self.assertIn("f.enterprise_id = :enterprise_id", db.calls[0][0])
        self.assertEqual(db.calls[0][1]["enterprise_id"], 8)
        with self.assertRaisesRegex(Exception, "Доступ запрещён"):
            endpoint(FakeDB([]), tenant, enterprise_id=9)

    def test_tenant_without_enterprise_and_unknown_role_rejected(self):
        for user in (SimpleNamespace(role="agronomist", enterprise_id=None), SimpleNamespace(role="other", enterprise_id=1)):
            with self.assertRaises(Exception) as caught: endpoint(FakeDB([]), user)
            self.assertEqual(caught.exception.status_code, 403)

    def test_global_scope_and_filters_are_inside_field_sql(self):
        db = FakeDB([[]]); endpoint(db, crop_type_id=4)
        self.assertNotIn("f.enterprise_id = :enterprise_id", db.calls[0][0])
        self.assertIn("f.is_active = true", db.calls[0][0])
        self.assertIn("cs.crop_type_id = :crop_type_id", db.calls[0][0])
        db = FakeDB([[]]); endpoint(db, enterprise_id=9)
        self.assertIn("f.enterprise_id = :enterprise_id", db.calls[0][0])

    def test_scope_limit_stops_before_histories_and_empty_is_one_select(self):
        db = FakeDB([[field(i) for i in range(MAX_SCOPE_FIELDS + 1)]])
        with self.assertRaises(Exception) as caught: endpoint(db)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(len(db.calls), 1)
        empty = FakeDB([[]]); response = endpoint(empty)
        self.assertEqual(len(empty.calls), 1)
        self.assertEqual(response["summary"]["fields_evaluated"], 0)

    def test_four_select_budget_batch_queries_and_no_forbidden_sql(self):
        db = FakeDB([[field()], [], [], []])
        response = endpoint(db, min_priority="low")
        self.assertEqual(len(db.calls), 4)
        sql = " ".join(call[0].lower() for call in db.calls)
        for table in ("alerts", "ndvi_records", "satellite_index_records"):
            self.assertEqual(sum(table in call[0].lower() for call in db.calls), 1)
        for forbidden in ("insert ", "update ", "delete ", "scouting_notes", "redis", "sentinel"):
            self.assertNotIn(forbidden, sql)
        self.assertEqual(response["items"][0]["spectral_summary"]["overall_confidence"], "insufficient")

    def test_summary_precedes_filter_and_limit_follows_sort(self):
        fields = [field(2), field(1)]
        alerts = [SimpleNamespace(**{**alert(1, "critical").__dict__, "field_id": 1})]
        db = FakeDB([fields, alerts, [], []])
        response = endpoint(db, min_priority="critical", limit=1)
        self.assertEqual(response["summary"]["fields_evaluated"], 2)
        self.assertEqual(response["summary"]["medium"], 1)
        self.assertEqual(response["summary"]["returned"], 1)
        self.assertEqual(response["items"][0]["field"]["id"], 1)
        self.assertEqual(response["items"][0]["rank"], 1)

    def test_source_contract_is_fixed_and_read_only(self):
        source = (BACKEND / "api" / "field_attention.py").read_text(encoding="utf-8").lower()
        self.assertEqual(INDEX_CODES, ("ndvi", "savi", "evi", "ndmi", "ndre"))
        self.assertNotIn("scouting_notes", source)
        for token in (".commit(", ".flush(", "cache_", "requests.", "http://", "https://"):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
