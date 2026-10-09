"""Collector-only provider request governor: global pacing and bounded HTTP 429 handling.

Why this exists (TASK_250/TASK_251). The Sentinel Hub Statistical API throttles this
account with HTTP 429 at roughly 21-25 requests per minute, far below the documented
300 requests/min; the exact rule is UNKNOWN. Until TASK_247 the NDVI collector was
accidentally slow (each insert waited out a failing Redis connect), so it stayed under
the limit. Once inserts stopped, its children ran twice as fast and every cycle died at
the first 429. This module makes the pacing explicit instead of accidental.

Scope and safety
* It is used only by the standalone collector processes. A service gets a governor only
  when a collector process attaches one (``attach_provider_governor``); the FastAPI web
  process never does, so no web worker ever sleeps here.
* Pacing is global across processes: every collector child (NDVI and supporting
  indices alike) reserves its request slot in one shared state file under an exclusive
  OS file lock. Each provider *budget* has its own minimum spacing and cooldown:
  ``processing`` (Statistical API requests, the throttled budget) and ``oauth`` (token
  endpoint; its limit is UNKNOWN and it is paced lightly).
* Only HTTP 429 is retried here, with bounded attempts, a total wait budget, and the
  provider's ``Retry-After`` honoured within bounds. Sentinel Hub documents
  ``Retry-After`` in milliseconds; the OAuth endpoint (Keycloak) uses RFC 9110 seconds
  or an HTTP date. A 429 that cannot clear inside the budget raises
  ``ProviderThrottleError`` (``rate_limited``); a 429 whose wait exceeds the policy, or
  a 403/429 whose provider error mentions a quota, raises ``quota_suspected``. All other
  statuses are returned untouched for the caller's existing ``raise_for_status``.
* Diagnostics keep only counts, endpoint labels, retry delays and allow-listed numeric
  rate-limit header values. No response body text, credential or bearer token is
  stored; a provider error is reduced to a code/reason token and a quota-keyword flag.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

import httpx

POLICY_ENV = "AGROSAT_PROVIDER_POLICY"
PACER_DIR_ENV = "AGROSAT_PROVIDER_PACER_DIR"
RUN_ID_ENV = "AGROSAT_PROVIDER_RUN_ID"
STAGE_ENV = "AGROSAT_PROVIDER_STAGE"
STATE_FILENAME = "provider_pacer_state.json"
LOCK_FILENAME = "provider_pacer.lock"
STATE_SCHEMA_VERSION = 1
BUDGET_PROCESSING = "processing"
BUDGET_OAUTH = "oauth"
ENDPOINT_BUDGET = {
    "statistical": BUDGET_PROCESSING,
    "process": BUDGET_PROCESSING,
    "catalog": BUDGET_PROCESSING,
    "oauth": BUDGET_OAUTH,
}
KNOWN_STAGES = ("ndvi", "multi", "standalone")
MAX_TIMESTAMPS = 1200
RATE_LIMIT_HEADERS = (
    "retry-after",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "ratelimit-limit",
    "ratelimit-remaining",
    "ratelimit-reset",
    "ratelimit-policy",
    "x-processingunits-spent",
)
_HEADER_VALUE = re.compile(r"^[0-9A-Za-z .,:;=/_+-]{1,80}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_QUOTA_WORDS = re.compile(r"(?i)quota|monthly|exhaust|total limit|processing units? limit")


class ProviderPolicyError(ValueError):
    """An out-of-bounds pacing or retry setting."""


class ProviderPacingError(RuntimeError):
    """The shared pacer could not be used safely (lock timeout, unbounded wait)."""


class ProviderThrottleError(httpx.HTTPStatusError):
    """A provider throttle that did not clear within the retry budget.

    ``kind`` is ``rate_limited`` (short-window throttling that persisted for the whole
    budget) or ``quota_suspected`` (the provider asks for a wait beyond the policy, or
    says the quota is spent). It subclasses ``httpx.HTTPStatusError`` so every existing
    ``except httpx.HTTPStatusError`` path keeps handling it.
    """

    def __init__(self, kind: str, endpoint: str, response: httpx.Response, *, attempts: int,
                 waited_seconds: float, retry_after_seconds: float | None):
        try:
            request = response.request
        except RuntimeError:
            request = httpx.Request("POST", "https://provider.invalid/unknown")
        super().__init__(f"provider throttle {kind} endpoint={endpoint}", request=request, response=response)
        self.kind = kind
        self.endpoint = endpoint
        self.attempts = attempts
        self.waited_seconds = round(waited_seconds, 3)
        self.retry_after_seconds = retry_after_seconds


def _bounded(name: str, value: Any, low: float, high: float, *, integer: bool = False) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ProviderPolicyError(f"{name} must be a finite number")
    if integer and int(value) != value:
        raise ProviderPolicyError(f"{name} must be an integer")
    if not low <= value <= high:
        raise ProviderPolicyError(f"{name} must be between {low:g} and {high:g}")
    return int(value) if integer else float(value)


@dataclass(frozen=True)
class ProviderPolicy:
    """Bounded pacing and HTTP 429 retry settings (all seconds unless stated)."""

    #: Minimum spacing between successive Statistical/processing requests, across all
    #: collector processes. 5 s = at most 12 requests/min (TASK_250: 0 throttles at
    #: <= ~15/min, throttles at >= ~21/min).
    min_interval_seconds: float = 5.0
    #: Minimum spacing between OAuth token requests (that endpoint's limit is UNKNOWN).
    oauth_min_interval_seconds: float = 1.0
    #: HTTP 429 retries per request (attempts = retries + 1).
    max_retries: int = 4
    #: Total time one request may spend waiting on 429 retries.
    retry_budget_seconds: float = 300.0
    #: First back-off when the provider sends no usable Retry-After; doubles per retry.
    base_backoff_seconds: float = 15.0
    #: Upper bound of one back-off wait.
    max_backoff_seconds: float = 120.0
    #: A provider-requested wait above this is treated as quota_suspected (no retry).
    max_retry_after_seconds: float = 600.0
    #: Safety cap on any single pacer wait (never sleep longer than this).
    max_pacer_wait_seconds: float = 900.0
    #: Bound on waiting for the shared pacer file lock.
    lock_timeout_seconds: float = 30.0

    def __post_init__(self):
        _bounded("min_interval_seconds", self.min_interval_seconds, 1, 120)
        _bounded("oauth_min_interval_seconds", self.oauth_min_interval_seconds, 0, 60)
        _bounded("max_retries", self.max_retries, 0, 10, integer=True)
        _bounded("retry_budget_seconds", self.retry_budget_seconds, 10, 1800)
        _bounded("base_backoff_seconds", self.base_backoff_seconds, 1, 300)
        _bounded("max_backoff_seconds", self.max_backoff_seconds, 5, 900)
        _bounded("max_retry_after_seconds", self.max_retry_after_seconds, 30, 3600)
        _bounded("max_pacer_wait_seconds", self.max_pacer_wait_seconds, 60, 3600)
        _bounded("lock_timeout_seconds", self.lock_timeout_seconds, 1, 300)
        if self.base_backoff_seconds > self.max_backoff_seconds:
            raise ProviderPolicyError("base_backoff_seconds must not exceed max_backoff_seconds")
        if self.max_backoff_seconds > self.retry_budget_seconds:
            raise ProviderPolicyError("max_backoff_seconds must not exceed retry_budget_seconds")

    def interval(self, budget: str) -> float:
        return self.oauth_min_interval_seconds if budget == BUDGET_OAUTH else self.min_interval_seconds

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_mapping(cls, data: Any) -> "ProviderPolicy":
        if not isinstance(data, dict) or not set(data) <= set(cls.__dataclass_fields__):
            raise ProviderPolicyError("provider policy has unknown or malformed settings")
        return cls(**data)


def parse_retry_after(value: str | None, endpoint: str, *, now: float | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` value, or None when absent/unusable.

    Sentinel Hub processing endpoints document the value in **milliseconds** (e.g.
    ``3398``). The OAuth endpoint follows RFC 9110: delta-seconds or an HTTP date.
    Negative, non-finite or unparsable values yield None (the caller then backs off).
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or len(text) > 64:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        number = float(text)
        if not math.isfinite(number):
            return None
        return round(number / 1000.0, 3) if endpoint != "oauth" else round(number, 3)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    seconds = when.timestamp() - (time.time() if now is None else now)
    return round(max(0.0, seconds), 3)


def sanitized_rate_limit_headers(headers: Any) -> dict[str, str]:
    """Only allow-listed rate-limit header names with short, plain values."""
    result: dict[str, str] = {}
    try:
        items = list(headers.items())
    except AttributeError:
        return result
    for name, value in items:
        key = str(name).strip().lower()
        if key in RATE_LIMIT_HEADERS and _HEADER_VALUE.fullmatch(str(value).strip()):
            result[key] = str(value).strip()
    return result


def provider_error_signal(response: httpx.Response) -> dict[str, Any]:
    """A provider error reduced to safe tokens: code/reason and a quota-keyword flag."""
    signal: dict[str, Any] = {"code": None, "reason": None, "mentions_quota": False}
    try:
        payload = response.json()
    except Exception:
        payload = None
    texts: list[str] = []
    candidates = [payload] if isinstance(payload, dict) else []
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        candidates.insert(0, payload["error"])
    for item in candidates:
        for key in ("code", "reason"):
            value = item.get(key)
            if signal[key] is None and isinstance(value, (str, int)) and _TOKEN.fullmatch(str(value)):
                signal[key] = str(value)
        for key in ("message", "description", "error_description", "reason"):
            if isinstance(item.get(key), str):
                texts.append(item[key][:500])
    signal["mentions_quota"] = any(_QUOTA_WORDS.search(text) for text in texts)
    return signal


class _FileLock:
    """Exclusive cross-process lock on one file, bounded by a timeout."""

    def __init__(self, path: Path, timeout_seconds: float):
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+b")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._lock()
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise ProviderPacingError("provider pacer lock timed out")
                time.sleep(0.02)

    def _lock(self):
        if os.name == "nt":
            import msvcrt
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def _empty_budget() -> dict[str, Any]:
    return {"next_at": 0.0, "cooldown_until": 0.0}


def _empty_run(run_id: str) -> dict[str, Any]:
    return {"run_id": run_id, "stages": {}, "timestamps": {BUDGET_PROCESSING: [], BUDGET_OAUTH: []}}


def _empty_stage() -> dict[str, Any]:
    return {"requests": {}, "http_429": {}, "retries": 0, "cooldown_seconds": 0.0, "pacing_wait_seconds": 0.0,
            "terminal": {}, "statuses": {}, "retry_after_seconds": [], "rate_limit_headers": {},
            "provider_error_codes": []}


@dataclass
class ProcessCounters:
    """What this one process did; reported in the child's own JSON summary."""

    requests: dict[str, int] = field(default_factory=dict)
    http_429: dict[str, int] = field(default_factory=dict)
    retries: int = 0
    cooldown_seconds: float = 0.0
    pacing_wait_seconds: float = 0.0
    last_failure: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["cooldown_seconds"] = round(self.cooldown_seconds, 3)
        data["pacing_wait_seconds"] = round(self.pacing_wait_seconds, 3)
        return data


