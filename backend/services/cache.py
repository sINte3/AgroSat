"""Optional Redis cache with PostgreSQL-preserving failure semantics."""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from typing import Any, Optional

import redis

from config import settings
from services.metrics import record_cache_operation


logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 2
RETRY_AFTER_FAILURE_SECONDS = 30
MAX_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_KEY_LENGTH = 512
SCAN_COUNT = 100
MAX_PATTERN_DELETE_KEYS = 1000
MAX_PATTERN_SCAN_CALLS = 100
MAX_INVALIDATION_PATTERNS = 32
CACHE_KEY_PREFIX = "agrosat"
ENVIRONMENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_redis = None
CACHE_AVAILABLE = False
_retry_after_monotonic = 0.0
_connect_lock = threading.Lock()


def _valid_key(key: str, *, pattern: bool = False) -> bool:
    if not isinstance(key, str) or not key or len(key) > MAX_KEY_LENGTH:
        return False
    if any(character.isspace() for character in key):
        return False
    return pattern or not any(character in key for character in "*?[]")


def _valid_ttl(ttl_seconds: int) -> bool:
    return (
        type(ttl_seconds) is int
        and 1 <= ttl_seconds <= MAX_CACHE_TTL_SECONDS
    )


def _physical_key(key: str, *, pattern: bool = False) -> str | None:
    """Return an environment-scoped physical key for one logical cache key."""
    if not _valid_key(key, pattern=pattern):
        return None
    environment = str(settings.environment or "").strip().lower()
    if not ENVIRONMENT_PATTERN.fullmatch(environment):
        return None
    physical = f"{CACHE_KEY_PREFIX}:{environment}:{key}"
    if len(physical) > MAX_KEY_LENGTH:
        return None
    return physical


def _mark_failed() -> None:
    global _redis, CACHE_AVAILABLE, _retry_after_monotonic
    _redis = None
    CACHE_AVAILABLE = False
    _retry_after_monotonic = time.monotonic() + RETRY_AFTER_FAILURE_SECONDS


def _client():
    """Connect lazily and back off after an outage.

    Importing the FastAPI application never opens a Redis connection. Returning
    ``None`` is always a cache bypass; callers continue to PostgreSQL.
    """
    global _redis, CACHE_AVAILABLE
    if CACHE_AVAILABLE and _redis is not None:
        return _redis
    if time.monotonic() < _retry_after_monotonic:
        return None
    with _connect_lock:
        if CACHE_AVAILABLE and _redis is not None:
            return _redis
        if time.monotonic() < _retry_after_monotonic:
            return None
        try:
            candidate = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
                socket_timeout=CONNECT_TIMEOUT_SECONDS,
            )
            candidate.ping()
        except Exception:
            _mark_failed()
            logger.warning("Redis unavailable; cache bypass enabled")
            return None
        _redis = candidate
        CACHE_AVAILABLE = True
        return candidate


def _discard_corrupt_value(client, physical_key: str) -> None:
    """Best-effort removal of one corrupt entry without disabling Redis."""
    try:
        client.delete(physical_key)
    except Exception:
        _mark_failed()


def cache_get(key: str) -> Optional[Any]:
    physical_key = _physical_key(key)
    if physical_key is None:
        record_cache_operation("get", "bypass")
        return None
    client = _client()
    if client is None:
        record_cache_operation("get", "unavailable")
        return None
    try:
        value = client.get(physical_key)
        if not value:
            record_cache_operation("get", "miss")
            return None
    except Exception:
        _mark_failed()
        record_cache_operation("get", "failure")
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        _discard_corrupt_value(client, physical_key)
        record_cache_operation("get", "failure")
        return None
    record_cache_operation("get", "hit")
    return decoded


def cache_set(key: str, value: Any, ttl_seconds: int = 120) -> bool:
    physical_key = _physical_key(key)
    if physical_key is None or not _valid_ttl(ttl_seconds):
        record_cache_operation("set", "bypass")
        return False
    client = _client()
    if client is None:
        record_cache_operation("set", "unavailable")
        return False
    try:
        client.setex(
            physical_key,
            ttl_seconds,
            json.dumps(value, default=str),
        )
        record_cache_operation("set", "success")
        return True
    except Exception:
        _mark_failed()
        record_cache_operation("set", "failure")
        return False


def cache_get_binary(key: str) -> Optional[bytes]:
    physical_key = _physical_key(key)
    if physical_key is None:
        record_cache_operation("get_binary", "bypass")
        return None
    client = _client()
    if client is None:
        record_cache_operation("get_binary", "unavailable")
        return None
    try:
        value = client.get(physical_key)
        if not value:
            record_cache_operation("get_binary", "miss")
            return None
    except Exception:
        _mark_failed()
        record_cache_operation("get_binary", "failure")
        return None
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (AttributeError, TypeError, UnicodeError, ValueError):
        _discard_corrupt_value(client, physical_key)
        record_cache_operation("get_binary", "failure")
        return None
    record_cache_operation("get_binary", "hit")
    return decoded


