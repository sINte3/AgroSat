"""Deterministic TASK_219 algorithm, safety-cap, and contract tests."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.autonomous_anomaly_engine import (
    RulePolicy, assess_candidate, cleanup_mask, freshness_status,
    plan_automatic_inspections, robust_signal, stable_keys,
)


NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)


@pytest.mark.parametrize(("days", "expected"), [(0,"FRESH"),(10,"FRESH"),(11,"AGING"),(20,"AGING"),(21,"STALE")])
def test_freshness_uses_last_accepted_observation(days, expected):
    assert freshness_status(NOW - timedelta(days=days), now=NOW) == expected


@pytest.mark.parametrize(("outcome", "expected"), [
    (None,"NEVER_COLLECTED"),("cloud_blocked","CLOUD_BLOCKED"),
    ("provider_degraded","PROVIDER_DEGRADED"),("quality_blocked","QUALITY_BLOCKED"),
])
def test_missing_observation_preserves_explicit_outcome(outcome, expected):
    assert freshness_status(None, now=NOW, last_outcome=outcome) == expected


def test_robust_baseline_is_median_mad_and_rejects_nonfinite():
    signal = robust_signal([0.62,0.60,0.61,0.63,0.59,0.60], 0.30)
    assert signal.baseline_median == 0.605
    assert signal.magnitude == 0.305
    assert signal.robust_deviation > 6
    with pytest.raises(ValueError, match="five"):
        robust_signal([0.5] * 4, 0.2)
    with pytest.raises(ValueError, match="finite"):
        robust_signal([0.5] * 5, float("nan"))


def assessment(**changes):
    values = dict(
        enterprise_id=2, field_id=7, provider="sentinel", index_code="ndvi",
        geometry_hash="a"*64, history=[0.62,0.60,0.61,0.63,0.59,0.60], current=0.20,
        affected_area_ha=1.0, field_area_ha=20.0, persistence_scenes=2,
        supporting_agreement=3, data_quality=0.95, acquired_at=NOW,
    )
    values.update(changes)
    return assess_candidate(**values)


def test_high_persistent_candidate_is_explainable_and_automatic():
    item = assessment()
    assert item is not None and item.eligible_for_automatic_inspection
    assert item.severity in {"HIGH", "EXTREME"}
    assert "robust deviations" in item.explanation
    assert "supporting indices" in item.explanation
    assert item.cooldown_until == NOW + timedelta(days=14)


def test_area_boundary_uses_safer_point_25_or_one_percent():
    assert assessment(affected_area_ha=0.24, field_area_ha=10) is None
    assert assessment(affected_area_ha=0.99, field_area_ha=100) is None
    assert assessment(affected_area_ha=1.0, field_area_ha=100) is not None


def test_normal_action_requires_two_scenes_but_extreme_path_is_explicit():
    moderate_single = assessment(current=0.45, persistence_scenes=1, supporting_agreement=4)
    assert moderate_single is not None
    assert not moderate_single.eligible_for_automatic_inspection
    extreme_single = assessment(current=0.0, persistence_scenes=1, supporting_agreement=4, data_quality=1)
    assert extreme_single is not None and extreme_single.eligible_for_automatic_inspection
    assert "extreme single valid scene" in extreme_single.explanation


def test_cleanup_removes_edge_nodata_and_small_components():
    candidate = [[False]*7 for _ in range(7)]
    valid = [[True]*7 for _ in range(7)]
    candidate[0][2] = True
    candidate[1][1] = True
    valid[1][1] = False
    for row,col in ((2,2),(2,3),(3,2),(3,3)):
        candidate[row][col] = True
    candidate[5][5] = True
    cleaned = cleanup_mask(candidate, valid, minimum_pixels=4)
    assert sum(sum(row) for row in cleaned) == 4
    assert not cleaned[0][2] and not cleaned[1][1] and not cleaned[5][5]


def candidates(count, *, fields=None):
    fields = fields or list(range(1, count + 1))
    return [
        {"id":index+1,"field_id":fields[index],"automatic":True,
         "confidence":0.99-index/1000,"source_key":f"{index:064x}",
         "zone_key":f"{index+100:064x}","cooldown_until":NOW-timedelta(days=1)}
        for index in range(count)
    ]


def test_auto_creation_cap_is_20_and_deterministic():
    plan = plan_automatic_inspections(candidates(25), active_field_count=275, now=NOW)
    assert plan.candidate_ids == tuple(range(1,21))
    assert plan.suppressed_cap_ids == tuple(range(21,26))
    assert not plan.spike_guard_triggered


def test_spike_guard_is_strictly_more_than_ten_percent():
    assert not plan_automatic_inspections(candidates(10), active_field_count=100, now=NOW).spike_guard_triggered
    plan = plan_automatic_inspections(candidates(11), active_field_count=100, now=NOW)
    assert plan.spike_guard_triggered and plan.candidate_ids == ()


def test_open_source_zone_is_deduplicated():
    items = candidates(2, fields=[1,2])
    existing = {(items[0]["source_key"],items[0]["zone_key"])}
    plan = plan_automatic_inspections(items, active_field_count=50, open_source_zone_keys=existing, now=NOW)
    assert plan.candidate_ids == (2,) and plan.suppressed_duplicate_ids == (1,)


def test_keys_are_stable_and_bound_to_tenant_field_provider_rule():
    first = stable_keys(enterprise_id=1,field_id=2,provider="sentinel",index_code="ndvi",geometry_hash="b"*64)
    assert first == stable_keys(enterprise_id=1,field_id=2,provider="sentinel",index_code="ndvi",geometry_hash="b"*64)
    assert first != stable_keys(enterprise_id=2,field_id=2,provider="sentinel",index_code="ndvi",geometry_hash="b"*64)


def test_migration_and_canonical_collector_contracts_are_single_head_ready():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "alembic/versions/0014_autonomous_satellite_monitoring.py").read_text("utf-8")
    collector = (root / "scripts/collect_satellite.py").read_text("utf-8")
    assert 'revision: str = "0014_autonomous_satellite_monitoring"' in migration
    assert 'down_revision: Union[str, Sequence[str], None] = "0013_anomaly_inspection_workflow"' in migration
    assert "pg_try_advisory_lock" in (root / "services/autonomous_monitoring.py").read_text("utf-8")
    assert 'default=2' in collector and 'default=21600' in collector
    assert "APScheduler" not in collector
