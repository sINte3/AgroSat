"""Offline behavioral coverage for the field-inspection workflow."""
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
from sqlalchemy.exc import IntegrityError  # noqa: E402

from api.field_inspections import router  # noqa: E402
from schemas.field_inspection import (CancelInspectionRequest, CompleteInspectionRequest,
    CreateInspectionRequest, TransitionRequest, UpdateInspectionRequest)  # noqa: E402
from services.field_inspections import (ActorScope, create, fingerprint, get, list_items,
                                        transition, update)  # noqa: E402


def user(role="admin", identifier=7, enterprise=None):
    return SimpleNamespace(role=role, id=identifier, enterprise_id=enterprise)


def payload(**changes):
    values = {"field_id": 3, "title": "Inspect crop", "source": "manual"}
    values.update(changes)
    return CreateInspectionRequest(**values)


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


class ExpiringUser:
    """ORM-user double that rejects attribute access after a boundary."""
    def __init__(self, role="admin", identifier=7, enterprise=None):
        self._values = {"role": role, "id": identifier, "enterprise_id": enterprise}
        self._expired = False
    def expire(self): self._expired = True
    def __getattr__(self, name):
        if name in self._values:
            if self._expired:
                raise AssertionError(f"expired ORM actor attribute read: {name}")
            return self._values[name]
        raise AttributeError(name)


def integrity_error():
    return IntegrityError("INSERT", {}, Exception("constraint"))


class ContractTests(unittest.TestCase):
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


class CreateTests(unittest.TestCase):
    def successful(self, actor=None, request=None):
        actor, request = actor or user(), request or payload(assigned_to_id=8)
        db = Session([[], [{"id": 3, "enterprise_id": 5}], [{"id": 8}], [], [{"id": 11}], [item_row()]])
        result = create(db, actor, request, "request-key-0001")
        return db, result

    def test_active_autobegun_session_create_commits_once_and_tenant_is_in_sql(self):
        db, result = self.successful(user("agronomist", 8, 5), payload())
        self.assertTrue(result[0]); self.assertEqual(11, result[1]["id"])
        self.assertEqual((1, 0, 0), (db.commits, db.rollbacks, db.begins))
        field_sql, field_params = db.calls[1]
        self.assertIn("f.enterprise_id=:eid", field_sql); self.assertEqual(5, field_params["eid"])
        insert_sql, insert_params = db.calls[4]
        self.assertEqual(8, insert_params["assigned"])
        self.assertIn("field_id,enterprise_id,created_by_id", insert_sql)

    def test_failed_create_viewer_and_ineligible_assignee_roll_back(self):
        db = Session([])
        with self.assertRaises(HTTPException) as caught: create(db, user("viewer", 9, 5), payload(), "request-key-0002")
        self.assertEqual(403, caught.exception.status_code); self.assertEqual(1, db.rollbacks)
        db = Session([[], [{"id": 3, "enterprise_id": 5}], []])
        with self.assertRaises(HTTPException) as caught: create(db, user(), payload(assigned_to_id=99), "request-key-0003")
        self.assertEqual(422, caught.exception.status_code); self.assertEqual(1, db.rollbacks)

    def test_cross_enterprise_field_and_unexpected_create_error_roll_back(self):
        db = Session([[], []])
        with self.assertRaises(HTTPException) as caught:
            create(db, user("agronomist", 8, 5), payload(), "request-key-0010")
        self.assertEqual(404, caught.exception.status_code); self.assertEqual(1, db.rollbacks)
        self.assertIn("f.enterprise_id=:eid", db.calls[1][0])
        db = Session([RuntimeError("offline failure")])
        with self.assertRaises(RuntimeError): create(db, user(), payload(), "request-key-0011")
        self.assertEqual(1, db.rollbacks)

    def test_agronomist_defaults_self_and_cannot_assign_another(self):
        db, _ = self.successful(user("agronomist", 8, 5), payload())
        self.assertEqual(8, db.calls[4][1]["assigned"])
        rejected = Session([])
        with self.assertRaises(HTTPException) as caught: create(rejected, user("agronomist", 8, 5), payload(assigned_to_id=9), "request-key-0004")
        self.assertEqual(403, caught.exception.status_code); self.assertEqual(1, rejected.rollbacks)

    def test_idempotent_replay_returns_existing_without_commit(self):
        request = payload(); fp = fingerprint(7, request, None)
        db = Session([[{"id": 11, "request_fingerprint": fp}], [item_row(11)]])
        created, result = create(db, user(), request, "request-key-0005")
        self.assertFalse(created); self.assertEqual(11, result["id"])
        self.assertEqual((0, 0), (db.commits, db.rollbacks))

    def test_same_key_different_payload_and_active_duplicate_return_409(self):
        db = Session([[{"id": 11, "request_fingerprint": "different"}]])
        with self.assertRaises(HTTPException) as caught: create(db, user(), payload(), "request-key-0006")
        self.assertEqual(409, caught.exception.status_code); self.assertEqual(1, db.rollbacks)
        db = Session([[], [{"id": 3, "enterprise_id": 5}], [{"id": 12}]])
        with self.assertRaises(HTTPException) as caught: create(db, user(), payload(), "request-key-0007")
        self.assertEqual(409, caught.exception.status_code); self.assertEqual(1, db.rollbacks)

    def test_integrity_race_classifies_same_key_and_active_field(self):
        request = payload(); fp = fingerprint(7, request, None)
        prefix = [[], [{"id": 3, "enterprise_id": 5}], [], integrity_error()]
        db = Session(prefix + [[{"id": 13, "request_fingerprint": fp}], [item_row(13)]])
        created, result = create(db, user(), request, "request-key-0008")
        self.assertFalse(created); self.assertEqual(13, result["id"]); self.assertEqual(0, db.commits)
        self.assertEqual(1, db.rollbacks)
        db = Session(prefix + [[], [{"id": 14}]])
        with self.assertRaises(HTTPException) as caught: create(db, user(), request, "request-key-0009")
        self.assertEqual(409, caught.exception.status_code); self.assertIn("Active", caught.exception.detail)
        self.assertEqual(1, db.rollbacks)

    def test_expired_actor_is_never_read_after_create_or_integrity_rollback(self):
        actor = ExpiringUser()
        db = Session([[], [{"id": 3, "enterprise_id": 5}], [], [{"id": 11}], [item_row()]], actor=actor)
        self.assertTrue(create(db, actor, payload(), "request-key-0012")[0])

        request = payload(); fp = fingerprint(7, request, None)
        actor = ExpiringUser()
        db = Session([[], [{"id": 3, "enterprise_id": 5}], [], integrity_error(),
                      [{"id": 13, "request_fingerprint": fp}], [item_row(13)]], actor=actor)
        self.assertFalse(create(db, actor, request, "request-key-0013")[0])
        self.assertEqual(1, db.rollbacks)

        actor = ExpiringUser()
        db = Session([[], [{"id": 3, "enterprise_id": 5}], [], integrity_error(), [], [{"id": 14}]], actor=actor)
        with self.assertRaises(HTTPException) as caught:
            create(db, actor, request, "request-key-0014")
        self.assertIn("14", caught.exception.detail)
        self.assertEqual(1, db.rollbacks)


