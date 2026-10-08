"""TASK_246: field-level Sentinel statistics integrity (blocker B7 of TASK_245).

Offline unit coverage, grouped by the task's requirements:
  A  metric request grid (CRS, resolution, polygon preserved)
  B  provider sample counts -> field-level validity
  C  missing values never become 0; genuine zero stays zero
  D  NDVI field-mean extremes
  E  acquisition identity: P1D dates, no duplicate logical observations,
     duplicates are not persistence
  F  NDVI and supporting indices share one footprint implementation
Every provider response here is synthetic; nothing contacts a network or a database.
"""

from __future__ import annotations

import ast
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import observation_quality as oq  # noqa: E402
from services import sentinel_grid as grid_module  # noqa: E402
from services.sentinel_grid import (  # noqa: E402
    SentinelGridError,
    metric_request_grid,
    utm_epsg_for_lon_lat,
)

# A ~4.6 ha irregular pentagon around the TASK_245 field 246 interior point
# (40.00338 N, 64.44036 E). Representative shape only, not a production record.
FIELD_246_LIKE = {
    "type": "Polygon",
    "coordinates": [[
        [64.4390, 40.0026], [64.4418, 40.0025], [64.4419, 40.0041],
        [64.4402, 40.0046], [64.4389, 40.0039], [64.4390, 40.0026],
    ]],
}
UTM_41N = "http://www.opengis.net/def/crs/EPSG/0/32641"
WGS84 = "http://www.opengis.net/def/crs/EPSG/0/4326"


def _geodesic_area_m2(geometry: dict) -> float:
    from pyproj import Geod
    from shapely.geometry import shape

    area, _ = Geod(ellps="WGS84").geometry_area_perimeter(shape(geometry))
    return abs(area)


# ─── A. Metric request grid ──────────────────────────────────────────────────

def test_bukhara_field_near_64_44e_selects_utm_41n_epsg_32641():
    assert utm_epsg_for_lon_lat(64.44036, 40.00338) == 32641
    grid = metric_request_grid(FIELD_246_LIKE, 10)
    assert grid.epsg == 32641
    assert grid.crs == UTM_41N


@pytest.mark.parametrize(("lon", "lat", "expected"), [
    (59.999999, 40.0, 32640),   # last point of zone 40
    (60.0, 40.0, 32641),        # boundary belongs to the zone east of it
    (65.999999, 40.0, 32641),
    (66.0, 40.0, 32642),
    (-180.0, 10.0, 32601),
    (180.0, 10.0, 32660),       # 180 degrees stays in zone 60
    (64.44, -0.0001, 32741),    # southern hemisphere
    (64.44, 0.0, 32641),        # equator is north
    (3.0, 84.0, 32631),         # northern UTM limit
    (3.0, -80.0, 32731),        # southern UTM limit
])
def test_utm_zone_boundaries(lon, lat, expected):
    assert utm_epsg_for_lon_lat(lon, lat) == expected


@pytest.mark.parametrize(("lon", "lat"), [
    (181.0, 40.0), (-180.5, 40.0), (64.0, 84.5), (64.0, -80.5), (math.nan, 40.0), ("x", 40.0),
])
def test_utm_zone_rejects_points_outside_the_utm_definition(lon, lat):
    with pytest.raises(SentinelGridError):
        utm_epsg_for_lon_lat(lon, lat)


def test_transformed_geometry_is_metric_and_keeps_the_whole_polygon():
    grid = metric_request_grid(FIELD_246_LIKE, 10)
    ring = grid.geometry["coordinates"][0]
    source_ring = FIELD_246_LIKE["coordinates"][0]
    assert grid.geometry["type"] == "Polygon"
    assert len(ring) == len(source_ring) == grid.source_vertex_count   # vertex for vertex
    for easting, northing in ring:                                      # UTM metres, zone 41N
        assert 166_000 < easting < 834_000
        assert 4_400_000 < northing < 4_450_000
    geodesic = _geodesic_area_m2(FIELD_246_LIKE)
    assert grid.area_m2 == pytest.approx(geodesic, rel=0.005)           # same polygon, metric units
    assert 40_000 < grid.area_m2 < 52_000                               # ~4.6 ha
    assert grid.expected_field_pixels == pytest.approx(grid.area_m2 / 100.0)
    json.dumps(grid.geometry)                                           # JSON-serialisable lists


