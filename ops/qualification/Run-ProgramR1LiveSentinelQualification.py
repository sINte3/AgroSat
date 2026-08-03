#!/usr/bin/env python3
"""Run one bounded, sanitized, read-only TASK 211 CDSE qualification.

The harness exercises the real application Statistical and Process service paths.
It never persists credentials, OAuth tokens, provider bodies, raster bytes, or
decoded pixel arrays. Only sanitized metadata is written beneath the approved
external evidence root.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable

import httpx


WORKTREE_ROOT = Path(__file__).resolve().parents[2]
BACKEND_PATH = WORKTREE_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from services.sentinel_provider import (  # noqa: E402
    CDSE_PROVIDER,
    PLANET_PROVIDER,
    SentinelProviderEndpoints,
    resolve_sentinel_provider,
)


PROGRAM = "PROGRAM R1 - TASK 211 final Sentinel closure"
REQUIRED_BRANCH = "task/task209-agrosat-global-program"
REQUIRED_STARTING_HEAD = "80262955e423b30dc28d2ea5a3ff6ee39188d5d1"
REQUIRED_SOURCE_MAIN_HEAD = "dfb57c7ff89c0af10f7907b81965487481c5b3e7"
ALLOWED_EVIDENCE_PREFIX = Path(
    r"C:\AgroSat_backups\TASK_211_PROGRAM_R1_FINAL_CLOSURE"
)
SOURCE_CHECKOUT = Path(r"C:\AgroSat")
RESTRICTED_CREDENTIAL_ROOTS = (
    Path(r"C:\AgroSat"),
    Path(r"C:\AgroSat_worktrees"),
    Path(r"C:\AgroSat_backups"),
)
EXPECTED_CREDENTIAL_KEYS = (
    "SENTINEL_HUB_CLIENT_ID",
    "SENTINEL_HUB_CLIENT_SECRET",
)

MAX_DATE_WINDOW_DAYS = 14
MAX_FIELD_GEOMETRIES = 1
MAX_INDEX_CODES = 1
MAX_STATISTICAL_REQUESTS = 3
MAX_PROCESS_REQUESTS = 2
MAX_RASTER_REQUESTS = MAX_PROCESS_REQUESTS  # compatibility alias for tests/evidence
MAX_OAUTH_REQUESTS = 2
MAX_RASTER_SIZE = 256
MAX_RETRYABLE_RETRIES = 1

QUALIFICATION_PROVIDER = CDSE_PROVIDER
QUALIFICATION_ENDPOINTS = resolve_sentinel_provider(QUALIFICATION_PROVIDER)
PLANET_ENDPOINTS = resolve_sentinel_provider(PLANET_PROVIDER)
TOKEN_URL = QUALIFICATION_ENDPOINTS.token_url
STATISTICAL_API_URL = QUALIFICATION_ENDPOINTS.statistical_url
PROCESS_API_URL = QUALIFICATION_ENDPOINTS.process_url

ALLOWED_CHANGED_PATHS = frozenset({
    "backend/services/satellite.py",
    "backend/services/ndvi_raster.py",
    "backend/services/sentinel_provider.py",
    "backend/tests/test_sentinel_provider.py",
    "backend/tests/test_ndvi_raster.py",
    "backend/tests/test_program_r1_live_sentinel_qualification.py",
    "backend/tests/test_task211_program_r1_final_closure.py",
    "backend/requirements.txt",
    "ops/qualification/Run-ProgramR1LiveSentinelQualification.py",
})

# Deterministic field-sized polygon; never loaded from a product database.
QUALIFICATION_GEOMETRY: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[
        [64.4200, 39.7600],
        [64.4300, 39.7600],
        [64.4300, 39.7700],
        [64.4200, 39.7700],
        [64.4200, 39.7600],
    ]],
}

EXIT_PASS = 0
EXIT_CREDENTIAL_BOUNDARY = 2
EXIT_ACCOUNT_PREREQUISITE = 3
EXIT_PROVIDER_PREREQUISITE = 4
EXIT_INTERNAL_BLOCKER = 5
EXIT_BASELINE = 6


class BaselineError(RuntimeError):
    """The required Git, filesystem, or evidence baseline was not retained."""


class CredentialBoundaryError(RuntimeError):
    """The process-local credential file failed its strict boundary."""


class RequestContractError(RuntimeError):
    """A request would violate the fixed TASK 211 live bounds."""


class EvidenceSanitizationError(RuntimeError):
    """Evidence contains a forbidden secret-bearing or raw-body field."""


@dataclass(frozen=True, slots=True)
class GeometrySummary:
    geometry_type: str
    fingerprint: str
    bbox: tuple[float, float, float, float]
    area_hectares: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.geometry_type,
            "crs": "EPSG:4326",
            "fingerprint": self.fingerprint,
            "bbox": {
                "west": self.bbox[0],
                "south": self.bbox[1],
                "east": self.bbox[2],
                "north": self.bbox[3],
            },
            "areaHectares": self.area_hectares,
            "sourceClass": "deterministic_isolated_task_211_geometry",
            "productionDatabaseLookup": False,
        }


@dataclass(frozen=True, slots=True)
class ProgramOutcome:
    task_marker: str
    program_marker: str
    status: str
    exit_code: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-env", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--starting-head", required=True)
    return parser.parse_args()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def geometry_fingerprint(geometry: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(geometry).encode("utf-8")).hexdigest()


def _is_within(path: Path, parent: Path) -> bool:
    normalized_path = os.path.normcase(str(path.resolve()))
    normalized_parent = os.path.normcase(str(parent.resolve()))
    try:
        return os.path.commonpath((normalized_path, normalized_parent)) == normalized_parent
    except ValueError:
        return False


def validate_evidence_root(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_dir() or not _is_within(resolved, ALLOWED_EVIDENCE_PREFIX):
        raise BaselineError("unexpected evidence root")
    required = {
        "00_BASELINE",
        "01_DIAGNOSIS",
        "02_IMPLEMENTATION",
        "03_TESTS",
        "04_LIVE_SENTINEL",
        "05_SECURITY_AND_SCOPE",
        "06_FINAL",
        "scratch",
    }
    if not all((resolved / item).is_dir() for item in required):
        raise BaselineError("incomplete evidence root")
    return resolved


def _git(worktree: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise BaselineError("Git baseline command failed")
    return completed.stdout.strip()


def _current_changed_paths(worktree: Path) -> list[str]:
    tracked = {
        value.strip().replace("\\", "/")
        for value in _git(worktree, "diff", "--name-only").splitlines()
        if value.strip()
    }
    untracked = {
        value.strip().replace("\\", "/")
        for value in _git(
            worktree,
            "ls-files",
            "--others",
            "--exclude-standard",
        ).splitlines()
        if value.strip()
    }
    return sorted(tracked | untracked)


def assert_git_baseline(
    worktree: Path,
    expected_head: str,
    starting_head: str,
) -> dict[str, Any]:
    if expected_head != REQUIRED_STARTING_HEAD or starting_head != REQUIRED_STARTING_HEAD:
        raise BaselineError("unexpected TASK 211 head argument")
    actual_head = _git(worktree, "rev-parse", "HEAD")
    branch = _git(worktree, "branch", "--show-current")
    remote_line = _git(
        worktree,
        "ls-remote",
        "--exit-code",
        "origin",
        f"refs/heads/{REQUIRED_BRANCH}",
    )
    remote_head = remote_line.split()[0] if remote_line else ""
    source_head = _git(SOURCE_CHECKOUT, "rev-parse", "HEAD")
    source_branch = _git(SOURCE_CHECKOUT, "branch", "--show-current")
    source_dirty = bool(
        _git(SOURCE_CHECKOUT, "status", "--porcelain=v1", "--untracked-files=all")
    )
    changed_paths = _current_changed_paths(worktree)
    allowed_changes = bool(changed_paths) and set(changed_paths).issubset(ALLOWED_CHANGED_PATHS)
    checks = {
        "branchExact": branch == REQUIRED_BRANCH,
        "headStillStartingHead": actual_head == starting_head,
        "remoteStillStartingHead": remote_head == starting_head,
        "implementationChangesPresent": bool(changed_paths),
        "implementationChangesRestricted": allowed_changes,
        "sourceMainBranchExact": source_branch == "main",
        "sourceMainHeadExact": source_head == REQUIRED_SOURCE_MAIN_HEAD,
        "sourceMainClean": not source_dirty,
    }
    if not all(checks.values()):
        raise BaselineError("exact pre-commit live qualification baseline was not retained")
    return {
        "checks": checks,
        "head": actual_head,
        "remoteHead": remote_head,
        "sourceMainHead": source_head,
        "changedPaths": changed_paths,
        "changedPathsAllowed": True,
        "qualificationExecutedBeforeImplementationCommit": True,
        "productSurfacesInvalidated": False,
    }


def _acl_security_flags(path: Path) -> tuple[bool, bool]:
    environment = os.environ.copy()
    environment["AGROSAT_TASK211_RUNTIME_ENV_FILE"] = str(path)
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "$acl = Get-Acl -LiteralPath $env:AGROSAT_TASK211_RUNTIME_ENV_FILE; "
                "$broad = @('S-1-1-0','S-1-5-11','S-1-5-32-545','S-1-5-32-546','S-1-5-32-547'); "
                "$hasBroadRead = $false; "
                "$rules = $acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]); "
                "$mask = [System.Security.AccessControl.FileSystemRights]::Read -bor "
                "[System.Security.AccessControl.FileSystemRights]::ReadData -bor "
                "[System.Security.AccessControl.FileSystemRights]::ReadAndExecute -bor "
                "[System.Security.AccessControl.FileSystemRights]::Modify -bor "
                "[System.Security.AccessControl.FileSystemRights]::FullControl; "
                "foreach ($rule in $rules) { if (($broad -contains $rule.IdentityReference.Value) -and "
                "$rule.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and "
                "(($rule.FileSystemRights -band $mask) -ne 0)) { $hasBroadRead = $true } }; "
                "[pscustomobject]@{protected=$acl.AreAccessRulesProtected; broadReadAbsent=(-not $hasBroadRead)} "
                "| ConvertTo-Json -Compress"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        return False, False
    try:
        flags = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return False, False
    return bool(flags.get("protected")), bool(flags.get("broadReadAbsent"))


def validate_credential_boundary(path: Path) -> dict[str, bool]:
    resolved = path.resolve(strict=True)
    outside_restricted = not any(
        _is_within(resolved, root) for root in RESTRICTED_CREDENTIAL_ROOTS
    )
    try:
        raw_lines = resolved.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        raise CredentialBoundaryError("credential file could not be parsed") from None
    assignments: list[tuple[str, bool]] = []
    format_valid = len(raw_lines) == 2
    for line in raw_lines:
        if not line or line != line.strip() or line.startswith("#") or "=" not in line:
            format_valid = False
            continue
        key, value = line.split("=", 1)
        if key != key.strip() or not key:
            format_valid = False
        assignments.append((key, bool(value.strip())))
    keys = [key for key, _ in assignments]
    exact_keys = format_valid and set(keys) == set(EXPECTED_CREDENTIAL_KEYS)
    duplicate_keys_absent = len(keys) == len(set(keys)) == len(EXPECTED_CREDENTIAL_KEYS)
    values_nonempty = exact_keys and all(present for _, present in assignments)
    acl_protected, broad_read_absent = _acl_security_flags(resolved)
    checks = {
        "credentialFileExists": resolved.is_file(),
        "credentialFileOutsideRepositoryWorktreesAndBackups": outside_restricted,
        "exactlyTwoPhysicalLines": len(raw_lines) == 2,
        "strictAssignmentFormat": format_valid,
        "accessControlInheritanceDisabled": acl_protected,
        "broadReadAccessAbsent": broad_read_absent,
        "exactRequiredKeySet": exact_keys,
        "duplicateKeysAbsent": duplicate_keys_absent,
        "allRequiredValuesNonEmpty": values_nonempty,
        "clientIdAvailable": exact_keys and values_nonempty,
        "clientSecretAvailable": exact_keys and values_nonempty,
        "credentialValuesIncluded": False,
        "credentialFileHashed": False,
    }
    if not all(value for key, value in checks.items() if key not in {
        "credentialValuesIncluded",
        "credentialFileHashed",
    }):
        raise CredentialBoundaryError("secure credential boundary failed")
    return checks


def select_date_window(today_utc: date) -> tuple[date, date]:
    end = today_utc - timedelta(days=1)
    start = end - timedelta(days=MAX_DATE_WINDOW_DAYS - 1)
    return start, end


def validate_geometry(geometry: dict[str, Any]) -> GeometrySummary:
    from pyproj import Geod
    from shapely.geometry import shape

    if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        raise RequestContractError("unsupported geometry type")
    candidate = shape(geometry)
    if candidate.is_empty or not candidate.is_valid:
        raise RequestContractError("invalid geometry")
    polygons = [candidate] if candidate.geom_type == "Polygon" else list(candidate.geoms)
    if len(polygons) != MAX_FIELD_GEOMETRIES:
        raise RequestContractError("geometry count exceeds bound")
    for polygon in polygons:
        exterior = list(polygon.exterior.coords)
        if len(exterior) < 4 or exterior[0] != exterior[-1]:
            raise RequestContractError("geometry ring is not closed")
    west, south, east, north = candidate.bounds
    if not (62.0 <= west < east <= 66.0 and 38.0 <= south < north <= 42.0):
        raise RequestContractError("geometry is outside the Bukhara qualification region")
    geod = Geod(ellps="WGS84")
    area_square_metres, _ = geod.geometry_area_perimeter(candidate)
    area_hectares = abs(area_square_metres) / 10_000.0
    if not 0.1 <= area_hectares <= 500.0:
        raise RequestContractError("geometry is not field-sized")
    return GeometrySummary(
        geometry_type=candidate.geom_type,
        fingerprint=geometry_fingerprint(geometry),
        bbox=(round(west, 6), round(south, 6), round(east, 6), round(north, 6)),
        area_hectares=round(area_hectares, 3),
    )


def _parse_utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RequestContractError("missing UTC timestamp")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise RequestContractError("invalid UTC timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise RequestContractError("timestamp is not explicit UTC")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise RequestContractError("timestamp is not explicit UTC")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_time_range(
    time_range: dict[str, Any],
    *,
    maximum_days: int,
) -> tuple[str, str, int]:
    if not isinstance(time_range, dict):
        raise RequestContractError("provider time range is absent")
    start = _parse_utc_timestamp(time_range.get("from"))
    end = _parse_utc_timestamp(time_range.get("to"))
    if end <= start:
        raise RequestContractError("provider time range is reversed")
    duration = end - start
    if duration.total_seconds() % 86400 != 0:
        raise RequestContractError("provider time range is not whole UTC days")
    days = int(duration.total_seconds() // 86400)
    if days < 1 or days > maximum_days:
        raise RequestContractError("provider time range exceeds bound")
    return _format_utc(start), _format_utc(end), days


def _provider_status_classification(status: int) -> tuple[str, bool]:
    if status in (401, 403):
        return "authentication", False
    if status == 429:
        return "quota_or_rate_limit", False
    if status in (408, 425):
        return "timeout", True
    if 500 <= status <= 599:
        return "provider_unavailable", True
    if status in (400, 422):
        return "request_rejected", False
    if 400 <= status <= 499:
        return "request_rejected", False
    if 200 <= status <= 299:
        return "success_http", False
    return "unexpected_http_status", False


def _safe_exception_classification(error: BaseException) -> tuple[str, bool]:
    if isinstance(error, httpx.TimeoutException):
        return "timeout", True
    if isinstance(error, httpx.RequestError):
        return "network", True
    return "provider_error", False


def inspect_statistical_response(response: httpx.Response) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "jsonParseable": False,
        "contractShapeValid": False,
        "intervalCount": 0,
        "usableIntervalCount": 0,
        "invalidIntervalCount": 0,
        "selectedIntervalFromUtc": None,
        "selectedIntervalToUtc": None,
        "intervalListSummary": [],
        "rawProviderBodyIncluded": False,
    }
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeError, ValueError):
        return metadata
    metadata["jsonParseable"] = True
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return metadata
    intervals = payload["data"]
    metadata["intervalCount"] = len(intervals)
    summaries: list[tuple[datetime, datetime, dict[str, Any]]] = []
    invalid_count = 0
    for interval in intervals:
        try:
            if not isinstance(interval, dict):
                raise RequestContractError("invalid interval object")
            bounds = interval.get("interval")
            if not isinstance(bounds, dict):
                raise RequestContractError("invalid interval bounds")
            interval_from = _parse_utc_timestamp(bounds.get("from"))
            interval_to = _parse_utc_timestamp(bounds.get("to"))
            if interval_to <= interval_from:
                raise RequestContractError("reversed interval")
            stats = (
                interval.get("outputs", {})
                .get("ndvi", {})
                .get("bands", {})
                .get("B0", {})
                .get("stats", {})
            )
            if not isinstance(stats, dict):
                raise RequestContractError("invalid stats object")
            sample_count = int(stats.get("sampleCount", 0))
            no_data_count = int(stats.get("noDataCount", 0))
            if sample_count < 0 or no_data_count < 0 or no_data_count > sample_count:
                raise RequestContractError("invalid pixel counts")
        except (RequestContractError, TypeError, ValueError, OverflowError):
            invalid_count += 1
            continue
        valid_pixel_count = sample_count - no_data_count
        summary = {
            "intervalFromUtc": _format_utc(interval_from),
            "intervalToUtc": _format_utc(interval_to),
            "sampleCount": sample_count,
            "noDataCount": no_data_count,
            "validPixelCount": valid_pixel_count,
            "validPixelsPct": (
                round(valid_pixel_count / sample_count * 100, 6)
                if sample_count > 0
                else 0.0
            ),
            "usable": valid_pixel_count > 0,
        }
        summaries.append((interval_from, interval_to, summary))
    summaries.sort(key=lambda item: (item[0], item[1]))
    metadata["invalidIntervalCount"] = invalid_count
    metadata["contractShapeValid"] = invalid_count == 0
    metadata["intervalListSummary"] = [item[2] for item in summaries]
    usable = [item for item in summaries if item[2]["usable"]]
    metadata["usableIntervalCount"] = len(usable)
    if usable:
        selected = max(usable, key=lambda item: (item[0], item[1]))[2]
        metadata["selectedIntervalFromUtc"] = selected["intervalFromUtc"]
        metadata["selectedIntervalToUtc"] = selected["intervalToUtc"]
    return metadata


class BoundedHttpRecorder:
    """Process-local guard around the real ``httpx.post`` entry point."""

    def __init__(
        self,
        delegate: Callable[..., httpx.Response],
        geometry_summary: GeometrySummary,
        provider_endpoints: SentinelProviderEndpoints = QUALIFICATION_ENDPOINTS,
    ) -> None:
        if provider_endpoints.name != QUALIFICATION_PROVIDER:
            raise RequestContractError("qualification provider is not CDSE")
        self._delegate = delegate
        self.geometry_summary = geometry_summary
        self.provider_endpoints = provider_endpoints
        self.ledger: list[dict[str, Any]] = []
        self.counts = {"oauth": 0, "statistical": 0, "process": 0}
        self.statistical_response_metadata: list[dict[str, Any]] = []
        self.raster_response_metadata: list[dict[str, Any]] = []
        self.contract_failure = False
        self.expected_process_interval: tuple[str, str] | None = None

    def bind_selected_observation(self, result: dict[str, Any]) -> None:
        from services.satellite import (
            NDVI_INDEX_CODE,
            NDVI_MASK_CONTRACT_ID,
            SENTINEL_DATASET,
        )

        try:
            interval_from, interval_to, days = _validate_time_range(
                {
                    "from": result["interval_from_utc"],
                    "to": result["interval_to_utc"],
                },
                maximum_days=1,
            )
        except (KeyError, RequestContractError):
            self.contract_failure = True
            raise RequestContractError("selected observation interval is invalid") from None
        if (
            days != 1
            or result.get("provider") != QUALIFICATION_PROVIDER
            or result.get("dataset") != SENTINEL_DATASET
            or result.get("index_code") != NDVI_INDEX_CODE
            or result.get("mask_contract_id") != NDVI_MASK_CONTRACT_ID
            or result.get("interval_is_daily") is not True
            or result.get("acquisition_timestamp_available") is not False
        ):
            self.contract_failure = True
            raise RequestContractError("selected observation contract is inconsistent")
        self.expected_process_interval = (interval_from, interval_to)

    def _validate_token(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        data = kwargs.get("data")
        if not isinstance(data, dict) or data.get("grant_type") != "client_credentials":
            raise RequestContractError("OAuth client-credentials form is invalid")
        if not all(
            isinstance(data.get(name), str) and bool(data.get(name).strip())
            for name in ("client_id", "client_secret")
        ):
            raise RequestContractError("OAuth credentials are unavailable")
        return {
            "operation": "oauth_client_credentials",
            "indices": [],
            "dateWindowDays": 0,
            "geometryFingerprint": None,
            "intervalFromUtc": None,
            "intervalToUtc": None,
            "dataset": None,
            "crs": None,
            "maskContractId": None,
            "evalscriptSha256": None,
        }

    def _validate_statistical(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        from services.satellite import (
            NDVI_EVALSCRIPT,
            NDVI_INDEX_CODE,
            NDVI_MASK_CONTRACT_ID,
            SENTINEL_CRS,
            SENTINEL_DATASET,
            SENTINEL_MAX_CLOUD_COVERAGE,
        )

        payload = kwargs.get("json")
        if not isinstance(payload, dict):
            raise RequestContractError("statistical JSON payload is absent")
        try:
            bounds = payload["input"]["bounds"]
            geometry = bounds["geometry"]
            crs = bounds["properties"]["crs"]
            data_items = payload["input"]["data"]
            data_item = data_items[0]
            input_time = data_item["dataFilter"]["timeRange"]
            aggregation = payload["aggregation"]
            aggregation_time = aggregation["timeRange"]
            evalscript = aggregation["evalscript"]
            calculations = payload["calculations"]
        except (KeyError, IndexError, TypeError):
            raise RequestContractError("statistical application payload is invalid") from None
        if len(data_items) != 1 or data_item.get("type") != SENTINEL_DATASET:
            raise RequestContractError("statistical dataset is not Sentinel-2 L2A")
        if geometry_fingerprint(geometry) != self.geometry_summary.fingerprint:
            raise RequestContractError("statistical geometry changed")
        if crs != SENTINEL_CRS:
            raise RequestContractError("statistical CRS changed")
        input_from, input_to, window_days = _validate_time_range(
            input_time,
            maximum_days=MAX_DATE_WINDOW_DAYS,
        )
        aggregation_from, aggregation_to, aggregation_days = _validate_time_range(
            aggregation_time,
            maximum_days=MAX_DATE_WINDOW_DAYS,
        )
        if (input_from, input_to) != (aggregation_from, aggregation_to):
            raise RequestContractError("statistical time ranges differ")
        if aggregation_days != window_days:
            raise RequestContractError("statistical window duration differs")
        if aggregation.get("aggregationInterval", {}).get("of") != "P1D":
            raise RequestContractError("statistical aggregation is not P1D")
        if evalscript != NDVI_EVALSCRIPT:
            raise RequestContractError("statistical NDVI evalscript changed")
        if not isinstance(calculations, dict) or set(calculations) != {"default"}:
            raise RequestContractError("statistical request is not NDVI-only")
        if data_item.get("dataFilter", {}).get("maxCloudCoverage") != SENTINEL_MAX_CLOUD_COVERAGE:
            raise RequestContractError("statistical cloud filter changed")
        if data_item.get("processing", {}).get("harmonizeValues") is not True:
            raise RequestContractError("statistical harmonization changed")
        return {
            "operation": "sentinel_statistical_ndvi",
            "indices": [NDVI_INDEX_CODE],
            "dateWindowDays": window_days,
            "geometryFingerprint": self.geometry_summary.fingerprint,
            "intervalFromUtc": input_from,
            "intervalToUtc": input_to,
            "dataset": SENTINEL_DATASET,
            "crs": SENTINEL_CRS,
            "maskContractId": NDVI_MASK_CONTRACT_ID,
            "evalscriptSha256": hashlib.sha256(NDVI_EVALSCRIPT.encode("utf-8")).hexdigest(),
        }

    def _validate_process(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        from services.ndvi_raster import EVALSCRIPT
        from services.satellite import (
            NDVI_INDEX_CODE,
            NDVI_MASK_CONTRACT_ID,
            SENTINEL_CRS,
            SENTINEL_DATASET,
            SENTINEL_MAX_CLOUD_COVERAGE,
        )

        payload = kwargs.get("json")
        if not isinstance(payload, dict):
            raise RequestContractError("Process JSON payload is absent")
        try:
            bounds = payload["input"]["bounds"]
            geometry = bounds["geometry"]
            crs = bounds["properties"]["crs"]
            data_items = payload["input"]["data"]
            data_item = data_items[0]
            time_range = data_item["dataFilter"]["timeRange"]
            output = payload["output"]
            evalscript = payload["evalscript"]
        except (KeyError, IndexError, TypeError):
            raise RequestContractError("Process application payload is invalid") from None
        if len(data_items) != 1 or data_item.get("type") != SENTINEL_DATASET:
            raise RequestContractError("Process dataset is not Sentinel-2 L2A")
        if geometry_fingerprint(geometry) != self.geometry_summary.fingerprint:
            raise RequestContractError("Process geometry changed")
        if crs != SENTINEL_CRS:
            raise RequestContractError("Process CRS changed")
        interval_from, interval_to, interval_days = _validate_time_range(
            time_range,
            maximum_days=1,
        )
        if interval_days != 1:
            raise RequestContractError("Process interval is not daily")
        if self.expected_process_interval is None:
            raise RequestContractError("Process request was not bound to a Statistical interval")
        if (interval_from, interval_to) != self.expected_process_interval:
            raise RequestContractError("Process interval differs from selected Statistical interval")
        if data_item.get("dataFilter", {}).get("maxCloudCoverage") != SENTINEL_MAX_CLOUD_COVERAGE:
            raise RequestContractError("Process cloud filter changed")
        if data_item.get("processing", {}).get("harmonizeValues") is not True:
            raise RequestContractError("Process harmonization changed")
        if output.get("width") != MAX_RASTER_SIZE or output.get("height") != MAX_RASTER_SIZE:
            raise RequestContractError("Process raster dimensions changed")
        responses = output.get("responses")
        if (
            not isinstance(responses, list)
            or len(responses) != 1
            or responses[0].get("identifier") != "default"
            or responses[0].get("format", {}).get("type") != "image/png"
        ):
            raise RequestContractError("Process output is not one PNG")
        if evalscript != EVALSCRIPT:
            raise RequestContractError("Process NDVI evalscript changed")
        return {
            "operation": "sentinel_process_ndvi_png",
            "indices": [NDVI_INDEX_CODE],
            "dateWindowDays": interval_days,
            "geometryFingerprint": self.geometry_summary.fingerprint,
            "intervalFromUtc": interval_from,
            "intervalToUtc": interval_to,
            "dataset": SENTINEL_DATASET,
            "crs": SENTINEL_CRS,
            "maskContractId": NDVI_MASK_CONTRACT_ID,
            "evalscriptSha256": hashlib.sha256(EVALSCRIPT.encode("utf-8")).hexdigest(),
        }

    # Backward-compatible name retained for focused tests.
    _validate_raster = _validate_process

    def _request_metadata(self, url: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if url == self.provider_endpoints.token_url:
            kind = "oauth"
            endpoint_class = "official_cdse_sentinel_hub_oauth_https"
            metadata = self._validate_token(kwargs)
        elif url == self.provider_endpoints.statistical_url:
            kind = "statistical"
            endpoint_class = "official_cdse_sentinel_hub_statistical_https"
            metadata = self._validate_statistical(kwargs)
        elif url == self.provider_endpoints.process_url:
            kind = "process"
            endpoint_class = "official_cdse_sentinel_hub_process_https"
            metadata = self._validate_process(kwargs)
        else:
            raise RequestContractError("unapproved live endpoint")
        metadata["endpointClass"] = endpoint_class
        metadata["kind"] = kind
        return kind, metadata

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        try:
            kind, metadata = self._request_metadata(str(url), kwargs)
            maximum = {
                "oauth": MAX_OAUTH_REQUESTS,
                "statistical": MAX_STATISTICAL_REQUESTS,
                "process": MAX_PROCESS_REQUESTS,
            }[kind]
            if self.counts[kind] >= maximum:
                raise RequestContractError(f"{kind} request bound exceeded")
        except RequestContractError:
            self.contract_failure = True
            raise

        self.counts[kind] += 1
        entry = {
            "requestOrdinal": len(self.ledger) + 1,
            "providerPreset": self.provider_endpoints.name,
            "endpointClass": metadata["endpointClass"],
            "operationClass": metadata["operation"],
            "indexCodes": metadata["indices"],
            "dataset": metadata["dataset"],
            "crs": metadata["crs"],
            "dateWindowDays": metadata["dateWindowDays"],
            "intervalFromUtc": metadata["intervalFromUtc"],
            "intervalToUtc": metadata["intervalToUtc"],
            "geometryFingerprint": metadata["geometryFingerprint"],
            "maskContractId": metadata["maskContractId"],
            "evalscriptSha256": metadata["evalscriptSha256"],
            "httpStatus": None,
            "durationMs": None,
            "providerClassification": "pending",
            "parsedObservationCount": 0,
            "retryDecision": "not_evaluated",
        }
        self.ledger.append(entry)

        bounded_timeout = {
            "oauth": httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
            "statistical": httpx.Timeout(connect=10.0, read=60.0, write=15.0, pool=10.0),
            "process": httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0),
        }[kind]
        kwargs["timeout"] = bounded_timeout
        kwargs["follow_redirects"] = False
        started = time.perf_counter()
        try:
            response = self._delegate(url, **kwargs)
        except BaseException as error:
            category, _ = _safe_exception_classification(error)
            entry["durationMs"] = round((time.perf_counter() - started) * 1000, 3)
            entry["providerClassification"] = category
            raise
        entry["durationMs"] = round((time.perf_counter() - started) * 1000, 3)
        entry["httpStatus"] = response.status_code
        category, _ = _provider_status_classification(response.status_code)
        entry["providerClassification"] = category

        if kind == "statistical" and 200 <= response.status_code <= 299:
            response_metadata = inspect_statistical_response(response)
            self.statistical_response_metadata.append(response_metadata)
            if not response_metadata["jsonParseable"] or not response_metadata["contractShapeValid"]:
                entry["providerClassification"] = "invalid_response"
            elif response_metadata["usableIntervalCount"] == 0:
                entry["providerClassification"] = "no_data"
        if kind == "process":
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            response_metadata = {
                "contentType": content_type or None,
                "responseSha256": hashlib.sha256(response.content).hexdigest(),
                "byteCount": len(response.content),
                "nonEmptyBytes": bool(response.content),
                "rawRasterPersisted": False,
                "pixelArrayPersisted": False,
            }
            self.raster_response_metadata.append(response_metadata)
        return response

    def latest(self, kind: str) -> dict[str, Any] | None:
        normalized_kind = "process" if kind == "raster" else kind
        operation = {
            "oauth": "oauth_client_credentials",
            "statistical": "sentinel_statistical_ndvi",
            "process": "sentinel_process_ndvi_png",
        }[normalized_kind]
        for entry in reversed(self.ledger):
            if entry["operationClass"] == operation:
                return entry
        return None


def retryable_entry(entry: dict[str, Any] | None) -> bool:
    return bool(entry and entry.get("providerClassification") in {
        "timeout",
        "network",
        "provider_unavailable",
    })


def mark_retry_decision(entry: dict[str, Any] | None, decision: str) -> None:
    if entry is not None:
        entry["retryDecision"] = decision


def classify_program_outcome(
    *,
    contract_failure: bool,
    oauth_succeeded: bool,
    statistical_entry: dict[str, Any] | None,
    statistical_parseable: bool,
    usable_observation: bool,
    provenance_passed: bool,
    quality_passed: bool,
    process_entry: dict[str, Any] | None = None,
    raster_entry: dict[str, Any] | None = None,
    raster_content_valid: bool = False,
    raster_passed: bool | None = None,
    reconciliation_passed: bool = False,
    mocks_used: bool = False,
) -> ProgramOutcome:
    process_entry = process_entry or raster_entry
    if raster_passed is not None:
        raster_content_valid = raster_passed
    entries = [entry for entry in (statistical_entry, process_entry) if entry]
    categories = {str(entry.get("providerClassification")) for entry in entries}
    statuses = {entry.get("httpStatus") for entry in entries}
    internal_categories = {
        "request_rejected",
        "invalid_response",
        "invalid_raster_content",
        "unexpected_http_status",
        "provider_error",
    }
    if (
        contract_failure
        or mocks_used
        or categories.intersection(internal_categories)
        or statuses.intersection({400, 422})
    ):
        return ProgramOutcome(
            "FAIL_TASK_211_INTERNAL_SENTINEL_BLOCKER",
            "FAIL_PROGRAM_R1_INTERNAL_SENTINEL_BLOCKER",
            "FAIL",
            EXIT_INTERNAL_BLOCKER,
        )
    if "authentication" in categories or statuses.intersection({401, 403}):
        return ProgramOutcome(
            "PARTIAL_TASK_211_SENTINEL_ACCOUNT_PREREQUISITE",
            "PARTIAL_PROGRAM_R1_SENTINEL_ACCOUNT_PREREQUISITE",
            "BLOCKED",
            EXIT_ACCOUNT_PREREQUISITE,
        )
    if categories.intersection({
        "quota_or_rate_limit",
        "timeout",
        "network",
        "provider_unavailable",
        "no_data",
    }):
        return ProgramOutcome(
            "PARTIAL_TASK_211_SENTINEL_PROVIDER_PREREQUISITE",
            "PARTIAL_PROGRAM_R1_SENTINEL_PROVIDER_PREREQUISITE",
            "BLOCKED",
            EXIT_PROVIDER_PREREQUISITE,
        )
    if not oauth_succeeded:
        return ProgramOutcome(
            "PARTIAL_TASK_211_SENTINEL_PROVIDER_PREREQUISITE",
            "PARTIAL_PROGRAM_R1_SENTINEL_PROVIDER_PREREQUISITE",
            "BLOCKED",
            EXIT_PROVIDER_PREREQUISITE,
        )
    if not statistical_parseable:
        return ProgramOutcome(
            "FAIL_TASK_211_INTERNAL_SENTINEL_BLOCKER",
            "FAIL_PROGRAM_R1_INTERNAL_SENTINEL_BLOCKER",
            "FAIL",
            EXIT_INTERNAL_BLOCKER,
        )
    if not usable_observation or not quality_passed:
        return ProgramOutcome(
            "PARTIAL_TASK_211_SENTINEL_PROVIDER_PREREQUISITE",
            "PARTIAL_PROGRAM_R1_SENTINEL_PROVIDER_PREREQUISITE",
            "BLOCKED",
            EXIT_PROVIDER_PREREQUISITE,
        )
    if not provenance_passed or not raster_content_valid or not reconciliation_passed:
        return ProgramOutcome(
            "FAIL_TASK_211_INTERNAL_SENTINEL_BLOCKER",
            "FAIL_PROGRAM_R1_INTERNAL_SENTINEL_BLOCKER",
            "FAIL",
            EXIT_INTERNAL_BLOCKER,
        )
    return ProgramOutcome(
        "PASS_TASK_211_PROGRAM_R1_FINAL_CLOSURE",
        "PASS_PROGRAM_R1_FULL_MERGE_READINESS",
        "PASS",
        EXIT_PASS,
    )


def assert_sanitized(serialized: str, forbidden_values: list[str]) -> None:
    lowered = serialized.casefold()
    forbidden_fragments = (
        '"authorization"',
        '"token"',
        '"access_token"',
        '"accesstoken"',
        '"oauth_token"',
        '"oauthtoken"',
        '"sentinel_hub_client_id"',
        '"sentinel_hub_client_secret"',
        '"client_id"',
        '"client_secret"',
        '"rawresponsebody"',
        '"rawproviderbody"',
        '"raw_provider_body"',
        '"raw_response_body"',
        '"rasterbytes"',
        '"pixelvalues"',
        "bearer ",
        "-----begin private key-----",
        "-----begin rsa private key-----",
        "-----begin ec private key-----",
        "-----begin openssh private key-----",
        "postgresql://",
        "postgres://",
        "redis://",
        "rediss://",
    )
    if any(fragment in lowered for fragment in forbidden_fragments):
        raise EvidenceSanitizationError("forbidden evidence field")
    if re.search(r"postgres(?:ql)?(?:\+[a-z0-9_]+)?://", lowered):
        raise EvidenceSanitizationError("database URL reached evidence")
    if re.search(r"https?://[^\s/:@]+:[^\s/@]+@", serialized, flags=re.IGNORECASE):
        raise EvidenceSanitizationError("credentialed URL reached evidence")
    if any(value and value in serialized for value in forbidden_values):
        raise EvidenceSanitizationError("credential or token value reached evidence")


def _reconciliation_facts(
    stats_result: dict[str, Any] | None,
    statistical_entry: dict[str, Any] | None,
    process_entry: dict[str, Any] | None,
    raster_validation: dict[str, Any] | None,
) -> dict[str, Any]:
    from services.satellite import (
        NDVI_INDEX_CODE,
        NDVI_MASK_CONTRACT_ID,
        SENTINEL_CRS,
        SENTINEL_DATASET,
    )

    same_provider = bool(
        stats_result
        and statistical_entry
        and process_entry
        and stats_result.get("provider") == QUALIFICATION_PROVIDER
        and statistical_entry.get("providerPreset") == QUALIFICATION_PROVIDER
        and process_entry.get("providerPreset") == QUALIFICATION_PROVIDER
    )
    same_dataset = bool(
        stats_result
        and statistical_entry
        and process_entry
        and stats_result.get("dataset") == SENTINEL_DATASET
        and statistical_entry.get("dataset") == SENTINEL_DATASET
        and process_entry.get("dataset") == SENTINEL_DATASET
    )
    same_index = bool(
        stats_result
        and stats_result.get("index_code") == NDVI_INDEX_CODE
        and statistical_entry
        and statistical_entry.get("indexCodes") == [NDVI_INDEX_CODE]
        and process_entry
        and process_entry.get("indexCodes") == [NDVI_INDEX_CODE]
    )
    same_geometry = bool(
        statistical_entry
        and process_entry
        and statistical_entry.get("geometryFingerprint")
        == process_entry.get("geometryFingerprint")
    )
    same_crs = bool(
        statistical_entry
        and process_entry
        and statistical_entry.get("crs") == SENTINEL_CRS
        and process_entry.get("crs") == SENTINEL_CRS
    )
    exact_interval = bool(
        stats_result
        and process_entry
        and stats_result.get("interval_from_utc") == process_entry.get("intervalFromUtc")
        and stats_result.get("interval_to_utc") == process_entry.get("intervalToUtc")
    )
    same_mask = bool(
        stats_result
        and statistical_entry
        and process_entry
        and raster_validation
        and stats_result.get("mask_contract_id") == NDVI_MASK_CONTRACT_ID
        and statistical_entry.get("maskContractId") == NDVI_MASK_CONTRACT_ID
        and process_entry.get("maskContractId") == NDVI_MASK_CONTRACT_ID
        and raster_validation.get("maskContractId") == NDVI_MASK_CONTRACT_ID
    )
    facts = {
        "sameProvider": same_provider,
        "sameDataset": same_dataset,
        "sameIndex": same_index,
        "sameGeometryFingerprint": same_geometry,
        "sameCrs": same_crs,
        "exactIntervalBounds": exact_interval,
        "compatibleMaskContract": same_mask,
        "sameHarmonization": bool(statistical_entry and process_entry),
        "sameCloudFilter": bool(statistical_entry and process_entry),
        "exactAcquisitionTimestampClaimed": False,
    }
    facts["passed"] = all(
        facts[key]
        for key in (
            "sameProvider",
            "sameDataset",
            "sameIndex",
            "sameGeometryFingerprint",
            "sameCrs",
            "exactIntervalBounds",
            "compatibleMaskContract",
            "sameHarmonization",
            "sameCloudFilter",
        )
    )
    return facts


def run_live(
    *,
    worktree: Path,
    evidence_root: Path,
    expected_head: str,
    starting_head: str,
    runtime_env: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], int]:
    baseline = assert_git_baseline(worktree, expected_head, starting_head)
    credential_checks = validate_credential_boundary(runtime_env)

    os.environ.pop("SENTINEL_HUB_CLIENT_ID", None)
    os.environ.pop("SENTINEL_HUB_CLIENT_SECRET", None)
    os.environ["SENTINEL_HUB_PROVIDER"] = QUALIFICATION_PROVIDER
    os.environ["AGROSAT_RUNTIME_ENV_FILE"] = str(runtime_env.resolve(strict=True))
    os.environ["RELEASE_REVISION"] = expected_head

    from config import settings
    from services import ndvi_raster
    from services.satellite import (
        CANONICAL_DATE_SEMANTICS,
        NDVI_EVALSCRIPT,
        NDVI_INDEX_CODE,
        NDVI_MASK_CONTRACT_ID,
        REAL_SATELLITE_SOURCE,
        SENTINEL_CRS,
        SENTINEL_DATASET,
        SENTINEL_MAX_CLOUD_COVERAGE,
        SentinelHubService,
        validate_ndvi_quality,
    )
    from services.satellite_safety import require_payload_provenance, require_real_service

    credential_checks["runtimeSettingsLoad"] = True
    credential_checks["clientIdAvailable"] = bool(
        isinstance(settings.sentinel_hub_client_id, str)
        and settings.sentinel_hub_client_id.strip()
    )
    credential_checks["clientSecretAvailable"] = bool(
        isinstance(settings.sentinel_hub_client_secret, str)
        and settings.sentinel_hub_client_secret.strip()
    )
    if not credential_checks["clientIdAvailable"] or not credential_checks["clientSecretAvailable"]:
        raise CredentialBoundaryError("runtime settings did not load credentials")
    if settings.sentinel_hub_provider != QUALIFICATION_PROVIDER:
        raise RequestContractError("runtime provider is not CDSE")
    if settings.wialon_enabled is not False:
        raise RequestContractError("Wialon must remain disabled")

    geometry_summary = validate_geometry(QUALIFICATION_GEOMETRY)
    from shapely.geometry import shape

    geometry_wkt = shape(QUALIFICATION_GEOMETRY).wkt
    date_from, date_to = select_date_window(datetime.now(timezone.utc).date())
    if (date_to - date_from).days + 1 != MAX_DATE_WINDOW_DAYS:
        raise RequestContractError("runtime date window changed")

    service = SentinelHubService()
    require_real_service(service)
    if (
        service.provider != QUALIFICATION_PROVIDER
        or service.provider_metadata != QUALIFICATION_ENDPOINTS.sanitized_metadata()
        or service._provider_endpoints != QUALIFICATION_ENDPOINTS
    ):
        raise RequestContractError("Sentinel provider chain is inconsistent")

    original_post = httpx.post
    recorder = BoundedHttpRecorder(original_post, geometry_summary, QUALIFICATION_ENDPOINTS)
    httpx.post = recorder.post
    original_service_factory = ndvi_raster.get_satellite_service
    ndvi_raster.get_satellite_service = lambda: service
    logging.getLogger("services.satellite").setLevel(logging.CRITICAL)

    stats_result: dict[str, Any] | None = None
    stats_parseable = False
    quality_passed = False
    quality_reason_class = "not_executed"
    provenance_passed = False
    raster_validation: dict[str, Any] | None = None
    raster_error_class: str | None = None
    raster_rejection_reason: str | None = None
    retry_count = 0
    try:
        for attempt in range(MAX_RETRYABLE_RETRIES + 1):
            stats_result = service.get_ndvi_stats(
                geometry_wkt,
                date_from,
                date_to,
                aggregation_interval="P1D",
            )
            oauth_entry = recorder.latest("oauth")
            stats_entry = recorder.latest("statistical")
            response_meta = (
                recorder.statistical_response_metadata[-1]
                if recorder.statistical_response_metadata
                else None
            )
            stats_parseable = bool(
                response_meta
                and response_meta["jsonParseable"]
                and response_meta["contractShapeValid"]
            )
            oauth_succeeded_now = bool(service._access_token)
            terminal_entry = stats_entry or oauth_entry
            if (
                not oauth_succeeded_now
                and oauth_entry
                and oauth_entry.get("httpStatus") == 200
            ):
                oauth_entry["providerClassification"] = "invalid_response"
            if (
                stats_result is None
                and stats_entry
                and stats_entry.get("httpStatus") == 200
                and response_meta
                and response_meta.get("usableIntervalCount", 0) > 0
            ):
                stats_entry["providerClassification"] = "invalid_response"
            if stats_result is not None:
                if stats_entry:
                    stats_entry["providerClassification"] = "sentinel_2_observation"
                    stats_entry["parsedObservationCount"] = 1
                mark_retry_decision(terminal_entry, "stop_application_parse_succeeded")
                break
            if retryable_entry(terminal_entry) and attempt < MAX_RETRYABLE_RETRIES:
                if recorder.counts["oauth"] >= MAX_OAUTH_REQUESTS and not oauth_succeeded_now:
                    mark_retry_decision(terminal_entry, "stop_oauth_bound")
                    break
                mark_retry_decision(terminal_entry, "retry_once")
                retry_count += 1
                continue
            mark_retry_decision(terminal_entry, "stop_no_retry")
            break

        oauth_succeeded = bool(service._access_token)
        oauth_entry = recorder.latest("oauth")
        if oauth_succeeded and oauth_entry:
            oauth_entry["providerClassification"] = "oauth_success"
            if oauth_entry["retryDecision"] == "not_evaluated":
                oauth_entry["retryDecision"] = "token_cached_for_application_requests"

        if stats_result is not None:
            require_payload_provenance(stats_result)
            provenance_passed = (
                stats_result.get("satellite") == REAL_SATELLITE_SOURCE
                and stats_result.get("source") == REAL_SATELLITE_SOURCE
            )
            quality_passed, _quality_reason = validate_ndvi_quality(
                stats_result.get("mean_ndvi"),
                cloud_cover_pct=stats_result.get("cloud_cover_pct"),
                min_ndvi=stats_result.get("min_ndvi"),
                max_ndvi=stats_result.get("max_ndvi"),
                field_name="task_211_isolated_qualification_geometry",
            )
            quality_reason_class = "accepted" if quality_passed else "rejected_by_existing_thresholds"

        if stats_result is not None and quality_passed and provenance_passed:
            recorder.bind_selected_observation(stats_result)
            for attempt in range(MAX_RETRYABLE_RETRIES + 1):
                try:
                    validated = ndvi_raster.request_process_png_for_interval(
                        QUALIFICATION_GEOMETRY,
                        stats_result["interval_from_utc"],
                        stats_result["interval_to_utc"],
                        MAX_RASTER_SIZE,
                    )
                    raster_validation = validated.validation.as_sanitized_dict()
                    del validated
                    process_entry = recorder.latest("process")
                    if process_entry:
                        process_entry["providerClassification"] = "sentinel_2_visible_png"
                        process_entry["retryDecision"] = "stop_decoded_visible_png"
                    if recorder.raster_response_metadata:
                        recorder.raster_response_metadata[-1].update(raster_validation)
                    break
                except BaseException as error:
                    raster_error_class = type(error).__name__
                    process_entry = recorder.latest("process")
                    if isinstance(error, ndvi_raster.RasterUpstreamInvalid):
                        raster_rejection_reason = error.reason_code
                        if process_entry:
                            process_entry["providerClassification"] = "invalid_raster_content"
                            process_entry["retryDecision"] = "stop_internal_raster_content_failure"
                        if recorder.raster_response_metadata:
                            recorder.raster_response_metadata[-1].update({
                                "contentValid": False,
                                "rejectionReasonClass": error.reason_code,
                                "rawRasterPersisted": False,
                                "pixelArrayPersisted": False,
                            })
                        break
                    if retryable_entry(process_entry) and attempt < MAX_RETRYABLE_RETRIES:
                        mark_retry_decision(process_entry, "retry_once")
                        retry_count += 1
                        continue
                    mark_retry_decision(process_entry, "stop_no_retry")
                    break

        stats_entry = recorder.latest("statistical")
        process_entry = recorder.latest("process")
        response_meta = (
            recorder.statistical_response_metadata[-1]
            if recorder.statistical_response_metadata
            else {
                "jsonParseable": False,
                "contractShapeValid": False,
                "intervalCount": 0,
                "usableIntervalCount": 0,
                "invalidIntervalCount": 0,
                "selectedIntervalFromUtc": None,
                "selectedIntervalToUtc": None,
                "intervalListSummary": [],
                "rawProviderBodyIncluded": False,
            }
        )
        reconciliation = _reconciliation_facts(
            stats_result,
            stats_entry,
            process_entry,
            raster_validation,
        )
        outcome = classify_program_outcome(
            contract_failure=recorder.contract_failure,
            oauth_succeeded=oauth_succeeded,
            statistical_entry=stats_entry or recorder.latest("oauth"),
            statistical_parseable=stats_parseable,
            usable_observation=stats_result is not None,
            provenance_passed=provenance_passed,
            quality_passed=quality_passed,
            process_entry=process_entry,
            raster_content_valid=bool(raster_validation and raster_validation.get("contentValid")),
            reconciliation_passed=bool(reconciliation["passed"]),
            mocks_used=False,
        )
        gate4_marker = (
            "PASS_GATE4_LIVE_SENTINEL_REAL_RASTER"
            if outcome.status == "PASS"
            else outcome.task_marker
        )
        captured_date = stats_result.get("captured_date") if stats_result else None
        selected_from = stats_result.get("interval_from_utc") if stats_result else None
        selected_to = stats_result.get("interval_to_utc") if stats_result else None
        process_from = process_entry.get("intervalFromUtc") if process_entry else None
        process_to = process_entry.get("intervalToUtc") if process_entry else None
        process_response_meta = (
            recorder.raster_response_metadata[-1]
            if recorder.raster_response_metadata
            else {
                "contentType": None,
                "responseSha256": None,
                "byteCount": 0,
                "nonEmptyBytes": False,
                "contentValid": False,
                "rawRasterPersisted": False,
                "pixelArrayPersisted": False,
            }
        )

        qualification = {
            "schemaVersion": 2,
            "program": PROGRAM,
            "recordedAtUtc": datetime.now(timezone.utc).isoformat(),
            "headAtQualification": expected_head,
            "startingHead": starting_head,
            "baseline": baseline,
            "provider": QUALIFICATION_PROVIDER,
            "satelliteDataSource": "Sentinel-2 L2A",
            "providerEndpointClass": QUALIFICATION_ENDPOINTS.endpoint_class,
            "providerIsMock": False,
            "credentialBoundary": credential_checks,
            "geometry": geometry_summary.as_dict(),
            "dateWindow": {
                "fromDateInclusive": date_from.isoformat(),
                "toDateInclusive": date_to.isoformat(),
                "calendarDays": MAX_DATE_WINDOW_DAYS,
                "selection": "runtime_utc_yesterday_back_at_most_14_calendar_days",
            },
            "hardBounds": {
                "maximumDateWindowDays": MAX_DATE_WINDOW_DAYS,
                "maximumFieldGeometries": MAX_FIELD_GEOMETRIES,
                "maximumIndicesPerStatisticalRequest": MAX_INDEX_CODES,
                "maximumStatisticalRequests": MAX_STATISTICAL_REQUESTS,
                "maximumProcessRequests": MAX_PROCESS_REQUESTS,
                "maximumOauthRequests": MAX_OAUTH_REQUESTS,
                "maximumRasterWidthAndHeight": MAX_RASTER_SIZE,
                "maximumRetryableRetriesPerOperation": MAX_RETRYABLE_RETRIES,
            },
            "actualRequestCounts": dict(recorder.counts),
            "retryCount": retry_count,
            "applicationPath": {
                "settingsLoader": "backend.config.Settings via AGROSAT_RUNTIME_ENV_FILE",
                "statisticalService": "services.satellite.SentinelHubService.get_ndvi_stats",
                "statisticalParser": "services.satellite.SentinelHubService._parse_stats_response",
                "qualityValidator": "services.satellite.validate_ndvi_quality",
                "processPayload": "services.ndvi_raster.build_process_payload_for_interval",
                "processRequestAndValidation": "services.ndvi_raster.request_process_png_for_interval",
                "decodedContentValidator": "services.ndvi_raster.validate_raster_content",
                "cacheBypassed": True,
            },
            "oauth": {
                "httpStatus": oauth_entry.get("httpStatus") if oauth_entry else None,
                "clientCredentialsExchangeSucceeded": oauth_succeeded,
                "tokenValueIncluded": False,
                "authorizationHeaderIncluded": False,
            },
            "statistical": {
                "httpStatus": stats_entry.get("httpStatus") if stats_entry else None,
                "provider": stats_result.get("provider") if stats_result else None,
                "indexCode": stats_result.get("index_code") if stats_result else NDVI_INDEX_CODE,
                "dataset": stats_result.get("dataset") if stats_result else SENTINEL_DATASET,
                "crs": SENTINEL_CRS,
                "geometryFingerprint": geometry_summary.fingerprint if stats_entry else None,
                "requestSucceeded": bool(stats_entry and stats_entry.get("httpStatus") == 200),
                "applicationResultParseable": stats_parseable and stats_result is not None,
                "providerIntervalCount": response_meta["intervalCount"],
                "providerUsableIntervalCount": response_meta["usableIntervalCount"],
                "providerInvalidIntervalCount": response_meta["invalidIntervalCount"],
                "intervalListSummary": response_meta["intervalListSummary"],
                "parsedObservationCount": 1 if stats_result is not None else 0,
                "selectedIntervalFromUtc": selected_from,
                "selectedIntervalToUtc": selected_to,
                "canonicalDate": captured_date,
                "canonicalDateSemantics": (
                    stats_result.get("captured_date_semantics")
                    if stats_result
                    else CANONICAL_DATE_SEMANTICS
                ),
                "aggregationIntervalSemantics": (
                    stats_result.get("aggregation_interval_semantics")
                    if stats_result
                    else "half_open_utc_aggregation_bucket_[from,to)"
                ),
                "exactAcquisitionTimestampAvailable": False,
                "sampleCount": stats_result.get("sample_count") if stats_result else None,
                "noDataCount": stats_result.get("no_data_count") if stats_result else None,
                "validPixelCount": stats_result.get("valid_pixel_count") if stats_result else None,
                "validPixelsPct": stats_result.get("valid_pixels_pct") if stats_result else None,
                "qualityValidationPassed": quality_passed,
                "qualityOutcomeClass": quality_reason_class,
                "thresholdsChanged": False,
                "realProvenanceValidated": provenance_passed,
                "maskContractId": NDVI_MASK_CONTRACT_ID,
                "evalscriptSha256": hashlib.sha256(NDVI_EVALSCRIPT.encode("utf-8")).hexdigest(),
                "rawProviderBodyIncluded": False,
            },
            "process": {
                "httpStatus": process_entry.get("httpStatus") if process_entry else None,
                "attempted": process_entry is not None,
                "provider": QUALIFICATION_PROVIDER if process_entry else None,
                "indexCode": NDVI_INDEX_CODE,
                "dataset": SENTINEL_DATASET,
                "crs": SENTINEL_CRS,
                "geometryFingerprint": geometry_summary.fingerprint if process_entry else None,
                "requestedIntervalFromUtc": process_from,
                "requestedIntervalToUtc": process_to,
                "exactAcquisitionTimestampAvailableInPngContract": False,
                "maskContractId": NDVI_MASK_CONTRACT_ID,
                "maxCloudCoverage": SENTINEL_MAX_CLOUD_COVERAGE,
                "harmonizeValues": True,
                "rasterSize": MAX_RASTER_SIZE,
                "response": process_response_meta,
                "decodedContentValid": bool(
                    raster_validation and raster_validation.get("contentValid")
                ),
                "errorClass": raster_error_class,
                "rejectionReasonClass": raster_rejection_reason,
            },
            "reconciliation": reconciliation,
            "mocksUsed": False,
            "databaseAccessAttempted": False,
            "redisAccessAttempted": False,
            "productionWrites": 0,
            "wialonExternalCalls": 0,
            "taskStatus": outcome.status,
            "taskMarker": outcome.task_marker,
            "programMarker": outcome.program_marker,
        }
        request_ledger = {
            "schemaVersion": 2,
            "headAtQualification": expected_head,
            "providerPreset": QUALIFICATION_PROVIDER,
            "requests": recorder.ledger,
            "counts": dict(recorder.counts),
            "rawProviderBodiesIncluded": False,
            "rawRasterIncluded": False,
            "oauthTokensIncluded": False,
            "authorizationHeadersIncluded": False,
            "productionWrites": 0,
        }
        security_review = {
            "schemaVersion": 2,
            "headAtQualification": expected_head,
            "credentialBoundary": credential_checks,
            "officialCdseEndpointsOnly": all(
                entry.get("providerPreset") == QUALIFICATION_PROVIDER
                and entry["endpointClass"].startswith("official_cdse_sentinel_hub_")
                for entry in recorder.ledger
            ),
            "providerChainPresetConsistent": all(
                entry.get("providerPreset") == QUALIFICATION_PROVIDER
                for entry in recorder.ledger
            ),
            "credentialValuesIncluded": False,
            "oauthTokensIncluded": False,
            "authorizationHeadersIncluded": False,
            "rawProviderBodiesIncluded": False,
            "rawRasterIncluded": False,
            "pixelArraysIncluded": False,
            "databaseUrlsIncluded": False,
            "redisUrlsIncluded": False,
            "privateKeysIncluded": False,
            "databaseAccessAttempted": False,
            "redisAccessAttempted": False,
            "wialonExternalCalls": 0,
            "productionWrites": 0,
            "sanitization": "PASS",
        }
        gate4 = {
            "schemaVersion": 2,
            "gate": "GATE4",
            "headAtQualification": expected_head,
            "status": "PASS" if outcome.status == "PASS" else outcome.status,
            "marker": gate4_marker,
            "taskMarker": outcome.task_marker,
            "programMarker": outcome.program_marker,
            "realSentinelRasterContent": bool(
                raster_validation and raster_validation.get("contentValid")
            ),
            "intervalReconciliation": bool(reconciliation["exactIntervalBounds"]),
            "mocksUsed": False,
            "wialonExternalCalls": 0,
            "productionWrites": 0,
        }
        forbidden_values = [
            settings.sentinel_hub_client_id,
            settings.sentinel_hub_client_secret,
            service._access_token or "",
        ]
        assert_sanitized(
            canonical_json({
                "qualification": qualification,
                "requestLedger": request_ledger,
                "securityReview": security_review,
                "gate4": gate4,
            }),
            forbidden_values,
        )
        return qualification, request_ledger, security_review, gate4, outcome.exit_code
    finally:
        httpx.post = original_post
        ndvi_raster.get_satellite_service = original_service_factory
        service._access_token = None
        service._token_expires_at = 0


def safe_failure_artifacts(
    *,
    expected_head: str,
    task_marker: str,
    program_marker: str,
    status: str,
    error_class: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    qualification = {
        "schemaVersion": 2,
        "program": PROGRAM,
        "headAtQualification": expected_head,
        "provider": QUALIFICATION_PROVIDER,
        "providerEndpointClass": QUALIFICATION_ENDPOINTS.endpoint_class,
        "providerIsMock": False,
        "taskStatus": status,
        "taskMarker": task_marker,
        "programMarker": program_marker,
        "errorClass": error_class,
        "credentialValuesIncluded": False,
        "oauthTokensIncluded": False,
        "authorizationHeadersIncluded": False,
        "rawProviderBodiesIncluded": False,
        "rawRasterIncluded": False,
        "mocksUsed": False,
        "databaseAccessAttempted": False,
        "redisAccessAttempted": False,
        "wialonExternalCalls": 0,
        "productionWrites": 0,
    }
    ledger = {
        "schemaVersion": 2,
        "headAtQualification": expected_head,
        "providerPreset": QUALIFICATION_PROVIDER,
        "requests": [],
        "counts": {"oauth": 0, "statistical": 0, "process": 0},
        "rawProviderBodiesIncluded": False,
        "rawRasterIncluded": False,
        "oauthTokensIncluded": False,
        "authorizationHeadersIncluded": False,
        "productionWrites": 0,
    }
    security = {
        "schemaVersion": 2,
        "headAtQualification": expected_head,
        "status": status,
        "errorClass": error_class,
        "credentialValuesIncluded": False,
        "oauthTokensIncluded": False,
        "authorizationHeadersIncluded": False,
        "rawProviderBodiesIncluded": False,
        "rawRasterIncluded": False,
        "productionWrites": 0,
    }
    gate4 = {
        "schemaVersion": 2,
        "gate": "GATE4",
        "headAtQualification": expected_head,
        "status": status,
        "marker": task_marker,
        "taskMarker": task_marker,
        "programMarker": program_marker,
        "realSentinelRasterContent": False,
        "intervalReconciliation": False,
        "mocksUsed": False,
        "wialonExternalCalls": 0,
        "productionWrites": 0,
    }
    return qualification, ledger, security, gate4


def write_gate4_artifacts(
    evidence_root: Path,
    qualification: dict[str, Any],
    request_ledger: dict[str, Any],
    security_review: dict[str, Any],
    gate4: dict[str, Any],
) -> None:
    destination = evidence_root / "04_LIVE_SENTINEL"
    atomic_json(destination / "LIVE_SENTINEL_QUALIFICATION.json", qualification)
    atomic_json(destination / "LIVE_SENTINEL_REQUEST_LEDGER.json", request_ledger)
    atomic_json(destination / "LIVE_SENTINEL_SECURITY_REVIEW.json", security_review)
    atomic_json(destination / "GATE4_RESULT.json", gate4)


def main() -> int:
    args = parse_args()
    worktree = Path(__file__).resolve().parents[2]
    try:
        evidence_root = validate_evidence_root(Path(args.evidence_root))
    except BaseException:
        print("BLOCKED_TASK_211_BASELINE")
        return EXIT_BASELINE

    try:
        artifacts = run_live(
            worktree=worktree,
            evidence_root=evidence_root,
            expected_head=args.expected_head,
            starting_head=args.starting_head,
            runtime_env=Path(args.runtime_env),
        )
        qualification, ledger, security, gate4, exit_code = artifacts
    except (BaselineError, CredentialBoundaryError) as error:
        qualification, ledger, security, gate4 = safe_failure_artifacts(
            expected_head=args.expected_head,
            task_marker="BLOCKED_TASK_211_BASELINE",
            program_marker="BLOCKED_PROGRAM_R1_BASELINE",
            status="BLOCKED",
            error_class=type(error).__name__,
        )
        exit_code = EXIT_BASELINE
    except BaseException as error:
        qualification, ledger, security, gate4 = safe_failure_artifacts(
            expected_head=args.expected_head,
            task_marker="FAIL_TASK_211_INTERNAL_SENTINEL_BLOCKER",
            program_marker="FAIL_PROGRAM_R1_INTERNAL_SENTINEL_BLOCKER",
            status="FAIL",
            error_class=type(error).__name__,
        )
        exit_code = EXIT_INTERNAL_BLOCKER

    write_gate4_artifacts(evidence_root, qualification, ledger, security, gate4)
    counts = ledger.get("counts", {})
    print(qualification["taskMarker"])
    print(f"SENTINEL_STATISTICAL_REQUESTS={counts.get('statistical', 0)}")
    print(f"SENTINEL_PROCESS_REQUESTS={counts.get('process', 0)}")
    print(f"OAUTH_REQUESTS={counts.get('oauth', 0)}")
    print(f"OBSERVATIONS_PARSED={qualification.get('statistical', {}).get('parsedObservationCount', 0)}")
    print("PRODUCTION_WRITES=0")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
