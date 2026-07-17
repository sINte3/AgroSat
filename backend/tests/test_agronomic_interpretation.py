"""Permanent offline tests for TASK_206 contextual interpretation."""
import math
import os
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.agronomic_interpretation import (  # noqa: E402
    CHANGE_STABLE_EPSILON, INDEX_CODES, add_cross_index_hypotheses,
    build_index_interpretation, build_summary, calculate_baseline,
    calculate_change, calculate_trend, latest_percentile, percentile,
)


def row(day, value, identifier=1, **values):
    defaults = dict(satellite="Sentinel-2", cloud_cover_pct=5, valid_pixels_pct=95,
                    min_value=None, max_value=None, std_value=None, p10_value=None, p90_value=None)
    defaults.update(values)
    return SimpleNamespace(id=identifier, captured_date=day, value=value, **defaults)


def history(count=7, start=date(2026, 1, 1), step=.02, **quality):
    return [row(start + timedelta(days=i * 3), .2 + i * step, i + 1, **quality) for i in range(count)]


class StatisticsTests(unittest.TestCase):
    def test_median_odd(self): self.assertEqual(calculate_baseline([row(date(2026,1,i+1), v, i) for i,v in enumerate([1,2,3,4,5])], 3)["median"], 3)
    def test_median_even(self): self.assertEqual(calculate_baseline([row(date(2026,1,i+1), v, i) for i,v in enumerate([1,2,3,4,5,6])], 3)["median"], 3.5)
    def test_p25_interpolation(self): self.assertEqual(percentile([1,2,3,4], .25), 1.75)
    def test_p75_interpolation(self): self.assertEqual(percentile([1,2,3,4], .75), 3.25)
    def test_latest_percentile(self): self.assertEqual(latest_percentile([1,2,3,4], 3), 62.5)
    def test_zero_denominator_percent(self): self.assertIsNone(calculate_change(row(date(2026,1,2), .2), row(date(2026,1,1), 0))["percent"])
    def test_stable_epsilon(self): self.assertEqual(calculate_change(row(date(2026,1,2), .2 + CHANGE_STABLE_EPSILON/2), row(date(2026,1,1), .2))["direction"], "stable")
    def test_change_rising(self): self.assertEqual(calculate_change(row(date(2026,1,2), .3), row(date(2026,1,1), .2))["direction"], "rising")
    def test_change_falling(self): self.assertEqual(calculate_change(row(date(2026,1,2), .1), row(date(2026,1,1), .2))["direction"], "falling")
    def test_ols_rising(self): self.assertEqual(calculate_trend(history(4))["direction"], "rising")
    def test_ols_falling(self): self.assertEqual(calculate_trend(history(4, step=-.02))["direction"], "falling")
    def test_ols_stable(self): self.assertEqual(calculate_trend(history(4, step=.0001))["direction"], "stable")
    def test_uneven_intervals(self):
        rows=[row(date(2026,1,1),.1,1),row(date(2026,1,3),.2,2),row(date(2026,1,10),.55,3)]
        self.assertAlmostEqual(calculate_trend(rows)["slope_per_day"], .05, places=4)
    def test_baseline_insufficient_zero_to_four(self):
        for count in range(5): self.assertEqual(calculate_baseline(history(count), .5)["status"], "insufficient_history")
    def test_baseline_available_five(self): self.assertNotEqual(calculate_baseline(history(5), .5)["status"], "insufficient_history")
    def test_nonfinite_excluded(self): self.assertEqual(build_index_interpretation("ndvi", history(2)+[row(date(2026,1,9), math.nan, 9)], date(2026,1,10))["data_quality"]["observation_count"], 2)
    def test_duplicate_date_flag(self):
        rows=history(2)+[row(history(2)[-1].captured_date,.9,99)]
        result=build_index_interpretation("ndvi",rows,date(2026,1,10)); self.assertIn("duplicate_date",result["data_quality"]["quality_flags"])
    def test_future_date_flag_and_nonnegative_freshness(self):
        result=build_index_interpretation("ndvi",[row(date(2026,2,1),.4)],date(2026,1,1)); self.assertIn("future_observation_date",result["data_quality"]["quality_flags"]); self.assertEqual(result["latest"]["freshness_days"],0)