class ProviderGovernor:
    """Shared pacing plus bounded 429 retries for one collector process."""

    def __init__(self, policy: ProviderPolicy, state_dir: Path, *, run_id: str, stage: str,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        if stage not in KNOWN_STAGES:
            raise ProviderPolicyError("unknown provider stage")
        if not run_id or not _TOKEN.fullmatch(run_id):
            raise ProviderPolicyError("provider run id is malformed")
        self.policy = policy
        self.state_dir = Path(state_dir)
        self.run_id = run_id
        self.stage = stage
        self.clock = clock
        self.sleep = sleep
        self.counters = ProcessCounters()
        self._state_path = self.state_dir / STATE_FILENAME
        self._lock_path = self.state_dir / LOCK_FILENAME

    # -- shared state -------------------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = None
        valid = (isinstance(state, dict) and state.get("schema_version") == STATE_SCHEMA_VERSION
                 and isinstance(state.get("budgets"), dict) and isinstance(state.get("run"), dict))
        if not valid:
            state = {"schema_version": STATE_SCHEMA_VERSION, "budgets": {}, "run": _empty_run(self.run_id)}
        for budget in (BUDGET_PROCESSING, BUDGET_OAUTH):
            entry = state["budgets"].get(budget)
            if not isinstance(entry, dict) or not all(isinstance(entry.get(k), (int, float)) for k in ("next_at", "cooldown_until")):
                state["budgets"][budget] = _empty_budget()
        if state["run"].get("run_id") != self.run_id or not isinstance(state["run"].get("stages"), dict):
            state["run"] = _empty_run(self.run_id)
        state["run"]["stages"].setdefault(self.stage, _empty_stage())
        return state

    def _save(self, state: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_name(f".{STATE_FILENAME}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self._state_path)

    def _locked(self):
        return _FileLock(self._lock_path, self.policy.lock_timeout_seconds)

    # -- pacing ---------------------------------------------------------------------------
    def acquire(self, endpoint: str) -> float:
        """Reserve the next request slot of the endpoint's budget; sleep until it.

        Reservations queue: concurrent processes get successive slots ``interval``
        apart. Waits stay bounded: a stored slot or cooldown further ahead than any
        policy could produce (corrupt state, wall-clock step back) is discarded, never
        obeyed blindly.
        """
        budget = ENDPOINT_BUDGET[endpoint]
        interval = self.policy.interval(budget)
        cooldown_cap = max(self.policy.max_retry_after_seconds, self.policy.max_backoff_seconds)
        with self._locked():
            state = self._load()
            now = self.clock()
            entry = state["budgets"][budget]
            next_at = float(entry["next_at"])
            if next_at - now > self.policy.max_pacer_wait_seconds:
                next_at = now
            cooldown = float(entry["cooldown_until"])
            if cooldown - now > cooldown_cap:
                cooldown = now
            slot = max(now, next_at, cooldown)
            wait = slot - now
            if wait > self.policy.max_pacer_wait_seconds:
                raise ProviderPacingError("provider pacer wait exceeds the policy cap")
            entry["next_at"] = slot + interval
            stage = state["run"]["stages"][self.stage]
            stage["requests"][endpoint] = stage["requests"].get(endpoint, 0) + 1
            stage["pacing_wait_seconds"] = round(stage["pacing_wait_seconds"] + wait, 3)
            stamps = state["run"]["timestamps"].setdefault(budget, [])
            stamps.append(round(slot, 3))
            del stamps[:-MAX_TIMESTAMPS]
            self._save(state)
        self.counters.requests[endpoint] = self.counters.requests.get(endpoint, 0) + 1
        self.counters.pacing_wait_seconds += wait
        if wait > 0:
            self.sleep(wait)
        return wait

    def cooldown(self, endpoint: str, seconds: float) -> None:
        """Hold the endpoint's budget for every collector process for ``seconds``."""
        budget = ENDPOINT_BUDGET[endpoint]
        with self._locked():
            state = self._load()
            entry = state["budgets"][budget]
            entry["cooldown_until"] = max(float(entry["cooldown_until"]), self.clock() + seconds)
            stage = state["run"]["stages"][self.stage]
            stage["cooldown_seconds"] = round(stage["cooldown_seconds"] + seconds, 3)
            self._save(state)
        self.counters.cooldown_seconds += seconds

    def _record(self, mutate: Callable[[dict[str, Any]], None]) -> None:
        with self._locked():
            state = self._load()
            mutate(state["run"]["stages"][self.stage])
            self._save(state)

    # -- requests -------------------------------------------------------------------------
    def post(self, endpoint: str, send: Callable[[], httpx.Response]) -> httpx.Response:
        """Send one provider request with pacing and bounded HTTP 429 handling."""
        if endpoint not in ENDPOINT_BUDGET:
            raise ProviderPolicyError("unknown provider endpoint")
        retries = 0
        waited = 0.0
        while True:
            self.acquire(endpoint)
            try:
                response = send()
            except Exception:
                def note_error(stage: dict[str, Any]) -> None:
                    key = f"{endpoint}:transport_error"
                    stage["statuses"][key] = stage["statuses"].get(key, 0) + 1
                self._record(note_error)
                raise
            status = int(response.status_code)
            signal = provider_error_signal(response) if status in (403, 429) else None

            def note(stage: dict[str, Any], status=status) -> None:
                key = f"{endpoint}:{status}"
                stage["statuses"][key] = stage["statuses"].get(key, 0) + 1
            self._record(note)
            if status == 403 and signal and signal["mentions_quota"]:
                self._fail("quota_suspected", endpoint, response, retries, waited, None, signal)
            if status != 429:
                return response
            retry_after = parse_retry_after(response.headers.get("retry-after"), endpoint, now=self.clock())
            headers = sanitized_rate_limit_headers(response.headers)

            def note429(stage: dict[str, Any]) -> None:
                stage["http_429"][endpoint] = stage["http_429"].get(endpoint, 0) + 1
                if retry_after is not None:
                    stage["retry_after_seconds"] = (stage["retry_after_seconds"] + [retry_after])[-50:]
                stage["rate_limit_headers"].update(headers)
                code = signal.get("code") or signal.get("reason")
                if code and code not in stage["provider_error_codes"]:
                    stage["provider_error_codes"] = (stage["provider_error_codes"] + [code])[-10:]
            self._record(note429)
            self.counters.http_429[endpoint] = self.counters.http_429.get(endpoint, 0) + 1
            if signal["mentions_quota"] or (retry_after is not None and retry_after > self.policy.max_retry_after_seconds):
                self._fail("quota_suspected", endpoint, response, retries, waited, retry_after, signal)
            if retries >= self.policy.max_retries:
                self._fail("rate_limited", endpoint, response, retries, waited, retry_after, signal)
            floor = max(self.policy.interval(ENDPOINT_BUDGET[endpoint]), 1.0)
            if retry_after is not None:
                # Honour the provider's wait (already below max_retry_after_seconds).
                delay = max(retry_after, floor)
            else:
                delay = max(min(self.policy.base_backoff_seconds * (2 ** retries), self.policy.max_backoff_seconds), floor)
            if waited + delay > self.policy.retry_budget_seconds:
                self._fail("rate_limited", endpoint, response, retries, waited, retry_after, signal)
            self.cooldown(endpoint, delay)
            waited += delay
            retries += 1
            self.counters.retries += 1

            def note_retry(stage: dict[str, Any]) -> None:
                stage["retries"] += 1
            self._record(note_retry)

    def _fail(self, kind: str, endpoint: str, response: httpx.Response, retries: int, waited: float,
              retry_after: float | None, signal: dict[str, Any] | None) -> None:
        failure = {"category": kind, "endpoint": endpoint, "status": int(response.status_code), "retries": retries,
                   "waited_seconds": round(waited, 3), "retry_after_seconds": retry_after,
                   "provider_code": (signal or {}).get("code") or (signal or {}).get("reason")}
        self.counters.last_failure = failure

        def note(stage: dict[str, Any]) -> None:
            key = f"{kind}:{endpoint}"
            stage["terminal"][key] = stage["terminal"].get(key, 0) + 1
        self._record(note)
        raise ProviderThrottleError(kind, endpoint, response, attempts=retries + 1, waited_seconds=waited,
                                    retry_after_seconds=retry_after)

    # -- reporting ------------------------------------------------------------------------
    def process_summary(self) -> dict[str, Any]:
        return {"stage": self.stage, **self.counters.as_dict()}


def run_report(state_dir: Path, run_id: str) -> dict[str, Any] | None:
    """The shared ledger of one run, with request rates derived from slot timestamps."""
    try:
        state = json.loads((Path(state_dir) / STATE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    run = state.get("run") if isinstance(state, dict) else None
    if not isinstance(run, dict) or run.get("run_id") != run_id:
        return None
    rates = {}
    for budget, stamps in (run.get("timestamps") or {}).items():
        stamps = sorted(float(s) for s in stamps if isinstance(s, (int, float)))
        if not stamps:
            continue
        span = stamps[-1] - stamps[0]
        window_max, left = 0, 0
        for right, value in enumerate(stamps):
            while value - stamps[left] >= 60.0:
                left += 1
            window_max = max(window_max, right - left + 1)
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        rates[budget] = {"requests": len(stamps), "span_seconds": round(span, 1),
                         "average_per_minute": round(len(stamps) / (span / 60.0), 2) if span > 0 else None,
                         "max_in_any_60_seconds": window_max,
                         "min_spacing_seconds": round(min(gaps), 3) if gaps else None}
    return {"run_id": run_id, "stages": run.get("stages", {}), "request_rates": rates}


def policy_from_environment(environ: dict[str, str] | None = None) -> ProviderPolicy | None:
    environ = os.environ if environ is None else environ
    raw = environ.get(POLICY_ENV)
    if not raw:
        return None
    try:
        return ProviderPolicy.from_mapping(json.loads(raw))
    except (ValueError, TypeError) as exc:
        raise ProviderPolicyError("AGROSAT_PROVIDER_POLICY is malformed") from exc


def governor_from_environment(environ: dict[str, str] | None = None, **kwargs) -> ProviderGovernor | None:
    """The collector process's governor, or None outside a configured collector run."""
    environ = os.environ if environ is None else environ
    policy = policy_from_environment(environ)
    pacer_dir = environ.get(PACER_DIR_ENV)
    if policy is None or not pacer_dir:
        return None
    if not Path(pacer_dir).is_absolute():
        raise ProviderPolicyError("AGROSAT_PROVIDER_PACER_DIR must be absolute")
    return ProviderGovernor(policy, Path(pacer_dir), run_id=environ.get(RUN_ID_ENV) or "standalone",
                            stage=environ.get(STAGE_ENV) or "standalone", **kwargs)


def attach_provider_governor(service: Any, environ: dict[str, str] | None = None) -> ProviderGovernor | None:
    """Attach the environment's governor to a real provider service (collector only)."""
    governor = governor_from_environment(environ)
    if governor is not None and not getattr(service, "is_mock", False):
        service.provider_governor = governor
    return governor


class ProviderEnvironment:
    """Set the governor environment for spawned collector children; restore afterwards."""

    def __init__(self, policy: ProviderPolicy, pacer_dir: Path, run_id: str, stage: str):
        self.values = {POLICY_ENV: policy.to_json(), PACER_DIR_ENV: str(pacer_dir), RUN_ID_ENV: run_id, STAGE_ENV: stage}
        self.previous: dict[str, str | None] = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.previous[key] = os.environ.get(key)
            os.environ[key] = value
        return self

    def set_stage(self, stage: str) -> None:
        if stage not in KNOWN_STAGES:
            raise ProviderPolicyError("unknown provider stage")
        os.environ[STAGE_ENV] = stage

    def __exit__(self, *exc):
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


#: Cycle-level provider failure categories. ``authentication`` and ``quota`` stop the
#: whole collection run (provider-wide); ``rate_limited`` stops only the current stage,
#: so the other stage still runs after the shared cooldown (TASK_251).
CYCLE_FAILURE_CATEGORIES = ("authentication", "quota", "rate_limited", "network", "timeout", "cloud", "provider_error")
STAGE_TERMINAL_CATEGORIES = frozenset({"authentication", "quota", "rate_limited"})
RUN_TERMINAL_CATEGORIES = frozenset({"authentication", "quota"})
_CHILD_MARKERS = (
    ("authentication", ("category=authentication", "invalid_client", "unauthorized", " 401", " 403")),
    ("quota", ("category=quota_suspected",)),
    ("rate_limited", ("category=rate_limited", "category=quota_or_rate_limit", "rate limit", " 429", "status=429")),
    ("network", ("category=network", "category=provider_unavailable", "connection", "dns")),
    ("timeout", ("category=timeout", "timeout", "timed out")),
    ("cloud", ("cloud", "quality_blocked")),
)


def child_failure_category(texts: list[Any], structured: Any = None, *, had_errors: bool = False) -> str | None:
    """Bounded failure category of one collector child attempt.

    ``structured`` is the governor's terminal category from the child's JSON summary
    (``rate_limited`` / ``quota_suspected``); it wins over text markers. Text markers
    remain for children without a governor (standalone or legacy output).
    """
    if structured == "rate_limited":
        return "rate_limited"
    if structured == "quota_suspected":
        return "quota"
    searchable = " ".join(str(item) for item in texts).lower()
    for category, markers in _CHILD_MARKERS:
        if any(marker in searchable for marker in markers):
            return category
    return "provider_error" if had_errors else None


def structured_failure(child_summary: Any) -> str | None:
    """The governor's terminal category recorded in a child summary, if any."""
    if not isinstance(child_summary, dict):
        return None
    resilience = child_summary.get("provider_resilience")
    failure = resilience.get("last_failure") if isinstance(resilience, dict) else None
    category = failure.get("category") if isinstance(failure, dict) else None
    return category if category in ("rate_limited", "quota_suspected") else None


def aggregate_process_summaries(summaries: list[Any]) -> dict[str, Any]:
    """Sum the per-child governor counters of one batch (bounded output)."""
    total: dict[str, Any] = {"children_reporting": 0, "requests": {}, "http_429": {}, "retries": 0,
                             "cooldown_seconds": 0.0, "pacing_wait_seconds": 0.0, "terminal_failures": []}
    for item in summaries:
        if not isinstance(item, dict):
            continue
        total["children_reporting"] += 1
        for key in ("requests", "http_429"):
            for endpoint, count in (item.get(key) or {}).items():
                if isinstance(count, int) and count >= 0 and endpoint in ENDPOINT_BUDGET:
                    total[key][endpoint] = total[key].get(endpoint, 0) + count
        for key in ("retries",):
            if isinstance(item.get(key), int) and item[key] >= 0:
                total[key] += item[key]
        for key in ("cooldown_seconds", "pacing_wait_seconds"):
            if isinstance(item.get(key), (int, float)) and item[key] >= 0:
                total[key] = round(total[key] + float(item[key]), 3)
        failure = item.get("last_failure")
        if isinstance(failure, dict) and len(total["terminal_failures"]) < 20:
            total["terminal_failures"].append({k: failure.get(k) for k in ("category", "endpoint", "status", "retries",
                                                                          "waited_seconds", "retry_after_seconds")})
    return total


class _StageOnly:
    """Inside an already configured collector run: only make sure a stage is set."""

    def __init__(self, stage: str):
        self.stage = stage
        self.previous: str | None = None
        self.changed = False

    def __enter__(self):
        if not os.environ.get(STAGE_ENV):
            self.previous, self.changed = os.environ.get(STAGE_ENV), True
            os.environ[STAGE_ENV] = self.stage
        return self

    def __exit__(self, *exc):
        if self.changed:
            if self.previous is None:
                os.environ.pop(STAGE_ENV, None)
            else:
                os.environ[STAGE_ENV] = self.previous


def ensure_collector_environment(stage: str, pacer_dir: Path, run_id: str):
    """Context for a cycle runner's children: reuse the canonical run's governor
    environment when present, otherwise configure the default policy so a standalone
    runner is paced too (its pacer state lives next to its own state/lock file)."""
    if os.environ.get(POLICY_ENV) and os.environ.get(PACER_DIR_ENV):
        policy_from_environment()  # fail early on a malformed inherited policy
        return _StageOnly(stage)
    return ProviderEnvironment(ProviderPolicy(), Path(pacer_dir), run_id, stage)


def service_governor(service: Any) -> ProviderGovernor | None:
    """The governor a provider service actually uses (only a real ProviderGovernor counts)."""
    candidate = getattr(service, "provider_governor", None)
    return candidate if isinstance(candidate, ProviderGovernor) else None
