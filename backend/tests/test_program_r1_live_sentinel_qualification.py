"""Unit coverage for the bounded PROGRAM R1 live Sentinel harness.

All HTTP behavior in this module is mocked; these tests never contact Sentinel.
"""

from __future__ import annotations

from datetime import date
import importlib.util
from pathlib import Path
import sys
from unittest.mock import Mock, patch

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = ROOT / "ops" / "qualification" / "Run-ProgramR1LiveSentinelQualification.py"
SPEC = importlib.util.spec_from_file_location("program_r1_live_sentinel", HARNESS_PATH)
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


def _statistical_payload(start: date, end: date):
    return {
        "input": {
            "bounds": {"geometry": HARNESS.QUALIFICATION_GEOMETRY},
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {
                        "from": f"{start.isoformat()}T00:00:00Z",
                        "to": f"{end.isoformat()}T23:59:59Z",
                    }
                },
            }],
        },
        "aggregation": {
            "timeRange": {
                "from": f"{start.isoformat()}T00:00:00Z",
                "to": f"{end.isoformat()}T23:59:59Z",
            },
            "aggregationInterval": {"of": "P1D"},
            "evalscript": 'output: [{ id: "ndvi", bands: 1 }]',
        },
        "calculations": {"default": {}},
    }


def _raster_payload(day: date, size: int = 256):
    next_day = date.fromordinal(day.toordinal() + 1)
    return {
        "input": {
            "bounds": {"geometry": HARNESS.QUALIFICATION_GEOMETRY},
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {
                        "from": f"{day.isoformat()}T00:00:00Z",
                        "to": f"{next_day.isoformat()}T00:00:00Z",
                    }
                },
            }],
        },
        "output": {
            "width": size,
            "height": size,
            "responses": [{
                "identifier": "default",
                "format": {"type": "image/png"},
            }],
        },
        "evalscript": "let ndvi = 0.5;",
    }


def _recorder(delegate=None):
    if delegate is None:
        delegate = Mock(return_value=_response(HARNESS.TOKEN_URL, 200, json_value={}))
    return HARNESS.BoundedHttpRecorder(
        delegate,
        HARNESS.validate_geometry(HARNESS.QUALIFICATION_GEOMETRY),
    )


def test_runtime_date_window_is_exactly_bounded_to_fourteen_days():
    start, end = HARNESS.select_date_window(date(2026, 8, 2))
    assert start == date(2026, 7, 19)
    assert end == date(2026, 8, 1)
    assert (end - start).days + 1 == HARNESS.MAX_DATE_WINDOW_DAYS


def test_qualification_geometry_is_closed_bukhara_field_sized_and_stable():
    summary = HARNESS.validate_geometry(HARNESS.QUALIFICATION_GEOMETRY)
    assert summary.geometry_type == "Polygon"
    assert 0.1 <= summary.area_hectares <= 500
    assert summary.fingerprint == HARNESS.geometry_fingerprint(HARNESS.QUALIFICATION_GEOMETRY)


def test_statistical_request_rejects_window_over_fourteen_days_without_http():
    delegate = Mock()
    recorder = _recorder(delegate)
    payload = _statistical_payload(date(2026, 7, 1), date(2026, 7, 15))
    with pytest.raises(HARNESS.RequestContractError):
        recorder.post(HARNESS.STATISTICAL_API_URL, json=payload)
    delegate.assert_not_called()
    assert recorder.contract_failure is True


def test_cdse_recorder_rejects_planet_and_arbitrary_endpoints_without_http():
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
    assert recorder.counts == {"oauth": 0, "statistical": 0, "raster": 0}


def test_statistical_request_rejects_changed_geometry_and_extra_index_without_http():
    delegate = Mock()
    payload = _statistical_payload(date(2026, 7, 19), date(2026, 8, 1))
    payload["input"]["bounds"]["geometry"] = {
        "type": "Polygon",
        "coordinates": [[[64.0, 39.0], [64.1, 39.0], [64.0, 39.0]]],
    }
    with pytest.raises(HARNESS.RequestContractError):
        _recorder(delegate).post(HARNESS.STATISTICAL_API_URL, json=payload)
    delegate.assert_not_called()

    payload = _statistical_payload(date(2026, 7, 19), date(2026, 8, 1))
    payload["calculations"]["unexpected"] = {}
    with pytest.raises(HARNESS.RequestContractError):
        _recorder(delegate).post(HARNESS.STATISTICAL_API_URL, json=payload)
    delegate.assert_not_called()

    payload = _statistical_payload(date(2026, 7, 19), date(2026, 8, 1))
    payload["calculations"] = {"evi": {}}
    with pytest.raises(HARNESS.RequestContractError):
        _recorder(delegate).post(HARNESS.STATISTICAL_API_URL, json=payload)
    delegate.assert_not_called()


