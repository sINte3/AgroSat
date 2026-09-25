"""The release health contract (TASK_230 Part H, TASK_228 semantics).

A backend passes only when, on loopback:

* ``GET /health/live`` answers 200, ``status == "alive"`` and
  ``release_revision`` is the candidate SHA;
* ``GET /health/ready`` answers 200, ``status == "ready"``, the same
  ``release_revision``, and its database component is ``ready`` with
  ``revision_match`` true and ``migration_revision`` and
  ``expected_migration_revision`` both equal to the expected revision.

Collector and cache state are recorded but never decide the result: the API
contract itself marks them ``required_for_api_readiness: false``.

A frontend passes when ``GET /`` serves the release's exact ``index.html`` and
``GET /health/live`` through its proxy reaches a backend reporting the
candidate SHA.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_BODY_BYTES = 256 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class HttpResult:
    status: int | None
    body: bytes
    error: str | None = None

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None


def http_get(url: str, timeout: float = 10.0) -> HttpResult:
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in LOOPBACK_HOSTS:
        return HttpResult(None, b"", "target_not_loopback")
    request = Request(url, headers={"Accept": "application/json, text/html", "User-Agent": "agrosat-controlplane"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return HttpResult(response.status, response.read(MAX_BODY_BYTES))
    except HTTPError as error:
        return HttpResult(error.code, error.read(MAX_BODY_BYTES))
    except (URLError, OSError, TimeoutError) as error:
        return HttpResult(None, b"", type(error).__name__)


def evaluate_backend(live: HttpResult, ready: HttpResult, candidate_sha: str,
                     expected_migration_revision: str, *, pre_task228_compatible: bool = False) -> dict[str, Any]:
    """The backend contract.

    A candidate is always held to the full TASK_228 contract. A release that
    predates TASK_228 (production 4cd8ea7 and earlier) does not publish
    ``revision_match`` or ``expected_migration_revision``; when such a release
    is the previous release or a rollback target, ``pre_task228_compatible``
    accepts their absence, but only their absence: its ``migration_revision``
    must still equal the head the caller derived from that release's own
    migration graph, and fields it does publish are checked strictly.
    """
    live_body = live.json() if isinstance(live.json(), dict) else {}
    ready_body = ready.json() if isinstance(ready.json(), dict) else {}
    components = ready_body.get("components") if isinstance(ready_body.get("components"), dict) else {}
    database = components.get("database") if isinstance(components.get("database"), dict) else {}
    legacy = pre_task228_compatible and "revision_match" not in database and "expected_migration_revision" not in database
    checks = {
        "live_http_200": live.status == 200,
        "live_status_alive": live_body.get("status") == "alive",
        "live_release_revision": live_body.get("release_revision") == candidate_sha,
        "ready_http_200": ready.status == 200,
        "ready_status_ready": ready_body.get("status") == "ready",
        "ready_release_revision": ready_body.get("release_revision") == candidate_sha,
        "database_status_ready": database.get("status") == "ready",
        "database_revision_match": legacy or database.get("revision_match") is True,
        "database_migration_revision": database.get("migration_revision") == expected_migration_revision,
        "database_expected_migration_revision": legacy or
        database.get("expected_migration_revision") == expected_migration_revision,
    }
    collector = components.get("collector") if isinstance(components.get("collector"), dict) else None
    cache = components.get("cache") if isinstance(components.get("cache"), dict) else None
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "readiness_contract": "pre_task228_compatible" if legacy else "task228",
        "observed": {
            "live_http_status": live.status, "ready_http_status": ready.status,
            "live_error": live.error, "ready_error": ready.error,
            "release_revision": ready_body.get("release_revision", live_body.get("release_revision")),
            "database": {key: database.get(key) for key in (
                "status", "migration_revision", "expected_migration_revision", "revision_match", "reason")},
        },
        # Operational, never blocking: the API contract says so itself.
        "non_blocking": {
            "collector_status": None if collector is None else collector.get("status"),
            "collector_required_for_api_readiness": None if collector is None else collector.get("required_for_api_readiness"),
            "cache_status": None if cache is None else cache.get("status"),
            "cache_required_for_api_readiness": None if cache is None else cache.get("required_for_api_readiness"),
        },
    }


def evaluate_frontend(index: HttpResult, proxied_live: HttpResult, dist_index_sha256: str,
                      candidate_sha: str) -> dict[str, Any]:
    proxied = proxied_live.json() if isinstance(proxied_live.json(), dict) else {}
    checks = {
        "index_http_200": index.status == 200,
        "index_is_release_bundle": index.status == 200 and hashlib.sha256(index.body).hexdigest() == dist_index_sha256,
        "proxy_live_http_200": proxied_live.status == 200,
        "proxy_reaches_candidate_backend": proxied.get("release_revision") == candidate_sha,
    }
    return {"pass": all(checks.values()), "checks": checks,
            "observed": {"index_http_status": index.status, "proxy_http_status": proxied_live.status,
                         "proxy_release_revision": proxied.get("release_revision"),
                         "index_error": index.error, "proxy_error": proxied_live.error}}


def probe_backend(port: int, candidate_sha: str, expected_migration_revision: str,
                  get: Callable[[str], HttpResult] = http_get, *, pre_task228_compatible: bool = False) -> dict[str, Any]:
    base = f"http://127.0.0.1:{port}"
    return evaluate_backend(get(f"{base}/health/live"), get(f"{base}/health/ready"),
                            candidate_sha, expected_migration_revision,
                            pre_task228_compatible=pre_task228_compatible)


def probe_frontend(port: int, dist_index_sha256: str, candidate_sha: str,
                   get: Callable[[str], HttpResult] = http_get) -> dict[str, Any]:
    base = f"http://127.0.0.1:{port}"
    return evaluate_frontend(get(f"{base}/"), get(f"{base}/health/live"), dist_index_sha256, candidate_sha)


def wait_for(probe: Callable[[], dict[str, Any]], *, deadline_seconds: float, interval_seconds: float = 1.0,
             clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Poll ``probe`` until it passes or the deadline expires; return the last result."""
    started = clock()
    attempts = 0
    while True:
        attempts += 1
        result = probe()
        elapsed = round(clock() - started, 3)
        if result["pass"] or elapsed >= deadline_seconds:
            return {**result, "attempts": attempts, "elapsed_seconds": elapsed}
        sleep(interval_seconds)
