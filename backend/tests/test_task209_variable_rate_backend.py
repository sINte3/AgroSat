"""Validation, authorization, idempotency, decision, and export tests."""

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from pydantic import ValidationError

from schemas.variable_rate import VariableRateCreateRequest, VariableRateDecisionRequest
from services import variable_rate_recommendations as service


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


def field():
    return SimpleNamespace(id=3, enterprise_id=5, name="Fixture Field")


def payload(**changes):
    value = {
        "field_id": 3,
        "productivity_run_id": 91,
        "crop_code": "cotton",
        "season_year": 2026,
        "recommendation_kind": "fertilizer",
        "rate_unit": "kg_ha",
        "minimum_rate": 80,
        "maximum_rate": 160,
        "zone_rates": {"low": 90, "medium": 120, "high": 150},
        "equipment_capability": {
            "equipment_name": "Fixture spreader",
            "supported_unit": "kg_ha",
            "minimum_controllable_rate": 50,
            "maximum_controllable_rate": 200,
            "compatibility_note": "Requires operator calibration before use",
        },
        "notes": "Human-entered agronomist draft",
        "safety_acknowledged": True,
    }
    value.update(changes)
    return VariableRateCreateRequest(**value)


def run_row(**changes):
    value = {
        "id": 501,
        "enterprise_id": 5,
        "field_id": 3,
        "field_name": "Fixture Field",
        "productivity_run_id": 91,
        "parent_recommendation_id": None,
        "version": 1,
        "crop_code": "cotton",
        "season_year": 2026,
        "recommendation_kind": "fertilizer",
        "rate_unit": "kg_ha",
        "minimum_rate": 80,
        "maximum_rate": 160,
        "zone_rates": {"low": 90, "medium": 120, "high": 150},
        "equipment_capability": {"equipment_name": "Fixture spreader"},
        "source_confidence": 0.78,
        "source_unzoned_area_ha": 41.42,
        "status": "draft",
        "safety_acknowledged": True,
        "notes": "Human-entered agronomist draft",
        "created_by_id": 7,
        "created_by_name": "Fixture Manager",
        "approved_by_id": None,
        "approved_by_name": None,
        "approved_at": None,
        "rejected_by_id": None,
        "rejected_by_name": None,
        "rejected_at": None,
        "decision_note": None,
        "created_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
        "algorithm_version": "yield_grid_stability_v1",
        "selected_seasons": [2024, 2025, 2026],
        "source_import_ids": [100, 101, 102],
    }
    value.update(changes)
    return value


def test_payload_requires_matching_units_human_bounds_equipment_and_ack():
    valid = payload()
    assert valid.zone_rates.high == 150
    for changes, match in (
        ({"rate_unit": "mm"}, "kind and rate unit"),
        ({"zone_rates": {"low": 70, "medium": 120, "high": 150}}, "human-entered"),
        ({"safety_acknowledged": False}, "acknowledgement"),
    ):
        with pytest.raises(ValidationError, match=match):
            payload(**changes)


def test_decision_requires_explicit_confirmation_and_note():
    assert VariableRateDecisionRequest(
        expected_version=1, confirm=True, note="Reviewed by manager"
    ).expected_version == 1
    with pytest.raises(ValidationError, match="confirmation"):
        VariableRateDecisionRequest(
            expected_version=1, confirm=False, note="Reviewed by manager"
        )


def test_detail_hides_cross_tenant_identity_inside_sql():
    db = Session([[]])
    with pytest.raises(HTTPException) as exc:
        service.get_recommendation(db, user(), 501)
    assert exc.value.status_code == 404
    statement, params = db.calls[0]
    assert "r.enterprise_id=:enterprise_id" in statement
    assert params["enterprise_id"] == 5


def test_viewer_cannot_create_before_any_sql():
    db = Session()
    with pytest.raises(HTTPException) as exc:
        service.create_recommendation(db, user("viewer"), field(), payload(), "request-01")
    assert exc.value.status_code == 403
    assert not db.calls


