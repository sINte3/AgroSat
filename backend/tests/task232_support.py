"""Shared PostgreSQL harness for the TASK_232 management analytics suites.

Not collected by pytest (no ``test_`` prefix). It extends the TASK_225 harness
(isolated ``agrosat_h0a*`` database only, real services, real FastAPI app):
cases move through the production inspection and agronomy-plan APIs and the
collector's verification reconciliation. SQL written here only seeds what an
operator, the collector or a pre-0013 release would already have persisted
(crop reference data, crop seasons, alerts, legacy TASK_209 rows) or moves a
recorded timestamp onto an exact period boundary.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from task225_support import JPEG
from test_task225_closed_loop_postgres import FINDING, ClosedLoopFlow


TASHKENT = ZoneInfo("Asia/Tashkent")
ENDPOINT = "/api/management-analytics"
EXTRA_FIELD = "POLYGON(({x} 39.900,{x2} 39.900,{x2} 39.904,{x} 39.904,{x} 39.900))"


class AnalyticsFlow(ClosedLoopFlow):
    """TASK_225 closed-loop driver plus generic, tenant-parameterised steps."""

    def setUp(self):
        super().setUp()
        self.sql("DELETE FROM crop_types WHERE code LIKE 't232_%'")
        self.cotton = self.scalar(
            "INSERT INTO crop_types (code, name_ru) VALUES ('t232_cotton','T232 Хлопок') RETURNING id")
        self.wheat = self.scalar(
            "INSERT INTO crop_types (code, name_ru) VALUES ('t232_wheat','T232 Пшеница') RETURNING id")
        self.year = datetime.now(TASHKENT).year
        self.quiet = [row["id"] for row in self.sql(
            "SELECT id FROM fields WHERE name LIKE 'T225 Quiet %' ORDER BY name")]
        self._key_sequence = 0
        self._extra_fields = 0

    def tearDown(self):
        super().tearDown()
        self.sql("DELETE FROM crop_types WHERE code LIKE 't232_%'")

    # ── seeds ───────────────────────────────────────────────────────────────

    def key(self, prefix):
        self._key_sequence += 1
        return f"t232-{prefix}-{self._key_sequence:04d}"

    def season(self, field_id, crop_type_id, year=None):
        self.sql(
            "INSERT INTO crop_seasons (field_id, crop_type_id, season_year) VALUES (:field, :crop, :year)",
            {"field": field_id, "crop": crop_type_id, "year": year or self.year})

    def extra_field(self, enterprise_id, *, active=True):
        x = 64.700 + self._extra_fields * 0.01
        self._extra_fields += 1
        field_id = self._field(enterprise_id, f"T232 Extra {self._extra_fields:02d}",
                               EXTRA_FIELD.format(x=f"{x:.3f}", x2=f"{x + 0.004:.3f}"))
        if not active:
            self.sql("UPDATE fields SET is_active=false WHERE id=:id", {"id": field_id})
        return field_id

    def alert(self, field_id, severity="critical"):
        return self.scalar(
            "INSERT INTO alerts (field_id, alert_type, severity, title, description, is_active, triggered_at) "
            "VALUES (:field, 'ndvi_drop', :severity, 'T232 alert', 'NDVI drop on the field', true, now()) "
            "RETURNING id", {"field": field_id, "severity": severity})

    def post(self, field_id, value, *, valid=100.0, days_after=9):
        return self.ndvi(field_id, self.today + timedelta(days=days_after), value, valid=valid)

    # ── HTTP ────────────────────────────────────────────────────────────────

    def ok(self, response, status=200):
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def analytics(self, user, expected=200, **params):
        response = self.client(user).get(ENDPOINT, params=params)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def people(self, enterprise_id):
        if enterprise_id == self.enterprise_b:
            return self.manager_b, self.agronomist_b
        return self.manager_a, self.agronomist_a

    # ── canonical inspection (TASK_217) ─────────────────────────────────────

    def open_manual(self, field_id, *, manager=None, assign=None, due_in=timedelta(days=2),
                    priority="normal", zone=None):
        body = {
            "field_id": field_id, "source_kind": "manual", "priority": priority,
            "reason": "Scouting request for the block",
            "due_at": (datetime.now(timezone.utc) + due_in).isoformat(),
        }
        if assign is not None:
            body["assigned_to_id"] = assign.id
        if zone is not None:
            body["zone"] = zone
        response = self.client(manager or self.manager_a).post(
            "/api/anomaly-inspections", json=body, headers={"Idempotency-Key": self.key("inspection")})
        return self.ok(response, 201)["inspection"]["id"]

    def drive(self, inspection_id, stop, *, manager=None, agronomist=None):
        """Move a canonical inspection to assigned/in_progress/submitted/confirmed/rejected."""
        row = self.inspection(inspection_id)
        default_manager, default_agronomist = self.people(row["enterprise_id"])
        manager, agronomist = manager or default_manager, agronomist or default_agronomist
        version = row["version"]
        if row["assigned_to_id"] is None:
            version = self.ok(self.client(manager).post(
                f"/api/anomaly-inspections/{inspection_id}/assignment",
                json={"expected_version": version, "assigned_to_id": agronomist.id}))["inspection"]["version"]
        if stop == "assigned":
            return
        worker = self.client(agronomist)
        version = self.ok(worker.post(f"/api/anomaly-inspections/{inspection_id}/start",
                                      json={"expected_version": version}))["inspection"]["version"]
        if stop == "in_progress":
            return
        version = self.ok(worker.put(f"/api/anomaly-inspections/{inspection_id}/finding", json={
            **FINDING, "expected_version": version,
            "inspected_at": datetime.now(timezone.utc).isoformat(),
        }))["inspection"]["version"]
        version = self.ok(worker.post(f"/api/anomaly-inspections/{inspection_id}/submit",
                                      json={"expected_version": version}))["inspection"]["version"]
        if stop == "submitted":
            return
        review = {"expected_version": version, "decision": stop}
        if stop == "rejected":
            review["reason"] = "Not a real problem on the ground"
        self.ok(self.client(manager).post(f"/api/anomaly-inspections/{inspection_id}/review", json=review))

    def confirmed_case(self, field_id, *, enterprise_id=None):
        manager, _ = self.people(enterprise_id or self.enterprise_a)
        inspection_id = self.open_manual(field_id, manager=manager)
        self.drive(inspection_id, "confirmed")
        return inspection_id

    # ── canonical remediation (TASK_220) ────────────────────────────────────

    def run_plan(self, inspection_id, *, stop="pending_verification", items=1, photos=0,
                 due_in=timedelta(days=3)):
        """Draft a plan for a confirmed inspection and drive its first cycle."""
        enterprise_id = self.inspection(inspection_id)["enterprise_id"]
        manager, _ = self.people(enterprise_id)
        response = self.client(manager).post("/api/agronomy-plans", json={
            "inspection_id": inspection_id, "reason": "Plan for the confirmed finding",
        }, headers={"Idempotency-Key": self.key("draft")})
        plan_id = self.ok(response, 201)["plan_id"]
        if stop != "draft":
            self.run_cycle(plan_id, stop=stop, items=items, photos=photos, due_in=due_in)
        return plan_id

    def run_cycle(self, plan_id, *, stop="pending_verification", items=1, photos=0,
                  due_in=timedelta(days=3)):
        """Add work to a draft/rework plan, approve it and execute the cycle."""
        plan = self.plan(plan_id)
        manager, agronomist = self.people(plan["enterprise_id"])
        boss, worker = self.client(manager), self.client(agronomist)
        version, work = plan["version"], []
        for number in range(items):
            body = self.ok(boss.post(f"/api/agronomy-plans/{plan_id}/work", json={
                "expected_version": version, "reason": "Work assigned to the crew",
                "category": "irrigation", "instruction": f"Repair irrigation line {number + 1}",
                "assigned_to_id": agronomist.id,
                "due_at": (datetime.now(timezone.utc) + due_in).isoformat(),
            }, headers={"Idempotency-Key": self.key("work")}), 201)
            version = body["version"]
            work.append([body["item_id"], body["item_version"]])
        version = self.ok(boss.post(f"/api/agronomy-plans/{plan_id}/transition", json={
            "expected_version": version, "reason": "Approved by the farm manager", "operation": "approve",
        }, headers={"Idempotency-Key": self.key("approve")}))["version"]
        if stop == "approved":
            return
        for item in work:
            body = self.ok(worker.post(f"/api/agronomy-plans/{plan_id}/work/{item[0]}/transition", json={
                "expected_version": item[1], "expected_plan_version": version,
                "reason": "Crew on site", "operation": "start",
            }, headers={"Idempotency-Key": self.key("start")}))
            version, item[1] = body["version"], body["item_version"]
        for item in work:
            for _ in range(photos):
                body = self.ok(worker.post(
                    f"/api/agronomy-plans/{plan_id}/work/{item[0]}/evidence",
                    data={"expected_plan_version": str(version), "expected_version": str(item[1]),
                          "key": self.key("photo")},
                    files={"photo": ("evidence.jpg", JPEG, "image/jpeg")}), 201)
                version, item[1] = body["version"], body["item_version"]
        if stop == "in_progress":
            return
        for item in work:
            body = self.ok(worker.post(f"/api/agronomy-plans/{plan_id}/work/{item[0]}/transition", json={
                "expected_version": item[1], "expected_plan_version": version,
                "reason": "Work finished", "operation": "complete",
                "result_note": "Line repaired and the block irrigated.",
            }, headers={"Idempotency-Key": self.key("complete")}))
            version, item[1] = body["version"], body["item_version"]
        self.assertEqual(self.plan(plan_id)["status"], "pending_verification")

    def plan_with_items(self, inspection_id, dues, *, started=()):
        """One approved plan with a work item per due offset; start only ``started`` items.

        Lets a test choose which item the Operational Center treats as the
        case's primary work item (in progress first, then earliest due).
        """
        manager, agronomist = self.people(self.inspection(inspection_id)["enterprise_id"])
        boss, worker = self.client(manager), self.client(agronomist)
        plan_id = self.ok(boss.post("/api/agronomy-plans", json={
            "inspection_id": inspection_id, "reason": "Plan for the confirmed finding",
        }, headers={"Idempotency-Key": self.key("draft")}), 201)["plan_id"]
        version, items = self.plan(plan_id)["version"], []
        now = datetime.now(timezone.utc)
        for number, due in enumerate(dues):
            body = self.ok(boss.post(f"/api/agronomy-plans/{plan_id}/work", json={
                "expected_version": version, "reason": "Work assigned to the crew",
                "category": "irrigation", "instruction": f"Repair irrigation line {number + 1}",
                "assigned_to_id": agronomist.id, "due_at": (now + due).isoformat(),
            }, headers={"Idempotency-Key": self.key("work")}), 201)
            version = body["version"]
            items.append([body["item_id"], body["item_version"]])
        version = self.ok(boss.post(f"/api/agronomy-plans/{plan_id}/transition", json={
            "expected_version": version, "reason": "Approved by the farm manager", "operation": "approve",
        }, headers={"Idempotency-Key": self.key("approve")}))["version"]
        for index in started:
            item = items[index]
            body = self.ok(worker.post(f"/api/agronomy-plans/{plan_id}/work/{item[0]}/transition", json={
                "expected_version": item[1], "expected_plan_version": version,
                "reason": "Crew on site", "operation": "start",
            }, headers={"Idempotency-Key": self.key("start")}))
            version, item[1] = body["version"], body["item_version"]
        return plan_id, [item[0] for item in items]

    def command_center(self, user):
        """The accepted Operational Center queue (by case key) and summary for ``user``."""
        from services import operational_center

        queue = operational_center.list_queue(self.session(), user, {"limit": 100, "offset": 0})
        summary = operational_center.summary(self.session(), user, {})
        return {item["case_key"]: item for item in queue["items"]}, summary

    def resolve(self, plan_id, operation, reason="Resolution recorded by the manager"):
        manager, _ = self.people(self.plan(plan_id)["enterprise_id"])
        return self.ok(self.transition(plan_id, operation, user=manager, key=self.key(operation),
                                       reason=reason))

    # ── reads ───────────────────────────────────────────────────────────────

    def fields_by_id(self, body):
        return {item["field_id"]: item for item in body["breakdowns"]["fields"]["items"]}
