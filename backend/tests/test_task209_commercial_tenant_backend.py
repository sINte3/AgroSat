"""Authorization, namespace, secret-redaction, and lifecycle tests."""

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from pydantic import ValidationError

from schemas.commercial_tenant import (
    CommercialProfileUpdate,
    LifecycleDecision,
    LifecycleRequestCreate,
    ProviderCredentialReferenceUpdate,
)
from services import commercial_tenant as service


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class Session:
    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        if not self.outcomes:
            raise AssertionError("unexpected SQL")
        return Result(self.outcomes.pop(0))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def user(role="manager", enterprise=5):
    return SimpleNamespace(role=role, id=7, enterprise_id=enterprise)


def enterprise():
    return {"id": 5, "name": "Fixture Tenant", "code": "fixture", "is_active": True}


def profile_payload():
    return CommercialProfileUpdate(
        plan_code="industrial",
        subscription_state="active",
        feature_flags={"pixel_anomalies": True},
        quota_limits={"fields": 1000, "users": 50},
        retention_policy={"audit_days": 2555},
        branding={"display_name": "Fixture Tenant", "primary_color": "#2563eb"},
    )


def lifecycle_row(**changes):
    value = {
        "id": 91,
        "enterprise_id": 5,
        "request_type": "export",
        "status": "requested",
        "requested_by_id": 7,
        "reviewed_by_id": None,
        "reviewed_at": None,
        "reason": "Annual tenant portability review",
        "decision_note": None,
        "created_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
        "result_reference": None,
        "failure_category": None,
    }
    value.update(changes)
    return value


def test_namespaces_partition_storage_cache_jobs_and_exports_without_names():
    assert service.tenant_namespaces(42) == {
        "storage": "tenants/42/",
        "cache": "agrosat:tenant:42:",
        "jobs": "tenant.42.",
        "exports": "tenant-42/",
    }
    with pytest.raises(ValueError):
        service.tenant_namespaces(0)


def test_profile_schema_rejects_negative_quota_unknown_branding_and_extra_data():
    assert profile_payload().quota_limits["fields"] == 1000
    with pytest.raises(ValidationError):
        CommercialProfileUpdate(
            **{
                **profile_payload().model_dump(),
                "quota_limits": {"fields": -1},
            }
        )
    with pytest.raises(ValidationError):
        CommercialProfileUpdate(
            **{
                **profile_payload().model_dump(),
                "branding": {"script": "unsafe"},
            }
        )


def test_cross_tenant_boundary_is_hidden_before_sql():
    db = Session()
    with pytest.raises(HTTPException) as exc:
        service.get_boundary(db, user(enterprise=6), 5)
    assert exc.value.status_code == 404
    assert not db.calls


def test_boundary_is_bounded_and_never_returns_credential_reference():
    profile = {
        "plan_code": "industrial",
        "subscription_state": "active",
        "feature_flags": {"pixel_anomalies": True},
        "quota_limits": {"fields": 1000},
        "retention_policy": {"audit_days": 2555},
        "branding": {"display_name": "Fixture Tenant"},
        "created_at": None,
        "updated_at": None,
    }
    provider = {
        "provider_code": "wialon",
        "status": "configured",
        "last_validated_at": None,
        "last_failure_category": None,
    }
    db = Session([[enterprise()], [profile], [provider]])
    value = service.get_boundary(db, user(), 5)
    assert value["providers"] == [{
        "provider_code": "wialon",
        "configured": True,
        "status": "configured",
        "last_validated_at": None,
        "last_failure_category": None,
    }]
    assert "secret_reference" not in repr(value)
    assert value["billing"]["payment_processing"] is False
    assert "LIMIT 100" in db.calls[2][0]


def test_profile_update_is_admin_only_and_audited():
    blocked = Session()
    with pytest.raises(HTTPException) as exc:
        service.update_profile(blocked, user(), 5, profile_payload())
    assert exc.value.status_code == 403
    assert not blocked.calls

    profile = {
        **profile_payload().model_dump(mode="json"),
        "created_at": None,
        "updated_at": None,
    }
    db = Session([[enterprise()], [], [], [enterprise()], [profile], []])
    value = service.update_profile(db, user("admin", None), 5, profile_payload())
    assert value["profile"]["plan_code"] == "industrial"
    assert db.commits == 1
    assert "ON CONFLICT (enterprise_id)" in db.calls[1][0]
    assert "INSERT INTO tenant_commercial_audit_events" in db.calls[2][0]
    assert db.calls[1][1]["namespaces"] == (
        '{"cache":"agrosat:tenant:5:","exports":"tenant-5/",'
        '"jobs":"tenant.5.","storage":"tenants/5/"}'
    )


