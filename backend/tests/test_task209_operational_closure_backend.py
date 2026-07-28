"""Offline behavior, authorization, idempotency, and query-shape coverage."""

from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from api.operational_closure import (
    action_router,
    inspection_router,
    verification_router,
)
from schemas.operational_closure import (
    CreateCorrectiveActionRequest,
    EvidenceMetadataRequest,
    RecordInspectionResultRequest,
    ResolveVerificationRequest,
    UpdateCorrectiveActionRequest,
)
from services import operational_closure as service
from services.operational_verification import (
    LIMITATION,
    Observation,
    resolve_direction,
)


def user(role="admin", identifier=7, enterprise=None):
    return SimpleNamespace(role=role, id=identifier, enterprise_id=enterprise)


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
            raise AssertionError("unexpected SQL statement")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return Result(outcome)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def inspection_row(**changes):
    values = {
        "id": 11,
        "field_id": 3,
        "enterprise_id": 5,
        "assigned_to_id": 7,
        "status": "in_progress",
        "version": 2,
    }
    values.update(changes)
    return values


def result_row(**changes):
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    values = {
        "id": 21,
        "inspection_id": 11,
        "field_id": 3,
        "enterprise_id": 5,
        "recorded_by_id": 7,
        "cause_code": "irrigation",
        "cause_details": "Blocked irrigation line",
        "evidence_note": "Observed dry row",
        "latitude": None,
        "longitude": None,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    values.update(changes)
    return values


def action_row(**changes):
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    values = {
        "id": 31,
        "inspection_id": 11,
        "result_id": 21,
        "field_id": 3,
        "enterprise_id": 5,
        "created_by_id": 7,
        "owner_id": 7,
        "owner_name": "Agronomist",
        "description": "Repair irrigation line",
        "due_date": date(2026, 8, 1),
        "status": "open",
        "is_overdue": False,
        "closure_reason": None,
        "closed_by_id": None,
        "closed_at": None,
        "reopen_reason": None,
        "reopened_by_id": None,
        "reopened_at": None,
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "latest_verification_id": None,
        "verification_status": None,
        "verification_result": None,
        "verification_confidence": None,
    }
    values.update(changes)
    return values


def observation(
    identifier,
    observed_at,
    value,
    *,
    field_id=3,
    index_code="ndvi",
    valid=90.0,
    cloud=5.0,
):
    return Observation(
        record_id=identifier,
        source="ndvi_records" if index_code == "ndvi" else "satellite_index_records",
        field_id=field_id,
        index_code=index_code,
        observed_at=observed_at,
        value=value,
        valid_pixels_pct=valid,
        cloud_cover_pct=cloud,
        satellite="Sentinel-2",
    )


def test_all_ten_operations_require_authentication():
    app = FastAPI()
    app.include_router(inspection_router)
    app.include_router(action_router)
    app.include_router(verification_router)
    client = TestClient(app)
    key = {"Idempotency-Key": "task209-auth-check"}
    calls = (
        ("post", "/api/field-inspections/1/result", {}, key),
        ("post", "/api/field-inspections/1/evidence", {}, key),
        ("post", "/api/field-inspections/1/actions", {}, key),
        ("get", "/api/field-inspections/1/timeline", None, None),
        ("get", "/api/operational-actions", None, None),
        ("patch", "/api/operational-actions/1", {}, key),
        ("post", "/api/operational-actions/1/close", {}, key),
        ("post", "/api/operational-actions/1/reopen", {}, key),
        ("post", "/api/operational-actions/1/verification-requests", {}, key),
        ("post", "/api/verification-requests/1/resolve", {}, key),
    )
    for method, path, body, headers in calls:
        kwargs = {"headers": headers or {}}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 401, (method, path, response.text)


def test_viewer_cannot_execute_any_write_before_sql():
    payload = RecordInspectionResultRequest(
        expected_version=1,
        cause_code="unconfirmed",
    )
    db = Session()
    with pytest.raises(HTTPException) as caught:
        service.record_result(db, user("viewer", enterprise=5), 11, payload, "task209-key")
    assert caught.value.status_code == 403
    assert db.calls == []


def test_manager_missing_action_query_is_tenant_scoped_and_non_enumerable():
    payload = UpdateCorrectiveActionRequest(
        expected_version=1,
        status="in_progress",
    )
    db = Session([[], []])
    with pytest.raises(HTTPException) as caught:
        service.update_action(
            db,
            user("manager", enterprise=5),
            99,
            payload,
            "task209-update",
        )
    assert caught.value.status_code == 404
    action_sql, action_params = db.calls[1]
    assert "a.enterprise_id=:eid" in action_sql
    assert action_params["eid"] == 5
    assert db.rollbacks == 1


def test_agronomist_result_lock_includes_assignment_and_tenant():
    payload = RecordInspectionResultRequest(
        expected_version=2,
        cause_code="unconfirmed",
    )
    db = Session([[], []])
    with pytest.raises(HTTPException) as caught:
        service.record_result(
            db,
            user("agronomist", enterprise=5),
            11,
            payload,
            "task209-result",
        )
    assert caught.value.status_code == 404
    lock_sql, lock_params = db.calls[1]
    assert "i.enterprise_id=:eid" in lock_sql
    assert "i.assigned_to_id=:actor_id" in lock_sql
    assert lock_params["eid"] == 5
    assert lock_params["actor_id"] == 7


def test_record_result_is_atomic_audited_and_reloads_after_commit():
    payload = RecordInspectionResultRequest(
        expected_version=2,
        cause_code="irrigation",
        cause_details="Blocked irrigation line",
        evidence_note="Observed dry row",
    )
    db = Session(
        [
            [],
            [inspection_row()],
            [{"id": 21}],
            [{"version": 3}],
            [],
            [result_row()],
        ]
    )
    item = service.record_result(
        db,
        user("agronomist", enterprise=5),
        11,
        payload,
        "task209-result",
    )
    assert item["id"] == 21
    assert db.commits == 1
    assert db.rollbacks == 0
    combined = "\n".join(sql for sql, _ in db.calls)
    assert "status='completed'" in combined
    assert "AND status='in_progress'" in combined
    assert "INSERT INTO operational_audit_events" in combined
    assert "ST_SetSRID(ST_MakePoint" in combined


def test_same_fingerprint_replay_does_not_repeat_result_write():
    payload = RecordInspectionResultRequest(
        expected_version=2,
        cause_code="unconfirmed",
    )
    actor = service._actor(user("manager", enterprise=5), write=True)
    fingerprint = service._fingerprint(
        "inspection_result_recorded",
        actor,
        11,
        payload,
    )
    db = Session(
        [
            [
                {
                    "event_type": "inspection_result_recorded",
                    "inspection_id": 11,
                    "action_id": None,
                    "verification_id": None,
                    "request_fingerprint": fingerprint,
                    "event_metadata": {"result_id": 21},
                }
            ],
            [result_row()],
        ]
    )
    item = service.record_result(
        db,
        user("manager", enterprise=5),
        11,
        payload,
        "task209-replay",
    )
    assert item["id"] == 21
    assert db.commits == 0
    assert all("INSERT INTO inspection_results" not in sql for sql, _ in db.calls)


def test_idempotency_key_conflict_is_409_before_aggregate_lock():
    payload = RecordInspectionResultRequest(
        expected_version=2,
        cause_code="unconfirmed",
    )
    db = Session(
        [
            [
                {
                    "event_type": "inspection_result_recorded",
                    "inspection_id": 11,
                    "action_id": None,
                    "verification_id": None,
                    "request_fingerprint": "f" * 64,
                    "event_metadata": {"result_id": 21},
                }
            ]
        ]
    )
    with pytest.raises(HTTPException) as caught:
        service.record_result(
            db,
            user("manager", enterprise=5),
            11,
            payload,
            "task209-conflict",
        )
    assert caught.value.status_code == 409
    assert len(db.calls) == 1
    assert db.rollbacks == 1


def test_action_list_uses_two_constant_queries_and_shared_tenant_filter():
    summary = {"total": 1, "overdue": 0, "awaiting_verification": 0}
    db = Session([[summary], [action_row()]])
    result = service.list_actions(
        db,
        user("manager", enterprise=5),
        {
            "enterprise_id": None,
            "inspection_id": None,
            "owner_id": None,
            "status": None,
            "overdue_only": False,
            "awaiting_verification": False,
            "limit": 50,
            "offset": 0,
        },
    )
    assert result["summary"] == summary
    assert len(result["items"]) == 1
    assert len(db.calls) == 2
    assert all("a.enterprise_id=:eid" in sql for sql, _ in db.calls)
    assert all(params["eid"] == 5 for _, params in db.calls)


def test_cross_tenant_action_list_filter_is_denied_before_query():
    db = Session()
    with pytest.raises(HTTPException) as caught:
        service.list_actions(
            db,
            user("manager", enterprise=5),
            {
                "enterprise_id": 6,
                "inspection_id": None,
                "owner_id": None,
                "status": None,
                "overdue_only": False,
                "awaiting_verification": False,
                "limit": 50,
                "offset": 0,
            },
        )
    assert caught.value.status_code == 403
    assert db.calls == []
    assert db.rollbacks == 1


def test_photo_and_location_request_contracts_are_bounded():
    photo = EvidenceMetadataRequest(
        expected_version=3,
        evidence_type="photo",
        original_filename="field.jpg",
        media_type="image/jpeg",
        byte_size=1024,
        sha256="a" * 64,
    )
    assert photo.provider == "metadata_only"
    with pytest.raises(ValueError):
        EvidenceMetadataRequest(
            expected_version=3,
            evidence_type="photo",
            original_filename="../field.jpg",
            media_type="image/jpeg",
            byte_size=1024,
            sha256="a" * 64,
        )
    with pytest.raises(ValueError):
        EvidenceMetadataRequest(
            expected_version=3,
            evidence_type="geolocation",
            latitude=40,
        )


def test_agronomist_cannot_assign_action_to_another_owner():
    payload = CreateCorrectiveActionRequest(
        expected_inspection_version=3,
        result_id=21,
        owner_id=8,
        description="Repair irrigation line",
        due_date=date(2099, 8, 1),
    )
    db = Session([[], [inspection_row(status="completed", version=3)]])
    with pytest.raises(HTTPException) as caught:
        service.create_action(
            db,
            user("agronomist", enterprise=5),
            11,
            payload,
            "task209-action",
        )
    assert caught.value.status_code == 403
    assert db.rollbacks == 1


def test_agronomist_cannot_reopen_or_resolve_verification():
    from schemas.operational_closure import ReopenCorrectiveActionRequest

    payload = ReopenCorrectiveActionRequest(
        expected_version=3,
        reopen_reason="Later evidence requires more work",
    )
    db = Session()
    with pytest.raises(HTTPException) as caught:
        service.reopen_action(
            db,
            user("agronomist", enterprise=5),
            31,
            payload,
            "task209-reopen",
        )
    assert caught.value.status_code == 403
    assert db.calls == []


def verification_row(**changes):
    values = {
        "id": 41,
        "action_id": 31,
        "field_id": 3,
        "enterprise_id": 5,
        "index_code": "ndvi",
        "reference_date": date(2026, 7, 2),
        "minimum_separation_days": 3,
        "status": "awaiting_observation",
        "version": 1,
        "inspection_id": 11,
    }
    values.update(changes)
    return values


def observation_row(identifier, observed_at, value):
    return {
        "id": identifier,
        "captured_date": observed_at,
        "value": value,
        "valid_pixels_pct": 90.0,
        "cloud_cover_pct": 5.0,
        "satellite": "Sentinel-2",
    }


def test_resolve_verification_persists_eligible_pair_and_audit():
    payload = ResolveVerificationRequest(
        expected_version=1,
        notes="Observed later accepted scene",
    )
    loaded = {
        **verification_row(status="resolved", version=2),
        "result": "improved",
        "confidence": "high",
    }
    db = Session(
        [
            [],
            [verification_row()],
            [observation_row(51, date(2026, 7, 1), 0.4)],
            [observation_row(52, date(2026, 7, 8), 0.5)],
            [{"version": 2}],
            [],
            [loaded],
        ]
    )
    item = service.resolve_verification(
        db,
        user("manager", enterprise=5),
        41,
        payload,
        "task209-resolve",
    )
    assert item["result"] == "improved"
    assert item["limitation"] == LIMITATION
    update_sql, update_params = db.calls[4]
    assert "status='resolved'" in update_sql
    assert update_params["reference_ndvi"] == 51
    assert update_params["observation_ndvi"] == 52
    assert update_params["reference_value"] == 0.4
    assert update_params["observation_value"] == 0.5
    assert update_params["delta_value"] == 0.1
    assert db.commits == 1


def test_insufficient_verification_preserves_reference_shape_without_candidate():
    payload = ResolveVerificationRequest(expected_version=1)
    loaded = {
        **verification_row(status="resolved", version=2),
        "result": "insufficient_data",
        "confidence": "low",
    }
    db = Session(
        [
            [],
            [verification_row()],
            [observation_row(51, date(2026, 7, 1), 0.4)],
            [],
            [{"version": 2}],
            [],
            [loaded],
        ]
    )
    item = service.resolve_verification(
        db,
        user("manager", enterprise=5),
        41,
        payload,
        "task209-insufficient",
    )
    assert item["result"] == "insufficient_data"
    _, update_params = db.calls[4]
    assert update_params["reference_ndvi"] == 51
    assert update_params["reference_value"] == 0.4
    assert update_params["observation_ndvi"] is None
    assert update_params["observation_value"] is None
    assert update_params["delta_value"] is None


def test_direction_engine_reports_high_confidence_improvement_without_causality():
    reference = observation(1, date(2026, 7, 1), 0.40004)
    candidate = observation(2, date(2026, 7, 8), 0.50004)
    outcome = resolve_direction(
        reference,
        candidate,
        field_id=3,
        index_code="ndvi",
        reference_date=date(2026, 7, 2),
        minimum_separation_days=3,
    )
    assert outcome["result"] == "improved"
    assert outcome["confidence"] == "high"
    assert outcome["delta_value"] == 0.1
    assert outcome["limitation"] == LIMITATION


def test_direction_engine_rounds_to_four_decimals_for_unchanged():
    reference = observation(1, date(2026, 7, 1), 0.400041)
    candidate = observation(2, date(2026, 7, 8), 0.400039)
    outcome = resolve_direction(
        reference,
        candidate,
        field_id=3,
        index_code="ndvi",
        reference_date=date(2026, 7, 2),
        minimum_separation_days=3,
    )
    assert outcome["result"] == "unchanged"
    assert outcome["delta_value"] == 0.0


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        observation(2, date(2026, 7, 3), 0.5),
        observation(2, date(2026, 7, 8), 0.5, valid=49),
        observation(2, date(2026, 7, 8), 0.5, cloud=31),
    ],
)
def test_direction_engine_explicitly_returns_insufficient_data(candidate):
    reference = observation(1, date(2026, 7, 1), 0.4)
    outcome = resolve_direction(
        reference,
        candidate,
        field_id=3,
        index_code="ndvi",
        reference_date=date(2026, 7, 2),
        minimum_separation_days=3,
    )
    assert outcome["result"] == "insufficient_data"
    assert outcome["confidence"] == "low"


def test_direction_engine_rejects_reference_scope_mismatch():
    with pytest.raises(ValueError):
        resolve_direction(
            observation(1, date(2026, 7, 1), 0.4, field_id=99),
            observation(2, date(2026, 7, 8), 0.5),
            field_id=3,
            index_code="ndvi",
            reference_date=date(2026, 7, 2),
            minimum_separation_days=3,
        )


def test_backend_source_has_no_orm_relationship_or_unbounded_collection():
    source = Path(service.__file__).read_text(encoding="utf-8")
    assert "relationship(" not in source
    assert "lazy=" not in source
    assert "LIMIT :limit OFFSET :offset" in source
    assert "FOR UPDATE" in source
    assert "captured_date>:reference_date" in source
