"""H0-A: unit contract tests for the canonical accepted-observation rule.

These cover the pure Python evaluation and the generated SQL predicate. The
database-backed proof lives in test_h0a_freshness_postgres_contract.py.
"""

import math
import unittest

from services import observation_quality as quality
from services import agronomy_policy
from services import operational_verification


FRESHNESS_MIN = quality.MIN_VALID_PIXELS_FRESHNESS_PCT
ANALYSIS_MIN = quality.MIN_VALID_PIXELS_ANALYSIS_PCT


def verdict(value, valid_pixels_pct, cloud_cover_pct, minimum=ANALYSIS_MIN):
    return quality.evaluate(
        value=value,
        valid_pixels_pct=valid_pixels_pct,
        cloud_cover_pct=cloud_cover_pct,
        minimum_valid_pixels_pct=minimum,
    )


class RequiredContractCases(unittest.TestCase):
    """The six cases named in the H0-A specification."""

    def test_a_null_cloud_metadata_is_accepted(self):
        # The production shape: real Sentinel-2 row, cloud never measured.
        result = verdict(0.62, 100.0, None)
        self.assertTrue(result.accepted)
        self.assertEqual(result.reason, quality.ACCEPTED)
        self.assertEqual(result.cloud_metadata, quality.CLOUD_UNAVAILABLE)

    def test_b_low_measured_cloud_is_accepted(self):
        result = verdict(0.62, 100.0, 10.0)
        self.assertTrue(result.accepted)
        self.assertEqual(result.reason, quality.ACCEPTED)
        self.assertEqual(result.cloud_metadata, quality.CLOUD_MEASURED)

    def test_c_high_measured_cloud_is_rejected_as_cloud(self):
        result = verdict(0.62, 100.0, 80.0)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, quality.REJECTED_CLOUD)

    def test_d_insufficient_valid_pixels_is_rejected_as_quality(self):
        result = verdict(0.62, ANALYSIS_MIN - 1.0, None)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, quality.REJECTED_QUALITY)

    def test_e_invalid_index_value_is_rejected_as_quality(self):
        for value in (None, 1.5, -3.0, float("nan"), float("inf"), "x"):
            with self.subTest(value=value):
                result = verdict(value, 100.0, None)
                self.assertFalse(result.accepted)
                self.assertEqual(result.reason, quality.REJECTED_QUALITY)

    def test_f_malformed_cloud_metadata_is_rejected_conservatively(self):
        # Present but unusable. Never silently reinterpreted as "not measured".
        for cloud in (float("nan"), float("inf"), float("-inf"), -5.0, 150.0):
            with self.subTest(cloud=cloud):
                result = verdict(0.62, 100.0, cloud)
                self.assertFalse(
                    result.accepted,
                    "malformed cloud metadata must not be accepted",
                )
                self.assertEqual(result.cloud_metadata, quality.CLOUD_MALFORMED)
                self.assertEqual(result.reason, quality.REJECTED_QUALITY)


class ThresholdTiers(unittest.TestCase):
    """The two existing valid-pixel minimums are preserved, not flattened."""

    def test_tiers_keep_their_specified_values(self):
        self.assertEqual(FRESHNESS_MIN, 60.0)
        self.assertEqual(ANALYSIS_MIN, 50.0)
        self.assertEqual(quality.MAX_CLOUD_COVER_PCT, 30.0)

    def test_freshness_tier_is_stricter_than_analysis_tier(self):
        # 55% valid pixels: good enough to analyse, not good enough for freshness.
        self.assertTrue(verdict(0.62, 55.0, None, minimum=ANALYSIS_MIN).accepted)
        self.assertFalse(verdict(0.62, 55.0, None, minimum=FRESHNESS_MIN).accepted)

    def test_threshold_boundary_is_inclusive(self):
        self.assertTrue(verdict(0.62, ANALYSIS_MIN, None, minimum=ANALYSIS_MIN).accepted)
        self.assertTrue(verdict(0.62, FRESHNESS_MIN, None, minimum=FRESHNESS_MIN).accepted)

    def test_cloud_boundary_is_inclusive(self):
        self.assertTrue(verdict(0.62, 100.0, quality.MAX_CLOUD_COVER_PCT).accepted)
        self.assertFalse(
            verdict(0.62, 100.0, quality.MAX_CLOUD_COVER_PCT + 0.1).accepted
        )


