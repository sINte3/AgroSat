"""TASK_223 / C3: writer, reader and database must name failures alike.

``satellite_collection_runs.failure_category`` is bounded by a CHECK
constraint. The collector used to classify failures with a wider vocabulary of
its own, so an ordinary exit code 1 produced ``partial`` — a label the
constraint rejects. The write raised, the aborted transaction then rejected
the unlock in ``finish_apply_run``'s ``finally``, and that unlock error
replaced the real one.

No database is needed here: these tests pin the vocabulary itself. The
PostgreSQL suite pins it against the live constraint.
"""

from __future__ import annotations

import unittest

from scripts import collect_satellite as collector
from services import health
from services.collection_failure import (
    RUN_FAILURE_CATEGORIES,
    canonical_failure_category,
)


def _summary(**updates):
    base = {
        "run_id": "vocabulary",
        "mode": "apply",
        "started_at": "2026-09-23T06:35:06+00:00",
        "finished_at": "2026-09-23T07:11:45+00:00",
        "duration_seconds": 2198.791,
        "diagnostics": [],
        "children": [],
    }
    return {**base, **updates}


class TheCollectorSpeaksTheDatabaseVocabulary(unittest.TestCase):
    def test_every_category_the_collector_can_emit_is_storable(self):
        emitted = set()
        for exit_code in (0, 1, 2, 3, 4, 130):
            emitted.add(collector.classify_failure(_summary(exit_code=exit_code)))
        for marker in (
            "authentication failed",
            "HTTP 401",
            "HTTP 403",
            "unauthorized",
            "invalid_client",
            "quota exceeded",
            "rate limit",
            "HTTP 429",
            "cloud",
            "quality_blocked",
            "timeout",
            "connection reset",
            "network unreachable",
            "dns failure",
            "something nobody anticipated",
        ):
            emitted.add(
                collector.classify_failure(_summary(exit_code=4, diagnostics=[marker]))
            )
        emitted.add(
            collector.classify_failure(
                _summary(exit_code=1, children=[{"timed_out": True}])
            )
        )
        emitted.add(
            collector.classify_failure(
                _summary(exit_code=1, children=[{"stderr": "cloud cover too high"}])
            )
        )
        unstorable = {value for value in emitted if value is not None} - set(
            RUN_FAILURE_CATEGORIES
        )
        self.assertEqual(
            unstorable,
            set(),
            "these labels would be rejected by ck_collection_runs_failure and "
            "would terminate the cycle's finish write",
        )

    def test_the_ordinary_partial_failure_is_storable(self):
        # Exit code 1 is the common case: the cycle ran, some fields failed.
        # It used to classify as 'partial', which the database rejects.
        category = collector.classify_failure(
            _summary(exit_code=1, children=[{"exit_code": 1, "counters": {}}])
        )
        self.assertEqual(category, "operational")
        self.assertIn(category, RUN_FAILURE_CATEGORIES)

    def test_a_cancelled_cycle_is_storable(self):
        category = collector.classify_failure(_summary(exit_code=130))
        self.assertEqual(category, "operational")
        self.assertIn(category, RUN_FAILURE_CATEGORIES)

    def test_cloud_and_quality_share_the_databases_label(self):
        for marker in ("cloud", "quality_blocked"):
            with self.subTest(marker=marker):
                self.assertEqual(
                    collector.classify_failure(
                        _summary(exit_code=1, diagnostics=[marker])
                    ),
                    "quality",
                )

    def test_recognised_conditions_keep_their_meaning(self):
        cases = (
            (_summary(exit_code=0), None),
            (_summary(exit_code=2), "contract"),
            (_summary(exit_code=3), "lock_contention"),
            (_summary(exit_code=4, diagnostics=["HTTP 401"]), "auth"),
            (_summary(exit_code=4, diagnostics=["HTTP 429"]), "quota"),
            (_summary(exit_code=4, diagnostics=["dns failure"]), "network"),
            (_summary(exit_code=4, children=[{"timed_out": True}]), "network"),
        )
        for summary, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(collector.classify_failure(summary), expected)


class TheMappingIsTotal(unittest.TestCase):
    def test_none_stays_none(self):
        self.assertIsNone(canonical_failure_category(None))
        self.assertIsNone(canonical_failure_category(""))
        self.assertIsNone(canonical_failure_category("   "))

    def test_every_durable_category_maps_to_itself(self):
        for category in RUN_FAILURE_CATEGORIES:
            with self.subTest(category=category):
                self.assertEqual(canonical_failure_category(category), category)

    def test_provider_labels_resolve(self):
        self.assertEqual(canonical_failure_category("authentication"), "auth")
        self.assertEqual(canonical_failure_category("provider_error"), "operational")
        self.assertEqual(canonical_failure_category("cloud_blocked"), "quality")

    def test_an_unknown_label_degrades_rather_than_breaking_the_write(self):
        self.assertEqual(canonical_failure_category("martian_interference"), "operational")

    def test_case_and_whitespace_do_not_produce_a_new_label(self):
        self.assertEqual(canonical_failure_category("  Cloud  "), "quality")
        self.assertEqual(canonical_failure_category("LOCK_CONTENTION"), "lock_contention")


class TheReaderSharesTheVocabulary(unittest.TestCase):
    def test_readiness_accepts_exactly_what_can_be_written(self):
        self.assertIs(
            health.ALLOWED_FAILURE_CATEGORIES,
            RUN_FAILURE_CATEGORIES,
            "the readiness reader must not keep a second list of categories",
        )

    def test_readiness_accepts_a_status_file_carrying_any_durable_category(self):
        for category in RUN_FAILURE_CATEGORIES:
            with self.subTest(category=category):
                self.assertIn(category, health.ALLOWED_FAILURE_CATEGORIES)


if __name__ == "__main__":
    unittest.main()
