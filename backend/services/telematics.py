"""Read-only, tenant-scoped telematics provider boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import threading
import time
from typing import Any, Protocol, Sequence


MAX_UNITS = 100
MAX_PAGES = 20
MAX_RANGE_HOURS = 24 * 31
MAX_SENSOR_VALUES = 32
STALE_AFTER_SECONDS = 15 * 60
MAX_TRACKED_TENANTS = 1000


class TelematicsProviderError(RuntimeError):
    def __init__(self, category: str, *, retryable: bool = False):
        super().__init__(category)
        self.category = category
        self.retryable = retryable


@dataclass(frozen=True)
class FieldTelematicsMapping:
    enterprise_id: int
    field_id: int
    unit_ids: tuple[str, ...]
    geofence_ids: tuple[str, ...] = ()
    allowed_sensors: tuple[str, ...] = ()
    provenance: str = "tenant_mapping"


class TelematicsProvider(Protocol):
    name: str

    def read(
        self,
        mapping: FieldTelematicsMapping,
        *,
        started_at: datetime,
        ended_at: datetime,
        limit: int,
        page_limit: int,
        timeout_seconds: float,
    ) -> Sequence[dict[str, Any]]: ...


class UnsupportedTelematicsProvider:
    name = "wialon"

    def read(self, *args, **kwargs):
        raise TelematicsProviderError("unsupported", retryable=False)


class TenantRequestLimiter:
    """Bounded process-local guard for a future live provider instance."""

    def __init__(self, max_requests: int, window_seconds: float, clock=time.monotonic):
        if not 1 <= max_requests <= 1000 or not 1 <= window_seconds <= 3600:
            raise ValueError("rate limiter bounds are invalid")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.clock = clock
        self._events: dict[int, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, enterprise_id: int) -> bool:
        tenant = int(enterprise_id)
        now = float(self.clock())
        cutoff = now - self.window_seconds
        with self._lock:
            if tenant not in self._events and len(self._events) >= MAX_TRACKED_TENANTS:
                stale = [
                    key for key, events in self._events.items()
                    if not events or events[-1] <= cutoff
                ]
                for key in stale[: max(1, len(self._events) // 10)]:
                    self._events.pop(key, None)
                if len(self._events) >= MAX_TRACKED_TENANTS:
                    return False
            events = [event for event in self._events.get(tenant, []) if event > cutoff]
            if len(events) >= self.max_requests:
                self._events[tenant] = events
                return False
            events.append(now)
            self._events[tenant] = events
            return True


def telematics_cache_key(
    enterprise_id: int,
    field_id: int,
    started_at: datetime,
    ended_at: datetime,
) -> str:
    return (
        f"telematics:v1:enterprise:{int(enterprise_id)}:field:{int(field_id)}:"
        f"{started_at.astimezone(timezone.utc).isoformat()}:"
        f"{ended_at.astimezone(timezone.utc).isoformat()}"
    )


def validate_request_bounds(
    started_at: datetime,
    ended_at: datetime,
    *,
    limit: int,
    page_limit: int,
    timeout_seconds: float,
) -> None:
    if started_at.tzinfo is None or ended_at.tzinfo is None:
        raise ValueError("timezone-aware range required")
    if ended_at <= started_at:
        raise ValueError("ended_at must be later than started_at")
    if (ended_at - started_at).total_seconds() > MAX_RANGE_HOURS * 3600:
        raise ValueError("time range exceeds 31 days")
    if not 1 <= limit <= MAX_UNITS:
        raise ValueError("unit limit is out of bounds")
    if not 1 <= page_limit <= MAX_PAGES:
        raise ValueError("page limit is out of bounds")
    if not 1 <= timeout_seconds <= 30:
        raise ValueError("timeout is out of bounds")


def _safe_text(value: Any, maximum: int = 255) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:maximum] if text else None


def _safe_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def sanitize_unit(
    value: dict[str, Any],
    mapping: FieldTelematicsMapping,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    unit_id = _safe_text(value.get("unit_id"), 128)
    if not unit_id or unit_id not in mapping.unit_ids:
        return None
    observed_at = _parse_timestamp(value.get("observed_at"))
    latitude = _safe_number(value.get("latitude"))
    longitude = _safe_number(value.get("longitude"))
    if observed_at is None or latitude is None or longitude is None:
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    age_seconds = max(0, int((now.astimezone(timezone.utc) - observed_at).total_seconds()))
    raw_sensors = value.get("sensors") if isinstance(value.get("sensors"), dict) else {}
    sensors = []
    for code in mapping.allowed_sensors[:MAX_SENSOR_VALUES]:
        raw = raw_sensors.get(code)
        if not isinstance(raw, dict):
            continue
        sensor_value = _safe_number(raw.get("value"))
        if sensor_value is None:
            continue
        sensors.append({
            "code": code,
            "value": sensor_value,
            "unit": _safe_text(raw.get("unit"), 32),
        })
    return {
        "unit_id": unit_id,
        "label": _safe_text(value.get("label"), 255) or unit_id,
        "position": {
            "latitude": latitude,
            "longitude": longitude,
            "observed_at": observed_at.isoformat(),
        },
        "movement": bool(value.get("movement")) if value.get("movement") is not None else None,
        "ignition": bool(value.get("ignition")) if value.get("ignition") is not None else None,
        "sensors": sensors,
        "field_intersection": value.get("field_intersection") is True,
        "geofence_intersection": (
            value.get("geofence_intersection") is True
            if mapping.geofence_ids else None
        ),
        "age_seconds": age_seconds,
        "stale": age_seconds > STALE_AFTER_SECONDS,
    }


def read_field_telematics(
    provider: TelematicsProvider,
    mapping: FieldTelematicsMapping | None,
    *,
    started_at: datetime,
    ended_at: datetime,
    limit: int = 50,
    page_limit: int = 5,
    timeout_seconds: float = 10,
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_request_bounds(
        started_at,
        ended_at,
        limit=limit,
        page_limit=page_limit,
        timeout_seconds=timeout_seconds,
    )
    if mapping is None:
        return {
            "status": "unsupported",
            "provider": provider.name,
            "reason": "mapping_unavailable",
            "units": [],
        }
    current = now or datetime.now(timezone.utc)
    try:
        raw_units = provider.read(
            mapping,
            started_at=started_at,
            ended_at=ended_at,
            limit=limit,
            page_limit=page_limit,
            timeout_seconds=timeout_seconds,
        )
    except TelematicsProviderError as error:
        return {
            "status": "unsupported" if error.category == "unsupported" else "unavailable",
            "provider": provider.name,
            "reason": error.category,
            "retryable": error.retryable,
            "mapping_provenance": mapping.provenance,
            "units": [],
        }
    units = []
    for raw in list(raw_units)[:limit]:
        if isinstance(raw, dict):
            unit = sanitize_unit(raw, mapping, now=current)
            if unit is not None:
                units.append(unit)
    stale = bool(units) and all(unit["stale"] for unit in units)
    return {
        "status": "stale" if stale else "available",
        "provider": provider.name,
        "mapping_provenance": mapping.provenance,
        "requested_range": {
            "started_at": started_at.astimezone(timezone.utc).isoformat(),
            "ended_at": ended_at.astimezone(timezone.utc).isoformat(),
        },
        "units": units,
        "unit_limit": limit,
        "page_limit": page_limit,
    }
