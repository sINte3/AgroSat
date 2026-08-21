from datetime import datetime, timezone
import math

import pytest
from pydantic import ValidationError

from schemas.anomaly_inspection import (
    ActionTransitionRequest,
    CreateInspectionRequest,
    FindingRequest,
    ReviewInspectionRequest,
    VerifyActionRequest,
)
from services.anomaly_inspections import _photo_content_type


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def pixel_payload(**overrides):
    value = {
        "field_id": 4,
        "source_kind": "pixel_ndvi",
        "provider": "cdse",
        "item_id": "signed-scene-identity",
        "acquired_at": NOW,
        "index_name": "ndvi",
        "sampled_value": 0.42,
        "geometry_hash": "a" * 64,
        "point": {"longitude": 64.5, "latitude": 40.0},
        "reason": "Inspect a bounded anomaly",
        "priority": "high",
        "assigned_to_id": 9,
        "due_at": NOW,
    }
    value.update(overrides)
    return value


def finding_payload(**overrides):
    value = {
        "expected_version": 2,
        "inspected_at": NOW,
        "gps_point": {"longitude": 64.5, "latitude": 40.0},
        "gps_accuracy_m": 5,
        "cause": "water_stress",
        "severity": "high",
        "affected_area_ha": 1.2,
        "observations": "Reduced turgor near the irrigation line",
        "recommended_action": "Inspect and restore irrigation delivery",
        "sync_state": "server",
    }
    value.update(overrides)
    return value


def test_pixel_snapshot_accepts_signed_identity_up_to_contract_limit():
    request = CreateInspectionRequest(**pixel_payload(item_id="x" * 768))
    assert len(request.item_id) == 768


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, -1.01, 1.01])
def test_pixel_snapshot_rejects_non_finite_and_out_of_bounds_values(value):
    with pytest.raises(ValidationError):
        CreateInspectionRequest(**pixel_payload(sampled_value=value))


def test_manual_source_rejects_provider_snapshot_fields():
    with pytest.raises(ValidationError):
        CreateInspectionRequest(**pixel_payload(source_kind="manual"))


def test_alert_source_requires_alert_identity():
    with pytest.raises(ValidationError):
        CreateInspectionRequest(**pixel_payload(
            source_kind="alert", provider=None, item_id=None, acquired_at=None,
            index_name=None, sampled_value=None, geometry_hash=None,
        ))


def test_point_and_zone_are_mutually_exclusive():
    with pytest.raises(ValidationError):
        CreateInspectionRequest(**pixel_payload(zone={
            "type": "Polygon", "coordinates": [[[64.4, 40.0], [64.5, 40.0], [64.5, 40.1], [64.4, 40.0]]],
        }))


def test_finding_requires_one_bounded_area_measure():
    assert FindingRequest(**finding_payload()).affected_area_ha == 1.2
    with pytest.raises(ValidationError):
        FindingRequest(**finding_payload(affected_area_ha=None, affected_area_pct=None))
    with pytest.raises(ValidationError):
        FindingRequest(**finding_payload(affected_area_pct=20))


def test_other_cause_requires_explanation():
    with pytest.raises(ValidationError):
        FindingRequest(**finding_payload(cause="other"))
    assert FindingRequest(**finding_payload(cause="other", other_explanation="Localized unknown damage")).cause.value == "other"


def test_rejection_and_terminal_action_notes_are_mandatory():
    with pytest.raises(ValidationError):
        ReviewInspectionRequest(expected_version=3, decision="rejected")
    with pytest.raises(ValidationError):
        ActionTransitionRequest(expected_version=2, transition="complete")
    assert ActionTransitionRequest(expected_version=2, transition="start").transition == "start"


def test_follow_up_is_allowed_only_for_an_ineffective_result():
    with pytest.raises(ValidationError):
        VerifyActionRequest(
            expected_version=3, result="effective", notes="Recovery confirmed",
            create_follow_up=True, follow_up_assignee_id=9, follow_up_due_at=NOW,
        )
    request = VerifyActionRequest(
        expected_version=3, result="ineffective", notes="Another cycle is required",
        create_follow_up=True, follow_up_assignee_id=9, follow_up_due_at=NOW,
    )
    assert request.create_follow_up is True


@pytest.mark.parametrize(
    ("content_type", "data", "extension"),
    [
        ("image/jpeg", b"\xff\xd8\xffpayload", ".jpg"),
        ("image/png", b"\x89PNG\r\n\x1a\npayload", ".png"),
        ("image/webp", b"RIFF0000WEBPpayload", ".webp"),
    ],
)
def test_photo_magic_contract(content_type, data, extension):
    assert _photo_content_type(data, content_type) == (content_type, extension)


def test_photo_magic_mismatch_is_rejected():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        _photo_content_type(b"not-a-png", "image/png")
    assert raised.value.status_code == 422