def test_create_validates_ready_source_and_persists_audit_atomically():
    source = {
        "id": 91,
        "confidence": 0.78,
        "area_delta_ha": 41.42,
        "result_status": "ready",
        "algorithm_version": "yield_grid_stability_v1",
        "zone_count": 3,
    }
    db = Session([[], [source], [], [{"id": 501}], [], [run_row()]])
    created, item = service.create_recommendation(
        db, user(), field(), payload(), "request-01"
    )
    assert created is True and item["status"] == "draft"
    assert db.commits == 1 and db.rollbacks == 0
    assert "productivity_zone_runs" in db.calls[1][0]
    assert "FOR UPDATE" in db.calls[2][0]
    assert "INSERT INTO variable_rate_recommendations" in db.calls[3][0]
    assert "INSERT INTO variable_rate_recommendation_events" in db.calls[4][0]
    assert db.calls[3][1]["zone_rates"] == '{"low": 90.0, "medium": 120.0, "high": 150.0}'


def test_idempotent_replay_returns_same_item_and_payload_conflict_is_409():
    fingerprint = service._fingerprint(7, payload())
    db = Session([[{"id": 501, "request_fingerprint": fingerprint}], [run_row()]])
    created, item = service.create_recommendation(
        db, user(), field(), payload(), "request-01"
    )
    assert created is False and item["id"] == 501
    conflict = Session([[{"id": 501, "request_fingerprint": "b" * 64}]])
    with pytest.raises(HTTPException) as exc:
        service.create_recommendation(
            conflict, user(), field(), payload(), "request-01"
        )
    assert exc.value.status_code == 409
    assert conflict.rollbacks == 1


def test_approve_is_management_only_versioned_and_audited():
    decision = VariableRateDecisionRequest(
        expected_version=1,
        confirm=True,
        note="Rates and equipment reviewed",
    )
    agronomist = Session()
    with pytest.raises(HTTPException) as exc:
        service.decide(
            agronomist, user("agronomist"), 501, decision, "approved"
        )
    assert exc.value.status_code == 403
    db = Session([
        [{"id": 501, "enterprise_id": 5, "field_id": 3}],
        [],
        [run_row(
            status="approved",
            approved_by_id=7,
            approved_by_name="Fixture Manager",
            approved_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
            decision_note="Rates and equipment reviewed",
        )],
    ])
    item = service.decide(db, user(), 501, decision, "approved")
    assert item["status"] == "approved"
    assert db.commits == 1
    assert "status='draft' AND version=:expected_version" in db.calls[0][0]
    assert "INSERT INTO variable_rate_recommendation_events" in db.calls[1][0]


def test_conflicting_decision_returns_409_without_overwrite():
    decision = VariableRateDecisionRequest(
        expected_version=1, confirm=True, note="Reviewed"
    )
    db = Session([[], [run_row(status="approved")]])
    with pytest.raises(HTTPException) as exc:
        service.decide(db, user(), 501, decision, "approved")
    assert exc.value.status_code == 409
    assert db.rollbacks == 1


def test_geojson_export_reconciles_human_rates_and_source_geometry():
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [[[[64.42, 39.77], [64.43, 39.77], [64.42, 39.77]]]],
    }
    db = Session([
        [run_row(status="approved")],
        [
            {
                "id": 1,
                "zone_class": "low",
                "geometry": geometry,
                "area_ha": 0.36,
                "mean_score": 0.25,
            },
            {
                "id": 2,
                "zone_class": "high",
                "geometry": geometry,
                "area_ha": 0.36,
                "mean_score": 0.75,
            },
        ],
    ])
    export = service.export_geojson(db, user(), 501)
    assert export["type"] == "FeatureCollection"
    assert export["crs"]["properties"]["name"] == "EPSG:4326"
    assert [feature["properties"]["rate"] for feature in export["features"]] == [90, 150]
    assert all(
        "not an autonomous prescription" in feature["properties"]["safety_statement"]
        for feature in export["features"]
    )


def test_list_is_bounded_and_uses_explicit_joins_without_lazy_loading():
    db = Session([[run_row()]])
    result = service.list_recommendations(
        db, user(), field(), limit=50, offset=0
    )
    assert len(result["items"]) == 1
    assert "LIMIT :limit OFFSET :offset" in db.calls[0][0]
    source = open(service.__file__, encoding="utf-8").read()
    assert "relationship(" not in source
    assert "lazy=" not in source