class ValidPixelsRemainMandatory(unittest.TestCase):
    """The repair must not degenerate into deleting quality filtering."""

    def test_missing_valid_pixels_is_rejected(self):
        self.assertFalse(verdict(0.62, None, None).accepted)

    def test_non_finite_valid_pixels_is_rejected(self):
        for pixels in (float("nan"), float("inf")):
            with self.subTest(pixels=pixels):
                self.assertFalse(verdict(0.62, pixels, None).accepted)

    def test_impossible_valid_pixels_percentage_is_rejected(self):
        for pixels in (-1.0, 101.0):
            with self.subTest(pixels=pixels):
                self.assertFalse(verdict(0.62, pixels, None).accepted)

    def test_index_value_range_is_enforced(self):
        self.assertTrue(verdict(quality.MIN_INDEX_VALUE, 100.0, None).accepted)
        self.assertTrue(verdict(quality.MAX_INDEX_VALUE, 100.0, None).accepted)
        self.assertFalse(verdict(quality.MAX_INDEX_VALUE + 0.01, 100.0, None).accepted)

    def test_negative_index_values_stay_valid(self):
        # NDMI and NDRE are legitimately negative; only out-of-range is invalid.
        self.assertTrue(verdict(-0.35, 100.0, None).accepted)

    def test_booleans_are_not_treated_as_numbers(self):
        self.assertFalse(verdict(True, 100.0, None).accepted)
        self.assertFalse(verdict(0.62, True, None).accepted)


class CloudMetadataClassification(unittest.TestCase):
    def test_none_is_unavailable_not_zero(self):
        self.assertEqual(
            quality.classify_cloud_metadata(None), quality.CLOUD_UNAVAILABLE
        )

    def test_zero_is_a_measurement(self):
        self.assertEqual(quality.classify_cloud_metadata(0.0), quality.CLOUD_MEASURED)

    def test_unavailable_is_neither_cloud_free_nor_cloud_blocked(self):
        # Accepted, but the detail never claims the scene was cloud free.
        result = verdict(0.62, 100.0, None)
        self.assertTrue(result.accepted)
        self.assertIn("not measured", result.detail)
        self.assertNotIn("cloud free", result.detail.lower())


