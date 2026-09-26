"""TASK_232: H1 Management Analytics v1 against PostgreSQL.

Every case below moves through production code: the canonical inspection API
(TASK_217), the agronomy-plan API (TASK_220), the collector's detection and
promotion steps and its verification reconciliation. The endpoint is called
over HTTP on main.app with only authentication and the session factory
overridden. See tests/task232_support.py for the harness.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import event

from task232_support import ENDPOINT, TASHKENT, AnalyticsFlow


ACTIVE_STATES = (
    "needs_inspection", "inspection_active", "awaiting_review", "awaiting_decision",
    "plan_active", "work_active", "awaiting_satellite_verification", "verification_blocked",
    "improved_awaiting_closure", "not_improved", "reopened",
)
PERIOD_KEYS = ("inspections_opened", "resolved_cycles", "improved", "unchanged", "worsened",
               "unverified", "reopened")
MARCH_FROM, MARCH_TO = "2026-03-01", "2026-03-31"


def leaves(value, path=""):
    """Every (path, scalar) pair of a JSON document."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, f"{path}[{index}]")
    else:
        yield path, value


def funnel_total(current):
    inspection, remediation = current["inspection_funnel"], current["remediation_funnel"]
    return (
        inspection["needs_inspection"]["total"] + inspection["inspection_active"]
        + inspection["awaiting_review"] + inspection["awaiting_decision"]
        + remediation["plan_active"]["total"] + remediation["work_active"]
        + remediation["awaiting_satellite_verification"]["total"]
        + remediation["verification_blocked"]["total"] + remediation["improved_awaiting_closure"]
        + remediation["not_improved"]["total"] + remediation["reopened"]
    )


class PanoramaMixin:
    """One enterprise-A case in every canonical current state, plus terminal ones."""

    def build_panorama(self):
        q = self.quiet
        for field_id in q + [self.field_a]:
            self.seed_quiet_history(field_id)
        blocked_field, reopened_field, closed_field = (self.extra_field(self.enterprise_a) for _ in range(3))
        for field_id in (blocked_field, reopened_field, closed_field):
            self.seed_quiet_history(field_id)
        cases = {
            "new": self.open_manual(q[0]),
            "assigned": self.open_manual(q[1], assign=self.agronomist_a),
            "in_progress": self.open_manual(q[2]),
            "submitted": self.open_manual(q[3]),
            "confirmed": self.confirmed_case(q[4]),
        }
        self.drive(cases["in_progress"], "in_progress")
        self.drive(cases["submitted"], "submitted")
        plans = {
            "draft": self.run_plan(self.confirmed_case(q[5]), stop="draft"),
            "approved": self.run_plan(self.confirmed_case(q[5]), stop="approved"),
            "working": self.run_plan(self.confirmed_case(q[6]), stop="in_progress", items=2),
            "pending": self.run_plan(self.confirmed_case(q[7])),
            "improved": self.run_plan(self.confirmed_case(q[8])),
            "unchanged": self.run_plan(self.confirmed_case(q[9])),
            "worsened": self.run_plan(self.confirmed_case(self.field_a)),
            "blocked": self.run_plan(self.confirmed_case(blocked_field)),
            "reopened": self.run_plan(self.confirmed_case(reopened_field)),
            "closed": self.run_plan(self.confirmed_case(closed_field)),
        }
        self.post(q[8], 0.76)             # baseline 0.64: +0.12, IMPROVED
        self.post(q[9], 0.66)             # +0.02, NO_MATERIAL_CHANGE
        self.post(self.field_a, 0.50)     # -0.14, WORSENED
        self.post(blocked_field, 0.90, valid=30.0)  # rejected scene: QUALITY_BLOCKED
        self.post(reopened_field, 0.50)
        self.post(closed_field, 0.80)
        self.reconcile()
        self.resolve(plans["reopened"], "rework", reason="Worsened after the repair; next cycle")
        self.resolve(plans["closed"], "close")
        cases["alert"] = self.alert(q[0])
        self.panorama_fields = {"blocked": blocked_field, "reopened": reopened_field, "closed": closed_field}
        return cases, plans


