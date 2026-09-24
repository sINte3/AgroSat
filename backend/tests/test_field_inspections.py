"""Offline coverage for the legacy TASK_209 field-inspection API.

Since TASK_225 the legacy workflow is read-only: rows stay readable and every
write answers 410 Gone without touching the database.
"""
import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.field_inspections import router  # noqa: E402
from services import field_inspections as service  # noqa: E402
from services.field_inspections import ITEM_SELECT, ActorScope, get, list_items  # noqa: E402


def user(role="admin", identifier=7, enterprise=None):
    return SimpleNamespace(role=role, id=identifier, enterprise_id=enterprise)


def item_row(identifier=11, status="pending", **changes):
    now = datetime(2026, 7, 15, 9)
    values = dict(id=identifier, field_id=3, field_name="Field", enterprise_id=5,
        enterprise_name="Enterprise", created_by_id=7, created_by_name="Creator",
        assigned_to_id=8, assigned_to_name="Assignee", source="manual",
        source_priority=None, source_attention_score=None, source_observation_date=None,
        source_reason_codes=[], title="Inspect crop", instructions=None, due_date=None,
        status=status, is_overdue=False, version=1, created_at=now, updated_at=now,
        started_at=None, completed_at=None, cancelled_at=None, completion_summary=None,
        cancellation_reason=None)
    values.update(changes)
    return values


class Result:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def first(self): return self.rows[0] if self.rows else None
    def all(self): return self.rows


class Session:
    """Scripted request-scoped Session; execute calls autobegin it."""
    def __init__(self, outcomes=(), active=True, actor=None):
        self.outcomes, self.calls = list(outcomes), []
        self.commits = self.rollbacks = self.begins = 0
        self.active = active
        self.actor = actor
    def in_transaction(self): return self.active
    def begin(self): self.begins += 1; raise AssertionError("service called begin")
    def execute(self, statement, params=None):
        self.active = True; self.calls.append((str(statement), params or {}))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException): raise outcome
        return Result(outcome)
    def commit(self):
        self.commits += 1; self.active = False
        if self.actor: self.actor.expire()
    def rollback(self):
        self.rollbacks += 1; self.active = False
        if self.actor: self.actor.expire()


