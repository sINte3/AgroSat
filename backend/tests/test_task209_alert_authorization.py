"""Alert mutation tenant-scope regression tests for TASK_209."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from api.alerts import acknowledge_alert
from models.monitoring import UserRole


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class RecordingSession:
    def __init__(self, row=None):
        self.calls = []
        self.commits = 0
        self.row = row

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return Result(self.row)

    def commit(self):
        self.commits += 1


def user(role, enterprise_id, user_id=7):
    return SimpleNamespace(role=role, enterprise_id=enterprise_id, id=user_id)


@patch("api.alerts.cache_delete_pattern")
def test_manager_acknowledge_is_scoped_inside_update(cache_delete):
    db = RecordingSession(row=SimpleNamespace(id=31, field_id=4))
    result = acknowledge_alert(
        alert_id=31,
        db=db,
        current_user=user("manager", 17),
    )

    assert result == {"status": "ok", "alert_id": 31}
    statement, params = db.calls[0]
    assert "AND f.enterprise_id = :eid" in statement
    assert params["eid"] == 17
    assert db.commits == 1
    assert cache_delete.call_count == 3


def test_manager_cross_tenant_alert_is_non_enumerable_404():
    db = RecordingSession(row=None)
    with pytest.raises(HTTPException) as exc:
        acknowledge_alert(
            alert_id=31,
            db=db,
            current_user=user("manager", 17),
        )

    assert exc.value.status_code == 404
    statement, params = db.calls[0]
    assert "AND f.enterprise_id = :eid" in statement
    assert params["eid"] == 17
    assert db.commits == 0


def test_viewer_enum_role_cannot_acknowledge():
    db = RecordingSession(row=SimpleNamespace(id=31, field_id=4))
    with pytest.raises(HTTPException) as exc:
        acknowledge_alert(
            alert_id=31,
            db=db,
            current_user=user(UserRole.VIEWER, 17),
        )

    assert exc.value.status_code == 403
    assert db.calls == []
    assert db.commits == 0


@patch("api.alerts.cache_delete_pattern")
def test_admin_acknowledge_remains_global(cache_delete):
    db = RecordingSession(row=SimpleNamespace(id=31, field_id=4))
    acknowledge_alert(
        alert_id=31,
        db=db,
        current_user=user("admin", None),
    )

    statement, params = db.calls[0]
    assert "AND f.enterprise_id = :eid" not in statement
    assert "eid" not in params
    assert db.commits == 1
    assert cache_delete.call_count == 3