class ConfidenceAndContextTests(unittest.TestCase):
    def test_no_data_score_zero_none(self):
        result=build_index_interpretation("ndvi",[],date(2026,1,1)); self.assertEqual((result["confidence"]["score"],result["confidence"]["level"]),(0,"none"))
    def test_single_observation_low(self): self.assertEqual(build_index_interpretation("ndvi",[row(date(2026,1,1),.2)],date(2026,1,2))["confidence"]["level"],"low")
    def test_fresh_dense_clean_high(self): self.assertEqual(build_index_interpretation("ndvi",history(8,std_value=.03),date(2026,1,23),{"crop_available":True,"growth_stage_available":True})["confidence"]["level"],"high")
    def test_stale_lowers_score(self):
        fresh=build_index_interpretation("ndvi",history(7,std_value=.03),date(2026,1,20))["confidence"]["score"]; stale=build_index_interpretation("ndvi",history(7,std_value=.03),date(2026,3,20))["confidence"]["score"]; self.assertLess(stale,fresh)
    def test_low_pixels_lowers_score(self):
        good=build_index_interpretation("ndvi",history(7,std_value=.03),date(2026,1,20))["confidence"]["score"]; bad=build_index_interpretation("ndvi",history(7,std_value=.03,valid_pixels_pct=20),date(2026,1,20))["confidence"]["score"]; self.assertLess(bad,good)
    def test_high_cloud_lowers_score(self):
        good=build_index_interpretation("ndvi",history(7,std_value=.03),date(2026,1,20))["confidence"]["score"]; bad=build_index_interpretation("ndvi",history(7,std_value=.03,cloud_cover_pct=80),date(2026,1,20))["confidence"]["score"]; self.assertLess(bad,good)
    def test_missing_context_not_fabricated(self):
        result=build_index_interpretation("ndvi",history(),date(2026,1,20),{}); self.assertNotIn("growth_stage",result)
    def test_score_bounds(self):
        for count in range(10): self.assertIn(build_index_interpretation("ndvi",history(count),date(2026,1,20))["confidence"]["score"],range(101))
    def test_deterministic_score(self):
        args=("ndvi",history(7,std_value=.03),date(2026,1,20)); self.assertEqual(build_index_interpretation(*args)["confidence"],build_index_interpretation(*args)["confidence"])
    def test_missing_dispersion_flag(self): self.assertIn("missing_dispersion",build_index_interpretation("evi",history(),date(2026,1,20))["data_quality"]["quality_flags"])


class SafetyTests(unittest.TestCase):
    def _hypotheses(self):
        items=[build_index_interpretation(code,history(7,step=.08,std_value=.03),date(2026,1,20)) for code in INDEX_CODES]; add_cross_index_hypotheses(items); return items
    def test_no_diagnosis_vocabulary(self): self.assertNotIn("диагноз установлен",str(self._hypotheses()).lower())
    def test_requires_field_check_always_true(self): self.assertTrue(all(h["requires_field_check"] for i in self._hypotheses() for h in i["hypotheses"]))
    def test_hypothesis_confidence_never_high(self): self.assertNotIn("high",[h["confidence"] for i in self._hypotheses() for h in i["hypotheses"]])
    def test_ndmi_alone_no_irrigation_prescription(self): self.assertNotIn("полить",str(build_index_interpretation("ndmi",history(),date(2026,1,20))).lower())
    def test_ndre_alone_no_nitrogen_claim(self): self.assertNotIn("дефицит азота",str(build_index_interpretation("ndre",history(),date(2026,1,20))).lower())
    def test_ndvi_alone_no_disease_claim(self): self.assertNotIn("доказана болезнь",str(build_index_interpretation("ndvi",history(),date(2026,1,20))).lower())
    def test_low_confidence_suppresses_single_index_hypothesis(self):
        item=build_index_interpretation("ndvi",history(1),date(2026,1,20)); add_cross_index_hypotheses([item]); self.assertEqual(item["hypotheses"],[])
    def test_checks_have_no_treatment_dose(self):
        text=str(self._hypotheses()).lower(); self.assertNotIn("доза",text); self.assertNotIn("литров",text)
    def test_checks_are_bounded_and_unique(self):
        for item in self._hypotheses(): self.assertLessEqual(len(item["recommended_checks"]),5); self.assertEqual(len(item["recommended_checks"]),len(set(item["recommended_checks"])))


