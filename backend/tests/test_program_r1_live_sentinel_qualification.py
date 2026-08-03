"""Offline unit coverage for the bounded TASK 211 live CDSE harness.

Every HTTP response in this module is synthetic and process-local. No test here
contacts CDSE or any other external service.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import struct
import sys
from unittest.mock import Mock, patch
import zlib

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
HARNESS_PATH = ROOT / "ops" / "qualification" / "Run-ProgramR1LiveSentinelQualification.py"
SPEC = importlib.util.spec_from_file_location("task211_live_sentinel", HARNESS_PATH)
assert SPEC and SPEC.loader
HARNESS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HARNESS
SPEC.loader.exec_module(HARNESS)


def _response(url: str, status: int = 200, *, json_value=None, content: bytes = b""):
    request = httpx.Request("POST", url)
    if json_value is not None:
        return httpx.Response(status, request=request, json=json_value)
    return httpx.Response(
        status,
        request=request,
        content=content,
        headers={"content-type": "image/png"},
    )


def _png_chunk(kind, data):
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _rgba_png(*, visible=True):
    size = HARNESS.MAX_RASTER_SIZE
    pixels = bytearray(size * size * 4)
    if visible:
        pixels[:4] = b"\x20\x80\x40\xff"
    rows = b"".join(
        b"\x00" + bytes(pixels[row * size * 4:(row + 1) * size * 4])
        for row in range(size)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
    )


def _statistical_payload(start: date, end_inclusive: date):
    from services.satellite import (
        NDVI_EVALSCRIPT,
        SENTINEL_CRS,
        SENTINEL_DATASET,
        SENTINEL_MAX_CLOUD_COVERAGE,
    )

    end_exclusive = end_inclusive + timedelta(days=1)
    time_range = {
        "from": f"{start.isoformat()}T00:00:00Z",
        "to": f"{end_exclusive.isoformat()}T00:00:00Z",
    }
    return {
        "input": {
            "bounds": {
                "geometry": HARNESS.QUALIFICATION_GEOMETRY,
                "properties": {"crs": SENTINEL_CRS},
            },
            "data": [{
                "type": SENTINEL_DATASET,
                "dataFilter": {
                    "timeRange": dict(time_range),
                    "maxCloudCoverage": SENTINEL_MAX_CLOUD_COVERAGE,
                },
                "processing": {"harmonizeValues": True},
            }],
        },
        "aggregation": {
            "timeRange": dict(time_range),
            "aggregationInterval": {"of": "P1D"},
            "evalscript": NDVI_EVALSCRIPT,
            "resx": 10,
            "resy": 10,
        },
        "calculations": {"default": {}},
    }


def _process_payload(
    interval_from="2026-07-30T00:00:00Z",
    interval_to="2026-07-31T00:00:00Z",
    *,
    size=256,
):
    from services import ndvi_raster

    return ndvi_raster.build_process_payload_for_interval(
        HARNESS.QUALIFICATION_GEOMETRY,
        interval_from,
        interval_to,
        size,
    )


def _stats_interval(
    interval_from,
    interval_to,
    *,
    sample_count=12,
    no_data_count=1,
    mean=0.5,
):
    return {
        "interval": {"from": interval_from, "to": interval_to},
        "outputs": {
            "ndvi": {
                "bands": {
                    "B0": {
                        "stats": {
                            "sampleCount": sample_count,
                            "noDataCount": no_data_count,
                            "mean": mean,
                            "min": 0.2,
                            "max": 0.8,
                            "stDev": 0.1,
                            "percentiles": {"10.0": 0.3, "90.0": 0.7},
                        }
                    }
                }
            }
        },
    }


def _stats_body():
    return {
        "data": [_stats_interval(
            "2026-07-30T00:00:00Z",
            "2026-07-31T00:00:00Z",
        )]
    }


def _recorder(delegate=None):
    if delegate is None:
        delegate = Mock(return_value=_response(HARNESS.TOKEN_URL, 200, json_value={}))
    return HARNESS.BoundedHttpRecorder(
        delegate,
        HARNESS.validate_geometry(HARNESS.QUALIFICATION_GEOMETRY),
    )


def _selected_result():
    from services.satellite import NDVI_MASK_CONTRACT_ID, SENTINEL_DATASET

    return {
        "interval_from_utc": "2026-07-30T00:00:00Z",
        "interval_to_utc": "2026-07-31T00:00:00Z",
        "provider": "cdse",
        "dataset": SENTINEL_DATASET,
        "index_code": "ndvi",
        "mask_contract_id": NDVI_MASK_CONTRACT_ID,
        "interval_is_daily": True,
        "acquisition_timestamp_available": False,
    }


def test_runtime_window_is_exactly_yesterday_back_fourteen_utc_days():
    start, end = HARNESS.select_date_window(date(2026, 8, 3))
    assert start == date(2026, 7, 20)
    assert end == date(2026, 8, 2)
    assert (end - start).days + 1 == HARNESS.MAX_DATE_WINDOW_DAYS


def test_geometry_is_closed_bukhara_field_sized_and_stable():
    summary = HARNESS.validate_geometry(HARNESS.QUALIFICATION_GEOMETRY)
    assert summary.geometry_type == "Polygon"
    assert 0.1 <= summary.area_hectares <= 500
    assert summary.fingerprint == HARNESS.geometry_fingerprint(HARNESS.QUALIFICATION_GEOMETRY)


def test_statistical_request_rejects_window_over_bound_without_http():
    delegate = Mock()
    recorder = _recorder(delegate)
    payload = _statistical_payload(date(2026, 7, 1), date(2026, 7, 15))
    with pytest.raises(HARNESS.RequestContractError):
        recorder.post(HARNESS.STATISTICAL_API_URL, json=payload)
    delegate.assert_not_called()
    assert recorder.contract_failure is True


def test_cdse_recorder_rejects_planet_arbitrary_and_mixed_provider_endpoints():
    delegate = Mock()
    recorder = _recorder(delegate)
    form = {
        "grant_type": "client_credentials",
        "client_id": "fixture-id",
        "client_secret": "fixture-value",
    }
    for url in (
        HARNESS.PLANET_ENDPOINTS.token_url,
        HARNESS.PLANET_ENDPOINTS.statistical_url,
        HARNESS.PLANET_ENDPOINTS.process_url,
        "https://example.invalid/arbitrary",
    ):
        with pytest.raises(HARNESS.RequestContractError):
            recorder.post(url, data=form)
    delegate.assert_not_called()
    assert recorder.counts == {"oauth": 0, "statistical": 0, "process": 0}
    with pytest.raises(HARNESS.RequestContractError):
        HARNESS.BoundedHttpRecorder(
            delegate,
            HARNESS.validate_geometry(HARNESS.QUALIFICATION_GEOMETRY),
            HARNESS.PLANET_ENDPOINTS,
        )


@pytest.mark.parametrize("mutation", ["geometry", "dataset", "index", "mask", "crs"])
def test_statistical_contract_rejects_geometry_dataset_index_mask_or_crs_drift(mutation):
    delegate = Mock()
    payload = _statistical_payload(date(2026, 7, 20), date(2026, 8, 2))
    if mutation == "geometry":
        payload["input"]["bounds"]["geometry"] = {
            "type": "Polygon",
            "coordinates": [[[64.0, 39.0], [64.1, 39.0], [64.0, 39.0]]],
        }
    elif mutation == "dataset":
        payload["input"]["data"][0]["type"] = "sentinel-1-grd"
    elif mutation == "index":
        payload["calculations"] = {"evi": {}}
    elif mutation == "mask":
        payload["aggregation"]["evalscript"] += "\n// changed"
    else:
        payload["input"]["bounds"]["properties"]["crs"] = "EPSG:3857"
    with pytest.raises(HARNESS.RequestContractError):
        _recorder(delegate).post(HARNESS.STATISTICAL_API_URL, json=payload)
    delegate.assert_not_called()


def test_process_requires_bound_selected_interval_and_exact_reconciliation():
    delegate = Mock(return_value=_response(HARNESS.PROCESS_API_URL, content=_rgba_png()))
    recorder = _recorder(delegate)
    with pytest.raises(HARNESS.RequestContractError):
        recorder.post(HARNESS.PROCESS_API_URL, json=_process_payload())
    delegate.assert_not_called()

    recorder = _recorder(delegate)
    recorder.bind_selected_observation(_selected_result())
    mismatched = _process_payload(
        "2026-07-31T00:00:00Z",
        "2026-08-01T00:00:00Z",
    )
    with pytest.raises(HARNESS.RequestContractError):
        recorder.post(HARNESS.PROCESS_API_URL, json=mismatched)
    delegate.assert_not_called()


@pytest.mark.parametrize("mutation", ["geometry", "dataset", "index", "mask", "crs", "size"])
def test_process_contract_rejects_reconciliation_drift_before_http(mutation):
    delegate = Mock()
    recorder = _recorder(delegate)
    recorder.bind_selected_observation(_selected_result())
    payload = _process_payload()
    if mutation == "geometry":
        payload["input"]["bounds"]["geometry"] = {
            "type": "Polygon",
            "coordinates": [[[64.0, 39.0], [64.1, 39.0], [64.0, 39.0]]],
        }
    elif mutation == "dataset":
        payload["input"]["data"][0]["type"] = "sentinel-1-grd"
    elif mutation == "index":
        payload["evalscript"] = payload["evalscript"].replace("ndvi", "evi")
    elif mutation == "mask":
        payload["evalscript"] += "\n// changed"
    elif mutation == "crs":
        payload["input"]["bounds"]["properties"]["crs"] = "EPSG:3857"
    else:
        payload["output"]["width"] = 512
    with pytest.raises(HARNESS.RequestContractError):
        recorder.post(HARNESS.PROCESS_API_URL, json=payload)
    delegate.assert_not_called()


def test_request_counters_enforce_all_hard_bounds_with_mocked_http():
    token_delegate = Mock(side_effect=lambda url, **_: _response(url, 200, json_value={}))
    recorder = _recorder(token_delegate)
    form = {
        "grant_type": "client_credentials",
        "client_id": "fixture-id",
        "client_secret": "fixture-value",
    }
    for _ in range(HARNESS.MAX_OAUTH_REQUESTS):
        recorder.post(HARNESS.TOKEN_URL, data=form)
    with pytest.raises(HARNESS.RequestContractError):
        recorder.post(HARNESS.TOKEN_URL, data=form)
    assert token_delegate.call_count == HARNESS.MAX_OAUTH_REQUESTS

    stats_delegate = Mock(
        side_effect=lambda url, **_: _response(url, 200, json_value={"data": []})
    )
    recorder = _recorder(stats_delegate)
    payload = _statistical_payload(date(2026, 7, 20), date(2026, 8, 2))
    for _ in range(HARNESS.MAX_STATISTICAL_REQUESTS):
        recorder.post(HARNESS.STATISTICAL_API_URL, json=payload)
    with pytest.raises(HARNESS.RequestContractError):
        recorder.post(HARNESS.STATISTICAL_API_URL, json=payload)
    assert stats_delegate.call_count == HARNESS.MAX_STATISTICAL_REQUESTS

    process_delegate = Mock(
        side_effect=lambda url, **_: _response(url, 200, content=_rgba_png())
    )
    recorder = _recorder(process_delegate)
    recorder.bind_selected_observation(_selected_result())
    for _ in range(HARNESS.MAX_PROCESS_REQUESTS):
        recorder.post(HARNESS.PROCESS_API_URL, json=_process_payload())
    with pytest.raises(HARNESS.RequestContractError):
        recorder.post(HARNESS.PROCESS_API_URL, json=_process_payload())
    assert process_delegate.call_count == HARNESS.MAX_PROCESS_REQUESTS


def test_statistical_response_summary_selects_latest_by_parsed_time_not_array_order():
    body = {
        "data": [
            _stats_interval("2026-07-30T00:00:00Z", "2026-07-31T00:00:00Z"),
            _stats_interval("2026-07-28T00:00:00Z", "2026-07-29T00:00:00Z"),
            _stats_interval(
                "2026-08-01T00:00:00Z",
                "2026-08-02T00:00:00Z",
                sample_count=12,
                no_data_count=12,
            ),
        ]
    }
    metadata = HARNESS.inspect_statistical_response(
        _response(HARNESS.STATISTICAL_API_URL, json_value=body)
    )
    assert metadata["contractShapeValid"] is True
    assert metadata["usableIntervalCount"] == 2
    assert metadata["selectedIntervalFromUtc"] == "2026-07-30T00:00:00Z"
    assert metadata["selectedIntervalToUtc"] == "2026-07-31T00:00:00Z"
    assert metadata["rawProviderBodyIncluded"] is False
    assert all("outputs" not in item for item in metadata["intervalListSummary"])


def test_current_real_application_chain_uses_one_cdse_token_exact_interval_and_decoder():
    from services import ndvi_raster
    from services.satellite import SentinelHubService
    from shapely.geometry import shape

    def delegate(url: str, **_kwargs):
        if url == HARNESS.TOKEN_URL:
            return _response(
                url,
                json_value={"access_token": "fixture-token", "expires_in": 3600},
            )
        if url == HARNESS.STATISTICAL_API_URL:
            return _response(url, json_value=_stats_body())
        if url == HARNESS.PROCESS_API_URL:
            return _response(url, content=_rgba_png())
        raise AssertionError("unexpected endpoint")

    recorder = _recorder(delegate)
    service = SentinelHubService(
        "fixture-id",
        "fixture-secret",
        provider=HARNESS.QUALIFICATION_PROVIDER,
    )
    geometry_wkt = shape(HARNESS.QUALIFICATION_GEOMETRY).wkt
    with patch.object(httpx, "post", side_effect=recorder.post):
        stats = service.get_ndvi_stats(
            geometry_wkt,
            date(2026, 7, 20),
            date(2026, 8, 2),
            aggregation_interval="P1D",
        )
        assert stats
        recorder.bind_selected_observation(stats)
        with patch.object(ndvi_raster, "get_satellite_service", return_value=service):
            raster = ndvi_raster.request_process_png_for_interval(
                HARNESS.QUALIFICATION_GEOMETRY,
                stats["interval_from_utc"],
                stats["interval_to_utc"],
                HARNESS.MAX_RASTER_SIZE,
            )

    assert stats["captured_date"] == "2026-07-30"
    assert stats["interval_from_utc"] == "2026-07-30T00:00:00Z"
    assert stats["interval_to_utc"] == "2026-07-31T00:00:00Z"
    assert stats["acquisition_timestamp_available"] is False
    assert raster.validation.non_transparent_pixel_count == 1
    assert raster.validation.visible_coverage_pct > 0
    assert recorder.counts == {"oauth": 1, "statistical": 1, "process": 1}
    process_entry = recorder.latest("process")
    assert process_entry["intervalFromUtc"] == stats["interval_from_utc"]
    assert process_entry["intervalToUtc"] == stats["interval_to_utc"]
    assert {entry["providerPreset"] for entry in recorder.ledger} == {"cdse"}
    reconciliation = HARNESS._reconciliation_facts(
        stats,
        recorder.latest("statistical"),
        process_entry,
        raster.validation.as_sanitized_dict(),
    )
    assert reconciliation["passed"] is True
    assert all(
        reconciliation[key]
        for key in (
            "sameProvider",
            "sameDataset",
            "sameIndex",
            "sameGeometryFingerprint",
            "sameCrs",
            "exactIntervalBounds",
            "compatibleMaskContract",
        )
    )


def test_offline_run_live_builds_pass_evidence_only_from_decoded_reconciled_facts(tmp_path):
    from config import settings

    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text(
        "SENTINEL_HUB_CLIENT_ID=fixture-id\n"
        "SENTINEL_HUB_CLIENT_SECRET=fixture-secret\n",
        encoding="utf-8",
    )

    def delegate(url: str, **_kwargs):
        if url == HARNESS.TOKEN_URL:
            return _response(
                url,
                json_value={"access_token": "fixture-token", "expires_in": 3600},
            )
        if url == HARNESS.STATISTICAL_API_URL:
            return _response(url, json_value=_stats_body())
        if url == HARNESS.PROCESS_API_URL:
            return _response(url, content=_rgba_png())
        raise AssertionError("unexpected endpoint")

    baseline = {
        "checks": {"offlineFixture": True},
        "head": HARNESS.REQUIRED_STARTING_HEAD,
        "remoteHead": HARNESS.REQUIRED_STARTING_HEAD,
        "sourceMainHead": HARNESS.REQUIRED_SOURCE_MAIN_HEAD,
        "changedPaths": sorted(HARNESS.ALLOWED_CHANGED_PATHS),
        "changedPathsAllowed": True,
    }
    credential_checks = {
        "credentialFileExists": True,
        "credentialFileOutsideRepositoryWorktreesAndBackups": True,
        "exactlyTwoPhysicalLines": True,
        "strictAssignmentFormat": True,
        "accessControlInheritanceDisabled": True,
        "broadReadAccessAbsent": True,
        "exactRequiredKeySet": True,
        "duplicateKeysAbsent": True,
        "allRequiredValuesNonEmpty": True,
        "clientIdAvailable": True,
        "clientSecretAvailable": True,
        "credentialValuesIncluded": False,
        "credentialFileHashed": False,
    }
    with (
        patch.object(HARNESS, "assert_git_baseline", return_value=baseline),
        patch.object(HARNESS, "validate_credential_boundary", return_value=credential_checks),
        patch.object(httpx, "post", side_effect=delegate),
        patch.object(settings, "sentinel_hub_client_id", "fixture-id"),
        patch.object(settings, "sentinel_hub_client_secret", "fixture-secret"),
        patch.object(settings, "sentinel_hub_provider", "cdse"),
        patch.object(settings, "wialon_enabled", False),
    ):
        qualification, ledger, security, gate4, exit_code = HARNESS.run_live(
            worktree=ROOT,
            evidence_root=tmp_path,
            expected_head=HARNESS.REQUIRED_STARTING_HEAD,
            starting_head=HARNESS.REQUIRED_STARTING_HEAD,
            runtime_env=runtime_env,
        )

    assert exit_code == HARNESS.EXIT_PASS
    assert qualification["taskMarker"] == "PASS_TASK_211_PROGRAM_R1_FINAL_CLOSURE"
    assert qualification["programMarker"] == "PASS_PROGRAM_R1_FULL_MERGE_READINESS"
    assert qualification["statistical"]["canonicalDate"] == "2026-07-30"
    assert qualification["statistical"]["exactAcquisitionTimestampAvailable"] is False
    assert qualification["process"]["requestedIntervalFromUtc"] == "2026-07-30T00:00:00Z"
    assert qualification["process"]["requestedIntervalToUtc"] == "2026-07-31T00:00:00Z"
    assert qualification["process"]["response"]["nonTransparentPixelCount"] == 1
    assert qualification["process"]["response"]["visibleCoveragePct"] > 0
    assert qualification["reconciliation"]["passed"] is True
    assert qualification["mocksUsed"] is False
    assert qualification["productionWrites"] == 0
    assert qualification["redisAccessAttempted"] is False
    assert qualification["wialonExternalCalls"] == 0
    assert ledger["counts"] == {"oauth": 1, "statistical": 1, "process": 1}
    assert security["sanitization"] == "PASS"
    assert gate4["marker"] == "PASS_GATE4_LIVE_SENTINEL_REAL_RASTER"


def test_statistical_and_process_evalscript_masks_are_compatible_and_pinned():
    from services import ndvi_raster
    from services.satellite import (
        NDVI_DENOMINATOR_EPSILON,
        NDVI_EVALSCRIPT,
        NDVI_INVALID_SCL_CLASSES,
        NDVI_MASK_CONTRACT_ID,
    )

    exclusion = "[" + ",".join(str(value) for value in NDVI_INVALID_SCL_CLASSES) + "]"
    for script in (NDVI_EVALSCRIPT, ndvi_raster.EVALSCRIPT):
        assert "sample.dataMask === 1" in script
        assert exclusion in script
        assert "B08 - sample.B04" in script
        assert NDVI_DENOMINATOR_EPSILON in script
    assert ndvi_raster.NDVI_MASK_CONTRACT_ID == NDVI_MASK_CONTRACT_ID
    stats_data = _statistical_payload(date(2026, 7, 20), date(2026, 8, 2))["input"]["data"][0]
    process_data = _process_payload()["input"]["data"][0]
    assert stats_data["type"] == process_data["type"] == "sentinel-2-l2a"
    assert stats_data["processing"] == process_data["processing"]
    assert stats_data["dataFilter"]["maxCloudCoverage"] == process_data["dataFilter"]["maxCloudCoverage"]


@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [
        (401, "authentication", False),
        (403, "authentication", False),
        (429, "quota_or_rate_limit", False),
        (408, "timeout", True),
        (400, "request_rejected", False),
        (422, "request_rejected", False),
        (503, "provider_unavailable", True),
    ],
)
def test_provider_status_classification(status, category, retryable):
    assert HARNESS._provider_status_classification(status) == (category, retryable)


def _classify(**changes):
    defaults = {
        "contract_failure": False,
        "oauth_succeeded": True,
        "statistical_entry": {
            "httpStatus": 200,
            "providerClassification": "sentinel_2_observation",
        },
        "statistical_parseable": True,
        "usable_observation": True,
        "provenance_passed": True,
        "quality_passed": True,
        "process_entry": {
            "httpStatus": 200,
            "providerClassification": "sentinel_2_visible_png",
        },
        "raster_content_valid": True,
        "reconciliation_passed": True,
        "mocks_used": False,
    }
    defaults.update(changes)
    return HARNESS.classify_program_outcome(**defaults)


@pytest.mark.parametrize("reason", ["fully_transparent", "png_decode_failed"])
def test_http_200_transparent_or_malformed_raster_is_internal_blocker(reason):
    outcome = _classify(
        process_entry={
            "httpStatus": 200,
            "providerClassification": "invalid_raster_content",
            "rejectionReasonClass": reason,
        },
        raster_content_valid=False,
        reconciliation_passed=False,
    )
    assert outcome.task_marker == "FAIL_TASK_211_INTERNAL_SENTINEL_BLOCKER"
    assert outcome.program_marker == "FAIL_PROGRAM_R1_INTERNAL_SENTINEL_BLOCKER"
    assert outcome.exit_code == HARNESS.EXIT_INTERNAL_BLOCKER


@pytest.mark.parametrize("status", [401, 403])
def test_401_or_403_is_account_prerequisite(status):
    outcome = _classify(
        oauth_succeeded=False,
        statistical_entry={
            "httpStatus": status,
            "providerClassification": "authentication",
        },
        statistical_parseable=False,
        usable_observation=False,
        provenance_passed=False,
        quality_passed=False,
        process_entry=None,
        raster_content_valid=False,
        reconciliation_passed=False,
    )
    assert outcome.task_marker == "PARTIAL_TASK_211_SENTINEL_ACCOUNT_PREREQUISITE"
    assert outcome.exit_code == HARNESS.EXIT_ACCOUNT_PREREQUISITE


@pytest.mark.parametrize(
    ("classification", "status"),
    [
        ("quota_or_rate_limit", 429),
        ("provider_unavailable", 503),
        ("timeout", None),
        ("network", None),
        ("no_data", 200),
    ],
)
def test_quota_network_timeout_5xx_or_bounded_no_data_is_provider_prerequisite(
    classification,
    status,
):
    outcome = _classify(
        statistical_entry={
            "httpStatus": status,
            "providerClassification": classification,
        },
        statistical_parseable=classification == "no_data",
        usable_observation=False,
        provenance_passed=False,
        quality_passed=False,
        process_entry=None,
        raster_content_valid=False,
        reconciliation_passed=False,
    )
    assert outcome.task_marker == "PARTIAL_TASK_211_SENTINEL_PROVIDER_PREREQUISITE"
    assert outcome.exit_code == HARNESS.EXIT_PROVIDER_PREREQUISITE


def test_mock_can_never_enter_pass_path():
    outcome = _classify(mocks_used=True)
    assert outcome.task_marker == "FAIL_TASK_211_INTERNAL_SENTINEL_BLOCKER"
    assert outcome.status == "FAIL"


def test_complete_real_reconciled_visible_path_maps_to_full_merge_readiness():
    outcome = _classify()
    assert outcome.task_marker == "PASS_TASK_211_PROGRAM_R1_FINAL_CLOSURE"
    assert outcome.program_marker == "PASS_PROGRAM_R1_FULL_MERGE_READINESS"
    assert outcome.status == "PASS"
    assert outcome.exit_code == HARNESS.EXIT_PASS


def test_sanitizer_rejects_credentials_tokens_headers_bodies_urls_and_private_keys():
    for unsafe in (
        '{"authorization":"Bearer fixture"}',
        '{"token":"fixture"}',
        '{"access_token":"fixture"}',
        '{"sentinel_hub_client_id":"fixture"}',
        '{"sentinel_hub_client_secret":"fixture"}',
        '{"rawProviderBody":"fixture"}',
        '{"rasterBytes":"fixture"}',
        '{"pixelValues":[1]}',
        '{"database":"postgresql+psycopg://fixture@localhost/db"}',
        '{"redis":"rediss://localhost/0"}',
        '{"url":"https://user:password@example.invalid/path"}',
        '{"key":"-----BEGIN PRIVATE KEY-----"}',
        '{"safe":"runtime-credential-fixture"}',
    ):
        with pytest.raises(HARNESS.EvidenceSanitizationError):
            HARNESS.assert_sanitized(unsafe, ["runtime-credential-fixture"])


def test_sanitizer_accepts_only_safe_availability_and_raster_metadata():
    safe = HARNESS.canonical_json({
        "clientIdAvailable": True,
        "clientSecretAvailable": True,
        "credentialValuesIncluded": False,
        "responseSha256": "0" * 64,
        "byteCount": 340,
        "nonTransparentPixelCount": 1,
        "visibleCoveragePct": 0.001526,
        "rawRasterPersisted": False,
        "pixelArrayPersisted": False,
    })
    HARNESS.assert_sanitized(safe, ["runtime-value-not-present"])


def test_sanitized_raster_evidence_write_contains_no_bytes_or_pixel_arrays(tmp_path):
    value = {
        "responseSha256": "0" * 64,
        "byteCount": 340,
        "decodedWidth": 256,
        "decodedHeight": 256,
        "nonTransparentPixelCount": 1,
        "visibleCoveragePct": 0.001526,
        "rawRasterPersisted": False,
        "pixelArrayPersisted": False,
    }
    destination = tmp_path / "raster_metadata.json"
    HARNESS.atomic_json(destination, value)
    loaded = json.loads(destination.read_text(encoding="utf-8"))
    assert loaded == value
    assert not any(isinstance(item, (bytes, bytearray, list)) for item in loaded.values())


def test_qualification_source_never_claims_unavailable_acquisition_timestamp():
    source = HARNESS_PATH.read_text(encoding="utf-8")
    assert '"exactAcquisitionTimestampAvailable": False' in source
    assert '"exactAcquisitionTimestampAvailableInPngContract": False' in source
    assert '"exactAcquisitionTimestampClaimed": False' in source
    assert "PNG response exposes an exact acquisition timestamp" not in source