def test_grid_refuses_points_projected_or_invalid_geometry_and_non_metric_resolution():
    with pytest.raises(SentinelGridError):
        metric_request_grid({"type": "Point", "coordinates": [64.44, 40.0]}, 10)
    with pytest.raises(SentinelGridError):  # already-projected coordinates are not EPSG:4326
        metric_request_grid({"type": "Polygon", "coordinates": [[[500000, 4428000], [500200, 4428000],
                                                                 [500200, 4428200], [500000, 4428000]]]}, 10)
    with pytest.raises(SentinelGridError):  # self-intersecting bow tie
        metric_request_grid({"type": "Polygon", "coordinates": [[[64.44, 40.0], [64.45, 40.01],
                                                                 [64.45, 40.0], [64.44, 40.01], [64.44, 40.0]]]}, 10)
    for resolution in (0, 1, 15, 60, True, 10.5):
        with pytest.raises(SentinelGridError):
            metric_request_grid(FIELD_246_LIKE, resolution)


def test_ndvi_request_uses_a_10_metre_utm_grid_and_never_epsg4326_degrees():
    from services.satellite import build_ndvi_statistical_payload

    payload = build_ndvi_statistical_payload(FIELD_246_LIKE, date(2026, 9, 24), date(2026, 10, 8))
    assert payload["input"]["bounds"]["properties"]["crs"] == UTM_41N
    assert (payload["aggregation"]["resx"], payload["aggregation"]["resy"]) == (10, 10)
    assert payload["aggregation"]["aggregationInterval"] == {"of": "P1D"}
    assert payload["input"]["bounds"]["geometry"] == metric_request_grid(FIELD_246_LIKE, 10).geometry
    assert WGS84 not in json.dumps(payload)


def test_ndvi_request_accepts_the_wkt_the_collector_passes():
    from shapely.geometry import shape
    from services.satellite import build_ndvi_statistical_payload

    from_wkt = build_ndvi_statistical_payload(shape(FIELD_246_LIKE).wkt, date(2026, 9, 24), date(2026, 10, 8))
    from_geojson = build_ndvi_statistical_payload(FIELD_246_LIKE, date(2026, 9, 24), date(2026, 10, 8))
    assert from_wkt == from_geojson


def test_supporting_index_request_uses_a_20_metre_utm_grid_and_never_epsg4326_degrees():
    from services.satellite_collection import build_statistical_payload
    from services.satellite_indices import build_multi_index_evalscript

    codes = ["savi", "evi", "ndmi", "ndre"]
    payload = build_statistical_payload(FIELD_246_LIKE, build_multi_index_evalscript(codes), codes,
                                        date(2026, 9, 24), date(2026, 10, 8))
    assert payload["input"]["bounds"]["properties"]["crs"] == UTM_41N
    assert (payload["aggregation"]["resx"], payload["aggregation"]["resy"]) == (20, 20)
    assert WGS84 not in json.dumps(payload)


@pytest.mark.parametrize("module_name", [
    "scripts.dry_run_multi_index_single_field",
    "scripts.preview_multi_index_batch",
    "scripts.write_multi_index_batch",
    "scripts.write_multi_index_single_field",
])
def test_legacy_multi_index_tools_delegate_to_the_metric_request(module_name):
    import importlib
    from services.satellite_indices import build_multi_index_evalscript

    module = importlib.import_module(module_name)
    builder = getattr(module, "build_statistical_payload", None) or getattr(module, "_build_statistical_payload")
    payload = builder(FIELD_246_LIKE, build_multi_index_evalscript(["savi"]), ["savi"],
                      date(2026, 9, 24), date(2026, 10, 8))
    assert payload["input"]["bounds"]["properties"]["crs"] == UTM_41N
    assert payload["aggregation"]["resx"] == 20
    assert payload["aggregation"]["aggregationInterval"]["of"] in {"P1D", "P5D"}


def test_the_batch_writer_no_longer_stores_five_day_bucket_starts():
    from scripts import write_multi_index_batch as writer
    from services.satellite_indices import build_multi_index_evalscript

    payload = writer._build_statistical_payload(FIELD_246_LIKE, build_multi_index_evalscript(["savi"]),
                                                ["savi"], date(2026, 9, 24), date(2026, 10, 8))
    assert payload["aggregation"]["aggregationInterval"] == {"of": "P1D"}