class ContractTests(AnalyticsFlow):

    def test_empty_scope_is_a_deterministic_zero_snapshot(self):
        from services import management_analytics as service

        body = self.analytics(self.manager_a)
        self.assertEqual(body["definitions_version"], "management_analytics_v1")
        self.assertEqual(body["timezone"], "Asia/Tashkent")
        self.assertEqual(body["scope"], {
            "role": "manager", "authorization": "tenant", "enterprise_id": self.enterprise_a,
            "field_id": None, "crop_type_id": None,
        })
        provenance = body["provenance"]
        self.assertEqual(provenance["definitions_fingerprint"], service.DEFINITIONS_FINGERPRINT)
        self.assertEqual(provenance["lifecycle"], "task220_canonical_remediation")
        self.assertEqual(provenance["excluded_legacy_sources"],
                         ["corrective_actions", "action_verification_requests"])
        today = datetime.now(TASHKENT).date()
        effective = body["period"]["effective"]
        self.assertEqual(body["period"]["requested"], {"date_from": None, "date_to": None})
        self.assertEqual((effective["date_from"], effective["date_to"], effective["days"]),
                         ((today - timedelta(days=29)).isoformat(), today.isoformat(), 30))
        self.assertEqual(effective["starts_at"], f"{today - timedelta(days=29)}T00:00:00+05:00")
        self.assertEqual(effective["ends_before"], f"{today + timedelta(days=1)}T00:00:00+05:00")
        self.assertEqual(body["coverage"], {
            "fields_in_scope": 11, "monitored_fields": 11, "inactive_fields": 0,
            "monitored_fields_with_active_problems": 0,
            "ndvi_freshness": {"fresh": 0, "aging": 0, "stale": 0, "never_collected": 0,
                               "cloud_blocked": 0, "provider_degraded": 0, "quality_blocked": 0,
                               "not_evaluated": 11},
        })
        for section in ("current", "period_activity", "outcomes"):
            numbers = [(path, value) for path, value in leaves(body[section])
                       if isinstance(value, int) and not isinstance(value, bool)]
            self.assertTrue(numbers)
            self.assertEqual([item for item in numbers if item[1] != 0], [], section)
        for name, block in body["completion"].items():
            self.assertEqual((block["numerator"], block["denominator"], block["rate"]), (0, 0, None), name)
        for name, metric in body["cycle_times"]["metrics"].items():
            self.assertEqual((metric["sample_count"], metric["median_hours"], metric["p90_hours"],
                              metric["status"]), (0, None, None, "no_samples"), name)
        periods = body["breakdowns"]["periods"]
        self.assertEqual(periods[0]["bucket_start"], effective["date_from"])
        self.assertEqual(periods[-1]["bucket_end"], effective["date_to"])
        self.assertTrue(all(value == 0 for row in periods for key, value in row.items()
                            if key not in {"bucket_start", "bucket_end"}))
        fields = body["breakdowns"]["fields"]
        self.assertEqual((fields["total"], fields["limit"], fields["offset"], len(fields["items"])),
                         (11, 50, 0, 11))
        self.assertEqual([row["enterprise_id"] for row in body["breakdowns"]["enterprises"]],
                         [self.enterprise_a])
        self.assertEqual(body["breakdowns"]["crops"][0]["crop_type_id"], None)
        self.assertIn("unsupported", body["cycle_times"])

    def test_the_endpoint_changes_nothing(self):
        self.seed_quiet_history(self.field_a)
        self.run_plan(self.confirmed_case(self.field_a))
        tables = ("field_inspections", "agronomy_plans", "agronomy_work_items", "agronomy_events",
                  "agronomy_verifications", "operational_notifications", "autonomous_anomaly_candidates")
        before = self.snapshot(*tables)
        self.analytics(self.manager_a)
        self.analytics(self.admin, field_id=self.field_a, granularity="day")
        self.assertEqual(self.dumps(self.snapshot(*tables)), self.dumps(before))

    def test_invalid_period_and_paging_parameters_are_rejected(self):
        manager = self.client(self.manager_a)
        for params in (
            {"date_from": "2026-03-10", "date_to": "2026-03-01"},
            {"date_from": "2025-01-01", "date_to": "2026-01-02"},
            {"granularity": "year"},
            {"field_limit": 0}, {"field_limit": 201}, {"field_offset": -1},
            {"date_from": "not-a-date"}, {"enterprise_id": 0}, {"field_id": -3},
        ):
            response = manager.get(ENDPOINT, params=params)
            self.assertEqual(response.status_code, 422, (params, response.text))
        self.assertEqual(manager.get(ENDPOINT, params={"date_from": "2025-01-01",
                                                       "date_to": "2025-12-31"}).status_code, 200)

    def test_an_empty_past_period_keeps_the_current_state(self):
        self.open_manual(self.field_a, due_in=-timedelta(days=1))
        body = self.analytics(self.manager_a, date_from="2025-01-01", date_to="2025-01-31",
                              granularity="month")
        self.assertEqual(body["current"]["active_problems"]["total"], 1)
        self.assertEqual(body["current"]["overdue"]["inspections"], 1)
        self.assertEqual(body["period_activity"]["inspections_opened"]["total"], 0)
        self.assertEqual([row["bucket_start"] for row in body["breakdowns"]["periods"]], ["2025-01-01"])


