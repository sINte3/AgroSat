from datetime import date
from threading import Event

import pytest
from shapely.geometry import shape

from services.pixel_anomaly_algorithm import (
    AnomalyContractError,
    AnomalyThresholds,
    HistoricalZone,
    PixelScene,
    analyze_pixel_scenes,
    classify_zones_with_history,
)
from services.pixel_scene_provider import (
    FixturePixelSceneProvider,
    PixelSceneRequest,
    PixelSceneUnavailable,
    SentinelNumericPixelProvider,
    scene_from_fixture,
)


def matrix(value, size=8):
    return tuple(tuple(value for _ in range(size)) for _ in range(size))


def scene(
    *,
    observed_at=date(2026, 6, 20),
    record_id=20,
    values=None,
    cloud_cover_pct=5.0,
    valid_pixels_pct=100.0,
):
    return PixelScene(
        enterprise_id=7,
        field_id=11,
        index_code="ndvi",
        record_type="ndvi_record",
        record_id=record_id,
        observed_at=observed_at,
        values=values or matrix(0.6),
        quality_mask=matrix(True),
        field_mask=matrix(True),
        bbox=(64.0, 39.0, 64.01, 39.01),
        cloud_cover_pct=cloud_cover_pct,
        valid_pixels_pct=valid_pixels_pct,
        provider="fixture",
        provenance={"scene": record_id},
    )


def anomaly_values():
    values = [list(row) for row in matrix(0.6)]
    for row, column in ((2, 2), (2, 3), (3, 2), (3, 3)):
        values[row][column] = 0.2
    return tuple(tuple(row) for row in values)


def comparison_values():
    values = [list(row) for row in matrix(0.62)]
    for row, column in ((2, 2), (2, 3), (3, 2), (3, 3)):
        values[row][column] = 0.55
    return tuple(tuple(row) for row in values)


def test_deterministic_connected_anomaly_is_valid_multipolygon():
    current = scene(values=anomaly_values())
    comparison = scene(
        observed_at=date(2026, 6, 10),
        record_id=10,
        values=comparison_values(),
    )
    first = analyze_pixel_scenes(current, comparison)
    second = analyze_pixel_scenes(current, comparison)
    assert first == second
    assert first.status == "detected"
    assert first.reason_codes == ()
    assert len(first.zones) == 1
    zone = first.zones[0]
    geometry = shape(zone.geometry)
    assert geometry.geom_type == "MultiPolygon"
    assert geometry.is_valid and not geometry.is_empty
    assert zone.area_ha > 0
    assert zone.pixel_count == 4
    assert 0 <= zone.score <= 1
    assert 0 <= zone.confidence <= 1
    assert zone.classification == "single_scene"
    assert len(first.run_key) == len(first.threshold_hash) == len(zone.zone_key) == 64


def test_history_classifies_persistent_and_recovering_without_diagnosis():
    current = scene(values=anomaly_values())
    comparison = scene(
        observed_at=date(2026, 6, 10),
        record_id=10,
        values=comparison_values(),
    )
    zone = analyze_pixel_scenes(current, comparison).zones[0]
    persistent = classify_zones_with_history(
        (zone,),
        (
            HistoricalZone(
                geometry=zone.geometry,
                area_ha=zone.area_ha,
                median_drop=zone.median_drop,
                persistence_count=2,
            ),
        ),
    )
    recovering = classify_zones_with_history(
        (zone,),
        (
            HistoricalZone(
                geometry=zone.geometry,
                area_ha=zone.area_ha * 2,
                median_drop=zone.median_drop,
                persistence_count=4,
            ),
        ),
    )
    assert persistent[0].classification == "persistent"
    assert persistent[0].persistence_count == 3
    assert recovering[0].classification == "recovering"
    assert recovering[0].persistence_count == 5


