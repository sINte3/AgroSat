"""TASK_225 (C5, C13, tenancy): one live state machine per concern.

Fixtures hold both generations at once, as production does: a legacy TASK_209
inspection (pending), a legacy inspection in progress, a legacy completed
inspection with a result and an open TASK_209 corrective action, a canonical
inspection in progress and a live TASK_220 plan. Every retired write must
answer 410 and change nothing; every canonical write must refuse a legacy row
except the reasoned close-out; tenant boundaries stay non-enumerating.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from task225_support import JPEG
from test_task225_closed_loop_postgres import ClosedLoopFlow


LEGACY_STATE_TABLES = (
    "field_inspections", "inspection_results", "inspection_evidence", "corrective_actions",
    "action_verification_requests", "operational_audit_events", "agronomy_plans",
    "agronomy_work_items", "agronomy_events",
)


class GenerationsFixture(ClosedLoopFlow):

    def setUp(self):
        super().setUp()
        self.legacy_pending = self._legacy_inspection("pending")
        self.legacy_active = self._legacy_inspection("in_progress", assigned=self.agronomist_a.id,
                                                     field_id=self._second_field())
        self.legacy_done = self._legacy_inspection("completed", field_id=self.field_b,
                                                   enterprise_id=self.enterprise_b,
                                                   created_by=self.manager_b.id)
        self.legacy_result = self.scalar(
            "INSERT INTO inspection_results (inspection_id, field_id, enterprise_id, recorded_by_id, "
            "cause_code, cause_details) VALUES (:inspection, :field, :enterprise, :actor, 'irrigation', "
            "'Blocked line') RETURNING id",
            {"inspection": self.legacy_done, "field": self.field_b, "enterprise": self.enterprise_b,
             "actor": self.manager_b.id})
        self.legacy_action = self.scalar(
            "INSERT INTO corrective_actions (inspection_id, result_id, field_id, enterprise_id, "
            "created_by_id, owner_id, description, due_date, status) VALUES (:inspection, :result, "
            ":field, :enterprise, :actor, :actor, 'Repair the line', :due, 'open') RETURNING id",
            {"inspection": self.legacy_done, "result": self.legacy_result, "field": self.field_b,
             "enterprise": self.enterprise_b, "actor": self.manager_b.id,
             "due": self.today + timedelta(days=5)})
        self.seed_quiet_history(self.field_a)
        self.current = self._canonical_in_progress()
        created_b = self.client(self.manager_b).post("/api/anomaly-inspections", json={
            "field_id": self.field_b, "source_kind": "manual", "reason": "Tenant B scouting request",
            "priority": "normal", "due_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        }, headers={"Idempotency-Key": "task225-boundary-tenant-b"})
        self.assertEqual(created_b.status_code, 201, created_b.text)
        self.current_b = created_b.json()["inspection"]["id"]
        self.plan_inspection = self._confirmed_manual_inspection()
        response = self.client(self.manager_a).post("/api/agronomy-plans", json={
            "inspection_id": self.plan_inspection, "reason": "Plan for the confirmed manual case"},
            headers={"Idempotency-Key": "task225-boundary-plan"})
        self.assertEqual(response.status_code, 201, response.text)
        self.plan_id = response.json()["plan_id"]

    def _second_field(self):
        return self._field(self.enterprise_a, "T225 Legacy Field",
                           "POLYGON((64.450 39.700,64.456 39.700,64.456 39.705,64.450 39.705,64.450 39.700))")

    def _legacy_inspection(self, status, *, assigned=None, field_id=None, enterprise_id=None,
                           created_by=None):
        """A row as TASK_209 wrote it before migration 0013."""
        timestamps = {"completed": ", completed_at", "in_progress": ", started_at"}.get(status, "")
        values = {"completed": ", now()", "in_progress": ", now()"}.get(status, "")
        return self.scalar(
            "INSERT INTO field_inspections (field_id, enterprise_id, created_by_id, assigned_to_id, "
            "client_request_id, request_fingerprint, source, title, status, source_kind, source_reason, "
            f"priority{timestamps}) VALUES (:field, :enterprise, :creator, :assigned, :key, :fingerprint, "
            f"'manual', 'Legacy inspection', :status, 'legacy', 'Legacy inspection', 'normal'{values}) "
            "RETURNING id",
            {"field": field_id or self.field_a, "enterprise": enterprise_id or self.enterprise_a,
             "creator": created_by or self.manager_a.id, "assigned": assigned,
             "key": f"legacy-{status}-{field_id or self.field_a}", "fingerprint": "f" * 64,
             "status": status})

    def _canonical_in_progress(self):
        manager = self.client(self.manager_a)
        response = manager.post("/api/anomaly-inspections", json={
            "field_id": self.field_a, "source_kind": "manual", "reason": "Scout the north block",
            "priority": "normal", "assigned_to_id": self.agronomist_a.id,
            "due_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        }, headers={"Idempotency-Key": "task225-boundary-current"})
        self.assertEqual(response.status_code, 201, response.text)
        inspection = response.json()["inspection"]
        started = self.client(self.agronomist_a).post(
            f"/api/anomaly-inspections/{inspection['id']}/start",
            json={"expected_version": inspection["version"]})
        self.assertEqual(started.status_code, 200, started.text)
        return inspection["id"]

    def _confirmed_manual_inspection(self):
        response = self.client(self.manager_a).post("/api/anomaly-inspections", json={
            "field_id": self.field_a, "source_kind": "manual", "reason": "Confirm the south block",
            "priority": "high",
            "due_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        }, headers={"Idempotency-Key": "task225-boundary-planned"})
        self.assertEqual(response.status_code, 201, response.text)
        inspection_id = response.json()["inspection"]["id"]
        self.inspect_and_confirm(inspection_id)
        return inspection_id

    def assert_unchanged(self, before):
        self.assertEqual(self.dumps(self.snapshot(*LEGACY_STATE_TABLES)), self.dumps(before))


class RetiredEndpointTests(GenerationsFixture):

    def test_legacy_result_endpoint_cannot_complete_a_current_inspection(self):
        before = self.snapshot(*LEGACY_STATE_TABLES)
        response = self.client(self.manager_a).post(
            f"/api/field-inspections/{self.current}/result",
            json={"expected_version": self.inspection(self.current)["version"], "cause_code": "irrigation"},
            headers={"Idempotency-Key": "task225-legacy-result"})
        self.assertEqual(response.status_code, 410, response.text)
        self.assertIn("/api/anomaly-inspections", response.json()["detail"]["replacement"])
        self.assertEqual(self.inspection(self.current)["status"], "in_progress")
        self.assert_unchanged(before)

    def test_every_retired_write_is_410_and_writes_nothing(self):
        manager_a, manager_b = self.client(self.manager_a), self.client(self.manager_b)
        calls = (
            (manager_a, "post", "/api/field-inspections", {"field_id": self.field_a, "title": "New legacy"}),
            (manager_a, "patch", f"/api/field-inspections/{self.legacy_pending}", {"expected_version": 1}),
            (manager_a, "post", f"/api/field-inspections/{self.legacy_pending}/start", {"expected_version": 1}),
            (manager_a, "post", f"/api/field-inspections/{self.legacy_active}/complete", {"expected_version": 1}),
            (manager_a, "post", f"/api/field-inspections/{self.legacy_pending}/cancel", {"expected_version": 1}),
            (manager_b, "post", f"/api/field-inspections/{self.legacy_done}/result", {"expected_version": 1}),
            (manager_b, "post", f"/api/field-inspections/{self.legacy_done}/evidence", {"expected_version": 1}),
            (manager_b, "post", f"/api/field-inspections/{self.legacy_done}/actions", {"expected_inspection_version": 1}),
            (manager_b, "patch", f"/api/operational-actions/{self.legacy_action}", {"expected_version": 1}),
            (manager_b, "post", f"/api/operational-actions/{self.legacy_action}/close", {"expected_version": 1}),
            (manager_b, "post", f"/api/operational-actions/{self.legacy_action}/reopen", {"expected_version": 1}),
            (manager_b, "post", f"/api/operational-actions/{self.legacy_action}/verification-requests", {}),
            (manager_b, "post", "/api/verification-requests/1/resolve", {"expected_version": 1}),
            (manager_a, "post", f"/api/anomaly-inspections/{self.plan_inspection}/actions",
             {"expected_inspection_version": 1, "action_type": "irrigation", "owner_id": self.agronomist_a.id,
              "instructions": "Legacy action", "due_at": datetime.now(timezone.utc).isoformat()}),
            (manager_a, "post", f"/api/anomaly-inspections/actions/{self.legacy_action}/transition",
             {"expected_version": 1, "transition": "start"}),
            (manager_a, "post", f"/api/anomaly-inspections/actions/{self.legacy_action}/verify",
             {"expected_version": 1, "result": "effective", "notes": "Looks fine"}),
        )
        before = self.snapshot(*LEGACY_STATE_TABLES)
        for client, method, path, body in calls:
            with self.subTest(method=method, path=path):
                response = getattr(client, method)(path, json=body,
                                                   headers={"Idempotency-Key": "task225-retired-write"})
                self.assertEqual(response.status_code, 410, response.text)
                detail = response.json()["detail"]
                self.assertEqual(detail["code"], "lifecycle_endpoint_retired")
                self.assertTrue(detail["replacement"])
        self.assert_unchanged(before)

    def test_web_refresh_is_retired_and_never_reaches_the_provider(self):
        before = self.snapshot("ndvi_records", "satellite_index_records", "satellite_collection_runs")
        with patch("services.satellite.get_satellite_service",
                   side_effect=AssertionError("provider must not be called")) as provider:
            response = self.client(self.manager_a).post(f"/api/ndvi/{self.field_a}/refresh")
        self.assertEqual(response.status_code, 410, response.text)
        self.assertIn("collect_satellite.py", response.json()["detail"]["replacement"])
        provider.assert_not_called()
        self.assertEqual(self.dumps(self.snapshot("ndvi_records", "satellite_index_records",
                                                  "satellite_collection_runs")), self.dumps(before))
        latest = self.client(self.manager_a).get(f"/api/ndvi/{self.field_a}/latest")
        self.assertEqual(latest.status_code, 200, latest.text)
        self.assertEqual(latest.json()["record"]["captured_date"], str(self.today - timedelta(days=2)))

    def test_legacy_history_stays_readable(self):
        manager_a, manager_b = self.client(self.manager_a), self.client(self.manager_b)
        listed = manager_a.get("/api/field-inspections").json()
        self.assertEqual({item["id"] for item in listed["items"]}, {self.legacy_pending, self.legacy_active})
        self.assertEqual(manager_a.get(f"/api/field-inspections/{self.legacy_pending}").status_code, 200)
        actions = manager_b.get("/api/operational-actions").json()
        self.assertEqual([item["id"] for item in actions["items"]], [self.legacy_action])
        closure = manager_b.get(f"/api/field-inspections/{self.legacy_done}/closure").json()
        self.assertEqual(closure["result"]["id"], self.legacy_result)
        self.assertEqual([item["id"] for item in closure["actions"]], [self.legacy_action])
        detail = manager_a.get(f"/api/anomaly-inspections/{self.legacy_pending}").json()
        self.assertEqual(detail["source"]["kind"], "legacy")


class StateMachineBoundaryTests(GenerationsFixture):

    def test_canonical_transitions_refuse_legacy_rows(self):
        agronomist, manager = self.client(self.agronomist_a), self.client(self.manager_a)
        version = self.inspection(self.legacy_active)["version"]
        before = self.snapshot(*LEGACY_STATE_TABLES)
        attempts = (
            (agronomist, "put", f"/api/anomaly-inspections/{self.legacy_active}/finding", {
                "expected_version": version, "inspected_at": datetime.now(timezone.utc).isoformat(),
                "cause": "pest", "severity": "low", "affected_area_pct": 5.0,
                "observations": "Legacy row", "recommended_action": "None"}),
            (agronomist, "post", f"/api/anomaly-inspections/{self.legacy_active}/submit",
             {"expected_version": version}),
            (manager, "post", f"/api/anomaly-inspections/{self.legacy_pending}/assignment",
             {"expected_version": 1, "assigned_to_id": self.agronomist_a.id}),
            (manager, "post", f"/api/anomaly-inspections/{self.legacy_active}/review",
             {"expected_version": version, "decision": "confirmed"}),
        )
        for client, method, path, body in attempts:
            with self.subTest(path=path):
                response = getattr(client, method)(path, json=body)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("legacy", response.json()["detail"])
        upload = agronomist.post(f"/api/anomaly-inspections/{self.legacy_active}/photos",
                                 data={"expected_version": str(version)},
                                 files={"photo": ("legacy.jpg", JPEG, "image/jpeg")})
        self.assertEqual(upload.status_code, 409, upload.text)
        self.assert_unchanged(before)
        self.assertEqual(list(__import__("pathlib").Path(self.media.name).rglob("*.jpg")), [])

    def test_legacy_row_can_only_be_closed_out_with_a_reason(self):
        manager = self.client(self.manager_a)
        version = self.inspection(self.legacy_pending)["version"]
        response = manager.post(f"/api/anomaly-inspections/{self.legacy_pending}/cancel",
                                json={"expected_version": version,
                                      "reason": "Superseded by a canonical inspection"})
        self.assertEqual(response.status_code, 200, response.text)
        row = self.inspection(self.legacy_pending)
        self.assertEqual((row["status"], row["source_kind"]), ("cancelled", "legacy"))
        self.assertIsNotNone(row["cancelled_at"])
        self.assertIsNone(row["completed_at"])
        audit = self.audit_events(self.legacy_pending)
        self.assertEqual([event["event_type"] for event in audit], ["inspection_cancelled"])
        self.assertEqual(audit[0]["event_metadata"]["origin"], "legacy_closeout")
        again = manager.post(f"/api/anomaly-inspections/{self.legacy_pending}/cancel",
                             json={"expected_version": version, "reason": "Duplicate close-out"})
        self.assertEqual(again.status_code, 409, again.text)
        self.assertEqual(len(self.audit_events(self.legacy_pending)), 1)

    def test_an_inspection_rooting_a_live_plan_cannot_be_cancelled_or_rejected(self):
        """A plan may be drafted from a submitted inspection; ending that
        inspection underneath the live plan would orphan the remediation."""
        response = self.client(self.manager_a).post("/api/anomaly-inspections", json={
            "field_id": self.field_a, "source_kind": "manual", "reason": "Submitted, then planned",
            "priority": "normal", "due_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        }, headers={"Idempotency-Key": "task225-boundary-submitted"})
        inspection_id = response.json()["inspection"]["id"]
        version = self.inspect_and_confirm(inspection_id, review=False)
        drafted = self.client(self.manager_a).post("/api/agronomy-plans", json={
            "inspection_id": inspection_id, "reason": "Plan drafted before review"},
            headers={"Idempotency-Key": "task225-boundary-submitted-plan"})
        self.assertEqual(drafted.status_code, 201, drafted.text)
        before = self.snapshot(*LEGACY_STATE_TABLES)
        manager = self.client(self.manager_a)
        cancel = manager.post(f"/api/anomaly-inspections/{inspection_id}/cancel",
                              json={"expected_version": version, "reason": "Trying to drop a planned case"})
        reject = manager.post(f"/api/anomaly-inspections/{inspection_id}/review",
                              json={"expected_version": version, "decision": "rejected",
                                    "reason": "Trying to reject a planned case"})
        for response in (cancel, reject):
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("active agronomy plan", response.json()["detail"])
        self.assert_unchanged(before)


class TenantBoundaryTests(GenerationsFixture):

    def test_foreign_reads_are_non_enumerating(self):
        manager_b = self.client(self.manager_b)
        missing = 987654
        for existing, absent in (
            (f"/api/anomaly-inspections/{self.current}", f"/api/anomaly-inspections/{missing}"),
            (f"/api/agronomy-plans/{self.plan_id}", f"/api/agronomy-plans/{missing}"),
            (f"/api/operational-center/cases/inspection:{self.current}",
             f"/api/operational-center/cases/inspection:{missing}"),
            (f"/api/anomaly-inspections/{self.legacy_pending}", f"/api/anomaly-inspections/{missing}"),
        ):
            with self.subTest(path=existing):
                foreign, nothing = manager_b.get(existing), manager_b.get(absent)
                self.assertEqual(foreign.status_code, 404, foreign.text)
                self.assertEqual((foreign.status_code, foreign.json()), (nothing.status_code, nothing.json()))

    def test_foreign_writes_are_rejected_without_mutation(self):
        manager_b, agronomist_b = self.client(self.manager_b), self.client(self.agronomist_b)
        before = self.snapshot(*LEGACY_STATE_TABLES)
        version = self.inspection(self.current)["version"]
        plan_version = self.plan(self.plan_id)["version"]
        attempts = (
            (manager_b, f"/api/anomaly-inspections/{self.current}/cancel",
             {"expected_version": version, "reason": "Foreign cancel"}, None),
            (manager_b, f"/api/anomaly-inspections/{self.legacy_pending}/cancel",
             {"expected_version": 1, "reason": "Foreign close-out"}, None),
            (agronomist_b, f"/api/anomaly-inspections/{self.current}/submit",
             {"expected_version": version}, None),
            (manager_b, f"/api/agronomy-plans/{self.plan_id}/transition",
             {"expected_version": plan_version, "reason": "Foreign cancel", "operation": "cancel"},
             {"Idempotency-Key": "task225-foreign-plan"}),
            (manager_b, "/api/agronomy-plans",
             {"inspection_id": self.plan_inspection, "reason": "Foreign plan draft"},
             {"Idempotency-Key": "task225-foreign-draft"}),
        )
        for client, path, body, headers in attempts:
            with self.subTest(path=path):
                response = client.post(path, json=body, headers=headers or {})
                self.assertEqual(response.status_code, 404, response.text)
        self.assert_unchanged(before)

    def test_client_supplied_enterprise_is_never_trusted(self):
        manager_a = self.client(self.manager_a)
        before = self.snapshot(*LEGACY_STATE_TABLES)
        response = manager_a.post("/api/anomaly-inspections", json={
            "field_id": self.field_b, "source_kind": "manual", "reason": "Cross-tenant creation",
            "priority": "normal", "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        }, headers={"Idempotency-Key": "task225-foreign-field"})
        self.assertEqual(response.status_code, 404, response.text)
        queue = manager_a.get("/api/anomaly-inspections/queue", params={"enterprise_id": self.enterprise_b})
        self.assertEqual(queue.status_code, 404, queue.text)
        center = manager_a.get("/api/operational-center/queue", params={"enterprise_id": self.enterprise_b})
        self.assertEqual(center.status_code, 404, center.text)
        self.assert_unchanged(before)

    def test_admin_is_global_and_tenants_see_only_their_cases(self):
        admin, manager_a, manager_b = (self.client(self.admin), self.client(self.manager_a),
                                       self.client(self.manager_b))
        self.assertEqual(admin.get(f"/api/anomaly-inspections/{self.current}").status_code, 200)
        self.assertEqual(admin.get(f"/api/agronomy-plans/{self.plan_id}").status_code, 200)
        self.assertEqual(admin.get(f"/api/field-inspections/{self.legacy_done}/closure").status_code, 200)

        def enterprises(client):
            items = client.get("/api/operational-center/queue", params={"limit": 100}).json()["items"]
            return {item["enterprise_id"] for item in items}

        self.assertEqual(enterprises(admin), {self.enterprise_a, self.enterprise_b})
        self.assertEqual(enterprises(manager_a), {self.enterprise_a})
        self.assertEqual(enterprises(manager_b), {self.enterprise_b})

    def test_viewer_is_read_only(self):
        before = self.snapshot(*LEGACY_STATE_TABLES)
        viewer = self.client(self.viewer_a)
        self.assertEqual(viewer.get(f"/api/anomaly-inspections/{self.current}").status_code, 200)
        response = viewer.post(f"/api/anomaly-inspections/{self.current}/cancel",
                               json={"expected_version": 1, "reason": "Viewer attempt"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assert_unchanged(before)
