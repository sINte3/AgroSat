"""TASK_246 (Fix C): duplicate NDVI rows are one acquisition for the canonical detector.

Before TASK_246 the NDVI collector re-stored one Sentinel acquisition under up
to five consecutive captured dates with identical statistics. Those rows are
still in production history. The detector must count them as ONE scene: not as
persistence, not as a longer baseline and not as a new scene when another copy
appears. Real database, real detection write path (collector apply cycle).
"""

from __future__ import annotations

from datetime import timedelta

from task225_support import HISTORY, Task225Base


class DuplicateAcquisitionTests(Task225Base):

    def _seed_series(self, field_id, recent_values):
        baseline_dates, _ = self.history_dates()
        for day, value in zip(baseline_dates, HISTORY):
            self.ndvi(field_id, day, value)
        return [self.ndvi(field_id, self.today - timedelta(days=offset), value)
                for offset, value in zip((4, 3, 2), recent_values)]

    def _only_candidate(self):
        rows = self.sql("SELECT persistence_scenes, severity, scene_id, evidence "
                        "FROM autonomous_anomaly_candidates")
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def test_one_acquisition_stored_on_three_dates_is_one_scene_not_persistence(self):
        ids = self._seed_series(self.field_a, (0.30, 0.30, 0.30))
        detection, _ = self.run_monitoring_cycle("task246-duplicates")
        self.assertEqual(detection["observation_candidates"], 1, detection)
        candidate = self._only_candidate()
        self.assertEqual(candidate["persistence_scenes"], 1)
        self.assertEqual(candidate["scene_id"], f"ndvi_record:{ids[0]}")
        self.assertEqual(candidate["evidence"]["persistence"]["record_ids"], [ids[0]])
        self.assertEqual(candidate["evidence"]["duplicate_rows_collapsed"], 2)
        # Three copies used to read as three scenes: confidence 0.85 and EXTREME.
        self.assertEqual(candidate["severity"], "MODERATE")

    def test_three_distinct_acquisitions_still_persist_three_scenes(self):
        self._seed_series(self.field_a, (0.30, 0.29, 0.28))
        detection, _ = self.run_monitoring_cycle("task246-distinct")
        self.assertEqual(detection["observation_candidates"], 1, detection)
        candidate = self._only_candidate()
        self.assertEqual(candidate["persistence_scenes"], 3)
        self.assertEqual(candidate["evidence"]["duplicate_rows_collapsed"], 0)

    def test_a_later_copy_of_the_same_acquisition_is_a_replay_not_a_new_scene(self):
        self._seed_series(self.field_a, (0.30, 0.30, 0.30))
        self.run_monitoring_cycle("task246-first")
        self.ndvi(self.field_a, self.today - timedelta(days=1), 0.30)
        detection, _ = self.run_monitoring_cycle("task246-second")
        self.assertEqual(detection["replayed_scene"], 1, detection)
        self.assertEqual(detection["observation_candidates"], 0, detection)
        self.assertEqual(self.count("autonomous_anomaly_candidates"), 1)