def cache_set_binary(key: str, value: bytes, ttl_seconds: int) -> bool:
    physical_key = _physical_key(key)
    if (
        physical_key is None
        or not isinstance(value, bytes)
        or not _valid_ttl(ttl_seconds)
    ):
        record_cache_operation("set_binary", "bypass")
        return False
    client = _client()
    if client is None:
        record_cache_operation("set_binary", "unavailable")
        return False
    try:
        client.setex(
            physical_key,
            ttl_seconds,
            base64.b64encode(value).decode("ascii"),
        )
        record_cache_operation("set_binary", "success")
        return True
    except Exception:
        _mark_failed()
        record_cache_operation("set_binary", "failure")
        return False


def cache_delete(key: str) -> bool:
    physical_key = _physical_key(key)
    if physical_key is None:
        record_cache_operation("delete", "bypass")
        return False
    client = _client()
    if client is None:
        record_cache_operation("delete", "unavailable")
        return False
    try:
        client.delete(physical_key)
        record_cache_operation("delete", "success")
        return True
    except Exception:
        _mark_failed()
        record_cache_operation("delete", "failure")
        return False


def cache_delete_pattern(pattern: str) -> bool:
    """Delete a bounded number of matching keys without Redis ``KEYS`` or flush."""
    physical_pattern = _physical_key(pattern, pattern=True)
    if physical_pattern is None:
        record_cache_operation("delete_pattern", "bypass")
        return False
    client = _client()
    if client is None:
        record_cache_operation("delete_pattern", "unavailable")
        return False
    cursor = 0
    deleted = 0
    scans = 0
    try:
        while scans < MAX_PATTERN_SCAN_CALLS and deleted < MAX_PATTERN_DELETE_KEYS:
            cursor, keys = client.scan(
                cursor=cursor,
                match=physical_pattern,
                count=SCAN_COUNT,
            )
            scans += 1
            remaining = MAX_PATTERN_DELETE_KEYS - deleted
            batch = list(keys)[:remaining]
            if batch:
                client.delete(*batch)
                deleted += len(batch)
            if cursor == 0:
                record_cache_operation("delete_pattern", "success")
                return True
        logger.warning("Redis pattern invalidation reached its bounded scan limit")
        record_cache_operation("delete_pattern", "failure")
        return False
    except Exception:
        _mark_failed()
        record_cache_operation("delete_pattern", "failure")
        return False


def _positive_identifier(value: int, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def field_read_model_cache_patterns(enterprise_id: int) -> tuple[str, ...]:
    """Return only the tenant and global read models affected by one field."""
    tenant = _positive_identifier(enterprise_id, "enterprise_id")
    scope = f"enterprise:{tenant}"
    return (
        f"fields:list:v2:{scope}",
        "fields:list:v2:all",
        f"fields:geojson:v2:{scope}",
        "fields:geojson:v2:all",
        f"field-tiles:*:metadata:{scope}",
        "field-tiles:*:metadata:all",
        f"field-tiles:*:tile:{scope}:*",
        "field-tiles:*:tile:all:*",
    )


def observation_cache_patterns(enterprise_id: int) -> tuple[str, ...]:
    """Return read models affected by one committed satellite observation."""
    tenant = _positive_identifier(enterprise_id, "enterprise_id")
    return (
        *field_read_model_cache_patterns(tenant),
        f"dashboard:summary:{tenant}",
        "dashboard:summary:all",
    )


def alert_mutation_cache_patterns(
    enterprise_id: int,
    field_id: int,
) -> tuple[str, ...]:
    """Return bounded read models affected by one committed alert mutation."""
    tenant = _positive_identifier(enterprise_id, "enterprise_id")
    field = _positive_identifier(field_id, "field_id")
    return (
        f"alerts:{tenant}:*",
        "alerts:all:*",
        f"field_alerts:{field}:*",
        f"dashboard:summary:{tenant}",
        "dashboard:summary:all",
        *field_read_model_cache_patterns(tenant),
    )


def cache_delete_patterns(patterns: tuple[str, ...]) -> bool:
    """Invalidate a small explicit pattern set without a shared/global flush."""
    if not isinstance(patterns, tuple):
        return False
    unique = tuple(dict.fromkeys(patterns))
    if not unique or len(unique) > MAX_INVALIDATION_PATTERNS:
        return False
    results = [cache_delete_pattern(pattern) for pattern in unique]
    return all(results)


def cache_probe() -> bool:
    """Return cache availability without making Redis a readiness dependency."""
    client = _client()
    if client is None:
        record_cache_operation("probe", "unavailable")
        return False
    try:
        available = bool(client.ping())
        record_cache_operation("probe", "success" if available else "failure")
        return available
    except Exception:
        _mark_failed()
        record_cache_operation("probe", "failure")
        return False
