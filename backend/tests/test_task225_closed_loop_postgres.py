"""TASK_225: the canonical closed loop, end to end, against PostgreSQL.

accepted observation -> deterministic signal -> canonical inspection ->
finding/review -> TASK_220 plan -> work + execution evidence -> later accepted
observation -> collector reconciliation -> IMPROVED -> normal close.

Every step goes through production code: the collector's detection and
promotion steps, the FastAPI application (TestClient over main.app with only
authentication and the session factory overridden) and the collector's
verification reconciliation. Time moves through closed_loop_agronomy.now, the
module's single clock, so completion and the seven-day wait are real.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading

from task225_support import FIELD_A_EDITED, INSIDE_A, JPEG, Task225Base


FINDING = {
    "cause": "water_stress", "severity": "high", "affected_area_pct": 60.0,
    "gps_accuracy_m": 4.0, "sync_state": "server",
    "observations": "Wilting along the eastern irrigation line; soil dry at 20 cm.",
    "recommended_action": "Restore irrigation delivery and re-measure soil moisture.",
}


class ClosedLoopFlow(Task225Base):
    """Shared driver: one satellite-origin case up to pending_verification."""

    def open_case(self):
        _, drops = self.seed_persistent_drop(self.field_a)
        self.run_monitoring_cycle()
        candidate = self.sql("SELECT * FROM autonomous_anomaly_candidates")[0]
        self.candidate_id = candidate["id"]
        self.inspection_id = candidate["inspection_id"]
        self.baseline_record = drops[-1]
        return self.inspection_id

    def inspect_and_confirm(self, inspection_id, *, review=True):
        manager, agronomist = self.client(self.manager_a), None
        version = self.inspection(inspection_id)["version"]
        body = manager.post(f"/api/anomaly-inspections/{inspection_id}/assignment",
                            json={"expected_version": version, "assigned_to_id": self.agronomist_a.id})
        self.assertEqual(body.status_code, 200, body.text)
        agronomist = self.client(self.agronomist_a)
        version = body.json()["inspection"]["version"]
        body = agronomist.post(f"/api/anomaly-inspections/{inspection_id}/start",
                               json={"expected_version": version})
        self.assertEqual(body.status_code, 200, body.text)
        version = body.json()["inspection"]["version"]
        body = agronomist.put(f"/api/anomaly-inspections/{inspection_id}/finding", json={
            **FINDING, "expected_version": version,
            "inspected_at": datetime.now(timezone.utc).isoformat(),
            "gps_point": {"longitude": INSIDE_A[0], "latitude": INSIDE_A[1]},
        })
        self.assertEqual(body.status_code, 200, body.text)
        version = body.json()["inspection"]["version"]
        body = agronomist.post(f"/api/anomaly-inspections/{inspection_id}/submit",
                               json={"expected_version": version})
        self.assertEqual(body.status_code, 200, body.text)
        version = body.json()["inspection"]["version"]
        if not review:
            return version
        manager = self.client(self.manager_a)
        body = manager.post(f"/api/anomaly-inspections/{inspection_id}/review",
                            json={"expected_version": version, "decision": "confirmed"})
        self.assertEqual(body.status_code, 200, body.text)
        self.assertEqual(body.json()["inspection"]["status"], "confirmed")

    def plan_and_execute(self, inspection_id, *, key_prefix="task225-plan"):
        manager = self.client(self.manager_a)
        response = manager.post("/api/agronomy-plans", json={
            "inspection_id": inspection_id, "reason": "Confirmed water stress on the satellite zone",
        }, headers={"Idempotency-Key": f"{key_prefix}-draft"})
        self.assertEqual(response.status_code, 201, response.text)
        plan = response.json()
        due = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        response = manager.post(f"/api/agronomy-plans/{plan['plan_id']}/work", json={
            "expected_version": plan["version"], "reason": "Irrigation repair assigned",
            "category": "irrigation", "instruction": "Repair the eastern line and irrigate the block.",
            "assigned_to_id": self.agronomist_a.id, "due_at": due,
        }, headers={"Idempotency-Key": f"{key_prefix}-work"})
        self.assertEqual(response.status_code, 201, response.text)
        work = response.json()
        item_id, item_version = work["item_id"], work["item_version"]
        response = manager.post(f"/api/agronomy-plans/{plan['plan_id']}/transition", json={
            "expected_version": work["version"], "reason": "Approved by the farm manager",
            "operation": "approve",
        }, headers={"Idempotency-Key": f"{key_prefix}-approve"})
        self.assertEqual(response.status_code, 200, response.text)
        plan_version = response.json()["version"]
        agronomist = self.client(self.agronomist_a)
        response = agronomist.post(f"/api/agronomy-plans/{plan['plan_id']}/work/{item_id}/transition", json={
            "expected_version": item_version, "expected_plan_version": plan_version,
            "reason": "Crew on site", "operation": "start",
        }, headers={"Idempotency-Key": f"{key_prefix}-start"})
        self.assertEqual(response.status_code, 200, response.text)
        plan_version, item_version = response.json()["version"], response.json()["item_version"]
        response = agronomist.post(
            f"/api/agronomy-plans/{plan['plan_id']}/work/{item_id}/evidence",
            data={"expected_plan_version": str(plan_version), "expected_version": str(item_version),
                  "key": f"{key_prefix}-photo"},
            files={"photo": ("repair.jpg", JPEG, "image/jpeg")},
        )
        self.assertEqual(response.status_code, 201, response.text)
        plan_version, item_version = response.json()["version"], response.json()["item_version"]
        response = agronomist.post(f"/api/agronomy-plans/{plan['plan_id']}/work/{item_id}/transition", json={
            "expected_version": item_version, "expected_plan_version": plan_version,
            "reason": "Repair finished", "operation": "complete",
            "result_note": "Line repaired, 40 mm applied, soil moisture restored.",
        }, headers={"Idempotency-Key": f"{key_prefix}-complete"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "pending_verification")
        return plan["plan_id"]

    def reconcile(self, days_after_completion=10):
        from services.closed_loop_agronomy import reconcile_pending

        self.clock_offset = timedelta(days=days_after_completion)
        try:
            return reconcile_pending(self.Session, limit=100)
        finally:
            self.clock_offset = timedelta(0)

    def post_observation(self, value, *, valid=100.0, days_after=9):
        return self.ndvi(self.field_a, self.today + timedelta(days=days_after), value, valid=valid)

    def transition(self, plan_id, operation, *, user=None, key=None, reason="Resolution recorded by manager"):
        client = self.client(user or self.manager_a)
        return client.post(f"/api/agronomy-plans/{plan_id}/transition", json={
            "expected_version": self.plan(plan_id)["version"], "reason": reason, "operation": operation,
        }, headers={"Idempotency-Key": key or f"task225-{operation}-{plan_id}"})

    def pending_case(self):
        inspection_id = self.open_case()
        self.inspect_and_confirm(inspection_id)
        return inspection_id, self.plan_and_execute(inspection_id)

    def case(self, inspection_id):
        from services import operational_center

        queue = operational_center.list_queue(self.session(), self.manager_a, {"limit": 200, "offset": 0})
        return next(item for item in queue["items"] if item["case_key"] == f"inspection:{inspection_id}")


class EndToEndTests(ClosedLoopFlow):

    def test_satellite_origin_case_is_verified_improved_and_closes_normally(self):
        from services import operational_notifications

        inspection_id = self.open_case()
        inspection = self.inspection(inspection_id)
        self.assertEqual((inspection["source_kind"], inspection["status"]), ("pixel_ndvi", "new"))
        self.assertEqual(self.case(inspection_id)["remediation_status"], "needs_inspection")

        self.inspect_and_confirm(inspection_id)
        self.assertEqual(self.case(inspection_id)["remediation_status"], "awaiting_decision")
        plan_id = self.plan_and_execute(inspection_id)
        plan = self.plan(plan_id)
        scope = plan["input_snapshot"]["verification_scope"]
        self.assertEqual(scope["kind"], "field")
        self.assertEqual(scope["source"], f"candidate:{self.candidate_id}")
        self.assertEqual(plan["candidate_id"], self.candidate_id)
        self.assertEqual(plan["baseline_record_id"], self.baseline_record)
        self.assertEqual(plan["baseline"]["zone_key"], scope["zone_key"])
        self.assertEqual(self.case(inspection_id)["remediation_status"], "awaiting_satellite_verification")

        # notifications while the plan waits for a later observation
        session = self.session()
        operational_notifications.reconcile_notifications(session, apply=True)
        waiting = self.sql("SELECT status FROM operational_notifications "
                           "WHERE notification_type='awaiting_satellite_verification'")
        self.assertTrue(waiting and all(row["status"] == "unread" for row in waiting))

        too_early = self.reconcile(days_after_completion=5)
        self.assertEqual(self.plan(plan_id)["verification_status"], "TOO_EARLY", too_early)

        post_id = self.post_observation(0.46)
        counters = self.reconcile()
        self.assertEqual(counters["improved"], 1, counters)
        plan = self.plan(plan_id)
        self.assertEqual((plan["status"], plan["verification_status"]), ("pending_verification", "IMPROVED"))
        verification = self.sql("SELECT * FROM agronomy_verifications WHERE status='IMPROVED'")[0]
        self.assertEqual((verification["baseline_record_id"], verification["post_record_id"]),
                         (self.baseline_record, post_id))
        measurements = verification["measurements"]
        self.assertEqual(measurements["delta"], 0.18)
        self.assertEqual(measurements["scope"]["reason"], "comparable")
        self.assertEqual(measurements["scope"]["zone_key"], scope["zone_key"])
        self.assertEqual(measurements["post"]["zone_key"], scope["zone_key"])
        self.assertEqual(measurements["statistics_scope"], "field")
        self.assertIn("не доказывает причинный эффект", measurements["explanation"])
        self.assertEqual(self.case(inspection_id)["remediation_status"], "improved_awaiting_closure")

        response = self.transition(plan_id, "close", key="task225-normal-close")
        self.assertEqual(response.status_code, 200, response.text)
        plan = self.plan(plan_id)
        self.assertEqual((plan["status"], plan["verification_status"]), ("closed", "IMPROVED"))
        self.assertEqual(self.scalar("SELECT state FROM autonomous_anomaly_candidates"), "RESOLVED")

        events = self.plan_events(plan_id)
        self.assertNotIn("override_close", events)
        self.assertEqual(events, [
            "recommendation_created", "work_created", "approve", "work_start",
            "execution_evidence", "work_complete", "verification", "verification", "close",
        ])
        audit = [event["event_type"] for event in self.audit_events(inspection_id)]
        self.assertEqual(audit, ["inspection_created", "inspection_assigned", "inspection_started",
                                 "finding_saved", "inspection_submitted", "inspection_confirmed"])
        transitions = self.sql("SELECT from_state,to_state FROM autonomous_anomaly_transitions ORDER BY id")
        self.assertEqual([(row["from_state"], row["to_state"]) for row in transitions],
                         [("NEW", "INSPECTION_CREATED"), ("INSPECTION_CREATED", "RESOLVED")])
        self.assertEqual(self.count("inspection_evidence", "agronomy_work_item_id IS NOT NULL"), 1)

        # terminal state is reflected by notifications and the projection
        operational_notifications.reconcile_notifications(self.session(), apply=True)
        waiting = self.sql("SELECT status FROM operational_notifications "
                           "WHERE notification_type='awaiting_satellite_verification'")
        self.assertTrue(waiting and all(row["status"] == "resolved" for row in waiting))
        case = self.case(inspection_id)
        self.assertEqual((case["remediation_status"], case["operational_status"]),
                         ("improved_closed", "improved_closed"))

        # replay is idempotent end to end
        frozen = self.snapshot("agronomy_plans", "agronomy_verifications", "agronomy_events",
                               "autonomous_anomaly_candidates", "field_inspections",
                               "operational_audit_events", "operational_notifications")
        self.assertEqual(self.reconcile()["eligible"], 0)
        self.run_monitoring_cycle("task225-replay-cycle")
        replay = self.client(self.manager_a).post(
            f"/api/agronomy-plans/{plan_id}/transition",
            json={"expected_version": plan["version"] - 1, "reason": "Resolution recorded by manager",
                  "operation": "close"},
            headers={"Idempotency-Key": "task225-normal-close"})
        self.assertEqual((replay.status_code, replay.json()["status"]), (200, "closed"))
        operational_notifications.reconcile_notifications(self.session(), apply=True)
        self.assertEqual(self.dumps(self.snapshot(
            "agronomy_plans", "agronomy_verifications", "agronomy_events",
            "autonomous_anomaly_candidates", "field_inspections",
            "operational_audit_events", "operational_notifications")), self.dumps(frozen))

    def test_manual_unscoped_case_keeps_field_level_verification(self):
        """A case without a spatial source still verifies on field statistics."""
        self.seed_quiet_history(self.field_a)
        manager = self.client(self.manager_a)
        response = manager.post("/api/anomaly-inspections", json={
            "field_id": self.field_a, "source_kind": "manual",
            "reason": "Agronomist reported uneven emergence", "priority": "normal",
            "due_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        }, headers={"Idempotency-Key": "task225-manual-case"})
        self.assertEqual(response.status_code, 201, response.text)
        inspection_id = response.json()["inspection"]["id"]
        self.inspect_and_confirm(inspection_id)
        plan_id = self.plan_and_execute(inspection_id, key_prefix="task225-manual")
        self.assertEqual(self.plan(plan_id)["input_snapshot"]["verification_scope"]["kind"], "unscoped")
        self.post_observation(0.72)
        self.reconcile()
        self.assertEqual(self.plan(plan_id)["verification_status"], "IMPROVED")


class CompanionOutcomeTests(ClosedLoopFlow):

    def test_unchanged_is_not_improvement_and_blocks_normal_close(self):
        inspection_id, plan_id = self.pending_case()
        self.post_observation(0.30)  # baseline 0.28: +0.02 is below the 0.05 threshold
        self.assertEqual(self.reconcile()["unchanged"], 1)
        self.assertEqual(self.plan(plan_id)["verification_status"], "NO_MATERIAL_CHANGE")
        self.assertEqual(self.case(inspection_id)["remediation_status"], "not_improved")
        refused = self.transition(plan_id, "close")
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(self.plan(plan_id)["status"], "pending_verification")

    def test_worsened_reopens_the_case_for_another_cycle(self):
        inspection_id, plan_id = self.pending_case()
        self.post_observation(0.18)
        self.assertEqual(self.reconcile()["worsened"], 1)
        self.assertEqual(self.plan(plan_id)["verification_status"], "WORSENED")
        self.assertEqual(self.case(inspection_id)["remediation_status"], "not_improved")
        response = self.transition(plan_id, "rework", reason="Worsened after repair; second cycle")
        self.assertEqual(response.status_code, 200, response.text)
        plan = self.plan(plan_id)
        self.assertEqual((plan["status"], plan["cycle"], plan["verification_status"]),
                         ("rework", 2, "PENDING_DATA"))
        self.assertEqual(self.case(inspection_id)["remediation_status"], "reopened")
        self.assertEqual(self.scalar("SELECT state FROM autonomous_anomaly_candidates"), "INSPECTION_CREATED")
        self.assertNotIn("override_close", self.plan_events(plan_id))

    def test_closed_case_can_be_reopened_through_the_canonical_plan(self):
        inspection_id, plan_id = self.pending_case()
        self.post_observation(0.46)
        self.reconcile()
        self.assertEqual(self.transition(plan_id, "close").status_code, 200)
        response = self.transition(plan_id, "reopen", key="task225-reopen",
                                   reason="Symptoms returned during a field visit")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.plan(plan_id)["status"], "rework")
        self.assertEqual(self.scalar("SELECT state FROM autonomous_anomaly_candidates"), "INSPECTION_CREATED")
        self.assertEqual(self.case(inspection_id)["remediation_status"], "reopened")

    def test_bad_quality_post_observation_is_blocked_not_improved(self):
        inspection_id, plan_id = self.pending_case()
        self.post_observation(0.80, valid=30.0)  # a large "improvement" in a rejected scene
        counters = self.reconcile()
        self.assertEqual(counters["pending_quality_provider"], 1, counters)
        plan = self.plan(plan_id)
        self.assertEqual(plan["verification_status"], "QUALITY_BLOCKED")
        self.assertEqual(self.count("agronomy_verifications", "post_record_id IS NOT NULL"), 0)
        case = self.case(inspection_id)
        self.assertEqual(case["remediation_status"], "verification_blocked")
        self.assertTrue(case["blocked"])
        self.assertEqual(self.transition(plan_id, "close").status_code, 409)

    def test_missing_post_observation_stays_pending_data(self):
        inspection_id, plan_id = self.pending_case()
        self.reconcile()
        self.assertEqual(self.plan(plan_id)["verification_status"], "PENDING_DATA")
        self.assertEqual(self.case(inspection_id)["remediation_status"], "awaiting_satellite_verification")

    def test_field_boundary_edit_makes_the_zone_non_comparable(self):
        inspection_id, plan_id = self.pending_case()
        self.sql("UPDATE fields SET geometry=ST_SetSRID(ST_GeomFromText(:polygon),4326) WHERE id=:id",
                 {"polygon": FIELD_A_EDITED, "id": self.field_a})
        self.post_observation(0.46)
        self.reconcile()
        plan = self.plan(plan_id)
        self.assertEqual(plan["verification_status"], "INCONCLUSIVE")
        measurements = self.sql("SELECT measurements FROM agronomy_verifications "
                                "WHERE status='INCONCLUSIVE'")[0]["measurements"]
        self.assertEqual(measurements["scope"]["reason"], "zone_geometry_changed")
        self.assertIsNone(measurements["post"]["zone_key"])
        self.assertEqual(self.case(inspection_id)["remediation_status"], "verification_blocked")
        self.assertEqual(self.transition(plan_id, "close").status_code, 409)

    def test_sub_field_zone_is_honestly_inconclusive(self):
        """No stored zonal statistic exists for a zone smaller than the field."""
        self.seed_quiet_history(self.field_a)
        response = self.client(self.manager_a).post("/api/anomaly-inspections", json={
            "field_id": self.field_a, "source_kind": "manual",
            "reason": "Operator drew the damaged block", "priority": "high",
            "zone": {"type": "Polygon", "coordinates": [[[64.401, 39.701], [64.403, 39.701],
                     [64.403, 39.703], [64.401, 39.703], [64.401, 39.701]]]},
            "due_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        }, headers={"Idempotency-Key": "task225-subfield-case"})
        self.assertEqual(response.status_code, 201, response.text)
        inspection_id = response.json()["inspection"]["id"]
        self.inspect_and_confirm(inspection_id)
        plan_id = self.plan_and_execute(inspection_id, key_prefix="task225-subfield")
        self.assertEqual(self.plan(plan_id)["input_snapshot"]["verification_scope"]["kind"], "subfield")
        self.post_observation(0.90)
        self.reconcile()
        self.assertEqual(self.plan(plan_id)["verification_status"], "INCONCLUSIVE")
        measurements = self.sql("SELECT measurements FROM agronomy_verifications "
                                "WHERE status='INCONCLUSIVE'")[0]["measurements"]
        self.assertEqual(measurements["scope"]["reason"], "zone_statistics_unavailable")
        self.assertIn("локальной зоны нет сохранённой статистики", measurements["explanation"])

    def test_override_closure_is_never_reported_as_improved(self):
        inspection_id, plan_id = self.pending_case()
        self.post_observation(0.18)
        self.reconcile()
        response = self.transition(plan_id, "override_close", reason="Crop destroyed by hail; case closed")
        self.assertEqual(response.status_code, 200, response.text)
        case = self.case(inspection_id)
        self.assertEqual((case["remediation_status"], case["operational_status"]),
                         ("closed_without_improvement", "closed_without_improvement"))


class ConcurrencyTests(ClosedLoopFlow):

    def test_concurrent_closure_has_one_winner_and_one_deterministic_conflict(self):
        _, plan_id = self.pending_case()
        self.post_observation(0.46)
        self.reconcile()
        version = self.plan(plan_id)["version"]
        barrier = threading.Barrier(2)

        def close(key):
            from fastapi import HTTPException
            from schemas.closed_loop_agronomy import PlanCommand
            from services import closed_loop_agronomy

            payload = PlanCommand(expected_version=version, reason="Concurrent close attempt",
                                  operation="close")
            session = self.Session()
            barrier.wait()
            try:
                return closed_loop_agronomy.transition(session, self.manager_a, plan_id, payload, key)["status"]
            except HTTPException as error:
                return error.status_code
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = sorted(map(str, pool.map(close, ("task225-race-a", "task225-race-b"))))
        self.assertEqual(outcomes, ["409", "closed"])
        events = self.plan_events(plan_id)
        self.assertEqual(events.count("close"), 1)
        self.assertEqual(events.count("conflict"), 1)
        self.assertEqual(self.plan(plan_id)["status"], "closed")

    def test_concurrent_candidate_promotion_opens_exactly_one_inspection(self):
        from services import autonomous_monitoring

        second = self._field(self.enterprise_a, "T225 Field A2", FIELD_A_EDITED.replace("39.70", "39.72"))
        self.seed_persistent_drop(self.field_a)
        self.seed_persistent_drop(second)  # two signals trip the spike guard: both stay NEW
        self.run_monitoring_cycle()
        candidate = self.sql("SELECT id,version FROM autonomous_anomaly_candidates "
                             "WHERE field_id=:field", {"field": self.field_a})[0]
        barrier = threading.Barrier(2)

        def promote(_):
            from fastapi import HTTPException

            session = self.Session()
            barrier.wait()
            try:
                return autonomous_monitoring.create_inspection(
                    session, self.admin, candidate["id"],
                    reason="Concurrent operator promotion", expected_version=candidate["version"])["state"]
            except HTTPException as error:
                return error.status_code
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = sorted(map(str, pool.map(promote, range(2))))
        self.assertEqual(outcomes, ["409", "INSPECTION_CREATED"])
        self.assertEqual(self.count("field_inspections", "field_id=:field", {"field": self.field_a}), 1)
        self.assertEqual(self.count("autonomous_anomaly_transitions",
                                    "candidate_id=:id", {"id": candidate["id"]}), 1)


class ProjectionTests(ClosedLoopFlow):

    def test_sql_projection_matches_the_decision_table_for_every_combination(self):
        from services import remediation_status

        inspections = ("pending", "new", "assigned", "in_progress", "submitted", "confirmed",
                       "rejected", "cancelled", "completed")
        plans = (None, "draft", "approved", "in_progress", "pending_verification", "rework",
                 "closed", "cancelled", "superseded")
        verifications = ("PENDING_DATA", "TOO_EARLY", "CLOUD_BLOCKED", "QUALITY_BLOCKED",
                         "PROVIDER_DEGRADED", "INCONCLUSIVE", "IMPROVED", "NO_MATERIAL_CHANGE", "WORSENED")
        combos = [(i, p, v) for i in inspections for p in plans for v in verifications]
        values = ",".join(f"({n},'{i}',{'NULL' if p is None else repr(p)},'{v}')"
                          for n, (i, p, v) in enumerate(combos))
        rows = self.sql(
            "SELECT n," + remediation_status.inspection_status_sql("i", "p") + " AS state "
            f"FROM (VALUES {values}) AS combo(n, inspection_status, plan_status, verification_status) "
            "CROSS JOIN LATERAL (SELECT combo.inspection_status AS status) i "
            "CROSS JOIN LATERAL (SELECT combo.plan_status AS status, "
            "combo.verification_status AS verification_status) p ORDER BY n")
        self.assertEqual(len(rows), len(combos))
        for row, (i, p, v) in zip(rows, combos):
            self.assertEqual(row["state"], remediation_status.inspection_status(i, p, v), (i, p, v))

    def test_candidate_case_detail_loads(self):
        """Before TASK_225 the candidate snapshot selected a column the table lacks."""
        second = self._field(self.enterprise_a, "T225 Field A2", FIELD_A_EDITED.replace("39.70", "39.72"))
        self.seed_persistent_drop(self.field_a)
        self.seed_persistent_drop(second)
        self.run_monitoring_cycle()  # spike guard: both candidates stay NEW, i.e. candidate cases
        candidate = self.scalar("SELECT id FROM autonomous_anomaly_candidates WHERE field_id=:field",
                                {"field": self.field_a})
        response = self.client(self.manager_a).get(f"/api/operational-center/cases/candidate:{candidate}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["case"]["remediation_status"], "needs_inspection")
        self.assertEqual(body["source_snapshot"]["evidence"]["scope"], "field")
