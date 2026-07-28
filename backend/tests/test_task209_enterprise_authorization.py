"""Enterprise API tenant-scope regression tests for TASK_209."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.enterprises import get_enterprise, list_enterprises


class EmptyResult:
    @staticmethod
    def fetchone():
        return None

    @staticmethod
    def fetchall():
        return []


class RecordingSession:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return EmptyResult()


def user(role, enterprise_id):
    return SimpleNamespace(role=role, enterprise_id=enterprise_id)


def test_manager_enterprise_list_is_scoped_inside_query():
    db = RecordingSession()
    result = asyncio.run(list_enterprises(db=db, current_user=user("manager", 17)))
    assert result == []
    statement, params = db.calls[0]
    assert "WHERE e.id = :eid" in statement
    assert params == {"eid": 17}


def test_manager_cross_tenant_enterprise_detail_is_non_enumerable_404():
    db = RecordingSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            get_enterprise(
                enterprise_id=99,
                db=db,
                current_user=user("manager", 17),
            )
        )
    assert exc.value.status_code == 404
    statement, params = db.calls[0]
    assert "WHERE id = :id AND id = :eid" in statement
    assert params == {"id": 99, "eid": 17}


def test_admin_enterprise_detail_remains_global():
    db = RecordingSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            get_enterprise(
                enterprise_id=99,
                db=db,
                current_user=user("admin", None),
            )
        )
    assert exc.value.status_code == 404
    statement, params = db.calls[0]
    assert "AND id = :eid" not in statement
    assert params == {"id": 99}