# ─── B. Provider sample counts ───────────────────────────────────────────────

def _ndvi_interval(day_from, day_to, *, sample_count=120, no_data_count=24, field_no_data=0,
                   mean=0.62, extra=None, drop=()):
    stats = {"sampleCount": sample_count, "noDataCount": no_data_count, "mean": mean,
             "min": 0.31, "max": 0.84, "stDev": 0.07, "percentiles": {"10.0": 0.52, "90.0": 0.71}}
    stats.update(extra or {})
    for key in drop:
        stats.pop(key, None)
    return {
        "interval": {"from": f"{day_from}T00:00:00Z", "to": f"{day_to}T00:00:00Z"},
        "outputs": {
            "ndvi": {"bands": {"B0": {"stats": stats}}},
            "observed": {"bands": {"B0": {"stats": {"sampleCount": sample_count, "noDataCount": field_no_data}}}},
        },
    }


def _service():
    from services.satellite import SentinelHubService

    return SentinelHubService("fixture-id", "fixture-secret", provider="cdse")


def test_parser_returns_the_provider_counts_120_minus_24_is_96_valid_80_percent():
    result = _service()._parse_stats_response({"data": [_ndvi_interval("2026-10-03", "2026-10-04")]})
    assert result["sample_count"] == 120
    assert result["no_data_count"] == 24
    assert result["valid_pixel_count"] == 96
    assert result["field_pixel_count"] == 120
    assert result["valid_pixels_pct"] == 80.0
    assert result["field_samples_accepted"] is True
    assert result["std_ndvi"] == 0.07 and result["p10_ndvi"] == 0.52 and result["p90_ndvi"] == 0.71


def test_valid_share_is_relative_to_the_field_polygon_not_its_bounding_box():
    # 120-pixel bounding box, 20 pixels outside the polygon, 4 more cloud-masked.
    result = _service()._parse_stats_response(
        {"data": [_ndvi_interval("2026-10-03", "2026-10-04", no_data_count=24, field_no_data=20)]})
    assert result["field_pixel_count"] == 100
    assert result["valid_pixel_count"] == 96
    assert result["valid_pixels_pct"] == 96.0


def test_a_one_pixel_grid_is_never_a_field_statistic():
    verdict = oq.evaluate_field_samples(sample_count=1, no_data_count=0, field_pixel_count=1)
    assert not verdict.accepted and verdict.reason == oq.SAMPLES_POINT
    result = _service()._parse_stats_response(
        {"data": [_ndvi_interval("2026-10-03", "2026-10-04", sample_count=1, no_data_count=0)]})
    assert result["field_samples_accepted"] is False
    assert result["field_samples_reason"] == oq.SAMPLES_POINT
    assert oq.field_sample_gate(result) == (False, "field-level sample contract not met (single_pixel_grid)")


def test_the_pre_task246_single_pixel_shape_is_quality_blocked_by_the_collector():
    from scripts import collect_ndvi

    old_shape = {"satellite": "Sentinel-2", "captured_date": "2026-10-03", "mean_ndvi": 0.6379,
                 "min_ndvi": 0.6379, "max_ndvi": 0.6379, "std_ndvi": 0.0, "valid_pixels_pct": 100.0,
                 "sample_count": 1, "no_data_count": 0}
    _, blocked = collect_ndvi.validate_observation(old_shape, date(2026, 9, 24), date(2026, 10, 8))
    assert blocked is True


@pytest.mark.parametrize(("valid", "field", "accepted", "reason"), [
    (9, 12, False, oq.SAMPLES_TOO_FEW_VALID),
    (10, 20, True, oq.SAMPLES_ACCEPTED),           # exactly the 50 % analysis tier
    (10, 21, False, oq.SAMPLES_PARTIAL_COVERAGE),
    (40, 120, False, oq.SAMPLES_PARTIAL_COVERAGE),
    (96, 120, True, oq.SAMPLES_ACCEPTED),
    (5, 9, False, oq.SAMPLES_FIELD_TOO_SMALL),
])
def test_minimum_valid_sample_and_share_rule(valid, field, accepted, reason):
    verdict = oq.evaluate_field_samples(sample_count=120, no_data_count=120 - valid, field_pixel_count=field)
    assert (verdict.accepted, verdict.reason) == (accepted, reason)
    assert oq.MIN_FIELD_VALID_SHARE_PCT == oq.MIN_VALID_PIXELS_ANALYSIS_PCT == 50.0