def test_membership_list_is_same_tenant_bounded_explicit_join():
    row = {
        "id": 1,
        "user_id": 7,
        "membership_role": "manager",
        "status": "active",
        "created_at": None,
        "full_name": "Fixture Manager",
        "is_active": True,
    }
    db = Session([[enterprise()], [row]])
    result = service.list_memberships(db, user(), 5, 50, 0)
    assert result["items"][0]["membership_role"] == "manager"
    assert "JOIN users" in db.calls[1][0]
    assert "LIMIT :limit OFFSET :offset" in db.calls[1][0]


def test_provider_reference_is_validated_stored_server_side_and_redacted():
    payload = ProviderCredentialReferenceUpdate(
        secret_reference="vault/agrosat/tenant-5/wialon",
        status="configured",
    )
    db = Session([[enterprise()], [], []])
    value = service.configure_provider(
        db, user("admin", None), 5, "wialon", payload
    )
    assert value == {
        "provider_code": "wialon",
        "configured": True,
        "status": "configured",
    }
    assert "secret_reference" not in value
    assert db.commits == 1
    assert db.calls[1][1]["secret_reference"].startswith("vault/")
    assert "secret_reference" not in db.calls[2][1]["details"]


def test_manager_may_request_export_but_not_deletion():
    deletion = LifecycleRequestCreate(
        request_type="deletion",
        reason="Tenant closure requested by authorized manager",
    )
    blocked = Session()
    with pytest.raises(HTTPException) as exc:
        service.create_lifecycle_request(
            blocked, user(), 5, deletion, "request-0001"
        )
    assert exc.value.status_code == 403
    assert not blocked.calls

    payload = LifecycleRequestCreate(
        request_type="export",
        reason="Annual tenant portability review",
    )
    db = Session([[enterprise()], [], [{"id": 91}], [], [lifecycle_row()]])
    created, item = service.create_lifecycle_request(
        db, user(), 5, payload, "request-0001"
    )
    assert created is True and item["status"] == "requested"
    assert db.commits == 1
    assert "INSERT INTO tenant_lifecycle_requests" in db.calls[2][0]
    assert "INSERT INTO tenant_commercial_audit_events" in db.calls[3][0]


def test_lifecycle_idempotency_conflict_and_admin_decision():
    payload = LifecycleRequestCreate(
        request_type="export",
        reason="Annual tenant portability review",
    )
    fingerprint = service._request_fingerprint(7, payload)
    replay = Session([
        [enterprise()],
        [{"id": 91, "request_fingerprint": fingerprint}],
        [lifecycle_row()],
    ])
    created, item = service.create_lifecycle_request(
        replay, user(), 5, payload, "request-0001"
    )
    assert created is False and item["id"] == 91

    conflict = Session([
        [enterprise()],
        [{"id": 91, "request_fingerprint": "f" * 64}],
    ])
    with pytest.raises(HTTPException) as exc:
        service.create_lifecycle_request(
            conflict, user(), 5, payload, "request-0001"
        )
    assert exc.value.status_code == 409 and conflict.rollbacks == 1

    decision = LifecycleDecision(
        decision="approved", confirm=True, note="Scope reviewed by administrator"
    )
    decided = lifecycle_row(
        status="approved",
        reviewed_by_id=7,
        reviewed_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        decision_note="Scope reviewed by administrator",
    )
    db = Session([[{"id": 91}], [], [decided]])
    item = service.decide_lifecycle_request(
        db, user("admin", None), 5, 91, decision
    )
    assert item["status"] == "approved"
    assert "status='requested'" in db.calls[0][0]
    assert db.commits == 1


def test_service_has_no_lazy_loading_payment_operation_or_web_scheduler():
    source = open(service.__file__, encoding="utf-8").read()
    assert "relationship(" not in source
    assert "lazy=" not in source
    assert "APScheduler" not in source
    for forbidden in ("charge_card", "create_payment", "issue_refund"):
        assert forbidden not in source
