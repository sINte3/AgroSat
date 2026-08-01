#!/usr/bin/env python3
"""Record a sanitized Sentinel prerequisite and internal-contract result."""

from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-env", required=True)
    parser.add_argument("--evidence-path", required=True)
    parser.add_argument("--expected-head", required=True)
    return parser.parse_args()


def present(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def main() -> int:
    args = parse_args()
    worktree = Path(__file__).resolve().parents[2]
    backend = worktree / "backend"
    runtime_env = Path(args.runtime_env).resolve(strict=True)
    evidence_path = Path(args.evidence_path).resolve()
    allowed = "c:\\agrosat_backups\\program_r1_completion_run\\"
    if not str(evidence_path).lower().startswith(allowed):
        raise RuntimeError("unexpected evidence path")

    os.environ["AGROSAT_RUNTIME_ENV_FILE"] = str(runtime_env)
    os.environ["RELEASE_REVISION"] = args.expected_head
    sys.path.insert(0, str(backend))

    import httpx

    from config import settings
    from services.satellite_collection import (
        MultiIndexSentinelHubService,
        STATISTICAL_API_URL,
        TOKEN_URL,
        build_statistical_payload,
    )
    from services.satellite_indices import (
        build_multi_index_evalscript,
        parse_multi_index_stats_response_flat,
        validate_index_quality,
    )
    from services.satellite_safety import (
        REAL_SATELLITE_SOURCE,
        classify_provider_error,
        safe_provider_error_summary,
    )

    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(worktree),
        text=True,
    ).strip()
    if head != args.expected_head:
        raise RuntimeError("unexpected worktree HEAD")

    client_id_available = present(settings.sentinel_hub_client_id)
    client_secret_available = present(settings.sentinel_hub_client_secret)
    complete = client_id_available and client_secret_available
    partial = client_id_available != client_secret_available

    evalscript = build_multi_index_evalscript(["savi", "ndmi"])
    payload = build_statistical_payload(
        {
            "type": "Polygon",
            "coordinates": [
                [[64.0, 39.0], [64.001, 39.0], [64.001, 39.001], [64.0, 39.0]]
            ],
        },
        evalscript,
        ["savi", "ndmi"],
        date(2026, 7, 1),
        date(2026, 7, 2),
    )
    empty_scene = parse_multi_index_stats_response_flat(
        {"data": []},
        ["savi", "ndmi"],
    )
    valid_quality = validate_index_quality(
        "savi",
        mean_value=0.42,
        min_value=0.1,
        max_value=0.8,
        cloud_cover_pct=10,
        valid_pixels_pct=90,
    )[0]
    cloud_rejected = not validate_index_quality(
        "savi",
        mean_value=0.42,
        min_value=0.1,
        max_value=0.8,
        cloud_cover_pct=95,
        valid_pixels_pct=90,
    )[0]
    low_valid_rejected = not validate_index_quality(
        "savi",
        mean_value=0.42,
        min_value=0.1,
        max_value=0.8,
        cloud_cover_pct=10,
        valid_pixels_pct=10,
    )[0]

    unsafe_body_marker = "provider-body-must-not-appear"
    request = httpx.Request("POST", STATISTICAL_API_URL)
    classification_matrix: dict[str, dict[str, Any]] = {}
    for status in (401, 403, 429, 408, 400, 503):
        response = httpx.Response(status, request=request, text=unsafe_body_marker)
        error = httpx.HTTPStatusError("unsafe-exception-text", request=request, response=response)
        classification = classify_provider_error(error)
        summary = safe_provider_error_summary(error)
        if unsafe_body_marker in summary or "unsafe-exception-text" in summary:
            raise RuntimeError("provider error sanitizer retained unsafe text")
        classification_matrix[str(status)] = classification.as_dict()
    timeout_class = classify_provider_error(httpx.ReadTimeout("unsafe-timeout", request=request))
    network_class = classify_provider_error(httpx.ConnectError("unsafe-network", request=request))

    collector_source = (
        backend / "scripts" / "collect_satellite_indices.py"
    ).read_text(encoding="utf-8", errors="replace")
    legacy_source = (backend / "services" / "satellite.py").read_text(
        encoding="utf-8", errors="replace"
    )
    safety_source = (backend / "services" / "satellite_safety.py").read_text(
        encoding="utf-8", errors="replace"
    )
    internal_checks = {
        "officialHttpsEndpoints": (
            STATISTICAL_API_URL == "https://services.sentinel-hub.com/api/v1/statistics"
            and TOKEN_URL.startswith("https://services.sentinel-hub.com/")
        ),
        "evalscriptAtAggregationTopLevel": (
            payload.get("aggregation", {}).get("evalscript") == evalscript
            and "evalscript" not in payload["input"]["data"][0]["processing"]
        ),
        "boundedGeometryAndDateScope": (
            payload["input"]["bounds"]["properties"]["crs"].endswith("/4326")
            and payload["aggregation"]["aggregationInterval"]["of"] == "P1D"
            and payload["input"]["data"][0]["dataFilter"]["timeRange"]["from"].startswith("2026-07-01")
            and payload["input"]["data"][0]["dataFilter"]["timeRange"]["to"].startswith("2026-07-02")
        ),
        "realProviderProvenance": (
            MultiIndexSentinelHubService.source == REAL_SATELLITE_SOURCE
            and MultiIndexSentinelHubService.is_mock is False
        ),
        "emptySceneClassifiable": empty_scene == [],
        "cloudAndValidPixelQualityContract": (
            valid_quality and cloud_rejected and low_valid_rejected
        ),
        "safeProviderClassification": (
            classification_matrix["401"]["category"] == "authentication"
            and classification_matrix["429"]["category"] == "quota_or_rate_limit"
            and classification_matrix["400"]["category"] == "request_rejected"
            and classification_matrix["503"]["category"] == "provider_unavailable"
            and timeout_class.category == "timeout"
            and network_class.category == "network"
        ),
        "rawProviderBodiesNotLoggedOrPersisted": (
            "response.text" not in legacy_source
            and "response.content" not in legacy_source
            and "str(error)" not in collector_source
            and "repr(error)" not in collector_source
            and "error.response.text" not in safety_source
        ),
        "mockWriteSuccessRejected": (
            "write_enabled and bool(args.mock_sentinel)" in collector_source
            and "require_real_service(service)" in collector_source
        ),
    }
    if not all(internal_checks.values()):
        failed = sorted(name for name, passed in internal_checks.items() if not passed)
        raise RuntimeError(f"Sentinel internal contract failed: {','.join(failed)}")

    status = "READY" if complete else "BLOCKED"
    marker = (
        "READY_FOR_BOUNDED_LIVE_SENTINEL_REQUEST"
        if complete
        else "BLOCKED_EXTERNAL_SENTINEL_PREREQUISITE"
    )
    result = {
        "schemaVersion": 1,
        "gate": "GATE4",
        "operation": "secure_runtime_prerequisite_and_internal_contract_probe",
        "head": head,
        "secureRuntimeConfiguration": {
            "settingsLoad": "PASS",
            "clientIdAvailable": client_id_available,
            "clientSecretAvailable": client_secret_available,
            "completeCredentialsAvailable": complete,
            "partialCredentialConfiguration": partial,
            "credentialValuesReadIntoEvidence": False,
        },
        "providerEndpointClass": "official_sentinel_hub_https",
        "internalChecks": internal_checks,
        "providerErrorClassifications": classification_matrix,
        "liveRequest": {
            "attempted": False,
            "requestCount": 0,
            "reason": None if complete else "complete_credentials_unavailable",
        },
        "externalProviderCalls": 0,
        "isolatedPostgresqlWrites": 0,
        "productionWrites": 0,
        "status": status,
        "marker": marker,
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(evidence_path, result)
    print(json.dumps({"status": status, "marker": marker, "head": head}))
    return 0 if complete else 3


if __name__ == "__main__":
    raise SystemExit(main())
