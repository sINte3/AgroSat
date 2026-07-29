"""Deterministic tests for measured-yield productivity zones."""

from services.productivity_zone_algorithm import (
    YieldMeasurement,
    analyze_productivity,
    result_payload,
)


def measurements(seasons=(2024, 2025, 2026), per_season=24):
    result = []
    for season_index, season in enumerate(seasons):
        for index in range(per_season):
            column = index % 6
            row = index // 6
            result.append(YieldMeasurement(
                import_id=100 + season_index,
                source_sha256=f"{season_index + 1:064x}",
                season_year=season,
                longitude=64.42 + column * 0.00035,
                latitude=39.77 + row * 0.00027,
                yield_t_ha=3.0 + column * 0.35 + row * 0.1 + season_index * 0.05,
            ))
    return result


def test_ready_fixture_is_reproducible_and_has_stable_provenance():
    first = analyze_productivity(7, measurements())
    second = analyze_productivity(7, list(reversed(measurements())))
    assert result_payload(first) == result_payload(second)
    assert first.status == "ready"
    assert len(first.run_key) == 64
    assert first.selected_seasons == (2024, 2025, 2026)
    assert first.source_import_ids == (100, 101, 102)
    assert 0 < first.confidence <= 1
    assert first.cells


def test_cells_are_bounded_valid_closed_polygons_and_tercile_classes():
    result = analyze_productivity(7, measurements())
    classes = {cell.zone_class for cell in result.cells}
    assert classes == {"low", "medium", "high"}
    for cell in result.cells:
        assert 0 <= cell.score <= 1
        assert len(cell.seasons) >= 2
        assert len(cell.polygon) == 5
        assert cell.polygon[0] == cell.polygon[-1]
        for longitude, latitude in cell.polygon:
            assert -180 <= longitude <= 180
            assert -90 <= latitude <= 90


def test_sparse_history_returns_explicit_insufficient_data_without_geometry():
    result = analyze_productivity(7, measurements(seasons=(2025, 2026)))
    assert result.status == "insufficient_data"
    assert "minimum_seasons_not_met" in result.reason_codes
    assert result.cells == ()
    assert result.confidence == 0


def test_sparse_season_returns_explicit_reason():
    result = analyze_productivity(7, measurements(per_season=19))
    assert result.status == "insufficient_data"
    assert "minimum_points_per_season_not_met" in result.reason_codes
    assert "minimum_total_points_not_met" in result.reason_codes


def test_zero_mad_returns_explicit_reason_without_fabricated_zones():
    fixture = [
        YieldMeasurement(
            import_id=100 + season_index,
            source_sha256=f"{season_index + 1:064x}",
            season_year=season,
            longitude=64.42 + (index % 5) * 0.00035,
            latitude=39.77 + (index // 5) * 0.00027,
            yield_t_ha=4.0,
        )
        for season_index, season in enumerate((2024, 2025, 2026))
        for index in range(20)
    ]
    result = analyze_productivity(7, fixture)
    assert result.status == "insufficient_data"
    assert set(result.reason_codes) == {
        "zero_mad_season_2024",
        "zero_mad_season_2025",
        "zero_mad_season_2026",
    }
    assert result.cells == ()


def test_input_validation_and_batch_bound_are_fail_closed():
    bad = measurements()
    bad[0] = YieldMeasurement(100, "a" * 64, 2024, 181, 39.77, 4)
    try:
        analyze_productivity(7, bad)
    except ValueError as exc:
        assert "invalid measured-yield input" in str(exc)
    else:
        raise AssertionError("invalid longitude must fail")
