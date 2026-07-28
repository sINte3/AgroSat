from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from schemas.pixel_anomaly import CreateInspectionFromAnomalyRequest
from services import pixel_anomalies as service


class Result:
    def __init__(self, rows=None):
        self.rows = rows or []

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class RecordingDB:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        if not self.results:
            raise AssertionError("unexpected SQL statement")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return Result(result)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def user(role="manager", enterprise_id=7, user_id=5):
    return SimpleNamespace(role=role, enterprise_id=enterprise_id, id=user_id)


def anomaly_row(**changes):
    values = {
        "id": 91,
        "field_id": 11,
        "field_name": "Synthetic Field",
        "enterprise_id": 7,
        "enterprise_name": "Synthetic Enterprise",
        "index_code": "ndvi",
        "current_observation_date": date(2026, 6, 20),
        "comparison_observation_date": date(2026, 6, 10),
        "area_ha": 2.5,
        "score": 0.7,
        "severity": "high",
        "persistence_count": 2,
        "classification": "persistent",
        "confidence": 0.8,
        "status": "open",
        "created_at": datetime(2026, 6, 20, tzinfo=timezone.utc),
        "inspection_id": None,
        "inspection_status": None,
        "inspection_assigned_to_id": None,
        "inspection_due_date": None,
    }
    values.update(changes)
    return values


def test_list_is_one_bounded_tenant_scoped_statement():
    row = anomaly_row(total=1)
    db = RecordingDB([[row]])
    response = service.list_items(
        db,
        user(),
        {
            "enterprise_id": None,
            "field_id": None,
            "index_code": None,
            "status": None,
            "classification": None,
            "date_from": None,
            "date_to": None,
            "limit": 50,
            "offset": 0,
        },
    )
    assert response["total"] == 1
    assert response["items"][0]["field"]["enterprise_id"] == 7
    assert len(db.calls) == 1
    sql, params = db.calls[0]
    assert "a.enterprise_id=:enterprise_id" in sql
    assert "LIMIT :limit OFFSET :offset" in sql
    assert params["enterprise_id"] == 7
    assert params["limit"] == 50


def test_list_foreign_enterprise_and_unbounded_date_range_fail_before_sql():
    db = RecordingDB([])
    base = {
        "enterprise_id": 8,
        "field_id": None,
        "index_code": None,
        "status": None,
        "classification": None,
        "date_from": None,
        "date_to": None,
        "limit": 50,
        "offset": 0,
    }
    with pytest.raises(HTTPException) as foreign:
        service.list_items(db, user(), base)
    assert foreign.value.status_code == 403
    with pytest.raises(HTTPException) as dates:
        service.list_items(
            db,
            user(),
            {
                **base,
                "enterprise_id": 7,
                "date_from": date(2024, 1, 1),
                "date_to": date(2026, 1, 2),
            },
        )
    assert dates.value.status_code == 422
    assert db.calls == []


def test_summary_is_one_statement_and_cross_tenant_is_non_enumerable():
    summary = {
        **anomaly_row(),
        "total": 2,
        "open": 1,
        "inspection_created": 1,
        "persistent": 1,
        "recovering": 0,
        "latest_observation_date": date(2026, 6, 20),
        "latest_confidence": 0.8,
        "insufficient_data_runs": 1,
    }
    db = RecordingDB([[summary]])
    response = service.field_summary(db, user(), 11)
    assert response["total"] == 2
    assert response["insufficient_data_runs"] == 1
    assert len(db.calls) == 1
    assert "f.enterprise_id=:actor_enterprise_id" in db.calls[0][0]
    denied = RecordingDB([[]])
    with pytest.raises(HTTPException) as missing:
        service.field_summary(denied, user(), 99)
    assert missing.value.status_code == 404
    assert denied.calls[0][1]["actor_enterprise_id"] == 7


def test_detail_and_geometry_each_use_one_tenant_scoped_statement():
    detail_row = anomaly_row(
        algorithm_version="paired_within_field_drop_v1",
        run_key="a" * 64,
        threshold_hash="b" * 64,
        thresholds={"comparison_drop": 0.1},
        quality_summary={"valid_pixels_pct": 100},
        provenance={"contract": "non_diagnostic"},
        run_provenance={"provider": "deterministic_fixture"},
        reason_codes=[],
    )
    detail_db = RecordingDB([[detail_row]])
    result = service.detail(detail_db, user(), 91)
    assert result["algorithm_version"] == "paired_within_field_drop_v1"
    assert result["provenance"] == {
        "contract": "non_diagnostic",
        "run": {"provider": "deterministic_fixture"},
    }
    assert len(detail_db.calls) == 1
    assert "a.enterprise_id=:actor_enterprise_id" in detail_db.calls[0][0]
    assert "r.provenance AS run_provenance" in detail_db.calls[0][0]

    geometry_db = RecordingDB(
        [[anomaly_row(geometry={"type": "MultiPolygon", "coordinates": []})]]
    )
    feature = service.geometry(geometry_db, user(), 91)
    assert feature["type"] == "Feature"
    assert feature["properties"]["classification"] == "persistent"
    assert len(geometry_db.calls) == 1
    assert "ST_AsGeoJSON" in geometry_db.calls[0][0]