class LifecycleTests(PanoramaMixin, AnalyticsFlow):

    def test_every_current_canonical_state_is_counted_once(self):
        self.build_panorama()
        current = self.analytics(self.manager_a)["current"]
        self.assertEqual(current["active_problems"], {
            "total": 15, "fields_affected": 13,
            "by_source": {"inspection": 14, "candidate": 0, "alert": 1},
            "by_priority": {"critical": 1, "high": 0, "normal": 14, "low": 0},
            "legacy_open_inspections": 0,
        })
        self.assertEqual(current["inspection_funnel"], {
            "needs_inspection": {"total": 3, "inspections": 2, "candidates": 0, "alerts": 1,
                                 "unassigned_inspections": 1},
            "inspection_active": 1, "awaiting_review": 1, "awaiting_decision": 1,
        })
        self.assertEqual(current["remediation_funnel"], {
            "plan_active": {"total": 2, "draft": 1, "approved": 1},
            "work_active": 1,
            "awaiting_satellite_verification": {"total": 1, "pending_data": 1, "too_early": 0},
            "verification_blocked": {"total": 1, "cloud_blocked": 0, "quality_blocked": 1,
                                     "provider_degraded": 0, "inconclusive": 0},
            "improved_awaiting_closure": 1,
            "not_improved": {"total": 2, "unchanged": 1, "worsened": 1},
            "reopened": 1,
        })
        self.assertEqual(funnel_total(current), current["active_problems"]["total"])
        self.assertEqual(current["work_items"],
                         {"active": 3, "planned": 1, "in_progress": 2, "unassigned": 0, "overdue": 0})
        self.assertEqual(current["overdue"],
                         {"cases": 0, "inspections": 0, "plans_with_overdue_work": 0, "work_items": 0})
        self.assertEqual(current["data_unavailable"], {"freshness_cases": 0, "external_cases": 0})

    def test_windowed_facts_outcomes_completion_and_durations(self):
        self.build_panorama()
        body = self.analytics(self.manager_a)
        self.assertEqual(body["period_activity"], {
            "anomaly_candidates_detected": 0,
            "inspections_opened": {"total": 15, "manual": 15, "alert": 0, "pixel_ndvi": 0},
            "inspections_confirmed": 11, "inspections_rejected": 0, "inspections_cancelled": 0,
            "plans_drafted": 10, "plan_cycles_approved": 9, "plan_cycles_work_completed": 7,
            "plan_cycles_verified": 5, "plans_cancelled": 0,
        })
        self.assertEqual(body["outcomes"], {
            "resolved_cycles": 2,
            "verified": {"improved": 1, "unchanged": 0, "worsened": 1, "total": 2},
            "unverified": {"total": 0, "pending_data": 0, "too_early": 0, "cloud_blocked": 0,
                           "quality_blocked": 0, "provider_degraded": 0, "inconclusive": 0},
            "closed": {"total": 1, "improved": 1, "without_improvement": 0},
            "returned_for_rework": 1,
            "reopened": {"total": 1, "after_closure": 0, "after_verification": 1},
        })
        completion = body["completion"]
        self.assertEqual({k: completion["work_completion"][k] for k in
                          ("numerator", "denominator", "rate", "ended_without_completion", "open")},
                         {"numerator": 7, "denominator": 9, "rate": 0.7778,
                          "ended_without_completion": 0, "open": 2})
        self.assertEqual({k: completion["verification_completion"][k] for k in
                          ("numerator", "denominator", "rate", "improved", "unchanged", "worsened",
                           "not_conclusive", "improved_rate")},
                         {"numerator": 5, "denominator": 7, "rate": 0.7143, "improved": 2,
                          "unchanged": 1, "worsened": 2, "not_conclusive": 2, "improved_rate": 0.2857})
        self.assertEqual({k: completion["plan_closure"][k] for k in
                          ("numerator", "denominator", "rate", "closed_improved",
                           "closed_without_improvement", "cancelled", "open")},
                         {"numerator": 1, "denominator": 10, "rate": 0.1, "closed_improved": 1,
                          "closed_without_improvement": 0, "cancelled": 0, "open": 9})
        samples = {name: metric["sample_count"] for name, metric in body["cycle_times"]["metrics"].items()}
        self.assertEqual(samples, {
            "signal_to_inspection_opened": 0, "inspection_opened_to_reviewed": 11,
            "inspection_submitted_to_plan_drafted": 10, "plan_approved_to_work_completed": 7,
            "work_completed_to_verified": 5, "case_opened_to_verified_closure": 1,
        })
        for metric in body["cycle_times"]["metrics"].values():
            if metric["sample_count"]:
                self.assertGreaterEqual(metric["median_hours"], 0)

    def test_closed_rejected_and_cancelled_records_are_not_current(self):
        self.seed_quiet_history(self.field_a)
        closed = self.run_plan(self.confirmed_case(self.field_a))
        self.post(self.field_a, 0.80)
        self.reconcile()
        self.resolve(closed, "close")
        rejected = self.open_manual(self.quiet[0])
        self.drive(rejected, "rejected")
        # Assigned on purpose: cancelling an unassigned 'new' canonical inspection
        # violates ck_field_inspections_assignment_state (pre-existing, TASK_217 path).
        cancelled = self.open_manual(self.quiet[1], assign=self.agronomist_a)
        self.ok(self.client(self.manager_a).post(f"/api/anomaly-inspections/{cancelled}/cancel", json={
            "expected_version": self.inspection(cancelled)["version"], "reason": "Duplicate request"}))
        alert = self.alert(self.quiet[2])
        self.sql("UPDATE alerts SET is_active=false WHERE id=:id", {"id": alert})
        body = self.analytics(self.manager_a)
        self.assertEqual(body["current"]["active_problems"]["total"], 0)
        self.assertEqual(body["coverage"]["monitored_fields_with_active_problems"], 0)
        activity = body["period_activity"]
        self.assertEqual((activity["inspections_opened"]["total"], activity["inspections_confirmed"],
                          activity["inspections_rejected"], activity["inspections_cancelled"]), (3, 1, 1, 1))
        self.assertEqual(body["outcomes"]["closed"], {"total": 1, "improved": 1, "without_improvement": 0})
        self.assertEqual(body["cycle_times"]["metrics"]["case_opened_to_verified_closure"]["sample_count"], 1)

    def test_satellite_signal_candidate_and_alert_sources(self):
        # One persistent drop among twelve active fields is promoted automatically.
        inspection_id = self.open_case()
        body = self.analytics(self.manager_a)
        self.assertEqual(body["period_activity"]["anomaly_candidates_detected"], 1)
        self.assertEqual(body["period_activity"]["inspections_opened"]["pixel_ndvi"], 1)
        self.assertEqual(body["current"]["inspection_funnel"]["needs_inspection"],
                         {"total": 1, "inspections": 1, "candidates": 0, "alerts": 0,
                          "unassigned_inspections": 1})
        signal = body["cycle_times"]["metrics"]["signal_to_inspection_opened"]
        self.assertEqual((signal["sample_count"], signal["status"]), (1, "measured"))
        self.assertIsNotNone(inspection_id)

    def test_unpromoted_candidates_and_alerts_count_once(self):
        from task225_support import FIELD_A_EDITED

        second = self._field(self.enterprise_a, "T225 Field A2", FIELD_A_EDITED.replace("39.70", "39.72"))
        self.seed_persistent_drop(self.field_a)
        self.seed_persistent_drop(second)
        self.run_monitoring_cycle()  # two signals trip the spike guard: both stay NEW candidates
        alert = self.alert(self.quiet[0])
        needs = self.analytics(self.manager_a)["current"]["inspection_funnel"]["needs_inspection"]
        self.assertEqual(needs, {"total": 3, "inspections": 0, "candidates": 2, "alerts": 1,
                                 "unassigned_inspections": 0})
        # Opening the canonical inspection from the alert replaces the alert case.
        response = self.client(self.manager_a).post("/api/anomaly-inspections", json={
            "field_id": self.quiet[0], "source_kind": "alert", "source_alert_id": alert,
            "reason": "Inspect the critical alert", "priority": "urgent",
            "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        }, headers={"Idempotency-Key": self.key("alert-case")})
        self.ok(response, 201)
        body = self.analytics(self.manager_a)
        self.assertEqual(body["current"]["inspection_funnel"]["needs_inspection"],
                         {"total": 3, "inspections": 1, "candidates": 2, "alerts": 0,
                          "unassigned_inspections": 1})
        self.assertEqual(sum(body["current"]["active_problems"]["by_priority"].values()), 3)
        self.assertEqual(body["period_activity"]["inspections_opened"]["alert"], 1)


class ExclusionTests(AnalyticsFlow):

    def _legacy_inspection(self, field_id, status, *, due_date=None, timestamp=""):
        columns = {"completed": ", completed_at", "in_progress": ", started_at"}.get(status, "")
        values = ", now()" if columns else ""
        return self.scalar(
            "INSERT INTO field_inspections (field_id, enterprise_id, created_by_id, client_request_id, "
            "request_fingerprint, source, title, status, source_kind, source_reason, priority, due_date"
            f"{columns}) VALUES (:field, :enterprise, :creator, :key, :fingerprint, 'manual', "
            f"'Legacy inspection', :status, 'legacy', 'Legacy inspection', 'normal', :due{values}) RETURNING id",
            {"field": field_id, "enterprise": self.enterprise_a, "creator": self.manager_a.id,
             "key": f"t232-legacy-{status}-{field_id}", "fingerprint": "f" * 64, "status": status,
             "due": due_date})

    def test_legacy_task209_history_never_becomes_current_truth(self):
        q = self.quiet
        self._legacy_inspection(q[0], "pending", due_date=self.today - timedelta(days=1))
        self._legacy_inspection(q[1], "in_progress", due_date=self.today)
        done = self._legacy_inspection(q[2], "completed")
        result = self.scalar(
            "INSERT INTO inspection_results (inspection_id, field_id, enterprise_id, recorded_by_id, "
            "cause_code) VALUES (:inspection, :field, :enterprise, :actor, 'irrigation') RETURNING id",
            {"inspection": done, "field": q[2], "enterprise": self.enterprise_a, "actor": self.manager_a.id})
        action = """INSERT INTO corrective_actions (inspection_id, result_id, field_id, enterprise_id,
            created_by_id, owner_id, description, due_date, status{extra}) VALUES (:inspection, :result,
            :field, :enterprise, :actor, :actor, 'Repair the line', :due, :status{values}) RETURNING id"""
        params = {"inspection": done, "result": result, "field": q[2], "enterprise": self.enterprise_a,
                  "actor": self.manager_a.id, "due": self.today - timedelta(days=3)}
        open_action = self.scalar(action.format(extra="", values=""), {**params, "status": "open"})
        closed_action = self.scalar(action.format(
            extra=", closure_reason, closed_by_id, closed_at", values=", 'Done', :actor, now()"),
            {**params, "status": "closed"})
        self.scalar(action.format(
            extra=", reopen_reason, reopened_by_id, reopened_at", values=", 'Came back', :actor, now()"),
            {**params, "status": "in_progress"})
        reference = self.ndvi(q[2], self.today - timedelta(days=20), 0.40)
        observation = self.ndvi(q[2], self.today - timedelta(days=10), 0.60)
        for action_id in (open_action, closed_action):
            self.sql(
                "INSERT INTO action_verification_requests (action_id, field_id, enterprise_id, "
                "requested_by_id, index_code, reference_date, minimum_separation_days, status, "
                "reference_ndvi_record_id, observation_ndvi_record_id, reference_value, observation_value, "
                "delta_value, reference_observed_at, observation_observed_at, reference_valid_pixels_pct, "
                "reference_cloud_cover_pct, reference_satellite, observation_valid_pixels_pct, "
                "observation_cloud_cover_pct, observation_satellite, result, confidence, resolved_at) "
                "VALUES (:action, :field, :enterprise, :actor, 'ndvi', :reference_date, 3, 'resolved', "
                ":reference, :observation, 0.40, 0.60, 0.20, :reference_date, :observation_date, 100, 0, "
                "'Sentinel-2', 100, 0, 'Sentinel-2', 'improved', 'high', now())",
                {"action": action_id, "field": q[2], "enterprise": self.enterprise_a,
                 "actor": self.manager_a.id, "reference": reference, "observation": observation,
                 "reference_date": self.today - timedelta(days=20),
                 "observation_date": self.today - timedelta(days=10)})
        body = self.analytics(self.manager_a)
        current = body["current"]
        # The two open legacy inspections are still canonical workload to drain
        # (the TASK_225 projection): needs_inspection and inspection_active.
        self.assertEqual(current["active_problems"]["total"], 2)
        self.assertEqual(current["active_problems"]["legacy_open_inspections"], 2)
        self.assertEqual((current["inspection_funnel"]["needs_inspection"]["total"],
                          current["inspection_funnel"]["inspection_active"]), (1, 1))
        # Legacy date-only deadline: overdue only after the local due day ended.
        self.assertEqual(current["overdue"], {"cases": 1, "inspections": 1,
                                              "plans_with_overdue_work": 0, "work_items": 0})
        self.assertEqual(current["work_items"]["active"], 0)
        self.assertEqual(current["remediation_funnel"]["reopened"], 0)
        # No legacy action, closure, reopen or "improved" verification reaches H1.
        for section in ("period_activity", "outcomes"):
            self.assertEqual([item for item in leaves(body[section])
                              if isinstance(item[1], int) and item[1]], [], section)
        for block in body["completion"].values():
            self.assertEqual(block["denominator"], 0)

    def test_missing_quality_blocked_and_inconclusive_data_are_not_outcomes(self):
        missing_field, blocked_field, zone_field = self.quiet[:3]
        for field_id in (missing_field, blocked_field, zone_field):
            self.seed_quiet_history(field_id)
        missing = self.run_plan(self.confirmed_case(missing_field))
        blocked = self.run_plan(self.confirmed_case(blocked_field))
        # Quiet field 2 spans 64.620-64.624 E, 39.800-39.804 N; the zone is a block inside it.
        zone = self.open_manual(zone_field, zone={"type": "Polygon", "coordinates": [[
            [64.6205, 39.8005], [64.6225, 39.8005], [64.6225, 39.8025], [64.6205, 39.8025],
            [64.6205, 39.8005]]]})
        self.drive(zone, "confirmed")
        zonal = self.run_plan(zone)
        self.assertEqual(self.plan(zonal)["input_snapshot"]["verification_scope"]["kind"], "subfield")
        self.post(blocked_field, 0.95, valid=30.0)  # a huge "improvement" in a rejected scene
        self.post(zone_field, 0.95)                 # field mean up, zone not measurable
        self.reconcile()
        self.assertEqual([self.plan(plan_id)["verification_status"] for plan_id in (missing, blocked, zonal)],
                         ["PENDING_DATA", "QUALITY_BLOCKED", "INCONCLUSIVE"])
        before = self.analytics(self.manager_a)
        remediation = before["current"]["remediation_funnel"]
        self.assertEqual(remediation["awaiting_satellite_verification"]["pending_data"], 1)
        self.assertEqual((remediation["verification_blocked"]["quality_blocked"],
                          remediation["verification_blocked"]["inconclusive"]), (1, 1))
        self.assertEqual(remediation["improved_awaiting_closure"], 0)
        self.assertEqual(before["outcomes"]["resolved_cycles"], 0)
        self.assertEqual(before["completion"]["verification_completion"]["numerator"], 0)
        self.assertEqual(before["cycle_times"]["metrics"]["work_completed_to_verified"]["sample_count"], 0)
        for plan_id in (missing, blocked, zonal):
            self.resolve(plan_id, "override_close", reason="Closed by the manager without evidence")
        outcomes = self.analytics(self.manager_a)["outcomes"]
        self.assertEqual(outcomes["verified"], {"improved": 0, "unchanged": 0, "worsened": 0, "total": 0})
        self.assertEqual(outcomes["unverified"], {"total": 3, "pending_data": 1, "too_early": 0,
                                                  "cloud_blocked": 0, "quality_blocked": 1,
                                                  "provider_degraded": 0, "inconclusive": 1})
        self.assertEqual(outcomes["closed"], {"total": 3, "improved": 0, "without_improvement": 3})
        closure = self.analytics(self.manager_a)["cycle_times"]["metrics"]["case_opened_to_verified_closure"]
        self.assertEqual(closure["sample_count"], 0)

    def test_pending_verification_is_not_a_terminal_outcome(self):
        self.seed_quiet_history(self.field_a)
        plan_id = self.run_plan(self.confirmed_case(self.field_a))
        self.post(self.field_a, 0.80)
        self.reconcile()
        body = self.analytics(self.manager_a)
        self.assertEqual(body["current"]["remediation_funnel"]["improved_awaiting_closure"], 1)
        self.assertEqual(body["outcomes"]["resolved_cycles"], 0)
        self.assertEqual(body["outcomes"]["verified"]["total"], 0)
        # The provisional verification is visible in the completion cohort only.
        self.assertEqual(body["completion"]["verification_completion"]["improved"], 1)
        self.assertEqual(self.plan(plan_id)["status"], "pending_verification")


class TenancyTests(AnalyticsFlow):

    def _two_tenants(self):
        self.open_manual(self.field_a)
        self.open_manual(self.quiet[0], due_in=-timedelta(hours=2))
        self.open_manual(self.field_b, manager=self.manager_b)

    def test_server_scope_wins_and_filters_only_narrow(self):
        self._two_tenants()
        mine = self.analytics(self.manager_a)
        self.assertEqual(mine["current"]["active_problems"]["total"], 2)
        self.assertEqual(mine["coverage"]["fields_in_scope"], 11)
        self.assertEqual({row["enterprise_id"] for row in mine["breakdowns"]["enterprises"]},
                         {self.enterprise_a})
        self.assertNotIn(self.field_b, self.fields_by_id(mine))
        def untimed(body):
            body = {key: value for key, value in body.items() if key != "generated_at"}
            body["current"] = {key: value for key, value in body["current"].items() if key != "as_of"}
            return self.dumps(body)

        self.assertEqual(untimed(self.analytics(self.manager_a, enterprise_id=self.enterprise_a)),
                         untimed(mine))
        theirs = self.analytics(self.manager_b)
        self.assertEqual((theirs["current"]["active_problems"]["total"], theirs["coverage"]["fields_in_scope"]),
                         (1, 1))
        everything = self.analytics(self.admin)
        self.assertEqual(everything["scope"]["authorization"], "global")
        self.assertEqual(everything["current"]["active_problems"]["total"], 3)
        self.assertEqual(everything["coverage"]["fields_in_scope"], 12)
        narrowed = self.analytics(self.admin, enterprise_id=self.enterprise_b)
        self.assertEqual((narrowed["current"]["active_problems"]["total"], narrowed["scope"]["enterprise_id"]),
                         (1, self.enterprise_b))
        one_field = self.analytics(self.manager_a, field_id=self.quiet[0])
        self.assertEqual((one_field["current"]["active_problems"]["total"],
                          one_field["current"]["overdue"]["inspections"],
                          one_field["coverage"]["fields_in_scope"]), (1, 1, 1))

    def test_cross_tenant_targets_are_indistinguishable_from_missing_ones(self):
        self._two_tenants()
        manager = self.client(self.manager_a)
        foreign = manager.get(ENDPOINT, params={"enterprise_id": self.enterprise_b})
        missing = manager.get(ENDPOINT, params={"enterprise_id": 987654})
        self.assertEqual((foreign.status_code, foreign.json()), (404, {"detail": "Enterprise not found"}))
        self.assertEqual((missing.status_code, missing.json()), (foreign.status_code, foreign.json()))
        foreign = manager.get(ENDPOINT, params={"field_id": self.field_b})
        missing = manager.get(ENDPOINT, params={"field_id": 987654})
        self.assertEqual((foreign.status_code, foreign.json()), (404, {"detail": "Field not found"}))
        self.assertEqual((missing.status_code, missing.json()), (foreign.status_code, foreign.json()))
        admin = self.client(self.admin)
        self.assertEqual(admin.get(ENDPOINT, params={"enterprise_id": 987654}).status_code, 404)
        crossed = admin.get(ENDPOINT, params={"enterprise_id": self.enterprise_a, "field_id": self.field_b})
        self.assertEqual((crossed.status_code, crossed.json()), (404, {"detail": "Field not found"}))
        crop = admin.get(ENDPOINT, params={"crop_type_id": 987654})
        self.assertEqual((crop.status_code, crop.json()), (404, {"detail": "Crop type not found"}))

    def test_only_management_roles_are_authorized(self):
        from fastapi.testclient import TestClient

        from api.auth import get_current_active_user
        from main import app

        for user in (self.agronomist_a, self.viewer_a):
            response = self.client(user).get(ENDPOINT)
            self.assertEqual(response.status_code, 403, response.text)
        app.dependency_overrides.pop(get_current_active_user, None)
        anonymous = TestClient(app).get(ENDPOINT)
        self.assertEqual(anonymous.status_code, 401, anonymous.text)

    def test_crop_filter_uses_the_current_crop_season(self):
        q = self.quiet
        self.season(self.field_a, self.cotton)
        self.season(q[0], self.wheat)
        self.season(q[1], self.cotton, self.year - 1)                 # latest season not after this year
        self.season(q[2], self.cotton, self.year - 1)
        self.season(q[2], self.wheat)                                  # rotated to wheat this year
        self.season(q[3], self.wheat)
        self.season(q[3], self.cotton, self.year + 1)                  # a future season is ignored
        for field_id in (self.field_a, q[0], q[1], q[2]):
            self.open_manual(field_id)
        cotton = self.analytics(self.manager_a, crop_type_id=self.cotton)
        self.assertEqual((cotton["current"]["active_problems"]["total"], cotton["coverage"]["fields_in_scope"]),
                         (2, 2))
        self.assertEqual(set(self.fields_by_id(cotton)), {self.field_a, q[1]})
        wheat = self.analytics(self.manager_a, crop_type_id=self.wheat)
        self.assertEqual((wheat["current"]["active_problems"]["total"], wheat["coverage"]["fields_in_scope"]),
                         (2, 3))
        crops = {row["crop_type_id"]: row for row in self.analytics(self.manager_a)["breakdowns"]["crops"]}
        self.assertEqual({key: (row["monitored_fields"], row["current"]["active_problems"])
                          for key, row in crops.items()},
                         {self.cotton: (2, 2), self.wheat: (3, 2), None: (6, 0)})
        self.assertEqual(crops[None]["crop_name"], None)


class AggregationTests(PanoramaMixin, AnalyticsFlow):

    def assert_reconciles(self, body, rows, label):
        current, period = body["current"], body["period_activity"]
        outcomes = body["outcomes"]
        totals = {
            "active_problems": current["active_problems"]["total"],
            "needs_inspection": current["inspection_funnel"]["needs_inspection"]["total"],
            "inspection_active": current["inspection_funnel"]["inspection_active"],
            "awaiting_review": current["inspection_funnel"]["awaiting_review"],
            "awaiting_decision": current["inspection_funnel"]["awaiting_decision"],
            "plan_active": current["remediation_funnel"]["plan_active"]["total"],
            "work_active": current["remediation_funnel"]["work_active"],
            "awaiting_satellite_verification":
                current["remediation_funnel"]["awaiting_satellite_verification"]["total"],
            "verification_blocked": current["remediation_funnel"]["verification_blocked"]["total"],
            "improved_awaiting_closure": current["remediation_funnel"]["improved_awaiting_closure"],
            "not_improved": current["remediation_funnel"]["not_improved"]["total"],
            "reopened": current["remediation_funnel"]["reopened"],
            "overdue_cases": current["overdue"]["cases"],
            "overdue_work_items": current["overdue"]["work_items"],
        }
        for key, expected in totals.items():
            self.assertEqual(sum(row["current"][key] for row in rows), expected, (label, key))
        expected_period = {
            "inspections_opened": period["inspections_opened"]["total"],
            "resolved_cycles": outcomes["resolved_cycles"],
            "improved": outcomes["verified"]["improved"],
            "unchanged": outcomes["verified"]["unchanged"],
            "worsened": outcomes["verified"]["worsened"],
            "unverified": outcomes["unverified"]["total"],
            "reopened": outcomes["reopened"]["total"],
        }
        for key, expected in expected_period.items():
            self.assertEqual(sum(row["period"][key] for row in rows), expected, (label, key))
        self.assertEqual(sum(row["monitored_fields"] for row in rows), body["coverage"]["monitored_fields"])

    def test_totals_reconcile_with_every_breakdown(self):
        self.build_panorama()
        self.open_manual(self.field_b, manager=self.manager_b)
        self.season(self.quiet[0], self.cotton)
        self.season(self.quiet[8], self.wheat)
        body = self.analytics(self.admin, field_limit=200)
        breakdowns = body["breakdowns"]
        self.assertEqual(len(breakdowns["enterprises"]), 2)
        self.assert_reconciles(body, breakdowns["enterprises"], "enterprise")
        self.assert_reconciles(body, breakdowns["crops"], "crop")
        self.assertEqual(breakdowns["fields"]["total"], len(breakdowns["fields"]["items"]))
        self.assert_reconciles(body, breakdowns["fields"]["items"], "field")
        buckets = breakdowns["periods"]
        activity, outcomes = body["period_activity"], body["outcomes"]
        for key, expected in {
            "anomaly_candidates_detected": activity["anomaly_candidates_detected"],
            "inspections_opened": activity["inspections_opened"]["total"],
            "plans_drafted": activity["plans_drafted"],
            "plan_cycles_approved": activity["plan_cycles_approved"],
            "plan_cycles_work_completed": activity["plan_cycles_work_completed"],
            "plan_cycles_verified": activity["plan_cycles_verified"],
            "resolved_cycles": outcomes["resolved_cycles"], "improved": outcomes["verified"]["improved"],
            "unchanged": outcomes["verified"]["unchanged"], "worsened": outcomes["verified"]["worsened"],
            "unverified": outcomes["unverified"]["total"], "closed": outcomes["closed"]["total"],
            "returned_for_rework": outcomes["returned_for_rework"], "reopened": outcomes["reopened"]["total"],
        }.items():
            self.assertEqual(sum(row[key] for row in buckets), expected, key)
        # Paging the field breakdown covers every field exactly once.
        pages, offset = [], 0
        while offset < breakdowns["fields"]["total"]:
            page = self.analytics(self.admin, field_limit=4, field_offset=offset)["breakdowns"]["fields"]
            pages.extend(item["field_id"] for item in page["items"])
            offset += 4
        self.assertEqual(pages, [item["field_id"] for item in breakdowns["fields"]["items"]])
        first = breakdowns["fields"]["items"][0]
        self.assertGreaterEqual(first["current"]["active_problems"], 1)

    def test_current_state_matches_the_operational_center(self):
        from services import operational_center

        self.build_panorama()
        body = self.analytics(self.manager_a)
        current = body["current"]
        summary = operational_center.summary(self.session(), self.manager_a, {})
        data = current["data_unavailable"]
        self.assertEqual(summary["active_situations"],
                         current["active_problems"]["total"] + data["freshness_cases"] + data["external_cases"])
        remediation = current["remediation_funnel"]
        self.assertEqual(summary["reopened"], remediation["reopened"])
        self.assertEqual(summary["not_improved"], remediation["not_improved"]["total"])
        self.assertEqual(summary["verification_blocked"], remediation["verification_blocked"]["total"])
        self.assertEqual(summary["awaiting_evidence"], remediation["work_active"])
        self.assertEqual(summary["awaiting_satellite_verification"],
                         remediation["awaiting_satellite_verification"]["total"]
                         + remediation["verification_blocked"]["total"]
                         + remediation["improved_awaiting_closure"] + remediation["not_improved"]["total"])
        funnel = current["inspection_funnel"]
        # Alert and candidate cases are 'needs_review' there, not awaiting inspection.
        self.assertEqual(summary["awaiting_field_inspection"],
                         funnel["needs_inspection"]["inspections"] + funnel["inspection_active"]
                         + funnel["awaiting_review"])
        self.assertEqual(summary["awaiting_work"], funnel["awaiting_decision"]
                         + remediation["plan_active"]["total"] + remediation["reopened"])
        self.assertEqual(summary["improved_or_closed_recent"], body["outcomes"]["closed"]["improved"])

    def test_work_evidence_and_repeated_verification_do_not_multiply_cases(self):
        working_field = self.quiet[0]
        self.seed_quiet_history(self.field_a)
        self.run_plan(self.confirmed_case(working_field), stop="in_progress", items=3, photos=2)
        plan_id = self.run_plan(self.confirmed_case(self.field_a), items=3, photos=2)
        self.post(self.field_a, 0.76)
        self.reconcile()
        self.post(self.field_a, 0.78, days_after=11)  # a newer accepted scene: a second IMPROVED row
        self.reconcile(days_after_completion=12)
        self.assertEqual(self.count("agronomy_verifications", "plan_id=:id AND status='IMPROVED'",
                                    {"id": plan_id}), 2)
        self.assertEqual(self.count("inspection_evidence", "agronomy_work_item_id IS NOT NULL"), 12)
        body = self.analytics(self.manager_a)
        current = body["current"]
        self.assertEqual(current["active_problems"]["total"], 2)
        self.assertEqual((current["remediation_funnel"]["work_active"],
                          current["remediation_funnel"]["improved_awaiting_closure"]), (1, 1))
        self.assertEqual(current["work_items"], {"active": 3, "planned": 0, "in_progress": 3,
                                                 "unassigned": 0, "overdue": 0})
        activity = body["period_activity"]
        self.assertEqual((activity["plan_cycles_work_completed"], activity["plan_cycles_verified"]), (1, 1))
        self.resolve(plan_id, "close")
        outcomes = self.analytics(self.manager_a)["outcomes"]
        self.assertEqual((outcomes["resolved_cycles"], outcomes["verified"]["improved"]), (1, 1))

    def test_reopened_cycles_are_counted_per_cycle(self):
        self.seed_quiet_history(self.field_a)
        plan_id = self.run_plan(self.confirmed_case(self.field_a))
        self.post(self.field_a, 0.50)
        self.reconcile()
        self.resolve(plan_id, "rework", reason="Worsened after the repair; next cycle")
        self.run_cycle(plan_id)
        self.post(self.field_a, 0.80, days_after=10)
        self.reconcile()
        self.assertEqual(self.plan(plan_id)["verification_status"], "IMPROVED")
        self.resolve(plan_id, "close")
        self.resolve(plan_id, "reopen", reason="Symptoms returned during a field visit")
        plan = self.plan(plan_id)
        self.assertEqual((plan["status"], plan["cycle"]), ("rework", 3))
        self.assertEqual(self.sql("SELECT cycle, status FROM agronomy_verifications WHERE plan_id=:id "
                                  "AND status IN ('IMPROVED','WORSENED') ORDER BY id", {"id": plan_id}),
                         [{"cycle": 1, "status": "WORSENED"}, {"cycle": 2, "status": "IMPROVED"}])
        body = self.analytics(self.manager_a)
        self.assertEqual(body["current"]["remediation_funnel"]["reopened"], 1)
        self.assertEqual(body["current"]["active_problems"]["total"], 1)
        self.assertEqual(body["outcomes"], {
            "resolved_cycles": 2,
            "verified": {"improved": 1, "unchanged": 0, "worsened": 1, "total": 2},
            "unverified": {"total": 0, "pending_data": 0, "too_early": 0, "cloud_blocked": 0,
                           "quality_blocked": 0, "provider_degraded": 0, "inconclusive": 0},
            "closed": {"total": 1, "improved": 1, "without_improvement": 0},
            "returned_for_rework": 1,
            "reopened": {"total": 2, "after_closure": 1, "after_verification": 1},
        })
        activity = body["period_activity"]
        self.assertEqual((activity["plans_drafted"], activity["plan_cycles_approved"],
                          activity["plan_cycles_work_completed"], activity["plan_cycles_verified"]),
                         (1, 2, 2, 2))
        completion = body["completion"]
        self.assertEqual((completion["work_completion"]["numerator"],
                          completion["work_completion"]["denominator"]), (2, 2))
        self.assertEqual((completion["verification_completion"]["improved"],
                          completion["verification_completion"]["worsened"]), (1, 1))
        metrics = body["cycle_times"]["metrics"]
        self.assertEqual((metrics["plan_approved_to_work_completed"]["sample_count"],
                          metrics["work_completed_to_verified"]["sample_count"],
                          metrics["case_opened_to_verified_closure"]["sample_count"]), (2, 2, 0))


class TimeTests(AnalyticsFlow):

    def _move(self, table, column, identifier, value):
        self.sql(f"UPDATE {table} SET {column}=:value WHERE id=:id", {"value": value, "id": identifier})

    def test_period_boundaries_are_half_open_in_local_time(self):
        ids = [self.open_manual(field_id) for field_id in self.quiet[:6]]
        start = datetime(2026, 3, 1, tzinfo=TASHKENT)
        end = datetime(2026, 4, 1, tzinfo=TASHKENT)
        anchors = [
            start,                                                # first local instant: inside
            start - timedelta(microseconds=1),                    # just before: outside
            end - timedelta(microseconds=1),                      # last local instant: inside
            end,                                                  # exclusive end: outside
            datetime(2026, 2, 28, 20, 0, tzinfo=timezone.utc),    # 01:00 local on 1 March: inside
            datetime(2026, 3, 31, 19, 30, tzinfo=timezone.utc),   # 00:30 local on 1 April: outside
        ]
        for inspection_id, anchor in zip(ids, anchors):
            self._move("field_inspections", "created_at", inspection_id, anchor)
        body = self.analytics(self.manager_a, date_from=MARCH_FROM, date_to=MARCH_TO, granularity="day")
        self.assertEqual(body["period_activity"]["inspections_opened"]["total"], 3)
        days = {row["bucket_start"]: row["inspections_opened"] for row in body["breakdowns"]["periods"]}
        self.assertEqual(len(days), 31)
        self.assertEqual((days["2026-03-01"], days["2026-03-31"], sum(days.values())), (2, 1, 3))
        # Current state is not narrowed by the period.
        self.assertEqual(body["current"]["active_problems"]["total"], 6)

    def test_week_and_month_buckets_are_local_calendar_and_clipped(self):
        ids = [self.open_manual(field_id) for field_id in self.quiet[:3]]
        for inspection_id, day in zip(ids, (4, 9, 20)):
            self._move("field_inspections", "created_at", inspection_id,
                       datetime(2026, 3, day, 23, 30, tzinfo=TASHKENT))
        weeks = self.analytics(self.manager_a, date_from="2026-03-04", date_to="2026-03-20",
                               granularity="week")["breakdowns"]["periods"]
        self.assertEqual([(row["bucket_start"], row["bucket_end"], row["inspections_opened"]) for row in weeks],
                         [("2026-03-04", "2026-03-08", 1), ("2026-03-09", "2026-03-15", 1),
                          ("2026-03-16", "2026-03-20", 1)])
        months = self.analytics(self.manager_a, date_from="2026-02-20", date_to="2026-03-20",
                                granularity="month")["breakdowns"]["periods"]
        self.assertEqual([(row["bucket_start"], row["bucket_end"], row["inspections_opened"]) for row in months],
                         [("2026-02-20", "2026-02-28", 0), ("2026-03-01", "2026-03-20", 3)])

    def test_lifecycle_events_are_anchored_at_their_own_time(self):
        self.seed_quiet_history(self.field_a)
        plan_id = self.run_plan(self.confirmed_case(self.field_a))
        self.post(self.field_a, 0.80)
        self.reconcile()
        self.resolve(plan_id, "close")
        inside = datetime(2026, 3, 15, 12, 0, tzinfo=TASHKENT)
        self.sql("UPDATE agronomy_events SET occurred_at=:at WHERE plan_id=:id AND event_type='close'",
                 {"at": inside, "id": plan_id})
        self.sql("UPDATE agronomy_plans SET closed_at=:at WHERE id=:id", {"at": inside, "id": plan_id})
        march = self.analytics(self.manager_a, date_from=MARCH_FROM, date_to=MARCH_TO)
        self.assertEqual((march["outcomes"]["resolved_cycles"], march["outcomes"]["verified"]["improved"]),
                         (1, 1))
        self.assertEqual(march["period_activity"]["plan_cycles_approved"], 0)
        recent = self.analytics(self.manager_a)
        self.assertEqual(recent["outcomes"]["resolved_cycles"], 0)
        self.assertEqual(recent["period_activity"]["plan_cycles_approved"], 1)

    def test_open_cycles_have_no_duration(self):
        q = self.quiet
        for field_id in q[:2]:
            self.seed_quiet_history(field_id)
        self.run_plan(self.confirmed_case(q[0]), stop="approved")
        self.run_plan(self.confirmed_case(q[1]))
        metrics = self.analytics(self.manager_a)["cycle_times"]["metrics"]
        self.assertEqual(metrics["plan_approved_to_work_completed"]["sample_count"], 1)
        self.assertEqual(metrics["work_completed_to_verified"]["sample_count"], 0)
        self.assertEqual(metrics["case_opened_to_verified_closure"]["sample_count"], 0)

    def test_median_p90_and_sample_count(self):
        ids = [self.confirmed_case(field_id) for field_id in self.quiet]
        reviewed = datetime.now(timezone.utc) - timedelta(minutes=5)
        for hours, inspection_id in zip(range(1, 11), ids):
            self.sql("UPDATE field_inspections SET created_at=:created, reviewed_at=:reviewed, "
                     "confirmed_at=:reviewed WHERE id=:id",
                     {"created": reviewed - timedelta(hours=hours), "reviewed": reviewed, "id": inspection_id})
        metric = self.analytics(self.manager_a)["cycle_times"]["metrics"]["inspection_opened_to_reviewed"]
        self.assertEqual((metric["sample_count"], metric["median_hours"], metric["p90_hours"]), (10, 5.5, 9.1))
        self.assertEqual(metric["start_event"], "field_inspections.created_at (inspection opened)")
        old = reviewed - timedelta(days=60)
        self.sql("UPDATE field_inspections SET created_at=:created, reviewed_at=:reviewed, "
                 "confirmed_at=:reviewed WHERE id=:id",
                 {"created": old - timedelta(hours=10), "reviewed": old, "id": ids[9]})
        metric = self.analytics(self.manager_a)["cycle_times"]["metrics"]["inspection_opened_to_reviewed"]
        self.assertEqual((metric["sample_count"], metric["median_hours"], metric["p90_hours"]), (9, 5.0, None))

    def test_overdue_follows_each_stage_deadline(self):
        q = self.quiet
        for field_id in q[:3]:
            self.seed_quiet_history(field_id)
        self.open_manual(q[3], due_in=-timedelta(hours=1))                     # overdue inspection
        self.open_manual(q[4], due_in=timedelta(hours=6))                      # not yet due
        late_submitted = self.open_manual(q[5], due_in=-timedelta(hours=1))
        self.drive(late_submitted, "submitted")                                # still inspection stage
        late_planned = self.open_manual(q[6], due_in=-timedelta(hours=1))
        self.drive(late_planned, "confirmed")
        self.run_plan(late_planned, stop="draft")                              # plan stage: no deadline yet
        self.run_plan(self.confirmed_case(q[0]), stop="in_progress", items=2, due_in=-timedelta(hours=1))
        self.run_plan(self.confirmed_case(q[1]), stop="approved", due_in=timedelta(days=2))
        pending = self.run_plan(self.confirmed_case(q[2]), due_in=-timedelta(hours=1))
        self.assertEqual(self.plan(pending)["status"], "pending_verification")  # completed: no work due
        overdue = self.analytics(self.manager_a)["current"]["overdue"]
        self.assertEqual(overdue, {"cases": 3, "inspections": 2, "plans_with_overdue_work": 1,
                                   "work_items": 2})


class QueryBudgetTests(PanoramaMixin, AnalyticsFlow):

    def statements(self, user, **params):
        seen = []

        def record(conn, cursor, statement, parameters, context, executemany):
            seen.append(statement)

        event.listen(self.engine, "before_cursor_execute", record)
        try:
            self.analytics(user, **params)
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        return seen

    def test_statement_count_does_not_grow_with_the_data(self):
        small = (len(self.statements(self.manager_a)),
                 len(self.statements(self.admin, field_id=self.field_a, crop_type_id=self.cotton)))
        self.build_panorama()
        for field_id in self.quiet:
            self.season(field_id, self.cotton)
        large = (len(self.statements(self.manager_a)),
                 len(self.statements(self.admin, field_id=self.field_a, crop_type_id=self.cotton)))
        self.assertEqual(small, large)
        self.assertEqual(large, (1, 2))
        statement = self.statements(self.manager_a, granularity="day")[0]
        self.assertNotIn("corrective_actions", statement)
        self.assertNotIn("action_verification_requests", statement)