def test_raster_request_rejects_size_over_256_without_http():
    delegate = Mock()
    recorder = _recorder(delegate)
    with pytest.raises(HARNESS.RequestContractError):
        recorder.post(
            HARNESS.PROCESS_API_URL,
            json=_raster_payload(date(2026, 7, 25), size=512),
        )
    delegate.assert_not_called()


def test_request_counter_enforces_oauth_hard_bound_with_mocked_http():
    delegate = Mock(side_effect=lambda url, **_: _response(url, 200, json_value={}))
    recorder = _recorder(delegate)
    form = {"grant_type": "client_credentials", "client_id": "fixture-id", "client_secret": "fixture-value"}
    recorder.post(HARNESS.TOKEN_URL, data=form)
    recorder.post(HARNESS.TOKEN_URL, data=form)
    with pytest.raises(HARNESS.RequestContractError):
        recorder.post(HARNESS.TOKEN_URL, data=form)
    assert delegate.call_count == HARNESS.MAX_OAUTH_REQUESTS
    assert recorder.counts["oauth"] == HARNESS.MAX_OAUTH_REQUESTS


@pytest.mark.parametrize(
    ("kind", "url", "payload_factory", "maximum"),
    [
        (
            "statistical",
            HARNESS.STATISTICAL_API_URL,
            lambda: {"json": _statistical_payload(date(2026, 7, 19), date(2026, 8, 1))},
            HARNESS.MAX_STATISTICAL_REQUESTS,
        ),
        (
            "raster",
            HARNESS.PROCESS_API_URL,
            lambda: {"json": _raster_payload(date(2026, 7, 25))},
            HARNESS.MAX_RASTER_REQUESTS,
        ),
    ],
)
def test_statistical_and_raster_request_counters_enforce_hard_bounds(
    kind,
    url,
    payload_factory,
    maximum,
):
    delegate = Mock(side_effect=lambda called_url, **_: _response(
        called_url,
        200,
        json_value={"data": []} if kind == "statistical" else None,
        content=b"\x89PNG\r\n\x1a\nfixture" if kind == "raster" else b"",
    ))
    recorder = _recorder(delegate)
    for _ in range(maximum):
        recorder.post(url, **payload_factory())
    with pytest.raises(HARNESS.RequestContractError):
        recorder.post(url, **payload_factory())
    assert recorder.counts[kind] == maximum
    assert delegate.call_count == maximum


def test_mocked_statistical_response_records_only_sanitized_metadata():
    body = {
        "data": [{
            "interval": {
                "from": "2026-07-25T00:00:00Z",
                "to": "2026-07-26T00:00:00Z",
            },
            "outputs": {
                "ndvi": {"bands": {"B0": {"stats": {"sampleCount": 12}}}}
            },
        }]
    }
    delegate = Mock(
        return_value=_response(HARNESS.STATISTICAL_API_URL, 200, json_value=body)
    )
    recorder = _recorder(delegate)
    recorder.post(
        HARNESS.STATISTICAL_API_URL,
        json=_statistical_payload(date(2026, 7, 19), date(2026, 8, 1)),
    )
    assert recorder.statistical_response_metadata[-1]["usableIntervalCount"] == 1
    serialized = HARNESS.canonical_json(recorder.ledger)
    assert "sampleCount" not in serialized
    assert "outputs" not in serialized
    assert recorder.ledger[-1]["geometryFingerprint"]


