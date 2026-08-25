"""TASK_209 read-only route, role, tenant, and boundedness contract matrix."""

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from main import app
from api.auth import get_current_active_user
from api.dependencies import (
    ALLOWED_ROLES,
    GLOBAL_ROLES,
    TENANT_ROLES,
    get_authorized_field_row,
    require_enterprise_scope,
)


ROLES = frozenset({"admin", "manager", "agronomist", "viewer"})
MUTATING_ROLES = frozenset({"admin", "manager", "agronomist"})
MANAGEMENT_ROLES = frozenset({"admin", "manager"})
PUBLIC = frozenset()


@dataclass(frozen=True)
class Contract:
    method: str
    path: str
    authenticated: bool
    roles: frozenset[str]
    tenant_scope: str
    write: bool = False
    cross_tenant: int | None = None
    unknown_object: int | None = None


def contract(
    method,
    path,
    roles=ROLES,
    tenant_scope="none",
    *,
    authenticated=True,
    write=False,
    cross_tenant=None,
    unknown_object=None,
):
    return Contract(
        method,
        path,
        authenticated,
        roles,
        tenant_scope,
        write,
        cross_tenant,
        unknown_object,
    )


MATRIX = (
    contract("GET", "/", PUBLIC, authenticated=False),
    contract("GET", "/health", PUBLIC, authenticated=False),
    contract("GET", "/health/live", PUBLIC, authenticated=False),
    contract("GET", "/health/ready", PUBLIC, authenticated=False),
    contract("POST", "/api/auth/login", PUBLIC, authenticated=False),
    contract("POST", "/api/auth/register", PUBLIC, authenticated=False, write=True),
    contract("GET", "/api/auth/me"),
    contract("GET", "/api/dashboard/summary", tenant_scope="enterprise"),
    contract("GET", "/api/enterprises/", tenant_scope="enterprise"),
    contract("GET", "/api/enterprises/{enterprise_id}", tenant_scope="enterprise_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/fields/", tenant_scope="enterprise_filter", cross_tenant=403),
    contract("GET", "/api/fields/geojson/all", tenant_scope="enterprise_filter", cross_tenant=403),
    contract("GET", "/api/field-tiles/metadata", tenant_scope="enterprise_filter", cross_tenant=403),
    contract("GET", "/api/field-tiles/{z}/{x}/{y}.mvt", tenant_scope="enterprise_filter", cross_tenant=403),
    contract("GET", "/api/fields/{field_id}", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/fields/{field_id}/geojson", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("POST", "/api/fields/", MUTATING_ROLES, "enterprise", write=True, cross_tenant=403),
    contract("PUT", "/api/fields/{field_id}", MUTATING_ROLES, "field_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/fields/{field_id}/season", MUTATING_ROLES, "field_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/ndvi/{field_id}/history", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/ndvi/{field_id}/latest", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("POST", "/api/ndvi/{field_id}/refresh", MUTATING_ROLES, "field_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/alerts/", tenant_scope="enterprise_filter", cross_tenant=403),
    contract("GET", "/api/alerts/{field_id}", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("PUT", "/api/alerts/{alert_id}/acknowledge", MUTATING_ROLES, "alert_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/weather/field/{field_id}", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/weather/location", MANAGEMENT_ROLES),
    contract("POST", "/api/ai/recommend", MUTATING_ROLES, "field_alert_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/telegram/status"),
    contract("POST", "/api/telegram/test", MANAGEMENT_ROLES, write=True),
    contract("POST", "/api/telegram/send-alert", MUTATING_ROLES, "alert_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/reports/management/summary", tenant_scope="enterprise"),
    contract("GET", "/api/reports/management/satellite-indices/summary", tenant_scope="enterprise"),
    contract("GET", "/api/reports/management/pdf", tenant_scope="enterprise"),
    contract("GET", "/api/reports/management/excel", tenant_scope="enterprise"),
    contract("GET", "/api/reports/enterprise/{enterprise_id}/pdf", tenant_scope="enterprise_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/satellite-indices/coverage", tenant_scope="enterprise_filter", cross_tenant=403),
    contract("GET", "/api/satellite-indices/{field_id}/latest", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/satellite-indices/{field_id}/history", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/satellite-data-quality/summary", tenant_scope="enterprise"),
    contract("GET", "/api/agronomic-risk/summary", tenant_scope="enterprise"),
    contract("GET", "/api/agronomic-risk/alert-candidates", tenant_scope="enterprise"),
    contract("GET", "/api/agronomic-interpretation/fields/{field_id}", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/ndvi-raster/fields/{field_id}/metadata", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/ndvi-raster/fields/{field_id}/image", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/raster/fields/{field_id}/metadata", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/raster/fields/{field_id}/image", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/raster/fields/{field_id}/scenes", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/raster/fields/{field_id}/workspace", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/raster/fields/{field_id}/pixel-image", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/raster/fields/{field_id}/sample", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/field-attention/queue", tenant_scope="enterprise_filter", cross_tenant=403),
    contract("GET", "/api/field-inspections", tenant_scope="enterprise_filter", cross_tenant=403),
    contract("POST", "/api/field-inspections", MUTATING_ROLES, "field_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/field-inspections/{inspection_id}", tenant_scope="inspection_object", cross_tenant=404, unknown_object=404),
    contract("PATCH", "/api/field-inspections/{inspection_id}", MUTATING_ROLES, "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/field-inspections/{inspection_id}/start", MUTATING_ROLES, "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/field-inspections/{inspection_id}/complete", MUTATING_ROLES, "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/field-inspections/{inspection_id}/cancel", MUTATING_ROLES, "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/operations/metrics", MANAGEMENT_ROLES),
    contract("POST", "/api/field-inspections/{inspection_id}/result", MUTATING_ROLES, "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/field-inspections/{inspection_id}/evidence", MUTATING_ROLES, "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/field-inspections/{inspection_id}/actions", MUTATING_ROLES, "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/field-inspections/{inspection_id}/timeline", tenant_scope="inspection_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/operational-actions", tenant_scope="enterprise_filter", cross_tenant=403),
    contract("PATCH", "/api/operational-actions/{action_id}", MUTATING_ROLES, "action_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/operational-actions/{action_id}/close", MUTATING_ROLES, "action_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/operational-actions/{action_id}/reopen", MANAGEMENT_ROLES, "action_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/operational-actions/{action_id}/verification-requests", MANAGEMENT_ROLES, "action_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/verification-requests/{verification_id}/resolve", MANAGEMENT_ROLES, "verification_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/field-inspections/{inspection_id}/closure", tenant_scope="inspection_object", cross_tenant=404, unknown_object=404),
    contract("POST", "/api/anomaly-inspections", MANAGEMENT_ROLES, "field_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/anomaly-inspections/queue", tenant_scope="enterprise_filter", cross_tenant=404),
    contract("GET", "/api/anomaly-inspections/assignees", MANAGEMENT_ROLES, "enterprise_filter", cross_tenant=404),
    contract("GET", "/api/anomaly-inspections/fields/{field_id}/timeline", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/anomaly-inspections/{inspection_id}", tenant_scope="inspection_object", cross_tenant=404, unknown_object=404),
    contract("POST", "/api/anomaly-inspections/{inspection_id}/assignment", MANAGEMENT_ROLES, "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/anomaly-inspections/{inspection_id}/start", frozenset({"agronomist"}), "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("PUT", "/api/anomaly-inspections/{inspection_id}/finding", frozenset({"agronomist"}), "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/anomaly-inspections/{inspection_id}/submit", frozenset({"agronomist"}), "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/anomaly-inspections/{inspection_id}/review", MANAGEMENT_ROLES, "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/anomaly-inspections/{inspection_id}/cancel", MANAGEMENT_ROLES, "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/anomaly-inspections/{inspection_id}/photos", frozenset({"agronomist"}), "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/anomaly-inspections/{inspection_id}/photos/{photo_id}", tenant_scope="inspection_object", cross_tenant=404, unknown_object=404),
    contract("DELETE", "/api/anomaly-inspections/{inspection_id}/photos/{photo_id}", MUTATING_ROLES, "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/anomaly-inspections/{inspection_id}/actions", MANAGEMENT_ROLES, "inspection_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/anomaly-inspections/actions/{action_id}/transition", MUTATING_ROLES, "action_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/anomaly-inspections/actions/{action_id}/verify", MANAGEMENT_ROLES, "action_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/executive/overview", MANAGEMENT_ROLES, tenant_scope="enterprise_filter", cross_tenant=403),
    contract("GET", "/api/executive/accountability", MANAGEMENT_ROLES, tenant_scope="enterprise_filter", cross_tenant=403),
    contract("GET", "/api/executive/export.xlsx", MANAGEMENT_ROLES, tenant_scope="enterprise_filter", cross_tenant=403),
    contract("GET", "/api/pixel-anomalies/fields/{field_id}/summary", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/pixel-anomalies", tenant_scope="enterprise_filter", cross_tenant=403),
    contract("GET", "/api/pixel-anomalies/{anomaly_id}", tenant_scope="anomaly_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/pixel-anomalies/{anomaly_id}/geometry", tenant_scope="anomaly_object", cross_tenant=404, unknown_object=404),
    contract("POST", "/api/pixel-anomalies/{anomaly_id}/inspection", MUTATING_ROLES, "anomaly_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/telematics/fields/{field_id}", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/irrigation-context/fields/{field_id}", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("POST", "/api/irrigation-context/fields/{field_id}/events", MUTATING_ROLES, "field_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/yield-map-imports/preview", MUTATING_ROLES, "field_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/yield-map-imports", MUTATING_ROLES, "field_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/yield-map-imports", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/yield-map-imports/{import_id}", tenant_scope="yield_import_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/yield-map-imports/{import_id}/points", tenant_scope="yield_import_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/productivity-zones/fields/{field_id}", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/productivity-zones/runs/{run_id}", tenant_scope="productivity_run_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/productivity-zones/runs/{run_id}/zones", tenant_scope="productivity_run_object", cross_tenant=404, unknown_object=404),
    contract("POST", "/api/variable-rate-recommendations", MUTATING_ROLES, "field_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/variable-rate-recommendations", tenant_scope="field_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/variable-rate-recommendations/{recommendation_id}", tenant_scope="variable_rate_object", cross_tenant=404, unknown_object=404),
    contract("POST", "/api/variable-rate-recommendations/{recommendation_id}/approve", MANAGEMENT_ROLES, "variable_rate_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/variable-rate-recommendations/{recommendation_id}/reject", MANAGEMENT_ROLES, "variable_rate_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/variable-rate-recommendations/{recommendation_id}/export.geojson", tenant_scope="variable_rate_object", cross_tenant=404, unknown_object=404),
    contract("GET", "/api/commercial/tenants/{enterprise_id}", tenant_scope="enterprise_object", cross_tenant=404, unknown_object=404),
    contract("PUT", "/api/commercial/tenants/{enterprise_id}", GLOBAL_ROLES, "enterprise_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/commercial/tenants/{enterprise_id}/memberships", MANAGEMENT_ROLES, "enterprise_object", cross_tenant=404, unknown_object=404),
    contract("PUT", "/api/commercial/tenants/{enterprise_id}/providers/{provider_code}", GLOBAL_ROLES, "enterprise_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/commercial/tenants/{enterprise_id}/lifecycle-requests", MANAGEMENT_ROLES, "enterprise_object", cross_tenant=404, unknown_object=404),
    contract("POST", "/api/commercial/tenants/{enterprise_id}/lifecycle-requests", MANAGEMENT_ROLES, "enterprise_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/commercial/tenants/{enterprise_id}/lifecycle-requests/{request_id}/decision", GLOBAL_ROLES, "enterprise_object", write=True, cross_tenant=404, unknown_object=404),
    contract("GET", "/api/monitoring/status", GLOBAL_ROLES, tenant_scope="global_operations"),
    contract("GET", "/api/monitoring/freshness", tenant_scope="enterprise_filter", cross_tenant=404),
    contract("GET", "/api/monitoring/candidates", tenant_scope="enterprise_filter", cross_tenant=404),
    contract("POST", "/api/monitoring/candidates/{candidate_id}/transition", GLOBAL_ROLES, "candidate_object", write=True, cross_tenant=404, unknown_object=404),
    contract("POST", "/api/monitoring/candidates/{candidate_id}/inspection", GLOBAL_ROLES, "candidate_object", write=True, cross_tenant=404, unknown_object=404),
)


def openapi_operations():
    operations = {}
    for path, path_item in app.openapi()["paths"].items():
        for method, operation in path_item.items():
            if method in {"get", "post", "put", "patch", "delete"}:
                operations[(method.upper(), path)] = operation
    return operations


def test_matrix_covers_every_openapi_operation_exactly():
    expected = {(item.method, item.path) for item in MATRIX}
    assert len(expected) == len(MATRIX) == 125
    assert expected == set(openapi_operations())


def test_openapi_security_matches_authentication_contract():
    operations = openapi_operations()
    for item in MATRIX:
        security = operations[(item.method, item.path)].get("security")
        if item.authenticated:
            assert security == [{"OAuth2PasswordBearer": []}], (item.method, item.path)
        else:
            assert not security, (item.method, item.path)


def test_role_contract_has_no_unknown_or_empty_protected_role_set():
    for item in MATRIX:
        assert item.roles <= ROLES
        if item.authenticated:
            assert item.roles


def test_viewer_is_excluded_from_every_protected_mutation():
    for item in MATRIX:
        if item.authenticated and item.write:
            assert "viewer" not in item.roles, (item.method, item.path)


def test_object_contracts_hide_cross_tenant_existence():
    object_scopes = {
        "enterprise_object",
        "field_object",
        "field_alert_object",
        "alert_object",
        "inspection_object",
        "action_object",
        "verification_object",
        "anomaly_object",
        "productivity_run_object",
        "variable_rate_object",
        "candidate_object",
    }
    for item in MATRIX:
        if item.tenant_scope in object_scopes:
            assert item.cross_tenant == 404, (item.method, item.path)
            assert item.unknown_object == 404, (item.method, item.path)


# Read-only audit debt. Regressions must be added here rather than hidden.
UNBOUNDED_ACCEPTANCE_DEBT = frozenset()


def test_unbounded_debt_is_explicit_and_does_not_expand():
    assert not UNBOUNDED_ACCEPTANCE_DEBT


def test_shared_role_policy_has_one_global_role_and_three_tenant_roles():
    assert ALLOWED_ROLES == ROLES
    assert GLOBAL_ROLES == {"admin"}
    assert TENANT_ROLES == {"manager", "agronomist", "viewer"}


def test_manager_requires_and_returns_assigned_enterprise_scope():
    manager = SimpleNamespace(role="manager", enterprise_id=17)
    assert require_enterprise_scope(manager) == 17

    with pytest.raises(HTTPException) as exc:
        require_enterprise_scope(SimpleNamespace(role="manager", enterprise_id=None))
    assert exc.value.status_code == 403


def test_manager_field_object_query_contains_tenant_predicate():
    class MissingResult:
        @staticmethod
        def fetchone():
            return None

    class RecordingSession:
        def __init__(self):
            self.statement = None
            self.params = None

        def execute(self, statement, params):
            self.statement = str(statement)
            self.params = params
            return MissingResult()

    session = RecordingSession()
    manager = SimpleNamespace(role="manager", enterprise_id=17)
    with pytest.raises(HTTPException) as exc:
        get_authorized_field_row(field_id=99, db=session, current_user=manager)
    assert exc.value.status_code == 404
    assert "f.enterprise_id = :eid" in session.statement
    assert session.params == {"fid": 99, "eid": 17}


def test_unknown_active_role_is_rejected_by_shared_auth_dependency():
    unknown = SimpleNamespace(is_active=True, role="unexpected")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_active_user(unknown))
    assert exc.value.status_code == 403