def test_inconsistent_counts_are_rejected():
    for sc, nd, field in ((10, 11, 10), (120, 0, 130), (120, 10, 100)):
        verdict = oq.evaluate_field_samples(sample_count=sc, no_data_count=nd, field_pixel_count=field)
        assert verdict.accepted is False and verdict.reason == oq.SAMPLES_INCONSISTENT, (sc, nd, field)


def test_field_pixels_are_the_largest_observed_footprint_floored_by_the_polygon_area():
    assert oq.field_pixel_count([316, 527, None, 38]) == 527
    assert oq.field_pixel_count([316, 527, 38], expected_field_pixels=462.7) == 527
    assert oq.field_pixel_count([38], expected_field_pixels=462.7) == 463    # only a partial swath seen
    assert oq.field_pixel_count([None]) is None
    assert oq.observed_pixel_count(1406, 879) == 527
    assert oq.observed_pixel_count(10, 11) is None and oq.observed_pixel_count(None, 0) is None


def _live_like(day, observed, *, mean):
    """Counts of the TASK_246 live probe (field 246, 10 m grid of 1406 pixels)."""
    return _ndvi_interval(day, f"{day[:8]}{int(day[8:]) + 1:02d}", sample_count=1406,
                          no_data_count=1406 - observed, field_no_data=1406 - observed, mean=mean)


def test_a_later_partial_swath_acquisition_cannot_displace_a_complete_one():
    complete = _live_like("2026-10-03", 527, mean=0.6886)
    partial = _live_like("2026-10-06", 38, mean=0.7539)
    result = _service()._parse_stats_response({"data": [complete, partial]}, expected_field_pixels=462.7)
    assert result["captured_date"] == "2026-10-03"
    assert (result["valid_pixel_count"], result["field_pixel_count"]) == (527, 527)
    assert result["valid_pixels_pct"] == 100.0 and result["field_samples_accepted"] is True


def test_when_only_a_partial_swath_is_seen_the_polygon_area_is_the_field():
    partial = _live_like("2026-10-06", 38, mean=0.7539)
    result = _service()._parse_stats_response({"data": [partial]}, expected_field_pixels=462.7)
    assert result["field_pixel_count"] == 463
    assert result["valid_pixels_pct"] == pytest.approx(8.2)
    assert result["field_samples_accepted"] is False
    assert result["field_samples_reason"] == oq.SAMPLES_PARTIAL_COVERAGE


def test_latest_trustworthy_interval_wins_over_a_newer_point_or_cloud_sample():
    older = _ndvi_interval("2026-10-01", "2026-10-02", mean=0.61)
    newer_cloudy = _ndvi_interval("2026-10-03", "2026-10-04", no_data_count=115, mean=0.20)
    result = _service()._parse_stats_response({"data": [newer_cloudy, older]})
    assert result["captured_date"] == "2026-10-01"
    assert result["field_samples_accepted"] is True


def test_when_nothing_is_trustworthy_the_latest_observed_interval_is_returned_blocked():
    only_cloudy = _ndvi_interval("2026-10-03", "2026-10-04", no_data_count=115)
    result = _service()._parse_stats_response({"data": [only_cloudy]})
    assert result["captured_date"] == "2026-10-03"
    assert result["valid_pixel_count"] == 5
    assert result["field_samples_accepted"] is False
    assert result["field_samples_reason"] == oq.SAMPLES_TOO_FEW_VALID


def _multi_interval(day, *, sample_count=120, no_data_count=24, field_no_data=0, values=None, drop=()):
    values = values or {"savi": 0.41, "evi": 0.36, "ndmi": 0.18, "ndre": 0.29}
    outputs = {}
    for code, mean in values.items():
        stats = {"sampleCount": sample_count, "noDataCount": no_data_count, "mean": mean,
                 "min": mean - 0.2, "max": mean + 0.2, "stDev": 0.05,
                 "percentiles": {"10.0": mean - 0.08, "90.0": mean + 0.08}}
        for key in drop:
            stats.pop(key, None)
        outputs[code] = {"bands": {"B0": {"stats": stats}}}
    outputs["observed"] = {"bands": {"B0": {"stats": {"sampleCount": sample_count, "noDataCount": field_no_data}}}}
    return {"interval": {"from": f"{day}T00:00:00Z", "to": f"{day[:8]}{int(day[8:]) + 1:02d}T00:00:00Z"},
            "outputs": outputs}


