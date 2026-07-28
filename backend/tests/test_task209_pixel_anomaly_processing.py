from datetime import date, datetime, timezone

import pytest

from services.pixel_anomaly_algorithm import (
    AnomalyThresholds,
    PixelScene,
    analyze_pixel_scenes,
)
from services.pixel_anomaly_processing import (
    AnomalyPersistenceError,
    persist_anomaly_result,
)


class Result:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows


class RecordingDB:
    def __init__(self, *, replay=False, history=None, fail_zone=False):
        self.replay = replay
        self.history = history or []
        self.fail_zone = fail_zone
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "ST_AsGeoJSON" in sql:
            return Result(rows=self.history)
        if "INSERT INTO pixel_anomaly_runs" in sql:
            return Result(scalar=None if self.replay else 44)
        if "SELECT id" in sql and "pixel_anomaly_runs" in sql:
            return Result(scalar=44)
        if "INSERT INTO pixel_anomalies" in sql and self.fail_zone:
            raise RuntimeError("isolated fixture failure")
        return Result()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def grid(value):
    return tuple(tuple(value for _ in range(8)) for _ in range(8))


def scenes(provider="sentinel_numeric_pixels"):
    current_values = [list(row) for row in grid(0.6)]
    comparison_values = [list(row) for row in grid(0.62)]
    for row, column in ((2, 2), (2, 3), (3, 2), (3, 3)):
        current_values[row][column] = 0.2
        comparison_values[row][column] = 0.55

    def make(observed_at, record_id, values):
        return PixelScene(
            enterprise_id=7,
            field_id=11,
            index_code="ndvi",
            record_type="ndvi_record",
            record_id=record_id,
            observed_at=observed_at,
            values=tuple(tuple(row) for row in values),
            quality_mask=grid(True),
            field_mask=grid(True),
            bbox=(64.0, 39.0, 64.01, 39.01),
            cloud_cover_pct=5,
            valid_pixels_pct=100,
            provider=provider,
            provenance={"record_id": record_id},
        )

    return (
        make(date(2026, 6, 20), 20, current_values),
        make(date(2026, 6, 10), 10, comparison_values),
    )


def analyzed(provider="sentinel_numeric_pixels"):
    return analyze_pixel_scenes(*scenes(provider))


def test_persistence_uses_one_history_query_one_batch_insert_and_one_commit():
    db = RecordingDB()
    thresholds = AnomalyThresholds()
    outcome = persist_anomaly_result(
        db,
        analyzed(),
        thresholds,
        started_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        processing_run_id="15047b35-1095-47e2-bba0-6c6943af8b41",
        finished_at=datetime(2026, 6, 20, 0, 0, 1, tzinfo=timezone.utc),
    )
    assert outcome.run_id == 44
    assert outcome.inserted is True
    assert outcome.zone_count == 1
    assert db.commits == 1
    assert db.rollbacks == 0
    assert len(db.calls) == 3
    assert sum("INSERT INTO pixel_anomalies" in sql for sql, _ in db.calls) == 1
    zone_params = next(
        params for sql, params in db.calls if "INSERT INTO pixel_anomalies" in sql
    )
    assert isinstance(zone_params, list) and len(zone_params) == 1
    assert zone_params[0]["enterprise_id"] == 7
    assert zone_params[0]["field_id"] == 11
    assert zone_params[0]["classification"] == "single_scene"


def test_replay_returns_existing_run_without_duplicate_zones():
    db = RecordingDB(replay=True)
    outcome = persist_anomaly_result(
        db,
        analyzed(),
        AnomalyThresholds(),
        started_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        processing_run_id="15047b35-1095-47e2-bba0-6c6943af8b41",
    )
    assert outcome.run_id == 44
    assert outcome.inserted is False
    assert outcome.zone_count == 0
    assert db.commits == 1
    assert db.rollbacks == 0
    assert not any("INSERT INTO pixel_anomalies" in sql for sql, _ in db.calls)


def test_history_classification_is_applied_before_zone_batch():
    result = analyzed()
    zone = result.zones[0]
    db = RecordingDB(
        history=[
            {
                "geometry": zone.geometry,
                "area_ha": zone.area_ha * 2,
                "median_drop": zone.median_drop,
                "persistence_count": 3,
            }
        ]
    )
    persist_anomaly_result(
        db,
        result,
        AnomalyThresholds(),
        started_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        processing_run_id="15047b35-1095-47e2-bba0-6c6943af8b41",
    )
    zone_params = next(
        params for sql, params in db.calls if "INSERT INTO pixel_anomalies" in sql
    )
    assert zone_params[0]["classification"] == "recovering"
    assert zone_params[0]["persistence_count"] == 4


def test_fixture_apply_fails_before_database_access():
    db = RecordingDB()
    with pytest.raises(AnomalyPersistenceError, match="fixture"):
        persist_anomaly_result(
            db,
            analyzed("deterministic_fixture"),
            AnomalyThresholds(),
            started_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
            processing_run_id="15047b35-1095-47e2-bba0-6c6943af8b41",
        )
    assert db.calls == []
    assert db.commits == db.rollbacks == 0


def test_transaction_rolls_back_if_zone_insert_fails():
    db = RecordingDB(fail_zone=True)
    with pytest.raises(RuntimeError, match="isolated fixture failure"):
        persist_anomaly_result(
            db,
            analyzed(),
            AnomalyThresholds(),
            started_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
            processing_run_id="15047b35-1095-47e2-bba0-6c6943af8b41",
        )
    assert db.commits == 0
    assert db.rollbacks == 1


def test_threshold_mismatch_fails_before_database_access():
    db = RecordingDB()
    with pytest.raises(AnomalyPersistenceError, match="fingerprint"):
        persist_anomaly_result(
            db,
            analyzed(),
            AnomalyThresholds(within_field_delta=0.2),
            started_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
            processing_run_id="15047b35-1095-47e2-bba0-6c6943af8b41",
        )
    assert db.calls == []
