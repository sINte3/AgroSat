"""Telegram alert tenant-scope regression tests for TASK_209."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from api.telegram import send_alert_notification
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


def user(role, enterprise_id, user_id=7):
    return SimpleNamespace(role=role, enterprise_id=enterprise_id, id=user_id)


@pytest.mark.parametrize("role", ["manager", "agronomist"])
@patch("api.telegram._enforce_telegram_enabled")
def test_tenant_mutating_roles_scope_alert_query(enabled, role):
    db = RecordingSession()
    with pytest.raises(HTTPException) as exc:
        send_alert_notification(
            payload={"alert_id": 31},
            db=db,
            current_user=user(role, 17),
        )

    assert exc.value.status_code == 404
    statement, params = db.calls[0]
    assert "AND f.enterprise_id = :eid" in statement
    assert params == {"aid": 31, "eid": 17}
    enabled.assert_called_once_with()


def test_manager_without_enterprise_fails_before_query_or_provider_check():
    db = RecordingSession()
    with patch("api.telegram._enforce_telegram_enabled") as enabled:
        with pytest.raises(HTTPException) as exc:
            send_alert_notification(
                payload={"alert_id": 31},
                db=db,
                current_user=user("manager", None),
            )

    assert exc.value.status_code == 403
    assert db.calls == []
    enabled.assert_not_called()


def test_viewer_enum_role_cannot_send_alert():
    db = RecordingSession()
    with patch("api.telegram._enforce_telegram_enabled") as enabled:
        with pytest.raises(HTTPException) as exc:
            send_alert_notification(
                payload={"alert_id": 31},
                db=db,
                current_user=user(UserRole.VIEWER, 17),
            )

    assert exc.value.status_code == 403
    assert db.calls == []
    enabled.assert_not_called()


@patch("api.telegram._enforce_telegram_enabled")
def test_admin_alert_query_remains_global(enabled):
    db = RecordingSession()
    with pytest.raises(HTTPException) as exc:
        send_alert_notification(
            payload={"alert_id": 31},
            db=db,
            current_user=user("admin", None),
        )

    assert exc.value.status_code == 404
    statement, params = db.calls[0]
    assert "AND f.enterprise_id = :eid" not in statement
    assert params == {"aid": 31}
    enabled.assert_called_once_with()