class ApiContractTests(unittest.TestCase):
    def _call(self, ndvi=None, indices=None):
        from api.agronomic_interpretation import get_agronomic_interpretation
        field=SimpleNamespace(id=1,name="Test field",enterprise_id=2,crop_name=None,season_year=None,soil_type=None)
        db=MagicMock(); db.execute.side_effect=[MagicMock(fetchone=MagicMock(return_value=field)),MagicMock(fetchall=MagicMock(return_value=ndvi or [])),MagicMock(fetchall=MagicMock(return_value=indices or []))]
        return get_agronomic_interpretation(1,date(2026,1,1),date(2026,1,20),db,SimpleNamespace(role="admin",enterprise_id=None)),db
    def test_stable_index_order(self): self.assertEqual([i["code"] for i in self._call()[0]["indices"]],list(INDEX_CODES))
    def test_existing_response_keys_preserved(self): self.assertTrue({"field","range","generated_at","overall_confidence","indices","limitations"}.issubset(self._call()[0]))
    def test_contextual_fields_present(self): self.assertTrue({"context","summary"}.issubset(self._call()[0]))
    def test_no_data_200_model(self): self.assertEqual(self._call()[0]["summary"]["status"],"insufficient_data")
    def test_partial_data_200_model(self): self.assertEqual(self._call(history(2))[0]["summary"]["indices_with_data"],1)
    def test_query_executions_three(self): self.assertEqual(self._call()[1].execute.call_count,3)
    def test_no_sql_per_index(self): self.assertLessEqual(self._call()[1].execute.call_count,4)
    def test_ndvi_uses_mean_ndvi_other_mean_value(self):
        self._call(); from api import agronomic_interpretation as api
        source=Path(api.__file__).read_text(encoding="utf-8"); self.assertIn("mean_ndvi AS value",source); self.assertIn("mean_value AS value",source)
    def test_tenant_scope_retained(self):
        from api.agronomic_interpretation import _field_context
        db=MagicMock(); db.execute.return_value.fetchone.return_value=None
        with self.assertRaisesRegex(Exception,"Field not found"): _field_context(db,9,SimpleNamespace(role="viewer",enterprise_id=8),2026)
        self.assertIn("f.enterprise_id = :enterprise_id",str(db.execute.call_args.args[0]))
    def test_history_is_bounded(self):
        self._call(); sql=" ".join(str(c.args[0]).lower() for c in self._call()[1].execute.call_args_list); self.assertIn("limit :history_limit",sql)
    def test_only_select_statements(self):
        sql=" ".join(str(c.args[0]).lower() for c in self._call()[1].execute.call_args_list); self.assertNotRegex(sql,r"\b(insert|update|delete)\b")
    def test_openapi_paths_preserved(self):
        os.environ.setdefault("SECRET_KEY","task206_test_only_secret_key_0123456789abcdef")
        with patch("redis.Redis.from_url",return_value=MagicMock()): from main import app
        paths=app.openapi()["paths"]
        for path in ("/api/agronomic-interpretation/fields/{field_id}","/api/ndvi/{field_id}/latest","/api/ndvi/{field_id}/history","/api/satellite-indices/{field_id}/latest","/api/satellite-indices/{field_id}/history"): self.assertIn(path,paths)
    def test_summary_not_stronger_than_components(self):
        payload,_=self._call(history(1)); self.assertNotEqual(payload["summary"]["confidence"],"high")
    def test_exact_new_index_keys(self):
        item=self._call()[0]["indices"][0]
        for key in ("index_code","display_name","plain_language_meaning","latest","previous","change","baseline","trend","heterogeneity","data_quality","confidence","hypotheses","recommended_checks","limitations"): self.assertIn(key,item)


if __name__ == "__main__": unittest.main()