class UpdateAndTransitionTests(unittest.TestCase):
    def test_update_validation_failure_rolls_back(self):
        db = Session([[{"enterprise_id": 5}], []])
        request = UpdateInspectionRequest(expected_version=1, assigned_to_id=99)
        with self.assertRaises(HTTPException) as caught: update(db, user(), 11, request)
        self.assertEqual(422, caught.exception.status_code); self.assertEqual(1, db.rollbacks)

    def test_update_atomic_predicates_conflict_and_success(self):
        request = UpdateInspectionRequest(expected_version=4, title="Updated title")
        db = Session([[], [{"id": 11, "version": 5, "status": "pending"}]])
        with self.assertRaises(HTTPException) as caught: update(db, user("agronomist", 8, 5), 11, request)
        self.assertEqual(409, caught.exception.status_code); self.assertEqual(1, db.rollbacks)
        sql, params = db.calls[0]
        for clause in ("version=:ver", "enterprise_id=:eid", "assigned_to_id=:uid"):
            self.assertIn(clause, sql)
        self.assertEqual({"id": 11, "ver": 4, "uid": 8, "eid": 5, "title": "Updated title"}, params)
        db = Session([[{"id": 11}], [item_row(11, version=5, title="Updated title")]])
        result = update(db, user(), 11, request)
        self.assertEqual("Updated title", result["title"]); self.assertEqual((1, 0), (db.commits, db.rollbacks))

    def test_start_complete_and_cancel_sql_rules(self):
        cases = [
            ("start", TransitionRequest(expected_version=2), "status='pending'", "assigned_to_id IS NOT NULL"),
            ("complete", CompleteInspectionRequest(expected_version=3, completion_summary="Work completed"), "status='in_progress'", "assigned_to_id=:uid"),
            ("cancel", CancelInspectionRequest(expected_version=4, cancellation_reason="No longer needed"), "created_by_id=:uid AND status='pending'", "status IN ('pending','in_progress')"),
        ]
        for action, request, first, second in cases:
            with self.subTest(action=action):
                db = Session([[{"id": 11}], [item_row()]])
                transition(db, user("agronomist", 8, 5), 11, request, action)
                sql, params = db.calls[0]
                self.assertIn(first, sql); self.assertIn(second, sql)
                self.assertEqual(request.expected_version, params["ver"])
                self.assertEqual((1, 0), (db.commits, db.rollbacks))

    def test_illegal_transition_returns_409_and_rolls_back(self):
        db = Session([[], [{"id": 11, "version": 2, "status": "completed"}]])
        with self.assertRaises(HTTPException) as caught:
            transition(db, user(), 11, TransitionRequest(expected_version=1), "start")
        self.assertEqual(409, caught.exception.status_code); self.assertEqual(1, db.rollbacks)

    def test_transition_unexpected_failure_rolls_back(self):
        db = Session([RuntimeError("offline failure")])
        with self.assertRaises(RuntimeError):
            transition(db, user(), 11, TransitionRequest(expected_version=1), "start")
        self.assertEqual((0, 1), (db.commits, db.rollbacks))

    def test_expired_actor_is_never_read_after_update_and_all_transitions(self):
        update_actor = ExpiringUser()
        db = Session([[{"id": 11}], [item_row(title="Updated title")]], actor=update_actor)
        update(db, update_actor, 11, UpdateInspectionRequest(expected_version=1, title="Updated title"))
        self.assertEqual(1, db.commits)

        cases = [
            ("start", TransitionRequest(expected_version=1)),
            ("complete", CompleteInspectionRequest(expected_version=1, completion_summary="Work completed")),
            ("cancel", CancelInspectionRequest(expected_version=1, cancellation_reason="No longer needed")),
        ]
        for action, request in cases:
            with self.subTest(action=action):
                actor = ExpiringUser()
                db = Session([[{"id": 11}], [item_row()]], actor=actor)
                transition(db, actor, 11, request, action)
                self.assertEqual((1, 0), (db.commits, db.rollbacks))


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