def test_supporting_index_parser_uses_the_same_sample_semantics():
    from services.satellite_indices import parse_multi_index_stats_response_flat

    records = parse_multi_index_stats_response_flat({"data": [_multi_interval("2026-10-03")]},
                                                    ["savi", "evi", "ndmi", "ndre"])
    assert len(records) == 4
    for record in records:
        assert (record["sample_count"], record["no_data_count"]) == (120, 24)
        assert record["valid_pixel_count"] == 96 and record["valid_pixels_pct"] == 80.0
        assert record["field_samples_accepted"] is True
    point = parse_multi_index_stats_response_flat({"data": [_multi_interval("2026-10-03", sample_count=1, no_data_count=0)]},
                                                  ["savi"])
    assert point[0]["field_samples_accepted"] is False


# ─── C. Missing values, zero and no-data ─────────────────────────────────────

@pytest.mark.parametrize("missing", ["mean", "min", "max", "stDev"])
def test_absent_ndvi_statistics_make_the_interval_unusable_never_zero(missing):
    result = _service()._parse_stats_response({"data": [_ndvi_interval("2026-10-03", "2026-10-04", drop=(missing,))]})
    assert result is None


def test_missing_percentiles_stay_missing():
    interval = _ndvi_interval("2026-10-03", "2026-10-04", extra={"percentiles": {}})
    result = _service()._parse_stats_response({"data": [interval]})
    assert result["p10_ndvi"] is None and result["p90_ndvi"] is None


@pytest.mark.parametrize("missing", ["sampleCount", "noDataCount"])
def test_missing_counts_are_not_fake_100_percent_valid(missing):
    result = _service()._parse_stats_response({"data": [_ndvi_interval("2026-10-03", "2026-10-04", drop=(missing,))]})
    assert result is None
    verdict = oq.evaluate_field_samples(sample_count=None if missing == "sampleCount" else 120,
                                        no_data_count=None if missing == "noDataCount" else 0,
                                        field_pixel_count=120)
    assert verdict.reason == oq.SAMPLES_MISSING and verdict.valid_pixels_pct is None


def test_missing_observed_footprint_output_is_not_a_field_statistic():
    interval = _ndvi_interval("2026-10-03", "2026-10-04")
    del interval["outputs"]["observed"]
    assert _service()._parse_stats_response({"data": [interval]}) is None
    # Not even when the polygon area alone could stand in for the footprint.
    assert _service()._parse_stats_response({"data": [interval]}, expected_field_pixels=100.0) is None


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", float("nan"), float("inf")])
def test_non_finite_statistics_are_rejected(bad):
    result = _service()._parse_stats_response(
        {"data": [_ndvi_interval("2026-10-03", "2026-10-04", extra={"mean": bad})]})
    assert result is None
    from services.satellite_indices import parse_multi_index_stats_response_flat

    interval = _multi_interval("2026-10-03")
    interval["outputs"]["ndmi"]["bands"]["B0"]["stats"]["mean"] = bad
    codes = [record["index_code"] for record in parse_multi_index_stats_response_flat({"data": [interval]}, ["ndmi", "savi"])]
    assert codes == ["savi"]


def test_a_no_data_day_produces_no_record_instead_of_zero():
    from services.satellite_indices import parse_multi_index_stats_response_flat

    no_data_day = _multi_interval("2026-10-06", no_data_count=120, values={"savi": 0.0, "ndmi": 0.0})
    assert parse_multi_index_stats_response_flat({"data": [no_data_day]}, ["savi", "ndmi"]) == []


def test_a_genuine_zero_index_from_many_valid_pixels_stays_a_real_zero():
    from services.satellite_collection import filter_by_quality
    from services.satellite_indices import parse_multi_index_stats_response_flat, validate_index_quality

    records = parse_multi_index_stats_response_flat(
        {"data": [_multi_interval("2026-10-03", values={"ndmi": 0.0})]}, ["ndmi"])
    assert len(records) == 1 and records[0]["mean_value"] == 0.0
    assert records[0]["field_samples_accepted"] is True
    assert validate_index_quality("ndmi", 0.0, valid_pixels_pct=records[0]["valid_pixels_pct"]) == (True, "ok")
    assert "ndmi" in filter_by_quality({"ndmi": records[0]}, ["ndmi"])


