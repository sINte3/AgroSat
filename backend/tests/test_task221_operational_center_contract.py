"""Focused source contracts for the TASK_221 operational read model."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from database import Base
import models.registry  # noqa: F401  Registers all source-owned tables.
from services import operational_center as service
from services.telematics import UnsupportedTelematicsProvider, read_field_telematics


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text("utf-8")


def user(role="manager", enterprise_id=7, user_id=31):
    return SimpleNamespace(role=role, enterprise_id=enterprise_id, id=user_id)


def test_single_next_migration_has_only_g_owned_entities_and_safe_downgrade():
    migration = read("alembic/versions/0016_operational_command_center.py")
    assert 'revision = "0016_operational_command_center"' in migration
    assert 'down_revision = "0015_closed_loop_agronomy"' in migration
    assert migration.count("op.create_table(") == 2
    assert '"operational_notifications"' in migration
    assert '"operational_notification_events"' in migration
    assert "requires backup restore" in migration
    assert "planned_field_operations" not in migration
    assert "actual_executions" not in migration


def test_metadata_registers_tenant_constraints_without_relationships():
    notification = Base.metadata.tables["operational_notifications"]
    events = Base.metadata.tables["operational_notification_events"]
    assert {"enterprise_id", "recipient_user_id", "case_key", "dedupe_key"} <= set(notification.c.keys())
    assert {"notification_id", "enterprise_id", "actor_key", "command_key"} <= set(events.c.keys())
    assert "uq_operational_notifications_dedupe" in {item.name for item in notification.constraints}
    assert "uq_operational_notification_events_command" in {item.name for item in events.constraints}
    assert not hasattr(notification, "relationships")


def test_router_is_authenticated_typed_and_main_has_no_web_scheduler():
    api = read("api/operational_center.py")
    main = read("main.py")
    assert 'prefix="/api/operational-center"' in api
    assert "get_current_active_user" in api
    assert "response_model=QueueResponse" in api
    assert 'alias="Idempotency-Key"' in api
    assert "operational_center_router" in main
    assert "APScheduler" not in main


@pytest.mark.parametrize(
    "case_key",
    [
        "inspection:1",
        "candidate:22",
        "alert:3",
        "freshness:4:ndvi",
        "external:7:123e4567-e89b-12d3-a456-426614174000",
    ],
)
def test_case_identity_allowlist(case_key):
    assert service.CASE_KEY.fullmatch(case_key)


@pytest.mark.parametrize(
    "case_key",
    ["inspection:0", "inspection:-1", "freshness:4:foo", "external:7:token", "x:1", "1 OR 1=1"],
)
def test_case_identity_rejects_ambiguous_or_unbounded_values(case_key):
    assert not service.CASE_KEY.fullmatch(case_key)


def test_tenant_and_assignment_scope_is_always_server_owned():
    actor = service._actor(user("agronomist", 7, 31))
    conditions, params = service._scope_conditions(actor, {"enterprise_id": 7})
    assert "c.enterprise_id=:actor_enterprise_id" in conditions
    assert any(":actor_user_id" in condition for condition in conditions)
    assert params["actor_enterprise_id"] == 7
    with pytest.raises(HTTPException) as error:
        service._scope_conditions(actor, {"enterprise_id": 8})
    assert error.value.status_code == 404


def test_viewer_cannot_mutate_notification_state():
    with pytest.raises(HTTPException) as error:
        service._actor(user("viewer"), write=True)
    assert error.value.status_code == 403


def test_read_model_contains_linked_source_deduplication_and_external_failure_case():
    sql = service.CASES_CTE
    assert "active_inspection" in sql
    assert "active_candidate" in sql
    assert "latest_collection_run" in sql
    assert "UNION ALL SELECT * FROM external_cases" in sql
    assert "LIMIT 1" in sql
    assert "LEFT JOIN LATERAL" in sql


def test_wialon_degraded_mode_performs_no_provider_call():
    class FailIfCalled(UnsupportedTelematicsProvider):
        def read(self, *args, **kwargs):  # pragma: no cover - a call is the failure.
            raise AssertionError("provider must not be called without a server mapping")

    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    result = read_field_telematics(
        FailIfCalled(),
        None,
        started_at=now - timedelta(hours=24),
        ended_at=now,
    )
    assert result == {
        "status": "unsupported",
        "provider": "wialon",
        "reason": "mapping_unavailable",
        "units": [],
    }


def test_queue_priority_reasons_are_explicit_not_an_opaque_score():
    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    item = {
        "is_overdue": True,
        "priority_rank": 0,
        "due_at": now,
        "operational_status": "awaiting_verification",
        "external_state": None,
        "blocked": False,
    }
    reasons = service._priority_reasons(item, now)
    assert reasons == ["overdue_work", "critical_source", "awaiting_satellite_observation"]
    assert "score" not in service.CASES_CTE.lower()
