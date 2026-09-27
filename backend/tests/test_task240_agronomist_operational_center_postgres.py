"""TASK_240: the agronomist Operational Center scope SQL (B6) on PostgreSQL.

B6: ``services.operational_center._agronomist_field_condition`` left one
parenthesis open, so for every agronomist GET /api/operational-center/
filter-options and GET /api/operational-center/fields/{field_id}/timeline
failed with SQLSTATE 42601 (syntax error) and answered HTTP 500. The tests
below execute the generated statements on PostgreSQL through main.app and pin
the unchanged scope around them (TASK_221 RBAC): an agronomist sees a field of
their own enterprise only while an inspection on it is assigned to them and
still active, or while work on it is assigned to them and not yet finished.

The tenants mirror the pilot's shape with synthetic identities: enterprise 9
(a manager, two agronomists, a viewer) and enterprise 7 (a manager and an
agronomist). Assignments move through the canonical inspection (TASK_217) and
agronomy-plan (TASK_220) APIs; SQL here only seeds reference data and moves
one recorded closure time past the 30-day window. See tests/task232_support.py.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import event, text

from task225_support import FIELD_A, FIELD_B
from task232_support import AnalyticsFlow


OPTIONS = "/api/operational-center/filter-options"
ANALYTICS = "/api/management-analytics"
NOT_FOUND = {"detail": "Field not found"}
UNKNOWN_FIELD = 987654


def timeline(field_id):
    return f"/api/operational-center/fields/{field_id}/timeline"


def paren_depths(sql):
    """Nesting depth before each character; single-quoted literals are skipped."""
    depth, quoted, depths = 0, False, []
    for char in sql:
        depths.append(depth)
        if char == "'":
            quoted = not quoted
        elif not quoted and char in "()":
            depth += 1 if char == "(" else -1
    return depths, depth


def test_agronomist_condition_is_one_group_of_two_sibling_scope_sources():
    """A database-free guard for the backend lane; PostgreSQL executes the SQL below."""
    from services.operational_center import _agronomist_field_condition

    condition = _agronomist_field_condition("f")
    depths, final = paren_depths(condition)
    assert final == 0, "unbalanced parentheses"
    assert condition.startswith("(EXISTS (") and min(depths[1:]) == 1, "one outer group spans the condition"
    work = condition.index("OR EXISTS (SELECT 1 FROM agronomy_work_items")
    assert depths[work] == 1, "assigned work is a sibling of the inspection EXISTS, not nested in it"
    assert set(text(condition).compile().params) == {"actor_user_id"}


class AgronomistScopeFlow(AnalyticsFlow):
    """The TASK_232 flow on pilot-shaped tenants: enterprise 9 is A, enterprise 7 is B."""

    def setUp(self):
        super().setUp()
        self.sql("DELETE FROM crop_types WHERE code LIKE 't240_%'")
        self.barley = self.scalar(
            "INSERT INTO crop_types (code, name_ru) VALUES ('t240_barley','T240 Ячмень') RETURNING id")

    def tearDown(self):
        super().tearDown()
        self.sql("DELETE FROM crop_types WHERE code LIKE 't240_%'")

    def _seed(self):
        self.sql("INSERT INTO enterprises (id, name, code, is_active) VALUES "
                 "(9,'T240 Enterprise 9','T240E9',true), (7,'T240 Enterprise 7','T240E7',true)")
        self.enterprise_a, self.enterprise_b = 9, 7
        self.field_a = self._field(9, "T240 Field 9A", FIELD_A)
        self.field_b = self._field(7, "T240 Field 7A", FIELD_B)
        self.admin = self._user(None, "admin", "t240-admin")
        self.manager_a = self._user(9, "manager", "t240-manager-9")
        self.agronomist_a = self._user(9, "agronomist", "t240-agronomist-9")
        self.agronomist_a2 = self._user(9, "agronomist", "t240-agronomist-9-second")
        self.viewer_a = self._user(9, "viewer", "t240-viewer-9")
        self.manager_b = self._user(7, "manager", "t240-manager-7")
        self.agronomist_b = self._user(7, "agronomist", "t240-agronomist-7")

    def inspect(self, field_id, agronomist, stop=None):
        """A canonical inspection assigned to ``agronomist``, driven to ``stop``."""
        manager = self.manager_b if agronomist.enterprise_id == self.enterprise_b else self.manager_a
        inspection_id = self.open_manual(field_id, manager=manager, assign=agronomist)
        if stop:
            self.drive(inspection_id, stop, manager=manager, agronomist=agronomist)
        return inspection_id

    def build_scope(self):
        """One enterprise-9 field per branch of the agronomist condition, plus enterprise 7."""
        a, a2, b = self.agronomist_a, self.agronomist_a2, self.agronomist_b
        f = {"active": self.field_a, "foreign": self.field_b}
        for name in ("confirmed", "work", "closed_recent", "closed_old", "rejected", "others", "idle"):
            f[name] = self.extra_field(self.enterprise_a)
        self.inspect(f["active"], a)                                  # assigned, not started
        self.inspect(f["confirmed"], a, "confirmed")                  # confirmed, no plan yet
        # A2 inspected and confirmed; the approved plan's planned work is A's.
        self.plan_with_items(self.inspect(f["work"], a2, "confirmed"), [timedelta(days=3)])
        for name in ("closed_recent", "closed_old"):
            self.resolve(self.run_plan(self.inspect(f[name], a, "confirmed")), "override_close")
        self.sql("UPDATE agronomy_plans p SET closed_at=p.closed_at - interval '31 days' "
                 "FROM field_inspections i WHERE i.id=p.inspection_id AND i.field_id=:field",
                 {"field": f["closed_old"]})
        self.inspect(f["rejected"], a, "rejected")
        self.inspect(f["others"], a2)                                 # someone else's inspection
        self.inspect(f["foreign"], b)
        self.season(f["active"], self.cotton)
        self.season(f["idle"], self.wheat)
        self.season(f["foreign"], self.barley)
        self.f = f
        return f

    def scope_of(self, agronomist):
        f = self.f
        return {
            self.agronomist_a.id: {f["active"], f["confirmed"], f["work"], f["closed_recent"]},
            self.agronomist_a2.id: {f["work"], f["others"]},
            self.agronomist_b.id: {f["foreign"]},
        }[agronomist.id]

    def options(self, user, expected=200, **params):
        response = self.client(user).get(OPTIONS, params=params)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    @staticmethod
    def ids(body):
        return {key: {item["id"] for item in items} for key, items in body.items()}


class AgronomistFilterOptionsTests(AgronomistScopeFlow):

    def test_each_agronomist_gets_only_their_assigned_scope(self):
        self.build_scope()
        a, a2, b = self.agronomist_a, self.agronomist_a2, self.agronomist_b
        self.assertEqual(self.ids(self.options(a)), {
            "enterprises": {9}, "fields": self.scope_of(a), "crops": {self.cotton}, "assignees": {a.id}})
        self.assertEqual(self.ids(self.options(a2)), {
            "enterprises": {9}, "fields": self.scope_of(a2), "crops": set(), "assignees": {a2.id}})
        self.assertEqual(self.ids(self.options(b)), {
            "enterprises": {7}, "fields": self.scope_of(b), "crops": {self.barley}, "assignees": {b.id}})

    def test_request_filters_only_narrow_the_server_scope(self):
        self.build_scope()
        a, b = self.agronomist_a, self.agronomist_b
        self.assertEqual(self.options(a, enterprise_id=9), self.options(a))
        self.assertEqual(self.options(b, enterprise_id=7), self.options(b))
        for user, enterprise_id in ((a, 7), (b, 9), (a, 987654)):
            self.assertEqual(self.options(user, 404, enterprise_id=enterprise_id), {"detail": "Enterprise not found"})

    def test_an_agronomist_without_assignments_gets_a_valid_empty_scope(self):
        self.build_scope()
        idle = self._user(7, "agronomist", "t240-agronomist-7-idle")
        self.assertEqual(self.options(idle), {
            "enterprises": [{"id": 7, "label": "T240 Enterprise 7", "enterprise_id": None}],
            "fields": [], "crops": [],
            "assignees": [{"id": idle.id, "label": "T240-AGRONOMIST-7-IDLE", "enterprise_id": 7}],
        })
        for field_id in (self.f["foreign"], self.f["active"]):
            response = self.client(idle).get(timeline(field_id))
            self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))

    def test_admin_manager_and_viewer_options_are_unchanged(self):
        f = self.build_scope()
        enterprise_9 = {value for key, value in f.items() if key != "foreign"}
        people_9 = {self.manager_a.id, self.agronomist_a.id, self.agronomist_a2.id}
        people_7 = {self.manager_b.id, self.agronomist_b.id}
        for user in (self.manager_a, self.viewer_a):
            self.assertEqual(self.ids(self.options(user)), {
                "enterprises": {9}, "fields": enterprise_9, "crops": {self.cotton, self.wheat},
                "assignees": people_9})
            self.assertEqual(self.options(user, 404, enterprise_id=7), {"detail": "Enterprise not found"})
        manager_7 = self.ids(self.options(self.manager_b))
        self.assertEqual(manager_7, {
            "enterprises": {7}, "fields": {f["foreign"]}, "crops": {self.barley}, "assignees": people_7})
        self.assertEqual(self.ids(self.options(self.admin)), {
            "enterprises": {7, 9}, "fields": set(f.values()), "crops": {self.cotton, self.wheat, self.barley},
            "assignees": people_9 | people_7})
        self.assertEqual(self.ids(self.options(self.admin, enterprise_id=7)), manager_7)

    def test_management_analytics_stays_forbidden_for_agronomists(self):
        self.build_scope()
        for user in (self.agronomist_a, self.agronomist_a2, self.agronomist_b):
            self.assertEqual(self.client(user).get(ANALYTICS).status_code, 403)
        self.assertEqual(self.client(self.manager_a).get(ANALYTICS).status_code, 200)


class AgronomistTimelineTests(AgronomistScopeFlow):

    def get(self, user, field_id):
        return self.client(user).get(timeline(field_id))

    def test_agronomists_read_the_timeline_of_fields_in_their_scope(self):
        f = self.build_scope()
        for agronomist in (self.agronomist_a, self.agronomist_a2, self.agronomist_b):
            for field_id in self.scope_of(agronomist):
                body = self.ok(self.get(agronomist, field_id))
                self.assertEqual((sorted(body), body["field_id"], body["limit"], body["offset"]),
                                 (["field_id", "items", "limit", "offset"], field_id, 100, 0))
                self.assertTrue(body["items"], (agronomist.id, field_id))
        kinds = {item["source_kind"] for item in self.ok(self.get(self.agronomist_a, f["work"]))["items"]}
        self.assertEqual(kinds, {"inspection", "agronomy_plan"})

    def test_fields_outside_the_agronomist_scope_are_not_found(self):
        f = self.build_scope()
        denied = (
            (self.agronomist_a, (f["closed_old"], f["rejected"], f["others"], f["idle"], f["foreign"])),
            (self.agronomist_a2, (f["active"], f["confirmed"], f["closed_recent"], f["idle"], f["foreign"])),
            (self.agronomist_b, tuple(value for key, value in f.items() if key != "foreign")),
        )
        for agronomist, field_ids in denied:
            for field_id in field_ids + (UNKNOWN_FIELD,):
                response = self.get(agronomist, field_id)
                self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND),
                                 (agronomist.id, field_id))

    def test_admin_manager_and_viewer_timelines_are_unchanged(self):
        f = self.build_scope()
        for user in (self.manager_a, self.viewer_a):
            for name, field_id in f.items():
                self.assertEqual(self.get(user, field_id).status_code, 404 if name == "foreign" else 200,
                                 (user.role, name))
        self.assertEqual(self.get(self.manager_b, f["foreign"]).status_code, 200)
        self.assertEqual(self.get(self.manager_b, f["active"]).status_code, 404)
        for field_id in f.values():
            self.assertEqual(self.get(self.admin, field_id).status_code, 200)
        self.assertEqual(self.get(self.admin, UNKNOWN_FIELD).status_code, 404)


class GeneratedSqlTests(AgronomistScopeFlow):

    def test_the_condition_executes_alone_for_any_alias(self):
        from services.operational_center import _agronomist_field_condition

        self.build_scope()
        for alias in ("f", "scoped_field"):
            condition = _agronomist_field_condition(alias)
            for agronomist in (self.agronomist_a, self.agronomist_a2, self.agronomist_b):
                rows = self.sql(f"SELECT {alias}.id FROM fields {alias} WHERE {condition}",
                                {"actor_user_id": agronomist.id})
                self.assertEqual({row["id"] for row in rows}, self.scope_of(agronomist), (alias, agronomist.id))

    def statements(self, user, path):
        seen = []

        def record(conn, cursor, statement, parameters, context, executemany):
            seen.append((statement, parameters))

        event.listen(self.engine, "before_cursor_execute", record)
        try:
            self.ok(self.client(user).get(path))
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        return seen

    def test_statement_count_is_fixed_and_equals_the_manager_path(self):
        a = self.agronomist_a

        def counts():
            return [len(self.statements(user, path))
                    for user in (a, self.manager_a) for path in (OPTIONS, timeline(self.field_a))]

        self.inspect(self.field_a, a)
        small = counts()
        for _ in range(5):
            self.inspect(self.extra_field(self.enterprise_a), a)
        self.assertEqual(counts(), small)
        self.assertEqual(small, [4, 2, 4, 2])
        scoped = [parameters for statement, parameters in self.statements(a, OPTIONS)
                  if "agronomy_work_items scope_work" in statement]
        self.assertEqual(scoped, [{"enterprise_id": 9, "actor_user_id": a.id}] * 2)
        scoped = [parameters for statement, parameters in self.statements(a, timeline(self.field_a))
                  if "agronomy_work_items scope_work" in statement]
        self.assertEqual(scoped, [{"field_id": self.field_a, "enterprise_id": 9, "actor_user_id": a.id}])