def test_supporting_index_evalscript_uses_the_ndvi_mask_contract():
    from services.satellite import NDVI_EVALSCRIPT, NDVI_INVALID_SCL_CLASSES
    from services.satellite_indices import build_multi_index_evalscript

    script = build_multi_index_evalscript(["savi", "evi", "ndmi", "ndre"])
    excluded = "[" + ",".join(str(value) for value in NDVI_INVALID_SCL_CLASSES) + "]"
    assert 0 in NDVI_INVALID_SCL_CLASSES                       # NO_DATA is excluded
    for text in (script, NDVI_EVALSCRIPT):
        assert "sample.dataMask === 1" in text
        assert excluded in text
    assert '"dataMask"' in script.split("output:")[0]          # input dataMask is read
    assert 'id: "dataMask", bands: ["savi", "evi", "ndmi", "ndre", "observed"]' in script
    assert "observed: [sample.dataMask]" in script
    assert ("dataMask: [isValid ? 1 : 0, isValid ? 1 : 0, isValid ? 1 : 0, isValid ? 1 : 0, sample.dataMask]"
            in script)
    assert 'id: "dataMask", bands: ["ndvi", "observed"]' in NDVI_EVALSCRIPT
    # A constant mask is not clipped to the polygon (TASK_246 live probe): never used.
    assert "[1]" not in script and "[1]" not in NDVI_EVALSCRIPT


def test_write_paths_fail_closed_without_the_parser_sample_verdict():
    from services.satellite_collection import filter_by_quality
    from scripts import write_multi_index_batch as batch

    record = {"captured_date": "2026-10-03", "index_code": "savi", "mean_value": 0.4,
              "min_value": 0.2, "max_value": 0.6, "std_value": 0.05, "valid_pixels_pct": 100.0}
    assert oq.field_sample_gate(record)[0] is False
    assert filter_by_quality({"savi": record}, ["savi"]) == {}
    assert batch._prepare_records({"savi": record}) == []


# ─── D. NDVI field-mean extremes ─────────────────────────────────────────────

@pytest.mark.parametrize(("value", "accepted"), [
    (1.0, False), (0.9501, False), (0.95, True), (0.62, True), (0.02, True),
    (0.0, False), (-0.1, False), (1.2, False), (math.nan, False), (math.inf, False),
])
def test_ndvi_field_mean_admission_range(value, accepted):
    from services.satellite import validate_ndvi_quality

    assert validate_ndvi_quality(value)[0] is accepted


def test_an_exact_1_000_field_mean_is_kept_by_the_parser_but_blocked_on_write():
    from scripts import collect_ndvi

    result = _service()._parse_stats_response(
        {"data": [_ndvi_interval("2026-10-03", "2026-10-04", extra={"mean": 1.0})]})
    assert result["mean_ndvi"] == 1.0                        # parser reports, never rewrites
    _, blocked = collect_ndvi.validate_observation(result, date(2026, 9, 24), date(2026, 10, 8))
    assert blocked is True


# ─── E. Acquisition identity ─────────────────────────────────────────────────

def test_the_ndvi_service_default_is_one_utc_day_per_interval():
    import inspect
    from services.satellite import SentinelHubService

    default = inspect.signature(SentinelHubService.get_ndvi_stats).parameters["aggregation_interval"].default
    assert default == "P1D"


def test_p1d_captured_date_is_the_acquisition_day():
    result = _service()._parse_stats_response({"data": [_ndvi_interval("2026-10-03", "2026-10-04")]})
    assert result["captured_date"] == "2026-10-03"
    assert result["captured_date_semantics"] == "utc_acquisition_day_of_p1d_interval"
    multi_day = _service()._parse_stats_response(
        {"data": [_ndvi_interval("2026-09-29", "2026-10-04")]}, aggregation_interval="P5D")
    assert multi_day["captured_date_semantics"] == "aggregation_interval_start_utc_date_not_acquisition_date"


