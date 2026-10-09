"""TASK_253: the acquisition gate against a real database (isolated, migrated to head).

The canonical collector runs in apply mode with everything real except the provider:
``begin_apply_run`` (advisory lock, run row), the gate's PostGIS scope query and its
checkpoint, freshness, monitoring and ``finish_apply_run``. Batch children persist the
acquisitions the simulated Catalog lists through the real idempotent writers
(``collect_ndvi.persist`` and ``collect_satellite_indices._insert_satellite_index_record``)
and write the real completion evidence.

The suite is skipped unless ``AGROSAT_TEST_DATABASE_URL`` is set, and the shared
harness refuses any database whose name does not start with ``agrosat_h0a``.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from collector_evidence import write_cycle_summary
from task225_support import RELEASE_COMMIT, Task225Base
from task253_support import Catalog, FakeDate, feature

INDICES = ("savi", "evi", "ndmi", "ndre")


class AcquisitionGatePostgresTests(Task225Base):

    def setUp(self):
        super().setUp()
        import database
        from scripts import collect_ndvi, collect_satellite as collector, collect_satellite_indices
        from services import collector_idempotency

        self.collector = collector
        self.patchers = [
            patch.object(database, "SessionLocal", self.Session),
            patch.object(collect_satellite_indices, "SessionLocal", self.Session),
            patch.object(collector_idempotency, "SessionLocal", self.Session),
            patch.object(collect_ndvi, "invalidate_observation_cache", lambda enterprise: True),
            patch.object(collect_satellite_indices, "_invalidate_observation_cache", lambda enterprise: True),
            patch.object(collector, "date", FakeDate),
            patch.dict(os.environ, {"AGROSAT_RELEASE_COMMIT": RELEASE_COMMIT,
                                    "AGROSAT_COLLECTOR_AUDIT_IDENTITY": "TASK_253_TEST"}),
        ]
        for patcher in self.patchers:
            patcher.start()
        FakeDate.current = self.today
        self.catalog = Catalog(today=self.today)
        self.directory = tempfile.TemporaryDirectory(prefix="task253-")
        self.root = Path(self.directory.name)
        self.cycles = 0
        self.child_calls = 0
        self.active = [row["id"] for row in self.sql("SELECT id FROM fields WHERE is_active ORDER BY id")]
        self.enterprise = {row["id"]: row["enterprise_id"] for row in self.sql("SELECT id, enterprise_id FROM fields")}

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.directory.cleanup()
        super().tearDown()

    # ── the simulated provider ──────────────────────────────────────────────

    def advance(self, days=1):
        FakeDate.current += timedelta(days=days)
        self.catalog.today = FakeDate.current

    def child(self, command, timeout):
        """One batch: persist what the Catalog lists, through the real writers."""
        from scripts import collect_ndvi
        from scripts.collect_satellite_indices import _insert_satellite_index_record

        self.child_calls += 1
        ids = [int(value) for value in command[command.index("--field-ids") + 1].split(",")]
        start = date.fromisoformat(command[command.index("--date-from") + 1])
        end = date.fromisoformat(command[command.index("--date-to") + 1])
        days = sorted({date.fromisoformat(item["properties"]["datetime"][:10])
                       for item in self.catalog.visible(start, end)})
        inserted = skipped = 0
        for field_id in ids:
            if "run_ndvi_collection_cycle.py" in command[1]:
                if not days:
                    continue
                record = {"captured_date": days[-1].isoformat(), "mean_ndvi": 0.61, "min_ndvi": 0.32,
                          "max_ndvi": 0.81, "std_ndvi": 0.05, "p10_ndvi": 0.52, "p90_ndvi": 0.7,
                          "cloud_cover_pct": None, "valid_pixels_pct": 96.0, "satellite": "Sentinel-2",
                          "field_samples_accepted": True, "field_samples_reason": "accepted"}
                if collect_ndvi.persist(field_id, self.enterprise[field_id], record, start, end):
                    inserted += 1
                else:
                    skipped += 1
            else:
                for day in days:
                    for code in INDICES:
                        if _insert_satellite_index_record(field_id=field_id, captured_date=day, index_code=code,
                                                          mean_value=0.4, satellite="Sentinel-2", min_value=0.2,
                                                          max_value=0.6, std_value=0.05, valid_pixels_pct=96.0,
                                                          enterprise_id=self.enterprise[field_id]):
                            inserted += 1
                        else:
                            skipped += 1
        write_cycle_summary(command, 0, {"success_count": len(ids), "inserted_count": inserted,
                                         "skipped_existing_count": skipped})
        return {"exit_code": 0, "timed_out": False, "stdout": "", "stderr": ""}

    def gate_factory(self, plan, args, run_id, cycle_deadline):
        # Everything real (PostGIS scope, checkpoint, chain, retry queues) except the Catalog.
        return self.collector.build_acquisition_gate(
            plan, args, run_id, cycle_deadline, search_factory=lambda: (self.catalog.search, self.catalog),
            identity=lambda: ("cdse", "official_cdse_sentinel_hub_https"))

    def cycle(self, *, gate_on=True):
        from config import settings

        self.cycles += 1
        calls_before = self.child_calls
        flags = ["--apply", "--all-active-fields", "--batch-size", "5", "--indices", "ndvi,savi,evi,ndmi,ndre",
                 "--output-dir", str(self.root / "output"), "--state-dir", str(self.root / "state"),
                 "--lock-dir", str(self.root / "locks")] + (["--acquisition-gate"] if gate_on else [])
        with patch.object(settings, "observation_detection_enabled", False):
            code, summary = self.collector.run(
                self.collector.parse_args(flags), child_runner=self.child,
                lock_acquire=lambda *a, **k: "lock", lock_release=lambda lock: None,
                run_id_factory=lambda: f"pgrun{self.cycles:03d}", gate_factory=self.gate_factory)
        run = self.sql("SELECT status, provider_status, failure_category, counters FROM satellite_collection_runs "
                       "ORDER BY started_at DESC, id DESC LIMIT 1")[0]
        return code, summary, run, self.child_calls - calls_before

    def observation_counts(self):
        return (self.scalar("SELECT count(*) FROM ndvi_records"), self.scalar("SELECT count(*) FROM satellite_index_records"))

    def assert_no_duplicates(self):
        self.assertEqual(self.scalar(
            "SELECT count(*) FROM (SELECT field_id, captured_date FROM ndvi_records "
            "GROUP BY 1,2 HAVING count(*) > 1) d"), 0)
        self.assertEqual(self.scalar(
            "SELECT count(*) FROM (SELECT field_id, captured_date, index_code FROM satellite_index_records "
            "GROUP BY 1,2,3 HAVING count(*) > 1) d"), 0)

    def freshness(self):
        return {(row["field_id"], row["index_code"]): (row["status"], row["last_accepted_scene"], row["last_accepted_at"])
                for row in self.sql("SELECT field_id, index_code, status, last_accepted_scene, last_accepted_at "
                                    "FROM satellite_field_freshness")}

    # ── tests ───────────────────────────────────────────────────────────────

    def test_full_skip_new_acquisition_and_scope_change_against_the_real_database(self):
        older, recent = self.today - timedelta(days=6), self.today - timedelta(days=1)
        self.catalog.publish(feature(older), older)
        self.catalog.publish(feature(recent), self.today)
        fields = len(self.active)

        # 1. The baseline: full collection, verified checkpoint, real rows.
        code, summary, run, children = self.cycle()
        self.assertEqual((code, summary["run_status"]), (0, "SUCCESS"), summary["diagnostics"])
        self.assertEqual(summary["acquisition_gate"]["reasons"], ["no_trusted_checkpoint"])
        self.assertEqual((run["status"], run["provider_status"]), ("succeeded", "healthy"))
        self.assertEqual(self.observation_counts(), (fields, fields * 2 * 4))
        checkpoint = json.loads((self.root / "state" / "acquisition_checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(checkpoint["status"], "verified")
        self.assertEqual(checkpoint["scope"]["field_count"], fields)
        fresh_before = self.freshness()

        # 2. Nothing changed: the Statistical stages are skipped, honestly recorded.
        self.advance()
        code, summary, run, children = self.cycle()
        self.assertEqual((code, children), (0, 0))
        self.assertEqual(summary["run_status"], "NO_NEW_ACQUISITIONS")
        self.assertEqual((run["status"], run["provider_status"], run["failure_category"]),
                         ("succeeded", "not_called", None))
        self.assertEqual(run["counters"]["acquisition_gate"]["outcome"], "NO_NEW_ACQUISITIONS")
        self.assertEqual(run["counters"]["acquisition_gate"]["statistical_collection"], "skipped")
        self.assertEqual(run["counters"]["success_count"], 0)
        self.assertEqual(self.observation_counts(), (fields, fields * 2 * 4))     # no fabricated rows
        fresh_after = self.freshness()
        # Freshness is recomputed from persisted observations only: no accepted scene moved.
        self.assertEqual({key: value[1:] for key, value in fresh_after.items()},
                         {key: value[1:] for key, value in fresh_before.items()})
        self.assertEqual(self.count("autonomous_anomaly_candidates"), 0)
        self.assertEqual(self.count("field_inspections"), 0)

        # 3. A new acquisition: full collection again, exactly the new rows, no duplicates.
        self.advance()
        newest = FakeDate.current - timedelta(days=1)
        self.catalog.publish(feature(newest))
        code, summary, run, children = self.cycle()
        self.assertEqual((code, summary["run_status"]), (0, "SUCCESS"))
        self.assertIn("new_acquisition", summary["acquisition_gate"]["reasons"])
        self.assertEqual(self.observation_counts(), (fields * 2, fields * 3 * 4))
        self.assert_no_duplicates()
        latest = {row["field_id"]: row["d"] for row in self.sql(
            "SELECT field_id, max(captured_date) AS d FROM ndvi_records GROUP BY field_id")}
        self.assertEqual(set(latest.values()), {newest})
        self.assertNotIn(self.today, {row["captured_date"] for row in self.sql("SELECT captured_date FROM ndvi_records")})

        # 4. A real boundary edit is a scope change found by the PostGIS fingerprint.
        self.advance()
        self.sql("UPDATE fields SET geometry = ST_SetSRID(ST_GeomFromText(:wkt), 4326) WHERE id = :id",
                 {"id": self.field_a,
                  "wkt": "POLYGON((64.400 39.700,64.4065 39.700,64.4065 39.705,64.400 39.705,64.400 39.700))"})
        code, summary, run, children = self.cycle()
        self.assertEqual(summary["acquisition_gate"]["reasons"], ["scope_changed"])
        self.assertGreater(children, 0)
        self.assert_no_duplicates()
        self.assertEqual(self.observation_counts(), (fields * 2, fields * 3 * 4))  # idempotent re-collection

        # 5. A deactivated field is a scope change too; then the next day skips again.
        self.advance()
        self.sql("UPDATE fields SET is_active = false WHERE id = :id", {"id": self.field_b})
        code, summary, run, children = self.cycle()
        self.assertIn("scope_changed", summary["acquisition_gate"]["reasons"])
        self.advance()
        code, summary, run, children = self.cycle()
        self.assertEqual((summary["run_status"], children, run["provider_status"]), ("NO_NEW_ACQUISITIONS", 0, "not_called"))
        self.assertEqual(self.scalar("SELECT count(*) FROM satellite_collection_runs WHERE status = 'running'"), 0)
        self.assert_no_duplicates()

    def test_a_disabled_gate_keeps_the_task252_cycle(self):
        self.catalog.publish(feature(self.today - timedelta(days=1)))
        first = self.cycle(gate_on=False)
        self.advance()
        code, summary, run, children = self.cycle(gate_on=False)
        self.assertEqual((code, summary["run_status"]), (0, "SUCCESS"))
        self.assertGreater(children, 0)
        self.assertEqual(self.catalog.calls, [])
        self.assertNotIn("acquisition_gate", summary)
        self.assertNotIn("acquisition_gate", run["counters"])
        self.assertEqual(run["provider_status"], "no_scene")
        self.assertFalse((self.root / "state" / "acquisition_checkpoint.json").exists())
        self.assertEqual(first[1]["run_status"], "SUCCESS")
        self.assert_no_duplicates()