def test_current_application_service_chain_uses_one_cdse_token_for_stats_and_process():
    backend = ROOT / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from services import ndvi_raster
    from services.satellite import SentinelHubService
    from shapely.geometry import shape

    stats_body = {
        "data": [{
            "interval": {
                "from": "2026-07-25T00:00:00Z",
                "to": "2026-07-25T23:59:59Z",
            },
            "outputs": {
                "ndvi": {
                    "bands": {
                        "B0": {
                            "stats": {
                                "sampleCount": 12,
                                "noDataCount": 1,
                                "mean": 0.5,
                                "min": 0.2,
                                "max": 0.8,
                                "stDev": 0.1,
                                "percentiles": {"10.0": 0.3, "90.0": 0.7},
                            }
                        }
                    }
                }
            },
        }]
    }

    def delegate(url: str, **_kwargs):
        if url == HARNESS.TOKEN_URL:
            return _response(
                url,
                200,
                json_value={"access_token": "fixture-token", "expires_in": 3600},
            )
        if url == HARNESS.STATISTICAL_API_URL:
            return _response(url, 200, json_value=stats_body)
        if url == HARNESS.PROCESS_API_URL:
            return _response(url, 200, content=b"\x89PNG\r\n\x1a\nfixture")
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
            date(2026, 7, 19),
            date(2026, 8, 1),
            aggregation_interval="P1D",
        )
        response = service.get_process_png(
            ndvi_raster.build_process_payload(
                HARNESS.QUALIFICATION_GEOMETRY,
                date(2026, 7, 25),
                HARNESS.MAX_RASTER_SIZE,
            )
        )

    assert stats and stats["captured_date"] == "2026-07-25"
    assert ndvi_raster.validate_png(response.content)
    assert recorder.counts == {"oauth": 1, "statistical": 1, "raster": 1}
    assert {entry["providerPreset"] for entry in recorder.ledger} == {"cdse"}


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


def test_sanitizer_rejects_credentials_tokens_headers_bodies_urls_and_private_keys():
    for unsafe in (
        '{"authorization":"Bearer fixture"}',
        '{"token":"fixture"}',
        '{"access_token":"fixture"}',
        '{"sentinel_hub_client_id":"fixture"}',
        '{"sentinel_hub_client_secret":"fixture"}',
        '{"rawProviderBody":"fixture"}',
        '{"database":"postgresql+psycopg://fixture@localhost/db"}',
        '{"redis":"rediss://localhost/0"}',
        '{"url":"https://user:password@example.invalid/path"}',
        '{"key":"-----BEGIN PRIVATE KEY-----"}',
        '{"safe":"runtime-credential-fixture"}',
    ):
        with pytest.raises(HARNESS.EvidenceSanitizationError):
            HARNESS.assert_sanitized(unsafe, ["runtime-credential-fixture"])


def test_sanitizer_accepts_boolean_credential_availability_only():
    safe = HARNESS.canonical_json({
        "clientIdAvailable": True,
        "clientSecretAvailable": True,
        "credentialValuesIncluded": False,
    })
    HARNESS.assert_sanitized(safe, ["runtime-value-not-present"])


def test_internal_request_rejection_cannot_be_reclassified_as_external():
    marker, status, exit_code = HARNESS.classify_program_outcome(
        contract_failure=False,
        oauth_succeeded=True,
        statistical_entry={"httpStatus": 400, "providerClassification": "request_rejected"},
        statistical_parseable=False,
        usable_observation=False,
        quality_passed=False,
        raster_entry=None,
        raster_passed=False,
    )
    assert marker == "FAIL_PROGRAM_R1_INTERNAL_SENTINEL_BLOCKER"
    assert status == "FAIL"
    assert exit_code == HARNESS.EXIT_INTERNAL_BLOCKER


@pytest.mark.parametrize("status_code", [400, 422])
def test_payload_rejection_is_always_internal(status_code):
    marker, status, exit_code = HARNESS.classify_program_outcome(
        contract_failure=False,
        oauth_succeeded=True,
        statistical_entry={
            "httpStatus": status_code,
            "providerClassification": "request_rejected",
        },
        statistical_parseable=False,
        usable_observation=False,
        quality_passed=False,
        raster_entry=None,
        raster_passed=False,
    )
    assert (marker, status, exit_code) == (
        "FAIL_PROGRAM_R1_INTERNAL_SENTINEL_BLOCKER",
        "FAIL",
        HARNESS.EXIT_INTERNAL_BLOCKER,
    )


def test_authentication_rejection_maps_to_account_prerequisite():
    marker, status, exit_code = HARNESS.classify_program_outcome(
        contract_failure=False,
        oauth_succeeded=False,
        statistical_entry={
            "operationClass": "oauth_client_credentials",
            "httpStatus": 401,
            "providerClassification": "authentication",
        },
        statistical_parseable=False,
        usable_observation=False,
        quality_passed=False,
        raster_entry=None,
        raster_passed=False,
    )
    assert marker == "PARTIAL_PROGRAM_R1_SENTINEL_ACCOUNT_PREREQUISITE"
    assert status == "BLOCKED"
    assert exit_code == HARNESS.EXIT_ACCOUNT_PREREQUISITE


