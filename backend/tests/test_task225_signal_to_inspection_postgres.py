"""TASK_225 (C6, C2): accepted observations -> signal -> canonical inspection.

Central acceptance: persisted accepted satellite observations make a field
eligible, the canonical collector steps record a deterministic candidate and
open ONE canonical inspection whose migration-0013 contract is complete and
whose provenance points back at the candidate; replay creates nothing new.

The pixel-anomaly path is exercised through the real INSERT as well: before
TASK_225 it omitted source_kind/source_reason, PostgreSQL rejected the row and
the IntegrityError was reported as a misleading 409 "concurrent" conflict.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from task225_support import FIELD_A, Task225Base, utc_midnight


class SignalProducerTests(Task225Base):

    def test_accepted_observations_become_one_field_scope_candidate(self):
        from services import autonomous_monitoring
        from services.pixel_ndvi import geometry_hash

        _, drops = self.seed_persistent_drop(self.field_a)
        self.seed_quiet_history(self.field_b)
        with self.collection_run("task225-detect-only") as run:
            counters = autonomous_monitoring.detect_observation_candidates(
                run, as_of=datetime.now(timezone.utc))
        self.assertEqual(counters["observation_candidates"], 1, counters)
        self.assertEqual(counters["no_signal"], 1, counters)  # field B: quiet history
        rows = self.sql(
            "SELECT a.*, ST_Equals(a.geometry, ST_Multi(f.geometry)) AS is_field, "
            "ST_AsGeoJSON(f.geometry)::json AS field_geometry "
            "FROM autonomous_anomaly_candidates a JOIN fields f ON f.id=a.field_id")
        self.assertEqual(len(rows), 1)
        candidate = rows[0]
        self.assertEqual(candidate["field_id"], self.field_a)
        self.assertEqual(candidate["enterprise_id"], self.enterprise_a)
        self.assertEqual(candidate["provider"], "sentinel2_field_statistics")
        self.assertEqual(candidate["index_code"], "ndvi")
        self.assertEqual(candidate["scene_id"], f"ndvi_record:{drops[-1]}")
        self.assertEqual(candidate["acquired_at"], utc_midnight(self.today - timedelta(days=2)))
        self.assertTrue(candidate["is_field"], "zone must be the whole field polygon")
        self.assertEqual(candidate["state"], "NEW")
        self.assertEqual(candidate["persistence_scenes"], 2)
        self.assertEqual(candidate["multi_index_agreement"], 3)
        self.assertEqual(candidate["severity"], "EXTREME")
        evidence = candidate["evidence"]
        self.assertEqual(evidence["scope"], "field")
        self.assertEqual(evidence["field_geometry_hash"], geometry_hash(candidate["field_geometry"]))
        self.assertEqual(evidence["source_snapshot"]["sampled_value"], 0.28)
        self.assertEqual(evidence["source_snapshot"]["comparison_value"], 0.64)
        self.assertEqual(evidence["contract"], "non_diagnostic")
        self.assertEqual(evidence["persistence"]["record_ids"], drops)
        self.assertEqual(len(evidence["baseline"]["record_ids"]), 10)
        self.assertEqual(evidence["quality"]["minimum_valid_pixels_pct"], 60.0)
        self.assertIn("whole field", candidate["explanation"])

    def test_candidate_opens_one_canonical_inspection_with_the_full_0013_contract(self):
        from services.pixel_ndvi import geometry_hash

        _, drops = self.seed_persistent_drop(self.field_a)
        detection, promotion = self.run_monitoring_cycle()
        self.assertEqual(detection["observation_candidates"], 1, detection)
        self.assertEqual(promotion["automatic_inspections"], 1, promotion)
        self.assertFalse(promotion["spike_guard_triggered"])
        candidate = self.sql("SELECT * FROM autonomous_anomaly_candidates")[0]
        self.assertEqual(candidate["state"], "INSPECTION_CREATED")
        inspection = self.inspection(candidate["inspection_id"])
        field_geometry = self.sql("SELECT ST_AsGeoJSON(geometry)::json AS g FROM fields WHERE id=:id",
                                  {"id": self.field_a})[0]["g"]
        # every column the 0013 contract makes mandatory for a satellite source
        expected = {
            "source_kind": "pixel_ndvi", "status": "new", "priority": "urgent",
            "field_id": self.field_a, "enterprise_id": self.enterprise_a,
            "created_by_id": self.admin.id, "assigned_to_id": None,
            "source_provider": "sentinel2_field_statistics",
            "source_item_id": f"ndvi_record:{drops[-1]}",
            "source_acquired_at": utc_midnight(self.today - timedelta(days=2)),
            "source_index_name": "ndvi", "source_sampled_value": 0.28,
            "source_comparison_value": 0.64, "source_delta": -0.36,
            "source_geometry_hash": geometry_hash(field_geometry),
            "source_snapshot_locked": True, "client_request_id": f"candidate-{candidate['id']}",
        }
        self.assertEqual({key: inspection[key] for key in expected}, expected)
        self.assertTrue(inspection["zone_is_field"])
        self.assertIsNone(inspection["source_point"])
        self.assertIn("whole field", inspection["source_reason"])
        self.assertTrue(inspection["title"].startswith("Автоматическая проверка спутниковой аномалии"))
        # provenance back to the source anomaly
        transitions = self.sql("SELECT from_state,to_state,actor_id FROM autonomous_anomaly_transitions")
        self.assertEqual(transitions, [{"from_state": "NEW", "to_state": "INSPECTION_CREATED",
                                        "actor_id": self.admin.id}])
        audit = self.audit_events(inspection["id"])
        self.assertEqual([event["event_type"] for event in audit], ["inspection_created"])
        self.assertEqual(audit[0]["event_metadata"]["origin"], "automatic_monitoring:r3-e-v1")
        self.assertEqual(audit[0]["event_metadata"]["source_reference"], f"ndvi_record:{drops[-1]}")
        self.assertEqual(audit[0]["event_metadata"]["source_kind"], "pixel_ndvi")

    def test_replay_of_the_cycle_creates_no_duplicate_candidate_or_inspection(self):
        self.seed_persistent_drop(self.field_a)
        self.run_monitoring_cycle("task225-cycle-one")
        before = self.snapshot("autonomous_anomaly_candidates", "field_inspections",
                               "autonomous_anomaly_transitions", "operational_audit_events")
        detection, promotion = self.run_monitoring_cycle("task225-cycle-two")
        self.assertEqual(detection["observation_candidates"], 0, detection)
        self.assertEqual(detection["replayed_scene"], 1, detection)
        self.assertEqual(promotion["automatic_inspections"], 0, promotion)
        after = self.snapshot("autonomous_anomaly_candidates", "field_inspections",
                              "autonomous_anomaly_transitions", "operational_audit_events")
        self.assertEqual(self.dumps(after), self.dumps(before))

    def test_a_new_scene_for_an_open_case_is_not_a_second_case(self):
        self.seed_persistent_drop(self.field_a)
        self.run_monitoring_cycle("task225-cycle-one")
        self.ndvi(self.field_a, self.today - timedelta(days=1), 0.27)
        detection, _ = self.run_monitoring_cycle("task225-cycle-two")
        self.assertEqual(detection["suppressed_active_case"], 1, detection)
        self.assertEqual(self.count("autonomous_anomaly_candidates"), 1)
        self.assertEqual(self.count("field_inspections"), 1)

    def test_rejected_observations_are_never_evidence(self):
        baseline_dates, recent = self.history_dates()
        for day, value in zip(baseline_dates, (0.62, 0.64, 0.63, 0.65, 0.66, 0.62, 0.64, 0.63, 0.65, 0.64)):
            self.ndvi(self.field_a, day, value)
        # The drop exists only in scenes the canonical contract rejects.
        self.ndvi(self.field_a, recent[0], 0.30, valid=40.0)
        self.ndvi(self.field_a, recent[1], 0.28, cloud=55.0)
        detection, _ = self.run_monitoring_cycle()
        self.assertEqual(detection["observation_candidates"], 0, detection)
        self.assertEqual(self.count("autonomous_anomaly_candidates"), 0)

    def test_short_or_stale_history_is_explicitly_not_a_signal(self):
        baseline_dates, recent = self.history_dates()
        for day in baseline_dates[:3]:
            self.ndvi(self.field_a, day, 0.64)
        self.ndvi(self.field_a, recent[1], 0.28)
        for day in baseline_dates:
            self.ndvi(self.field_b, day, 0.64)  # newest accepted scene is 20 days old
        detection, _ = self.run_monitoring_cycle()
        self.assertEqual(detection["insufficient_history"], 1, detection)
        self.assertEqual(detection["stale_observation"], 1, detection)
        self.assertEqual(self.count("autonomous_anomaly_candidates"), 0)

    def test_spike_guard_keeps_mass_signals_for_review_and_operator_promotion_is_canonical(self):
        from services import autonomous_monitoring

        second = self._field(self.enterprise_a, "T225 Field A2",
                             FIELD_A.replace("39.70", "39.71"))
        self.seed_persistent_drop(self.field_a)
        self.seed_persistent_drop(second)
        detection, promotion = self.run_monitoring_cycle()
        self.assertEqual(detection["observation_candidates"], 2, detection)
        self.assertTrue(promotion["spike_guard_triggered"], promotion)
        self.assertEqual(promotion["automatic_inspections"], 0)
        self.assertEqual(self.count("field_inspections"), 0)
        candidate = self.sql("SELECT id,version FROM autonomous_anomaly_candidates "
                             "WHERE field_id=:field", {"field": self.field_a})[0]
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as denied:
            autonomous_monitoring.create_inspection(
                self.session(), self.manager_a, candidate["id"],
                reason="Manager tries to promote", expected_version=candidate["version"])
        self.assertEqual(denied.exception.status_code, 403)
        result = autonomous_monitoring.create_inspection(
            self.session(), self.admin, candidate["id"],
            reason="Operator review confirmed the signal", expected_version=candidate["version"])
        inspection = self.inspection(result["inspection_id"])
        self.assertEqual(inspection["source_kind"], "pixel_ndvi")
        self.assertTrue(inspection["zone_is_field"])
        self.assertEqual(inspection["client_request_id"], f"candidate-{candidate['id']}")
        self.assertEqual(self.audit_events(inspection["id"])[0]["event_metadata"]["origin"],
                         "monitoring_review")
        with self.assertRaises(HTTPException) as stale:
            autonomous_monitoring.create_inspection(
                self.session(), self.admin, candidate["id"],
                reason="Second promotion of the same candidate", expected_version=candidate["version"])
        self.assertEqual(stale.exception.status_code, 409)
        self.assertEqual(self.count("field_inspections"), 1)


class ProducerPreviewTests(Task225Base):
    """The read-only preview shares the collector's decision and writes nothing."""

    def test_preview_matches_the_write_and_changes_nothing(self):
        from services import autonomous_monitoring

        _, drops = self.seed_persistent_drop(self.field_a)
        self.seed_quiet_history(self.field_b)
        as_of = datetime.now(timezone.utc)
        before = self.snapshot("autonomous_anomaly_candidates", "field_inspections",
                               "satellite_collection_runs")
        preview = autonomous_monitoring.preview_observation_candidates(self.session(), as_of=as_of)
        self.assertEqual(self.dumps(self.snapshot("autonomous_anomaly_candidates", "field_inspections",
                                                  "satellite_collection_runs")), self.dumps(before))
        self.assertEqual(preview["counters"]["observation_candidates"], 1)
        [proposed] = preview["candidates"]
        self.assertEqual((proposed["field_id"], proposed["scene_id"], proposed["automatic"]),
                         (self.field_a, f"ndvi_record:{drops[-1]}", True))
        with self.collection_run("task225-preview-parity") as run:
            written = autonomous_monitoring.detect_observation_candidates(run, as_of=as_of)
        stored = self.sql("SELECT field_id, scene_id, severity, confidence, score "
                          "FROM autonomous_anomaly_candidates")
        self.assertEqual(written["observation_candidates"], 1)
        self.assertEqual(stored, [{key: proposed[key] for key in
                                   ("field_id", "scene_id", "severity", "confidence", "score")}])
        replay = autonomous_monitoring.preview_observation_candidates(self.session(), as_of=as_of)
        self.assertEqual((replay["counters"]["observation_candidates"], replay["counters"]["replayed_scene"]),
                         (0, 1))

    def test_cli_prints_a_read_only_report(self):
        import contextlib
        import io
        import json as jsonlib

        from scripts import preview_observation_candidates as cli

        self.seed_persistent_drop(self.field_a)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(["--limit", "5"], session_factory=self.Session)
        report = jsonlib.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["mode"], "read-only preview")
        self.assertEqual(report["counters"]["observation_candidates"], 1)
        self.assertEqual(report["active_fields"], 12)
        self.assertEqual(report["by_severity"], {"EXTREME": 1})
        self.assertFalse(report["spike_guard_would_trigger"])
        self.assertEqual(self.count("autonomous_anomaly_candidates"), 0)