class SqlPredicate(unittest.TestCase):
    def test_null_cloud_is_tolerated(self):
        sql = quality.accepted_observation_sql(
            value_column="mean_ndvi", minimum_valid_pixels_pct=ANALYSIS_MIN
        )
        self.assertIn("cloud_cover_pct IS NULL", sql)
        self.assertIn("cloud_cover_pct <= 30.0", sql)
        self.assertNotIn("COALESCE", sql)

    def test_valid_pixels_threshold_is_rendered(self):
        sql = quality.accepted_observation_sql(
            value_column="mean_value", minimum_valid_pixels_pct=FRESHNESS_MIN
        )
        self.assertIn("valid_pixels_pct >= 60.0", sql)
        self.assertIn("valid_pixels_pct IS NOT NULL", sql)

    def test_require_cloud_metadata_produces_the_stricter_form(self):
        sql = quality.accepted_observation_sql(
            value_column="mean_ndvi",
            minimum_valid_pixels_pct=ANALYSIS_MIN,
            require_cloud_metadata=True,
        )
        self.assertIn("cloud_cover_pct IS NOT NULL", sql)
        self.assertNotIn("cloud_cover_pct IS NULL", sql)

    def test_predicate_carries_no_bind_parameters(self):
        sql = quality.accepted_observation_sql(
            value_column="mean_ndvi", minimum_valid_pixels_pct=ANALYSIS_MIN
        )
        self.assertNotIn(":", sql)

    def test_identifier_is_validated(self):
        for bad in ("mean_ndvi; DROP TABLE fields", "1=1 OR x", "", None, "a b"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    quality.accepted_observation_sql(
                        value_column=bad, minimum_valid_pixels_pct=ANALYSIS_MIN
                    )

    def test_threshold_must_be_a_percentage(self):
        for bad in (-1.0, 101.0):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    quality.accepted_observation_sql(
                        value_column="mean_ndvi", minimum_valid_pixels_pct=bad
                    )

    def test_qualified_column_references_are_allowed(self):
        sql = quality.accepted_observation_sql(
            value_column="n.mean_ndvi",
            valid_pixels_column="n.valid_pixels_pct",
            cloud_column="n.cloud_cover_pct",
            minimum_valid_pixels_pct=FRESHNESS_MIN,
        )
        self.assertIn("n.mean_ndvi IS NOT NULL", sql)


class AgronomyPolicyStatusMapping(unittest.TestCase):
    """agronomy_plans.verification_status vocabulary is unchanged."""

    def test_null_cloud_observation_is_no_longer_quality_blocked(self):
        observation = {"value": 0.62, "valid": 100.0, "cloud": None}
        self.assertIsNone(agronomy_policy.quality(observation))

    def test_high_cloud_is_still_cloud_blocked(self):
        observation = {"value": 0.62, "valid": 100.0, "cloud": 80.0}
        self.assertEqual(agronomy_policy.quality(observation), "CLOUD_BLOCKED")

    def test_low_valid_pixels_is_still_quality_blocked(self):
        observation = {"value": 0.62, "valid": 10.0, "cloud": None}
        self.assertEqual(agronomy_policy.quality(observation), "QUALITY_BLOCKED")

    def test_missing_observation_is_pending_data(self):
        self.assertEqual(agronomy_policy.quality(None), "PENDING_DATA")
        self.assertEqual(agronomy_policy.quality({}), "PENDING_DATA")

    def test_returned_values_stay_inside_the_persisted_vocabulary(self):
        allowed = {None, "CLOUD_BLOCKED", "QUALITY_BLOCKED", "PENDING_DATA"}
        for observation in (
            None,
            {},
            {"value": 0.62, "valid": 100.0, "cloud": None},
            {"value": 0.62, "valid": 100.0, "cloud": 80.0},
            {"value": 9.0, "valid": 100.0, "cloud": None},
            {"value": 0.62, "valid": None, "cloud": float("nan")},
        ):
            with self.subTest(observation=observation):
                self.assertIn(agronomy_policy.quality(observation), allowed)


class LegacyVerificationEngine(unittest.TestCase):
    def _observation(self, cloud, valid=100.0, value=0.5):
        return operational_verification.Observation(
            record_id=1,
            source="ndvi_records",
            field_id=1,
            index_code="ndvi",
            observed_at=__import__("datetime").date(2026, 9, 1),
            value=value,
            valid_pixels_pct=valid,
            cloud_cover_pct=cloud,
            satellite="Sentinel-2",
        )

    def test_null_cloud_observation_is_accepted(self):
        self.assertTrue(
            operational_verification.accepted_quality(self._observation(None))
        )

    def test_high_cloud_observation_is_rejected(self):
        self.assertFalse(
            operational_verification.accepted_quality(self._observation(80.0))
        )

    def test_low_valid_pixels_observation_is_rejected(self):
        self.assertFalse(
            operational_verification.accepted_quality(self._observation(None, valid=10.0))
        )

    def test_unmeasured_cloud_does_not_earn_high_confidence(self):
        # Unavailable metadata cannot support a positive cloud claim.
        self.assertFalse(operational_verification._cloud_within(None, 10.0))
        self.assertTrue(operational_verification._cloud_within(5.0, 10.0))

    def test_shared_constants_come_from_the_canonical_contract(self):
        self.assertEqual(
            operational_verification.QUALITY_VALID_PIXELS_MIN, ANALYSIS_MIN
        )
        self.assertEqual(
            operational_verification.QUALITY_CLOUD_MAX, quality.MAX_CLOUD_COVER_PCT
        )


class SingleSourceOfTruth(unittest.TestCase):
    """No consumer may reintroduce a hand-written cloud acceptance predicate."""

    # services/operational_closure.py left this list in TASK_225: its legacy
    # verification writes were retired, so it no longer reads observations at
    # all (pinned by test_retired_closure_reads_no_observations).
    CONSUMERS = (
        "services/autonomous_monitoring.py",
        "services/closed_loop_agronomy.py",
        "services/executive_accountability.py",
        "services/agronomy_policy.py",
        "services/operational_verification.py",
    )

    FORBIDDEN = (
        "COALESCE(n.cloud_cover_pct,101)",
        "COALESCE(s.cloud_cover_pct,101)",
        "cloud_cover_pct BETWEEN",
    )

    def test_consumers_contain_no_null_hostile_predicate(self):
        from pathlib import Path

        backend = Path(__file__).resolve().parents[1]
        for relative in self.CONSUMERS:
            source = (backend / relative).read_text(encoding="utf-8")
            for fragment in self.FORBIDDEN:
                with self.subTest(module=relative, fragment=fragment):
                    self.assertNotIn(fragment, source)

    def test_retired_closure_reads_no_observations(self):
        from pathlib import Path

        backend = Path(__file__).resolve().parents[1]
        source = (backend / "services/operational_closure.py").read_text(encoding="utf-8")
        for table in ("ndvi_records", "satellite_index_records", "cloud_cover_pct"):
            with self.subTest(table=table):
                self.assertNotIn(table, source)

    def test_consumers_use_the_canonical_module(self):
        from pathlib import Path

        backend = Path(__file__).resolve().parents[1]
        for relative in self.CONSUMERS:
            source = (backend / relative).read_text(encoding="utf-8")
            with self.subTest(module=relative):
                self.assertIn("observation_quality", source)

    # The ingest admission gates keep their own, deliberately looser predicate,
    # but the cloud ceiling itself must exist in exactly one place.
    INGEST_GATES = (
        "services/satellite.py",
        "services/satellite_indices.py",
    )

    def test_ingest_gates_source_the_shared_cloud_ceiling(self):
        from pathlib import Path

        backend = Path(__file__).resolve().parents[1]
        for relative in self.INGEST_GATES:
            source = (backend / relative).read_text(encoding="utf-8")
            with self.subTest(module=relative):
                self.assertIn(
                    "observation_quality.MAX_CLOUD_COVER_PCT",
                    source,
                    "the ingest gate must not hardcode the cloud ceiling",
                )
                self.assertNotIn("cloud_cover_pct > 30", source)

    def test_ingest_gates_still_tolerate_absent_cloud_metadata(self):
        """The ingest gate must not reject an observation for missing metadata."""
        from services.satellite import validate_ndvi_quality
        from services.satellite_indices import validate_index_quality

        accepted, reason = validate_ndvi_quality(
            mean_ndvi=0.62, cloud_cover_pct=None, field_name="H0A"
        )
        self.assertTrue(accepted, reason)
        accepted, reason = validate_index_quality(
            index_code="savi", mean_value=0.48, cloud_cover_pct=None, valid_pixels_pct=100.0
        )
        self.assertTrue(accepted, reason)

        # Measured excess still rejects, at the canonical ceiling.
        self.assertFalse(
            validate_ndvi_quality(
                mean_ndvi=0.62, cloud_cover_pct=30.1, field_name="H0A"
            )[0]
        )
        self.assertFalse(
            validate_index_quality(
                index_code="savi", mean_value=0.48, cloud_cover_pct=30.1, valid_pixels_pct=100.0
            )[0]
        )

    def test_canonical_module_imports_nothing_but_the_standard_library(self):
        import ast
        from pathlib import Path

        backend = Path(__file__).resolve().parents[1]
        tree = ast.parse(
            (backend / "services/observation_quality.py").read_text(encoding="utf-8")
        )
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        # A pure contract: no database, provider, scheduler or cache dependency.
        self.assertEqual(imported, {"__future__", "dataclasses", "math", "re"})


if __name__ == "__main__":
    unittest.main()