def test_successful_http_with_invalid_oauth_body_is_internal_blocker():
    marker, status, exit_code = HARNESS.classify_program_outcome(
        contract_failure=False,
        oauth_succeeded=False,
        statistical_entry={
            "operationClass": "oauth_client_credentials",
            "httpStatus": 200,
            "providerClassification": "invalid_response",
        },
        statistical_parseable=False,
        usable_observation=False,
        quality_passed=False,
        raster_entry=None,
        raster_passed=False,
    )
    assert marker == "FAIL_PROGRAM_R1_INTERNAL_SENTINEL_BLOCKER"
    assert status == "FAIL"
    assert exit_code == HARNESS.EXIT_INTERNAL_BLOCKER


def test_no_data_maps_to_provider_prerequisite_without_mock_success():
    marker, status, exit_code = HARNESS.classify_program_outcome(
        contract_failure=False,
        oauth_succeeded=True,
        statistical_entry={"httpStatus": 200, "providerClassification": "no_data"},
        statistical_parseable=True,
        usable_observation=False,
        quality_passed=False,
        raster_entry=None,
        raster_passed=False,
    )
    assert marker == "PARTIAL_PROGRAM_R1_SENTINEL_PROVIDER_PREREQUISITE"
    assert status == "BLOCKED"
    assert exit_code == HARNESS.EXIT_PROVIDER_PREREQUISITE


@pytest.mark.parametrize(
    ("provider_classification", "http_status"),
    [
        ("quota_or_rate_limit", 429),
        ("provider_unavailable", 503),
        ("timeout", None),
        ("network", None),
    ],
)
def test_retryable_provider_and_quota_outcomes_are_provider_prerequisites(
    provider_classification,
    http_status,
):
    marker, status, exit_code = HARNESS.classify_program_outcome(
        contract_failure=False,
        oauth_succeeded=True,
        statistical_entry={
            "httpStatus": http_status,
            "providerClassification": provider_classification,
        },
        statistical_parseable=False,
        usable_observation=False,
        quality_passed=False,
        raster_entry=None,
        raster_passed=False,
    )
    assert (marker, status, exit_code) == (
        "PARTIAL_PROGRAM_R1_SENTINEL_PROVIDER_PREREQUISITE",
        "BLOCKED",
        HARNESS.EXIT_PROVIDER_PREREQUISITE,
    )


def test_statistical_authentication_rejection_is_account_prerequisite():
    marker, status, exit_code = HARNESS.classify_program_outcome(
        contract_failure=False,
        oauth_succeeded=True,
        statistical_entry={
            "httpStatus": 403,
            "providerClassification": "authentication",
        },
        statistical_parseable=False,
        usable_observation=False,
        quality_passed=False,
        raster_entry=None,
        raster_passed=False,
    )
    assert (marker, status, exit_code) == (
        "PARTIAL_PROGRAM_R1_SENTINEL_ACCOUNT_PREREQUISITE",
        "BLOCKED",
        HARNESS.EXIT_ACCOUNT_PREREQUISITE,
    )


def test_invalid_statistical_success_body_is_internal_blocker():
    marker, status, exit_code = HARNESS.classify_program_outcome(
        contract_failure=False,
        oauth_succeeded=True,
        statistical_entry={
            "httpStatus": 200,
            "providerClassification": "invalid_response",
        },
        statistical_parseable=False,
        usable_observation=False,
        quality_passed=False,
        raster_entry=None,
        raster_passed=False,
    )
    assert (marker, status, exit_code) == (
        "FAIL_PROGRAM_R1_INTERNAL_SENTINEL_BLOCKER",
        "FAIL",
        HARNESS.EXIT_INTERNAL_BLOCKER,
    )


def test_complete_real_path_facts_map_to_full_merge_readiness():
    marker, status, exit_code = HARNESS.classify_program_outcome(
        contract_failure=False,
        oauth_succeeded=True,
        statistical_entry={"httpStatus": 200, "providerClassification": "sentinel_2_observation"},
        statistical_parseable=True,
        usable_observation=True,
        quality_passed=True,
        raster_entry={"httpStatus": 200, "providerClassification": "sentinel_2_png"},
        raster_passed=True,
    )
    assert marker == "PASS_PROGRAM_R1_FULL_MERGE_READINESS"
    assert status == "PASS"
    assert exit_code == HARNESS.EXIT_PASS
