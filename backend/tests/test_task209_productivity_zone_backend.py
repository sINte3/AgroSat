"""Tenant scope, bounded SQL, persistence, and API tests for zones."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
import pytest

from services import productivity_zones as service


RUN_UUID = UUID("11111111-1111-4111-8111-111111111111")


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
        return Result(self.outcomes.pop(0))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def user(role="manager", enterprise=5):
    return SimpleNamespace(role=role, id=7, enterprise_id=enterprise)


def field():
    return SimpleNamespace(id=3, enterprise_id=5, name="Fixture Field")


def run_row(**changes):
    value = {
        "id": 91,
        "enterprise_id": 5,
        "field_id": 3,
        "field_name": "Fixture Field",
        "algorithm": "yield_grid_stability",
        "algorithm_version": "yield_grid_stability_v1",
        "run_key": "a" * 64,
        "run_uuid": RUN_UUID,
        "selected_seasons": [2024, 2025, 2026],
        "source_import_ids": [100, 101, 102],
        "source_sha256s": ["1" * 64, "2" * 64, "3" * 64],
        "parameters": {"grid_metres": 30},
        "result_status": "ready",
        "reason_codes": [],
        "point_count": 72,
        "eligible_cell_count": 12,
        "field_area_ha": 10,
        "zoned_area_ha": 1.08,
        "area_delta_ha": 8.92,
        "confidence": 0.75,
        "provenance": {"input_kind": "accepted_measured_yield"},
        "started_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
        "finished_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
        "created_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
    }
    value.update(changes)
    return value


def input_rows(seasons=(2024, 2025, 2026), per_season=24):
    rows = []
    for season_index, season in enumerate(seasons):
        for index in range(per_season):
            rows.append({
                "import_id": 100 + season_index,
                "source_sha256": f"{season_index + 1:064x}",
                "season_year": season,
                "longitude": 64.42 + (index % 6) * 0.00035,
                "latitude": 39.77 + (index // 6) * 0.00027,
                "yield_t_ha": (
                    3.0
                    + (index % 6) * 0.35
                    + (index // 6) * 0.1
                    + season_index * 0.05
                ),
                "field_area_ha": 10.0,
            })
    return rows


def zone_rows():
    polygon = {
        "type": "MultiPolygon",
        "coordinates": [[[
            [64.42, 39.77],
            [64.421, 39.77],
            [64.421, 39.771],
            [64.42, 39.771],
            [64.42, 39.77],
        ]]],
    }
    return [
        {
            "zone_class": zone_class,
            "mean_score": score,
            "geometry": polygon,
            "area_ha": 0.36,
        }
        for zone_class, score in (
            ("low", 0.25),
            ("medium", 0.5),
            ("high", 0.75),
        )
    ]


def test_run_read_is_tenant_scoped_inside_sql_and_hides_foreign_id():
    db = Session([[]])
    with pytest.raises(HTTPException) as exc:
        service.get_run(db, user(), 91)
    assert exc.value.status_code == 404
    statement, params = db.calls[0]
    assert "r.enterprise_id=:enterprise_id" in statement
    assert params == {"run_id": 91, "enterprise_id": 5}


def test_admin_run_read_remains_global_and_returns_safety_statement():
    db = Session([[run_row()]])
    item = service.get_run(db, user("admin", None), 91)
    assert item["id"] == 91
    assert "not an agronomic prescription" in item["safety_statement"]
    assert "r.enterprise_id=:enterprise_id" not in db.calls[0][0]


def test_latest_and_zone_geometry_reads_are_bounded_and_explicit():
    db = Session([[run_row()], [run_row()], [{
        "id": 4,
        "zone_class": "low",
        "area_ha": 0.36,
        "mean_score": 0.25,
        "confidence": 0.75,
        "provenance": {},
        "geometry": {"type": "MultiPolygon", "coordinates": []},
        "created_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
    }]])
    assert service.latest_for_field(db, user(), field())["latest_run"]["id"] == 91
    payload = service.list_zones(db, user(), 91, limit=10, offset=0)
    assert len(payload["items"]) == 1
    assert "LIMIT :limit OFFSET :offset" in db.calls[2][0]
    assert "ST_AsGeoJSON" in db.calls[2][0]


def test_load_inputs_is_one_bounded_tenant_qualified_statement():
    db = Session([input_rows()])
    loaded, area = service.load_inputs(db, 5, 3, (2024, 2025, 2026))
    assert len(loaded) == 72
    assert area == 10
    assert len(db.calls) == 1
    statement, params = db.calls[0]
    assert "PARTITION BY i.season_year" in statement
    assert "ORDER BY season_year DESC LIMIT 5" in statement
    assert "i.enterprise_id=:enterprise_id" in statement
    assert params["enterprise_id"] == 5


def test_dry_run_is_deterministic_and_never_writes():
    first_db = Session([input_rows()])
    second_db = Session([input_rows()])
    first = service.compute_field(
        first_db, 5, 3, run_uuid=RUN_UUID, write=False
    )
    second = service.compute_field(
        second_db, 5, 3, run_uuid=RUN_UUID, write=False
    )
    assert first == second
    assert first["result"]["status"] == "ready"
    assert first_db.commits == first_db.rollbacks == 0
    assert len(first_db.calls) == 1


def test_ready_apply_clips_groups_reconciles_and_persists_atomically():
    db = Session([input_rows(), zone_rows(), [{"id": 91}], []])
    outcome = service.compute_field(
        db, 5, 3, run_uuid=RUN_UUID, write=True
    )
    assert outcome["created"] is True
    assert outcome["status"] == "ready"
    assert db.commits == 1
    assert db.rollbacks == 0
    assert len(db.calls) == 4
    assert "ST_Intersection" in db.calls[1][0]
    assert "ST_UnaryUnion" in db.calls[1][0]
    assert "INSERT INTO productivity_zone_runs" in db.calls[2][0]
    assert "INSERT INTO productivity_zones" in db.calls[3][0]


def test_replayed_run_key_returns_existing_without_duplicate_zone_write():
    db = Session([input_rows(), zone_rows(), [], [{"id": 91}]])
    outcome = service.compute_field(
        db, 5, 3, run_uuid=RUN_UUID, write=True
    )
    assert outcome == {
        "created": False,
        "run_id": 91,
        "run_key": outcome["run_key"],
        "status": "ready",
    }
    assert db.commits == 0
    assert db.rollbacks == 1
    assert len(db.calls) == 4


def test_insufficient_data_persists_no_zone_geometry():
    db = Session([input_rows(seasons=(2025, 2026)), [{"id": 92}]])
    outcome = service.compute_field(
        db, 5, 3, run_uuid=RUN_UUID, write=True
    )
    assert outcome["status"] == "insufficient_data"
    assert db.commits == 1
    assert len(db.calls) == 2
    assert all(
        "INSERT INTO productivity_zones" not in statement
        for statement, _ in db.calls
    )


def test_unknown_role_and_missing_tenant_fail_closed_without_sql():
    for actor in (user("unknown"), user("manager", None)):
        db = Session()
        with pytest.raises(HTTPException) as exc:
            service.get_run(db, actor, 91)
        assert exc.value.status_code == 403
        assert not db.calls