class ContractTests(unittest.TestCase):
    def test_nullable_due_date_projects_a_boolean_overdue_flag(self):
        self.assertIn(
            "COALESCE(i.status IN ('pending','in_progress') AND i.due_date < :today, false) AS is_overdue",
            ITEM_SELECT,
        )

    def test_actor_scope_is_frozen_scalar_data_and_session_expires_on_commit(self):
        from dataclasses import FrozenInstanceError
        from database import SessionLocal
        actor = ActorScope("admin", 7, None)
        with self.assertRaises(FrozenInstanceError): actor.role = "viewer"
        self.assertTrue(SessionLocal.kw.get("expire_on_commit", True))

    def test_all_seven_operations_require_auth(self):
        app = FastAPI(); app.include_router(router); client = TestClient(app)
        calls = [("post", "", {}), ("get", "", None), ("get", "/1", None),
                 ("patch", "/1", {}), ("post", "/1/start", {}),
                 ("post", "/1/complete", {}), ("post", "/1/cancel", {})]
        for method, suffix, body in calls:
            response = getattr(client, method)("/api/field-inspections" + suffix, json=body) if body is not None else getattr(client, method)("/api/field-inspections" + suffix)
            self.assertEqual(401, response.status_code)
        operations = [operation for path in app.openapi()["paths"].values() for operation in path.values()]
        self.assertEqual(7, len(operations)); self.assertTrue(all(x["security"] for x in operations))

    def test_migration_identity_table_scope_and_partial_index(self):
        path = BACKEND / "alembic/versions/0005_field_inspections.py"
        spec = importlib.util.spec_from_file_location("task200a_migration", path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.assertEqual("0005_field_inspections", module.revision)
        self.assertEqual("0004_repair_core_constraints", module.down_revision)
        source = path.read_text(encoding="utf-8")
        self.assertEqual(1, source.count("op.create_table("))
        self.assertIn('op.create_table(\n        "field_inspections"', source)
        self.assertIn("uq_field_inspections_one_active_per_field", source)
        self.assertIn("postgresql_where=sa.text(ACTIVE_PREDICATE)", source)

    def test_fastapi_reuses_auth_session_and_auth_select_autobegins(self):
        session = Session([], active=False)
        def get_db(): yield session
        def auth(db=Depends(get_db)):
            db.active = True; db.calls.append(("AUTH SELECT", {})); return (db, user())
        app = FastAPI()
        @app.get("/proof")
        def proof(db=Depends(get_db), authenticated=Depends(auth)):
            return {"same": db is authenticated[0], "active": db.in_transaction()}
        result = TestClient(app).get("/proof").json()
        self.assertEqual({"same": True, "active": True}, result)

    def test_source_has_no_forbidden_access_or_transaction_helpers(self):
        source = (BACKEND / "services/field_inspections.py").read_text(encoding="utf-8").lower()
        for token in ("db.begin", "begin_nested", "db.query", "email", "password", "redis", "sentinel", "scouting_notes", "http://", "https://"):
            self.assertNotIn(token, source)


class RetirementTests(unittest.TestCase):
    """TASK_225: no second inspection state machine accepts new work."""

    WRITES = (("post", ""), ("patch", "/11"), ("post", "/11/start"),
              ("post", "/11/complete"), ("post", "/11/cancel"))

    def test_every_legacy_write_is_410_with_a_canonical_replacement_and_no_sql(self):
        from api.auth import get_current_active_user
        from database import get_db
        untouched = Session([])
        app = FastAPI(); app.include_router(router)
        app.dependency_overrides[get_db] = lambda: untouched
        app.dependency_overrides[get_current_active_user] = lambda: user("manager", 7, 5)
        client = TestClient(app)
        for method, suffix in self.WRITES:
            with self.subTest(method=method, suffix=suffix):
                response = getattr(client, method)(
                    "/api/field-inspections" + suffix,
                    json={"expected_version": 1, "field_id": 3, "title": "Inspect crop", "source": "manual"},
                    headers={"Idempotency-Key": "task225-legacy-write"},
                )
                self.assertEqual(410, response.status_code, response.text)
                detail = response.json()["detail"]
                self.assertEqual("lifecycle_endpoint_retired", detail["code"])
                self.assertIn("/api/anomaly-inspections", detail["replacement"])
        self.assertEqual([], untouched.calls)
        self.assertEqual((0, 0), (untouched.commits, untouched.rollbacks))

    def test_legacy_service_has_no_write_path(self):
        source = (BACKEND / "services/field_inspections.py").read_text(encoding="utf-8")
        for fragment in ("INSERT INTO", "UPDATE field_inspections", ".commit("):
            self.assertNotIn(fragment, source)
        for name in ("create", "update", "transition"):
            self.assertFalse(hasattr(service, name))


class ReadTests(unittest.TestCase):
    def filters(self, **changes):
        values = dict(enterprise_id=None, field_id=None, assigned_to_id=None, status=None,
            overdue_only=False, due_before=None, created_after=None, limit=10, offset=0)
        values.update(changes); return values

    def test_list_one_select_tenant_filter_and_nonempty_summary(self):
        row = item_row(); row.update(total_count=4, pending_count=1, in_progress_count=1,
            completed_count=1, cancelled_count=1, overdue_count=1)
        db = Session([[row]])
        result = list_items(db, user("viewer", 9, 5), self.filters())
        self.assertEqual(1, len(db.calls)); self.assertEqual(4, result["summary"]["total"])
        self.assertEqual(1, len(result["items"])); self.assertIn("WITH filtered AS", db.calls[0][0])
        self.assertIn("i.enterprise_id=:eid", db.calls[0][0]); self.assertEqual(5, db.calls[0][1]["eid"])

    def test_empty_offset_page_keeps_summary_and_creates_no_fake_item(self):
        summary_only = {"id": None, "total_count": 3, "pending_count": 2,
            "in_progress_count": 1, "completed_count": 0, "cancelled_count": 0,
            "overdue_count": 1}
        db = Session([[summary_only]])
        result = list_items(db, user(), self.filters(offset=50))
        self.assertEqual({"total": 3, "pending": 2, "in_progress": 1,
            "completed": 0, "cancelled": 0, "overdue": 1}, result["summary"])
        self.assertEqual([], result["items"]); self.assertEqual(1, len(db.calls))
        self.assertIn("LEFT JOIN paged ON true", db.calls[0][0])

    def test_get_is_joined_tenant_select_without_sensitive_columns(self):
        db = Session([[item_row()]])
        result = get(db, user("viewer", 9, 5), 11)
        sql, params = db.calls[0]
        self.assertEqual(11, result["id"]); self.assertIn("JOIN fields", sql)
        self.assertIn("i.enterprise_id=:eid", sql); self.assertEqual(5, params["eid"])
        self.assertNotIn("email", sql.lower()); self.assertNotIn("password", sql.lower())

    def test_read_success_has_no_boundary_and_read_failures_roll_back_once(self):
        row = item_row(); row.update(total_count=1, pending_count=1, in_progress_count=0,
            completed_count=0, cancelled_count=0, overdue_count=0)
        listed = Session([[row]])
        list_items(listed, user(), self.filters())
        fetched = Session([[item_row()]])
        get(fetched, user(), 11)
        self.assertEqual((0, 0), (listed.commits, listed.rollbacks))
        self.assertEqual((0, 0), (fetched.commits, fetched.rollbacks))

        foreign_filter = Session([])
        with self.assertRaises(HTTPException):
            list_items(foreign_filter, user("viewer", 9, 5), self.filters(enterprise_id=6))
        missing = Session([[]])
        with self.assertRaises(HTTPException): get(missing, user(), 999)
        self.assertEqual(1, foreign_filter.rollbacks)
        self.assertEqual(1, missing.rollbacks)

    def test_unknown_and_missing_tenant_roles_are_rejected(self):
        for actor in (user("unknown", 9, 5), user("viewer", 9, None)):
            with self.subTest(role=actor.role, enterprise=actor.enterprise_id):
                db = Session([])
                with self.assertRaises(HTTPException) as caught:
                    list_items(db, actor, self.filters())
                self.assertEqual(403, caught.exception.status_code)
                self.assertEqual(1, db.rollbacks)


if __name__ == "__main__":
    unittest.main()