def test_root_cause_p5d_bucket_starts_move_with_the_run_day_but_p1d_does_not():
    """The same acquisition (2026-10-03) seen by two consecutive daily runs."""
    service = _service()
    # Pre-TASK_246: P5D buckets anchored at the window start (run day - 14).
    run_one = service._parse_stats_response({"data": [_ndvi_interval("2026-09-29", "2026-10-04")]},
                                            aggregation_interval="P5D")
    run_two = service._parse_stats_response({"data": [_ndvi_interval("2026-09-30", "2026-10-05")]},
                                            aggregation_interval="P5D")
    assert run_one["mean_ndvi"] == run_two["mean_ndvi"]
    assert run_one["captured_date"] != run_two["captured_date"]          # one acquisition, two dates
    # TASK_246: P1D intervals; both runs see the same acquisition day.
    day = _ndvi_interval("2026-10-03", "2026-10-04")
    first = service._parse_stats_response({"data": [_ndvi_interval("2026-09-25", "2026-09-26"), day]})
    second = service._parse_stats_response({"data": [day, _ndvi_interval("2026-09-26", "2026-09-27")]})
    assert first["captured_date"] == second["captured_date"] == "2026-10-03"


def test_a_rerun_without_a_new_acquisition_creates_no_new_observation_and_a_new_one_does():
    from scripts import collect_ndvi

    stored: set[tuple[int, str]] = set()

    def writer(field_id, _enterprise, record, _start, _end):
        key = (field_id, record["captured_date"])           # ndvi_records UNIQUE (field_id, captured_date)
        if key in stored:
            return False
        stored.add(key)
        return True

    def run(response, tmp_dir):
        service = Mock(is_mock=False, source="Sentinel-2")
        service.get_ndvi_stats.return_value = _service()._parse_stats_response(response)
        log = Path(tmp_dir) / f"run_{len(stored)}_{id(response)}.json"
        args = collect_ndvi.parse_args(["--write", "--field-id", "246", "--output-log", str(log),
                                        "--date-from", "2026-09-24", "--date-to", "2026-10-08"])
        code = collect_ndvi.run(args, lookup=lambda _: {"geometry_wkt": "X", "enterprise_id": 9},
                                service_factory=lambda: service, writer=writer)
        return code, json.loads(log.read_text(encoding="utf-8"))

    import tempfile
    from unittest.mock import patch

    acquisition = {"data": [_ndvi_interval("2026-10-03", "2026-10-04")]}
    newer = {"data": [_ndvi_interval("2026-10-03", "2026-10-04"), _ndvi_interval("2026-10-06", "2026-10-07", mean=0.58)]}
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(collect_ndvi, "acquire_lock", return_value="lock"), \
            patch.object(collect_ndvi, "release_lock"):
        assert run(acquisition, tmp)[1]["inserted_count"] == 1
        repeat = run(acquisition, tmp)[1]
        assert (repeat["inserted_count"], repeat["skipped_existing_count"]) == (0, 1)
        assert run(newer, tmp)[1]["inserted_count"] == 1
    assert stored == {(246, "2026-10-03"), (246, "2026-10-06")}


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


def _row(row_id, day, value, *, field_id=7, std=0.0):
    return {"field_id": field_id, "enterprise_id": 9, "area_ha": 4.6, "id": row_id,
            "captured_date": date.fromisoformat(day), "value": value, "valid_pixels_pct": 100.0,
            "cloud_cover_pct": None, "min_ndvi": value, "max_ndvi": value, "std_ndvi": std,
            "p10_ndvi": value, "p90_ndvi": value}


def test_detection_series_collapses_repeated_rows_into_one_acquisition():
    from services import autonomous_monitoring as am

    rows = [_row(1, "2026-09-14", 0.79), _row(2, "2026-09-25", 0.787), _row(3, "2026-09-26", 0.787),
            _row(4, "2026-09-27", 0.787), _row(5, "2026-09-28", 0.787),
            _row(6, "2026-09-29", 0.638), _row(7, "2026-09-30", 0.638), _row(8, "2026-10-01", 0.638),
            _row(9, "2026-10-02", 0.638), _row(10, "2026-10-03", 0.638)]
    session = Mock()
    session.execute.return_value = _Result(rows)
    series = am._detection_series(session, as_of_date=date(2026, 10, 8), lookback_from=date(2026, 6, 10))
    scenes = series[7]["scenes"]
    assert [scene["id"] for scene in scenes] == [1, 2, 6]               # first row keeps the identity
    assert [scene["duplicate_ids"] for scene in scenes] == [[], [3, 4, 5], [7, 8, 9, 10]]
    params = session.execute.call_args[0][1]
    assert params["per_field"] == (am.DETECTION_BASELINE_SCENES + am.DETECTION_PERSISTENCE_WINDOW) * 5


