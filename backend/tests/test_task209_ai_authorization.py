"""AI recommendation tenant-scope regression tests for TASK_209."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.ai import get_ai_recommendation
from models.monitoring import UserRole


class EmptyResult:
    @staticmethod
    def fetchone():
        return None


class RecordingSession:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return EmptyResult()


def user(role, enterprise_id):
    return SimpleNamespace(role=role, enterprise_id=enterprise_id)


@pytest.mark.parametrize("role", ["manager", "agronomist"])
def test_tenant_mutating_roles_scope_context_query(role):
    db = RecordingSession()
    with pytest.raises(HTTPException) as exc:
        get_ai_recommendation(
            payload={"alert_id": 31, "field_id": 4},
            db=db,
            current_user=user(role, 17),
        )

    assert exc.value.status_code == 404
    statement, params = db.calls[0]
    assert "AND f.enterprise_id = :eid" in statement
    assert params["eid"] == 17


def test_manager_without_enterprise_fails_before_query():
    db = RecordingSession()
    with pytest.raises(HTTPException) as exc:
        get_ai_recommendation(
            payload={"alert_id": 31, "field_id": 4},
            db=db,
            current_user=user("manager", None),
        )

    assert exc.value.status_code == 403
    assert db.calls == []


def test_viewer_enum_role_cannot_generate_recommendation():
    db = RecordingSession()
    with pytest.raises(HTTPException) as exc:
        get_ai_recommendation(
            payload={"alert_id": 31, "field_id": 4},
            db=db,
            current_user=user(UserRole.VIEWER, 17),
        )

    assert exc.value.status_code == 403
    assert db.calls == []


def test_admin_context_query_remains_global():
    db = RecordingSession()
    with pytest.raises(HTTPException) as exc:
        get_ai_recommendation(
            payload={"alert_id": 31, "field_id": 4},
            db=db,
            current_user=user("admin", None),
        )

    assert exc.value.status_code == 404
    statement, params = db.calls[0]
    assert "AND f.enterprise_id = :eid" not in statement
    assert "eid" not in params