class PixelAnomalyInspectionTests(Task225Base):
    """The C2 path through the real INSERT, from a really persisted zone."""

    def _persist_zone(self):
        from services.pixel_anomaly_algorithm import AnomalyThresholds, PixelScene, analyze_pixel_scenes
        from services.pixel_anomaly_processing import persist_anomaly_result

        current_day, comparison_day = self.today - timedelta(days=2), self.today - timedelta(days=12)
        current_id = self.ndvi(self.field_a, current_day, 0.66)
        comparison_id = self.ndvi(self.field_a, comparison_day, 0.70)
        size = 10
        comparison_values = tuple(tuple(0.70 for _ in range(size)) for _ in range(size))
        current_values = tuple(
            tuple(0.35 if 2 <= row <= 4 and 2 <= column <= 4 else 0.70 for column in range(size))
            for row in range(size)
        )
        mask = tuple(tuple(True for _ in range(size)) for _ in range(size))
        bbox = (64.400, 39.700, 64.406, 39.705)

        def scene(record_id, day, values):
            return PixelScene(
                enterprise_id=self.enterprise_a, field_id=self.field_a, index_code="ndvi",
                record_type="ndvi_record", record_id=record_id, observed_at=day, values=values,
                quality_mask=mask, field_mask=mask, bbox=bbox, cloud_cover_pct=5.0,
                valid_pixels_pct=95.0, provider="sentinel_numeric_pixels",
                provenance={"source": "task225-test-scene"},
            )

        result = analyze_pixel_scenes(scene(current_id, current_day, current_values),
                                      scene(comparison_id, comparison_day, comparison_values))
        self.assertEqual(result.status, "detected")
        session = self.session()
        persist_anomaly_result(session, result, AnomalyThresholds(),
                               started_at=datetime.now(timezone.utc), processing_run_id=str(uuid.uuid4()))
        anomaly = self.sql("SELECT * FROM pixel_anomalies")[0]
        return anomaly, current_id

    def _payload(self, **values):
        from schemas.pixel_anomaly import CreateInspectionFromAnomalyRequest

        return CreateInspectionFromAnomalyRequest(**values)

    def test_pixel_zone_opens_one_real_canonical_inspection(self):
        from services import pixel_anomalies

        anomaly, current_id = self._persist_zone()
        # The defect first: on 66c1be8 this INSERT omitted source_kind and
        # source_reason, PostgreSQL rejected it and the caller saw a 409.
        response = pixel_anomalies.create_inspection(
            self.session(), self.manager_a, anomaly["id"],
            self._payload(assigned_to_id=self.agronomist_a.id,
                          due_date=self.today + timedelta(days=3)), "task225-pixel-001")
        self.assertTrue(response["created"])
        self.assertEqual(anomaly["provenance"]["median_current_value"], 0.35)
        self.assertEqual(anomaly["provenance"]["median_comparison_value"], 0.7)
        self.assertEqual(response["inspection"]["status"], "assigned")
        inspection = self.inspection(response["inspection"]["id"])
        self.assertEqual(inspection["source_kind"], "pixel_ndvi")
        self.assertTrue(inspection["source_reason"].startswith("Pixel anomaly zone"))
        self.assertEqual(anomaly["severity"], "critical")
        self.assertEqual(inspection["priority"], "urgent")
        self.assertEqual(inspection["source_item_id"], f"ndvi_record:{current_id}")
        self.assertEqual(inspection["source_sampled_value"], 0.35)
        self.assertEqual(inspection["source_comparison_value"], 0.7)
        self.assertEqual(inspection["source_delta"], -0.35)
        self.assertEqual(inspection["source_provider"], "sentinel_numeric_pixels")
        self.assertFalse(inspection["zone_is_field"])
        self.assertTrue(self.scalar(
            "SELECT ST_Equals(i.source_zone, a.geometry) FROM field_inspections i, pixel_anomalies a "
            "WHERE i.id=:inspection AND a.id=:anomaly",
            {"inspection": inspection["id"], "anomaly": anomaly["id"]}), "zone copied exactly")
        self.assertEqual(inspection["due_date"], self.today + timedelta(days=3))
        link = self.sql("SELECT * FROM pixel_anomaly_inspections")
        self.assertEqual([(row["anomaly_id"], row["inspection_id"]) for row in link],
                         [(anomaly["id"], inspection["id"])])
        self.assertEqual(self.scalar("SELECT status FROM pixel_anomalies"), "inspection_created")
        events = [event["event_type"] for event in self.audit_events(inspection["id"])]
        self.assertEqual(events, ["inspection_created", "inspection_assigned"])
        self.assertEqual(self.audit_events(inspection["id"])[0]["event_metadata"]["origin"],
                         f"pixel_anomaly:{anomaly['id']}")

    def test_replay_is_idempotent_and_conflicts_are_typed(self):
        from fastapi import HTTPException
        from services import pixel_anomalies

        anomaly, _ = self._persist_zone()
        first = pixel_anomalies.create_inspection(
            self.session(), self.manager_a, anomaly["id"], self._payload(), "task225-pixel-002")
        before = self.snapshot("field_inspections", "pixel_anomaly_inspections",
                               "operational_audit_events", "pixel_anomalies")
        again = pixel_anomalies.create_inspection(
            self.session(), self.manager_a, anomaly["id"], self._payload(), "task225-pixel-002")
        self.assertFalse(again["created"])
        self.assertEqual(again["inspection"]["id"], first["inspection"]["id"])
        with self.assertRaises(HTTPException) as changed:
            pixel_anomalies.create_inspection(
                self.session(), self.manager_a, anomaly["id"],
                self._payload(title="A different request body"), "task225-pixel-002")
        self.assertEqual(changed.exception.status_code, 409)
        with self.assertRaises(HTTPException) as second_key:
            pixel_anomalies.create_inspection(
                self.session(), self.manager_a, anomaly["id"], self._payload(), "task225-pixel-003")
        self.assertEqual(second_key.exception.status_code, 409)
        self.assertEqual(second_key.exception.detail, "Pixel anomaly is not open")
        self.assertEqual(self.dumps(self.snapshot("field_inspections", "pixel_anomaly_inspections",
                                                  "operational_audit_events", "pixel_anomalies")),
                         self.dumps(before))

    def test_foreign_tenant_cannot_see_or_promote_the_zone(self):
        from fastapi import HTTPException
        from services import pixel_anomalies

        anomaly, _ = self._persist_zone()
        for caller in (self.manager_b, self.agronomist_b):
            with self.assertRaises(HTTPException) as hidden:
                pixel_anomalies.create_inspection(
                    self.session(), caller, anomaly["id"], self._payload(), "task225-pixel-foreign")
            self.assertEqual(hidden.exception.status_code, 404)
        missing = None
        with self.assertRaises(HTTPException) as absent:
            pixel_anomalies.create_inspection(
                self.session(), self.manager_b, anomaly["id"] + 999, self._payload(), "task225-pixel-absent")
        missing = absent.exception
        self.assertEqual((hidden.exception.status_code, hidden.exception.detail),
                         (missing.status_code, missing.detail))
        self.assertEqual(self.count("field_inspections"), 0)

    def test_candidate_path_does_not_duplicate_a_zone_that_already_has_an_inspection(self):
        from services import pixel_anomalies

        anomaly, _ = self._persist_zone()
        pixel_anomalies.create_inspection(
            self.session(), self.manager_a, anomaly["id"], self._payload(), "task225-pixel-004")
        # make the pixel candidate automatic-eligible, then run promotion
        self.sql("UPDATE pixel_anomalies SET confidence=0.95, severity='critical'")
        with self.collection_run("task225-pixel-promotion") as run:
            from services import autonomous_monitoring
            promotion = autonomous_monitoring.reconcile_pixel_candidates(run)
        self.assertEqual(promotion["inserted_candidates"], 1, promotion)
        self.assertEqual(promotion["automatic_inspections"], 0, promotion)
        self.assertEqual(promotion["automatic_refused"], 1, promotion)
        self.assertEqual(self.count("field_inspections"), 1)
        self.assertEqual(self.scalar("SELECT state FROM autonomous_anomaly_candidates"), "NEW")

    def test_zone_without_a_value_snapshot_is_refused_without_a_write(self):
        from fastapi import HTTPException
        from services import pixel_anomalies

        anomaly, _ = self._persist_zone()
        self.sql("UPDATE pixel_anomalies SET provenance = provenance - 'median_current_value' "
                 "- 'median_comparison_value'")
        with self.assertRaises(HTTPException) as refused:
            pixel_anomalies.create_inspection(
                self.session(), self.manager_a, anomaly["id"], self._payload(), "task225-pixel-005")
        self.assertEqual(refused.exception.status_code, 409)
        self.assertEqual(self.count("field_inspections"), 0)
        self.assertEqual(self.scalar("SELECT status FROM pixel_anomalies"), "open")
