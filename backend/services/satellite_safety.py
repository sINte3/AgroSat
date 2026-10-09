"""Pure credential, provenance, and sanitized provider-error guards."""

from dataclasses import asdict, dataclass
import json

import httpx

REAL_SATELLITE_SOURCE = "Sentinel-2"
MOCK_SATELLITE_SOURCE = "Mock/Dev"
_UNSAFE_MARKERS = ("mock", "dev", "test", "synthetic")


class SatelliteConfigurationError(RuntimeError):
    pass


class SatelliteProvenanceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderErrorClassification:
    category: str
    retryable: bool
    status_code: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def provider_endpoint_label(error: BaseException) -> str | None:
    """Which provider endpoint failed, from the request URL path only (never query or host).

    TASK_251: the token request and the Statistical request used to log one identical
    line, so a 429 could not be attributed (TASK_250 E6).
    """
    explicit = getattr(error, "endpoint", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    try:
        path = error.request.url.path.lower()  # type: ignore[attr-defined]
    except Exception:
        return None
    if "openid-connect" in path or path.endswith("/token"):
        return "oauth"
    if "statistic" in path:
        return "statistical"
    if "catalog" in path:
        return "catalog"
    if "process" in path:
        return "process"
    return "other"


def classify_provider_error(error: BaseException) -> ProviderErrorClassification:
    """Classify provider failures without retaining exception text or response bodies."""
    from services.provider_governor import ProviderDeadlineError, ProviderPacingError, ProviderThrottleError

    if isinstance(error, ProviderDeadlineError):
        # TASK_251A: a wait or request that could not finish before the child's deadline;
        # rate_limited when a shared 429 cooldown is what did not fit.
        return ProviderErrorClassification(error.kind, True)
    if isinstance(error, ProviderThrottleError):
        # The collector governor already retried within its bounded budget:
        # rate_limited = a short-window throttle that persisted for the whole budget;
        # quota_suspected = the provider asked for a long wait or reported a quota.
        return ProviderErrorClassification(error.kind, error.kind == "rate_limited", error.response.status_code)
    if isinstance(error, ProviderPacingError):
        # A local coordination failure (pacer lock/state), not a provider response.
        return ProviderErrorClassification("pacing_unavailable", False)
    if isinstance(error, SatelliteConfigurationError):
        return ProviderErrorClassification("configuration", False)
    if isinstance(error, httpx.TimeoutException):
        return ProviderErrorClassification("timeout", True)
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        if status_code in (401, 403):
            return ProviderErrorClassification("authentication", False, status_code)
        if status_code == 429:
            return ProviderErrorClassification("quota_or_rate_limit", True, status_code)
        if status_code in (408, 425):
            return ProviderErrorClassification("timeout", True, status_code)
        if 500 <= status_code <= 599:
            return ProviderErrorClassification("provider_unavailable", True, status_code)
        if 400 <= status_code <= 499:
            return ProviderErrorClassification("request_rejected", False, status_code)
        return ProviderErrorClassification("unexpected_http_status", False, status_code)
    if isinstance(error, httpx.RequestError):
        return ProviderErrorClassification("network", True)
    if isinstance(error, (json.JSONDecodeError, IndexError, KeyError, TypeError, ValueError)):
        return ProviderErrorClassification("invalid_response", False)
    return ProviderErrorClassification("provider_error", False)


def safe_provider_error_summary(error: BaseException) -> str:
    classification = classify_provider_error(error)
    status = "" if classification.status_code is None else f" status={classification.status_code}"
    retryable = str(classification.retryable).lower()
    endpoint = provider_endpoint_label(error)
    suffix = "" if endpoint is None else f" endpoint={endpoint}"
    return f"Sentinel provider error category={classification.category}{status} retryable={retryable}{suffix}"


def validate_credentials(client_id, client_secret):
    values = (client_id, client_secret)
    present = [isinstance(value, str) and bool(value.strip()) for value in values]
    if any(present) and not all(present):
        raise SatelliteConfigurationError("Sentinel Hub credentials are incomplete")
    if not all(present):
        raise SatelliteConfigurationError("Complete Sentinel Hub credentials are required")
    if any(value != value.strip() for value in values):
        raise SatelliteConfigurationError("Sentinel Hub credentials are malformed")
    return client_id, client_secret


def credentials_are_missing(client_id, client_secret):
    try:
        validate_credentials(client_id, client_secret)
    except SatelliteConfigurationError:
        return True
    return False


def is_unsafe_provenance(source):
    if not isinstance(source, str) or not source.strip():
        return True
    normalized = source.strip().casefold()
    return any(marker in normalized for marker in _UNSAFE_MARKERS)


def require_real_provenance(source):
    if not isinstance(source, str) or not source.strip():
        raise SatelliteProvenanceError("Satellite provenance is required")
    if is_unsafe_provenance(source) or source.strip().casefold() != REAL_SATELLITE_SOURCE.casefold():
        raise SatelliteProvenanceError("Unsafe satellite provenance")
    return REAL_SATELLITE_SOURCE


def require_real_service(service):
    """Require an explicitly non-mock service with canonical real provenance."""
    if service is None or not hasattr(service, "is_mock"):
        raise SatelliteProvenanceError("Real satellite service identity is required")
    if service.is_mock is not False:
        raise SatelliteProvenanceError("Real satellite service identity is required")
    return require_real_provenance(getattr(service, "source", None))


def require_payload_provenance(payload, key="satellite"):
    if not isinstance(payload, dict):
        raise SatelliteProvenanceError("Satellite payload provenance is required")
    return require_real_provenance(payload.get(key))


def validate_batch_provenance(records, key="satellite"):
    records = list(records)
    for record in records:
        require_payload_provenance(record, key=key)
    return records
