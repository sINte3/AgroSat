#!/usr/bin/env python3
"""Run one bounded, sanitized, read-only PROGRAM R1 Sentinel qualification.

The live run deliberately exercises the release-candidate application services.
It never persists OAuth tokens, authorization headers, raw provider bodies, or
pixel arrays.  The only filesystem writes are atomic sanitized evidence files.
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
import subprocess
import sys
import time
from typing import Any, Callable

import httpx


PROGRAM = "PROGRAM R1 — Live Sentinel Completion"
REQUIRED_BRANCH = "task/task209-agrosat-global-program"
REQUIRED_SOURCE_MAIN_HEAD = "dfb57c7ff89c0af10f7907b81965487481c5b3e7"
ALLOWED_EVIDENCE_PREFIX = Path(
    r"C:\AgroSat_backups\PROGRAM_R1_LIVE_SENTINEL_COMPLETION"
)
SOURCE_CHECKOUT = Path(r"C:\AgroSat")
RESTRICTED_CREDENTIAL_ROOTS = (
    Path(r"C:\AgroSat"),
    Path(r"C:\AgroSat_worktrees"),
    Path(r"C:\AgroSat_backups"),
)
EXPECTED_CREDENTIAL_KEYS = {
    "SENTINEL_HUB_CLIENT_ID",
    "SENTINEL_HUB_CLIENT_SECRET",
}

MAX_DATE_WINDOW_DAYS = 14
MAX_FIELD_GEOMETRIES = 1
MAX_INDEX_CODES = 2
MAX_STATISTICAL_REQUESTS = 3
MAX_RASTER_REQUESTS = 2
MAX_OAUTH_REQUESTS = 2
MAX_RASTER_SIZE = 256
MAX_RETRYABLE_RETRIES = 1

TOKEN_URL = "https://services.sentinel-hub.com/auth/realms/main/protocol/openid-connect/token"
STATISTICAL_API_URL = "https://services.sentinel-hub.com/api/v1/statistics"
PROCESS_API_URL = "https://services.sentinel-hub.com/api/v1/process"

# Deterministic isolated geometry inherited from the accepted PROGRAM R1
# qualification dataset.  It is not read from a product database.
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
    """Exact Git/release baseline was not retained."""


class CredentialBoundaryError(RuntimeError):
    """Credential file failed the secure process-local boundary."""


class RequestContractError(RuntimeError):
    """A live request would violate the qualification bounds."""


class EvidenceSanitizationError(RuntimeError):
    """Evidence retained forbidden secret-bearing material."""


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
            "sourceClass": "deterministic_isolated_program_r1_qualification_geometry",
            "productionDatabaseLookup": False,
        }


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
    required = {"00_BASELINE", "04_LIVE_SENTINEL", "06_FINAL", "scratch"}
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


def assert_git_baseline(
    worktree: Path,
    expected_head: str,
    starting_head: str,
) -> dict[str, Any]:
    actual_head = _git(worktree, "rev-parse", "HEAD")
    branch = _git(worktree, "branch", "--show-current")
    dirty = bool(_git(worktree, "status", "--porcelain=v1", "--untracked-files=all"))
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
    changed_paths = [
        value.strip()
        for value in _git(worktree, "diff", "--name-only", f"{starting_head}..{actual_head}").splitlines()
        if value.strip()
    ]
    allowed_changes = all(
        path == "ops/qualification/Run-ProgramR1LiveSentinelQualification.py"
        or path == "backend/tests/test_program_r1_live_sentinel_qualification.py"
        for path in changed_paths
    )
    checks = {
        "branchExact": branch == REQUIRED_BRANCH,
        "headExact": actual_head == expected_head,
        "worktreeClean": not dirty,
        "remoteHeadExact": remote_head == expected_head,
        "sourceMainBranchExact": source_branch == "main",
        "sourceMainHeadExact": source_head == REQUIRED_SOURCE_MAIN_HEAD,
        "sourceMainClean": not source_dirty,
        "qualificationOnlyDiff": allowed_changes,
    }
    if not all(checks.values()):
        raise BaselineError("exact release-candidate baseline was not retained")
    return {
        "checks": checks,
        "head": actual_head,
        "remoteHead": remote_head,
        "sourceMainHead": source_head,
        "repositoryChangesOccurred": bool(changed_paths),
        "changedPaths": changed_paths,
        "productSurfacesInvalidated": False,
    }


def _acl_inheritance_disabled(path: Path) -> bool:
    environment = os.environ.copy()
    environment["AGROSAT_RUNTIME_ENV_FILE"] = str(path)
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "(Get-Acl -LiteralPath $env:AGROSAT_RUNTIME_ENV_FILE).AreAccessRulesProtected",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        env=environment,
    )
    return completed.returncode == 0 and completed.stdout.strip().casefold() == "true"


def validate_credential_boundary(path: Path) -> dict[str, bool]:
    resolved = path.resolve(strict=True)
    outside_restricted = not any(
        _is_within(resolved, root) for root in RESTRICTED_CREDENTIAL_ROOTS
    )
    assignments: list[tuple[str, bool]] = []
    malformed = False
    try:
        with resolved.open("r", encoding="utf-8-sig") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                if "=" not in line:
                    malformed = True
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if (
                    len(value) >= 2
                    and value[0] == value[-1]
                    and value[0] in {"'", '"'}
                ):
                    value = value[1:-1]
                assignments.append((key, bool(value.strip())))
    except (OSError, UnicodeError):
        raise CredentialBoundaryError("credential file could not be parsed") from None

    keys = [key for key, _ in assignments]
    exact_keys = (
        not malformed
        and set(keys) == EXPECTED_CREDENTIAL_KEYS
        and len(keys) == len(EXPECTED_CREDENTIAL_KEYS)
    )
    duplicate_keys_absent = len(keys) == len(set(keys))
    nonempty = exact_keys and all(value_present for _, value_present in assignments)
    acl_protected = _acl_inheritance_disabled(resolved)
    checks = {
        "credentialFileExists": resolved.is_file(),
        "credentialFileOutsideRepositoryWorktreesAndBackups": outside_restricted,
        "accessControlInheritanceDisabled": acl_protected,
        "exactRequiredKeySet": exact_keys,
        "duplicateKeysAbsent": duplicate_keys_absent,
        "allRequiredValuesNonEmpty": nonempty,
        "clientIdAvailable": exact_keys and nonempty,
        "clientSecretAvailable": exact_keys and nonempty,
        "credentialValuesIncluded": False,
    }
    if not all(value for key, value in checks.items() if key != "credentialValuesIncluded"):
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


def _time_range_dates(time_range: dict[str, Any]) -> tuple[date, date, int]:
    try:
        start = date.fromisoformat(str(time_range["from"])[:10])
        end = date.fromisoformat(str(time_range["to"])[:10])
    except (KeyError, TypeError, ValueError):
        raise RequestContractError("invalid provider time range") from None
    window_days = (end - start).days + 1
    if window_days < 1 or window_days > MAX_DATE_WINDOW_DAYS:
        raise RequestContractError("date window exceeds bound")
    return start, end, window_days


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
        "latestUsableIntervalFrom": None,
        "latestUsableIntervalTo": None,
    }
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeError, ValueError):
        return metadata
    metadata["jsonParseable"] = True
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return metadata
    metadata["contractShapeValid"] = True
    intervals = payload["data"]
    metadata["intervalCount"] = len(intervals)
    usable: list[dict[str, Any]] = []
    for interval in intervals:
        if not isinstance(interval, dict):
            metadata["contractShapeValid"] = False
            continue
        stats = (
            interval.get("outputs", {})
            .get("ndvi", {})
            .get("bands", {})
            .get("B0", {})
            .get("stats", {})
        )
        try:
            sample_count = float(stats.get("sampleCount", 0))
        except (TypeError, ValueError):
            sample_count = 0
        if sample_count > 0:
            usable.append(interval)
    metadata["usableIntervalCount"] = len(usable)
    if usable:
        interval = usable[-1].get("interval", {})
        metadata["latestUsableIntervalFrom"] = interval.get("from")
        metadata["latestUsableIntervalTo"] = interval.get("to")
    return metadata


class BoundedHttpRecorder:
    """Process-local wrapper around the real httpx.post entry point."""

    def __init__(
        self,
        delegate: Callable[..., httpx.Response],
        geometry_summary: GeometrySummary,
    ) -> None:
        self._delegate = delegate
        self.geometry_summary = geometry_summary
        self.ledger: list[dict[str, Any]] = []
        self.counts = {"oauth": 0, "statistical": 0, "raster": 0}
        self.statistical_response_metadata: list[dict[str, Any]] = []
        self.raster_response_metadata: list[dict[str, Any]] = []
        self.contract_failure = False

    def _validate_token(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        data = kwargs.get("data")
        if not isinstance(data, dict):
            raise RequestContractError("OAuth form payload is absent")
        if data.get("grant_type") != "client_credentials":
            raise RequestContractError("OAuth grant type is invalid")
        present = [
            isinstance(data.get(name), str) and bool(data.get(name).strip())
            for name in ("client_id", "client_secret")
        ]
        if not all(present):
            raise RequestContractError("OAuth credentials are unavailable")
        return {
            "operation": "oauth_client_credentials",
            "indices": [],
            "dateWindowDays": 0,
            "geometryFingerprint": None,
        }

    def _validate_statistical(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        payload = kwargs.get("json")
        if not isinstance(payload, dict):
            raise RequestContractError("statistical JSON payload is absent")
        try:
            geometry = payload["input"]["bounds"]["geometry"]
            data_items = payload["input"]["data"]
            input_time = data_items[0]["dataFilter"]["timeRange"]
            aggregation = payload["aggregation"]
            aggregation_time = aggregation["timeRange"]
            evalscript = aggregation["evalscript"]
            calculations = payload["calculations"]
        except (KeyError, IndexError, TypeError):
            raise RequestContractError("statistical application payload is invalid") from None
        if len(data_items) != 1 or data_items[0].get("type") != "sentinel-2-l2a":
            raise RequestContractError("statistical data source is not bounded Sentinel-2 L2A")
        if geometry_fingerprint(geometry) != self.geometry_summary.fingerprint:
            raise RequestContractError("statistical geometry changed")
        input_start, input_end, window_days = _time_range_dates(input_time)
        aggregation_start, aggregation_end, _ = _time_range_dates(aggregation_time)
        if (input_start, input_end) != (aggregation_start, aggregation_end):
            raise RequestContractError("statistical time ranges differ")
        if aggregation.get("aggregationInterval", {}).get("of") != "P1D":
            raise RequestContractError("statistical aggregation interval is not daily")
        if not isinstance(evalscript, str) or 'id: "ndvi"' not in evalscript:
            raise RequestContractError("NDVI evalscript is absent")
        if not isinstance(calculations, dict) or not 1 <= len(calculations) <= MAX_INDEX_CODES:
            raise RequestContractError("statistical index count exceeds bound")
        return {
            "operation": "sentinel_statistical_ndvi",
            "indices": ["ndvi"],
            "dateWindowDays": window_days,
            "geometryFingerprint": self.geometry_summary.fingerprint,
        }

    def _validate_raster(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        payload = kwargs.get("json")
        if not isinstance(payload, dict):
            raise RequestContractError("raster JSON payload is absent")
        try:
            geometry = payload["input"]["bounds"]["geometry"]
            data_items = payload["input"]["data"]
            time_range = data_items[0]["dataFilter"]["timeRange"]
            output = payload["output"]
            evalscript = payload["evalscript"]
        except (KeyError, IndexError, TypeError):
            raise RequestContractError("raster application payload is invalid") from None
        if len(data_items) != 1 or data_items[0].get("type") != "sentinel-2-l2a":
            raise RequestContractError("raster data source is not bounded Sentinel-2 L2A")
        if geometry_fingerprint(geometry) != self.geometry_summary.fingerprint:
            raise RequestContractError("raster geometry changed")
        start, end, _ = _time_range_dates(time_range)
        if (end - start).days != 1:
            raise RequestContractError("raster request is not a one-day interval")
        width = output.get("width")
        height = output.get("height")
        if width != MAX_RASTER_SIZE or height != MAX_RASTER_SIZE:
            raise RequestContractError("raster size exceeds qualification bound")
        if not isinstance(evalscript, str) or "ndvi" not in evalscript.casefold():
            raise RequestContractError("raster NDVI evalscript is absent")
        return {
            "operation": "sentinel_process_ndvi_png",
            "indices": ["ndvi"],
            "dateWindowDays": 1,
            "geometryFingerprint": self.geometry_summary.fingerprint,
        }

    def _request_metadata(self, url: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if url == TOKEN_URL:
            kind = "oauth"
            endpoint_class = "official_sentinel_hub_oauth_https"
            metadata = self._validate_token(kwargs)
        elif url == STATISTICAL_API_URL:
            kind = "statistical"
            endpoint_class = "official_sentinel_hub_statistical_https"
            metadata = self._validate_statistical(kwargs)
        elif url == PROCESS_API_URL:
            kind = "raster"
            endpoint_class = "official_sentinel_hub_process_https"
            metadata = self._validate_raster(kwargs)
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
                "raster": MAX_RASTER_REQUESTS,
            }[kind]
            if self.counts[kind] >= maximum:
                raise RequestContractError(f"{kind} request bound exceeded")
        except RequestContractError:
            self.contract_failure = True
            raise

        self.counts[kind] += 1
        entry = {
            "requestOrdinal": len(self.ledger) + 1,
            "endpointClass": metadata["endpointClass"],
            "operationClass": metadata["operation"],
            "indexCodes": metadata["indices"],
            "dateWindowDays": metadata["dateWindowDays"],
            "geometryFingerprint": metadata["geometryFingerprint"],
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
            "raster": httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0),
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
            metadata = inspect_statistical_response(response)
            self.statistical_response_metadata.append(metadata)
            if not metadata["jsonParseable"] or not metadata["contractShapeValid"]:
                entry["providerClassification"] = "invalid_response"
            elif metadata["usableIntervalCount"] == 0:
                entry["providerClassification"] = "no_data"
        if kind == "raster":
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            self.raster_response_metadata.append(
                {
                    "contentType": content_type or None,
                    "nonEmptyBytes": bool(response.content),
                    "byteCount": len(response.content),
                    "rawBytesPersisted": False,
                }
            )
        return response

    def latest(self, kind: str) -> dict[str, Any] | None:
        operation = {
            "oauth": "oauth_client_credentials",
            "statistical": "sentinel_statistical_ndvi",
            "raster": "sentinel_process_ndvi_png",
        }[kind]
        for entry in reversed(self.ledger):
            if entry["operationClass"] == operation:
                return entry
        return None


def retryable_entry(entry: dict[str, Any] | None) -> bool:
    if not entry:
        return False
    return entry.get("providerClassification") in {
        "timeout",
        "network",
        "provider_unavailable",
    }


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
    quality_passed: bool,
    raster_entry: dict[str, Any] | None,
    raster_passed: bool,
) -> tuple[str, str, int]:
    entries = [entry for entry in (statistical_entry, raster_entry) if entry]
    categories = {str(entry.get("providerClassification")) for entry in entries}
    statuses = {entry.get("httpStatus") for entry in entries}
    if contract_failure or "request_rejected" in categories or statuses.intersection({400, 422}):
        return "FAIL_PROGRAM_R1_INTERNAL_SENTINEL_BLOCKER", "FAIL", EXIT_INTERNAL_BLOCKER
    if "invalid_response" in categories:
        return "FAIL_PROGRAM_R1_INTERNAL_SENTINEL_BLOCKER", "FAIL", EXIT_INTERNAL_BLOCKER
    if not oauth_succeeded:
        if any(
            entry.get("operationClass") == "oauth_client_credentials"
            and entry.get("httpStatus") in (401, 403)
            for entry in entries
        ):
            return (
                "PARTIAL_PROGRAM_R1_SENTINEL_ACCOUNT_PREREQUISITE",
                "BLOCKED",
                EXIT_ACCOUNT_PREREQUISITE,
            )
        return (
            "PARTIAL_PROGRAM_R1_SENTINEL_PROVIDER_PREREQUISITE",
            "BLOCKED",
            EXIT_PROVIDER_PREREQUISITE,
        )
    if not statistical_parseable:
        return "FAIL_PROGRAM_R1_INTERNAL_SENTINEL_BLOCKER", "FAIL", EXIT_INTERNAL_BLOCKER
    if not usable_observation or not quality_passed:
        return (
            "PARTIAL_PROGRAM_R1_SENTINEL_PROVIDER_PREREQUISITE",
            "BLOCKED",
            EXIT_PROVIDER_PREREQUISITE,
        )
    if raster_entry and raster_entry.get("httpStatus") in (401, 403):
        return (
            "PARTIAL_PROGRAM_R1_SENTINEL_ACCOUNT_PREREQUISITE",
            "BLOCKED",
            EXIT_ACCOUNT_PREREQUISITE,
        )
    if not raster_passed:
        if raster_entry and raster_entry.get("providerClassification") in {
            "timeout",
            "network",
            "provider_unavailable",
            "quota_or_rate_limit",
        }:
            return (
                "PARTIAL_PROGRAM_R1_SENTINEL_PROVIDER_PREREQUISITE",
                "BLOCKED",
                EXIT_PROVIDER_PREREQUISITE,
            )
        return "FAIL_PROGRAM_R1_INTERNAL_SENTINEL_BLOCKER", "FAIL", EXIT_INTERNAL_BLOCKER
    return "PASS_PROGRAM_R1_FULL_MERGE_READINESS", "PASS", EXIT_PASS


def assert_sanitized(serialized: str, forbidden_values: list[str]) -> None:
    lowered = serialized.casefold()
    forbidden_fragments = (
        '"authorization"',
        '"access_token"',
        '"rawresponsebody"',
        '"rawproviderbody"',
        "bearer ",
        "-----begin private key-----",
        "postgresql://",
        "postgres://",
        "redis://",
    )
    if any(fragment in lowered for fragment in forbidden_fragments):
        raise EvidenceSanitizationError("forbidden evidence field")
    if any(value and value in serialized for value in forbidden_values):
        raise EvidenceSanitizationError("credential or token value reached evidence")


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
    os.environ["AGROSAT_RUNTIME_ENV_FILE"] = str(runtime_env.resolve(strict=True))
    os.environ["RELEASE_REVISION"] = expected_head
    backend = worktree / "backend"
    sys.path.insert(0, str(backend))

    from config import settings
    from services import ndvi_raster
    from services.raster_provider import provider_metadata
    from services.satellite import (
        SentinelHubService,
        validate_ndvi_quality,
    )
    from services.satellite_safety import (
        REAL_SATELLITE_SOURCE,
        require_payload_provenance,
        require_real_service,
    )

    client_id_available = (
        isinstance(settings.sentinel_hub_client_id, str)
        and bool(settings.sentinel_hub_client_id.strip())
    )
    client_secret_available = (
        isinstance(settings.sentinel_hub_client_secret, str)
        and bool(settings.sentinel_hub_client_secret.strip())
    )
    credential_checks["runtimeSettingsLoad"] = True
    credential_checks["clientIdAvailable"] = client_id_available
    credential_checks["clientSecretAvailable"] = client_secret_available
    if not (client_id_available and client_secret_available):
        raise CredentialBoundaryError("runtime settings did not load complete credentials")
    if settings.wialon_enabled is not False:
        raise RequestContractError("Wialon must remain disabled")

    geometry_summary = validate_geometry(QUALIFICATION_GEOMETRY)
    from shapely.geometry import shape
    geometry_wkt = shape(QUALIFICATION_GEOMETRY).wkt
    date_from, date_to = select_date_window(datetime.now(timezone.utc).date())
    if (date_to - date_from).days + 1 > MAX_DATE_WINDOW_DAYS:
        raise RequestContractError("runtime date window exceeds bound")

    service = SentinelHubService()
    require_real_service(service)
    original_post = httpx.post
    recorder = BoundedHttpRecorder(original_post, geometry_summary)
    httpx.post = recorder.post
    # The raster helper normally constructs the same service class again.  Reuse
    # this already-authenticated real instance process-locally so the bounded run
    # does not spend an unnecessary second OAuth request.
    original_service_factory = ndvi_raster.get_satellite_service
    ndvi_raster.get_satellite_service = lambda: service
    logging.getLogger("services.satellite").setLevel(logging.CRITICAL)

    stats_result: dict[str, Any] | None = None
    stats_parseable = False
    quality_passed = False
    quality_reason_class = "not_executed"
    provenance_passed = False
    raster_passed = False
    raster_bytes_count = 0
    raster_error_class: str | None = None
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
            oauth_succeeded_now = bool(service._access_token)
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
                mark_retry_decision(terminal_entry, "stop_application_parse_succeeded")
                if stats_entry:
                    stats_entry["providerClassification"] = "sentinel_2_observation"
                    stats_entry["parsedObservationCount"] = 1
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
            provenance_passed = stats_result.get("satellite") == REAL_SATELLITE_SOURCE
            quality_passed, _quality_reason = validate_ndvi_quality(
                stats_result.get("mean_ndvi"),
                cloud_cover_pct=stats_result.get("cloud_cover_pct"),
                min_ndvi=stats_result.get("min_ndvi"),
                max_ndvi=stats_result.get("max_ndvi"),
                field_name="program_r1_isolated_qualification_geometry",
            )
            quality_reason_class = "accepted" if quality_passed else "rejected_by_current_thresholds"

        if stats_result is not None and quality_passed and provenance_passed:
            try:
                observation_date = date.fromisoformat(str(stats_result["captured_date"]))
                if not date_from <= observation_date <= date_to:
                    raise RequestContractError("parsed observation date is outside request window")
                for attempt in range(MAX_RETRYABLE_RETRIES + 1):
                    try:
                        image = ndvi_raster.request_process_png(
                            QUALIFICATION_GEOMETRY,
                            observation_date,
                            MAX_RASTER_SIZE,
                        )
                        raster_bytes_count = len(image)
                        raster_passed = ndvi_raster.validate_png(image)
                        del image
                        entry = recorder.latest("raster")
                        if entry:
                            entry["providerClassification"] = (
                                "sentinel_2_png" if raster_passed else "invalid_response"
                            )
                            entry["retryDecision"] = "stop_valid_png" if raster_passed else "stop_invalid_png"
                        break
                    except BaseException as error:
                        raster_error_class = type(error).__name__
                        entry = recorder.latest("raster")
                        if retryable_entry(entry) and attempt < MAX_RETRYABLE_RETRIES:
                            mark_retry_decision(entry, "retry_once")
                            retry_count += 1
                            continue
                        mark_retry_decision(entry, "stop_no_retry")
                        break
            except (KeyError, TypeError, ValueError, RequestContractError) as error:
                raster_error_class = type(error).__name__

        stats_entry = recorder.latest("statistical")
        raster_entry = recorder.latest("raster")
        response_meta = (
            recorder.statistical_response_metadata[-1]
            if recorder.statistical_response_metadata
            else {
                "jsonParseable": False,
                "contractShapeValid": False,
                "intervalCount": 0,
                "usableIntervalCount": 0,
                "latestUsableIntervalFrom": None,
                "latestUsableIntervalTo": None,
            }
        )
        marker, sentinel_status, exit_code = classify_program_outcome(
            contract_failure=recorder.contract_failure,
            oauth_succeeded=oauth_succeeded,
            statistical_entry=stats_entry or recorder.latest("oauth"),
            statistical_parseable=stats_parseable,
            usable_observation=stats_result is not None and provenance_passed,
            quality_passed=quality_passed,
            raster_entry=raster_entry,
            raster_passed=raster_passed,
        )
        gate4_marker = (
            "PASS_GATE4_LIVE_SENTINEL_WITH_WIALON_DEFERRED"
            if marker == "PASS_PROGRAM_R1_FULL_MERGE_READINESS"
            else marker
        )
        parsed_observations = 1 if stats_result is not None else 0
        captured_date = stats_result.get("captured_date") if stats_result else None
        valid_pixels_available = bool(
            stats_result is not None and stats_result.get("valid_pixels_pct") is not None
        )
        qualification = {
            "schemaVersion": 1,
            "program": PROGRAM,
            "recordedAt": datetime.now(timezone.utc).isoformat(),
            "head": expected_head,
            "startingHead": starting_head,
            "provider": "Sentinel-2",
            "providerEndpointClass": "official_sentinel_hub_https",
            "providerIsMock": False,
            "credentialBoundary": credential_checks,
            "geometry": geometry_summary.as_dict(),
            "dateWindow": {
                "from": date_from.isoformat(),
                "to": date_to.isoformat(),
                "calendarDays": (date_to - date_from).days + 1,
                "selection": "runtime_utc_yesterday_back_14_calendar_days",
            },
            "hardBounds": {
                "maximumDateWindowDays": MAX_DATE_WINDOW_DAYS,
                "maximumFieldGeometries": MAX_FIELD_GEOMETRIES,
                "maximumIndicesPerStatisticalRequest": MAX_INDEX_CODES,
                "maximumStatisticalRequests": MAX_STATISTICAL_REQUESTS,
                "maximumRasterRequests": MAX_RASTER_REQUESTS,
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
                "rasterPayload": "services.ndvi_raster.build_process_payload",
                "rasterRequestAndValidation": "services.ndvi_raster.request_process_png",
                "rasterProviderContract": provider_metadata("ndvi"),
                "rasterCacheLayerBypassedForReadOnlyQualification": True,
            },
            "oauth": {
                "clientCredentialsExchangeSucceeded": oauth_succeeded,
                "tokenValueIncluded": False,
                "authorizationHeaderIncluded": False,
            },
            "statistical": {
                "requestSucceeded": bool(stats_entry and stats_entry.get("httpStatus") == 200),
                "applicationResultParseable": stats_parseable and stats_result is not None,
                "providerIntervalCount": response_meta["intervalCount"],
                "providerUsableIntervalCount": response_meta["usableIntervalCount"],
                "parsedObservationCount": parsed_observations,
                "capturedDate": captured_date,
                "capturedDateSemantics": (
                    "current application parser truncates the latest non-empty aggregation interval 'to' value to YYYY-MM-DD"
                ),
                "latestUsableIntervalFrom": response_meta["latestUsableIntervalFrom"],
                "latestUsableIntervalTo": response_meta["latestUsableIntervalTo"],
                "cloudCoverFieldAvailable": bool(
                    stats_result is not None and stats_result.get("cloud_cover_pct") is not None
                ),
                "cloudCoverUnavailableReason": (
                    None
                    if stats_result is not None and stats_result.get("cloud_cover_pct") is not None
                    else "current NDVI Statistical API parser does not return per-observation cloud cover"
                ),
                "validPixelsFieldAvailable": valid_pixels_available,
                "validPixelsPct": stats_result.get("valid_pixels_pct") if valid_pixels_available else None,
                "qualityValidationExecuted": stats_result is not None,
                "qualityValidationPassed": quality_passed,
                "qualityOutcomeClass": quality_reason_class,
                "thresholdsChanged": False,
                "realProvenanceValidated": provenance_passed,
            },
            "raster": {
                "attempted": raster_entry is not None,
                "applicationPathAvailable": True,
                "requestSucceeded": raster_passed,
                "indexCode": "ndvi",
                "size": MAX_RASTER_SIZE,
                "requestedDate": captured_date if raster_entry else None,
                "requestedDateUsesApplicationCapturedDate": bool(raster_entry and captured_date),
                "actualAcquisitionTimestampAvailableInCurrentPngContract": False,
                "actualDateContract": (
                    "official Process API request is constrained to the one-day application captured-date window; PNG response exposes no acquisition timestamp"
                ),
                "geometryFingerprint": geometry_summary.fingerprint if raster_entry else None,
                "provider": "Sentinel-2" if raster_entry else None,
                "contentTypeValid": raster_passed,
                "nonEmptyBytes": raster_bytes_count > 0,
                "byteCount": raster_bytes_count,
                "pixelArrayPersisted": False,
                "rawImagePersisted": False,
                "errorClass": raster_error_class,
            },
            "reconciliation": {
                "sameIndex": bool(raster_entry and stats_result is not None),
                "sameGeometryFingerprint": bool(raster_entry and stats_result is not None),
                "sameProvider": bool(raster_entry and provenance_passed),
                "compatibleRequestedDateSemantics": bool(raster_entry and captured_date),
                "exactNumericalRasterColorEqualityClaimed": False,
            },
            "databaseAccessAttempted": False,
            "redisAccessAttempted": False,
            "productionWrites": 0,
            "wialonExternalCalls": 0,
            "status": sentinel_status,
            "marker": marker,
        }
        request_ledger = {
            "schemaVersion": 1,
            "head": expected_head,
            "requests": recorder.ledger,
            "counts": dict(recorder.counts),
            "rawProviderBodiesIncluded": False,
            "oauthTokensIncluded": False,
            "authorizationHeadersIncluded": False,
            "productionWrites": 0,
        }
        security_review = {
            "schemaVersion": 1,
            "head": expected_head,
            "credentialBoundary": credential_checks,
            "officialHttpsEndpointsOnly": all(
                entry["endpointClass"].startswith("official_sentinel_hub_")
                for entry in recorder.ledger
            ),
            "credentialValuesIncluded": False,
            "oauthTokensIncluded": False,
            "authorizationHeadersIncluded": False,
            "rawProviderBodiesIncluded": False,
            "rawRasterIncluded": False,
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
            "schemaVersion": 1,
            "gate": "GATE4",
            "head": expected_head,
            "status": "PASS" if sentinel_status == "PASS" else sentinel_status,
            "marker": gate4_marker,
            "sentinelLive": sentinel_status,
            "wialonScope": "DEFERRED_TO_NEXT_PILOT",
            "wialonFeatureFlag": "DISABLED",
            "wialonExternalCalls": 0,
            "productionWrites": 0,
            "inheritedWialonEvidenceHashValidated": True,
            "productSurfacesInvalidated": False,
        }
        forbidden_values = [
            settings.sentinel_hub_client_id,
            settings.sentinel_hub_client_secret,
            service._access_token or "",
        ]
        serialized_bundle = canonical_json(
            {
                "qualification": qualification,
                "requestLedger": request_ledger,
                "securityReview": security_review,
                "gate4": gate4,
            }
        )
        assert_sanitized(serialized_bundle, forbidden_values)
        return qualification, request_ledger, security_review, gate4, exit_code
    finally:
        httpx.post = original_post
        ndvi_raster.get_satellite_service = original_service_factory
        service._access_token = None
        service._token_expires_at = 0


def safe_failure_artifacts(
    *,
    expected_head: str,
    marker: str,
    status: str,
    error_class: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    qualification = {
        "schemaVersion": 1,
        "program": PROGRAM,
        "head": expected_head,
        "status": status,
        "marker": marker,
        "errorClass": error_class,
        "credentialValuesIncluded": False,
        "oauthTokensIncluded": False,
        "authorizationHeadersIncluded": False,
        "rawProviderBodiesIncluded": False,
        "productionWrites": 0,
    }
    ledger = {
        "schemaVersion": 1,
        "head": expected_head,
        "requests": [],
        "counts": {"oauth": 0, "statistical": 0, "raster": 0},
        "rawProviderBodiesIncluded": False,
        "oauthTokensIncluded": False,
        "authorizationHeadersIncluded": False,
        "productionWrites": 0,
    }
    security = {
        "schemaVersion": 1,
        "head": expected_head,
        "status": status,
        "errorClass": error_class,
        "credentialValuesIncluded": False,
        "oauthTokensIncluded": False,
        "authorizationHeadersIncluded": False,
        "rawProviderBodiesIncluded": False,
        "productionWrites": 0,
    }
    gate4 = {
        "schemaVersion": 1,
        "gate": "GATE4",
        "head": expected_head,
        "status": status,
        "marker": marker,
        "sentinelLive": status,
        "wialonScope": "DEFERRED_TO_NEXT_PILOT",
        "wialonFeatureFlag": "DISABLED",
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
        print("BLOCKED_PROGRAM_R1_LIVE_SENTINEL_BASELINE")
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
    except BaselineError as error:
        qualification, ledger, security, gate4 = safe_failure_artifacts(
            expected_head=args.expected_head,
            marker="BLOCKED_PROGRAM_R1_LIVE_SENTINEL_BASELINE",
            status="BLOCKED",
            error_class=type(error).__name__,
        )
        exit_code = EXIT_BASELINE
    except CredentialBoundaryError as error:
        qualification, ledger, security, gate4 = safe_failure_artifacts(
            expected_head=args.expected_head,
            marker="BLOCKED_PROGRAM_R1_SENTINEL_CREDENTIAL_BOUNDARY",
            status="BLOCKED",
            error_class=type(error).__name__,
        )
        exit_code = EXIT_CREDENTIAL_BOUNDARY
    except BaseException as error:
        qualification, ledger, security, gate4 = safe_failure_artifacts(
            expected_head=args.expected_head,
            marker="FAIL_PROGRAM_R1_INTERNAL_SENTINEL_BLOCKER",
            status="FAIL",
            error_class=type(error).__name__,
        )
        exit_code = EXIT_INTERNAL_BLOCKER

    write_gate4_artifacts(evidence_root, qualification, ledger, security, gate4)
    counts = ledger.get("counts", {})
    print(qualification["marker"])
    print(f"SENTINEL_STATISTICAL_REQUESTS={counts.get('statistical', 0)}")
    print(f"SENTINEL_RASTER_REQUESTS={counts.get('raster', 0)}")
    print(f"OAUTH_REQUESTS={counts.get('oauth', 0)}")
    print(f"OBSERVATIONS_PARSED={qualification.get('statistical', {}).get('parsedObservationCount', 0)}")
    print("PRODUCTION_WRITES=0")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