def test_cross_tenant_anomaly_id_returns_404_with_no_fallback_query():
    db = RecordingDB([[]])
    with pytest.raises(HTTPException) as error:
        service.detail(db, user(), 999)
    assert error.value.status_code == 404
    assert len(db.calls) == 1
    assert db.calls[0][1] == {"actor_enterprise_id": 7, "anomaly_id": 999}


def test_viewer_and_foreign_agronomist_assignment_fail_before_sql():
    payload = CreateInspectionFromAnomalyRequest()
    viewer_db = RecordingDB([])
    with pytest.raises(HTTPException) as viewer:
        service.create_inspection(
            viewer_db,
            user("viewer"),
            91,
            payload,
            "fixture-key-001",
        )
    assert viewer.value.status_code == 403
    assert viewer_db.calls == []

    agronomist_db = RecordingDB([])
    with pytest.raises(HTTPException) as assignee:
        service.create_inspection(
            agronomist_db,
            user("agronomist", user_id=6),
            91,
            CreateInspectionFromAnomalyRequest(assigned_to_id=8),
            "fixture-key-002",
        )
    assert assignee.value.status_code == 403
    assert agronomist_db.calls == []


def test_create_inspection_links_and_updates_in_one_commit():
    db = RecordingDB(
        [
            [],
            [
                {
                    "id": 91,
                    "field_id": 11,
                    "enterprise_id": 7,
                    "status": "open",
                    "severity": "high",
                    "current_observation_date": date(2026, 6, 20),
                }
            ],
            [{"id": 6}],
            [],
            [
                {
                    "id": 301,
                    "status": "pending",
                    "assigned_to_id": 6,
                    "due_date": None,
                }
            ],
            [],
            [{"id": 91}],
        ]
    )
    response = service.create_inspection(
        db,
        user("agronomist", user_id=6),
        91,
        CreateInspectionFromAnomalyRequest(
            instructions="Inspect the measured anomaly zone"
        ),
        "fixture-key-003",
    )
    assert response["created"] is True
    assert response["inspection"]["id"] == 301
    assert response["inspection"]["assigned_to_id"] == 6
    assert db.commits == 1
    assert db.rollbacks == 0
    assert len(db.calls) == 7
    statements = [sql for sql, _ in db.calls]
    assert "FOR UPDATE" in statements[1]
    assert "INSERT INTO field_inspections" in statements[4]
    assert "INSERT INTO pixel_anomaly_inspections" in statements[5]
    assert "UPDATE pixel_anomalies" in statements[6]
    assert all(
        db.calls[index][1]["enterprise_id"] == 7
        for index in (3, 4, 5, 6)
    )


def test_create_replay_is_read_only_and_payload_conflict_is_409():
    replay_row = {
        "anomaly_id": 91,
        "request_fingerprint": None,
        "inspection_id": 301,
        "inspection_status": "pending",
        "inspection_assigned_to_id": None,
        "inspection_due_date": None,
    }
    payload = CreateInspectionFromAnomalyRequest()
    actor = service._actor(user(), write=True)
    replay_row["request_fingerprint"] = service._fingerprint(
        actor,
        91,
        payload,
        None,
    )
    replay_db = RecordingDB([[replay_row]])
    response = service.create_inspection(
        replay_db,
        user(),
        91,
        payload,
        "fixture-key-004",
    )
    assert response["created"] is False
    assert len(replay_db.calls) == 1
    assert replay_db.commits == replay_db.rollbacks == 0

    conflict_db = RecordingDB([[{**replay_row, "request_fingerprint": "f" * 64}]])
    with pytest.raises(HTTPException) as conflict:
        service.create_inspection(
            conflict_db,
            user(),
            91,
            payload,
            "fixture-key-004",
        )
    assert conflict.value.status_code == 409
    assert conflict_db.rollbacks == 1


def test_no_orm_relationship_or_lazy_loading_in_api_service():
    source = open(service.__file__, encoding="utf-8").read()
    assert "relationship(" not in source
    assert ".query(" not in source
    assert "joinedload" not in source
    assert "selectinload" not in source
