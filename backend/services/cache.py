"""Optional Redis cache with PostgreSQL-preserving failure semantics."""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from typing import Any, Optional

import redis

from config import settings


logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 2
RETRY_AFTER_FAILURE_SECONDS = 30
MAX_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_KEY_LENGTH = 512
SCAN_COUNT = 100
MAX_PATTERN_DELETE_KEYS = 1000
MAX_PATTERN_SCAN_CALLS = 100

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


def cache_get(key: str) -> Optional[Any]:
    if not _valid_key(key):
        return None
    client = _client()
    if client is None:
        return None
    try:
        value = client.get(key)
        return json.loads(value) if value else None
    except Exception:
        _mark_failed()
        return None


def cache_set(key: str, value: Any, ttl_seconds: int = 120) -> bool:
    if not _valid_key(key) or not _valid_ttl(ttl_seconds):
        return False
    client = _client()
    if client is None:
        return False
    try:
        client.setex(key, ttl_seconds, json.dumps(value, default=str))
        return True
    except Exception:
        _mark_failed()
        return False


def cache_get_binary(key: str) -> Optional[bytes]:
    if not _valid_key(key):
        return None
    client = _client()
    if client is None:
        return None
    try:
        value = client.get(key)
        if not value:
            return None
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception:
        _mark_failed()
        return None


def cache_set_binary(key: str, value: bytes, ttl_seconds: int) -> bool:
    if (
        not _valid_key(key)
        or not isinstance(value, bytes)
        or not _valid_ttl(ttl_seconds)
    ):
        return False
    client = _client()
    if client is None:
        return False
    try:
        client.setex(
            key,
            ttl_seconds,
            base64.b64encode(value).decode("ascii"),
        )
        return True
    except Exception:
        _mark_failed()
        return False


def cache_delete(key: str) -> bool:
    if not _valid_key(key):
        return False
    client = _client()
    if client is None:
        return False
    try:
        client.delete(key)
        return True
    except Exception:
        _mark_failed()
        return False


def cache_delete_pattern(pattern: str) -> bool:
    """Delete a bounded number of matching keys without Redis ``KEYS`` or flush."""
    if not _valid_key(pattern, pattern=True):
        return False
    client = _client()
    if client is None:
        return False
    cursor = 0
    deleted = 0
    scans = 0
    try:
        while scans < MAX_PATTERN_SCAN_CALLS and deleted < MAX_PATTERN_DELETE_KEYS:
            cursor, keys = client.scan(
                cursor=cursor,
                match=pattern,
                count=SCAN_COUNT,
            )
            scans += 1
            remaining = MAX_PATTERN_DELETE_KEYS - deleted
            batch = list(keys)[:remaining]
            if batch:
                client.delete(*batch)
                deleted += len(batch)
            if cursor == 0:
                return True
        logger.warning("Redis pattern invalidation reached its bounded scan limit")
        return False
    except Exception:
        _mark_failed()
        return False