def test_four_way_connectivity_does_not_join_diagonal_pixels():
    values = [list(row) for row in matrix(0.6)]
    prior = [list(row) for row in matrix(0.62)]
    for row, column in ((1, 1), (2, 2), (3, 3), (4, 4)):
        values[row][column] = 0.1
        prior[row][column] = 0.5
    result = analyze_pixel_scenes(
        scene(values=tuple(tuple(row) for row in values)),
        scene(
            observed_at=date(2026, 6, 10),
            record_id=10,
            values=tuple(tuple(row) for row in prior),
        ),
    )
    assert result.status == "no_anomaly"
    assert result.zones == ()


@pytest.mark.parametrize(
    ("current_changes", "comparison_scene", "reason"),
    [
        ({"cloud_cover_pct": 31.0}, True, "current_cloud_cover"),
        ({"valid_pixels_pct": 59.0}, True, "current_valid_pixels"),
        ({}, False, "comparison_missing"),
    ],
)
def test_quality_gates_return_explicit_insufficient_data(
    current_changes,
    comparison_scene,
    reason,
):
    current = scene(**current_changes)
    comparison = (
        scene(observed_at=date(2026, 6, 10), record_id=10)
        if comparison_scene
        else None
    )
    result = analyze_pixel_scenes(current, comparison)
    assert result.status == "insufficient_data"
    assert reason in result.reason_codes
    assert result.zones == ()


def test_temporal_gate_rejects_wrong_order_and_excessive_age():
    current = scene()
    newer = scene(observed_at=date(2026, 6, 21), record_id=21)
    old = scene(observed_at=date(2026, 1, 1), record_id=1)
    assert (
        analyze_pixel_scenes(current, newer).reason_codes
        == ("temporal_separation_too_short",)
    )
    assert (
        analyze_pixel_scenes(current, old).reason_codes
        == ("temporal_separation_too_long",)
    )


def test_scene_validation_rejects_non_finite_and_unbounded_input():
    values = [list(row) for row in matrix(0.6)]
    values[0][0] = float("nan")
    with pytest.raises(AnomalyContractError, match="finite"):
        analyze_pixel_scenes(
            scene(values=tuple(tuple(row) for row in values)),
            scene(observed_at=date(2026, 6, 10), record_id=10),
        )
    with pytest.raises(AnomalyContractError, match="minimum_separation_days"):
        analyze_pixel_scenes(
            scene(),
            None,
            AnomalyThresholds(minimum_separation_days=0),
        )


def test_live_provider_fails_closed_and_never_substitutes_fixture():
    request = PixelSceneRequest(
        enterprise_id=7,
        field_id=11,
        index_code="ndvi",
        observed_at=date(2026, 6, 20),
        record_type="ndvi_record",
        record_id=20,
    )
    with pytest.raises(PixelSceneUnavailable) as error:
        SentinelNumericPixelProvider().fetch(
            request,
            timeout_seconds=30,
            cancel_event=Event(),
        )
    assert error.value.category == "unsupported_data"


def test_fixture_provider_requires_exact_identity():
    payload = {
        "fixture_id": "phase8-zone-a",
        "enterprise_id": 7,
        "field_id": 11,
        "index_code": "ndvi",
        "record_type": "ndvi_record",
        "record_id": 20,
        "observed_at": "2026-06-20",
        "values": [list(row) for row in anomaly_values()],
        "quality_mask": [list(row) for row in matrix(True)],
        "field_mask": [list(row) for row in matrix(True)],
        "bbox": [64.0, 39.0, 64.01, 39.01],
        "cloud_cover_pct": 5,
        "valid_pixels_pct": 100,
    }
    parsed = scene_from_fixture(payload)
    provider = FixturePixelSceneProvider({(11, date(2026, 6, 20)): parsed})
    exact = PixelSceneRequest(
        enterprise_id=7,
        field_id=11,
        index_code="ndvi",
        observed_at=date(2026, 6, 20),
        record_type="ndvi_record",
        record_id=20,
    )
    assert provider.fetch(exact, timeout_seconds=30, cancel_event=Event()) == parsed
    wrong = PixelSceneRequest(**{**exact.__dict__, "enterprise_id": 8})
    with pytest.raises(PixelSceneUnavailable, match="identity mismatch"):
        provider.fetch(wrong, timeout_seconds=30, cancel_event=Event())
