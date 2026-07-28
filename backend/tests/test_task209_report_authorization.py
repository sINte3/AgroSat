"""Report export tenant-scope regression tests for TASK_209."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.reports import download_enterprise_pdf
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


@pytest.mark.parametrize("role", ["manager", "agronomist", UserRole.VIEWER])
def test_tenant_roles_scope_enterprise_export_lookup(role):
    db = RecordingSession()
    with pytest.raises(HTTPException) as exc:
        download_enterprise_pdf(
            enterprise_id=99,
            db=db,
            current_user=user(role, 17),
        )

    assert exc.value.status_code == 404
    statement, params = db.calls[0]
    assert "WHERE id = :eid AND id = :scope_eid" in statement
    assert params == {"eid": 99, "scope_eid": 17}


def test_manager_without_enterprise_fails_before_export_query():
    db = RecordingSession()
    with pytest.raises(HTTPException) as exc:
        download_enterprise_pdf(
            enterprise_id=99,
            db=db,
            current_user=user("manager", None),
        )

    assert exc.value.status_code == 403
    assert db.calls == []


def test_admin_enterprise_export_lookup_remains_global():
    db = RecordingSession()
    with pytest.raises(HTTPException) as exc:
        download_enterprise_pdf(
            enterprise_id=99,
            db=db,
            current_user=user("admin", None),
        )

    assert exc.value.status_code == 404
    statement, params = db.calls[0]
    assert "scope_eid" not in statement
    assert params == {"eid": 99}
