"""TASK_232: source and HTTP contracts of H1 Management Analytics v1 (no database)."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from main import app
from schemas.management_analytics import ManagementAnalyticsResponse
from services import management_analytics as service
from services import remediation_status
from services.agronomy_policy import STATUSES as VERIFICATION_STATUSES
from services.executive_accountability import resolve_window


ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "/api/management-analytics"

# Pinned together with DEFINITIONS_VERSION. If this fails, a metric definition,
# the Operational Center case model or the remediation projection changed:
# review the change, bump definitions_version when meaning changed, then re-pin.
PINNED_DEFINITIONS = ("management_analytics_v1", "91bca1b614782a938742b28a71db41d21a9a0fc916a942479f4964540211ea05")


def user(role="manager", enterprise_id=5, user_id=7):
    return SimpleNamespace(id=user_id, role=role, enterprise_id=enterprise_id, is_active=True)


class NoDatabase:
    """Any statement is a failure: these requests must be refused before SQL."""

    def execute(self, *args, **kwargs):  # pragma: no cover - reaching it is the failure
        raise AssertionError("the database must not be queried")

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def client_as():
    from api.auth import get_current_active_user
    from database import get_db

    def build(identity):
        app.dependency_overrides[get_db] = lambda: NoDatabase()
        app.dependency_overrides[get_current_active_user] = lambda: identity
        return TestClient(app)

    yield build
    app.dependency_overrides.clear()


def test_router_is_registered_read_only_and_authenticated():
    paths = {path: operations for path, operations in app.openapi()["paths"].items()
             if path.startswith(ENDPOINT)}
    assert {path: set(operations) for path, operations in paths.items()} == {ENDPOINT: {"get"}}
    operation = paths[ENDPOINT]["get"]
    assert operation["security"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ManagementAnalyticsResponse")
    source = (ROOT / "api/management_analytics.py").read_text("utf-8")
    assert "get_current_active_user" in source
    assert "response_model=ManagementAnalyticsResponse" in source
    assert "management_analytics_router" in (ROOT / "main.py").read_text("utf-8")


def test_scope_is_server_side_and_filters_only_narrow():
    assert service.resolve_scope(user("admin", None)).authorization == "global"
    assert service.resolve_scope(user("admin", None), enterprise_id=9).enterprise_id == 9
    manager = service.resolve_scope(user(), field_id=3, current_crop_type_id=4)
    assert (manager.authorization, manager.enterprise_id, manager.field_id,
            manager.current_crop_type_id) == ("tenant", 5, 3, 4)
    assert service.resolve_scope(user(), enterprise_id=5).enterprise_id == 5
    with pytest.raises(HTTPException) as caught:
        service.resolve_scope(user(), enterprise_id=6)
    assert (caught.value.status_code, caught.value.detail) == (404, "Enterprise not found")
    for denied in (user("agronomist"), user("viewer"), user("unknown"), user("manager", None)):
        with pytest.raises(HTTPException) as caught:
            service.resolve_scope(denied)
        assert caught.value.status_code == 403


def test_statement_scope_fragments_come_from_the_server_scope():
    manager = service.statement_for(service.resolve_scope(user()))
    assert "f.enterprise_id=:scope_enterprise_id" in manager
    assert "c.enterprise_id=:scope_enterprise_id" in manager
    admin = service.statement_for(service.resolve_scope(user("admin", None)))
    assert ":scope_enterprise_id" not in admin
    assert "c.root_source='external' AND true" in admin
    narrowed = service.statement_for(
        service.resolve_scope(user("admin", None), field_id=3, current_crop_type_id=4))
    assert "f.id=:field_id" in narrowed and "c.field_id=:field_id" in narrowed
    assert "crop.crop_type_id=:current_crop_type_id" in narrowed
    assert "c.root_source='external' AND false" in narrowed
    for statement in (manager, admin, narrowed):
        assert "@" not in statement


def test_statement_reads_only_the_canonical_lifecycles():
    statement = service.STATEMENT_TEMPLATE
    for retired in ("corrective_actions", "action_verification_requests", "operational_audit_events"):
        assert retired not in statement
    for canonical in ("agronomy_plans", "agronomy_events", "agronomy_verifications",
                      "agronomy_work_items", "field_inspections", "autonomous_anomaly_candidates"):
        assert canonical in statement
    assert remediation_status.inspection_status_sql("i", "p") in statement
    assert service.EXCLUDED_LEGACY_SOURCES == ("corrective_actions", "action_verification_requests")


def test_overdue_cases_are_the_operational_center_flag_not_a_second_rule():
    from services.operational_center import CASES_CTE

    # The case model defines is_overdue once; H1 only counts it.
    assert CASES_CTE.count("AS is_overdue") == 1
    assert service.CURRENT_COLUMNS["overdue_cases"] == "is_overdue"
    assert service.BREAKDOWN_CURRENT_COLUMNS["overdue_cases"] == "is_overdue"
    template = service.STATEMENT_TEMPLATE[len(CASES_CTE):]
    assert "c.is_overdue" in template
    for own_rule in ("due_date <", ":as_of_date", "inspection_overdue", "plan_overdue"):
        assert own_rule not in template
    # Late work items are a different unit with their own explicit name.
    assert "AS overdue_work_items" in template


def test_no_current_figure_reuses_an_operational_center_summary_name():
    from schemas.management_analytics import CurrentState, RemediationStatusCounts
    from schemas.operational_center import SummaryResponse

    assert set(CurrentState.model_fields) & set(SummaryResponse.model_fields) == {"as_of"}
    # State counts are keyed by the TASK_225 values themselves, inside by_remediation_status.
    assert set(RemediationStatusCounts.model_fields) == set(service.ACTIVE_STATES)


def test_crop_is_declared_a_current_classification():
    from schemas.management_analytics import Breakdowns, CropClassification, FieldBreakdown

    assert CropClassification.model_fields["historical_crop_at_event"].annotation.__args__ == (False,)
    assert "current_crops" in Breakdowns.model_fields and "crops" not in Breakdowns.model_fields
    assert {"current_crop_type_id", "current_crop_name"} <= set(FieldBreakdown.model_fields)
    assert "crop_type_id" not in FieldBreakdown.model_fields
    classification = service._crop_classification(service.datetime(2026, 9, 26, tzinfo=service.TASHKENT))
    assert (classification["basis"], classification["reference_year"],
            classification["historical_crop_at_event"]) == ("current_crop_season", 2026, False)


def test_module_uses_explicit_sql_and_no_orm_loading():
    source = (ROOT / "services/management_analytics.py").read_text("utf-8")
    for forbidden in ("relationship(", ".query(", "joinedload", "selectinload", "lazy=", "from models"):
        assert forbidden not in source


def test_outcome_vocabulary_matches_policy_r3_f_v1():
    assert set(service.CONCLUSIVE) | set(service.NOT_CONCLUSIVE) == set(VERIFICATION_STATUSES)
    assert not set(service.CONCLUSIVE) & set(service.NOT_CONCLUSIVE)
    assert service.CONCLUSIVE == ("IMPROVED", "NO_MATERIAL_CHANGE", "WORSENED")
    assert set(service.ACTIVE_STATES) == (
        set(remediation_status.REMEDIATION_STATES) - remediation_status.TERMINAL_STATES
        - {remediation_status.DATA_UNAVAILABLE}
    )


def test_definitions_are_versioned_and_pinned():
    assert (service.DEFINITIONS_VERSION, service.DEFINITIONS_FINGERPRINT) == PINNED_DEFINITIONS


def test_duration_statistics_rules():
    empty = service.duration_metric("inspection_opened_to_reviewed", None)
    assert (empty["sample_count"], empty["median_hours"], empty["p90_hours"], empty["status"]) == (
        0, None, None, "no_samples")
    few = service.duration_metric("inspection_opened_to_reviewed",
                                  {"sample_count": 9, "median_hours": 5.0, "p90_hours": 8.2})
    assert (few["median_hours"], few["p90_hours"], few["status"]) == (5.0, None, "measured")
    enough = service.duration_metric("inspection_opened_to_reviewed",
                                     {"sample_count": 10, "median_hours": 5.5, "p90_hours": 9.1234})
    assert (enough["median_hours"], enough["p90_hours"]) == (5.5, 9.12)
    assert set(service.DURATIONS) == set(ManagementAnalyticsResponse.model_fields["cycle_times"]
                                         .annotation.model_fields["metrics"].annotation.model_fields)
    for definition in service.DURATIONS.values():
        assert {"start_event", "end_event", "period_anchor", "population"} <= set(definition)


def test_rates_never_divide_by_zero():
    assert service._rate(0, 0) is None
    assert service._rate(3, 0) is None
    assert service._rate(1, 3) == 0.3333
    assert service._rate(7, 9) == 0.7778


def test_local_calendar_buckets():
    window = resolve_window(date(2026, 3, 4), date(2026, 3, 20))
    assert service.bucket_starts(window, "week") == [date(2026, 3, 2), date(2026, 3, 9), date(2026, 3, 16)]
    assert service.bucket_starts(window, "month") == [date(2026, 3, 1)]
    assert len(service.bucket_starts(window, "day")) == 17
    year = resolve_window(date(2025, 12, 15), date(2026, 2, 3))
    assert service.bucket_starts(year, "month") == [date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]
    periods = service._periods(window, "week", [{"bucket_start": "2026-03-02", "inspections_opened": 2}])
    assert [(row["bucket_start"], row["bucket_end"], row["inspections_opened"]) for row in periods] == [
        (date(2026, 3, 4), date(2026, 3, 8), 2), (date(2026, 3, 9), date(2026, 3, 15), 0),
        (date(2026, 3, 16), date(2026, 3, 20), 0)]


def test_response_contract_is_strict_and_versioned():
    assert ManagementAnalyticsResponse.model_fields["definitions_version"].annotation.__args__ == (
        "management_analytics_v1",)
    with pytest.raises(ValidationError):
        ManagementAnalyticsResponse.model_validate({"definitions_version": "management_analytics_v2"})


@pytest.mark.parametrize("params", [
    {"date_from": "2026-03-10", "date_to": "2026-03-01"},
    {"date_from": "2025-01-01", "date_to": "2026-01-02"},
    {"granularity": "quarter"},
    {"field_limit": 0}, {"field_limit": 201}, {"field_offset": -1}, {"field_offset": 10001},
    {"enterprise_id": 0}, {"field_id": -1}, {"current_crop_type_id": 0}, {"date_to": "2026-02-30"},
])
def test_invalid_requests_are_refused_before_any_sql(client_as, params):
    response = client_as(user()).get(ENDPOINT, params=params)
    assert response.status_code == 422, response.text


def test_ambiguous_crop_type_id_is_refused_not_ignored(client_as):
    response = client_as(user()).get(ENDPOINT, params={"crop_type_id": 4})
    assert response.status_code == 422
    assert "current_crop_type_id" in response.json()["detail"]
    assert "crop_type_id" not in {parameter["name"] for parameter in
                                  app.openapi()["paths"][ENDPOINT]["get"]["parameters"]}


@pytest.mark.parametrize("role", ["agronomist", "viewer"])
def test_non_management_roles_are_refused_before_any_sql(client_as, role):
    response = client_as(user(role)).get(ENDPOINT)
    assert response.status_code == 403


def test_foreign_enterprise_is_refused_before_any_sql(client_as):
    response = client_as(user()).get(ENDPOINT, params={"enterprise_id": 6})
    assert (response.status_code, response.json()) == (404, {"detail": "Enterprise not found"})


def test_unauthenticated_request_is_refused():
    app.dependency_overrides.clear()
    assert TestClient(app).get(ENDPOINT).status_code == 401