def test_identical_values_far_apart_or_with_different_statistics_stay_separate_scenes():
    from services import autonomous_monitoring as am

    rows = [_row(1, "2026-09-10", 0.64), _row(2, "2026-09-16", 0.64),          # 6 days apart
            _row(3, "2026-09-17", 0.64, std=0.05)]                           # different statistics
    session = Mock()
    session.execute.return_value = _Result(rows)
    scenes = am._detection_series(session, as_of_date=date(2026, 10, 8), lookback_from=date(2026, 6, 10))[7]["scenes"]
    assert [scene["id"] for scene in scenes] == [1, 2, 3]


def test_repeated_rows_do_not_inflate_persistence():
    from services import autonomous_monitoring as am
    from services.autonomous_anomaly_engine import RulePolicy

    baseline = [0.62, 0.64, 0.63, 0.65, 0.66, 0.62, 0.64, 0.63, 0.65, 0.64]
    rows = [_row(i + 1, f"2026-08-{10 + 2 * i:02d}", value) for i, value in enumerate(baseline)]
    rows += [_row(20, "2026-10-01", 0.30), _row(21, "2026-10-02", 0.30), _row(22, "2026-10-03", 0.30)]
    session = Mock()
    session.execute.return_value = _Result(rows)
    scenes = am._detection_series(session, as_of_date=date(2026, 10, 8), lookback_from=date(2026, 6, 10))[7]["scenes"]
    values = [scene["value"] for scene in scenes]
    assert am._persistence(values, RulePolicy()) == 1                  # one acquisition, not three
    raw = baseline + [0.30, 0.30, 0.30]
    assert am._persistence(raw, RulePolicy()) == 3                     # what the duplicates used to claim


# ─── F. One shared footprint implementation ──────────────────────────────────

def _imported_names(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    return {alias.name for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == module for alias in node.names}


def test_ndvi_and_supporting_index_requests_share_the_grid_helper_and_footprint():
    from services.satellite import build_ndvi_statistical_payload
    from services.satellite_collection import build_statistical_payload
    from services.satellite_indices import build_multi_index_evalscript

    for relative in ("services/satellite.py", "services/satellite_collection.py"):
        assert "metric_request_grid" in _imported_names(BACKEND / relative, "services.sentinel_grid")
    ndvi = build_ndvi_statistical_payload(FIELD_246_LIKE, date(2026, 9, 24), date(2026, 10, 8))
    index = build_statistical_payload(FIELD_246_LIKE, build_multi_index_evalscript(["savi"]), ["savi"],
                                      date(2026, 9, 24), date(2026, 10, 8))
    assert ndvi["input"]["bounds"] == index["input"]["bounds"]               # same polygon, same CRS
    assert (ndvi["aggregation"]["resx"], index["aggregation"]["resx"]) == (10, 20)


def test_no_statistical_builder_still_sends_epsg4326():
    sources = {
        relative: (BACKEND / relative).read_text(encoding="utf-8")
        for relative in ("services/satellite_collection.py", "scripts/dry_run_multi_index_single_field.py",
                         "scripts/preview_multi_index_batch.py", "scripts/write_multi_index_batch.py",
                         "scripts/write_multi_index_single_field.py")
    }
    for relative, text in sources.items():
        assert "EPSG/0/4326" not in text, relative
    satellite = (BACKEND / "services/satellite.py").read_text(encoding="utf-8")
    statistical = satellite[satellite.index("def build_ndvi_statistical_payload"):satellite.index("def _finite_number")]
    assert "SENTINEL_CRS" not in statistical and '"resx": 10' not in statistical


def test_grid_module_has_no_epsg4326_fallback_path():
    source = Path(grid_module.__file__).read_text(encoding="utf-8")
    assert "EPSG/0/4326" not in source
    assert grid_module.ALLOWED_RESOLUTIONS_M == frozenset({10, 20})
